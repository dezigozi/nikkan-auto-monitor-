"""
日刊自動車新聞 トピックスモニター - バックエンド
  1. netdenjd.com にログインして記事一覧を取得
  2. キーワードにヒットした記事を OpenRouter(無料LLM) で要約
  3. Slack に投稿
  4. data/articles.json に保存（フロントエンドが読む）
"""

import json
import os
import sys
import asyncio
from datetime import datetime
from pathlib import Path

# --- サードパーティ ---
try:
    from playwright.async_api import async_playwright
    from openai import OpenAI
    import requests
except ImportError as e:
    print(f"[ERROR] 依存パッケージが不足しています: {e}")
    print("  pip install -r requirements.txt を実行してください")
    sys.exit(1)

# =====================
#  設定読み込み
# =====================
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

def load_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    # GitHub Actions の Secrets（環境変数）があれば上書き
    if os.environ.get("NETDENJD_USERNAME"):
        cfg["source"]["username"] = os.environ["NETDENJD_USERNAME"]
    if os.environ.get("NETDENJD_PASSWORD"):
        cfg["source"]["password"] = os.environ["NETDENJD_PASSWORD"]
    if os.environ.get("SLACK_WEBHOOK_URL"):
        cfg["slack"]["webhook_url"] = os.environ["SLACK_WEBHOOK_URL"]
    if os.environ.get("OPENROUTER_API_KEY"):
        cfg.setdefault("openrouter", {})["api_key"] = os.environ["OPENROUTER_API_KEY"]
    # keywords.json があればキーワードを上書き（GitHub上で管理）
    kw_file = BASE_DIR / "keywords.json"
    if kw_file.exists():
        with open(kw_file, encoding="utf-8") as f:
            cfg["keywords"] = json.load(f)
    return cfg

# =====================
#  スクレイピング
# =====================
async def scrape_articles(cfg: dict, keywords: list[str]) -> tuple[list[dict], int]:
    """netdenjd.com にログインし、記事一覧の【タイトル】でキーワード判定したうえで、
    ヒットした記事だけ本文を取得して返す。

    処理順が重要:
      1. 一覧から全記事のタイトル＋URLだけを軽く集める（上限なし）
      2. 全タイトルでキーワード判定 → ヒットを絞り込む
      3. ヒットした記事だけ本文を深掘りして要約材料にする

    本文取得は記事ごとにページ遷移する重い処理。先にタイトルで判定することで、
    一覧の全記事が判定対象になり、件数上限で後ろの記事を取りこぼすことがなくなる。

    戻り値: (ヒット記事のリスト, 一覧でスキャンした総記事数)
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await ctx.new_page()

        # --- ログイン（本文は有料ログイン制のため、要約材料の取得に必要）---
        print("[1/4] ログイン中...")
        await page.goto(cfg["source"]["login_url"], wait_until="domcontentloaded", timeout=30000)
        try:
            await page.fill('input[name="email"], input[type="email"], input[name="username"]',
                            cfg["source"]["username"])
            await page.fill('input[name="password"], input[type="password"]',
                            cfg["source"]["password"])
            await page.click('button[type="submit"], input[type="submit"]')
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            print("   → ログイン完了")
        except Exception as e:
            print(f"   [WARN] ログイン操作に問題が発生しました: {e}")

        # --- 記事一覧を取得（タイトル＋URLのみ。本文はまだ取らない）---
        print("[2/4] 記事一覧のタイトルを取得中...")
        await page.goto(cfg["source"]["url"], wait_until="domcontentloaded", timeout=30000)

        raw = await page.eval_on_selector_all(
            "a[href]",
            """els => els.map(e => ({
                href: e.href,
                text: (e.innerText || '').trim(),
                title: (e.getAttribute('title') || '').trim()
            }))"""
        )

        # 重複除去 ＋ タイトル抽出（CSSで見出しが途中省略される場合に備え title属性も併用）
        seen = set()
        candidates = []
        for lk in raw:
            href = lk["href"]
            if "netdenjd.com" not in href or href in seen:
                continue
            title = lk["title"] if len(lk["title"]) > len(lk["text"]) else lk["text"]
            if len(title) <= 20:   # ナビ・カテゴリ等の短いリンクを除外
                continue
            seen.add(href)
            candidates.append({"title": title[:120], "url": href})

        print(f"   → {len(candidates)} 件の記事を一覧から検出")

        # --- タイトルでキーワード判定（一覧の全記事が対象）---
        matched = filter_by_keywords(candidates, keywords)
        print(f"   → うち {len(matched)} 件がキーワードにヒット")

        # --- ヒットした記事だけ本文を取得（要約材料）---
        print("[3/4] ヒット記事の本文を取得中...")
        for art in matched:
            try:
                await page.goto(art["url"], wait_until="domcontentloaded", timeout=15000)
                art["body"] = await page.evaluate("""() => {
                    const sel = ['article', 'main', '.article-body', '.content', 'body'];
                    for (const s of sel) {
                        const el = document.querySelector(s);
                        if (el) return el.innerText.trim().slice(0, 3000);
                    }
                    return document.body.innerText.trim().slice(0, 3000);
                }""")
                art["time_raw"] = await page.evaluate("""() => {
                    const t = document.querySelector('time, .date, .pub-date, [datetime]');
                    return t ? (t.getAttribute('datetime') || t.innerText) : '';
                }""")
            except Exception as e:
                # 本文取得に失敗してもヒット判定は維持（要約は空でWeb/Slackに出す）
                print(f"   [WARN] 本文取得に失敗: {art['title'][:30]} : {e}")
                art["body"] = ""
                art["time_raw"] = ""

        await browser.close()

    return matched, len(candidates)


# =====================
#  キーワードフィルタリング
# =====================
def filter_by_keywords(articles: list[dict], keywords: list[str]) -> list[dict]:
    """タイトルにキーワードを含む記事を返す（厳密マッチ）"""
    results = []
    for art in articles:
        # タイトルのみをチェック（本文は誤検出が多いため）
        matched = [kw for kw in keywords if kw in art["title"]]
        if matched:
            art["tags"] = matched
            results.append(art)
    return results


# =====================
#  OpenRouter(無料LLM) で要約
# =====================
def summarize_article(client: OpenAI, model_name: str, article: dict, length: int) -> str:
    prompt = (
        f"以下の自動車業界ニュース記事を、{length}字程度で日本語要約してください。\n"
        "重要なポイント（数字・企業名・新技術・市場動向）を含めて簡潔にまとめてください。\n\n"
        f"【タイトル】{article['title']}\n\n"
        f"【本文】\n{article.get('body', '')[:1500]}\n\n"
        f"要約（{length}字程度）:"
    )

    # OpenAI互換 chat.completions（429/5xx/timeout は client の max_retries で自動再試行）
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=length * 4 + 100,
    )
    return (response.choices[0].message.content or "").strip()


# =====================
#  Slack 投稿
# =====================
def post_to_slack(cfg: dict, articles: list[dict], date_str: str):
    webhook = cfg["slack"]["webhook_url"]
    if not webhook:
        print("   [SKIP] Slack Webhook URL が未設定です")
        return

    # ヘッダーブロック
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"📰 日刊自動車新聞 トピックスモニター｜{date_str}",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"本日 *{len(articles)}件* の記事がヒットしました。"
            }
        },
        {"type": "divider"}
    ]

    # 各記事ブロック
    # 元記事は有料ログイン制のため、要約を本文としてそのまま読めるよう前面に出す。
    for art in articles:
        tag_text = "  ".join([f"`{t}`" for t in art.get("tags", [])])
        summary = (art.get("summary") or "").strip()
        summary_line = summary if summary else "_（要約を取得できませんでした）_"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{tag_text}\n"
                    f"*<{art['url']}|{art['title']}>* 🔒\n"
                    f"{summary_line}"
                )
            }
        })
        blocks.append({"type": "divider"})

    payload = {"blocks": blocks}
    resp = requests.post(webhook, json=payload, timeout=10)
    if resp.status_code == 200:
        print(f"   → Slack 送信完了 ({len(articles)}件)")
    else:
        print(f"   [WARN] Slack 送信失敗: {resp.status_code} {resp.text}")


# =====================
#  JSON 保存
# =====================
def save_articles(cfg: dict, articles: list[dict], date_str: str, all_count: int):
    out_dir = BASE_DIR / cfg["output"]["data_dir"]
    out_dir.mkdir(exist_ok=True)

    out_path = BASE_DIR / cfg["output"]["articles_file"]

    # 既存データ読み込み（過去分を保持）
    existing = []
    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)

    # 今日のデータ
    today_entry = {
        "date": date_str,
        "fetched_at": datetime.now().strftime("%H:%M"),
        "scanned": all_count,
        "articles": [
            {
                "id": f"{date_str}-{i+1}",
                "tags": art["tags"],
                "time": art.get("fetched_time", ""),
                "title": art["title"],
                "url": art["url"],
                "summary": art.get("summary", ""),  # AI要約（Web/MD出力で表示）
            }
            for i, art in enumerate(articles)
        ]
    }

    # 同日エントリがあれば上書き、なければ先頭に追加
    existing = [e for e in existing if e["date"] != date_str]
    data = [today_entry] + existing[:29]  # 直近30日分を保持

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"   → data/articles.json に保存完了")


# =====================
#  メイン処理
# =====================
async def main():
    cfg = load_config()
    date_str = datetime.now().strftime("%Y年%-m月%-d日")
    # Windows では %-m が動かないため代替
    now = datetime.now()
    date_str = f"{now.year}年{now.month}月{now.day}日"

    print("=" * 50)
    print(f"日刊自動車新聞 トピックスモニター")
    print(f"実行日時: {now.strftime('%Y/%m/%d %H:%M:%S')}")
    print(f"監視キーワード: {', '.join(cfg['keywords'])}")
    print("=" * 50)

    # 1+2. スクレイピング（一覧タイトルでキーワード判定 → ヒット記事の本文取得）
    matched, scanned = await scrape_articles(cfg, cfg["keywords"])
    print(f"\n[フィルタ結果] {len(matched)}/{scanned} 件がヒット")

    if not matched:
        print("本日ヒットした記事はありませんでした。")
        # ヒットなしでも Slack に通知（無音防止）
        webhook = cfg["slack"]["webhook_url"]
        if webhook:
            payload = {"text": f"📰 日刊自動車新聞 {date_str}\n本日はキーワードにヒットした記事はありませんでした（スキャン: {scanned}件）"}
            requests.post(webhook, json=payload, timeout=10)
        save_articles(cfg, [], date_str, scanned)
        return

    # 3. OpenRouter(無料LLM) で要約
    or_cfg = cfg.get("openrouter", {})
    or_api_key = or_cfg.get("api_key", "")
    if or_api_key:
        print("\n[3/4] OpenRouter(無料LLM) で記事を要約中...")
        or_client = OpenAI(
            api_key=or_api_key,
            base_url=or_cfg.get("base_url", "https://openrouter.ai/api/v1"),
            timeout=float(or_cfg.get("timeout", 30)),
            max_retries=int(or_cfg.get("max_retries", 3)),  # 429/5xx/timeout を自動再試行
            default_headers={"X-Title": "nikkan-auto-monitor"},  # OpenRouter 推奨（任意）
        )
        model_name = or_cfg.get("model", "openai/gpt-oss-120b:free")
        summary_length = or_cfg.get("summary_length", 150)
        for i, art in enumerate(matched, 1):
            try:
                art["summary"] = summarize_article(or_client, model_name, art, summary_length)
                print(f"   [{i}/{len(matched)}] 要約完了: {art['title'][:40]}...")
            except Exception as e:
                print(f"   [WARN] 要約失敗: {e}")
                art["summary"] = ""
    else:
        print("\n[3/4] スキップ（OPENROUTER_API_KEY が未設定）")
        for art in matched:
            art["summary"] = ""

    # 4. Slack 投稿
    print("\n[Slack] 投稿中...")
    post_to_slack(cfg, matched, date_str)

    # 5. JSON 保存
    print("\n[保存] articles.json を更新中...")
    save_articles(cfg, matched, date_str, scanned)

    print("\n✅ 完了！")
    for art in matched:
        print(f"  [{', '.join(art['tags'])}] {art['title'][:60]}")


if __name__ == "__main__":
    asyncio.run(main())

"""
日刊自動車新聞 トピックスモニター - バックエンド
  1. netdenjd.com にログインして記事一覧を取得
  2. キーワードにヒットした記事を Gemini で要約
  3. Slack に投稿
  4. data/articles.json に保存（フロントエンドが読む）
"""

import json
import os
import re
import sys
import asyncio
import time
from datetime import datetime
from pathlib import Path

# --- サードパーティ ---
try:
    from playwright.async_api import async_playwright
    from google import genai
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
    # keywords.json があればキーワードを上書き（GitHub上で管理）
    kw_file = BASE_DIR / "keywords.json"
    if kw_file.exists():
        with open(kw_file, encoding="utf-8") as f:
            cfg["keywords"] = json.load(f)
    return cfg

# =====================
#  スクレイピング
# =====================
async def scrape_articles(cfg: dict) -> list[dict]:
    """netdenjd.com にログインして本日の記事一覧を取得する"""
    articles = []

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

        # --- ログイン ---
        print("[1/4] ログイン中...")
        await page.goto(cfg["source"]["login_url"], wait_until="domcontentloaded", timeout=30000)

        # ログインフォームへの入力（セレクタはサイト構造に合わせて要調整）
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

        # --- 記事一覧ページへ ---
        print("[2/4] 記事一覧を取得中...")
        await page.goto(cfg["source"]["url"], wait_until="domcontentloaded", timeout=30000)

        # 記事リンクを収集（セレクタはサイト構造に合わせて要調整）
        links = await page.eval_on_selector_all(
            "a[href]",
            """els => els
                .map(e => ({ href: e.href, text: e.innerText.trim() }))
                .filter(x => x.text.length > 20 && x.href.includes('netdenjd.com'))
            """
        )

        # 重複除去
        seen = set()
        candidates = []
        for lk in links:
            if lk["href"] not in seen and lk["text"]:
                seen.add(lk["href"])
                candidates.append(lk)

        print(f"   → {len(candidates)} 件のリンクを検出")

        # --- 各記事の本文取得 ---
        print("[3/4] 記事本文を取得中...")
        for lk in candidates[:60]:   # 上限60件
            try:
                await page.goto(lk["href"], wait_until="domcontentloaded", timeout=15000)

                # 本文テキスト取得（article タグ or main タグ or body）
                body = await page.evaluate("""() => {
                    const sel = ['article', 'main', '.article-body', '.content', 'body'];
                    for (const s of sel) {
                        const el = document.querySelector(s);
                        if (el) return el.innerText.trim().slice(0, 3000);
                    }
                    return document.body.innerText.trim().slice(0, 3000);
                }""")

                # 掲載時刻の取得を試みる
                time_text = await page.evaluate("""() => {
                    const t = document.querySelector('time, .date, .pub-date, [datetime]');
                    return t ? (t.getAttribute('datetime') || t.innerText) : '';
                }""")

                articles.append({
                    "title": lk["text"][:120],
                    "url": lk["href"],
                    "body": body,
                    "time_raw": time_text,
                })
            except Exception:
                continue

        await browser.close()

    print(f"   → {len(articles)} 件の記事を取得")
    return articles


# =====================
#  キーワードフィルタリング
# =====================
def filter_by_keywords(articles: list[dict], keywords: list[str]) -> list[dict]:
    """タイトルまたは本文にキーワードを含む記事を返す"""
    results = []
    for art in articles:
        matched = [kw for kw in keywords
                   if kw in art["title"] or kw in art.get("body", "")]
        if matched:
            art["tags"] = matched
            results.append(art)
    return results


# =====================
#  Gemini で要約
# =====================
def summarize_article(client, model_name: str, article: dict, length: int) -> str:
    prompt = f"""以下の自動車業界ニュース記事を、{length}字程度で日本語要約してください。
重要なポイント（数字・企業名・新技術・市場動向）を含めて簡潔にまとめてください。

【タイトル】{article['title']}

【本文】
{article.get('body', '')[:2000]}

要約（{length}字程度）:"""

    response = client.models.generate_content(model=model_name, contents=prompt)
    return response.text.strip()


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
    for art in articles:
        tag_text = "  ".join([f"`{t}`" for t in art.get("tags", [])])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{tag_text}\n"
                    f"*<{art['url']}|{art['title']}>*\n"
                    f"{art.get('summary', '')}"
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
                "summary": art.get("summary", ""),
                "url": art["url"],
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

    # 1. スクレイピング
    all_articles = await scrape_articles(cfg)

    # 2. キーワードフィルタリング
    matched = filter_by_keywords(all_articles, cfg["keywords"])
    print(f"\n[フィルタ結果] {len(matched)}/{len(all_articles)} 件がヒット")

    if not matched:
        print("本日ヒットした記事はありませんでした。")
        save_articles(cfg, [], date_str, len(all_articles))
        return

    # 3. Gemini で要約
    print("\n[4/4] Gemini で要約中...")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("   [WARN] GEMINI_API_KEY が未設定のため要約をスキップします")
        for art in matched:
            art["summary"] = "（要約未実行 — GEMINI_API_KEY を設定してください）"
    else:
        client = genai.Client(api_key=api_key)
        model_name = cfg["gemini"]["model"]
        for i, art in enumerate(matched[:20], 1):   # 上限20件（無料枠対策）
            print(f"   {i}/{min(len(matched),20)}: {art['title'][:40]}...")
            try:
                art["summary"] = summarize_article(client, model_name, art, cfg["gemini"]["summary_length"])
            except Exception as e:
                print(f"   [WARN] 要約失敗: {e}")
                art["summary"] = "（要約に失敗しました）"
            time.sleep(4)   # 無料枠: 15req/min → 4秒待機

    # 4. Slack 投稿
    print("\n[Slack] 投稿中...")
    post_to_slack(cfg, matched, date_str)

    # 5. JSON 保存
    print("\n[保存] articles.json を更新中...")
    save_articles(cfg, matched, date_str, len(all_articles))

    print("\n✅ 完了！")
    for art in matched:
        print(f"  [{', '.join(art['tags'])}] {art['title'][:60]}")


if __name__ == "__main__":
    asyncio.run(main())

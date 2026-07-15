"""
日刊自動車新聞 トピックスモニター - バックエンド
  1. netdenjd.com にログインして記事一覧を取得
  2. キーワードにヒットした記事を OpenRouter(無料LLM) で要約
  3. Slack に投稿
  4. data/articles.json に保存（フロントエンドが読む）
"""

import json
import os
import re
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
    """記事一覧の【タイトル】でキーワード判定し、ヒットした記事だけ本文を取得して返す。

    処理順が重要:
      1. 公開トップページから全記事のタイトル＋URLを集める（ログイン前・上限なし）
      2. 全タイトルでキーワード判定 → ヒットを絞り込む
      3. ヒットがあればログインし、ヒット記事だけ本文を深掘りして要約材料にする

    一覧をログイン前の公開ページから取るのが肝。会員ログイン後のトップは一覧が
    絞り込まれ、下部のカテゴリ（自動車流通など）の記事が一覧から消えるため、
    「キーワードはあるのに拾えない」取りこぼしが起きる。
    本文は有料ログイン制なので、要約材料が必要なヒット記事の取得時だけログインする。

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

        # --- 記事一覧を取得（ログイン前の公開トップページ。タイトル＋URLのみ）---
        print("[1/4] 記事一覧のタイトルを取得中...")
        await page.goto(cfg["source"]["url"], wait_until="load", timeout=30000)

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
            # カテゴリ一覧などは記事ではない。キーワードを含む長い title 属性を
            # 持つため、文字数だけの判定では記事として混入してしまう。
            if "/archives/category/" in href or "/archives/tag/" in href:
                continue
            title = lk["title"] if len(lk["title"]) > len(lk["text"]) else lk["text"]
            if title.lower().startswith("view all posts filed under"):
                continue
            if len(title) <= 20:   # ナビ・カテゴリ等の短いリンクを除外
                continue
            seen.add(href)
            candidates.append({"title": title[:120], "url": href})

        print(f"   → {len(candidates)} 件の記事を一覧から検出")

        # --- タイトルでキーワード判定（一覧の全記事が対象）---
        matched = filter_by_keywords(candidates, keywords)
        print(f"   → うち {len(matched)} 件がキーワードにヒット")

        if not matched:
            await browser.close()
            return [], len(candidates)

        # --- ログイン（本文は有料ログイン制のため、要約材料の取得に必要）---
        print("[2/4] ログイン中...")
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
# 無料モデルは配信プロバイダーが1社しかないものが多く、そこが混雑すると
# その日の要約が全滅する。配信元が異なるモデルを並べてフォールバックさせる。
DEFAULT_FALLBACK_MODELS = [
    "qwen/qwen3-next-80b-a3b-instruct:free",   # Venice
    "google/gemma-4-31b-it:free",              # Google AI Studio / OpenInference
    "nvidia/nemotron-3-super-120b-a12b:free",  # Nvidia
    "meta-llama/llama-3.3-70b-instruct:free",  # Venice
]

def summarize_article(client: OpenAI, model_name: str, article: dict, length: int) -> str:
    prompt = (
        f"以下の自動車業界ニュース記事を、{length}字程度で日本語要約してください。\n"
        "重要なポイント（数字・企業名・新技術・市場動向）を含めて簡潔にまとめてください。\n"
        "記事にない情報は推測・補完しないでください。\n"
        "回答は要約本文1段落だけにし、前置き、説明、文字数計算、思考過程、引用符は出力しないでください。\n\n"
        f"【タイトル】{article['title']}\n\n"
        f"【本文】\n{article.get('body', '')[:1500]}\n\n"
        f"要約（{length}字程度）:"
    )

    # OpenAI互換 chat.completions（429/5xx/timeout は client の max_retries で自動再試行）
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": "あなたは日本語のニュース編集者です。完成した要約本文だけを出力します。"
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        # gpt-oss 等の reasoning 系モデルは思考にもトークンを消費するため余裕を持たせる。
        # 足りないと「正常応答なのに本文が空」になり、要約が無言で消える。
        max_tokens=2000,
    )
    return (response.choices[0].message.content or "").strip()


SUMMARY_LEAK_PATTERNS = (
    r"\bwe need\b",
    r"\blet(?:'|’)s\b",
    r"\bcount (?:the )?characters?\b",
    r"\bnow count\b",
    r"\bfinal summary\b",
    r"文字数を(?:数|カウント)",
    r"思考過程",
)


def validate_summary(summary: str, target_length: int) -> tuple[bool, str]:
    """要約本文として不自然な応答を弾き、別モデルへの切り替え理由を返す。"""
    text = summary.strip()
    if not text:
        return False, "空の応答"
    if len(text) > max(500, target_length * 3):
        return False, f"長すぎる応答 ({len(text)}文字)"
    if text.count("\n") > 2:
        return False, "複数段落の応答"
    for pattern in SUMMARY_LEAK_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return False, "思考過程を含む応答"
    # 日本語記事の要約なのに日本語がほぼない回答も採用しない。
    japanese_chars = len(re.findall(r"[ぁ-んァ-ヶ一-龠]", text))
    if japanese_chars < 20:
        return False, "日本語が不足した応答"
    return True, ""


def summarize_with_fallback(client: OpenAI, models: list[str], article: dict,
                            length: int, fail_streak: dict) -> tuple[str, str, str]:
    """モデルを順に試し、最初に取れた要約を返す。

    例外だけでなく「空の要約」も失敗として次のモデルへ切り替える。
    fail_streak はモデルごとの連続失敗回数。2回連続で失敗したモデルは
    この実行内ではスキップして時間を浪費しない（全モデル該当時は全部再挑戦）。

    戻り値: (要約, 使用モデル, 最後のエラー内容)
    """
    candidates = [m for m in models if fail_streak.get(m, 0) < 2] or models
    last_error = ""
    for model_name in candidates:
        try:
            summary = summarize_article(client, model_name, article, length)
        except Exception as e:
            summary = ""
            last_error = f"{model_name}: {e}"
        valid, reason = validate_summary(summary, length)
        if valid:
            fail_streak[model_name] = 0
            return summary, model_name, ""
        fail_streak[model_name] = fail_streak.get(model_name, 0) + 1
        last_error = f"{model_name}: {reason}"
    return "", "", last_error


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

    # 要約失敗を無音にしない（無料LLMの混雑・制限で失敗した日に気づけるように）
    failed_count = sum(1 for a in articles if not (a.get("summary") or "").strip())
    if failed_count:
        blocks.insert(2, {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"⚠️ うち *{failed_count}件* は要約を取得できませんでした（無料LLMの混雑・制限の可能性）"
            }
        })

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
            # モデル自体をフォールバックさせるため、同一モデルへのリトライは最小限にする
            max_retries=int(or_cfg.get("max_retries", 1)),
            default_headers={"X-Title": "nikkan-auto-monitor"},  # OpenRouter 推奨（任意）
        )
        models = [or_cfg.get("model", "openai/gpt-oss-120b:free")]
        models += or_cfg.get("fallback_models", DEFAULT_FALLBACK_MODELS)
        models = list(dict.fromkeys(models))  # 順序維持で重複除去
        summary_length = or_cfg.get("summary_length", 150)
        fail_streak: dict = {}
        for i, art in enumerate(matched, 1):
            summary, used_model, err = summarize_with_fallback(
                or_client, models, art, summary_length, fail_streak)
            art["summary"] = summary
            if summary:
                print(f"   [{i}/{len(matched)}] 要約完了 ({used_model}): {art['title'][:40]}...")
            else:
                print(f"   [WARN] [{i}/{len(matched)}] 全モデルで要約失敗: {err}")
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

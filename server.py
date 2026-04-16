"""
WEB設定からconfig.jsonを更新してGit pushするローカルサーバー
実行: python server.py
"""

import json
import subprocess
import sys
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"


@app.route('/api/save-config', methods=['POST'])
def save_config():
    """config.json を更新してGit pushする"""
    try:
        data = request.json
        config = {
            "source": {
                "url": data.get("source", {}).get("url", "https://www.netdenjd.com/show-paper?date="),
                "login_url": "https://www.netdenjd.com/login",
                "username": data.get("source", {}).get("username", ""),
                "password": data.get("source", {}).get("password", ""),
            },
            "keywords": data.get("keywords", []),
            "slack": {
                "webhook_url": data.get("slack", {}).get("webhook_url", ""),
                "channel": data.get("slack", {}).get("channel", "#auto-news"),
                "message_format": "title_and_summary",
            },
            "schedule": {"time": "07:00", "days": ["月","火","水","木","金"]},
            "gemini": {"model": "gemini-2.0-flash", "summary_length": 150},
            "output": {"data_dir": "data", "articles_file": "data/articles.json"},
        }

        # ローカルの config.json を保存（パスワード含む）
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        # config.json.example も保存（ローカル用、パスワード削除）
        config_example = config.copy()
        config_example["source"]["username"] = ""
        config_example["source"]["password"] = ""
        example_file = BASE_DIR / "config.json.example"
        with open(example_file, "w", encoding="utf-8") as f:
            json.dump(config_example, f, ensure_ascii=False, indent=2)

        # Git commit & push（config.json.example のみ）
        subprocess.run(
            ["git", "add", "config.json.example"],
            cwd=BASE_DIR, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "WEBから設定を更新"],
            cwd=BASE_DIR, check=True
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=BASE_DIR, check=True
        )

        return jsonify({"success": True, "message": "config.json を更新してGit pushしました"})

    except subprocess.CalledProcessError as e:
        return jsonify({"success": False, "message": f"Git操作に失敗しました: {e}"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": f"エラー: {e}"}), 400


@app.route('/health', methods=['GET'])
def health():
    """ヘルスチェック"""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    print("=" * 50)
    print("日刊自動車新聞 設定同期サーバー")
    print("=" * 50)
    print("\n✅ サーバー起動中... http://localhost:3000")
    print("\nこのターミナルを開いたままにしておいてください。")
    print("WEBページから設定を保存すると自動でGit pushします。\n")
    app.run(host="127.0.0.1", port=3000, debug=False)

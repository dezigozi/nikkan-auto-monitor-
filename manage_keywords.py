"""
キーワード管理ツール
使い方:
  python manage_keywords.py list              -- 一覧表示
  python manage_keywords.py add <キーワード>   -- 追加
  python manage_keywords.py remove <キーワード> -- 削除
  python manage_keywords.py edit <旧> <新>    -- 変更
"""

import json
import sys
from pathlib import Path

KW_FILE = Path(__file__).parent / "keywords.json"


def load():
    with open(KW_FILE, encoding="utf-8") as f:
        return json.load(f)


def save(kws):
    with open(KW_FILE, "w", encoding="utf-8") as f:
        json.dump(kws, f, ensure_ascii=False, indent=2)


def cmd_list():
    kws = load()
    print(f"\n現在のキーワード一覧（{len(kws)}件）:")
    for i, kw in enumerate(kws, 1):
        print(f"  {i:2d}. {kw}")
    print()


def cmd_add(word):
    kws = load()
    if word in kws:
        print(f"[SKIP] 「{word}」はすでに登録されています")
        return
    kws.append(word)
    save(kws)
    print(f"[OK] 「{word}」を追加しました（現在 {len(kws)}件）")


def cmd_remove(word):
    kws = load()
    if word not in kws:
        print(f"[ERROR] 「{word}」は登録されていません")
        cmd_list()
        return
    kws.remove(word)
    save(kws)
    print(f"[OK] 「{word}」を削除しました（現在 {len(kws)}件）")


def cmd_edit(old_word, new_word):
    kws = load()
    if old_word not in kws:
        print(f"[ERROR] 「{old_word}」は登録されていません")
        cmd_list()
        return
    if new_word in kws:
        print(f"[SKIP] 「{new_word}」はすでに登録されています")
        return
    idx = kws.index(old_word)
    kws[idx] = new_word
    save(kws)
    print(f"[OK] 「{old_word}」→「{new_word}」に変更しました")


def main():
    args = sys.argv[1:]

    if not args or args[0] == "list":
        cmd_list()

    elif args[0] == "add":
        if len(args) < 2:
            print("[ERROR] キーワードを指定してください")
            print("  例: python manage_keywords.py add デンソー")
            sys.exit(1)
        cmd_add(args[1])

    elif args[0] == "remove":
        if len(args) < 2:
            print("[ERROR] キーワードを指定してください")
            print("  例: python manage_keywords.py remove デンソー")
            sys.exit(1)
        cmd_remove(args[1])

    elif args[0] == "edit":
        if len(args) < 3:
            print("[ERROR] 変更前と変更後のキーワードを指定してください")
            print("  例: python manage_keywords.py edit デンソー DENSO")
            sys.exit(1)
        cmd_edit(args[1], args[2])

    else:
        print(f"[ERROR] 不明なコマンド: {args[0]}")
        print("使い方:")
        print("  python manage_keywords.py list")
        print("  python manage_keywords.py add <キーワード>")
        print("  python manage_keywords.py remove <キーワード>")
        print("  python manage_keywords.py edit <旧キーワード> <新キーワード>")
        sys.exit(1)


if __name__ == "__main__":
    main()

# split_app.py
# -*- coding: utf-8 -*-
import os
import re
from pathlib import Path

SRC = Path("app.py")          # 分割したい元ファイル
OUT_DIR = Path("app_parts")   # 出力先フォルダ

def main():
    text = SRC.read_text(encoding="utf-8")

    # セクション見出し行（"# -----------------------------------------------------------------------------" の次の行）を拾う
    # 例: "# Flask 基本設定", "# Babel（多言語化）" など
    header_pat = re.compile(r"^# -{5,}\s*\n#\s*(.+?)\s*\n# -{5,}\s*$", re.M)

    headers = [(m.start(), m.end(), m.group(1).strip()) for m in header_pat.finditer(text)]
    if not headers:
        raise SystemExit("区切りが見つかりませんでした（# ----------------------------------------------------------------------------- が想定形式か確認してください）")

    OUT_DIR.mkdir(exist_ok=True)

    # 先頭（ヘッダ〜最初の区切りまで）も Part 00 として出す
    first_start = headers[0][0]
    parts = [("00_header_and_imports", text[:first_start])]

    # セクションごとに切る
    for i, (start, end, title) in enumerate(headers, start=1):
        next_start = headers[i][0] if i < len(headers) else len(text)
        body = text[start:next_start]

        safe = re.sub(r"[^\w\-]+", "_", title).strip("_")
        name = f"{i:02d}_{safe}"[:80]
        parts.append((name, body))

    # 書き出し
    for name, body in parts:
        out = OUT_DIR / f"{name}.py"
        out.write_text(body, encoding="utf-8")
        print(f"wrote: {out} ({len(body)} chars)")

    print("\nDone.")
    print(f"出力先: {OUT_DIR.resolve()}")

if __name__ == "__main__":
    main()

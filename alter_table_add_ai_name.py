import os
from dotenv import load_dotenv
load_dotenv()

import psycopg2

import sqlite3

conn = sqlite3.connect("posts.db")
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE posts ADD COLUMN ai_name TEXT;")
    print("✅ カラム 'ai_name' を追加しました。")
except sqlite3.OperationalError as e:
    if "duplicate column name" in str(e):
        print("⚠ カラム 'ai_name' はすでに存在しています。")
    else:
        print(f"⚠ エラーが発生しました: {e}")

conn.commit()
conn.close()

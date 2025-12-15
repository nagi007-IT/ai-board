# alter_table_add_tags.py
import os
from dotenv import load_dotenv
load_dotenv()
import psycopg2

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS tags (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL
);
""")

conn.commit()
conn.close()
print("tagsテーブルを作成しました")

import psycopg2
import os
from dotenv import load_dotenv
load_dotenv()

tags = ["AI", "画像生成", "文章", "チャット", "Python", "API"]

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()
for tag in tags:
    cur.execute("INSERT INTO tags (name) VALUES (%s) ON CONFLICT (name) DO NOTHING;", (tag,))
conn.commit()
conn.close()
print("初期タグを追加しました")

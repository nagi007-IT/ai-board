# alter_table_add_favorites.py
import sqlite3

DB = "posts.db"  # DBの場所が異なる場合は合わせてください

conn = sqlite3.connect(DB)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS favorites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  post_id INTEGER NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, post_id)
);
""")

conn.commit()
conn.close()
print("favorites テーブルOK")

import sqlite3

conn = sqlite3.connect("posts.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS comments (
id INTEGER PRIMARY KEY AUTOINCREMENT,
post_id INTEGER,
name TEXT,
comment TEXT,
created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()
conn.close()
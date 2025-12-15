import sqlite3

conn = sqlite3.connect("posts.db")
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE posts ADD COLUMN chatlog TEXT;")
    print("✅ カラム 'chatlog' を追加しました。")
except sqlite3.OperationalError as e:
    if "duplicate column name" in str(e):
        print("⚠ カラム 'chatlog' はすでに存在しています。")
    else:
        print(f"⚠ エラーが発生しました: {e}")

conn.commit()
conn.close()

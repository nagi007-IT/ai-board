import sqlite3

conn = sqlite3.connect('posts.db')
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(posts);")
columns = cursor.fetchall()
conn.close()

for col in columns:
    print(col)

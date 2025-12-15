import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def add_image_url_column():
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cursor = conn.cursor()
    cursor.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS image_url TEXT;")
    conn.commit()
    conn.close()
    print("✅ image_url カラムを追加しました。")

if __name__ == "__main__":
    add_image_url_column()

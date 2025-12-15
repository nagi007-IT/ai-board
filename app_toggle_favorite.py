
# -*- coding: utf-8 -*-
import sqlite3
import uuid
from flask import Flask, render_template, request, redirect, url_for, session
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "your-secret-key"

def convert_to_jst(utc_str):
    if not utc_str:
        return ""
    try:
        utc_dt = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S")
        jst_dt = utc_dt + timedelta(hours=9)
        return jst_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return utc_str

def init_db():
    conn = sqlite3.connect('posts.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            genre TEXT,
            title TEXT,
            content TEXT,
            tools TEXT,
            chatlog TEXT,
            ai_name TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            author TEXT,
            favorites INTEGER DEFAULT 0
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            name TEXT,
            comment TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS favorite_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            user_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(post_id, user_id)
        )
    ''')

    conn.commit()
    conn.close()

@app.before_request
def assign_user_id():
    if "user_id" not in session:
        session["user_id"] = str(uuid.uuid4())

@app.route("/")
def home():
    return redirect(url_for("show_posts"))

@app.route("/new", methods=["GET", "POST"])
def new_post():
    if request.method == "POST":
        genre = request.form["genre"]
        title = request.form["title"]
        content = request.form["content"]
        tools = request.form["tools"]
        chatlog = request.form["chatlog"]
        ai_name = request.form["ai_name"]
        author = request.form["author"]

        conn = sqlite3.connect('posts.db')
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO posts (genre, title, content, tools, chatlog, ai_name, author) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (genre, title, content, tools, chatlog, ai_name, author)
        )
        conn.commit()
        conn.close()

        return redirect(url_for("show_posts"))

    return render_template("new_post.html")

@app.route("/posts")
def show_posts():
    keyword = request.args.get("q", "").strip()
    genre_filter = request.args.get("genre", "").strip()
    user_id = session.get("user_id")

    conn = sqlite3.connect("posts.db")
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT genre FROM posts ORDER BY genre ASC")
    genres = [row[0] for row in cursor.fetchall() if row[0]]

    if keyword:
        cursor.execute(
            "SELECT id, genre, title, content, tools, chatlog, ai_name, created_at, author, favorites FROM posts WHERE genre LIKE ? OR title LIKE ? OR ai_name LIKE ? OR tools LIKE ? ORDER BY id DESC",
            (f"%{keyword}%",)*4
        )
    elif genre_filter:
        cursor.execute(
            "SELECT id, genre, title, content, tools, chatlog, ai_name, created_at, author, favorites FROM posts WHERE genre = ? ORDER BY id DESC",
            (genre_filter,)
        )
    else:
        cursor.execute(
            "SELECT id, genre, title, content, tools, chatlog, ai_name, created_at, author, favorites FROM posts ORDER BY id DESC"
        )

    posts = [list(post) for post in cursor.fetchall()]
    for post in posts:
        post[7] = convert_to_jst(post[7])
        cursor.execute("SELECT 1 FROM favorite_users WHERE post_id=? AND user_id=?", (post[0], user_id))
        post.append(cursor.fetchone() is not None)

    conn.close()
    return render_template("posts.html", posts=posts, keyword=keyword, genres=genres, selected_genre=genre_filter)

@app.route("/favorite/<int:post_id>", methods=["POST"])
def toggle_favorite(post_id):
    user_id = session.get("user_id")
    conn = sqlite3.connect("posts.db")
    cursor = conn.cursor()

    cursor.execute("SELECT 1 FROM favorite_users WHERE post_id=? AND user_id=?", (post_id, user_id))
    already = cursor.fetchone()

    if already:
        cursor.execute("DELETE FROM favorite_users WHERE post_id=? AND user_id=?", (post_id, user_id))
        cursor.execute("UPDATE posts SET favorites = favorites - 1 WHERE id = ?", (post_id,))
    else:
        cursor.execute("INSERT OR IGNORE INTO favorite_users (post_id, user_id) VALUES (?, ?)", (post_id, user_id))
        cursor.execute("UPDATE posts SET favorites = favorites + 1 WHERE id = ?", (post_id,))

    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("show_posts"))

if __name__ == "__main__":
    init_db()
    print("Flask アプリ起動中")
    app.run(debug=True)

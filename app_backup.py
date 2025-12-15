# -*- coding: utf-8 -*-
import sqlite3
from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime, timedelta

app = Flask(__name__)

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

    cursor.execute("""
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
        favorites INTEGER DEFAULT 0,
        tags TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER,
        name TEXT,
        comment TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS favorites_table (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER UNIQUE
    )
    """)

    conn.commit()
    conn.close()

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
        cursor.execute('''
            INSERT INTO posts (genre, title, content, tools, chatlog, ai_name, author)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (genre, title, content, tools, chatlog, ai_name, author))
        conn.commit()
        conn.close()

        return redirect(url_for("show_posts"))

    return render_template("new_post.html")

@app.route("/posts")
def show_posts():
    keyword = request.args.get("q", "").strip()
    genre_filter = request.args.get("genre", "").strip()
    sort = request.args.get("sort", "new")  # ← 並び順パラメータ（デフォルトは新着）

    conn = sqlite3.connect("posts.db")
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT genre FROM posts ORDER BY genre ASC")
    genres = [row[0] for row in cursor.fetchall() if row[0]]

    # 並び順の条件を決定
    if sort == "old":
        order_clause = "ORDER BY created_at ASC"
    elif sort == "favorites":
        order_clause = "ORDER BY favorites DESC"
    else:
        order_clause = "ORDER BY created_at DESC"

    base_query = """
        SELECT id, genre, title, content, tools, chatlog, ai_name, created_at, author, favorites
        FROM posts
    """

    # 条件によってWHERE句とORDER BY句を構成
    if keyword:
        like_keyword = f"%{keyword}%"
        query = f"""{base_query}
            WHERE genre LIKE ? OR title LIKE ? OR ai_name LIKE ? OR tools LIKE ?
            {order_clause}
        """
        cursor.execute(query, (like_keyword, like_keyword, like_keyword, like_keyword))
    elif genre_filter:
        query = f"""{base_query}
            WHERE genre = ?
            {order_clause}
        """
        cursor.execute(query, (genre_filter,))
    else:
        query = f"""{base_query}
            {order_clause}
        """
        cursor.execute(query)

    posts = [list(post) for post in cursor.fetchall()]
    conn.close()

    for post in posts:
        post[7] = convert_to_jst(post[7])  # 日時をJSTに変換

    return render_template("posts.html", posts=posts, keyword=keyword, genres=genres,
                           selected_genre=genre_filter, sort=sort)


@app.route("/post/<int:post_id>")
def post_detail(post_id):
    conn = sqlite3.connect("posts.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, genre, title, content, tools, chatlog, ai_name, created_at, author, favorites
        FROM posts
        WHERE id = ?
    """, (post_id,))
    post = cursor.fetchone()

    cursor.execute("""
        SELECT id, name, comment, created_at
        FROM comments
        WHERE post_id = ?
        ORDER BY created_at DESC
    """, (post_id,))
    comments = cursor.fetchall()
    conn.close()

    if post:
        post = list(post)
        post[7] = convert_to_jst(post[7])
        comments = [(c[0], c[1], c[2], convert_to_jst(c[3])) for c in comments]
        return render_template("detail.html", post=post, comments=comments, post_id=post_id)
    else:
        return "投稿が見つかりませんでした", 404

@app.route("/edit/<int:post_id>", methods=["GET", "POST"])
def edit_post(post_id):
    conn = sqlite3.connect("posts.db")
    cursor = conn.cursor()

    if request.method == "POST":
        genre = request.form["genre"]
        title = request.form["title"]
        content = request.form["content"]
        tools = request.form["tools"]
        chatlog = request.form["chatlog"]
        ai_name = request.form["ai_name"]

        cursor.execute("""
            UPDATE posts
            SET genre=?, title=?, content=?, tools=?, chatlog=?, ai_name=?
            WHERE id=?
        """, (genre, title, content, tools, chatlog, ai_name, post_id))
        conn.commit()
        conn.close()
        return redirect(url_for("show_posts"))

    cursor.execute("""
        SELECT genre, title, content, tools, chatlog, ai_name, created_at, author
        FROM posts
        WHERE id=?
    """, (post_id,))
    post = cursor.fetchone()
    conn.close()

    if post:
        return render_template("edit_post.html", post=post, post_id=post_id)
    else:
        return "投稿が見つかりませんでした", 404

@app.route("/delete/<int:post_id>", methods=["POST"])
def delete_post(post_id):
    conn = sqlite3.connect("posts.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM posts WHERE id=?", (post_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("show_posts"))

@app.route("/comment/<int:post_id>", methods=["POST"])
def add_comment(post_id):
    name = request.form["name"]
    comment = request.form["comment"]

    conn = sqlite3.connect("posts.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO comments (post_id, name, comment)
        VALUES (?, ?, ?)
    """, (post_id, name, comment))
    conn.commit()
    conn.close()
    return redirect(url_for("post_detail", post_id=post_id))

@app.route("/comment/delete/<int:comment_id>", methods=["POST"])
def delete_comment(comment_id):
    post_id = request.args.get("post_id")
    conn = sqlite3.connect("posts.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM comments WHERE id=?", (comment_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("post_detail", post_id=post_id))

@app.route("/favorite/<int:post_id>", methods=["POST"])
def toggle_favorite(post_id):
    conn = sqlite3.connect("posts.db")
    cursor = conn.cursor()

    # トグル処理：既に押してるか？
    cursor.execute("SELECT id FROM favorites_table WHERE post_id=?", (post_id,))
    existing = cursor.fetchone()

    if existing:
        # 既にお気に入り → 削除＆カウント減らす
        cursor.execute("DELETE FROM favorites_table WHERE post_id=?", (post_id,))
        cursor.execute("UPDATE posts SET favorites = favorites - 1 WHERE id=?", (post_id,))
    else:
        # お気に入り追加
        cursor.execute("INSERT INTO favorites_table (post_id) VALUES (?)", (post_id,))
        cursor.execute("UPDATE posts SET favorites = favorites + 1 WHERE id=?", (post_id,))

    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for("show_posts"))

if __name__ == "__main__":
    init_db()
    print("Flask アプリ起動中")
    app.run(debug=True)

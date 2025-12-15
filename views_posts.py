# views_posts.py
from datetime import datetime
import io

from flask import (
    render_template, request, redirect, url_for,
    flash, make_response, abort
)
from flask_login import login_required, current_user

from app_core import (
    app, _, limiter,
    get_db_connection, cdnize,
    get_all_tags, get_tags_for_posts, get_favorite_counts, get_user_favorites,
    build_posts_filter_sql, sanitize,
    upload_original_and_enqueue_thumb, UploadError,
    pdfcanvas, A4, mm, simpleSplit,
)


# -----------------------------
# /posts : 一覧
# -----------------------------
@app.route("/posts")
def show_posts():
    sort = request.args.get("sort", "new")
    keyword = request.args.get("q", "").strip()
    selected_genre = request.args.get("genre", "").strip() or None
    page = max(1, request.args.get("page", type=int, default=1))
    per_page = app.config["PER_PAGE"]
    offset = (page - 1) * per_page

    tags_param = (request.args.get("tags") or "").strip()
    tag_param = (request.args.get("tag") or "").strip()
    selected_tag_ids: list[int] = []

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        all_tags = get_all_tags(conn)

        # タグ指定パラメータを分解
        if tags_param:
            for tok in tags_param.split(","):
                tok = tok.strip()
                if tok.isdigit():
                    selected_tag_ids.append(int(tok))
        elif tag_param:
            if tag_param.isdigit():
                selected_tag_ids.append(int(tag_param))
            else:
                cur.execute("SELECT id FROM tags WHERE name=%s", (tag_param,))
                r = cur.fetchone()
                if r:
                    selected_tag_ids.append(r[0])

        # 閲覧権限
        is_mod = current_user.is_authenticated and current_user.is_staff

        # ジャンル候補
        if is_mod:
            cur.execute(
                "SELECT DISTINCT genre FROM posts "
                "WHERE genre IS NOT NULL AND genre<>'' "
                "ORDER BY genre"
            )
        else:
            cur.execute(
                "SELECT DISTINCT genre FROM posts "
                "WHERE status='public' AND genre IS NOT NULL AND genre<>'' "
                "ORDER BY genre"
            )
        genres = [r[0] for r in cur.fetchall()]

        # 絞り込み WHERE / ts_rank 用 SQL を生成
        where_sql, params, rank_sql = build_posts_filter_sql(
            keyword, selected_genre, selected_tag_ids, include_non_public=is_mod
        )

        # 件数
        cur.execute(f"SELECT COUNT(*) FROM posts p {where_sql}", params)
        total = cur.fetchone()[0]
        has_prev = page > 1
        has_next = (offset + per_page) < total

        # 並び替え
        order_by = {
            "new": "p.created_at DESC",
            "updated": "p.updated_at DESC NULLS LAST, p.created_at DESC",
            "old": "p.created_at ASC",
            "likes": "favorite_count DESC NULLS LAST, p.created_at DESC",
            "ai": "p.ai_name ASC, p.created_at DESC",
            "relevance": "rank DESC NULLS LAST, p.created_at DESC",
        }.get(sort, "p.created_at DESC")

        # 取得列（列順依存を消すため、後で dict 化する）
        select_cols = """
            p.id, p.genre, p.title, p.content, p.tools, p.chatlog, p.ai_name, p.author,
            p.image_url, p.image_orig_url, p.image_thumb_url,
            p.created_at, p.updated_at, p.status,
            COALESCE(fc.cnt, 0) AS favorite_count
        """
        if rank_sql:
            select_cols += f", {rank_sql}"

        final_params = list(params) + [per_page, offset]

        cur.execute(
            f"""
            WITH fav AS (
                SELECT post_id, COUNT(*) AS cnt
                  FROM favorites
                 GROUP BY post_id
            )
            SELECT {select_cols}
              FROM posts p
              LEFT JOIN fav fc ON fc.post_id = p.id
              {where_sql}
             ORDER BY {order_by}
             LIMIT %s OFFSET %s
            """,
            final_params,
        )

        rows = cur.fetchall()

        # ★重要：fetch直後に列名を確保（この後cur.executeするとdescriptionが上書きされる）
        colnames = [d[0] for d in cur.description]

        # ★listではなくdictにする（posts.html で p.get(...) を使える）
        posts = [dict(zip(colnames, row)) for row in rows]

        # ★一覧カード用の画像URLを確定（thumb優先→なければimage_url→orig）
        for p in posts:
            raw = p.get("image_thumb_url") or p.get("image_url") or p.get("image_orig_url")
            p["card_image_url"] = cdnize(raw) if raw else None

        fav_counts = {p["id"]: (p.get("favorite_count") or 0) for p in posts}
        post_ids = [p["id"] for p in posts]

        # タグ情報・ユーザーのお気に入り
        tags_map = get_tags_for_posts(conn, post_ids) if post_ids else {}
        user_favs = get_user_favorites(conn, current_user.id) if current_user.is_authenticated else {}

        # タグ件数（タグ条件は除外した母数でカウント）
        where_base, params_base = [], []
        if not is_mod:
            where_base.append("p.status='public'")
        if selected_genre:
            where_base.append("p.genre=%s")
            params_base.append(selected_genre)
        if keyword:
            where_base.append("p.search_vec @@ websearch_to_tsquery('simple', %s)")
            params_base.append(keyword)

        where_base_sql = ("WHERE " + " AND ".join(where_base)) if where_base else ""

        cur.execute(
            f"""
            SELECT t.id, t.name, COUNT(*)
              FROM tags t
              JOIN post_tags pt ON pt.tag_id = t.id
              JOIN posts p ON p.id = pt.post_id
              {where_base_sql}
             GROUP BY t.id, t.name
             ORDER BY t.name
            """,
            params_base,
        )
        tag_counts = {row[0]: row[2] for row in cur.fetchall()}

        return render_template(
            "posts.html",
            posts=posts,
            fav_counts=fav_counts,
            user_favs=user_favs,
            sort=sort,
            keyword=keyword,
            genres=genres,
            selected_genre=selected_genre,
            all_tags=all_tags,
            selected_tag_ids=selected_tag_ids,  # ★setにしない（join/順序安定のため）
            tags_map=tags_map,
            page=page,
            has_prev=has_prev,
            has_next=has_next,
            tag_counts=tag_counts,
            total=total,
            page_size=per_page,
        )
    finally:
        conn.close()


# -----------------------------
# /favorites : お気に入り一覧
# -----------------------------
@app.route("/favorites")
@login_required
def my_favorites():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if current_user.is_staff:
            cur.execute(
                """
                SELECT p.* FROM favorites f
                JOIN posts p ON p.id=f.post_id
                WHERE f.user_id=%s
                ORDER BY f.created_at DESC
                """,
                (current_user.id,),
            )
        else:
            cur.execute(
                """
                SELECT p.* FROM favorites f
                JOIN posts p ON p.id=f.post_id
                WHERE f.user_id=%s AND p.status='public'
                ORDER BY f.created_at DESC
                """,
                (current_user.id,),
            )
        posts = cur.fetchall()
        posts = [list(p) for p in posts]

        fav_counts = get_favorite_counts(conn)
        user_favs = {p[0]: True for p in posts}
        return render_template("favorites.html", posts=posts, fav_counts=fav_counts, user_favs=user_favs)
    finally:
        conn.close()

@app.route("/post/<int:post_id>")
def post_detail(post_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 投稿本体
        cur.execute("SELECT * FROM posts WHERE id=%s", (post_id,))
        post = cur.fetchone()
        if not post:
            return render_template("errors/404.html"), 404

        # 非公開の閲覧制御（既存ロジック踏襲）
        is_mod = current_user.is_authenticated and getattr(current_user, "is_staff", False)
        cur.execute("SELECT status FROM posts WHERE id=%s", (post_id,))
        st = cur.fetchone()
        if st and (not is_mod) and st[0] != "public":
            return render_template("errors/404.html"), 404

        # 投稿者をDBから確実に取得（列ズレ対策）
        cur.execute("SELECT author FROM posts WHERE id=%s", (post_id,))
        author_row = cur.fetchone()
        author_username = author_row[0] if author_row else None

        can_edit = (
            current_user.is_authenticated
            and (current_user.username == author_username or getattr(current_user, "is_admin", False))
        )

        # タグ
        cur.execute(
            """
            SELECT t.id, t.name
              FROM post_tags pt
              JOIN tags t ON t.id=pt.tag_id
             WHERE pt.post_id=%s
             ORDER BY t.name
            """,
            (post_id,),
        )
        post_tags = cur.fetchall()

        # コメント
        if is_mod:
            cur.execute(
                """
                SELECT id, post_id, comment, author, created_at, parent_id, depth, path, status
                  FROM comments
                 WHERE post_id=%s
                 ORDER BY COALESCE(path,''), created_at ASC
                """,
                (post_id,),
            )
        else:
            cur.execute(
                """
                SELECT id, post_id, comment, author, created_at, parent_id, depth, path, status
                  FROM comments
                 WHERE post_id=%s AND status='public'
                 ORDER BY COALESCE(path,''), created_at ASC
                """,
                (post_id,),
            )
        comments = cur.fetchall()

        # いいね数
        cur.execute("SELECT COUNT(*) FROM favorites WHERE post_id=%s", (post_id,))
        fav_count = cur.fetchone()[0]

        # 自分がいいね済みか
        is_favorited = False
        if current_user.is_authenticated:
            cur.execute(
                "SELECT 1 FROM favorites WHERE user_id=%s AND post_id=%s",
                (current_user.id, post_id),
            )
            is_favorited = cur.fetchone() is not None

        return render_template(
            "detail.html",
            post=list(post),
            comments=comments,
            favorite_count=fav_count,
            is_favorited=is_favorited,
            post_tags=post_tags,
            author_username=author_username,
            can_edit=can_edit,
        )
    finally:
        conn.close()


# -----------------------------
# PDF Export
# -----------------------------
@app.route("/post/<int:post_id>/export.pdf")
def export_post_pdf(post_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if current_user.is_authenticated and current_user.is_staff:
            cur.execute(
                """
                SELECT id, genre, title, content, tools, chatlog, ai_name, author,
                       image_thumb_url, created_at, updated_at
                  FROM posts WHERE id=%s
                """,
                (post_id,),
            )
        else:
            cur.execute(
                """
                SELECT id, genre, title, content, tools, chatlog, ai_name, author,
                       image_thumb_url, created_at, updated_at
                  FROM posts WHERE id=%s AND status='public'
                """,
                (post_id,),
            )
        row = cur.fetchone()
        if not row:
            return render_template("errors/404.html"), 404
    finally:
        conn.close()

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    width, height = A4

    def draw_title(text, y):
        c.setFont("Helvetica-Bold", 16)
        c.drawString(20 * mm, y, text)
        return y - 10 * mm

    def draw_kv(k, v, y):
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, y, f"{k}:")
        c.setFont("Helvetica", 11)
        for ln in simpleSplit(v or "-", "Helvetica", 11, width - 40 * mm):
            c.drawString(45 * mm, y, ln)
            y -= 6 * mm
        return y - 2 * mm

    y = height - 25 * mm
    y = draw_title(row[2] or "(no title)", y)
    y = draw_kv(_("Genre"), row[1], y)
    y = draw_kv(_("AI used"), row[6], y)
    y = draw_kv(_("Author"), row[7], y)
    y = draw_kv(_("Posted at"), row[9].strftime("%Y-%m-%d %H:%M") if row[9] else "-", y)
    y = draw_kv(_("Updated at"), row[10].strftime("%Y-%m-%d %H:%M") if row[10] else "-", y)
    y = draw_kv(_("Tools"), row[4], y)
    y = draw_kv(_("Content"), row[3], y)
    y = draw_kv(_("Chat log URL"), row[5], y)
    c.showPage()
    c.save()

    pdf = buf.getvalue()
    buf.close()

    resp = make_response(pdf)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'inline; filename="post_{post_id}.pdf"'
    return resp


# -----------------------------
# slug fallback
# -----------------------------
@app.route("/post/<slug>")
def post_slug_fallback(slug):
    if slug.isdigit():
        abort(404)
    return redirect(url_for("user_profile", username=slug), code=301)


# -----------------------------
# /new : 新規投稿
# -----------------------------
@app.route("/new", methods=["GET", "POST"])
@login_required
@limiter.limit("5/minute")
def new_post():
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id, name FROM tags ORDER BY name")
        tags = cur.fetchall()

        if request.method == "POST":
            genre = sanitize(request.form["genre"].strip())
            title = sanitize(request.form["title"].strip())
            content = sanitize(request.form["content"].strip())
            tools = sanitize(request.form["tools"].strip())
            chatlog = sanitize(request.form["chatlog"].strip())
            ai_name = sanitize(request.form["ai_name"].strip())
            author = current_user.username

            if not title or not content:
                flash(_("Title and content are required."), "warning")
                return redirect(url_for("new_post"))

            cur.execute(
                """
                INSERT INTO posts (genre, title, content, tools, chatlog,
                                   ai_name, author, created_at, updated_at, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s, NOW(), NOW(), 'public')
                RETURNING id
                """,
                (genre, title, content, tools, chatlog, ai_name, author),
            )
            post_id = cur.fetchone()[0]

            selected_tag_ids = request.form.getlist("tags")
            for tid in selected_tag_ids:
                cur.execute("INSERT INTO post_tags (post_id, tag_id) VALUES (%s,%s)", (post_id, tid))

            # ★最重要：画像アップロード前に一旦 commit（別コネクションUPDATE問題を回避）
            conn.commit()

            image = request.files.get("image")
            if image and image.filename:
                try:
                    upload_original_and_enqueue_thumb(image, post_id=post_id)
                except UploadError as ue:
                    flash(str(ue), "warning")
                except Exception:
                    app.logger.exception("upload enqueue failed")
                    flash(_("Image upload failed."), "warning")

            conn.commit()
            flash(_("Post created."), "success")
            return redirect(url_for("show_posts"))

        return render_template("new_post.html", tags=tags)
    finally:
        conn.close()


# -----------------------------
# /edit/<id> : 編集（タグ編集対応）
# -----------------------------
@app.route("/edit/<int:post_id>", methods=["GET", "POST"])
@login_required
@limiter.limit("10/minute")
def edit_post(post_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 投稿取得
        cur.execute("SELECT * FROM posts WHERE id=%s", (post_id,))
        post = cur.fetchone()
        if not post:
            return render_template("errors/404.html"), 404

        # ★author をDBから確実に取得（列ズレ対策）
        cur.execute("SELECT author FROM posts WHERE id=%s", (post_id,))
        author_row = cur.fetchone()
        author_username = author_row[0] if author_row else None

        # 権限：投稿者 or 管理者のみ
        if (author_username != current_user.username) and (not current_user.is_admin):
            return render_template("errors/403.html"), 403

        # タグ一覧（選択肢）
        cur.execute("SELECT id, name FROM tags ORDER BY name")
        all_tags = cur.fetchall()

        # 現在の投稿タグ（選択済み）
        cur.execute(
            """
            SELECT t.id
              FROM post_tags pt
              JOIN tags t ON t.id = pt.tag_id
             WHERE pt.post_id=%s
            """,
            (post_id,),
        )
        selected_tag_ids = {r[0] for r in cur.fetchall()}

        if request.method == "POST":
            title = sanitize(request.form["title"].strip())
            content = sanitize(request.form["content"].strip())
            tools = sanitize(request.form["tools"].strip())
            chatlog = sanitize(request.form["chatlog"].strip())
            ai_name = sanitize(request.form["ai_name"].strip())
            genre = sanitize(request.form["genre"].strip())

            # ★ 追加：タグ（複数）
            raw_tag_ids = request.form.getlist("tags")  # ["1","3",...]
            tag_ids = []
            for x in raw_tag_ids:
                try:
                    tag_ids.append(int(x))
                except (TypeError, ValueError):
                    continue

            if not title or not content:
                flash(_("Title and content are required."), "warning")
                return redirect(url_for("edit_post", post_id=post_id))

            # 本体更新
            cur.execute(
                """
                UPDATE posts
                   SET title=%s, content=%s, tools=%s, chatlog=%s,
                       ai_name=%s, genre=%s, updated_at=NOW()
                 WHERE id=%s
                """,
                (title, content, tools, chatlog, ai_name, genre, post_id),
            )

            # ★ タグ更新：いったん全削除→入れ直し（付け替えに強い）
            cur.execute("DELETE FROM post_tags WHERE post_id=%s", (post_id,))
            if tag_ids:
                for tid in tag_ids:
                    cur.execute(
                        "INSERT INTO post_tags (post_id, tag_id) VALUES (%s, %s)",
                        (post_id, tid),
                    )

            # ここまでを確定
            conn.commit()

            # 画像差し替え（既存の動き維持）
            image = request.files.get("image")
            if image and image.filename:
                try:
                    upload_original_and_enqueue_thumb(image, post_id=post_id)
                except UploadError as ue:
                    flash(str(ue), "warning")
                except Exception:
                    app.logger.exception("reupload enqueue failed")
                    flash(_("Image upload failed."), "warning")

            conn.commit()
            flash(_("Post updated."), "success")
            return redirect(url_for("post_detail", post_id=post_id))

        # GET：編集画面へ（タグ情報も渡す）
        return render_template(
            "edit_post.html",
            post=post,
            all_tags=all_tags,
            selected_tag_ids=selected_tag_ids,
        )
    finally:
        conn.close()


# -----------------------------
# /delete/<id> : 削除
# -----------------------------
@app.route("/delete/<int:post_id>", methods=["POST"])
@login_required
@limiter.limit("30/hour")
def delete_post(post_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT author FROM posts WHERE id=%s", (post_id,))
        row = cur.fetchone()
        if not row:
            return render_template("errors/404.html"), 404
        if (row[0] != current_user.username) and (not current_user.is_admin):
            return render_template("errors/403.html"), 403
        cur.execute("DELETE FROM posts WHERE id=%s", (post_id,))
        conn.commit()
        flash(_("Post deleted."), "info")
        return redirect(url_for("show_posts"))
    finally:
        conn.close()


# -----------------------------
# コメント追加
# -----------------------------
@app.route("/comment/<int:post_id>", methods=["POST"])
@login_required
@limiter.limit("20/minute")
def add_comment(post_id):
    text = sanitize(request.form["comment"].strip())
    parent_id = request.form.get("parent_id", type=int)
    author = current_user.username

    if not text:
        flash(_("Comment cannot be empty."), "warning")
        return redirect(url_for("post_detail", post_id=post_id))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        depth, path = 0, None
        if parent_id:
            cur.execute("SELECT id, path, depth FROM comments WHERE id=%s AND post_id=%s", (parent_id, post_id))
            pr = cur.fetchone()
            if pr:
                parent_path = pr[1] or f"{pr[0]:06d}"
                depth = (pr[2] or 0) + 1
                path = f"{parent_path}/"

        cur.execute(
            """
            INSERT INTO comments (post_id, comment, author, created_at,
                                  parent_id, depth, path, status)
            VALUES (%s,%s,%s,NOW(),%s,%s,%s,'public') RETURNING id
            """,
            (post_id, text, author, parent_id, depth, path),
        )
        cid = cur.fetchone()[0]
        new_path = f"{cid:06d}" if path is None else f"{path}{cid:06d}"
        cur.execute("UPDATE comments SET path=%s WHERE id=%s", (new_path, cid))

        conn.commit()
        flash(_("Comment posted."), "success")
        return redirect(url_for("post_detail", post_id=post_id))
    finally:
        conn.close()


# -----------------------------
# コメント削除
# -----------------------------
@app.route("/comment/delete/<int:comment_id>", methods=["POST"])
@login_required
@limiter.limit("30/hour")
def delete_comment(comment_id):
    post_id = request.args.get("post_id", type=int)
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT author, post_id FROM comments WHERE id=%s", (comment_id,))
        row = cur.fetchone()
        if not row:
            return render_template("errors/404.html"), 404
        c_author, c_post_id = row
        post_id = post_id or c_post_id

        cur.execute("SELECT author FROM posts WHERE id=%s", (post_id,))
        pr = cur.fetchone()
        allowed = (current_user.username in (c_author, pr[0]) or current_user.is_admin)
        if not allowed:
            return render_template("errors/403.html"), 403

        cur.execute("DELETE FROM comments WHERE id=%s", (comment_id,))
        conn.commit()
        flash(_("Comment deleted."), "info")
        return redirect(url_for("post_detail", post_id=post_id))
    finally:
        conn.close()


# -----------------------------
# お気に入りトグル
# -----------------------------
@app.route("/favorite/<int:post_id>", methods=["POST"], endpoint="toggle_favorite")
@login_required
@limiter.limit("60/minute")
def toggle_favorite(post_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM favorites WHERE user_id=%s AND post_id=%s", (current_user.id, post_id))
        exists = cur.fetchone()
        if exists:
            cur.execute("DELETE FROM favorites WHERE user_id=%s AND post_id=%s", (current_user.id, post_id))
            flash(_("Removed from favorites."), "info")
        else:
            if not current_user.is_staff:
                cur.execute("SELECT status FROM posts WHERE id=%s", (post_id,))
                st = cur.fetchone()
                if not st or st[0] != "public":
                    flash(_("You cannot favorite this post right now."), "warning")
                    return redirect(url_for("post_detail", post_id=post_id))
            cur.execute("INSERT INTO favorites (user_id, post_id) VALUES (%s,%s)", (current_user.id, post_id))
            flash(_("Added to favorites."), "success")

        conn.commit()
        return redirect(url_for("post_detail", post_id=post_id))
    finally:
        conn.close()


# -----------------------------
# ユーザープロフィール
# -----------------------------
@app.route("/user/<username>")
def user_profile(username):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        if current_user.is_authenticated and current_user.is_staff:
            cur.execute("SELECT * FROM posts WHERE author=%s ORDER BY created_at DESC", (username,))
        else:
            cur.execute(
                "SELECT * FROM posts WHERE author=%s AND status='public' ORDER BY created_at DESC",
                (username,),
            )
        posts = cur.fetchall()
        posts = [list(p) for p in posts]
        return render_template("user.html", username=username, posts=posts)
    finally:
        conn.close()

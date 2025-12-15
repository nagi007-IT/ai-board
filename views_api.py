# views_api.py
import io
import json
import csv
from datetime import datetime

from flask import Response, make_response, request, jsonify
from flask_login import current_user

from app_core import (
    app, _, cdnize,
    get_db_connection,
    build_posts_filter_sql,
)


@app.route("/export/posts.csv")
def export_posts_csv():
    sort = request.args.get("sort", "new")
    keyword = request.args.get("q", "").strip()
    selected_genre = request.args.get("genre", "").strip() or None
    tags_param = (request.args.get("tags") or "").strip()

    selected_tag_ids = [int(x) for x in tags_param.split(",") if x.strip().isdigit()] if tags_param else []

    is_mod = current_user.is_authenticated and current_user.is_staff
    where_sql, params, rank_sql = build_posts_filter_sql(
        keyword, selected_genre, selected_tag_ids, include_non_public=is_mod
    )

    order_by = {
        "new": "p.created_at DESC",
        "updated": "p.updated_at DESC NULLS LAST, p.created_at DESC",
        "old": "p.created_at ASC",
        "likes": "favorite_count DESC NULLS LAST, p.created_at DESC",
        "ai": "p.ai_name ASC, p.created_at DESC",
        "relevance": "rank DESC NULLS LAST, p.created_at DESC",
    }.get(sort, "p.created_at DESC")

    select_cols = "p.*, COALESCE(fc.cnt,0) AS favorite_count"
    final_params = []
    if rank_sql:
        select_cols += f", {rank_sql}"
        final_params.append(keyword)
    final_params += params

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        WITH fav AS (SELECT post_id, COUNT(*) AS cnt FROM favorites GROUP BY post_id)
        SELECT {select_cols}
          FROM posts p
          LEFT JOIN fav fc ON fc.post_id=p.id
          {where_sql}
         ORDER BY {order_by}
        """,
        final_params,
    )
    rows = cur.fetchall()
    conn.close()

    def _generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "genre", "title", "content", "tools", "chatlog", "ai_name",
                         "author", "image_url", "created_at", "updated_at", "image_orig_url",
                         "image_thumb_url", "status", "favorite_count", "rank(if any)"])
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)

        for r in rows:
            writer.writerow([x if not isinstance(x, datetime) else x.isoformat(sep=" ") for x in r])
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

    resp = Response(_generate(), content_type="text/csv; charset=utf-8")
    resp.headers["Content-Disposition"] = "attachment; filename=posts.csv"
    return resp


@app.route("/export/posts.json")
def export_posts_json():
    sort = request.args.get("sort", "new")
    keyword = request.args.get("q", "").strip()
    selected_genre = request.args.get("genre", "").strip() or None
    tags_param = (request.args.get("tags") or "").strip()
    selected_tag_ids = [int(x) for x in tags_param.split(",") if x.strip().isdigit()] if tags_param else []

    is_mod = current_user.is_authenticated and current_user.is_staff
    where_sql, params, rank_sql = build_posts_filter_sql(
        keyword, selected_genre, selected_tag_ids, include_non_public=is_mod
    )

    order_by = {
        "new": "p.created_at DESC",
        "updated": "p.updated_at DESC NULLS LAST, p.created_at DESC",
        "old": "p.created_at ASC",
        "likes": "favorite_count DESC NULLS LAST, p.created_at DESC",
        "ai": "p.ai_name ASC, p.created_at DESC",
        "relevance": "rank DESC NULLS LAST, p.created_at DESC",
    }.get(sort, "p.created_at DESC")

    select_cols = "p.*, COALESCE(fc.cnt,0) AS favorite_count"
    final_params = []
    if rank_sql:
        select_cols += f", {rank_sql}"
        final_params.append(keyword)
    final_params += params

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        f"""
        WITH fav AS (SELECT post_id, COUNT(*) AS cnt FROM favorites GROUP BY post_id)
        SELECT {select_cols}
          FROM posts p
          LEFT JOIN fav fc ON fc.post_id=p.id
          {where_sql}
         ORDER BY {order_by}
        """,
        final_params,
    )
    rows = cur.fetchall()
    conn.close()

    data = []
    for r in rows:
        rec = {
            "id": r[0],
            "genre": r[1],
            "title": r[2],
            "content": r[3],
            "tools": r[4],
            "chatlog": r[5],
            "ai_name": r[6],
            "author": r[7],
            "image_url": r[8],
            "created_at": r[9].isoformat(sep=" ") if isinstance(r[9], datetime) else r[9],
            "updated_at": r[10].isoformat(sep=" ") if isinstance(r[10], datetime) else r[10],
            "image_orig_url": r[11] if len(r) > 11 else None,
            "image_thumb_url": r[12] if len(r) > 12 else None,
            "status": r[13] if len(r) > 13 else None,
            "favorite_count": r[-1] if rank_sql is None else r[-2],
        }
        if rank_sql is not None:
            rec["rank"] = r[-1]
        data.append(rec)

    resp = make_response(json.dumps(data, ensure_ascii=False, indent=2))
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    return resp


@app.route("/api/posts")
def api_posts():
    limit = max(1, min(100, request.args.get("limit", type=int, default=20)))
    offset = max(0, request.args.get("offset", type=int, default=0))
    q = (request.args.get("q") or "").strip()

    conn = get_db_connection()
    cur = conn.cursor()
    if q:
        cur.execute(
            """
            SELECT id, genre, title, content, tools, chatlog, ai_name, author, image_thumb_url, created_at, updated_at
              FROM posts
             WHERE status='public' AND search_vec @@ websearch_to_tsquery('simple', %s)
             ORDER BY created_at DESC
             LIMIT %s OFFSET %s
            """,
            (q, limit, offset),
        )
    else:
        cur.execute(
            """
            SELECT id, genre, title, content, tools, chatlog, ai_name, author, image_thumb_url, created_at, updated_at
              FROM posts
             WHERE status='public'
             ORDER BY created_at DESC
             LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
    rows = cur.fetchall()
    conn.close()

    data = []
    for r in rows:
        data.append(
            {
                "id": r[0],
                "genre": r[1],
                "title": r[2],
                "content": r[3],
                "tools": r[4],
                "chatlog": r[5],
                "ai_name": r[6],
                "author": r[7],
                "image_thumb_url": cdnize(r[8]),
                "created_at": r[9].isoformat(sep=" ") if isinstance(r[9], datetime) else r[9],
                "updated_at": r[10].isoformat(sep=" ") if isinstance(r[10], datetime) else r[10],
            }
        )
    return jsonify(data)


@app.route("/api/posts/<int:post_id>")
def api_post_detail(post_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, genre, title, content, tools, chatlog, ai_name, author, image_thumb_url, created_at, updated_at, status
          FROM posts WHERE id=%s
        """,
        (post_id,),
    )
    r = cur.fetchone()
    conn.close()
    if not r or r[11] != "public":
        return jsonify({"error": "not found"}), 404

    return jsonify(
        {
            "id": r[0],
            "genre": r[1],
            "title": r[2],
            "content": r[3],
            "tools": r[4],
            "chatlog": r[5],
            "ai_name": r[6],
            "author": r[7],
            "image_thumb_url": cdnize(r[8]),
            "created_at": r[9].isoformat(sep=" ") if isinstance(r[9], datetime) else r[9],
            "updated_at": r[10].isoformat(sep=" ") if isinstance(r[10], datetime) else r[10],
        }
    )

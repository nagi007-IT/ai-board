# views_notify.py
from datetime import datetime

from flask import jsonify, request
from flask_login import login_required, current_user

from app_core import app, limiter, get_db_connection, get_notify_counts_for_user


# 通知カウント API はポーリングされるのでレート制限除外
@limiter.exempt
@app.route("/_notify_counts")
@login_required
def notify_counts_api():
    return jsonify(get_notify_counts_for_user(current_user.id, current_user.username))


@app.route("/_notify_read", methods=["POST"])
@login_required
def notify_mark_read():
    typ = request.form.get("type")  # or 'all'
    now = datetime.utcnow()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO user_reads (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (current_user.id,),
    )
    if typ == "all":
        cur.execute(
            """
            UPDATE user_reads
               SET comments_on_my_posts=%s,
                   replies_to_me=%s,
                   favorites_on_my_posts=%s
             WHERE user_id=%s
            """,
            (now, now, now, current_user.id),
        )
    elif typ in ("comments_on_my_posts", "replies_to_me", "favorites_on_my_posts"):
        cur.execute(f"UPDATE user_reads SET {typ}=%s WHERE user_id=%s", (now, current_user.id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# テンプレ用に常に notify_counts を埋め込む
@app.context_processor
def inject_notify_counts():
    if current_user.is_authenticated:
        try:
            return {"notify_counts": get_notify_counts_for_user(current_user.id, current_user.username)}
        except Exception:
            app.logger.exception("notify count failed")
    return {"notify_counts": {"comments_on_my_posts": 0, "replies_to_me": 0, "favorites_on_my_posts": 0}}

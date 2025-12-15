# views_admin.py
from functools import wraps

from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user

from app_core import app, get_db_connection, sanitize, _


def staff_required(f):
    @wraps(f)
    @login_required
    def _wrap(*args, **kwargs):
        if not current_user.is_staff:
            return render_template("errors/403.html"), 403
        return f(*args, **kwargs)
    return _wrap


@app.route("/admin")
@staff_required
def admin_dashboard():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM posts")
    total_posts = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM comments")
    total_comments = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM reports WHERE status='open'")
    open_reports = cur.fetchone()[0]
    conn.close()
    return render_template("admin.html", total_posts=total_posts, total_comments=total_comments, open_reports=open_reports)


@app.route("/report", methods=["POST"])
@login_required
def create_report():
    target_type = request.form.get("target_type")
    target_id = request.form.get("target_id", type=int)
    reason = sanitize((request.form.get("reason") or "").strip()[:500])
    if target_type not in ("post", "comment") or not target_id:
        flash(_("Invalid report."), "warning")
        return redirect(request.referrer or url_for("show_posts"))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO reports (target_type, target_id, reporter_id, reason, status, created_at)
            VALUES (%s,%s,%s,%s,'open',NOW())
            ON CONFLICT (target_type, target_id, reporter_id) DO NOTHING
            """,
            (target_type, target_id, current_user.id, reason),
        )
        conn.commit()
        flash(_("Reported. Thank you."), "info")
    except Exception:
        conn.rollback()
        app.logger.exception("report failed")
        flash(_("Failed to submit report."), "danger")
    finally:
        conn.close()

    return redirect(request.referrer or url_for("show_posts"))


@app.route("/moderate/post/<int:post_id>/<action>", methods=["POST"])
@staff_required
def moderate_post(post_id, action):
    conn = get_db_connection()
    cur = conn.cursor()
    new_status = None
    if action == "hide":
        new_status = "hidden"
    elif action == "unhide":
        new_status = "public"
    elif action == "delete":
        new_status = "deleted"

    if new_status:
        cur.execute("UPDATE posts SET status=%s, updated_at=NOW() WHERE id=%s", (new_status, post_id))
        cur.execute(
            """
            INSERT INTO moderation_actions (moderator_id, action, target_type, target_id, note, created_at)
            VALUES (%s,%s,'post',%s,NULL,NOW())
            """,
            (current_user.id, action, post_id),
        )
        conn.commit()
        flash(_("Post moderation applied."), "success")
    conn.close()
    return redirect(request.referrer or url_for("post_detail", post_id=post_id))


@app.route("/moderate/comment/<int:comment_id>/<action>", methods=["POST"])
@staff_required
def moderate_comment(comment_id, action):
    conn = get_db_connection()
    cur = conn.cursor()
    new_status = None
    if action == "hide":
        new_status = "hidden"
    elif action == "unhide":
        new_status = "public"
    elif action == "delete":
        cur.execute("DELETE FROM comments WHERE id=%s", (comment_id,))
        cur.execute(
            """
            INSERT INTO moderation_actions (moderator_id, action, target_type, target_id, note, created_at)
            VALUES (%s,%s,'comment',%s,NULL,NOW())
            """,
            (current_user.id, action, comment_id),
        )
        conn.commit()
        conn.close()
        flash(_("Comment removed."), "success")
        return redirect(request.referrer or url_for("show_posts"))

    if new_status:
        cur.execute("UPDATE comments SET status=%s WHERE id=%s", (new_status, comment_id))
        cur.execute(
            """
            INSERT INTO moderation_actions (moderator_id, action, target_type, target_id, note, created_at)
            VALUES (%s,%s,'comment',%s,NULL,NOW())
            """,
            (current_user.id, action, comment_id),
        )
        conn.commit()
        flash(_("Comment moderation applied."), "success")
    conn.close()
    return redirect(request.referrer or url_for("show_posts"))

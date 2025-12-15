# views_base.py
from flask import render_template, redirect, url_for, request, send_from_directory, abort, session, flash
from werkzeug.exceptions import RequestEntityTooLarge
from flask_wtf.csrf import CSRFError

from app_core import app, LANGUAGES, _  # _ は flask_babel.gettext

# ルート "/" → 投稿一覧へ
@app.route("/")
def home():
    return redirect(url_for("show_posts"))

# favicon を static から返す
@app.route("/favicon.ico")
def favicon():
    import os
    static_dir = os.path.join(app.root_path, "static")
    path = os.path.join(static_dir, "favicon.ico")
    if os.path.exists(path):
        return send_from_directory(static_dir, "favicon.ico")
    abort(404)

# /public/* → /static/* 互換
@app.route("/public/<path:filename>")
def public_files(filename):
    import os
    static_dir = os.path.join(app.root_path, "static")
    return send_from_directory(static_dir, filename)

# /public 直アクセスは無音で無視（204）
@app.route("/public", endpoint="public_root_silent")
def public_root_silent():
    return "", 204

# 言語切り替え
@app.route("/i18n/set/<lang>")
def i18n_set(lang):
    if lang in LANGUAGES:
        session["lang"] = lang
        flash(_("Language changed."), "info")
    else:
        flash(_("Unsupported language."), "warning")
    return redirect(request.referrer or url_for("show_posts"))

# --- エラーハンドラ ---
@app.errorhandler(403)
def err_403(e):
    return render_template("errors/403.html"), 403


@app.errorhandler(404)
def err_404(e):
    return render_template("errors/404.html"), 404


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash(_("Security token expired or invalid. Please retry."), "warning")
    return redirect(request.referrer or url_for("show_posts"))


@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(e):
    flash(_("File too large."), "warning")
    return redirect(request.referrer or url_for("show_posts"))

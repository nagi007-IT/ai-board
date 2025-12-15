# views_auth.py
from flask import render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required

from app_core import app, sanitize, get_db_connection, User, check_password_hash, generate_password_hash, _


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = sanitize(request.form["username"].strip())
        password = request.form["password"]
        if not username or not password:
            flash(_("Please enter username & password."), "warning")
            return redirect(url_for("register"))
        hashed = generate_password_hash(password)
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO users (username, password) VALUES (%s,%s)", (username, hashed))
            conn.commit()
            flash(_("Registered. Please log in."), "success")
            return redirect(url_for("login"))
        except Exception:
            conn.rollback()
            app.logger.exception("register failed")
            flash(_("Registration failed. Try another username."), "danger")
        finally:
            conn.close()
    return render_template("register.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    return redirect(url_for("register"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, password, COALESCE(role,'user'), COALESCE(status,'active') FROM users WHERE username=%s",
            (username,),
        )
        row = cur.fetchone()
        conn.close()
        if row and check_password_hash(row[2], password):
            if row[4] in ("banned",):
                flash(_("Your account is banned."), "danger")
                return render_template("login.html")
            if row[4] in ("suspended",):
                flash(_("Your account is suspended."), "warning")
                return render_template("login.html")
            login_user(User(*row))
            flash(_("Logged in."), "success")
            return redirect(url_for("show_posts"))
        flash(_("Invalid credentials."), "danger")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash(_("Logged out."), "info")
    return redirect(url_for("login"))

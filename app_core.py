# -*- coding: utf-8 -*-
"""
Flask ポートフォリオ - コアモジュール app_core.py

- Flask アプリ生成
- ログ / レート制限 / Babel / CSRF / LoginManager
- DB 接続 & スキーママイグレーション
- 画像アップロード（S3 + RQ サムネ生成）
- 通知・タグ・検索ヘルパ
- Jinja フィルタ（cdn, datetime_jp）
"""

import os
import io
import csv
import json
import time
import logging
import secrets
from logging.handlers import RotatingFileHandler
from datetime import datetime
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

from flask import (
    Flask,
    request,
    g,
    redirect,
    url_for,
    flash,
    Response,
    jsonify,
    session,
    make_response,
    send_from_directory,
)
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,  # noqa: F401  （他モジュールで使う前提）
)
from werkzeug.security import generate_password_hash, check_password_hash  # noqa: F401
from werkzeug.utils import secure_filename

# CSRF
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError, generate_csrf  # noqa: F401

# i18n（Babel 3/4 両対応）
from flask_babel import Babel, gettext as _

# サニタイズ
import bleach

# レート制限
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import psycopg2
import boto3

# RQ / Redis（サムネ生成のジョブ化）
import redis
import rq

# Pillow（サムネイル生成）
from PIL import Image, UnidentifiedImageError, ImageFile

# PDF（個別エクスポート用）
from reportlab.pdfgen import canvas as pdfcanvas  # noqa: F401
from reportlab.lib.pagesizes import A4  # noqa: F401
from reportlab.lib.units import mm  # noqa: F401
from reportlab.lib.utils import simpleSplit  # noqa: F401

from werkzeug.exceptions import RequestEntityTooLarge  # noqa: F401

# 画像読み込み時の安全ガード
ImageFile.LOAD_TRUNCATED_IMAGES = False
Image.MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", "25000000"))

# -----------------------------------------------------------------------------
# Flask 基本設定
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.jinja_env.add_extension("jinja2.ext.do")
app.secret_key = os.getenv("SECRET_KEY", "defaultsecret")

# CSRF 保護
csrf = CSRFProtect(app)

app.config.update(
    PER_PAGE=int(os.getenv("PER_PAGE", 8)),
    LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO").upper(),
    LOG_TO_FILE=os.getenv("LOG_TO_FILE", "0") == "1",
    ADMIN_USERNAMES=set(
        u.strip() for u in os.getenv("ADMIN_USERNAMES", "").split(",") if u.strip()
    ),
    WTF_CSRF_CHECK_DEFAULT=True,
    WTF_CSRF_TIME_LIMIT=60 * 60 * 8,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "1") == "1",
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "5")) * 1024 * 1024,
    BABEL_TRANSLATION_DIRECTORIES="translations",
)

# 画像の許可拡張子 / MIME
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/jpg",
    "image/pjpeg",
}

# ---- bleach 設定 ----
BLEACH_TAGS = ["b", "i", "strong", "em", "br", "p", "ul", "ol", "li", "a", "code", "pre"]
BLEACH_ATTRS = {"a": ["href", "rel", "target"]}


def sanitize(s: str) -> str:
    return bleach.clean(s or "", tags=BLEACH_TAGS, attributes=BLEACH_ATTRS, strip=True)


# ---- CSRF トークンを Jinja から利用できるようにする ----
@app.context_processor
def inject_csrf_token():
    # base.html で <input type="hidden" name="csrf_token" value="{{ csrf_token() }}"> が使える
    return dict(csrf_token=generate_csrf)


# ---- レートリミット初期化（Redis が無ければ自動フォールバック）----
default_rate = os.getenv("RATELIMIT_DEFAULT", "200/hour")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    redis.from_url(REDIS_URL).ping()
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[default_rate],
        storage_uri=REDIS_URL,
        strategy="moving-window",
    )
    app.logger.info("Rate limit storage: Redis (%s)", REDIS_URL)
except Exception:
    limiter = Limiter(
        key_func=get_remote_address,
        app=app,
        default_limits=[default_rate],
        storage_uri="memory://",
        strategy="moving-window",
    )
    app.logger.warning(
        "Redis not available. Falling back to in-memory rate limit storage."
    )

# -----------------------------------------------------------------------------
# Babel（多言語化）
# -----------------------------------------------------------------------------
LANGUAGES = [
    x.strip() for x in (os.getenv("LANGUAGES") or "ja,en").split(",") if x.strip()
]
DEFAULT_LOCALE = LANGUAGES[0] if LANGUAGES else "ja"
app.config.setdefault("BABEL_DEFAULT_LOCALE", DEFAULT_LOCALE)


def _select_locale():
    """優先順: 1) ?lang= 2) セッション 3) Accept-Language 4) DEFAULT_LOCALE"""
    lang = (request.args.get("lang") or "").lower()
    if lang in LANGUAGES:
        if session.get("lang") != lang:
            session["lang"] = lang
        return lang
    lang = (session.get("lang") or "").lower()
    if lang in LANGUAGES:
        return lang
    best = request.accept_languages.best_match(LANGUAGES)
    return best or app.config["BABEL_DEFAULT_LOCALE"]


try:
    babel = Babel(app, locale_selector=_select_locale)  # 4.x
except TypeError:
    babel = Babel(app)  # 3.x

    try:

        @babel.localeselector
        def _legacy_locale():
            return _select_locale()

    except AttributeError:
        app.logger.warning("Babel: localeselector API not found")

# Jinja から {{ _('text') }} が呼べるように
app.jinja_env.globals.update(_=_)

# -----------------------------------------------------------------------------
# ログ
# -----------------------------------------------------------------------------
def setup_logging():
    level = getattr(logging, app.config["LOG_LEVEL"], logging.INFO)
    app.logger.setLevel(level)
    for h in list(app.logger.handlers):
        app.logger.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    sh = logging.StreamHandler()
    sh.setLevel(level)
    sh.setFormatter(fmt)
    app.logger.addHandler(sh)

    if app.config["LOG_TO_FILE"]:
        log_dir = os.path.join(os.path.dirname(__file__), "logs")
        os.makedirs(log_dir, exist_ok=True)
        fh = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(fmt)
        app.logger.addHandler(fh)


setup_logging()


@app.before_request
def _start_timer():
    g._t0 = time.perf_counter()


@app.after_request
def _log_request(response):
    try:
        dur_ms = int(
            (time.perf_counter() - getattr(g, "_t0", time.perf_counter())) * 1000
        )
    except Exception:
        dur_ms = -1

    if request.path == "/favicon.ico" and response.status_code == 404:
        app.logger.debug("404 for /favicon.ico (Referer=%s)", request.referrer or "-")
    else:
        app.logger.info(
            "%s %s %s %sms UA=%s IP=%s",
            request.method,
            request.full_path,
            response.status_code,
            dur_ms,
            request.headers.get("User-Agent", "-")[:80],
            request.remote_addr,
        )

    if request.path.startswith(("/api/", "/export/")):
        response.headers["Cache-Control"] = "public, max-age=30"
    return response


# -----------------------------------------------------------------------------
# 認証（Flask-Login）
# -----------------------------------------------------------------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


def get_db_connection():
    """
    DATABASE_URL / SUPABASE_DB_* から Postgres に接続します。

    - DATABASE_URL がURL形式ならそれを優先
    - DATABASE_URL がホスト名だけ、または未設定なら SUPABASE_DB_* から組み立てる
    - postgres:// を postgresql:// に補正
    - sslmode / connect_timeout は環境変数で調整可能
    """
    raw_dsn = os.getenv("DATABASE_URL")

    # 1. DATABASE_URL がフルURL形式なら優先して使う
    if raw_dsn and "://" in raw_dsn:
        dsn = raw_dsn
        # Heroku 互換（postgres:// → postgresql://）
        if dsn.startswith("postgres://"):
            dsn = "postgresql://" + dsn[len("postgres://") :]
    else:
        # 2. DATABASE_URL が無い or 単なるホスト名のときは SUPABASE_DB_* から組み立てる
        host = (raw_dsn or os.getenv("SUPABASE_DB_HOST") or "localhost").strip()
        port = os.getenv("SUPABASE_DB_PORT", "5432").strip()
        dbname = os.getenv("SUPABASE_DB_NAME", "postgres").strip()
        user = os.getenv("SUPABASE_DB_USER", "postgres").strip()
        password = os.getenv("SUPABASE_DB_PASSWORD", "").strip()

        if not host:
            raise RuntimeError(
                "DATABASE_URL / SUPABASE_DB_HOST が設定されていません。"
            )

        # postgresql://user:pass@host:port/dbname 形式に組み立て
        dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"

    u = urlparse(dsn)
    if not u.hostname:
        raise RuntimeError(
            "DATABASE_URL / SUPABASE_DB_* が不正です（hostname が取れません）。"
        )

    # host だけログ（パスワード等は絶対に出さない）
    app.logger.debug(
        "DB connect target host=%s db=%s user=%s",
        u.hostname,
        (u.path or "/").lstrip("/"),
        u.username,
    )

    connect_timeout = int(os.getenv("PG_CONNECT_TIMEOUT", "5"))

    # sslmode:
    # - DSN 文字列に sslmode= が既に含まれるならそれを尊重
    # - 無ければ DB_SSLMODE（指定が無ければ prefer）を使う
    sslmode_env = os.getenv("DB_SSLMODE", "prefer")

    if "sslmode=" in dsn:
        return psycopg2.connect(dsn, connect_timeout=connect_timeout)
    return psycopg2.connect(
        dsn, connect_timeout=connect_timeout, sslmode=sslmode_env
    )


class User(UserMixin):
    def __init__(self, id, username, password, role="user", status="active"):
        self.id = id
        self.username = username
        self.password = password
        self.role = role or "user"
        self.status = status or "active"

    @property
    def is_admin(self):
        return self.role == "admin" or (self.username in app.config["ADMIN_USERNAMES"])

    @property
    def is_moderator(self):
        return self.is_admin or self.role == "moderator"

    @property
    def is_staff(self):
        return self.is_moderator


@login_manager.user_loader
def load_user(user_id):
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, password, COALESCE(role,'user'), COALESCE(status,'active') FROM users WHERE id=%s",
            (int(user_id),),
        )
        row = cur.fetchone()
        return User(*row) if row else None
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# RQ / Redis（サムネ生成ジョブ）
# -----------------------------------------------------------------------------
def _s3_client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("S3_REGION"),
    )


# ENABLE_RQ の値で RQ を使うかどうか切り替える
USE_RQ = os.getenv("ENABLE_RQ", "0") == "1"

if USE_RQ:
    try:
        redis_conn = redis.from_url(REDIS_URL)
        rq_queue = rq.Queue("thumbnails", connection=redis_conn, default_timeout=180)
        app.logger.info("RQ thumbnail queue enabled (Redis=%s)", REDIS_URL)
    except Exception:
        redis_conn = None
        rq_queue = None
        app.logger.warning("Redis not available for RQ; thumbnail queue disabled.")
else:
    redis_conn = None
    rq_queue = None
    app.logger.info("ENABLE_RQ=0; thumbnail queue is disabled.")



def rq_generate_thumbnail(post_id, orig_url):
    """バックグラウンド: オリジナルからサムネ生成 → S3 → posts.image_thumb_url 更新"""
    s3 = _s3_client()
    bucket = os.getenv("S3_BUCKET_NAME")
    region = os.getenv("S3_REGION")
    if not bucket or not region or not orig_url:
        return

    s3_prefix = f"https://{bucket}.s3.{region}.amazonaws.com/"
    if orig_url.startswith(s3_prefix):
        key = orig_url[len(s3_prefix) :]
    else:
        key = orig_url.split("/", 3)[-1]

    tmp = io.BytesIO()
    s3.download_fileobj(bucket, key, tmp)
    tmp.seek(0)

    img = Image.open(tmp).convert("RGB")

    # 16:9 センタークロップ + 幅 max 900
    w, h = img.size
    target = 16 / 9
    cur_ratio = w / h
    if cur_ratio > target:
        new_w = int(h * target)
        x1 = (w - new_w) // 2
        img = img.crop((x1, 0, x1 + new_w, h))
    else:
        new_h = int(w / target)
        y1 = (h - new_h) // 2
        img = img.crop((0, y1, w, y1 + new_h))

    if img.width > 900:
        img = img.resize(
            (900, int(img.height * (900 / img.width))), Image.LANCZOS
        )

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    out.seek(0)

    base = os.path.splitext(os.path.basename(key))[0]
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    thumb_key = f"thumb_{base}_{ts}.jpg"

    # ▼▼▼ 修正：ACL を付けずにアップロード ▼▼▼
    s3.upload_fileobj(
        out,
        bucket,
        thumb_key,
        ExtraArgs={"ContentType": "image/jpeg"},
    )
    # ▲▲▲ ここまで ▲▲▲

    thumb_url = f"https://{bucket}.s3.{region}.amazonaws.com/{thumb_key}"


    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE posts SET image_thumb_url=%s, updated_at=NOW() WHERE id=%s",
        (thumb_url, post_id),
    )
    conn.commit()
    conn.close()
    return thumb_url


# -----------------------------------------------------------------------------
# CDN 置換（Jinja フィルタ）
# -----------------------------------------------------------------------------
def cdnize(url: str) -> str:
    if not url:
        return url
    cf = os.getenv("CDN_DOMAIN")
    bucket = os.getenv("S3_BUCKET_NAME")
    region = os.getenv("S3_REGION")
    if not (cf and bucket and region):
        return url
    s3_prefix = f"https://{bucket}.s3.{region}.amazonaws.com/"
    if url.startswith(s3_prefix):
        return url.replace(s3_prefix, f"https://{cf}/")
    return url


@app.template_filter("cdn")
def jinja_filter_cdn(url):
    return cdnize(url)


@app.template_filter("datetime_jp")
def datetime_jp(value):
    if not value:
        return "-"
    try:
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:
        try:
            return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(value)


# -----------------------------------------------------------------------------
# スキーマ
# -----------------------------------------------------------------------------
def ensure_schema():
    conn = get_db_connection()
    cur = conn.cursor()

    # posts
    cur.execute(
        "ALTER TABLE posts ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP"
    )
    cur.execute(
        """
        DO $$
        BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='posts' AND column_name='updated_at') THEN
            ALTER TABLE posts ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='posts' AND column_name='image_orig_url') THEN
            ALTER TABLE posts ADD COLUMN image_orig_url TEXT;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='posts' AND column_name='image_thumb_url') THEN
            ALTER TABLE posts ADD COLUMN image_thumb_url TEXT;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='posts' AND column_name='status') THEN
            ALTER TABLE posts ADD COLUMN status TEXT DEFAULT 'public';
        END IF;
        END $$;
        """
    )
    cur.execute(
        """
        DO $$
        BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='posts_status_check') THEN
            ALTER TABLE posts ADD CONSTRAINT posts_status_check CHECK (status IN ('public','hidden','deleted'));
        END IF;
        END $$;
        """
    )

    # tags / post_tags / favorites
    cur.execute(
        """CREATE TABLE IF NOT EXISTS tags (id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE);"""
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS post_tags (
        post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (post_id, tag_id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL,
        post_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, post_id)
        );
        """
    )

    # comments
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
        id SERIAL PRIMARY KEY,
        post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
        comment TEXT NOT NULL,
        author TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute(
        """
        DO $$
        BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='parent_id') THEN
            ALTER TABLE comments ADD COLUMN parent_id INTEGER REFERENCES comments(id) ON DELETE CASCADE;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='depth') THEN
            ALTER TABLE comments ADD COLUMN depth INTEGER DEFAULT 0;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='path') THEN
            ALTER TABLE comments ADD COLUMN path TEXT;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='status') THEN
            ALTER TABLE comments ADD COLUMN status TEXT DEFAULT 'public';
        END IF;
        END $$;
        """
    )
    cur.execute(
        """
        DO $$
        BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='comments_status_check') THEN
            ALTER TABLE comments ADD CONSTRAINT comments_status_check CHECK (status IN ('public','hidden','deleted'));
        END IF;
        END $$;
        """
    )

    # users: role/status
    cur.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user',
        ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';
        """
    )
    cur.execute(
        """
        DO $$
        BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='users_role_check') THEN
            ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('user','moderator','admin'));
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='users_status_check') THEN
            ALTER TABLE users ADD CONSTRAINT users_status_check CHECK (status IN ('active','suspended','banned','shadowbanned'));
        END IF;
        END $$;
        """
    )

    # reports / moderation_actions
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
        id SERIAL PRIMARY KEY,
        target_type TEXT NOT NULL CHECK (target_type IN ('post','comment')),
        target_id INTEGER NOT NULL,
        reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE (target_type, target_id, reporter_id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moderation_actions (
        id SERIAL PRIMARY KEY,
        moderator_id INTEGER NOT NULL REFERENCES users(id),
        action TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id INTEGER NOT NULL,
        note TEXT,
        created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )

    # indexes
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    except Exception:
        pass

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at DESC);"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_post_tags_post ON post_tags(post_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_post_tags_tag ON post_tags(tag_id);")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_fav_user_created ON favorites(user_id, created_at DESC);"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fav_post ON favorites(post_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_comments_path ON comments(post_id, path);"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_modact_target ON moderation_actions(target_type, target_id);"
    )

    conn.commit()
    conn.close()


def ensure_search_schema():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        DO $$
        BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='posts' AND column_name='search_vec') THEN
            ALTER TABLE posts ADD COLUMN search_vec tsvector;
            UPDATE posts SET search_vec = to_tsvector(
            'simple',
            coalesce(genre,'')||' '||coalesce(title,'')||' '||coalesce(content,'')||' '||
            coalesce(tools,'')||' '||coalesce(ai_name,'')||' '||coalesce(author,'')
            );
        END IF;
        END $$;
        """
    )
    cur.execute(
        """
        CREATE OR REPLACE FUNCTION posts_search_tsv_update() RETURNS trigger AS $$
        BEGIN
        NEW.search_vec := to_tsvector(
            'simple',
            coalesce(NEW.genre,'')||' '||coalesce(NEW.title,'')||' '||coalesce(NEW.content,'')||' '||
            coalesce(NEW.tools,'')||' '||coalesce(NEW.ai_name,'')||' '||coalesce(NEW.author,'')
        );
        RETURN NEW;
        END $$ LANGUAGE plpgsql;
        """
    )
    cur.execute(
        """
        DO $$
        BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='posts_searchvec_trigger') THEN
            CREATE TRIGGER posts_searchvec_trigger
            BEFORE INSERT OR UPDATE OF genre,title,content,tools,ai_name,author
            ON posts FOR EACH ROW
            EXECUTE FUNCTION posts_search_tsv_update();
        END IF;
        END $$;
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_posts_search_vec ON posts USING GIN (search_vec);"
    )
    conn.commit()
    conn.close()


def ensure_notify_schema():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_reads (
        user_id INTEGER PRIMARY KEY,
        comments_on_my_posts TIMESTAMP,
        replies_to_me TIMESTAMP,
        favorites_on_my_posts TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()


# -----------------------------------------------------------------------------
# 通知ユーティリティ
# -----------------------------------------------------------------------------
def get_notify_last_reads(conn, user_id):
    cur = conn.cursor()
    cur.execute(
        "SELECT comments_on_my_posts, replies_to_me, favorites_on_my_posts FROM user_reads WHERE user_id=%s",
        (user_id,),
    )
    row = cur.fetchone()
    if row:
        return {
            "comments_on_my_posts": row[0],
            "replies_to_me": row[1],
            "favorites_on_my_posts": row[2],
        }
    return {
        "comments_on_my_posts": None,
        "replies_to_me": None,
        "favorites_on_my_posts": None,
    }


def get_notify_counts_for_user(user_id, username):
    conn = get_db_connection()
    cur = conn.cursor()
    last = get_notify_last_reads(conn, user_id)

    # 1) 自分の投稿への新規コメント
    params = [username, username]
    where_time = ""
    if last["comments_on_my_posts"]:
        where_time = "AND c.created_at > %s"
        params.append(last["comments_on_my_posts"])
    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM comments c
        JOIN posts p ON p.id=c.post_id
        WHERE p.author=%s AND c.author<>%s {where_time}
        """,
        params,
    )
    cnt_comments = cur.fetchone()[0]

    # 2) 自分のコメントへの新規返信
    params = [username, username]
    where_time = ""
    if last["replies_to_me"]:
        where_time = "AND c.created_at > %s"
        params.append(last["replies_to_me"])
    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM comments c
        WHERE c.parent_id IN (SELECT id FROM comments WHERE author=%s)
        AND c.author<>%s {where_time}
        """,
        params,
    )
    cnt_replies = cur.fetchone()[0]

    # 3) 自分の投稿への新規お気に入り
    params = [username, username]
    where_time = ""
    if last["favorites_on_my_posts"]:
        where_time = "AND f.created_at > %s"
        params.append(last["favorites_on_my_posts"])
    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM favorites f
        JOIN posts p ON p.id=f.post_id
        JOIN users u ON u.id=f.user_id
        WHERE p.author=%s AND u.username<>%s {where_time}
        """,
        params,
    )
    cnt_favs = cur.fetchone()[0]

    conn.close()
    return {
        "comments_on_my_posts": cnt_comments,
        "replies_to_me": cnt_replies,
        "favorites_on_my_posts": cnt_favs,
    }


# -----------------------------------------------------------------------------
# 画像アップロード（S3 + CDN 化）
# -----------------------------------------------------------------------------
class UploadError(ValueError):
    pass


def upload_bytes_to_s3(bytes_io, filename, content_type="image/jpeg"):
    bucket = os.getenv("S3_BUCKET_NAME")
    region = os.getenv("S3_REGION")
    if not bucket:
        raise ValueError("S3_BUCKET_NAME is not set")
    s3 = _s3_client()
    bytes_io.seek(0)

    # ▼▼▼ 修正：ACL を付けずにアップロード ▼▼▼
    s3.upload_fileobj(
        bytes_io,
        bucket,
        filename,
        ExtraArgs={"ContentType": content_type},
    )
    # ▲▲▲ ここまで ▲▲▲

    url = f"https://{bucket}.s3.{region}.amazonaws.com/{filename}"
    return cdnize(url)



def validate_image_file(file_storage):
    """拡張子なしのファイル名も許容しつつ、安全性をチェック"""
    if not file_storage or not file_storage.filename:
        raise UploadError(_("No file selected."))

    filename = secure_filename(file_storage.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    mime = (file_storage.mimetype or "").lower()

    # デバッグ用ログ
    app.logger.info(
        "image upload: filename=%s ext=%s mime=%s",
        filename,
        ext or "(none)",
        mime,
    )

    # 明示的に非対応にしたい拡張子
    if ext in {".heic", ".heif", ".avif"}:
        raise UploadError(
            _("Unsupported extension (jpg/jpeg/png/webp only). Please convert to JPG/PNG.")
        )

    # 拡張子が付いている場合だけホワイトリストチェック
    if ext and ext not in ALLOWED_IMAGE_EXTS:
        raise UploadError(_("Unsupported extension (jpg/jpeg/png/webp only)."))

    # MIME タイプは必ずチェック
    if mime and mime not in ALLOWED_IMAGE_MIMES:
        raise UploadError(_("Invalid image MIME type."))

    # サイズチェック
    max_len = app.config.get("MAX_CONTENT_LENGTH")
    content_len = request.content_length or getattr(
        file_storage, "content_length", None
    )
    if max_len and content_len and content_len > max_len:
        raise UploadError(
            _("File too large (max %(n)s MB).", n=max_len // (1024 * 1024))
        )

    # 実際に画像として開けるか検証
    try:
        file_storage.stream.seek(0)
        with Image.open(file_storage.stream) as im:
            im.verify()
        file_storage.stream.seek(0)
    except Image.DecompressionBombError:
        raise UploadError(_("Suspiciously large image."))
    except UnidentifiedImageError:
        raise UploadError(_("Unrecognized image file."))
    except Exception:
        raise UploadError(_("Image validation failed."))

def upload_original_and_enqueue_thumb(file_storage, *, post_id):
    """元画像を S3 に保存し、サムネ生成ジョブをキューに入れる"""
    validate_image_file(file_storage)

    rand = secrets.token_urlsafe(16)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    orig_key = f"orig_{rand}_{ts}.jpg"

    # 画像 → JPEG 変換
    buf = io.BytesIO()
    try:
        img = Image.open(file_storage.stream).convert("RGB")
        img.save(buf, format="JPEG", quality=92, optimize=True)
    except Exception as e:
        raise UploadError(f"画像の処理に失敗しました: {e}") from e

    # S3 にアップロード（ここで失敗したら本当にアップロード失敗）
    try:
        orig_url_cdn = upload_bytes_to_s3(buf, orig_key, "image/jpeg")
    except Exception as e:
        raise UploadError(f"S3 へのアップロードに失敗しました: {e}") from e

    # DB にオリジナル画像 URL を保存
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE posts SET image_orig_url=%s, updated_at=NOW() WHERE id=%s",
        (orig_url_cdn, post_id),
    )
    conn.commit()
    conn.close()

    # RQ が有効なときだけ、サムネ生成をバックグラウンドキューに積む
    # 失敗してもアップロード自体は成功扱いにする
    if rq_queue is not None:
        try:
            rq_queue.enqueue(rq_generate_thumbnail, post_id, orig_url_cdn)
        except Exception:
            app.logger.exception(
                "Thumbnail enqueue failed (post_id=%s). Continue without background thumbnail.",
                post_id,
            )
    else:
        app.logger.debug(
            "RQ queue is disabled; skip thumbnail enqueue for post_id=%s", post_id
        )

    return orig_url_cdn



# -----------------------------------------------------------------------------
# タグ/お気に入りヘルパ
# -----------------------------------------------------------------------------
def get_all_tags(conn):
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM tags ORDER BY name")
    return cur.fetchall()


def get_tags_for_posts(conn, post_ids):
    tags_map = {}
    if not post_ids:
        return tags_map
    cur = conn.cursor()
    placeholders = ",".join(["%s"] * len(post_ids))
    cur.execute(
        f"""
        SELECT pt.post_id, t.id, t.name
        FROM post_tags pt
        JOIN tags t ON t.id=pt.tag_id
        WHERE pt.post_id IN ({placeholders})
        ORDER BY t.name
        """,
        post_ids,
    )
    for post_id, tag_id, tag_name in cur.fetchall():
        tags_map.setdefault(post_id, []).append((tag_id, tag_name))
    return tags_map


def get_favorite_counts(conn):
    cur = conn.cursor()
    cur.execute("SELECT post_id, COUNT(*) FROM favorites GROUP BY post_id")
    return {row[0]: row[1] for row in cur.fetchall()}


def get_user_favorites(conn, user_id):
    cur = conn.cursor()
    cur.execute("SELECT post_id FROM favorites WHERE user_id=%s", (user_id,))
    return {row[0]: True for row in cur.fetchall()}


# -----------------------------------------------------------------------------
# 検索フィルタ SQL
# -----------------------------------------------------------------------------
def build_posts_filter_sql(
    keyword, selected_genre, selected_tag_ids, *, include_non_public=False
):
    where, params, rank_sql = [], [], None
    if not include_non_public:
        where.append("p.status='public'")
    if selected_genre:
        where.append("p.genre=%s")
        params.append(selected_genre)
    if keyword:
        where.append("p.search_vec @@ websearch_to_tsquery('simple', %s)")
        params.append(keyword)
        rank_sql = (
            "ts_rank(p.search_vec, websearch_to_tsquery('simple', %s)) AS rank"
        )
    if selected_tag_ids:
        where.append(
            """
            p.id IN (
            SELECT pt.post_id
                FROM post_tags pt
            WHERE pt.tag_id = ANY(%s)
            GROUP BY pt.post_id
            HAVING COUNT(DISTINCT pt.tag_id) = %s
            )
            """
        )
        params.append(selected_tag_ids)
        params.append(len(selected_tag_ids))
    return ("WHERE " + " AND ".join(where)) if where else "", params, rank_sql


# -----------------------------------------------------------------------------
# 起動前初期化
# -----------------------------------------------------------------------------
def _init_all():
    try:
        ensure_schema()
        ensure_search_schema()
        ensure_notify_schema()
        app.logger.info("Schema ensured.")
    except Exception:
        app.logger.exception("Schema init failed")


_init_all()

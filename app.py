# app.py
"""
エントリーポイント用 app.py
- app_core で Flask アプリ本体を生成
- 各 views_* モジュールを import してルートを登録
"""

import os
from app_core import app  # Flask インスタンス

# ルート群を import（順不同でOK）
import views_base      # noqa: F401
import views_auth      # noqa: F401
import views_posts     # noqa: F401
import views_admin     # noqa: F401
import views_api       # noqa: F401
import views_notify    # noqa: F401

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)

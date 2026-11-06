"""FIX-01 cross-process Worker boot.

- 屏蔽 pymilvus.settings 导入期 `load_dotenv()`（它会无视 ENV_FILE 读取 backend/.env）；
- 可选把在线评测报告目录指向隔离临时目录（FIX01_REPORTS_DIR）；
- 复用生产 celery_app，不修改任何生产配置。
"""

from __future__ import annotations

import os

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False  # noqa: E731

from app.workers.celery_app import celery_app  # noqa: E402

_reports = (os.getenv("FIX01_REPORTS_DIR") or "").strip()
if _reports:
    from pathlib import Path

    import app.enterprise_rag.routes as rag_routes  # noqa: E402

    rag_routes.REPORTS_DIR = Path(_reports)

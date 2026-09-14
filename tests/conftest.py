"""Test configuration.

Environment defaults are set BEFORE any app module is imported, because
`app.core.config.settings` is constructed at import time. Without this a
developer's local `.env` leaks into the run and CI behaves differently from a
laptop — the worst kind of flake to debug.
"""

from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("AUTH_DISABLED", "true")
os.environ.setdefault("AUTH_ISSUER", "http://localhost:8001")
os.environ.setdefault("AUTH_AUDIENCE", "teachassist-api")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://teachassist:teachassist@localhost:5432/lmsdb_test"
)
# CI runs Postgres as a service container on localhost, where TLS is neither
# available nor meaningful.
os.environ.setdefault("DB_SSL_MODE", "disable")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("QUEUE_BACKEND", "postgres")
os.environ.setdefault("LANGFUSE_ENABLED", "false")

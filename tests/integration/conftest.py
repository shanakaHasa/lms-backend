"""Fixtures for the tests that need a real Postgres.

These are skipped by default (`-m "not integration"` in `pyproject.toml`) and
run in Phase 2, once `alembic upgrade head` has been applied against a real
database. Writing them now is deliberate: the alternative is authoring a test
suite at the same moment as debugging a first migration against a cloud
database, which is the worst possible time to be doing both.

**Each test runs inside a transaction that is rolled back**, so the tests share
one database without sharing state and leave nothing behind.
`join_transaction_mode="create_savepoint"` is what makes that work while the
code under test still calls `session.commit()` — the commit releases a
savepoint rather than committing the outer transaction, so production code needs
no test-only branches.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.core.security import Principal

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

ALL_SCOPES = frozenset(
    {
        "students:read",
        "students:write",
        "courses:read",
        "courses:write",
        "enrolments:write",
    }
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(settings.database_url)
    try:
        connection = await engine.connect()
    except Exception as exc:  # pragma: no cover - only when no database is up
        await engine.dispose()
        pytest.skip(f"no database available: {exc}")

    transaction = await connection.begin()
    db = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield db
    finally:
        await db.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.fixture
def principal_a() -> Principal:
    return Principal(
        user_id="teacher-a", tenant_id=TENANT_A, email="a@example.com", scopes=ALL_SCOPES
    )


@pytest.fixture
def principal_b() -> Principal:
    return Principal(
        user_id="teacher-b", tenant_id=TENANT_B, email="b@example.com", scopes=ALL_SCOPES
    )

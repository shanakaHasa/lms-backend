"""A test client over the real app, with only storage faked.

What is **not** overridden here is the point: the routes, the services, the
scope gates, the error handlers and the response models are all production code.
Only the four repository providers are swapped, and they are rebuilt exactly the
way `app.core.deps` builds them — from `principal.tenant_id` and nothing else.

That distinction is what makes the tenant-isolation matrix meaningful. Faking
the *service* providers would have been easier and would have replaced the very
wiring under test; faking storage leaves the whole chain intact — bearer token →
`Principal` → tenant-scoped repository → service that accepts no tenant
argument — with only the SQL swapped out. The SQL is covered separately, in
`test_repository_sql.py`, by compiling each statement against the Postgres
dialect.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core import deps
from app.core.security import Principal
from app.main import create_app
from tests.fakes import (
    FakeAuditSink,
    FakeCourseRepository,
    FakeEnrolmentRepository,
    FakeStudentRepository,
    Store,
)

__all__ = ["ALL_SCOPES", "as_tenant", "build_client"]

ALL_SCOPES = frozenset(
    {
        "students:read",
        "students:write",
        "courses:read",
        "courses:write",
        "enrolments:write",
    }
)


def as_tenant(tenant: str = "tenant-a", scopes: frozenset[str] = ALL_SCOPES) -> Principal:
    return Principal(
        user_id=f"teacher-{tenant}",
        tenant_id=tenant,
        email="teacher@example.com",
        scopes=scopes,
    )


def build_client(store: Store, principal: Principal) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.current_principal] = lambda: principal
    app.dependency_overrides[deps.get_student_repository] = lambda: FakeStudentRepository(
        store, principal.tenant_id
    )
    app.dependency_overrides[deps.get_course_repository] = lambda: FakeCourseRepository(
        store, principal.tenant_id
    )
    app.dependency_overrides[deps.get_enrolment_repository] = lambda: FakeEnrolmentRepository(
        store, principal.tenant_id
    )
    app.dependency_overrides[deps.get_audit_sink] = FakeAuditSink
    return TestClient(app)

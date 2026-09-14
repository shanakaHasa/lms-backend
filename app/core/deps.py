"""FastAPI dependencies: the seam between HTTP and the service layer.

Routers depend on these; services depend on nothing from FastAPI. That split is
what lets the CLI, the eval runner and the MCP server drive exactly the same
code the HTTP layer does.

The wiring is split deliberately at the **repository**, not at the service. The
tenant-isolation suite overrides the repository providers with in-memory fakes
and leaves the real services and real routes in place, so what those tests
exercise is the genuine chain — token → principal → tenant-scoped storage — with
only the SQL swapped out. Overriding the service providers instead would replace
the very wiring under test. The SQL itself is covered separately, by compiling
each statement against the Postgres dialect.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import Unauthorized
from app.core.logging import actor_id_ctx, tenant_id_ctx
from app.core.security import Principal, verify_token
from app.repositories.course import CourseRepository, SqlCourseRepository
from app.repositories.enrolment import EnrolmentRepository, SqlEnrolmentRepository
from app.repositories.student import SqlStudentRepository, StudentRepository
from app.services.audit import AuditSink, SqlAuditSink
from app.services.course_service import CourseService
from app.services.enrolment_service import EnrolmentService
from app.services.student_service import StudentService


async def current_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    from app.core.config import settings

    if settings.auth_disabled:
        from app.core.security import LOCAL_PRINCIPAL

        _bind_log_context(LOCAL_PRINCIPAL)
        return LOCAL_PRINCIPAL

    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")

    principal = await verify_token(authorization.split(" ", 1)[1])
    _bind_log_context(principal)
    return principal


def _bind_log_context(principal: Principal) -> None:
    # Every subsequent log line in this request carries who and which tenant,
    # which is what makes an audit row traceable back to a request.
    tenant_id_ctx.set(principal.tenant_id)
    actor_id_ctx.set(principal.user_id)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
DbSession = Annotated[AsyncSession, Depends(get_session)]


# ── Storage ─────────────────────────────────────────────────────────────────
# Each repository is constructed from `principal.tenant_id` and nothing else.
# This is the single place a tenant enters the data layer, so it is the single
# place that could get it wrong.


def get_student_repository(session: DbSession, principal: CurrentPrincipal) -> StudentRepository:
    return SqlStudentRepository(session, principal.tenant_id)


def get_course_repository(session: DbSession, principal: CurrentPrincipal) -> CourseRepository:
    return SqlCourseRepository(session, principal.tenant_id)


def get_enrolment_repository(
    session: DbSession, principal: CurrentPrincipal
) -> EnrolmentRepository:
    return SqlEnrolmentRepository(session, principal.tenant_id)


def get_audit_sink(session: DbSession, principal: CurrentPrincipal) -> AuditSink:
    # Same session as the action, so the two commit or roll back together.
    return SqlAuditSink(session, principal)


StudentRepo = Annotated[StudentRepository, Depends(get_student_repository)]
CourseRepo = Annotated[CourseRepository, Depends(get_course_repository)]
EnrolmentRepo = Annotated[EnrolmentRepository, Depends(get_enrolment_repository)]
Audit = Annotated[AuditSink, Depends(get_audit_sink)]


# ── Services ────────────────────────────────────────────────────────────────


def get_student_service(
    repo: StudentRepo, audit: Audit, principal: CurrentPrincipal
) -> StudentService:
    return StudentService(repo, audit, principal)


def get_course_service(
    repo: CourseRepo, audit: Audit, principal: CurrentPrincipal
) -> CourseService:
    return CourseService(repo, audit, principal)


def get_enrolment_service(
    repo: EnrolmentRepo,
    students: StudentRepo,
    courses: CourseRepo,
    audit: Audit,
    principal: CurrentPrincipal,
) -> EnrolmentService:
    return EnrolmentService(repo, students, courses, audit, principal)


Students = Annotated[StudentService, Depends(get_student_service)]
Courses = Annotated[CourseService, Depends(get_course_service)]
Enrolments = Annotated[EnrolmentService, Depends(get_enrolment_service)]

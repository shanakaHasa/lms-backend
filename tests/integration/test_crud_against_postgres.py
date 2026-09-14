"""What only a real database can prove.

The unit suites cover the business rules against fakes and the SQL against the
Postgres dialect. Neither can tell you whether the *database* enforces what the
model modules claim it does — a partial unique index that was never created, a
composite foreign key the migration spelled differently, a CHECK constraint that
disagrees with the schema's `Literal`. Those failures are invisible until
something actually executes against Postgres.

So each test here targets a guarantee that is **structural in the database**
rather than upheld by application code. A test that the fakes could already
prove does not belong in this file.

Run with `pytest -m integration` after `alembic upgrade head`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Principal
from app.models.audit_event import AuditEvent
from app.models.course import Course
from app.models.enrolment import Enrolment
from app.models.student import Student
from app.repositories.course import SqlCourseRepository
from app.repositories.enrolment import SqlEnrolmentRepository
from app.repositories.student import SqlStudentRepository
from app.schemas.course import CourseCreate
from app.schemas.student import StudentCreate
from app.services.audit import SqlAuditSink
from app.services.course_service import CourseService
from app.services.enrolment_service import EnrolmentService
from app.services.student_service import StudentService
from tests.integration.conftest import TENANT_A, TENANT_B

pytestmark = pytest.mark.integration


def students(session: AsyncSession, principal: Principal) -> StudentService:
    return StudentService(
        SqlStudentRepository(session, principal.tenant_id),
        SqlAuditSink(session, principal),
        principal,
    )


def courses(session: AsyncSession, principal: Principal) -> CourseService:
    return CourseService(
        SqlCourseRepository(session, principal.tenant_id),
        SqlAuditSink(session, principal),
        principal,
    )


def enrolments(session: AsyncSession, principal: Principal) -> EnrolmentService:
    return EnrolmentService(
        SqlEnrolmentRepository(session, principal.tenant_id),
        SqlStudentRepository(session, principal.tenant_id),
        SqlCourseRepository(session, principal.tenant_id),
        SqlAuditSink(session, principal),
        principal,
    )


def student_payload(**overrides: object) -> StudentCreate:
    base: dict[str, object] = {
        "student_number": f"S{uuid.uuid4().hex[:8].upper()}",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": f"{uuid.uuid4().hex[:12]}@example.com",
    }
    base.update(overrides)
    return StudentCreate(**base)  # type: ignore[arg-type]


def course_payload(**overrides: object) -> CourseCreate:
    base: dict[str, object] = {
        "code": f"C{uuid.uuid4().hex[:8].upper()}",
        "title": "Systems Programming",
    }
    base.update(overrides)
    return CourseCreate(**base)  # type: ignore[arg-type]


# ── The end-to-end path ─────────────────────────────────────────────────────


async def test_the_whole_flow_round_trips_through_postgres(
    session: AsyncSession, principal_a: Principal
) -> None:
    """The first test to run in Phase 2.

    If the migration and the models disagree anywhere in these three tables,
    this is where it surfaces — before anything more interesting is attempted.
    """
    student = await students(session, principal_a).create(student_payload())
    course = await courses(session, principal_a).create(course_payload())
    enrolment, created = await enrolments(session, principal_a).enrol(student.id, course.id)
    assert created

    entries, total = await enrolments(session, principal_a).roster(course.id, limit=10, offset=0)
    assert total == 1
    assert entries[0][1].id == student.id
    assert entries[0][0].id == enrolment.id


# ── Constraints the database owns ───────────────────────────────────────────


async def test_the_partial_unique_index_rejects_a_live_duplicate(
    session: AsyncSession, principal_a: Principal
) -> None:
    """`uq_students_tenant_number`, enforced by Postgres rather than by us.

    The service checks first so the error is useful, so this goes around it and
    inserts directly — otherwise it would be testing the service's check a
    second time instead of the index.
    """
    service = students(session, principal_a)
    first = await service.create(student_payload(student_number="S-DUP"))

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            Student(
                id=uuid.uuid4(),
                tenant_id=TENANT_A,
                student_number="S-DUP",
                first_name="Grace",
                last_name="Hopper",
                email="grace@example.com",
                email_normalized="grace@example.com",
                status="active",
                student_metadata={},
                created_by="test",
            )
        )
        await session.flush()

    assert first.student_number == "S-DUP"


async def test_a_soft_delete_frees_the_identifier_for_reuse(
    session: AsyncSession, principal_a: Principal
) -> None:
    """The reason the unique indexes are partial.

    `WHERE deleted_at IS NULL` is what separates a soft delete that frees a
    student number from one that poisons it forever — and only Postgres can
    confirm the index was actually created that way.
    """
    service = students(session, principal_a)
    first = await service.create(student_payload(student_number="S-REUSE"))
    await service.delete(first.id)
    await session.flush()

    second = await service.create(student_payload(student_number="S-REUSE"))
    assert second.id != first.id


async def test_the_composite_foreign_key_refuses_a_cross_tenant_enrolment(
    session: AsyncSession, principal_a: Principal, principal_b: Principal
) -> None:
    """The highest-value constraint in the schema.

    The service already returns 404 for this, so the insert goes in directly.
    What is being proved is that the guarantee survives a bug in the service —
    that joining a student in one institution to a course in another is a
    database error, not something a missing WHERE clause can cause.
    """
    student = await students(session, principal_a).create(student_payload())
    foreign_course = await courses(session, principal_b).create(course_payload())
    await session.flush()

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            Enrolment(
                id=uuid.uuid4(),
                tenant_id=TENANT_A,
                student_id=student.id,
                course_id=foreign_course.id,
                status="enrolled",
                enrolled_at=datetime.now(UTC),
            )
        )
        await session.flush()


async def test_a_student_cannot_be_enrolled_twice_even_by_a_direct_insert(
    session: AsyncSession, principal_a: Principal
) -> None:
    # `uq_enrolments_student_course` is what makes a duplicated proposal from
    # the assistant harmless rather than merely unlikely.
    student = await students(session, principal_a).create(student_payload())
    course = await courses(session, principal_a).create(course_payload())
    enrolment, _ = await enrolments(session, principal_a).enrol(student.id, course.id)
    await session.flush()

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            Enrolment(
                id=uuid.uuid4(),
                tenant_id=TENANT_A,
                student_id=student.id,
                course_id=course.id,
                status="enrolled",
                enrolled_at=datetime.now(UTC),
            )
        )
        await session.flush()

    assert enrolment.id is not None


@pytest.mark.parametrize(
    ("table", "row"),
    [
        (
            "students",
            lambda: Student(
                id=uuid.uuid4(),
                tenant_id=TENANT_A,
                student_number=f"S{uuid.uuid4().hex[:6]}",
                first_name="A",
                last_name="B",
                email="c@example.com",
                email_normalized="c@example.com",
                status="expelled",
                student_metadata={},
                created_by="test",
            ),
        ),
        (
            "courses",
            lambda: Course(
                id=uuid.uuid4(),
                tenant_id=TENANT_A,
                code=f"X{uuid.uuid4().hex[:6].upper()}",
                title="T",
                description="",
                status="deleted",
            ),
        ),
    ],
)
async def test_the_check_constraints_reject_a_status_the_schema_would_not_accept(
    session: AsyncSession, table: str, row: object
) -> None:
    """The database's opinion of a valid status must match the API's.

    A unit test already asserts the `Literal` and the CHECK constraint agree in
    the model metadata. This asserts the CHECK actually reached the database.
    """
    with pytest.raises((IntegrityError, DBAPIError)), session.begin_nested():
        session.add(row())  # type: ignore[operator]
        await session.flush()


# ── The audit trail ─────────────────────────────────────────────────────────


async def test_an_audit_row_lands_in_the_same_transaction_as_the_action(
    session: AsyncSession, principal_a: Principal
) -> None:
    student = await students(session, principal_a).create(student_payload())
    await session.flush()

    rows = await session.execute(
        select(AuditEvent).where(
            AuditEvent.tenant_id == TENANT_A,
            AuditEvent.resource_id == str(student.id),
        )
    )
    recorded = rows.scalars().all()
    assert [r.action for r in recorded] == ["student.create"]
    assert recorded[0].pii_accessed is True
    assert recorded[0].actor_id == "teacher-a"


async def test_a_rolled_back_action_takes_its_audit_row_with_it(
    session: AsyncSession, principal_a: Principal
) -> None:
    """Why the audit sink shares the caller's session.

    An audit row written in its own transaction can commit while the action
    rolls back, and then the log claims something happened that did not. That is
    worse than no log at all, because it is trusted.
    """
    service = students(session, principal_a)

    async with session.begin_nested() as savepoint:
        student = await service.create(student_payload())
        await session.flush()
        await savepoint.rollback()

    rows = await session.execute(
        select(AuditEvent).where(AuditEvent.resource_id == str(student.id))
    )
    assert rows.scalars().all() == []


# ── Real SQL against real data ──────────────────────────────────────────────


async def test_search_and_pagination_behave_against_postgres(
    session: AsyncSession, principal_a: Principal
) -> None:
    # ILIKE, the ORDER BY tiebreak and the count/list agreement are all things
    # the fakes approximate in Python rather than execute.
    service = students(session, principal_a)
    for surname in ("Hopper", "Lovelace", "Liskov"):
        await service.create(student_payload(last_name=surname))
    await session.flush()

    _, matched = await service.list(limit=10, offset=0, query="lov")
    assert matched == 1

    first_page, total = await service.list(limit=2, offset=0)
    second_page, _ = await service.list(limit=2, offset=2)
    assert total == 3
    assert {s.id for s in first_page}.isdisjoint({s.id for s in second_page})


async def test_the_roster_join_excludes_a_soft_deleted_student(
    session: AsyncSession, principal_a: Principal
) -> None:
    student = await students(session, principal_a).create(student_payload())
    course = await courses(session, principal_a).create(course_payload())
    await enrolments(session, principal_a).enrol(student.id, course.id)
    await session.flush()

    await students(session, principal_a).delete(student.id)
    await session.flush()

    _, total = await enrolments(session, principal_a).roster(course.id, limit=10, offset=0)
    assert total == 0


async def test_one_tenants_repository_cannot_read_anothers_rows(
    session: AsyncSession, principal_a: Principal, principal_b: Principal
) -> None:
    """The tenant filter, in SQL, against real rows.

    The unit suite proves the predicate is in the compiled statement; this
    proves the statement returns nothing when it runs.
    """
    theirs = await students(session, principal_b).create(student_payload())
    await session.flush()

    assert await SqlStudentRepository(session, TENANT_A).get(theirs.id) is None
    assert await SqlStudentRepository(session, TENANT_B).get(theirs.id) is not None

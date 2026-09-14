"""Enrolment business rules.

These matter more than ordinary CRUD tests. From step B6 the assistant proposes
enrolments, and at B7 an approved proposal is executed minutes after it was
made — so retries, re-deliveries and a world that moved in between are the
normal case here, not the edge case.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import Conflict, Forbidden, NotFound
from app.core.security import Principal
from app.services.enrolment_service import EnrolmentService
from tests.fakes import (
    FakeAuditSink,
    FakeCourseRepository,
    FakeEnrolmentRepository,
    FakeStudentRepository,
    Store,
    make_course,
    make_student,
)

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
ALL_SCOPES = ("enrolments:write", "students:read")


def principal(tenant: str = TENANT_A, *scopes: str) -> Principal:
    return Principal(
        user_id="teacher-1",
        tenant_id=tenant,
        email="teacher@example.com",
        scopes=frozenset(scopes or ALL_SCOPES),
    )


def build(
    store: Store | None = None, actor: Principal | None = None
) -> tuple[EnrolmentService, Store, FakeAuditSink]:
    store = store or Store()
    actor = actor or principal()
    audit = FakeAuditSink()
    service = EnrolmentService(
        FakeEnrolmentRepository(store, actor.tenant_id),
        FakeStudentRepository(store, actor.tenant_id),
        FakeCourseRepository(store, actor.tenant_id),
        audit,
        actor,
    )
    return service, store, audit


def seed(store: Store, tenant: str = TENANT_A, **kwargs: object) -> tuple[uuid.UUID, uuid.UUID]:
    student = make_student(tenant, **kwargs.get("student", {}))  # type: ignore[arg-type]
    course = make_course(tenant, **kwargs.get("course", {}))  # type: ignore[arg-type]
    store.students[student.id] = student
    store.courses[course.id] = course
    return student.id, course.id


# ── Scope gates ─────────────────────────────────────────────────────────────


async def test_enrolling_requires_the_write_scope() -> None:
    service, store, _ = build(actor=principal(TENANT_A, "students:read"))
    student_id, course_id = seed(store)
    with pytest.raises(Forbidden, match="enrolments:write"):
        await service.enrol(student_id, course_id)


async def test_the_roster_requires_students_read_not_courses_read() -> None:
    # A roster is a list of named people, so it is gated on student data rather
    # than on the course that happens to contain them.
    service, store, _ = build(actor=principal(TENANT_A, "enrolments:write", "courses:read"))
    _, course_id = seed(store)
    with pytest.raises(Forbidden, match="students:read"):
        await service.roster(course_id, limit=10, offset=0)


# ── Idempotence: the property the agent depends on ──────────────────────────


async def test_enrolling_twice_returns_the_same_row() -> None:
    """A retried tool call must converge, not fail.

    The assistant retries, queues re-deliver, and an approval can be clicked
    twice. Every one of those must land on one enrolment.
    """
    service, store, _ = build()
    student_id, course_id = seed(store)

    first, created_first = await service.enrol(student_id, course_id)
    second, created_second = await service.enrol(student_id, course_id)

    assert first.id == second.id
    assert (created_first, created_second) == (True, False)
    assert len(store.enrolments) == 1


async def test_a_withdrawn_enrolment_is_reactivated_rather_than_duplicated() -> None:
    # `UNIQUE (student_id, course_id)` forbids a second row, so re-enrolling
    # has to be a status transition.
    service, store, _ = build()
    student_id, course_id = seed(store)

    enrolment, _ = await service.enrol(student_id, course_id)
    await service.withdraw(enrolment.id)
    again, created = await service.enrol(student_id, course_id)

    assert (again.id, again.status, created) == (enrolment.id, "enrolled", False)
    assert len(store.enrolments) == 1


async def test_re_enrolling_never_overwrites_a_completed_outcome() -> None:
    """A retry must not be able to erase an academic record.

    This is the case where "idempotent" and "converge on the requested state"
    disagree, and the record wins.
    """
    service, store, _ = build()
    student_id, course_id = seed(store)
    enrolment, _ = await service.enrol(student_id, course_id)
    enrolment.status = "completed"
    enrolment.grade = "A"

    again, created = await service.enrol(student_id, course_id)

    assert (again.status, again.grade, created) == ("completed", "A", False)


async def test_withdrawing_twice_is_a_no_op() -> None:
    service, store, audit = build()
    student_id, course_id = seed(store)
    enrolment, _ = await service.enrol(student_id, course_id)

    await service.withdraw(enrolment.id)
    await service.withdraw(enrolment.id)

    assert store.enrolments[enrolment.id].status == "withdrawn"
    assert audit.actions().count("enrolment.withdraw") == 1


# ── Revalidation: both sides, every time ────────────────────────────────────


async def test_enrolling_an_unknown_student_is_a_404() -> None:
    service, store, _ = build()
    _, course_id = seed(store)
    with pytest.raises(NotFound, match="student"):
        await service.enrol(uuid.uuid4(), course_id)


async def test_enrolling_into_an_unknown_course_is_a_404() -> None:
    service, store, _ = build()
    student_id, _ = seed(store)
    with pytest.raises(NotFound, match="course"):
        await service.enrol(student_id, uuid.uuid4())


async def test_a_student_deleted_between_proposal_and_approval_is_caught() -> None:
    """The TOCTOU case the approval gate at B7 depends on.

    A proposal carries two ids and is approved later. Executing on the arguments
    alone, without re-reading, would enrol a student who no longer exists.
    """
    service, store, _ = build()
    student_id, course_id = seed(store)
    store.students[student_id].deleted_at = datetime.now(UTC)

    with pytest.raises(NotFound, match="student"):
        await service.enrol(student_id, course_id)


async def test_enrolling_into_an_archived_course_is_refused() -> None:
    service, store, _ = build()
    student_id, course_id = seed(store, course={"status": "archived"})
    with pytest.raises(Conflict, match="archived"):
        await service.enrol(student_id, course_id)


# ── Tenancy ─────────────────────────────────────────────────────────────────


async def test_a_student_cannot_be_enrolled_into_another_tenants_course() -> None:
    """The composite foreign keys would refuse this row at the database.

    Catching it here turns what would be a constraint violation into a 404 — and
    proves the service never sees the other tenant's course in the first place.
    """
    store = Store()
    student_id, _ = seed(store, TENANT_A)
    _, foreign_course = seed(store, TENANT_B)
    service, _, _ = build(store, principal(TENANT_A))

    with pytest.raises(NotFound, match="course"):
        await service.enrol(student_id, foreign_course)
    assert store.enrolments == {}


async def test_withdrawing_another_tenants_enrolment_is_a_404() -> None:
    store = Store()
    their_student, their_course = seed(store, TENANT_B)
    service_b, _, _ = build(store, principal(TENANT_B))
    theirs, _ = await service_b.enrol(their_student, their_course)

    service_a, _, _ = build(store, principal(TENANT_A))
    with pytest.raises(NotFound):
        await service_a.withdraw(theirs.id)
    assert store.enrolments[theirs.id].status == "enrolled"


async def test_a_roster_for_another_tenants_course_is_a_404() -> None:
    store = Store()
    _, their_course = seed(store, TENANT_B)
    service, _, _ = build(store, principal(TENANT_A))
    with pytest.raises(NotFound, match="course"):
        await service.roster(their_course, limit=10, offset=0)


# ── The roster ──────────────────────────────────────────────────────────────


async def test_a_soft_deleted_student_drops_off_the_roster() -> None:
    """The join predicate that looks redundant and is not.

    Students are soft-deleted, so the `ON DELETE CASCADE` on the composite
    foreign key never fires and the enrolment row outlives its student. Without
    excluding deleted students in the roster query, a deleted person keeps
    appearing on class lists.
    """
    service, store, _ = build()
    student_id, course_id = seed(store)
    await service.enrol(student_id, course_id)

    entries, total = await service.roster(course_id, limit=10, offset=0)
    assert total == 1

    store.students[student_id].deleted_at = datetime.now(UTC)
    entries, total = await service.roster(course_id, limit=10, offset=0)
    assert (entries, total) == ([], 0)


async def test_a_roster_can_be_filtered_by_status() -> None:
    service, store, _ = build()
    first, course_id = seed(store, student={"student_number": "S001"})
    second = make_student(
        TENANT_A, student_number="S002", email="grace@example.com", last_name="Hopper"
    )
    store.students[second.id] = second

    enrolment, _ = await service.enrol(first, course_id)
    await service.enrol(second.id, course_id)
    await service.withdraw(enrolment.id)

    _, enrolled = await service.roster(course_id, limit=10, offset=0, status="enrolled")
    _, everyone = await service.roster(course_id, limit=10, offset=0)
    assert (enrolled, everyone) == (1, 2)


# ── Audit ───────────────────────────────────────────────────────────────────


async def test_an_enrolment_is_recorded_as_a_pii_write() -> None:
    service, store, audit = build()
    student_id, course_id = seed(store)
    await service.enrol(student_id, course_id)

    record = audit.records[-1]
    assert record.action == "enrolment.create"
    assert record.pii_accessed is True
    assert record.metadata["student_id"] == str(student_id)


async def test_a_repeated_enrolment_is_recorded_distinctly_from_a_new_one() -> None:
    # An operator reading the trail needs to tell "enrolled again" from
    # "enrolled twice", and only one of those is a bug worth chasing.
    service, store, audit = build()
    student_id, course_id = seed(store)
    await service.enrol(student_id, course_id)
    await service.enrol(student_id, course_id)
    assert audit.actions() == ["enrolment.create", "enrolment.create.noop"]


async def test_a_reactivation_records_what_it_came_from() -> None:
    service, store, audit = build()
    student_id, course_id = seed(store)
    enrolment, _ = await service.enrol(student_id, course_id)
    await service.withdraw(enrolment.id)
    audit.records.clear()

    await service.enrol(student_id, course_id)

    assert audit.records[0].action == "enrolment.reactivate"
    assert audit.records[0].metadata == {"from": "withdrawn"}

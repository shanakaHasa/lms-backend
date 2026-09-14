"""Course business rules."""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import Conflict, Forbidden, NotFound
from app.core.security import Principal
from app.schemas.course import CourseCreate, CourseUpdate
from app.services.course_service import CourseService
from tests.fakes import FakeAuditSink, FakeCourseRepository, Store, make_course

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


def principal(tenant: str = TENANT_A, *scopes: str) -> Principal:
    return Principal(
        user_id="teacher-1",
        tenant_id=tenant,
        email="teacher@example.com",
        scopes=frozenset(scopes or ("courses:read", "courses:write")),
    )


def build(
    store: Store | None = None, actor: Principal | None = None
) -> tuple[CourseService, Store, FakeAuditSink]:
    store = store or Store()
    actor = actor or principal()
    audit = FakeAuditSink()
    return CourseService(FakeCourseRepository(store, actor.tenant_id), audit, actor), store, audit


def payload(**overrides: object) -> CourseCreate:
    base: dict[str, object] = {"code": "COMP201", "title": "Systems Programming"}
    base.update(overrides)
    return CourseCreate(**base)  # type: ignore[arg-type]


# ── Scope gates ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("get", (uuid.uuid4(),)),
        ("get_by_code", ("COMP201",)),
        ("create", (payload(),)),
        ("update", (uuid.uuid4(), CourseUpdate(title="X"))),
    ],
)
async def test_every_method_refuses_a_principal_without_the_scope(
    method: str, args: tuple[object, ...]
) -> None:
    service, _, _ = build(actor=principal(TENANT_A, "students:read"))
    with pytest.raises(Forbidden, match="scope"):
        await getattr(service, method)(*args)


# ── The code, which retrieval depends on ────────────────────────────────────


async def test_the_code_is_normalised_to_upper_case() -> None:
    """Not cosmetic.

    The code appears verbatim in the questions the assistant answers, and
    retrieval filters on it. A tenant holding both `COMP201` and `comp201` would
    split one course's materials across two codes and silently halve recall.
    """
    service, _, _ = build()
    course = await service.create(payload(code=" comp201 "))
    assert course.code == "COMP201"


async def test_a_course_resolves_by_the_code_a_person_types() -> None:
    # The assistant's tools call this: a teacher asks about "comp201", not a
    # UUID.
    service, _, _ = build()
    created = await service.create(payload())
    assert (await service.get_by_code("comp201")).id == created.id
    assert (await service.get_by_code(" COMP201 ")).id == created.id


async def test_an_unknown_code_is_echoed_back_as_the_caller_typed_it() -> None:
    # The lookup normalises; the message does not. "no course with code
    # 'COMP999'" would make a teacher wonder whether the capitalisation was the
    # problem, when it never is.
    service, _, _ = build()
    with pytest.raises(NotFound, match="comp999"):
        await service.get_by_code("comp999")


async def test_a_duplicate_code_conflicts_after_normalisation() -> None:
    service, _, _ = build()
    await service.create(payload())
    with pytest.raises(Conflict) as excinfo:
        await service.create(payload(code="comp201", title="Something else"))
    assert excinfo.value.detail == {"field": "code"}


async def test_two_tenants_may_hold_the_same_code() -> None:
    store = Store()
    service_a, _, _ = build(store, principal(TENANT_A))
    service_b, _, _ = build(store, principal(TENANT_B))
    await service_a.create(payload())
    await service_b.create(payload())
    assert len(store.courses) == 2


def test_the_code_cannot_be_changed() -> None:
    """Absent from `CourseUpdate` by design.

    Renaming a code after materials have been ingested against it would strand
    every chunk filtered by the old one. Archiving and recreating keeps the
    history honest instead of rewriting it.
    """
    assert "code" not in CourseUpdate.model_fields


# ── Reads and updates ───────────────────────────────────────────────────────


async def test_a_course_in_another_tenant_is_a_404() -> None:
    store = Store()
    theirs = make_course(TENANT_B)
    store.courses[theirs.id] = theirs
    service, _, _ = build(store, principal(TENANT_A))
    with pytest.raises(NotFound):
        await service.get(theirs.id)
    with pytest.raises(NotFound):
        await service.update(theirs.id, CourseUpdate(title="Mine now"))
    assert store.courses[theirs.id].title == "Systems Programming"


async def test_archiving_is_a_status_change_not_a_deletion() -> None:
    # Materials, enrolments and grades all point at a course; a course that
    # vanished would take the meaning of those rows with it.
    service, store, _ = build()
    course = await service.create(payload())
    await service.update(course.id, CourseUpdate(status="archived"))
    assert store.courses[course.id].status == "archived"


async def test_an_explicit_null_clears_a_nullable_field() -> None:
    service, _, _ = build()
    course = await service.create(payload(teacher_user_id="teacher-9"))
    updated = await service.update(
        course.id, CourseUpdate.model_validate({"teacher_user_id": None})
    )
    assert updated.teacher_user_id is None


async def test_an_empty_patch_is_rejected() -> None:
    service, _, _ = build()
    course = await service.create(payload())
    with pytest.raises(Conflict, match="no fields"):
        await service.update(course.id, CourseUpdate())


async def test_listing_filters_by_teacher_and_status() -> None:
    service, _, _ = build()
    await service.create(payload(teacher_user_id="teacher-9"))
    await service.create(payload(code="COMP101", title="Intro", teacher_user_id="teacher-7"))
    await service.create(payload(code="COMP301", title="Compilers", status="archived"))

    _, mine = await service.list(limit=10, offset=0, teacher_user_id="teacher-9")
    _, active = await service.list(limit=10, offset=0, status="active")
    assert (mine, active) == (1, 2)


# ── Audit ───────────────────────────────────────────────────────────────────


async def test_course_access_is_not_recorded_as_a_pii_read() -> None:
    """The flag has to mean something.

    If every audit row claimed PII access, the partial index over `pii_accessed`
    would match every row and the access-review query would become a full scan.
    """
    service, _, audit = build()
    course = await service.create(payload())
    await service.get(course.id)
    await service.list(limit=10, offset=0)
    assert all(record.pii_accessed is False for record in audit.records)


async def test_every_course_write_leaves_a_trail() -> None:
    service, _, audit = build()
    course = await service.create(payload())
    await service.update(course.id, CourseUpdate(title="Renamed"))
    assert audit.actions() == ["course.create", "course.update"]

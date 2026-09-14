"""Student business rules, exercised with no database.

Every test here builds the real `StudentService` over an in-memory store. What
is faked is storage; the rules, the scope gates and the audit decisions are the
production ones.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import Conflict, Forbidden, NotFound
from app.core.security import Principal
from app.schemas.student import StudentCreate, StudentUpdate
from app.services.student_service import StudentService
from tests.fakes import FakeAuditSink, FakeStudentRepository, Store, make_student

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


def principal(tenant: str = TENANT_A, *scopes: str) -> Principal:
    return Principal(
        user_id="teacher-1",
        tenant_id=tenant,
        email="teacher@example.com",
        scopes=frozenset(scopes or ("students:read", "students:write")),
    )


def build(
    store: Store | None = None, actor: Principal | None = None
) -> tuple[StudentService, Store, FakeAuditSink]:
    store = store or Store()
    actor = actor or principal()
    audit = FakeAuditSink()
    service = StudentService(FakeStudentRepository(store, actor.tenant_id), audit, actor)
    return service, store, audit


def payload(**overrides: object) -> StudentCreate:
    base: dict[str, object] = {
        "student_number": "S001",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
    }
    base.update(overrides)
    return StudentCreate(**base)  # type: ignore[arg-type]


# ── Construction ────────────────────────────────────────────────────────────


def test_a_service_cannot_be_built_across_two_tenants() -> None:
    """The guard that catches a wiring mistake before it becomes a data leak.

    A service holds a principal and a repository, and each carries a tenant. If
    they disagree, every check below runs against one tenant while the query
    runs against another.
    """
    store = Store()
    with pytest.raises(RuntimeError, match="tenant-b"):
        StudentService(FakeStudentRepository(store, TENANT_B), FakeAuditSink(), principal(TENANT_A))


def test_a_repository_cannot_be_built_without_a_tenant() -> None:
    with pytest.raises(ValueError, match="tenant_id is required"):
        FakeStudentRepository(Store(), "")


# ── Scope gates ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("get", (uuid.uuid4(),)),
        ("create", (payload(),)),
        ("update", (uuid.uuid4(), StudentUpdate(first_name="X"))),
        ("delete", (uuid.uuid4(),)),
    ],
)
async def test_every_method_refuses_a_principal_without_the_scope(
    method: str, args: tuple[object, ...]
) -> None:
    """The gate lives in the service, not the route.

    At B6 the assistant's tools call this service directly and at B7 the
    approval executor does too — neither goes through FastAPI, so a gate that
    only existed in a route decorator would be absent from both.
    """
    service, _, _ = build(actor=principal(TENANT_A, "chat:use"))
    with pytest.raises(Forbidden, match="scope"):
        await getattr(service, method)(*args)


async def test_listing_requires_read() -> None:
    service, _, _ = build(actor=principal(TENANT_A, "students:write"))
    with pytest.raises(Forbidden):
        await service.list(limit=10, offset=0)


# ── Create ──────────────────────────────────────────────────────────────────


async def test_create_normalises_the_identifiers() -> None:
    service, _, _ = build()
    student = await service.create(
        payload(student_number=" s001 ", email="Ada.Lovelace@Example.com")
    )
    assert student.student_number == "S001"
    assert student.email_normalized == "ada.lovelace@example.com"
    # The typed form is preserved: a person's own capitalisation of their name
    # is not ours to overwrite.
    assert student.email == "Ada.Lovelace@example.com"


async def test_create_stamps_the_actor_from_the_token() -> None:
    # Absent from StudentCreate on purpose — a caller-supplied `created_by`
    # would make the audit trail forgeable.
    service, _, _ = build()
    student = await service.create(payload())
    assert student.created_by == "teacher-1"
    assert student.tenant_id == TENANT_A


async def test_create_generates_the_id_before_the_insert() -> None:
    # So the audit row can carry it in the same transaction, with no round trip.
    service, _, audit = build()
    student = await service.create(payload())
    assert student.id is not None
    assert audit.records[-1].resource_id == str(student.id)


async def test_a_duplicate_student_number_is_a_conflict_not_a_crash() -> None:
    service, _, _ = build()
    await service.create(payload())
    with pytest.raises(Conflict) as excinfo:
        await service.create(payload(email="grace@example.com"))
    assert excinfo.value.detail == {"field": "student_number"}
    assert excinfo.value.status_code == 409


async def test_a_duplicate_email_is_caught_after_normalisation() -> None:
    """`Ada@Example.com` and `ada@example.com` are one person.

    Comparing raw input here would let both through and leave the partial unique
    index to reject the second one with an opaque constraint error.
    """
    service, _, _ = build()
    await service.create(payload())
    with pytest.raises(Conflict) as excinfo:
        await service.create(payload(student_number="S002", email="ADA@example.com"))
    assert excinfo.value.detail == {"field": "email"}


async def test_another_tenant_may_reuse_the_same_identifiers() -> None:
    # The unique indexes are per tenant. Two institutions both having an "S001"
    # is the normal case, not a collision.
    store = Store()
    service_a, _, _ = build(store, principal(TENANT_A))
    service_b, _, _ = build(store, principal(TENANT_B))
    await service_a.create(payload())
    await service_b.create(payload())
    assert len(store.students) == 2


async def test_a_deleted_students_identifiers_become_reusable() -> None:
    """What makes the partial indexes worth their complexity.

    `WHERE deleted_at IS NULL` is the difference between a soft delete freeing
    the student number and a soft delete poisoning it forever.
    """
    service, _, _ = build()
    first = await service.create(payload())
    await service.delete(first.id)
    second = await service.create(payload())
    assert second.id != first.id


# ── Read ────────────────────────────────────────────────────────────────────


async def test_reading_a_missing_student_is_a_404() -> None:
    service, _, _ = build()
    with pytest.raises(NotFound):
        await service.get(uuid.uuid4())


async def test_a_student_in_another_tenant_is_indistinguishable_from_absent() -> None:
    """404, never 403.

    A 403 would confirm the record exists, which turns the endpoint into an
    enumeration oracle across institutions.
    """
    store = Store()
    theirs = make_student(TENANT_B)
    store.students[theirs.id] = theirs
    service, _, _ = build(store, principal(TENANT_A))
    with pytest.raises(NotFound):
        await service.get(theirs.id)


async def test_a_soft_deleted_student_is_gone_from_reads() -> None:
    service, _, _ = build()
    student = await service.create(payload())
    await service.delete(student.id)
    with pytest.raises(NotFound):
        await service.get(student.id)
    _, total = await service.list(limit=10, offset=0)
    assert total == 0


async def test_search_matches_name_number_and_email() -> None:
    service, _, _ = build()
    await service.create(payload())
    await service.create(
        payload(
            student_number="S002", first_name="Grace", last_name="Hopper", email="grace@example.com"
        )
    )
    for term, expected in [("lovelace", 1), ("S002", 1), ("grace@", 1), ("e", 2)]:
        _, total = await service.list(limit=10, offset=0, query=term)
        assert total == expected, term


# ── Update ──────────────────────────────────────────────────────────────────


async def test_an_omitted_field_is_left_alone() -> None:
    # The whole point of PATCH. Without `exclude_unset` every update would blank
    # every field the caller did not mention.
    service, _, _ = build()
    student = await service.create(payload(year_level=2))
    updated = await service.update(student.id, StudentUpdate(first_name="Augusta"))
    assert updated.first_name == "Augusta"
    assert updated.last_name == "Lovelace"
    assert updated.year_level == 2


async def test_an_explicit_null_clears_a_nullable_field() -> None:
    service, _, _ = build()
    student = await service.create(payload(year_level=2))
    updated = await service.update(student.id, StudentUpdate.model_validate({"year_level": None}))
    assert updated.year_level is None


async def test_an_empty_patch_is_rejected_rather_than_silently_succeeding() -> None:
    service, _, _ = build()
    student = await service.create(payload())
    with pytest.raises(Conflict, match="no fields"):
        await service.update(student.id, StudentUpdate())


async def test_changing_an_email_to_one_already_in_use_conflicts() -> None:
    service, _, _ = build()
    await service.create(payload())
    other = await service.create(payload(student_number="S002", email="grace@example.com"))
    with pytest.raises(Conflict) as excinfo:
        await service.update(other.id, StudentUpdate(email="ada@example.com"))
    assert excinfo.value.detail == {"field": "email"}


async def test_resubmitting_a_students_own_email_is_not_a_conflict() -> None:
    # The duplicate check has to exclude the row being edited, or saving a form
    # without changing the address would fail.
    service, _, _ = build()
    student = await service.create(payload())
    updated = await service.update(student.id, StudentUpdate(email="ADA@example.com"))
    assert updated.email_normalized == "ada@example.com"


async def test_updating_across_tenants_is_a_404() -> None:
    store = Store()
    theirs = make_student(TENANT_B)
    store.students[theirs.id] = theirs
    service, _, _ = build(store, principal(TENANT_A))
    with pytest.raises(NotFound):
        await service.update(theirs.id, StudentUpdate(first_name="Mallory"))
    assert store.students[theirs.id].first_name == "Ada"


# ── Delete ──────────────────────────────────────────────────────────────────


async def test_deletion_is_soft() -> None:
    # The row survives so enrolments, grades and audit entries still resolve to
    # a person.
    service, store, _ = build()
    student = await service.create(payload())
    await service.delete(student.id)
    assert store.students[student.id].deleted_at is not None


async def test_deleting_across_tenants_is_a_404() -> None:
    store = Store()
    theirs = make_student(TENANT_B)
    store.students[theirs.id] = theirs
    service, _, _ = build(store, principal(TENANT_A))
    with pytest.raises(NotFound):
        await service.delete(theirs.id)
    assert store.students[theirs.id].deleted_at is None


# ── Audit ───────────────────────────────────────────────────────────────────


async def test_reading_one_student_is_recorded_as_a_pii_access() -> None:
    service, _, audit = build()
    student = await service.create(payload())
    audit.records.clear()
    await service.get(student.id)
    assert audit.records[0].action == "student.read"
    assert audit.records[0].pii_accessed is True


async def test_a_list_writes_one_row_carrying_the_count() -> None:
    """Not one row per student.

    Audit volume should follow intent: a fifty-row page is one act of looking,
    and fifty rows would bury the single lookup an investigation is after.
    """
    service, _, audit = build()
    for n in range(3):
        await service.create(payload(student_number=f"S00{n}", email=f"s{n}@example.com"))
    audit.records.clear()

    await service.list(limit=10, offset=0)

    assert len(audit.records) == 1
    assert audit.records[0].action == "student.list"
    assert audit.records[0].metadata["returned"] == 3
    assert audit.records[0].metadata["total"] == 3


async def test_an_update_records_which_fields_changed_but_not_their_values() -> None:
    """The audit log says the email changed; it does not hold the addresses.

    Copying values in would spread the same personal data into a second table
    with a different retention policy — and the field name is what an
    investigation actually needs.
    """
    service, _, audit = build()
    student = await service.create(payload())
    audit.records.clear()
    await service.update(student.id, StudentUpdate(email="new@example.com"))

    record = audit.records[0]
    assert record.metadata == {"changed": ["email"]}
    assert "new@example.com" not in str(record.metadata)


async def test_every_write_leaves_a_trail() -> None:
    service, _, audit = build()
    student = await service.create(payload())
    await service.update(student.id, StudentUpdate(first_name="Augusta"))
    await service.delete(student.id)
    assert audit.actions() == ["student.create", "student.update", "student.delete"]


async def test_a_refused_write_leaves_no_trail() -> None:
    # A Conflict means nothing happened, so an audit row claiming otherwise
    # would be a lie in the one table that must not contain any.
    service, _, audit = build()
    await service.create(payload())
    audit.records.clear()
    with pytest.raises(Conflict):
        await service.create(payload())
    assert audit.records == []

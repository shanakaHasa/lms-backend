"""The tenant boundary, over the whole HTTP surface.

This is the suite that would catch a cross-institution data leak, so what it
fakes matters as much as what it asserts.

**Only storage is faked**, by `tests.client.build_client` — see that module
for why the service providers are deliberately left alone.

**The fake store is shared between tenants.** Both tenants' rows live in one
dict, and each fake repository filters by the tenant it was built with. A
missing tenant filter therefore returns the *other* tenant's row and fails here
— which would not happen if each tenant had its own store.

What remains uncovered by this file is the SQL, since the fakes are
dictionaries. `test_repository_sql.py` compiles every real statement against the
Postgres dialect and asserts the tenant predicate is in each one.

The meta-test at the bottom is what keeps this honest over time: it enumerates
the OpenAPI surface and fails when a route is not in the matrix, so an endpoint
added months from now cannot quietly skip the check.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import deps
from app.main import create_app
from app.models.enrolment import Enrolment
from tests.client import ALL_SCOPES, as_tenant, build_client
from tests.fakes import Store, make_course, make_student

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


class World:
    """Two institutions, each with a student, a course and an enrolment."""

    def __init__(self) -> None:
        self.store = Store()
        self.ids: dict[str, dict[str, uuid.UUID]] = {}
        for tenant, number in ((TENANT_A, "S001"), (TENANT_B, "S002")):
            student = make_student(
                tenant, student_number=number, email=f"{number.lower()}@example.com"
            )
            course = make_course(tenant, code=f"{tenant.upper()}-101")
            enrolment = Enrolment(
                id=uuid.uuid4(),
                tenant_id=tenant,
                student_id=student.id,
                course_id=course.id,
                status="enrolled",
                enrolled_at=student.created_at,
                created_at=student.created_at,
                updated_at=student.created_at,
            )
            self.store.students[student.id] = student
            self.store.courses[course.id] = course
            self.store.enrolments[enrolment.id] = enrolment
            self.ids[tenant] = {
                "student_id": student.id,
                "course_id": course.id,
                "enrolment_id": enrolment.id,
            }


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def client(world: World) -> Iterator[TestClient]:
    with build_client(world.store, as_tenant(TENANT_A)) as test_client:
        yield test_client


# ── The matrix ──────────────────────────────────────────────────────────────
#
# Every entry names a route, how to reach it with another tenant's identifiers,
# and what must come back. `route` is the OpenAPI path template, which is what
# the meta-test at the bottom matches against.

FOREIGN_CASES: list[dict[str, Any]] = [
    {
        "route": ("GET", "/api/v1/students/{student_id}"),
        "call": lambda c, ids: c.get(f"/api/v1/students/{ids['student_id']}"),
        "expect": 404,
    },
    {
        "route": ("PATCH", "/api/v1/students/{student_id}"),
        "call": lambda c, ids: c.patch(
            f"/api/v1/students/{ids['student_id']}", json={"first_name": "Mallory"}
        ),
        "expect": 404,
    },
    {
        "route": ("DELETE", "/api/v1/students/{student_id}"),
        "call": lambda c, ids: c.delete(f"/api/v1/students/{ids['student_id']}"),
        "expect": 404,
    },
    {
        "route": ("GET", "/api/v1/courses/{course_id}"),
        "call": lambda c, ids: c.get(f"/api/v1/courses/{ids['course_id']}"),
        "expect": 404,
    },
    {
        "route": ("PATCH", "/api/v1/courses/{course_id}"),
        "call": lambda c, ids: c.patch(
            f"/api/v1/courses/{ids['course_id']}", json={"title": "Mine now"}
        ),
        "expect": 404,
    },
    {
        "route": ("GET", "/api/v1/courses/{course_id}/roster"),
        "call": lambda c, ids: c.get(f"/api/v1/courses/{ids['course_id']}/roster"),
        "expect": 404,
    },
    {
        "route": ("POST", "/api/v1/enrolments"),
        "call": lambda c, ids: c.post(
            "/api/v1/enrolments",
            json={"student_id": str(ids["student_id"]), "course_id": str(ids["course_id"])},
        ),
        "expect": 404,
    },
    {
        "route": ("DELETE", "/api/v1/enrolments/{enrolment_id}"),
        "call": lambda c, ids: c.delete(f"/api/v1/enrolments/{ids['enrolment_id']}"),
        "expect": 404,
    },
    # Collection endpoints take no foreign identifier, so the assertion is that
    # the page contains only this tenant's rows. Checked in the tests below.
    {"route": ("GET", "/api/v1/students"), "call": None, "expect": None},
    {"route": ("POST", "/api/v1/students"), "call": None, "expect": None},
    {"route": ("GET", "/api/v1/courses"), "call": None, "expect": None},
    {"route": ("POST", "/api/v1/courses"), "call": None, "expect": None},
]

ADDRESSED_CASES = [case for case in FOREIGN_CASES if case["call"] is not None]


@pytest.mark.parametrize(
    "case", ADDRESSED_CASES, ids=[f"{c['route'][0]} {c['route'][1]}" for c in ADDRESSED_CASES]
)
def test_another_tenants_record_is_unreachable(
    case: dict[str, Any], client: TestClient, world: World
) -> None:
    """404, never 403.

    A 403 would confirm the record exists, which turns every endpoint into an
    enumeration oracle across institutions.
    """
    response = case["call"](client, world.ids[TENANT_B])
    assert response.status_code == case["expect"], response.text


def test_a_refused_cross_tenant_write_changes_nothing() -> None:
    """A 404 is only half the guarantee; the other half is that nothing moved."""
    world = World()
    theirs = world.ids[TENANT_B]
    before = {
        "name": world.store.students[theirs["student_id"]].first_name,
        "deleted": world.store.students[theirs["student_id"]].deleted_at,
        "title": world.store.courses[theirs["course_id"]].title,
        "status": world.store.enrolments[theirs["enrolment_id"]].status,
        "enrolments": len(world.store.enrolments),
    }

    with build_client(world.store, as_tenant(TENANT_A)) as client:
        client.patch(f"/api/v1/students/{theirs['student_id']}", json={"first_name": "Mallory"})
        client.delete(f"/api/v1/students/{theirs['student_id']}")
        client.patch(f"/api/v1/courses/{theirs['course_id']}", json={"title": "Mine now"})
        client.delete(f"/api/v1/enrolments/{theirs['enrolment_id']}")

    student = world.store.students[theirs["student_id"]]
    assert student.first_name == before["name"]
    assert student.deleted_at == before["deleted"]
    assert world.store.courses[theirs["course_id"]].title == before["title"]
    assert world.store.enrolments[theirs["enrolment_id"]].status == before["status"]
    assert len(world.store.enrolments) == before["enrolments"]


@pytest.mark.parametrize(
    ("path", "key", "mine"),
    [
        ("/api/v1/students", "student_number", "S001"),
        ("/api/v1/courses", "code", "TENANT-A-101"),
    ],
)
def test_a_collection_returns_only_this_tenants_rows(
    client: TestClient, path: str, key: str, mine: str
) -> None:
    # The store holds one row per tenant, so a missing filter would show two.
    body = client.get(path).json()
    assert body["total"] == 1, f"{path} leaked rows: {body}"
    assert [item[key] for item in body["items"]] == [mine]


def test_a_roster_shows_only_this_tenants_students(client: TestClient, world: World) -> None:
    mine = world.ids[TENANT_A]
    body = client.get(f"/api/v1/courses/{mine['course_id']}/roster").json()
    assert body["total"] == 1
    assert body["items"][0]["student"]["student_number"] == "S001"


def test_a_tenant_cannot_be_supplied_as_input(client: TestClient, world: World) -> None:
    """The reason isolation here is structural rather than validated.

    There is no `tenant_id` field on any request schema, so a body that carries
    one is rejected outright by `extra="forbid"` — there is nothing to override.
    """
    response = client.post(
        "/api/v1/students",
        json={
            "student_number": "S999",
            "first_name": "Mallory",
            "last_name": "Ward",
            "email": "mallory@example.com",
            "tenant_id": TENANT_B,
        },
    )
    assert response.status_code == 422


def test_a_tenant_query_parameter_is_ignored(client: TestClient) -> None:
    # Unknown query parameters are not an error in FastAPI; what matters is
    # that this one changes nothing.
    body = client.get(f"/api/v1/students?tenant_id={TENANT_B}").json()
    assert body["total"] == 1
    assert body["items"][0]["student_number"] == "S001"


# ── Scope gates, over HTTP ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "json_body", "missing"),
    [
        ("get", "/api/v1/students", None, "students:read"),
        (
            "post",
            "/api/v1/students",
            {
                "student_number": "S9",
                "first_name": "A",
                "last_name": "B",
                "email": "s9@example.com",
            },
            "students:write",
        ),
        ("get", "/api/v1/courses", None, "courses:read"),
        ("post", "/api/v1/courses", {"code": "X101", "title": "T"}, "courses:write"),
        (
            "post",
            "/api/v1/enrolments",
            {"student_id": str(uuid.uuid4()), "course_id": str(uuid.uuid4())},
            "enrolments:write",
        ),
    ],
)
def test_a_missing_scope_is_a_403(
    world: World, method: str, path: str, json_body: dict[str, Any] | None, missing: str
) -> None:
    """403 here, unlike the 404 for another tenant's row.

    The distinction is deliberate: "you may not do this" leaks nothing, whereas
    "this record exists but is not yours" leaks the record's existence.
    """
    actor = as_tenant(TENANT_A, ALL_SCOPES - {missing})
    with build_client(world.store, actor) as client:
        response = client.request(method.upper(), path, json=json_body)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


def test_the_roster_is_gated_on_students_read_not_courses_read(world: World) -> None:
    # A roster is a list of named people. Gating it on `courses:read` would let
    # anyone who can see a timetable read the class list.
    actor = as_tenant(TENANT_A, frozenset({"courses:read", "enrolments:write"}))
    with build_client(world.store, actor) as client:
        response = client.get(f"/api/v1/courses/{world.ids[TENANT_A]['course_id']}/roster")

    assert response.status_code == 403


# ── The meta-test ───────────────────────────────────────────────────────────


def api_surface() -> set[tuple[str, str]]:
    """Every `/api/v1` operation the app exposes.

    Read from the OpenAPI schema rather than `app.routes`, because FastAPI wraps
    included routers rather than flattening them — and because the schema is the
    surface a client can actually reach, which is exactly what this matrix has
    to cover.
    """
    spec = create_app().openapi()
    return {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method in operations
        if path.startswith("/api/v1")
    }


def test_every_api_route_is_covered_by_this_matrix() -> None:
    """The test that keeps the matrix honest once this file stops being new.

    Adding an endpoint without adding its isolation case fails the suite, which
    is the only reliable way to stop a tenant check being skipped by omission
    six months from now.
    """
    covered = {case["route"] for case in FOREIGN_CASES}
    uncovered = api_surface() - covered
    assert not uncovered, "these routes have no tenant-isolation case: " + ", ".join(
        f"{method} {path}" for method, path in sorted(uncovered)
    )


def test_the_matrix_names_no_route_that_does_not_exist() -> None:
    # The other direction: a renamed path would otherwise leave a case that
    # passes by testing nothing.
    covered = {case["route"] for case in FOREIGN_CASES}
    assert not covered - api_surface()


# ── The one link the matrix overrides ───────────────────────────────────────


def test_the_real_providers_scope_storage_to_the_token_tenant() -> None:
    """Closes the loop the fixtures above deliberately open.

    Every test in this module replaces the repository providers, so the genuine
    ones in `app.core.deps` are the single link the matrix cannot exercise.
    They are four one-line functions, and all four have to read the tenant from
    the principal and nowhere else — so they are called directly here.
    """
    principal = as_tenant(TENANT_B)
    session = object()  # never used: construction is all that is under test

    for provider in (
        deps.get_student_repository,
        deps.get_course_repository,
        deps.get_enrolment_repository,
    ):
        repository = provider(session, principal)  # type: ignore[arg-type]
        assert repository.tenant_id == TENANT_B, provider.__name__

    sink = deps.get_audit_sink(session, principal)  # type: ignore[arg-type]
    assert sink.principal.tenant_id == TENANT_B  # type: ignore[attr-defined]


def test_a_service_built_from_the_real_providers_refuses_a_mismatched_tenant() -> None:
    """The guard that would catch a future wiring mistake.

    If someone later wires a service to a repository built for a different
    tenant, every check inside that service runs against one tenant while its
    queries run against another. That is a leak, so it fails loudly at
    construction rather than quietly at runtime.
    """
    from app.services.student_service import StudentService

    session = object()
    repository = deps.get_student_repository(session, as_tenant(TENANT_A))  # type: ignore[arg-type]
    sink = deps.get_audit_sink(session, as_tenant(TENANT_B))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match=TENANT_B):
        StudentService(repository, sink, as_tenant(TENANT_B))

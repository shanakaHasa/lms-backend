"""The HTTP contract.

Status codes, response shapes and error envelopes, over the real routes with
only storage faked. The business rules themselves are covered in the service
suites; what is asserted here is the part a client — the frontend at step F2,
and the agent's HTTP caller later — actually depends on.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.client import ALL_SCOPES, as_tenant, build_client
from tests.fakes import Store

STUDENT = {
    "student_number": "S001",
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
}
COURSE = {"code": "COMP201", "title": "Systems Programming"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    with build_client(Store(), as_tenant("tenant-a", ALL_SCOPES)) as test_client:
        yield test_client


def create_student(client: TestClient, **overrides: object) -> dict:  # type: ignore[type-arg]
    response = client.post("/api/v1/students", json={**STUDENT, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


def create_course(client: TestClient, **overrides: object) -> dict:  # type: ignore[type-arg]
    response = client.post("/api/v1/courses", json={**COURSE, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


# ── Students ────────────────────────────────────────────────────────────────


def test_creating_a_student_answers_201_with_the_record(client: TestClient) -> None:
    body = create_student(client)
    assert body["student_number"] == "S001"
    assert body["created_by"] == "teacher-tenant-a"
    assert body["created_at"] and body["updated_at"]


def test_the_response_leaks_neither_the_tenant_nor_the_index_column(
    client: TestClient,
) -> None:
    """`tenant_id` and `email_normalized` are both absent by design.

    The tenant is a server-side fact a client has no use for, and
    `email_normalized` is an implementation detail of the unique index —
    returning both invites a client to key off the wrong one.
    """
    body = create_student(client)
    assert "tenant_id" not in body
    assert "email_normalized" not in body


def test_a_student_can_be_fetched_listed_patched_and_deleted(client: TestClient) -> None:
    created = create_student(client)
    student_id = created["id"]

    assert client.get(f"/api/v1/students/{student_id}").status_code == 200

    page = client.get("/api/v1/students").json()
    assert (page["total"], page["limit"], page["offset"]) == (1, 25, 0)

    patched = client.patch(f"/api/v1/students/{student_id}", json={"first_name": "Augusta"})
    assert patched.status_code == 200
    assert patched.json()["first_name"] == "Augusta"
    assert patched.json()["last_name"] == "Lovelace"

    assert client.delete(f"/api/v1/students/{student_id}").status_code == 204
    assert client.get(f"/api/v1/students/{student_id}").status_code == 404


def test_a_duplicate_is_a_409_naming_the_field(client: TestClient) -> None:
    create_student(client)
    response = client.post("/api/v1/students", json=STUDENT)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert response.json()["error"]["field"] == "student_number"


def test_an_unknown_id_is_a_404_not_a_500(client: TestClient) -> None:
    response = client.get(f"/api/v1/students/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_malformed_uuid_is_a_422(client: TestClient) -> None:
    assert client.get("/api/v1/students/not-a-uuid").status_code == 422


# ── Input validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "not-an-email"),
        ("first_name", "   "),
        ("student_number", ""),
        ("year_level", 0),
        ("status", "expelled"),
    ],
)
def test_bad_input_is_rejected_before_it_reaches_the_database(
    client: TestClient, field: str, value: object
) -> None:
    # `status` matters most here: the schema's Literal and the CHECK constraint
    # have to agree, and a value only the database rejects would be a 500 that
    # cannot appear until Phase 2 attaches one.
    response = client.post("/api/v1/students", json={**STUDENT, field: value})
    assert response.status_code == 422, response.text


def test_an_unknown_field_is_rejected_rather_than_ignored(client: TestClient) -> None:
    # `extra="forbid"`. Silently dropping a field a caller believed was saved is
    # worse than refusing it, and it is what makes a smuggled `tenant_id` a 422.
    response = client.post("/api/v1/students", json={**STUDENT, "grade": "A"})
    assert response.status_code == 422


def test_the_page_size_is_capped(client: TestClient) -> None:
    # `?limit=100000` is the cheapest denial of service in any CRUD API.
    assert client.get("/api/v1/students?limit=1000").status_code == 422
    assert client.get("/api/v1/students?limit=100").status_code == 200


def test_pagination_walks_the_collection_without_repeating(client: TestClient) -> None:
    for n in range(5):
        create_student(
            client, student_number=f"S{n:03d}", email=f"s{n}@example.com", last_name=f"Name{n}"
        )

    first = client.get("/api/v1/students?limit=2&offset=0").json()
    second = client.get("/api/v1/students?limit=2&offset=2").json()

    assert first["total"] == second["total"] == 5
    assert {item["id"] for item in first["items"]}.isdisjoint(
        item["id"] for item in second["items"]
    )


# ── Courses ─────────────────────────────────────────────────────────────────


def test_a_course_code_comes_back_normalised(client: TestClient) -> None:
    assert create_course(client, code="comp201")["code"] == "COMP201"


def test_a_duplicate_course_code_is_a_409(client: TestClient) -> None:
    create_course(client)
    assert client.post("/api/v1/courses", json=COURSE).status_code == 409


def test_the_course_code_cannot_be_patched(client: TestClient) -> None:
    course = create_course(client)
    response = client.patch(f"/api/v1/courses/{course['id']}", json={"code": "COMP999"})
    assert response.status_code == 422


# ── Enrolments: the contract the agent will depend on ───────────────────────


def test_enrolling_answers_201_then_200(client: TestClient) -> None:
    """The distinction a retry needs.

    201 means a row was created, 200 means one already existed. Without the
    split, a client cannot tell "enrolled" from "was already enrolled" — and
    with a 409 instead, every retried proposal at B7 would look like a failure.
    """
    student = create_student(client)
    course = create_course(client)
    body = {"student_id": student["id"], "course_id": course["id"]}

    first = client.post("/api/v1/enrolments", json=body)
    second = client.post("/api/v1/enrolments", json=body)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_withdrawing_answers_204_and_is_repeatable(client: TestClient) -> None:
    student = create_student(client)
    course = create_course(client)
    enrolment = client.post(
        "/api/v1/enrolments",
        json={"student_id": student["id"], "course_id": course["id"]},
    ).json()

    assert client.delete(f"/api/v1/enrolments/{enrolment['id']}").status_code == 204
    assert client.delete(f"/api/v1/enrolments/{enrolment['id']}").status_code == 204


def test_enrolling_into_an_archived_course_is_a_409(client: TestClient) -> None:
    student = create_student(client)
    course = create_course(client)
    client.patch(f"/api/v1/courses/{course['id']}", json={"status": "archived"})

    response = client.post(
        "/api/v1/enrolments",
        json={"student_id": student["id"], "course_id": course["id"]},
    )
    assert response.status_code == 409
    assert "archived" in response.json()["error"]["message"]


def test_a_roster_carries_the_student_not_just_an_id(client: TestClient) -> None:
    # Otherwise every client rendering a class list has to make one call per
    # row to find out whose name it is.
    student = create_student(client)
    course = create_course(client)
    client.post(
        "/api/v1/enrolments",
        json={"student_id": student["id"], "course_id": course["id"]},
    )

    page = client.get(f"/api/v1/courses/{course['id']}/roster").json()
    assert page["total"] == 1
    entry = page["items"][0]
    assert entry["status"] == "enrolled"
    assert entry["student"]["last_name"] == "Lovelace"
    # A roster needs identification, not the whole record.
    assert "created_by" not in entry["student"]


def test_a_deleted_student_disappears_from_the_roster(client: TestClient) -> None:
    student = create_student(client)
    course = create_course(client)
    client.post(
        "/api/v1/enrolments",
        json={"student_id": student["id"], "course_id": course["id"]},
    )
    client.delete(f"/api/v1/students/{student['id']}")

    assert client.get(f"/api/v1/courses/{course['id']}/roster").json()["total"] == 0


# ── The error envelope ──────────────────────────────────────────────────────


def test_every_error_carries_a_request_id(client: TestClient) -> None:
    # The one thing that ties a user's screenshot to a log line.
    body = client.get(f"/api/v1/students/{uuid.uuid4()}").json()
    assert body["request_id"]
    assert set(body) == {"error", "request_id"}

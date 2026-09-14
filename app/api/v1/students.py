"""Student endpoints.

These handlers are thin on purpose. Every rule that matters — the scope gate,
the tenant scope, the normalisation, the audit row — lives in the service, so
that the agent's tools at step B6 and the approval executor at B7 get all of it
without going through HTTP.

There is no `tenant_id` path or query parameter anywhere in this module, and
that absence is the isolation guarantee: there is nothing for a caller to set.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.core.deps import Students
from app.schemas.common import DEFAULT_PAGE_SIZE, Limit, Offset, Page, SearchQuery
from app.schemas.student import StudentCreate, StudentOut, StudentUpdate

router = APIRouter(prefix="/students", tags=["students"])


@router.post(
    "",
    response_model=StudentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a student",
    description="Requires `students:write`. A duplicate student number or email returns 409.",
)
async def create_student(payload: StudentCreate, service: Students) -> StudentOut:
    student = await service.create(payload)
    return StudentOut.model_validate(student)


@router.get(
    "",
    response_model=Page[StudentOut],
    summary="List students",
    description="Requires `students:read`. Writes one audit row for the page, not one per student.",
)
async def list_students(
    service: Students,
    limit: Limit = DEFAULT_PAGE_SIZE,
    offset: Offset = 0,
    status_filter: str | None = None,
    q: SearchQuery = None,
) -> Page[StudentOut]:
    students, total = await service.list(limit=limit, offset=offset, status=status_filter, query=q)
    return Page[StudentOut](
        items=[StudentOut.model_validate(s) for s in students],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{student_id}",
    response_model=StudentOut,
    summary="Fetch one student",
    description=(
        "Requires `students:read`. Writes an audit row with `pii_accessed`. "
        "A student belonging to another tenant returns 404, not 403 — a 403 "
        "would confirm the record exists."
    ),
)
async def get_student(student_id: uuid.UUID, service: Students) -> StudentOut:
    return StudentOut.model_validate(await service.get(student_id))


@router.patch(
    "/{student_id}",
    response_model=StudentOut,
    summary="Update a student",
    description="Requires `students:write`. Omitted fields are left untouched.",
)
async def update_student(
    student_id: uuid.UUID, payload: StudentUpdate, service: Students
) -> StudentOut:
    return StudentOut.model_validate(await service.update(student_id, payload))


@router.delete(
    "/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a student",
    description=(
        "Requires `students:write`. The deletion is soft: enrolments and audit "
        "rows keep resolving to a person, while the student number and email "
        "become reusable because the unique indexes exclude deleted rows."
    ),
)
async def delete_student(student_id: uuid.UUID, service: Students) -> Response:
    await service.delete(student_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

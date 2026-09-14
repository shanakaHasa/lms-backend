"""Enrolment endpoints.

Two things here are unusual for a CRUD API, and both exist because the
assistant will be driving this surface from step B6.

**POST answers 200 for a repeat.** Enrolling someone already enrolled is not an
error — it is a retry converging on the intended state. 201 means a row was
created; 200 means one already existed. A client can tell the difference without
the service having to raise on the ordinary case.

**DELETE withdraws rather than deletes.** The row stays, so the history stays,
and re-enrolling reuses it — which `UNIQUE (student_id, course_id)` requires in
any case.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.core.deps import Enrolments
from app.schemas.enrolment import EnrolmentCreate, EnrolmentOut

router = APIRouter(prefix="/enrolments", tags=["enrolments"])


@router.post(
    "",
    response_model=EnrolmentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Enrol a student in a course",
    description=(
        "Requires `enrolments:write`. Idempotent: a repeat returns 200 with the "
        "existing enrolment. A withdrawn enrolment is reactivated; a completed "
        "one is returned untouched, because a retry must not overwrite an outcome."
    ),
    responses={200: {"description": "The student was already enrolled."}},
)
async def create_enrolment(
    payload: EnrolmentCreate, service: Enrolments, response: Response
) -> EnrolmentOut:
    enrolment, created = await service.enrol(payload.student_id, payload.course_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return EnrolmentOut.model_validate(enrolment)


@router.delete(
    "/{enrolment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Withdraw an enrolment",
    description=(
        "Requires `enrolments:write`. Sets the status to `withdrawn` rather than "
        "removing the row. Withdrawing twice is a no-op."
    ),
)
async def withdraw_enrolment(enrolment_id: uuid.UUID, service: Enrolments) -> Response:
    await service.withdraw(enrolment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

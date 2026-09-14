"""Course endpoints, including the roster.

The roster lives here rather than under `/enrolments` because that is how it is
asked for: "who is in COMP201". Enrolment is the act; the roster is a view of a
course.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.core.deps import Courses, Enrolments
from app.schemas.common import DEFAULT_PAGE_SIZE, Limit, Offset, Page, SearchQuery
from app.schemas.course import CourseCreate, CourseOut, CourseUpdate
from app.schemas.enrolment import RosterEntry
from app.schemas.student import StudentSummary

router = APIRouter(prefix="/courses", tags=["courses"])


@router.post(
    "",
    response_model=CourseOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a course",
    description=(
        "Requires `courses:write`. The code is normalised to upper case, so a "
        "tenant cannot end up with both `COMP201` and `comp201` splitting one "
        "course's materials across two codes."
    ),
)
async def create_course(payload: CourseCreate, service: Courses) -> CourseOut:
    return CourseOut.model_validate(await service.create(payload))


@router.get(
    "",
    response_model=Page[CourseOut],
    summary="List courses",
    description="Requires `courses:read`.",
)
async def list_courses(
    service: Courses,
    limit: Limit = DEFAULT_PAGE_SIZE,
    offset: Offset = 0,
    status_filter: str | None = None,
    teacher_user_id: str | None = None,
    q: SearchQuery = None,
) -> Page[CourseOut]:
    courses, total = await service.list(
        limit=limit,
        offset=offset,
        status=status_filter,
        query=q,
        teacher_user_id=teacher_user_id,
    )
    return Page[CourseOut](
        items=[CourseOut.model_validate(c) for c in courses],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{course_id}",
    response_model=CourseOut,
    summary="Fetch one course",
    description="Requires `courses:read`.",
)
async def get_course(course_id: uuid.UUID, service: Courses) -> CourseOut:
    return CourseOut.model_validate(await service.get(course_id))


@router.patch(
    "/{course_id}",
    response_model=CourseOut,
    summary="Update a course",
    description=(
        "Requires `courses:write`. The code is not updatable: renaming it would "
        "strand every material already ingested against the old one. Archive the "
        "course and create the right one instead."
    ),
)
async def update_course(course_id: uuid.UUID, payload: CourseUpdate, service: Courses) -> CourseOut:
    return CourseOut.model_validate(await service.update(course_id, payload))


@router.get(
    "/{course_id}/roster",
    response_model=Page[RosterEntry],
    summary="List the students in a course",
    description=(
        "Requires `students:read`, because a roster is a list of named people. "
        "Soft-deleted students are excluded."
    ),
)
async def course_roster(
    course_id: uuid.UUID,
    service: Enrolments,
    limit: Limit = DEFAULT_PAGE_SIZE,
    offset: Offset = 0,
    status_filter: str | None = None,
) -> Page[RosterEntry]:
    entries, total = await service.roster(
        course_id, limit=limit, offset=offset, status=status_filter
    )
    return Page[RosterEntry](
        items=[
            RosterEntry(
                enrolment_id=enrolment.id,
                status=enrolment.status,
                grade=enrolment.grade,
                enrolled_at=enrolment.enrolled_at,
                student=StudentSummary.model_validate(student),
            )
            for enrolment, student in entries
        ],
        total=total,
        limit=limit,
        offset=offset,
    )

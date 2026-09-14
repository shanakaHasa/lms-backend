"""Request and response shapes for enrolments.

This is the table the assistant proposes writes to, so the request shape is the
narrowest in the service: two identifiers and nothing else. Grade and status
are not settable at creation — a model that hallucinated `"grade": "A"` into a
proposal would otherwise be writing an academic record.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.student import StudentSummary

__all__ = [
    "ENROLMENT_STATUSES",
    "EnrolmentCreate",
    "EnrolmentOut",
    "RosterEntry",
]

EnrolmentStatus = Literal["enrolled", "completed", "withdrawn"]
# Mirrors the CHECK constraint on `enrolments.status`; a test asserts they agree.
ENROLMENT_STATUSES: frozenset[str] = frozenset({"enrolled", "completed", "withdrawn"})


class EnrolmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: uuid.UUID
    course_id: uuid.UUID


class EnrolmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_id: uuid.UUID
    course_id: uuid.UUID
    status: str
    grade: str | None
    enrolled_at: datetime
    created_at: datetime
    updated_at: datetime


class RosterEntry(BaseModel):
    """A student's place in a course, as the roster view renders it."""

    model_config = ConfigDict(from_attributes=True)

    enrolment_id: uuid.UUID
    status: str
    grade: str | None
    enrolled_at: datetime
    student: StudentSummary

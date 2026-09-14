"""Request and response shapes for courses.

The course code is normalised to upper case on the way in. This is not
cosmetic: the code appears verbatim in questions the assistant has to answer
("what's in comp201 week 3"), and retrieval filters on it. A tenant holding both
`COMP201` and `comp201` would split one course's materials across two codes and
silently halve recall.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "COURSE_STATUSES",
    "CourseCreate",
    "CourseOut",
    "CourseSummary",
    "CourseUpdate",
    "normalise_code",
]

CourseStatus = Literal["active", "archived"]
# Mirrors the CHECK constraint on `courses.status`; a test asserts they agree.
COURSE_STATUSES: frozenset[str] = frozenset({"active", "archived"})

Code = Annotated[str, Field(min_length=2, max_length=32)]
Title = Annotated[str, Field(min_length=1, max_length=255)]
Credits = Annotated[int, Field(ge=0, le=200)]


def normalise_code(code: str) -> str:
    return code.strip().upper()


class CourseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Code
    title: Title
    description: str = Field(default="", max_length=10_000)
    term: str | None = Field(default=None, max_length=32)
    # An opaque user id from the auth service. There is no foreign key to
    # validate it against, which is exactly why the service boundary is real —
    # so this is stored as given and never dereferenced here.
    teacher_user_id: str | None = Field(default=None, max_length=128)
    credits: Credits | None = None
    status: CourseStatus = "active"

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class CourseUpdate(BaseModel):
    """The code is absent on purpose.

    Renaming a course code after materials have been ingested against it would
    strand every chunk filtered by the old one. If a code is genuinely wrong,
    archive the course and create the right one — which keeps the history
    honest instead of rewriting it.
    """

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: str | None = Field(default=None, max_length=10_000)
    term: str | None = Field(default=None, max_length=32)
    teacher_user_id: str | None = Field(default=None, max_length=128)
    credits: Credits | None = None
    status: CourseStatus | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class CourseSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    title: str
    term: str | None
    status: str


class CourseOut(CourseSummary):
    model_config = ConfigDict(from_attributes=True)

    description: str
    teacher_user_id: str | None
    credits: int | None
    created_at: datetime
    updated_at: datetime

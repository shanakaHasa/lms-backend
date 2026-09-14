"""Request and response shapes for students.

`tenant_id` appears in none of them. It arrives signed in the token and is
applied by the repository, so there is no field a caller could set to reach
another institution's records — the isolation is structural, not validated.

`created_by` is likewise absent from the request shapes: it is the authenticated
actor, and letting a caller supply it would make the audit trail forgeable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

__all__ = [
    "STUDENT_STATUSES",
    "StudentCreate",
    "StudentOut",
    "StudentSummary",
    "StudentUpdate",
    "normalise_email",
    "normalise_student_number",
]

StudentStatus = Literal["active", "inactive", "graduated", "withdrawn"]
# Mirrors the CHECK constraint on `students.status`. A test asserts the two
# agree, so adding a status to one without the other fails the suite rather
# than failing at runtime with a constraint violation.
STUDENT_STATUSES: frozenset[str] = frozenset({"active", "inactive", "graduated", "withdrawn"})

Name = Annotated[str, Field(min_length=1, max_length=128)]
StudentNumber = Annotated[str, Field(min_length=1, max_length=32)]
YearLevel = Annotated[int, Field(ge=1, le=20)]


def normalise_email(email: str) -> str:
    """The form the unique index and every lookup use.

    Case and surrounding whitespace are not identity: `Ada@Example.com ` and
    `ada@example.com` are one person, and without folding them the partial
    unique index would happily admit both.

    `EmailStr` already lowercases the domain, so only the local part is left to
    fold here — but folding it unconditionally means this function, not a
    validator's internals, is what the unique index depends on.
    """
    return email.strip().lower()


def normalise_student_number(student_number: str) -> str:
    """Institutions write these in upper case and type them in either."""
    return student_number.strip().upper()


class StudentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_number: StudentNumber
    first_name: Name
    last_name: Name
    email: EmailStr
    year_level: YearLevel | None = None
    status: StudentStatus = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class StudentUpdate(BaseModel):
    """Every field optional: this is a PATCH, and an omitted field is untouched.

    `None` therefore has to mean "clear it" for the nullable fields only, which
    is why `year_level` is the sole field where it is accepted. Distinguishing
    omitted from null is what `model_dump(exclude_unset=True)` gives us.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: Name | None = None
    last_name: Name | None = None
    email: EmailStr | None = None
    year_level: YearLevel | None = None
    status: StudentStatus | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class StudentSummary(BaseModel):
    """What a roster row carries: enough to identify a student, no more."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    student_number: str
    first_name: str
    last_name: str
    email: str
    status: str


class StudentOut(StudentSummary):
    """The full record.

    `email_normalized` is deliberately not exposed. It is an implementation
    detail of the unique index, and returning both invites a client to key off
    the wrong one.
    """

    model_config = ConfigDict(from_attributes=True)

    year_level: int | None
    metadata: dict[str, Any] = Field(validation_alias="student_metadata")
    created_by: str
    created_at: datetime
    updated_at: datetime

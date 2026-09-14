"""The SQL, verified without a database.

Phase 1 ships every table, every query and the whole agent before a single
migration is applied. The fakes cover the business rules, but they model
dictionaries rather than Postgres — so nothing in them would notice a column
that does not exist, a join to the wrong table, or a predicate that silently
drops the tenant filter.

Compiling each statement against the Postgres dialect closes most of that gap.
SQLAlchemy resolves every column against the mapped model and emits real
Postgres SQL, so a typo raises here rather than at 3am in Phase 2. What this
cannot catch is anything the *database* knows and the models do not — which is
what the migration drift checker and the integration suite are for.

`literal_binds` is used throughout so the rendered SQL contains the actual
values, which is what makes "is the tenant in the WHERE clause" assertable.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from app.repositories import course as course_repo
from app.repositories import enrolment as enrolment_repo
from app.repositories import student as student_repo

TENANT = "tenant-a"
STUDENT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
COURSE_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


def sql(statement: Select) -> str:  # type: ignore[type-arg]
    """Render a statement as the Postgres it would actually execute."""
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


# Every statement this service can issue against these three tables. The list is
# the point: a new query that is not here is a query nobody has compiled.
ALL_STATEMENTS = {
    "student.get": student_repo.stmt_get_student(TENANT, STUDENT_ID),
    "student.by_ids": student_repo.stmt_students_by_ids(TENANT, [STUDENT_ID]),
    "student.by_number": student_repo.stmt_student_by_number(TENANT, "S001"),
    "student.by_email": student_repo.stmt_student_by_email(TENANT, "ada@example.com"),
    "student.list": student_repo.stmt_list_students(TENANT, limit=10, offset=0),
    "student.list_filtered": student_repo.stmt_list_students(
        TENANT, limit=10, offset=0, status="active", query="love"
    ),
    "student.count": student_repo.stmt_count_students(TENANT),
    "student.count_filtered": student_repo.stmt_count_students(
        TENANT, status="active", query="love"
    ),
    "course.get": course_repo.stmt_get_course(TENANT, COURSE_ID),
    "course.by_code": course_repo.stmt_course_by_code(TENANT, "COMP201"),
    "course.list": course_repo.stmt_list_courses(TENANT, limit=10, offset=0),
    "course.list_filtered": course_repo.stmt_list_courses(
        TENANT, limit=10, offset=0, status="active", query="comp", teacher_user_id="t-9"
    ),
    "course.count": course_repo.stmt_count_courses(TENANT),
    "enrolment.get": enrolment_repo.stmt_get_enrolment(TENANT, COURSE_ID),
    "enrolment.for_pair": enrolment_repo.stmt_enrolment_for_pair(TENANT, STUDENT_ID, COURSE_ID),
    "enrolment.roster": enrolment_repo.stmt_roster(TENANT, COURSE_ID, limit=10, offset=0),
    "enrolment.roster_filtered": enrolment_repo.stmt_roster(
        TENANT, COURSE_ID, limit=10, offset=0, status="enrolled"
    ),
    "enrolment.count_roster": enrolment_repo.stmt_count_roster(TENANT, COURSE_ID),
    "enrolment.count_roster_filtered": enrolment_repo.stmt_count_roster(
        TENANT, COURSE_ID, status="enrolled"
    ),
    "enrolment.for_student": enrolment_repo.stmt_list_for_student(TENANT, STUDENT_ID),
}


# ── The properties that must hold of every statement ────────────────────────


@pytest.mark.parametrize("name", sorted(ALL_STATEMENTS))
def test_every_statement_compiles_to_postgres(name: str) -> None:
    """The whole reason this file exists.

    Compilation resolves every column against the mapped model, so a renamed or
    misspelled attribute fails here instead of in Phase 2.
    """
    assert sql(ALL_STATEMENTS[name]).strip()


@pytest.mark.parametrize("name", sorted(ALL_STATEMENTS))
def test_every_statement_is_scoped_to_one_tenant(name: str) -> None:
    """The single assertion that matters most in this file.

    Tenant scoping is structural — the repository is constructed with a tenant
    and no method accepts one — but "structural" is a claim about code that has
    to be checked against the SQL actually emitted.
    """
    rendered = sql(ALL_STATEMENTS[name])
    assert f"tenant_id = '{TENANT}'" in rendered, rendered


# ── Soft deletion, which is easy to get half-right ──────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "student.get",
        "student.by_ids",
        "student.by_number",
        "student.by_email",
        "student.list",
        "student.count",
    ],
)
def test_no_student_query_can_see_a_deleted_row(name: str) -> None:
    # Without this the partial unique indexes would also be wrong: a query that
    # ignores `deleted_at` would find the deleted row the index deliberately
    # excludes, and the two would disagree about whether an email is in use.
    assert "deleted_at IS NULL" in sql(ALL_STATEMENTS[name])


def test_the_roster_excludes_deleted_students_itself() -> None:
    """The predicate that looks redundant next to ON DELETE CASCADE.

    It is not redundant: students are *soft* deleted, so the cascade never
    fires and the enrolment row outlives its student. Without this, a deleted
    person keeps appearing on class lists.
    """
    for name in ("enrolment.roster", "enrolment.count_roster"):
        rendered = sql(ALL_STATEMENTS[name])
        assert "students.deleted_at IS NULL" in rendered, rendered


# ── Shape of individual queries ─────────────────────────────────────────────


def test_listing_students_has_a_total_order() -> None:
    """Offset pagination without a total order is not stable.

    Two requests for the same offset can legitimately return different rows if
    the sort key has ties, so the primary key is appended as a tiebreak.
    """
    rendered = sql(ALL_STATEMENTS["student.list"])
    assert "ORDER BY students.last_name, students.first_name, students.id" in rendered
    assert "LIMIT 10" in rendered


def test_searching_students_covers_every_field_a_teacher_would_type() -> None:
    rendered = sql(ALL_STATEMENTS["student.list_filtered"]).lower()
    for column in ("first_name", "last_name", "student_number", "email_normalized"):
        assert f"{column} ilike" in rendered, column


def _where(rendered: str) -> str:
    """The WHERE clause alone, with ORDER BY / LIMIT / OFFSET trimmed off."""
    body = rendered.split("WHERE ", 1)[1]
    for tail in (" ORDER BY ", " LIMIT ", " OFFSET "):
        body = body.split(tail, 1)[0]
    return " ".join(body.split())


@pytest.mark.parametrize(
    ("listed", "counted"),
    [
        ("student.list_filtered", "student.count_filtered"),
        ("student.list", "student.count"),
        ("enrolment.roster", "enrolment.count_roster"),
        ("enrolment.roster_filtered", "enrolment.count_roster_filtered"),
    ],
)
def test_a_count_filters_exactly_as_its_list_does(listed: str, counted: str) -> None:
    """`total` has to describe the same set the page came from.

    Comparing the whole WHERE clause rather than spot-checking predicates is
    what makes this hold for filters nobody has thought of yet: a predicate
    added to one and not the other fails immediately. If they ever diverged,
    every pager in the UI would offer pages that do not exist.
    """
    assert _where(sql(ALL_STATEMENTS[listed])) == _where(sql(ALL_STATEMENTS[counted]))


def test_a_search_term_reaches_the_sql_as_a_contains_pattern() -> None:
    # Rendered as `%%` because the compiler escapes `%` for the DBAPI
    # paramstyle; the database still sees a single one.
    assert "ILIKE '%%love%%'" in sql(ALL_STATEMENTS["student.list_filtered"])


def test_the_roster_joins_students_rather_than_fetching_them_one_by_one() -> None:
    # The N+1 that turns a 40-student roster into 41 round trips.
    rendered = sql(ALL_STATEMENTS["enrolment.roster"])
    assert "JOIN students ON students.id = enrolments.student_id" in rendered


def test_resolving_many_students_is_one_query() -> None:
    assert "IN (" in sql(ALL_STATEMENTS["student.by_ids"])


def test_the_idempotence_lookup_matches_the_unique_constraint() -> None:
    """`for_pair` is what makes a retried proposal harmless.

    It has to key on exactly the columns `uq_enrolments_student_course` covers,
    or the check and the constraint would disagree and the retry would surface
    as a 500.
    """
    rendered = sql(ALL_STATEMENTS["enrolment.for_pair"])
    assert f"enrolments.student_id = '{STUDENT_ID}'" in rendered
    assert f"enrolments.course_id = '{COURSE_ID}'" in rendered


def test_counts_are_counts_not_fetches() -> None:
    # `len(rows)` after loading every match is how a list endpoint becomes an
    # accidental table scan over a whole institution.
    for name in ("student.count", "course.count", "enrolment.count_roster"):
        rendered = sql(ALL_STATEMENTS[name]).lower()
        assert rendered.startswith("select count(")
        assert "limit" not in rendered


# ── Guarding the schema/enum agreement ──────────────────────────────────────


def test_the_status_literals_match_the_check_constraints() -> None:
    """The API's allowed values and the database's must not drift apart.

    A status the schema accepts and the CHECK constraint rejects is a 500 that
    only appears once a real database is attached — which, in this project, is
    a whole phase away.
    """
    from sqlalchemy import CheckConstraint

    import app.models  # noqa: F401
    from app.core.db import Base
    from app.schemas.course import COURSE_STATUSES
    from app.schemas.enrolment import ENROLMENT_STATUSES
    from app.schemas.student import STUDENT_STATUSES

    for table_name, allowed in (
        ("students", STUDENT_STATUSES),
        ("courses", COURSE_STATUSES),
        ("enrolments", ENROLMENT_STATUSES),
    ):
        check = next(
            str(c.sqltext)
            for c in Base.metadata.tables[table_name].constraints
            if isinstance(c, CheckConstraint) and "status IN" in str(c.sqltext)
        )
        in_database = {value.strip("' ") for value in check.split("(")[1].rstrip(")").split(",")}
        assert in_database == allowed, f"{table_name}: {in_database} != {allowed}"

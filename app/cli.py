"""Operator CLI.

Calls the same service layer the HTTP API does -- nothing in `app/services/`
imports FastAPI, which is what makes that possible.

Commands land as the steps do:
  B3  seed-dev      an institution with courses and students  <- done
  B4  ingest        run the worker once, or drain the queue
  B5  reindex       rebuild the vector index from Postgres
"""

from __future__ import annotations

import asyncio
import time

import typer

from app.core.config import settings

app = typer.Typer(help="TeachAssist backend operator commands", no_args_is_help=True)


def _redacted_target(url: str) -> str:
    """host:port/database, with the credentials stripped."""
    return url.rsplit("@", 1)[-1] if "@" in url else url


@app.command()
def info() -> None:
    """Print the effective configuration. Secrets are never included."""
    typer.echo(f"service     : {settings.service_name}")
    typer.echo(f"environment : {settings.app_env}")
    typer.echo(f"database    : {_redacted_target(settings.database_url)}")
    typer.echo(f"db ssl      : {settings.db_ssl_mode}")
    auth = "DISABLED (local principal)" if settings.auth_disabled else settings.auth_issuer
    typer.echo(f"auth        : {auth}")
    typer.echo(f"storage     : {settings.storage_backend}")
    typer.echo(f"queue       : {settings.queue_backend}")
    typer.echo(f"llm         : {settings.llm_provider}")
    typer.echo(f"embeddings  : {settings.embedding_model} ({settings.embedding_dim}d)")
    typer.echo(f"vector index: {settings.pinecone_index}")


@app.command("db-check")
def db_check() -> None:
    """Connect to the database and report version and round-trip latency.

    Latency is printed because it decides how you work: a remote database adds
    its round trip to every query in the integration suite.
    """
    from sqlalchemy import text

    from app.core.db import engine

    async def _check() -> int:
        typer.echo(f"connecting to {_redacted_target(settings.database_url)} ...")
        started = time.perf_counter()
        try:
            async with engine.connect() as conn:
                connect_ms = (time.perf_counter() - started) * 1000
                version = (await conn.execute(text("SELECT version()"))).scalar_one()
                ping_started = time.perf_counter()
                await conn.execute(text("SELECT 1"))
                ping_ms = (time.perf_counter() - ping_started) * 1000
        except Exception as exc:
            typer.secho(f"FAILED: {type(exc).__name__}: {exc}", fg=typer.colors.RED)
            return 1
        finally:
            await engine.dispose()

        typer.secho("connected", fg=typer.colors.GREEN)
        typer.echo(f"  server  : {str(version).split(' on ')[0]}")
        typer.echo(f"  connect : {connect_ms:.0f} ms")
        typer.echo(f"  query   : {ping_ms:.0f} ms round trip")
        if ping_ms > 100:
            typer.secho(
                f"\n  {ping_ms:.0f} ms per query is high. Every integration test pays it;\n"
                "  prefer the unit suite locally and integration in CI.",
                fg=typer.colors.YELLOW,
            )
        return 0

    raise typer.Exit(asyncio.run(_check()))


# ── Seeding ─────────────────────────────────────────────────────────────────

# Invented people. This project never holds real student data, and a seeder is
# exactly where a "just this once" copy of a real export would end up — so the
# names live here, in source, where that cannot happen by accident.
SEED_STUDENTS = [
    ("S1001", "Ada", "Lovelace", 1),
    ("S1002", "Grace", "Hopper", 1),
    ("S1003", "Barbara", "Liskov", 2),
    ("S1004", "Alan", "Turing", 2),
    ("S1005", "Katherine", "Johnson", 3),
    ("S1006", "Donald", "Knuth", 3),
]

SEED_COURSES = [
    ("COMP101", "Introduction to Programming", "2026-S1", 15),
    ("COMP201", "Systems Programming", "2026-S1", 15),
    ("COMP301", "Compilers", "2026-S2", 20),
]


@app.command("seed-dev")
def seed_dev(
    tenant: str = typer.Option("", help="Tenant to seed. Defaults to the local principal's."),
) -> None:
    """Fill a development institution with synthetic courses and students.

    Runs through the same services the API does, so the data it writes obeys
    every rule the API enforces — normalisation, uniqueness, audit rows — rather
    than being INSERTed past them.
    """
    if settings.app_env == "prod":
        # A seeder pointed at production is one mistyped environment variable
        # away at all times, and it writes rows that look real.
        typer.secho("refusing to seed a production environment", fg=typer.colors.RED)
        raise typer.Exit(1)

    from app.core.db import SessionLocal, engine
    from app.core.errors import Conflict
    from app.core.security import LOCAL_PRINCIPAL, Principal
    from app.repositories.course import SqlCourseRepository
    from app.repositories.enrolment import SqlEnrolmentRepository
    from app.repositories.student import SqlStudentRepository
    from app.schemas.course import CourseCreate
    from app.schemas.student import StudentCreate
    from app.services.audit import SqlAuditSink
    from app.services.course_service import CourseService
    from app.services.enrolment_service import EnrolmentService
    from app.services.student_service import StudentService

    principal = (
        LOCAL_PRINCIPAL
        if not tenant
        else Principal(
            user_id=LOCAL_PRINCIPAL.user_id,
            tenant_id=tenant,
            email=LOCAL_PRINCIPAL.email,
            scopes=LOCAL_PRINCIPAL.scopes,
        )
    )

    async def _seed() -> int:
        created = {"students": 0, "courses": 0, "enrolments": 0}
        async with SessionLocal() as session:
            audit = SqlAuditSink(session, principal)
            student_repo = SqlStudentRepository(session, principal.tenant_id)
            course_service = CourseService(
                SqlCourseRepository(session, principal.tenant_id), audit, principal
            )
            student_service = StudentService(student_repo, audit, principal)
            enrolment_service = EnrolmentService(
                SqlEnrolmentRepository(session, principal.tenant_id),
                SqlStudentRepository(session, principal.tenant_id),
                SqlCourseRepository(session, principal.tenant_id),
                audit,
                principal,
            )

            courses = []
            for code, title, term, credits in SEED_COURSES:
                try:
                    course = await course_service.create(
                        CourseCreate(
                            code=code,
                            title=title,
                            term=term,
                            credits=credits,
                            teacher_user_id=principal.user_id,
                        )
                    )
                    created["courses"] += 1
                except Conflict:
                    # Re-running the seeder is normal; it tops up rather than
                    # failing halfway and leaving a partial institution.
                    course = await course_service.get_by_code(code)
                courses.append(course)

            students = []
            for number, first, last, year in SEED_STUDENTS:
                try:
                    student = await student_service.create(
                        StudentCreate(
                            student_number=number,
                            first_name=first,
                            last_name=last,
                            email=f"{first.lower()}.{last.lower()}@students.example.com",
                            year_level=year,
                        )
                    )
                    created["students"] += 1
                except Conflict:
                    existing = await student_repo.by_number(number)
                    if existing is None:
                        continue
                    student = existing
                students.append(student)

            # Every student into their year's course, so the roster, the search
            # and (from B5) retrieval all have something to work with.
            for student in students:
                course = courses[min((student.year_level or 1) - 1, len(courses) - 1)]
                _, was_created = await enrolment_service.enrol(student.id, course.id)
                created["enrolments"] += int(was_created)

            await session.commit()

        typer.secho(f"seeded {principal.tenant_id}", fg=typer.colors.GREEN)
        for label, count in created.items():
            typer.echo(f"  {label:<11}: {count} new")
        await engine.dispose()
        return 0

    raise typer.Exit(asyncio.run(_seed()))


if __name__ == "__main__":
    app()

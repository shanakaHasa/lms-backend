"""Operator CLI.

Calls the same service layer the HTTP API does -- nothing in `app/services/`
imports FastAPI, which is what makes that possible.

Commands land as the steps do:
  B3  seed-dev      an institution with courses and students
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


if __name__ == "__main__":
    app()

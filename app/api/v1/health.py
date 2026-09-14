"""Liveness and readiness.

`/healthz` answers "is this process alive" and must never touch a dependency --
otherwise a brief database blip makes the load balancer kill containers that
were about to recover. `/readyz` answers "can this process serve traffic".

The distinction that matters here: **auth being down does not make this service
unready.** Tokens are verified offline against cached keys, so an auth outage
degrades new logins, not this service. Auth appears in `checks` as information
only. Wiring it into readiness would turn an auth blip into a task-recycling
storm at the exact moment auth is already struggling.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.core.security import jwks_cache

router = APIRouter(tags=["health"])
log = get_logger(__name__)

VERSION = "0.1.0"


class HealthResponse(BaseModel):
    status: str
    version: str
    service: str
    checks: dict[str, str] = {}


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=VERSION, service=settings.service_name)


@router.get("/readyz", response_model=HealthResponse)
async def readyz(response: Response) -> HealthResponse:
    checks: dict[str, str] = {}

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        log.warning("readiness_check_failed", dependency="postgres", error=str(exc))
        checks["postgres"] = "error"

    if settings.auth_disabled:
        checks["auth"] = "disabled"
    elif jwks_cache.is_empty:
        # No key material at all means no request can be authenticated. This is
        # the one auth condition that genuinely blocks readiness.
        checks["auth"] = "no_keys"
    else:
        checks["auth"] = "stale" if jwks_cache.is_stale else "ok"

    checks["storage"] = settings.storage_backend
    checks["queue"] = settings.queue_backend

    healthy = checks["postgres"] == "ok" and checks["auth"] != "no_keys"
    if not healthy:
        response.status_code = 503
    return HealthResponse(
        status="ok" if healthy else "degraded",
        version=VERSION,
        service=settings.service_name,
        checks=checks,
    )

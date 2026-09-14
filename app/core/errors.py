"""Typed application errors, mapped to HTTP responses in one place.

Same hierarchy as auth-backend so the two services behave identically to a
client. What differs is the domain errors at the bottom: this service has an
approval engine, and each of its failure modes needs to be distinguishable by a
caller so the UI can say something useful rather than "something went wrong".
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger, request_id_ctx

log = get_logger(__name__)


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class UpstreamUnavailable(AppError):
    status_code = 503
    code = "upstream_unavailable"


# ── Approval engine (step B7) ───────────────────────────────────────────────
# Each of these is a distinct thing a user can do something about, so they get
# distinct codes rather than collapsing into a generic 409.


class ProposalExpired(AppError):
    """The proposal outlived its window. Ask the assistant again."""

    status_code = 410
    code = "proposal_expired"


class ProposalAlreadyDecided(AppError):
    """Approved or rejected already. Approving twice must be a no-op, not a
    second write, so this is what the second caller sees."""

    status_code = 409
    code = "proposal_already_decided"


class ProposalTampered(AppError):
    """The arguments no longer match the hash computed when the proposal was
    made. Someone changed the payload between proposal and approval."""

    status_code = 409
    code = "proposal_tampered"


class ProposalRevalidationFailed(AppError):
    """The world moved between proposal and approval -- the student was deleted,
    or is already enrolled. Executing blind here would be a TOCTOU bug."""

    status_code = 409
    code = "proposal_revalidation_failed"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_r: Request, exc: AppError) -> JSONResponse:
        log.warning("app_error", code=exc.code, message=exc.message, **exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message, **exc.detail},
                "request_id": request_id_ctx.get(),
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(_r: Request, exc: Exception) -> JSONResponse:
        # Never leak internals. The request_id ties this to the stack trace.
        log.exception("unhandled_exception", error=str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "internal_error", "message": "Internal server error"},
                "request_id": request_id_ctx.get(),
            },
        )

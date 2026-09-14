"""FastAPI dependencies: the seam between HTTP and the service layer.

Routers depend on these; services depend on nothing from FastAPI. That split is
what lets the CLI, the eval runner and the MCP server drive exactly the same
code the HTTP layer does.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import Unauthorized
from app.core.logging import actor_id_ctx, tenant_id_ctx
from app.core.security import Principal, verify_token


async def current_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    from app.core.config import settings

    if settings.auth_disabled:
        from app.core.security import LOCAL_PRINCIPAL

        _bind_log_context(LOCAL_PRINCIPAL)
        return LOCAL_PRINCIPAL

    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")

    principal = await verify_token(authorization.split(" ", 1)[1])
    _bind_log_context(principal)
    return principal


def _bind_log_context(principal: Principal) -> None:
    # Every subsequent log line in this request carries who and which tenant,
    # which is what makes an audit row traceable back to a request.
    tenant_id_ctx.set(principal.tenant_id)
    actor_id_ctx.set(principal.user_id)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
DbSession = Annotated[AsyncSession, Depends(get_session)]

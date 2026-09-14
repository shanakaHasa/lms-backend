"""Shared service construction rules.

Services hold a `Principal` and a repository, and those two carry a tenant each.
If they ever disagree, every tenant check below is being made against one tenant
while the query runs against another — so the mismatch is caught at
construction, loudly, rather than becoming a cross-tenant read.
"""

from __future__ import annotations

from typing import Protocol

from app.core.security import Principal

__all__ = ["HasTenant", "assert_same_tenant"]


class HasTenant(Protocol):
    tenant_id: str


def assert_same_tenant(principal: Principal, *repositories: HasTenant) -> None:
    for repository in repositories:
        if repository.tenant_id != principal.tenant_id:
            # A programming error in the wiring, not anything a request can
            # cause — so it is not an AppError with a status code. If this ever
            # fires in production it should page someone, not return a 4xx.
            raise RuntimeError(
                f"repository is scoped to {repository.tenant_id!r} but the principal "
                f"belongs to {principal.tenant_id!r}"
            )

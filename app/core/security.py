"""Token verification.

This service **verifies** tokens and never issues them. That asymmetry is the
whole boundary with `auth-backend`, and there is deliberately no code here that
can mint one.

Verification is offline against cached JWKS, so a normal request never calls
auth. The cost of that choice is that a token cannot be withdrawn mid-life; the
compensating controls are short token lifetimes and (later) the revocation feed.

While `auth-backend` is still being built, `AUTH_DISABLED=true` yields a fixed
principal. Everything downstream already depends on `Principal`, so switching to
real tokens changes configuration, not structure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import jwt

from app.core.config import settings
from app.core.errors import Forbidden, Unauthorized
from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Principal:
    """Who is making this request, and what they may do.

    `tenant_id` arrives signed, in the token. It is never read from a header, a
    query parameter, a request body, or a tool argument -- which is what makes
    cross-tenant access impossible to reach by crafting input.
    """

    user_id: str
    tenant_id: str
    email: str | None
    scopes: frozenset[str]

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise Forbidden(f"missing required scope: {scope}")

    def has(self, scope: str) -> bool:
        return scope in self.scopes


# Development stand-in, used only when AUTH_DISABLED=true.
#
# It carries every scope, including `proposals:approve`, because a single-user
# local setup has to be able to both propose and approve. The separation between
# those two scopes is what tests exercise, and they construct principals
# directly rather than using this one -- so the permissive default here cannot
# quietly weaken the assertions that matter.
LOCAL_PRINCIPAL = Principal(
    user_id="local-teacher",
    tenant_id="local-institution",
    email="teacher@example.invalid",
    scopes=frozenset(
        {
            "students:read",
            "students:write",
            "courses:read",
            "courses:write",
            "enrolments:write",
            "materials:read",
            "materials:write",
            "chat:use",
            "proposals:approve",
        }
    ),
)


class JwksCache:
    """Caches auth's public keys, with the behaviours JWKS clients get wrong.

    1. Stale-while-revalidate: an expired cache is still served while a refresh
       happens, so an auth blip never adds latency to a request here.
    2. Refetch once on an unknown `kid` -- this is the key-rotation path, and it
       must work without a deploy.
    3. **That refetch is rate-limited.** A negative cache for unknown kids plus a
       per-process cooldown. Without this, an attacker sending tokens with
       random `kid` values turns this service into a DoS amplifier pointed at
       our own identity provider. This is the most commonly missed detail in
       JWKS handling.
    """

    def __init__(self) -> None:
        self._keys: dict[str, Any] = {}
        self._fetched_at: float = 0.0
        self._last_fetch_attempt: float = 0.0
        self._unknown_kids: dict[str, float] = {}

    @property
    def is_empty(self) -> bool:
        return not self._keys

    @property
    def is_stale(self) -> bool:
        return time.monotonic() - self._fetched_at > settings.jwks_cache_ttl_seconds

    def _cooling_down(self) -> bool:
        elapsed = time.monotonic() - self._last_fetch_attempt
        return elapsed < settings.jwks_unknown_kid_cooldown_seconds

    def _is_known_bad(self, kid: str) -> bool:
        seen = self._unknown_kids.get(kid)
        if seen is None:
            return False
        if time.monotonic() - seen > settings.jwks_negative_cache_seconds:
            del self._unknown_kids[kid]
            return False
        return True

    async def get_key(self, kid: str) -> Any:
        if self._is_known_bad(kid):
            # Already established this kid does not exist. Do not ask again.
            raise Unauthorized("unknown signing key")

        if kid in self._keys and not self.is_stale:
            return self._keys[kid]

        if kid not in self._keys and not self._cooling_down():
            await self.refresh()

        if kid not in self._keys:
            self._unknown_kids[kid] = time.monotonic()
            raise Unauthorized("unknown signing key")
        return self._keys[kid]

    async def refresh(self) -> None:
        import httpx2

        self._last_fetch_attempt = time.monotonic()
        try:
            async with httpx2.AsyncClient(timeout=5.0) as client:
                response = await client.get(settings.jwks_uri)
                response.raise_for_status()
                document = response.json()
        except Exception as exc:
            # Serving a stale key set beats failing the request. Only a cold
            # start with no keys at all is fatal, and /readyz reports that.
            log.warning("jwks_refresh_failed", error=str(exc), stale=not self.is_empty)
            return

        keys: dict[str, Any] = {}
        for entry in document.get("keys", []):
            # Ignore anything we cannot use rather than failing the whole set.
            if entry.get("kty") != "RSA" or entry.get("alg") not in (None, "RS256"):
                continue
            kid = entry.get("kid")
            if not isinstance(kid, str):
                continue
            try:
                keys[kid] = jwt.PyJWK(entry).key
            except Exception as exc:
                log.warning("jwks_key_unusable", kid=kid, error=str(exc))

        if keys:
            self._keys = keys
            self._fetched_at = time.monotonic()
            self._unknown_kids.clear()
            log.info("jwks_refreshed", key_count=len(keys))


jwks_cache = JwksCache()


async def verify_token(token: str) -> Principal:
    if settings.auth_disabled:
        return LOCAL_PRINCIPAL

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise Unauthorized(f"malformed token: {exc}") from exc

    kid = header.get("kid")
    if not isinstance(kid, str):
        raise Unauthorized("token has no key id")

    key = await jwks_cache.get_key(kid)

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            # Pinned, so `alg: none` and HS256-signed-with-the-public-key are
            # structurally impossible rather than merely unlikely.
            algorithms=["RS256"],
            issuer=settings.auth_issuer,
            # Verified, unlike the reference scaffold this replaces. Without it a
            # token minted for auth's own admin API is replayable here.
            audience=settings.auth_audience,
            leeway=settings.clock_skew_leeway_seconds,
        )
    except jwt.PyJWTError as exc:
        raise Unauthorized(f"invalid token: {exc}") from exc

    if claims.get("token_use") != "access":
        raise Unauthorized("expected an access token")

    # Without a tenant a request cannot be scoped to an institution, so refuse
    # rather than defaulting to anything.
    tenant_id = claims.get("tid")
    if not tenant_id:
        raise Unauthorized("token has no tenant claim")

    return Principal(
        user_id=claims["sub"],
        tenant_id=str(tenant_id),
        email=claims.get("email"),
        scopes=frozenset(str(claims.get("scope", "")).split()),
    )


async def warm_jwks() -> None:
    """Fetch at startup so the first real request is not slowed by it."""
    if settings.auth_disabled:
        return
    await jwks_cache.refresh()

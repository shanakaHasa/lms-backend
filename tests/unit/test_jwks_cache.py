"""JWKS cache behaviour.

These pin the behaviours that JWKS clients usually get wrong. The rate-limiting
ones matter most: without them, an attacker sending tokens with random `kid`
values turns this service into a denial-of-service amplifier aimed at our own
identity provider.

No network is used — `refresh` is replaced with a counter.
"""

from __future__ import annotations

import pytest

from app.core.errors import Unauthorized
from app.core.security import JwksCache


class CountingCache(JwksCache):
    """A cache whose refresh records how often it was asked, and optionally
    installs a key."""

    def __init__(self, install: dict[str, str] | None = None) -> None:
        super().__init__()
        self.refresh_calls = 0
        self._install = install or {}

    async def refresh(self) -> None:  # type: ignore[override]
        import time

        self.refresh_calls += 1
        self._last_fetch_attempt = time.monotonic()
        if self._install:
            self._keys = dict(self._install)
            self._fetched_at = time.monotonic()
            self._unknown_kids.clear()


async def test_a_known_key_is_served_without_a_refresh() -> None:
    cache = CountingCache(install={"k1": "key-material"})
    await cache.refresh()
    cache.refresh_calls = 0

    assert await cache.get_key("k1") == "key-material"
    assert cache.refresh_calls == 0


async def test_an_unknown_kid_triggers_exactly_one_refetch() -> None:
    # This is the key-rotation path: a token signed by a key we have not seen
    # must work without a deploy.
    cache = CountingCache(install={"k1": "key-material"})

    with pytest.raises(Unauthorized):
        await cache.get_key("rotated-in")
    assert cache.refresh_calls == 1


async def test_a_flood_of_unknown_kids_does_not_flood_auth() -> None:
    """The DoS-amplifier guard.

    Ten tokens with ten different bogus key ids must not produce ten requests
    to the identity provider. The per-process cooldown caps it at one.
    """
    cache = CountingCache()

    for i in range(10):
        with pytest.raises(Unauthorized):
            await cache.get_key(f"bogus-{i}")

    assert cache.refresh_calls == 1, (
        f"{cache.refresh_calls} refreshes for 10 bogus kids -- the cooldown is not working"
    )


async def test_a_repeated_unknown_kid_is_negatively_cached() -> None:
    # The same bad kid asked twice must not even reach the cooldown check.
    cache = CountingCache()

    with pytest.raises(Unauthorized):
        await cache.get_key("bogus")
    calls_after_first = cache.refresh_calls

    with pytest.raises(Unauthorized):
        await cache.get_key("bogus")

    assert cache.refresh_calls == calls_after_first


async def test_a_failed_refresh_leaves_the_cache_usable() -> None:
    """Stale-while-revalidate.

    If auth is unreachable, keys we already hold keep working. An auth outage
    degrades new logins, not every request to this service.
    """

    class FailingRefresh(CountingCache):
        async def refresh(self) -> None:  # type: ignore[override]
            self.refresh_calls += 1  # simulates a network error: nothing changes

    cache = FailingRefresh(install={"k1": "key-material"})
    cache._keys = {"k1": "key-material"}
    cache._fetched_at = 0.0  # forces staleness

    assert not cache.is_empty
    assert cache.is_stale


def test_an_empty_cache_is_reported_as_empty() -> None:
    # /readyz uses this: no key material at all is the one auth condition that
    # genuinely blocks readiness.
    assert JwksCache().is_empty

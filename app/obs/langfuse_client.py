"""Langfuse tracing.

Two rules this module enforces, and they are the reason it is a facade rather
than direct SDK calls scattered through the code:

1. **Observability is never load-bearing.** If Langfuse is down, misconfigured,
   or simply not installed, every call here becomes a no-op and the request
   still succeeds. Callers never branch on `if tracing_enabled`.
2. **Nothing leaves the trust boundary un-redacted.** Every input, output and
   metadata payload goes through `app.core.redaction` first, so student contact
   details and API keys cannot reach a third-party trace.

Prompts are fetched from Langfuse prompt management, so a prompt change is a
versioned, reviewable event that needs no deploy -- and the version that
produced an answer is stamped on both the trace and the eval run that graded it.
The in-repo copy is the fallback, so a prompt-service outage degrades to "last
known good" rather than an outage.

Pinned to Langfuse v2: the SDK major must match the server major, and a v2
server is a single container where v3+ needs ClickHouse, Redis and object
storage.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger, trace_id_ctx
from app.core.redaction import redact_payload

log = get_logger(__name__)

_client: Any | None = None
_init_attempted = False


def get_client() -> Any | None:
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True

    if not settings.langfuse_enabled:
        return None
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        log.warning("langfuse_keys_missing", detail="tracing disabled")
        return None

    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            flush_at=20,
            flush_interval=2.0,
        )
        log.info("langfuse_initialised", host=settings.langfuse_host)
    except Exception as exc:
        # Never fail a request because tracing failed to start.
        log.warning("langfuse_init_failed", error=str(exc))
        _client = None
    return _client


def _clean(value: Any) -> Any:
    return redact_payload(value, enabled=settings.langfuse_mask_pii)


def _clean_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    for key in ("input", "output", "metadata"):
        if key in kwargs:
            kwargs[key] = _clean(kwargs[key])
    return kwargs


class Span:
    """Wraps a Langfuse span, or nothing at all."""

    def __init__(self, handle: Any | None) -> None:
        self._handle = handle

    def end(self, **kwargs: Any) -> None:
        if self._handle is None:
            return
        try:
            self._handle.end(**_clean_kwargs(kwargs))
        except Exception as exc:
            log.debug("langfuse_span_end_failed", error=str(exc))

    def update(self, **kwargs: Any) -> None:
        if self._handle is None:
            return
        try:
            self._handle.update(**_clean_kwargs(kwargs))
        except Exception as exc:
            log.debug("langfuse_span_update_failed", error=str(exc))


class Trace:
    """Wraps a Langfuse trace, or nothing at all.

    The point of the null case: callers write `trace.span(...)` unconditionally
    and never guard on whether tracing is configured.
    """

    def __init__(self, handle: Any | None) -> None:
        self._handle = handle

    @property
    def id(self) -> str | None:
        return getattr(self._handle, "id", None)

    def span(self, name: str, **kwargs: Any) -> Span:
        if self._handle is None:
            return Span(None)
        try:
            return Span(self._handle.span(name=name, **_clean_kwargs(kwargs)))
        except Exception as exc:
            log.debug("langfuse_span_failed", error=str(exc))
            return Span(None)

    def generation(self, name: str, **kwargs: Any) -> Span:
        if self._handle is None:
            return Span(None)
        try:
            return Span(self._handle.generation(name=name, **_clean_kwargs(kwargs)))
        except Exception as exc:
            log.debug("langfuse_generation_failed", error=str(exc))
            return Span(None)

    def score(self, name: str, value: float, comment: str | None = None) -> None:
        if self._handle is None:
            return
        try:
            self._handle.score(name=name, value=value, comment=comment)
        except Exception as exc:
            log.debug("langfuse_score_failed", error=str(exc))

    def update(self, **kwargs: Any) -> None:
        if self._handle is None:
            return
        try:
            self._handle.update(**_clean_kwargs(kwargs))
        except Exception as exc:
            log.debug("langfuse_update_failed", error=str(exc))


def start_trace(
    name: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    input: Any = None,
) -> Trace:
    client = get_client()
    if client is None:
        return Trace(None)
    try:
        handle = client.trace(
            name=name,
            user_id=user_id,
            session_id=session_id,
            tags=tags or [],
            metadata=_clean(metadata or {}),
            input=_clean(input),
        )
        trace_id_ctx.set(handle.id)
        return Trace(handle)
    except Exception as exc:
        log.debug("langfuse_trace_failed", error=str(exc))
        return Trace(None)


@contextmanager
def traced_span(trace: Trace, name: str, **kwargs: Any) -> Iterator[Span]:
    span = trace.span(name, **kwargs)
    try:
        yield span
    except Exception as exc:
        span.end(level="ERROR", status_message=str(exc))
        raise
    else:
        span.end()


def get_prompt(name: str, *, fallback: str, label: str = "production") -> tuple[str, str]:
    """Return (prompt_text, version_label).

    Falls back to the in-repo copy when Langfuse is unreachable, so a
    prompt-service outage degrades to "last known good", not an outage.
    """
    client = get_client()
    if client is None:
        return fallback, "repo-fallback"
    try:
        prompt = client.get_prompt(name, label=label)
        return prompt.prompt, f"{name}:v{prompt.version}"
    except Exception as exc:
        log.warning("langfuse_prompt_fetch_failed", prompt=name, error=str(exc))
        return fallback, "repo-fallback"


def flush() -> None:
    client = get_client()
    if client is not None:
        try:
            client.flush()
        except Exception as exc:
            log.debug("langfuse_flush_failed", error=str(exc))

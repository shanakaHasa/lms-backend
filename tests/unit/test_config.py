"""Configuration guards.

A misconfigured production must fail at startup rather than serve student
records with development defaults. Each assertion corresponds to a real way a
service like this gets deployed insecurely.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

PROD_SAFE = {
    "app_env": "prod",
    "auth_disabled": False,
    "auth_issuer": "https://auth.example.com",
    "db_ssl_mode": "verify-full",
    "storage_backend": "s3",
    "langfuse_mask_pii": True,
    "_env_file": None,
}


def _prod(**overrides: object) -> Settings:
    return Settings(**{**PROD_SAFE, **overrides})  # type: ignore[arg-type]


def test_a_correctly_configured_prod_boots() -> None:
    assert _prod().app_env == "prod"


def test_prod_refuses_to_run_without_authentication() -> None:
    # The single most important guard here: this service holds student records.
    with pytest.raises(ValidationError, match="AUTH_DISABLED"):
        _prod(auth_disabled=True)


def test_prod_refuses_a_plaintext_issuer() -> None:
    with pytest.raises(ValidationError, match="AUTH_ISSUER must be https"):
        _prod(auth_issuer="http://auth.example.com")


def test_prod_refuses_a_localhost_issuer() -> None:
    with pytest.raises(ValidationError, match="localhost"):
        _prod(auth_issuer="https://localhost:8001")


def test_prod_requires_verified_tls_to_the_database() -> None:
    # `require` encrypts but does not authenticate the server.
    with pytest.raises(ValidationError, match="DB_SSL_MODE"):
        _prod(db_ssl_mode="require")


def test_prod_refuses_local_disk_storage() -> None:
    # Local disk is not durable and does not survive a task being replaced.
    with pytest.raises(ValidationError, match="STORAGE_BACKEND"):
        _prod(storage_backend="local")


def test_prod_refuses_unmasked_traces() -> None:
    with pytest.raises(ValidationError, match="LANGFUSE_MASK_PII"):
        _prod(langfuse_mask_pii=False)


def test_all_problems_are_reported_together() -> None:
    # One boot, one message listing everything wrong -- rather than fixing them
    # one failed deploy at a time.
    with pytest.raises(ValidationError) as exc:
        _prod(auth_disabled=True, storage_backend="local", db_ssl_mode="require")
    message = str(exc.value)
    assert "AUTH_DISABLED" in message
    assert "STORAGE_BACKEND" in message
    assert "DB_SSL_MODE" in message


def test_database_url_must_name_the_async_driver() -> None:
    # PostgresDsn would accept this and fail later with an opaque driver error.
    with pytest.raises(ValidationError, match="asyncpg"):
        Settings(database_url="postgresql://a:b@h/db", _env_file=None)


def test_issuer_trailing_slash_is_normalised() -> None:
    # `iss` is compared byte-for-byte against the token claim.
    assert Settings(auth_issuer="http://localhost:8001/", _env_file=None).auth_issuer == (
        "http://localhost:8001"
    )


def test_jwks_uri_is_derived_from_the_issuer() -> None:
    settings = Settings(auth_issuer="https://auth.example.com", _env_file=None)
    assert settings.jwks_uri == "https://auth.example.com/.well-known/jwks.json"


def test_explicit_jwks_url_overrides_the_derived_one() -> None:
    settings = Settings(auth_jwks_url="https://cdn.example.com/keys", _env_file=None)
    assert settings.jwks_uri == "https://cdn.example.com/keys"

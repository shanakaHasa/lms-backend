"""Configuration.

Same shape as auth-backend's: everything from the environment, and validators
that turn a misconfigured production into a startup crash rather than a service
that runs with development defaults.

The difference from auth is what it must refuse: this service holds student
personal information and must never boot in production with authentication
switched off.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "dev", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # -- App ---------------------------------------------------------------
    app_env: AppEnv = "local"
    log_level: str = "INFO"
    api_port: int = 8000
    service_name: str = "backend"

    # -- Database ----------------------------------------------------------
    # A plain str, not PostgresDsn: PostgresDsn accepts "postgresql://", which
    # then fails at connect time with an opaque sync-driver error.
    database_url: str = "postgresql+asyncpg://teachassist:teachassist@localhost:5432/lmsdb"
    test_database_url: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False
    db_ssl_mode: Literal["disable", "require", "verify-full"] = "require"
    db_ca_bundle_path: str | None = None

    # -- Auth (this service VERIFIES tokens; it never issues them) ----------
    # While auth-backend is still being built, a fixed principal stands in.
    # Every route and tool already goes through it, so nothing changes
    # structurally when real tokens arrive.
    auth_disabled: bool = True
    auth_issuer: str = "http://localhost:8001"
    auth_audience: str = "teachassist-api"
    auth_jwks_url: str | None = None
    jwks_cache_ttl_seconds: int = 300
    # Without these two, a token carrying a random `kid` makes this service
    # hammer auth's JWKS endpoint -- a DoS amplifier pointed at our own IdP.
    jwks_unknown_kid_cooldown_seconds: int = 30
    jwks_negative_cache_seconds: int = 60
    clock_skew_leeway_seconds: int = 60

    # -- Storage (local now, S3 later, same interface) ---------------------
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_path: str = "./data/materials"
    s3_bucket: str | None = None

    # -- Job queue (Postgres now, SQS later, same interface) ---------------
    queue_backend: Literal["postgres", "sqs"] = "postgres"
    queue_visibility_timeout_seconds: int = 300
    queue_max_attempts: int = 3
    sqs_queue_url: str | None = None

    # -- LLM (step B6) -----------------------------------------------------
    llm_provider: Literal["openai", "anthropic"] = "openai"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    llm_model: str | None = None
    llm_cheap_model: str | None = None

    # -- Embeddings and vector store (step B5) -----------------------------
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    pinecone_api_key: str | None = None
    # The index name carries the model and dimension because a Pinecone index's
    # dimension is fixed at creation -- changing embedding provider means a new
    # index and a full reindex, not a config flip.
    pinecone_index: str = "teachassist-local-te3s-1536"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"

    # -- Retrieval tuning --------------------------------------------------
    retrieval_top_k: int = 40
    retrieval_final_k: int = 8
    chunk_tokens: int = 700
    chunk_overlap_tokens: int = 100
    rerank_enabled: bool = True

    # -- Observability -----------------------------------------------------
    langfuse_enabled: bool = False
    langfuse_host: str = "http://localhost:3001"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_mask_pii: bool = True

    # -- CORS --------------------------------------------------------------
    cors_origins: str = "http://localhost:3000"

    # ---------------------------------------------------------------------

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _must_name_the_async_driver(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "database URLs must use the postgresql+asyncpg:// driver; "
                f"got {v.split('://', 1)[0]}://"
            )
        return v

    @field_validator("auth_issuer")
    @classmethod
    def _no_trailing_slash(cls, v: str) -> str:
        # `iss` is compared byte-for-byte against the token claim.
        return v.rstrip("/")

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> Settings:
        if self.app_env != "prod":
            return self

        problems: list[str] = []
        # The one that matters most: this service holds student records.
        if self.auth_disabled:
            problems.append("AUTH_DISABLED must be false")
        if not self.auth_issuer.startswith("https://"):
            problems.append("AUTH_ISSUER must be https")
        if "localhost" in self.auth_issuer or "127.0.0.1" in self.auth_issuer:
            problems.append("AUTH_ISSUER must not point at localhost")
        if self.db_ssl_mode != "verify-full":
            problems.append("DB_SSL_MODE must be verify-full")
        if self.storage_backend != "s3":
            problems.append("STORAGE_BACKEND must be s3 (local disk is not durable)")
        if not self.langfuse_mask_pii:
            problems.append("LANGFUSE_MASK_PII must be true")

        if problems:
            raise ValueError("unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def jwks_uri(self) -> str:
        return self.auth_jwks_url or f"{self.auth_issuer}/.well-known/jwks.json"

    @property
    def boto_kwargs(self) -> dict[str, Any]:
        return {}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

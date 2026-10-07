"""Control-plane settings, read from the environment. Nothing here is a default secret.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MIN_SESSION_SECRET = 32


class ConfigError(ValueError):
    """The configuration is unusable or unsafe."""


@dataclass(frozen=True)
class Settings:
    """Everything the control plane needs from its environment."""

    database_url: str
    session_secret: str
    storage_root: Path
    env: Literal["dev", "test", "production"] = "dev"
    kms: Literal["local", "aws"] = "local"
    #: base64 of 32 random bytes; the dev-only master key for the local KMS provider
    local_kms_key: str | None = field(default=None, repr=False)
    aws_kms_key_id: str | None = None
    stripe_secret_key: str | None = field(default=None, repr=False)
    stripe_webhook_secret: str | None = field(default=None, repr=False)
    public_url: str = "http://localhost:8080"
    dashboard_url: str = "http://localhost:3000"
    #: authenticates platform operators (Tycheon staff); the operator API is off when unset
    operator_token: str | None = field(default=None, repr=False)
    session_ttl_seconds: int = 8 * 3600
    max_upload_bytes: int = 20_000_000

    def __post_init__(self) -> None:
        if len(self.session_secret) < MIN_SESSION_SECRET:
            raise ConfigError(
                f"the session secret must be at least {MIN_SESSION_SECRET} characters"
            )
        if self.operator_token is not None and len(self.operator_token) < MIN_SESSION_SECRET:
            raise ConfigError("the operator token must be at least 32 characters")
        if self.kms == "local":
            if self.env == "production":
                raise ConfigError("the local KMS provider is for development only")
            if not self.local_kms_key:
                raise ConfigError("the local KMS provider needs TYCHEON_CP_LOCAL_KMS_KEY")
            try:
                key = base64.b64decode(self.local_kms_key, validate=True)
            except ValueError as exc:
                raise ConfigError("TYCHEON_CP_LOCAL_KMS_KEY must be base64 of 32 bytes") from exc
            if len(key) != 32:
                raise ConfigError("TYCHEON_CP_LOCAL_KMS_KEY must be base64 of 32 bytes")
        elif not self.aws_kms_key_id:
            raise ConfigError("the AWS KMS provider needs TYCHEON_CP_AWS_KMS_KEY_ID")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if environ is None else environ)

        def need(name: str) -> str:
            value = env.get(name)
            if not value:
                raise ConfigError(f"{name} is required")
            return value

        mode = env.get("TYCHEON_CP_ENV", "dev")
        kms = env.get("TYCHEON_CP_KMS", "local")
        if mode not in ("dev", "test", "production") or kms not in ("local", "aws"):
            raise ConfigError("TYCHEON_CP_ENV or TYCHEON_CP_KMS has an unknown value")
        return cls(
            database_url=need("TYCHEON_CP_DATABASE_URL"),
            session_secret=need("TYCHEON_CP_SESSION_SECRET"),
            storage_root=Path(env.get("TYCHEON_CP_STORAGE_ROOT", "./.tycheon-cloud")),
            env=mode,  # type: ignore[arg-type]
            kms=kms,  # type: ignore[arg-type]
            local_kms_key=env.get("TYCHEON_CP_LOCAL_KMS_KEY"),
            aws_kms_key_id=env.get("TYCHEON_CP_AWS_KMS_KEY_ID"),
            stripe_secret_key=env.get("STRIPE_SECRET_KEY"),
            stripe_webhook_secret=env.get("STRIPE_WEBHOOK_SECRET"),
            public_url=env.get("TYCHEON_CP_PUBLIC_URL", "http://localhost:8080"),
            dashboard_url=env.get("TYCHEON_CP_DASHBOARD_URL", "http://localhost:3000"),
            operator_token=env.get("TYCHEON_CP_OPERATOR_TOKEN"),
        )

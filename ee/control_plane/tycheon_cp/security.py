"""Passwords, API keys and session tokens.

* Passwords use scrypt from the standard library (memory-hard, no extra dependency).
* An API key is ``tyk_<prefix>_<secret>``. The prefix is a public lookup handle; the secret is 256
  random bits and only its SHA-256 is stored, so a database leak does not yield usable keys. High
  entropy is what makes an unsalted fast hash acceptable here (it is not for passwords).
* Session tokens are short-lived HS256 JWTs carrying only the user, org and role.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Literal

import jwt

Role = Literal["owner", "admin", "member", "viewer"]
ROLE_RANK: dict[str, int] = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
SCOPES = ("analytics", "mcp", "admin")

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
_KEY = re.compile(r"^tyk_([A-Za-z0-9]{12})_([A-Za-z0-9_-]{43})$")
MIN_PASSWORD = 12
MAX_PASSWORD = 256
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s]{2,}$")


class AuthError(Exception):
    """Authentication or authorisation failed. The message is safe to show a caller."""


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if len(value) > 254 or not _EMAIL.fullmatch(value):
        raise AuthError("that email address is not valid")
    return value


def hash_password(password: str) -> str:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AuthError(f"a password must be {MIN_PASSWORD} to {MAX_PASSWORD} characters")
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


# Verified against when the user does not exist, so login time does not reveal valid emails.
_DUMMY = hash_password("not-a-real-password-for-timing")


def verify_password(password: str, stored: str | None) -> bool:
    candidate = stored or _DUMMY
    try:
        scheme, salt_b64, digest_b64 = candidate.split("$")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        if scheme != "scrypt" or len(password) > MAX_PASSWORD:
            return False
        actual = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected) and stored is not None


@dataclass(frozen=True)
class NewApiKey:
    """A freshly minted key. ``secret`` is shown to the user once and never stored."""

    prefix: str
    secret: str
    token: str
    secret_hash: str


def hash_key_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def new_api_key() -> NewApiKey:
    prefix = "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(12))
    secret = secrets.token_urlsafe(32)
    return NewApiKey(prefix, secret, f"tyk_{prefix}_{secret}", hash_key_secret(secret))


def parse_api_key(token: str | None) -> tuple[str, str] | None:
    """``(prefix, secret)`` from a well-formed key, else ``None`` (never raises)."""
    match = _KEY.fullmatch(token or "")
    return (match.group(1), match.group(2)) if match else None


def key_matches(secret: str, stored_hash: str | None) -> bool:
    """Constant-time check; a missing key is compared against a dummy so timing is uniform."""
    expected = stored_hash or hash_key_secret("no-such-key")
    return hmac.compare_digest(hash_key_secret(secret), expected) and stored_hash is not None


@dataclass(frozen=True)
class Session:
    user_id: str
    org_id: str
    role: Role


def issue_session(secret: str, session: Session, *, ttl_seconds: int) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": session.user_id,
        "org": session.org_id,
        "role": session.role,
        "iat": now,
        "exp": now + ttl_seconds,
        "aud": "tycheon-cp",
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def read_session(secret: str, token: str | None) -> Session:
    if not token:
        raise AuthError("sign in to continue")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="tycheon-cp",
            options={"require": ["exp", "sub", "org", "role"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("your session is not valid; sign in again") from exc
    if claims["role"] not in ROLE_RANK:
        raise AuthError("your session is not valid; sign in again")
    return Session(str(claims["sub"]), str(claims["org"]), claims["role"])


def require_role(session: Session, minimum: Role) -> None:
    if ROLE_RANK[session.role] < ROLE_RANK[minimum]:
        raise AuthError("you do not have permission to do that")

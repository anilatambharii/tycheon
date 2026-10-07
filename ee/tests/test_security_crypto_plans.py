"""Passwords, API keys, sessions, envelope encryption, plans, rate limits, config. No database.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import base64
import os
import time

import pytest

from tycheon_cp.config import ConfigError, Settings
from tycheon_cp.crypto import AwsKms, CryptoError, LocalKms, SealedSecret, open_sealed, seal
from tycheon_cp.plans import PlanError, load_plans, parse_plans
from tycheon_cp.ratelimit import RateLimitedError, RateLimiter
from tycheon_cp.security import (
    AuthError,
    Session,
    hash_password,
    issue_session,
    key_matches,
    new_api_key,
    normalize_email,
    parse_api_key,
    read_session,
    require_role,
    verify_password,
)

pytestmark = pytest.mark.anyio


# --------------------------------------------------------------------------- passwords
def test_a_password_verifies_and_a_wrong_one_does_not() -> None:
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterx", stored)
    assert stored != hash_password("correct horse battery")  # salted


def test_a_missing_account_never_verifies_but_still_does_the_work() -> None:
    assert verify_password("whatever-password", None) is False


@pytest.mark.parametrize("bad", ["short", "x" * 300])
def test_weak_or_absurd_passwords_are_refused(bad) -> None:
    with pytest.raises(AuthError):
        hash_password(bad)


def test_a_malformed_stored_hash_fails_closed() -> None:
    for junk in ("", "scrypt$$", "md5$aa$bb", "not-a-hash", "scrypt$%%%$%%%"):
        assert verify_password("anything-long-enough", junk) is False


def test_emails_are_normalised_and_validated() -> None:
    assert normalize_email("  Ann@Example.COM ") == "ann@example.com"
    for bad in ("nope", "a@b", "a b@c.de", "@x.com", "a@" + "b" * 300 + ".com"):
        with pytest.raises(AuthError):
            normalize_email(bad)


# ---------------------------------------------------------------------------- API keys
def test_an_api_key_round_trips_and_only_its_hash_is_kept() -> None:
    key = new_api_key()
    parsed = parse_api_key(key.token)
    assert parsed == (key.prefix, key.secret)
    assert key.secret not in key.secret_hash and len(key.secret_hash) == 64
    assert key_matches(key.secret, key.secret_hash)
    assert not key_matches(new_api_key().secret, key.secret_hash)
    assert not key_matches(key.secret, None)


def test_keys_are_unique_and_long() -> None:
    tokens = {new_api_key().token for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(t) >= 60 for t in tokens)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "tyk_",
        "tyk_short_x",
        "xyz_aaaaaaaaaaaa_" + "a" * 43,
        "tyk_aaaaaaaaaaaa_" + "a" * 42,
        "tyk_aaaaaaaaaaaa_" + "a" * 44,
        "tyk_aaaa aaaaaaa_" + "a" * 43,
        "tyk_aaaaaaaaaaaa_" + "a" * 42 + "!",
    ],
)
def test_malformed_keys_never_parse(bad) -> None:
    assert parse_api_key(bad) is None


# --------------------------------------------------------------------------- sessions
SECRET = "s" * 40


def test_a_session_round_trips() -> None:
    token = issue_session(SECRET, Session("u1", "o1", "admin"), ttl_seconds=60)
    assert read_session(SECRET, token) == Session("u1", "o1", "admin")


def test_sessions_reject_tampering_expiry_and_a_different_secret() -> None:
    token = issue_session(SECRET, Session("u1", "o1", "owner"), ttl_seconds=60)
    with pytest.raises(AuthError):
        read_session("t" * 40, token)
    with pytest.raises(AuthError):
        read_session(SECRET, token[:-3] + "AAA")
    expired = issue_session(SECRET, Session("u1", "o1", "owner"), ttl_seconds=-5)
    with pytest.raises(AuthError):
        read_session(SECRET, expired)
    with pytest.raises(AuthError):
        read_session(SECRET, None)


def test_a_token_with_no_signature_algorithm_is_refused() -> None:
    import jwt

    forged = jwt.encode(
        {
            "sub": "u",
            "org": "o",
            "role": "owner",
            "aud": "tycheon-cp",
            "exp": int(time.time()) + 60,
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(AuthError):
        read_session(SECRET, forged)


def test_roles_are_ordered() -> None:
    owner, viewer = Session("u", "o", "owner"), Session("u", "o", "viewer")
    require_role(owner, "admin")
    require_role(viewer, "viewer")
    with pytest.raises(AuthError):
        require_role(viewer, "member")


# ------------------------------------------------------------------ envelope encryption
MASTER = base64.b64encode(os.urandom(32)).decode()
OTHER_MASTER = base64.b64encode(os.urandom(32)).decode()


async def test_a_secret_round_trips_and_is_not_stored_in_the_clear() -> None:
    kms = LocalKms(MASTER)
    sealed = await seal(kms, b"vendor-api-key-123", org_id="o1", credential_id="c1")
    assert b"vendor-api-key-123" not in sealed.ciphertext + sealed.wrapped_dek + sealed.nonce
    assert await open_sealed(kms, sealed, org_id="o1", credential_id="c1") == b"vendor-api-key-123"


async def test_each_secret_gets_its_own_data_key() -> None:
    kms = LocalKms(MASTER)
    a = await seal(kms, b"same", org_id="o1", credential_id="c1")
    b = await seal(kms, b"same", org_id="o1", credential_id="c1")
    assert a.wrapped_dek != b.wrapped_dek and a.ciphertext != b.ciphertext


async def test_a_secret_cannot_be_opened_for_another_tenant_or_credential() -> None:
    kms = LocalKms(MASTER)
    sealed = await seal(kms, b"secret", org_id="org-a", credential_id="c1")
    for org, cred, purpose in (
        ("org-b", "c1", "credential"),  # another tenant
        ("org-a", "c2", "credential"),  # another credential
        ("org-a", "c1", "oidc"),  # another purpose
    ):
        with pytest.raises(CryptoError):
            await open_sealed(kms, sealed, org_id=org, credential_id=cred, purpose=purpose)


async def test_a_swapped_ciphertext_does_not_open_even_with_a_valid_wrapped_key() -> None:
    kms = LocalKms(MASTER)
    a = await seal(kms, b"alpha", org_id="o1", credential_id="c1")
    b = await seal(kms, b"beta", org_id="o1", credential_id="c1")
    franken = SealedSecret(a.kms_key_id, a.wrapped_dek, a.nonce, b.ciphertext)
    with pytest.raises(CryptoError):
        await open_sealed(kms, franken, org_id="o1", credential_id="c1")


async def test_tampered_or_foreign_key_material_fails_closed() -> None:
    kms = LocalKms(MASTER)
    sealed = await seal(kms, b"secret", org_id="o1", credential_id="c1")
    flipped = bytearray(sealed.ciphertext)
    flipped[0] ^= 1
    with pytest.raises(CryptoError):
        await open_sealed(
            kms,
            SealedSecret(sealed.kms_key_id, sealed.wrapped_dek, sealed.nonce, bytes(flipped)),
            org_id="o1",
            credential_id="c1",
        )
    with pytest.raises(CryptoError):
        await open_sealed(LocalKms(OTHER_MASTER), sealed, org_id="o1", credential_id="c1")


def test_the_local_master_key_must_be_32_bytes() -> None:
    with pytest.raises(CryptoError):
        LocalKms(base64.b64encode(b"short").decode())


class FakeKmsClient:
    """Stands in for boto3's KMS client: records the encryption context it is given."""

    def __init__(self) -> None:
        self.contexts: list[dict[str, str]] = []

    def encrypt(self, **kw):
        self.contexts.append(kw["EncryptionContext"])
        return {"CiphertextBlob": b"wrapped:" + kw["Plaintext"]}

    def decrypt(self, **kw):
        if kw["EncryptionContext"] not in self.contexts:
            raise RuntimeError("InvalidCiphertextException with secrets in the message")
        return {"Plaintext": kw["CiphertextBlob"].removeprefix(b"wrapped:")}


async def test_aws_kms_binds_the_context_and_never_leaks_its_errors() -> None:
    client = FakeKmsClient()
    kms = AwsKms("arn:aws:kms:eu-west-1:000000000000:key/test", client=client)
    sealed = await seal(kms, b"secret", org_id="o1", credential_id="c1")
    assert await open_sealed(kms, sealed, org_id="o1", credential_id="c1") == b"secret"
    with pytest.raises(CryptoError) as raised:
        await open_sealed(kms, sealed, org_id="o2", credential_id="c1")
    assert "secrets in the message" not in str(raised.value)


# ----------------------------------------------------------------------------- plans
def test_the_shipped_plans_match_the_product_brief() -> None:
    plans = load_plans()
    dev, startup, ent = plans.get("developer"), plans.get("startup"), plans.get("enterprise")
    assert dev.limit("forecast_calls") == 1000 and dev.price is None and dev.research_use_only
    assert dev.data_frequencies == ("1D",) and not dev.has("mcp")
    assert startup.limit("forecast_calls") == 1_000_000
    assert startup.price is not None and startup.price.amount_cents == 100_000
    assert (
        startup.price.interval == "month"
        and startup.has("mcp")
        and startup.has("calibration_reports")
    )
    assert ent.has("finetune") and ent.has("dedicated_gpu") and ent.has("private_deployment")
    assert not startup.has("finetune")


def test_an_unknown_plan_or_meter_is_an_error_not_a_default() -> None:
    plans = load_plans()
    with pytest.raises(PlanError):
        plans.get("platinum")
    assert plans.get("developer").limit("not_a_meter") == 0
    with pytest.raises(PlanError):
        parse_plans(
            {
                "meters": {"a": "x"},
                "plans": {
                    "developer": {
                        "name": "d",
                        "rate_limit_per_minute": 1,
                        "retention_days": 1,
                        "limits": {"b": 1},
                    }
                },
            }
        )
    with pytest.raises(PlanError):
        parse_plans({"meters": {"a": "x"}, "plans": {}})


def test_overrides_must_be_non_negative_integers() -> None:
    dev = load_plans().get("developer")
    assert dev.limit("forecast_calls", {"forecast_calls": 5}) == 5
    for bad in (-1, 1.5, "9", True):
        with pytest.raises(PlanError):
            dev.limit("forecast_calls", {"forecast_calls": bad})


# ------------------------------------------------------------------------ rate limits
def test_the_bucket_allows_a_burst_then_refuses_then_refills() -> None:
    now = [0.0]
    limiter = RateLimiter(clock=lambda: now[0])
    for _ in range(6):
        limiter.allow("org", 6)
    with pytest.raises(RateLimitedError) as raised:
        limiter.allow("org", 6)
    assert 0 < raised.value.retry_after <= 10.5
    now[0] += 10.0  # 6/min = 1 token per 10 s
    limiter.allow("org", 6)
    with pytest.raises(RateLimitedError):
        limiter.allow("org", 6)


def test_buckets_are_per_key_and_a_zero_limit_refuses_everything() -> None:
    limiter = RateLimiter(clock=lambda: 0.0)
    limiter.allow("a", 1)
    limiter.allow("b", 1)
    with pytest.raises(RateLimitedError):
        limiter.allow("a", 1)
    with pytest.raises(RateLimitedError):
        limiter.allow("c", 0)


# ------------------------------------------------------------------------------ config
def _env(**over):
    base = {
        "TYCHEON_CP_DATABASE_URL": "postgresql://x",
        "TYCHEON_CP_SESSION_SECRET": "s" * 40,
        "TYCHEON_CP_LOCAL_KMS_KEY": MASTER,
    }
    base.update(over)
    return base


def test_settings_load_and_hide_secrets_in_repr() -> None:
    s = Settings.from_env(_env(STRIPE_SECRET_KEY="sk_test_abc"))
    assert s.env == "dev" and s.kms == "local"
    assert "sk_test_abc" not in repr(s) and MASTER not in repr(s)


@pytest.mark.parametrize(
    "over",
    [
        {"TYCHEON_CP_SESSION_SECRET": "short"},  # pragma: allowlist secret
        {"TYCHEON_CP_ENV": "production"},  # local KMS in production
        {"TYCHEON_CP_LOCAL_KMS_KEY": "not base64!!"},
        {"TYCHEON_CP_LOCAL_KMS_KEY": base64.b64encode(b"x" * 16).decode()},
        {"TYCHEON_CP_KMS": "aws"},  # no key id
        {"TYCHEON_CP_KMS": "gcp"},
        {"TYCHEON_CP_DATABASE_URL": ""},
    ],
)
def test_unsafe_settings_are_refused(over) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(_env(**over))

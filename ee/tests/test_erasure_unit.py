"""Erasure pieces that need no database: receipts, the tombstone log, the Stripe calls, the cache.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import hashlib
import time
import types
from pathlib import Path

import pytest

from tycheon_cp import erasure
from tycheon_cp.billing import RealStripe
from tycheon_cp.config import ConfigError, Settings
from tycheon_cp.errors import ApiProblem
from tycheon_cp.tombstones import Tombstone, Tombstones

KEY = "sk_test_x"  # pragma: allowlist secret


def settings(**over) -> Settings:
    base = {
        "database_url": "postgresql://x",
        "session_secret": "s" * 40,
        "storage_root": Path("/tmp/x"),  # noqa: S108
        "env": "test",
        "local_kms_key": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    }
    return Settings(**{**base, **over})


# ----------------------------------------------------------------------------- receipts
def test_the_slug_hash_is_keyed_so_a_list_of_company_names_cannot_reverse_it() -> None:
    plain = hashlib.sha256(b"acme-capital").hexdigest()
    a = erasure.slug_hmac(settings(), "acme-capital")
    assert a != plain and len(a) == 64
    assert a == erasure.slug_hmac(settings(), "acme-capital")
    assert a != erasure.slug_hmac(settings(session_secret="t" * 40), "acme-capital")
    assert a != erasure.slug_hmac(settings(erasure_receipt_key="k" * 32), "acme-capital")


def test_the_grace_period_is_bounded() -> None:
    assert settings(erasure_grace_days=0).erasure_grace_days == 0
    for bad in (-1, 91):
        with pytest.raises(ConfigError):
            settings(erasure_grace_days=bad)


def test_the_tombstone_directory_defaults_inside_the_storage_root_but_outside_any_org() -> None:
    found = erasure.tombstone_dir(settings())
    assert found == Path("/tmp/x") / "_erasure-log"  # noqa: S108
    assert erasure.tombstone_dir(settings(erasure_log_dir=Path("/mnt/worm"))) == Path("/mnt/worm")


# --------------------------------------------------------------------------- tombstones
def record(**over) -> Tombstone:
    base = {
        "receipt_id": "11111111-1111-1111-1111-111111111111",
        "org_id": "22222222-2222-2222-2222-222222222222",
        "slug_hmac": "ab" * 32,
        "requested_by": None,
        "actor_role": "owner",
        "requested_at": "2026-10-01T00:00:00+00:00",
        "erased_at": "2026-10-15T00:00:00+00:00",
    }
    return Tombstone(**{**base, **over})


def test_a_tombstone_is_written_once_and_never_rewritten(tmp_path) -> None:
    log = Tombstones(tmp_path / "log")
    first = log.write(record())
    before = first.read_text(encoding="utf-8")
    log.write(record(actor_role="admin"))  # same receipt id: ignored
    assert first.read_text(encoding="utf-8") == before
    assert [t.receipt_id for t in log.all()] == ["11111111-1111-1111-1111-111111111111"]
    assert not list((tmp_path / "log").glob("*.tmp"))


def test_a_tombstone_with_a_malformed_id_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="receipt_id"):
        Tombstone.from_dict({**record().__dict__, "receipt_id": "../../etc/passwd"})


# ------------------------------------------------------------------------------ Stripe
class StripeError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SubscriptionsApi:
    def __init__(self, status: str = "active", error: Exception | None = None) -> None:
        self.status, self.error = status, error
        self.cancelled: list[tuple[str, dict]] = []

    def retrieve(self, sub_id):
        if self.error:
            raise self.error
        return types.SimpleNamespace(status=self.status)

    def cancel(self, sub_id, params=None):
        self.cancelled.append((sub_id, params))


class CustomersApi:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.updates = error, []

    def update(self, customer, params):
        if self.error:
            raise self.error
        self.updates.append((customer, params))


def stripe_with(subs=None, customers=None) -> RealStripe:
    v1 = types.SimpleNamespace(
        subscriptions=subs or SubscriptionsApi(), customers=customers or CustomersApi()
    )
    return RealStripe(KEY, client=types.SimpleNamespace(v1=v1))


def test_cancel_bills_the_final_usage_without_proration() -> None:
    subs = SubscriptionsApi()
    stripe_with(subs).cancel_subscription("sub_1")
    assert subs.cancelled == [("sub_1", {"invoice_now": True, "prorate": False})]


def test_cancelling_twice_is_a_no_op_so_a_retry_can_finish() -> None:
    subs = SubscriptionsApi(status="canceled")
    stripe_with(subs).cancel_subscription("sub_1")
    assert subs.cancelled == []
    gone = SubscriptionsApi(error=StripeError("resource_missing"))
    stripe_with(gone).cancel_subscription("sub_1")  # already deleted: also done


def test_a_real_stripe_failure_is_not_swallowed() -> None:
    with pytest.raises(StripeError):
        stripe_with(
            SubscriptionsApi(error=StripeError("api_connection_error"))
        ).cancel_subscription("s")


def test_redaction_clears_the_contact_details_and_our_org_id() -> None:
    customers = CustomersApi()
    stripe_with(customers=customers).redact_customer("cus_1")
    customer, params = customers.updates[0]
    assert customer == "cus_1"
    assert params["name"] == "" and params["email"] == "" and params["phone"] == ""
    assert params["metadata"] == {"tycheon_org_id": ""}
    stripe_with(customers=CustomersApi(error=StripeError("resource_missing"))).redact_customer("c")
    with pytest.raises(StripeError):
        stripe_with(customers=CustomersApi(error=StripeError("rate_limit"))).redact_customer("c")


# ------------------------------------------------------------------------ the model cache
@pytest.mark.anyio
async def test_a_cached_model_is_dropped_once_the_database_no_longer_allows_it(monkeypatch) -> None:
    from uuid import UUID

    from tycheon_cp.bridge import ModelUnavailableError
    from tycheon_ft import loader, registry

    org, model = UUID(int=1), UUID(int=2)
    key = (str(org), str(model))
    resolver = loader.PrivateModelResolver(None, None, None, revalidate_seconds=0.0)  # type: ignore[arg-type]
    resolver._cache[key] = object()  # type: ignore[assignment]
    resolver._validated[key] = time.monotonic() - 60

    async def gone(*_a, **_k):
        raise ApiProblem(404, "not_found", "no")

    monkeypatch.setattr(registry, "get_model", gone)
    with pytest.raises(ModelUnavailableError):
        await resolver._load(org, model)
    assert key not in resolver._cache and key not in resolver._validated


@pytest.mark.anyio
async def test_a_cached_model_that_was_archived_is_no_longer_served(monkeypatch) -> None:
    from uuid import UUID

    from tycheon_cp.bridge import ModelUnavailableError
    from tycheon_ft import loader, registry

    org, model = UUID(int=1), UUID(int=2)
    key = (str(org), str(model))
    resolver = loader.PrivateModelResolver(None, None, None, revalidate_seconds=0.0)  # type: ignore[arg-type]
    resolver._cache[key] = object()  # type: ignore[assignment]
    resolver._validated[key] = time.monotonic() - 60

    async def archived(*_a, **_k):
        return {"status": "archived"}

    monkeypatch.setattr(registry, "get_model", archived)
    with pytest.raises(ModelUnavailableError, match="archived"):
        await resolver._load(org, model)
    assert key not in resolver._cache


def test_evicting_an_organisation_drops_all_of_its_cached_models() -> None:
    from tycheon_ft import loader

    resolver = loader.PrivateModelResolver(None, None, None)  # type: ignore[arg-type]
    resolver._cache[("org-1", "a")] = object()  # type: ignore[assignment]
    resolver._cache[("org-1", "b")] = object()  # type: ignore[assignment]
    resolver._cache[("org-2", "a")] = object()  # type: ignore[assignment]
    assert resolver.evict_org("org-1") == 2
    assert list(resolver._cache) == [("org-2", "a")]

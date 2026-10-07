"""Stripe billing: test-mode enforcement, catalogue, checkout, webhooks, usage reporting, operators.

The webhook events below are hand-written to the shape of Stripe's documented event objects (they
are not recordings from Stripe) and are signed with the real Stripe signature scheme, so the
verification path under test is the SDK's own.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import pytest

from tycheon_cp.billing import BillingError, RealStripe, require_test_key
from tycheon_cp.plans import load_plans

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]

SECRET = "whsec_test_only_not_a_real_secret_0000"  # pragma: allowlist secret
OPERATOR = {"Authorization": "Bearer operator-token-for-tests-0123456789abcdef"}
FORECAST = {"symbol": "SYN-GBM", "horizon": 3, "n_samples": 80, "n_origins": 30}


# ------------------------------------------------------------------------- test mode only
@pytest.mark.parametrize(
    "key", [None, "", "sk_live_abc", "rk_live_abc", "pk_test_abc", "whsec_x", "sk_prod_1"]
)
def test_live_or_missing_keys_are_refused(key) -> None:
    with pytest.raises(BillingError):
        require_test_key(key)
    with pytest.raises(BillingError):
        RealStripe(key)


def test_test_keys_are_accepted() -> None:
    assert require_test_key("sk_test_abc") == "sk_test_abc"
    assert require_test_key("rk_test_abc") == "rk_test_abc"


# --------------------------------------------------------------- catalogue from config
class FakeSdk:
    """Just enough of ``stripe.StripeClient`` to watch the catalogue being created."""

    def __init__(self, existing_prices=(), existing_meters=()) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.existing_prices = list(existing_prices)
        self.existing_meters = list(existing_meters)

        def record(name, result=None):
            def call(params=None):
                self.calls.append((name, params or {}))
                return result(params) if callable(result) else result

            return call

        self.v1 = SimpleNamespace(
            prices=SimpleNamespace(
                list=lambda params: SimpleNamespace(
                    data=[
                        SimpleNamespace(id=f"price_{k}")
                        for k in self.existing_prices
                        if k in params["lookup_keys"]
                    ]
                ),
                create=record("price.create", lambda p: SimpleNamespace(id="price_new")),
            ),
            products=SimpleNamespace(
                create=record("product.create", SimpleNamespace(id="prod_new"))
            ),
            billing=SimpleNamespace(
                meters=SimpleNamespace(
                    list=lambda params: SimpleNamespace(
                        data=[SimpleNamespace(event_name=e) for e in self.existing_meters]
                    ),
                    create=record("meter.create", SimpleNamespace(id="mtr")),
                )
            ),
        )

    def made(self, name):
        return [p for n, p in self.calls if n == name]


def test_the_catalogue_is_created_from_the_plans_file() -> None:
    sdk = FakeSdk()
    prices = RealStripe("sk_test_x", client=sdk).ensure_catalog(load_plans())
    assert prices == {"tycheon_startup_monthly": "price_new"}
    (price,) = sdk.made("price.create")
    assert price["unit_amount"] == 100_000 and price["currency"] == "usd"
    assert (
        price["recurring"] == {"interval": "month"}
        and price["lookup_key"] == "tycheon_startup_monthly"
    )
    events = {m["event_name"] for m in sdk.made("meter.create")}
    assert events == {f"tycheon_{k}" for k in load_plans().meters}
    assert not any(
        "developer" in json.dumps(p) for p in sdk.made("product.create")
    )  # the free plan has no price


def test_creating_the_catalogue_twice_creates_nothing_the_second_time() -> None:
    plans = load_plans()
    sdk = FakeSdk(
        existing_prices=["tycheon_startup_monthly"],
        existing_meters=[f"tycheon_{k}" for k in plans.meters],
    )
    prices = RealStripe("sk_test_x", client=sdk).ensure_catalog(plans)
    assert prices == {"tycheon_startup_monthly": "price_tycheon_startup_monthly"}
    assert sdk.calls == []


# --------------------------------------------------------------------------- checkout
async def test_the_owner_starts_a_checkout_for_a_trial_of_the_startup_plan(
    client, signup_org, fake_stripe
) -> None:
    a = await signup_org()
    res = await client.post("/v1/billing/checkout", json={"plan": "startup"}, headers=a.session)
    assert res.status_code == 200 and res.json()["url"].startswith("https://checkout.stripe.test/")
    sent = fake_stripe.checkouts[0]
    assert sent["price_id"] == "price_test_startup" and sent["trial_days"] == 14
    assert sent["org_id"] == a.org_id and fake_stripe.customers[0]["email"] == a.email
    again = await client.post("/v1/billing/checkout", json={"plan": "startup"}, headers=a.session)
    assert again.status_code == 200 and len(fake_stripe.customers) == 1  # one customer per org


@pytest.mark.parametrize("plan", ["developer", "enterprise", "platinum"])
async def test_only_self_serve_plans_can_be_bought(client, signup_org, plan) -> None:
    a = await signup_org()
    res = await client.post("/v1/billing/checkout", json={"plan": plan}, headers=a.session)
    assert res.status_code == 422


async def test_billing_actions_need_the_owner(client, signup_org, cp) -> None:
    from tycheon_cp.security import Session, issue_session

    a = await signup_org()
    admin = {
        "Authorization": "Bearer "
        + issue_session(cp.session_secret, Session(a.user_id, a.org_id, "admin"), ttl_seconds=60)
    }
    assert (
        await client.post("/v1/billing/checkout", json={"plan": "startup"}, headers=admin)
    ).status_code == 403
    assert (await client.post("/v1/billing/portal", headers=admin)).status_code == 403
    assert (await client.get("/v1/billing/preview", headers=admin)).status_code == 200


async def test_the_portal_and_preview_work_for_an_org_with_no_subscription_yet(
    client, signup_org
) -> None:
    a = await signup_org()
    portal = await client.post("/v1/billing/portal", headers=a.session)
    assert portal.status_code == 200 and portal.json()["url"].startswith(
        "https://billing.stripe.test/"
    )
    preview = (await client.get("/v1/billing/preview", headers=a.session)).json()
    assert preview["total_cents"] == 100_000 and preview["for"]["price"] == "price_test_startup"


async def test_an_api_key_cannot_touch_billing(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key()
    for method, url in (
        ("POST", "/v1/billing/checkout"),
        ("POST", "/v1/billing/portal"),
        ("GET", "/v1/billing/preview"),
    ):
        res = await client.request(
            method, url, json={"plan": "startup"} if method == "POST" else None, headers=a.key
        )
        assert res.status_code == 403


# ----------------------------------------------------------------------------- webhooks
def signed(
    body: dict, *, secret: str = SECRET, at: int | None = None
) -> tuple[bytes, dict[str, str]]:
    payload = json.dumps(body).encode()
    stamp = int(time.time()) if at is None else at
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return payload, {
        "Stripe-Signature": f"t={stamp},v1={digest}",
        "Content-Type": "application/json",
    }


_counter = iter(range(10_000))


def event(kind: str, obj: dict, *, created: int | None = None, id_: str | None = None) -> dict:
    return {
        "id": id_ or f"evt_test_{next(_counter)}_{time.time_ns()}",
        "object": "event",
        "type": kind,
        "created": created or int(time.time()),
        "data": {"object": obj},
    }


def subscription(
    customer: str,
    *,
    status="active",
    lookup_key="tycheon_startup_monthly",
    sub_id=None,
    trial_end=None,
    metadata=None,
) -> dict:
    return {
        "id": sub_id or f"sub_{customer}",
        "object": "subscription",
        "customer": customer,
        "status": status,
        "trial_end": trial_end,
        "cancel_at_period_end": False,
        "metadata": metadata or {},
        "items": {
            "data": [
                {
                    "price": {"id": "price_x", "lookup_key": lookup_key},
                    "current_period_end": 1_900_000_000,
                }
            ]
        },
    }


async def post_event(client, body, **kw):
    payload, headers = signed(body, **kw)
    return await client.post("/v1/billing/webhook", content=payload, headers=headers)


async def with_customer(client, signup_org):
    """An org that already has a Stripe customer (created through checkout)."""
    a = await signup_org()
    await client.post("/v1/billing/checkout", json={"plan": "startup"}, headers=a.session)
    status = (await client.get("/v1/billing/status", headers=a.session)).json()
    assert status["has_customer"] is True
    return a


async def customer_id(owner, org) -> str:
    return await owner.fetchval(
        "SELECT stripe_customer_id FROM subscriptions WHERE org_id = $1::uuid", org.org_id
    )


async def plan_of(client, org) -> dict:
    return (await client.get("/v1/me", headers=org.session)).json()["plan"]


async def test_a_bad_signature_is_rejected_and_changes_nothing(client, signup_org, owner) -> None:
    a = await with_customer(client, signup_org)
    cus = await customer_id(owner, a)
    body = event("customer.subscription.created", subscription(cus))
    forged, headers = signed(body, secret="whsec_wrong_secret")
    assert (
        await client.post("/v1/billing/webhook", content=forged, headers=headers)
    ).status_code == 400
    assert (
        await client.post("/v1/billing/webhook", content=forged)
    ).status_code == 400  # no header
    payload, good = signed(body)
    tampered = payload.replace(b"active", b"trialing")
    assert (
        await client.post("/v1/billing/webhook", content=tampered, headers=good)
    ).status_code == 400
    assert (await plan_of(client, a))["key"] == "developer"


async def test_a_replayed_old_signature_is_rejected(client, signup_org, owner) -> None:
    a = await with_customer(client, signup_org)
    body = event("customer.subscription.created", subscription(await customer_id(owner, a)))
    old = int(time.time()) - 3600
    assert (await post_event(client, body, at=old)).status_code == 400


async def test_a_subscription_event_upgrades_the_plan_and_unlocks_features(
    client, signup_org, owner
) -> None:
    a = await with_customer(client, signup_org)
    cus = await customer_id(owner, a)
    trial_end = int(time.time()) + 14 * 86400
    res = await post_event(
        client,
        event(
            "customer.subscription.created",
            subscription(cus, status="trialing", trial_end=trial_end),
        ),
    )
    assert res.status_code == 200 and res.json()["result"] == "applied"
    plan = await plan_of(client, a)
    assert plan["key"] == "startup" and plan["status"] == "trialing" and "mcp" in plan["features"]
    status = (await client.get("/v1/billing/status", headers=a.session)).json()
    assert status["plan"] == "startup" and status["trial_end"] is not None
    assert await owner.fetchval("SELECT plan FROM orgs WHERE id = $1::uuid", a.org_id) == "startup"


async def test_the_plan_comes_from_the_price_not_from_metadata(client, signup_org, owner) -> None:
    a = await with_customer(client, signup_org)
    cus = await customer_id(owner, a)
    sneaky = subscription(cus, metadata={"tycheon_plan": "enterprise"})
    await post_event(client, event("customer.subscription.created", sneaky))
    assert (await plan_of(client, a))["key"] == "startup"
    unknown_price = subscription(cus, lookup_key="some_other_price")
    res = await post_event(client, event("customer.subscription.updated", unknown_price))
    assert res.json()["result"] == "ignored" and (await plan_of(client, a))["key"] == "startup"


async def test_a_duplicate_event_is_applied_once(client, signup_org, owner) -> None:
    a = await with_customer(client, signup_org)
    body = event(
        "customer.subscription.created",
        subscription(await customer_id(owner, a)),
        id_="evt_dupe_0001",
    )
    assert (await post_event(client, body)).json()["result"] == "applied"
    assert (await post_event(client, body)).json()["result"] == "duplicate"


async def test_an_older_event_never_overwrites_newer_state(client, signup_org, owner) -> None:
    a = await with_customer(client, signup_org)
    cus = await customer_id(owner, a)
    now = int(time.time())
    await post_event(
        client,
        event("customer.subscription.updated", subscription(cus, status="active"), created=now),
    )
    late = await post_event(
        client,
        event(
            "customer.subscription.updated", subscription(cus, status="past_due"), created=now - 600
        ),
    )
    assert late.json()["result"] == "ignored"
    assert (await plan_of(client, a))["status"] == "active"


async def test_failed_and_paid_invoices_move_the_status(client, signup_org, owner) -> None:
    a = await with_customer(client, signup_org)
    cus = await customer_id(owner, a)
    await post_event(client, event("customer.subscription.created", subscription(cus)))
    await post_event(client, event("invoice.payment_failed", {"id": "in_1", "customer": cus}))
    status = await plan_of(client, a)
    assert (
        status["status"] == "past_due" and status["key"] == "startup"
    )  # a grace period, not a cut-off
    await post_event(client, event("invoice.paid", {"id": "in_2", "customer": cus}))
    assert (await plan_of(client, a))["status"] == "active"


async def test_cancelling_returns_the_org_to_the_free_plan(client, signup_org, owner) -> None:
    a = await with_customer(client, signup_org)
    cus = await customer_id(owner, a)
    await post_event(
        client,
        event("customer.subscription.created", subscription(cus), created=int(time.time()) - 100),
    )
    await a.make_key(("analytics", "mcp"))
    await post_event(
        client, event("customer.subscription.deleted", subscription(cus, status="canceled"))
    )
    plan = await plan_of(client, a)
    assert plan["key"] == "developer" and "mcp" not in plan["features"]
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert (await client.post("/v1/mcp", json=rpc, headers=a.key)).status_code == 403


async def test_events_for_unknown_customers_or_types_are_ignored(client, signup_org) -> None:
    await signup_org()
    unknown_customer = event("customer.subscription.created", subscription("cus_nobody"))
    assert (await post_event(client, unknown_customer)).json()["result"] == "ignored"
    assert (await post_event(client, event("charge.refunded", {"id": "ch_1"}))).json()[
        "result"
    ] == "ignored"


async def test_checkout_completion_only_links_a_customer_that_org_owns(
    client, signup_org, owner
) -> None:
    a, b = await with_customer(client, signup_org), await with_customer(client, signup_org)
    cus_b = await customer_id(owner, b)
    # an event claiming org A completed a checkout for org B's customer must not link anything
    forged = event(
        "checkout.session.completed",
        {"client_reference_id": a.org_id, "customer": cus_b, "subscription": "sub_evil"},
    )
    assert (await post_event(client, forged)).json()["result"] == "ignored"
    assert (
        await owner.fetchval(
            "SELECT stripe_subscription_id FROM subscriptions WHERE org_id = $1::uuid", a.org_id
        )
        is None
    )
    honest = event(
        "checkout.session.completed",
        {"client_reference_id": b.org_id, "customer": cus_b, "subscription": "sub_ok"},
    )
    assert (await post_event(client, honest)).json()["result"] == "applied"
    assert (
        await owner.fetchval(
            "SELECT stripe_subscription_id FROM subscriptions WHERE org_id = $1::uuid", b.org_id
        )
        == "sub_ok"
    )


async def test_each_orgs_plan_changes_independently(client, signup_org, owner) -> None:
    a, b = await with_customer(client, signup_org), await with_customer(client, signup_org)
    await post_event(
        client,
        event(
            "customer.subscription.created",
            subscription(await customer_id(owner, a), sub_id="sub_a"),
        ),
    )
    assert (await plan_of(client, a))["key"] == "startup" and (await plan_of(client, b))[
        "key"
    ] == "developer"


# --------------------------------------------------------------------- usage reporting
async def test_usage_is_reported_to_stripe_exactly_once(
    client, signup_org, cp, fake_stripe
) -> None:
    from tycheon_cp.billing_routes import build_billing

    a = await with_customer(client, signup_org)
    await a.make_key()
    for _ in range(4):
        assert (await client.post("/v1/forecast", json=FORECAST, headers=a.key)).status_code == 200
    billing = cp.extras["billing"] if "billing" in cp.extras else build_billing(cp, fake_stripe)
    mine_before = len(fake_stripe.usage)
    sent = await billing.report_usage()
    mine = fake_stripe.usage[mine_before:]
    forecasts = [u for u in mine if u["kind"] == "forecast_calls"]
    assert sent >= 4 and len(forecasts) == 4 and all(u["value"] == 1 for u in forecasts)
    assert len({u["identifier"] for u in mine}) == len(mine)  # stable, unique identifiers
    assert await billing.report_usage() == 0  # nothing is sent twice


async def test_orgs_without_a_stripe_customer_are_not_reported(
    client, signup_org, cp, fake_stripe
) -> None:
    from tycheon_cp.billing_routes import build_billing

    a = await signup_org()
    await a.make_key()
    await client.post("/v1/forecast", json=FORECAST, headers=a.key)
    before = len(fake_stripe.usage)
    await build_billing(cp, fake_stripe).report_usage()
    assert all(u["customer"] != "" for u in fake_stripe.usage[before:])
    assert not any(
        u for u in fake_stripe.usage[before:] if u["identifier"].startswith(f"tycheon-{a.org_id}")
    )


# ----------------------------------------------------------------------------- operator
async def test_the_operator_api_does_not_exist_without_the_token(client, signup_org) -> None:
    a = await signup_org()
    slug = (await client.get("/v1/me", headers=a.session)).json()["org"]["slug"]
    body = {"plan": "enterprise"}
    for headers in ({}, {"Authorization": "Bearer wrong"}, a.session):
        res = await client.put(f"/operator/orgs/{slug}/subscription", json=body, headers=headers)
        assert res.status_code == 404  # indistinguishable from a route that is not there


async def test_an_operator_puts_an_org_on_enterprise_terms(client, signup_org) -> None:
    a = await signup_org()
    slug = (await client.get("/v1/me", headers=a.session)).json()["org"]["slug"]
    res = await client.put(
        f"/operator/orgs/{slug}/subscription",
        json={"plan": "enterprise", "limits_override": {"gpu_seconds": 7200}},
        headers=OPERATOR,
    )
    assert res.status_code == 200
    plan = await plan_of(client, a)
    assert plan["key"] == "enterprise" and "finetune" in plan["features"]
    usage = {
        m["kind"]: m for m in (await client.get("/v1/usage", headers=a.session)).json()["meters"]
    }
    assert usage["gpu_seconds"]["limit"] == 7200
    bad = await client.put(
        f"/operator/orgs/{slug}/subscription", json={"plan": "nonsense"}, headers=OPERATOR
    )
    assert bad.status_code >= 400


async def test_custom_build_engagements_are_tracked_as_one_off_invoices(
    client, signup_org, fake_stripe
) -> None:
    a, b = await signup_org(), await signup_org()
    slug = (await client.get("/v1/me", headers=a.session)).json()["org"]["slug"]
    made = await client.post(
        f"/operator/orgs/{slug}/one-off-invoices",
        json={"description": "Custom regime-model build, phase 1", "amount_cents": 2_500_000},
        headers=OPERATOR,
    )
    assert made.status_code == 201 and made.json()["status"] == "draft"
    assert fake_stripe.invoices[-1]["amount_cents"] == 2_500_000
    mine = (await client.get("/v1/billing/one-off-invoices", headers=a.session)).json()
    assert [i["description"] for i in mine] == ["Custom regime-model build, phase 1"]
    assert (await client.get("/v1/billing/one-off-invoices", headers=b.session)).json() == []


@pytest.mark.parametrize("amount", [0, -5, 10**12])
async def test_absurd_invoice_amounts_are_refused(client, signup_org, amount) -> None:
    a = await signup_org()
    slug = (await client.get("/v1/me", headers=a.session)).json()["org"]["slug"]
    res = await client.post(
        f"/operator/orgs/{slug}/one-off-invoices",
        json={"description": "x", "amount_cents": amount},
        headers=OPERATOR,
    )
    assert res.status_code == 422


async def test_a_customer_cannot_invoice_or_upgrade_itself(client, signup_org) -> None:
    a = await signup_org()
    slug = (await client.get("/v1/me", headers=a.session)).json()["org"]["slug"]
    assert (
        await client.post(
            f"/operator/orgs/{slug}/one-off-invoices",
            json={"description": "x", "amount_cents": 1},
            headers=a.session,
        )
    ).status_code == 404
    assert (
        await client.put(
            f"/operator/orgs/{slug}/subscription", json={"plan": "enterprise"}, headers=a.session
        )
    ).status_code == 404

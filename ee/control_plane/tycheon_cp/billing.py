"""Stripe billing: catalogue from configuration, checkout, portal, metered usage, webhooks.

**Test mode only.** :class:`StripeApi` refuses any key that is not a test-mode key, and there is no
setting that turns that off: going live is a code change that needs explicit approval.

Design points:

* Products, prices and meters are created from ``plans.toml`` idempotently (looked up by
  ``lookup_key`` / event name), so running it twice changes nothing.
* Webhooks are authenticated by Stripe's signature, de-duplicated by event id, applied in order
  (an older event never overwrites newer state), and mapped to a plan only through the *price*
  the subscription actually holds, never through metadata a customer could influence.
* Usage is reported to Stripe meters from the committed usage events, once each, with a stable
  identifier so a retry cannot double-bill.
* The Stripe SDK is synchronous; calls run in a worker thread.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import asyncio
import functools
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

import stripe

from tycheon_cp.errors import ApiProblem

if TYPE_CHECKING:
    from tycheon_cp import store
    from tycheon_cp.db import Database
    from tycheon_cp.plans import Plans

TEST_KEY_PREFIXES = ("sk_test_", "rk_test_")
SIGNATURE_TOLERANCE_SECONDS = 300
METER_EVENT_PREFIX = "tycheon_"


class BillingError(Exception):
    """Billing is misconfigured or Stripe refused. The message is safe to log, not to show."""


class StripeApi(Protocol):
    """The handful of Stripe operations the control plane needs (faked in tests)."""

    def ensure_catalog(self, plans: Plans) -> dict[str, str]: ...

    def create_customer(self, *, email: str, name: str, org_id: str) -> str: ...

    def checkout_url(
        self,
        *,
        customer: str,
        price_id: str,
        org_id: str,
        plan: str,
        trial_days: int,
        success_url: str,
        cancel_url: str,
    ) -> str: ...

    def portal_url(self, *, customer: str, return_url: str) -> str: ...

    def preview(
        self, *, customer: str, subscription: str | None, price_id: str | None
    ) -> dict[str, Any]: ...

    def report_usage(
        self, *, kind: str, customer: str, value: int, identifier: str, timestamp: int
    ) -> None: ...

    def draft_invoice(
        self, *, customer: str, description: str, amount_cents: int, currency: str
    ) -> str: ...


def require_test_key(key: str | None) -> str:
    if not key or not key.startswith(TEST_KEY_PREFIXES):
        raise BillingError(
            "Stripe is restricted to test mode: a test-mode key (sk_test_...) is required"
        )
    return key


class RealStripe:
    """The Stripe SDK behind :class:`StripeApi`. Refuses live keys at construction."""

    def __init__(self, api_key: str | None, *, client: Any | None = None) -> None:
        key = require_test_key(api_key)
        self._client = client if client is not None else stripe.StripeClient(key)

    # ------------------------------------------------------------------ catalogue
    def ensure_catalog(self, plans: Plans) -> dict[str, str]:
        """Create the catalogue from configuration; return lookup_key -> price id."""
        v1 = self._client.v1
        prices: dict[str, str] = {}
        for plan in plans.plans.values():
            if plan.price is None:
                continue
            found = v1.prices.list({"lookup_keys": [plan.price.lookup_key], "limit": 1})
            if found.data:
                prices[plan.price.lookup_key] = found.data[0].id
                continue
            product = v1.products.create(
                {
                    "name": f"Tycheon {plan.name}",
                    "description": plan.description,
                    "metadata": {"tycheon_plan": plan.key},
                }
            )
            price = v1.prices.create(
                {
                    "product": product.id,
                    "currency": plan.price.currency,
                    "unit_amount": plan.price.amount_cents,
                    "recurring": {"interval": plan.price.interval},
                    "lookup_key": plan.price.lookup_key,
                    "metadata": {"tycheon_plan": plan.key},
                }
            )
            prices[plan.price.lookup_key] = price.id
        existing = {m.event_name for m in v1.billing.meters.list({"limit": 100}).data}
        for kind in plans.meters:
            event = METER_EVENT_PREFIX + kind
            if event not in existing:
                v1.billing.meters.create(
                    {
                        "display_name": f"Tycheon {kind.replace('_', ' ')}",
                        "event_name": event,
                        "default_aggregation": {"formula": "sum"},
                        "customer_mapping": {
                            "event_payload_key": "stripe_customer_id",
                            "type": "by_id",
                        },
                        "value_settings": {"event_payload_key": "value"},
                    }
                )
        return prices

    # ------------------------------------------------------------------- customers
    def create_customer(self, *, email: str, name: str, org_id: str) -> str:
        customer = self._client.v1.customers.create(
            {"email": email, "name": name, "metadata": {"tycheon_org_id": org_id}}
        )
        return str(customer.id)

    def checkout_url(
        self,
        *,
        customer: str,
        price_id: str,
        org_id: str,
        plan: str,
        trial_days: int,
        success_url: str,
        cancel_url: str,
    ) -> str:
        data: dict[str, Any] = {"metadata": {"tycheon_org_id": org_id, "tycheon_plan": plan}}
        if trial_days:
            data["trial_period_days"] = trial_days
        session = self._client.v1.checkout.sessions.create(
            {  # type: ignore[arg-type]
                "mode": "subscription",
                "customer": customer,
                "client_reference_id": org_id,
                "line_items": [{"price": price_id, "quantity": 1}],
                "subscription_data": data,
                "success_url": success_url,
                "cancel_url": cancel_url,
            }
        )
        return str(session.url)

    def portal_url(self, *, customer: str, return_url: str) -> str:
        session = self._client.v1.billing_portal.sessions.create(
            {"customer": customer, "return_url": return_url}
        )
        return str(session.url)

    # --------------------------------------------------------------------- invoices
    def preview(
        self, *, customer: str, subscription: str | None, price_id: str | None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"customer": customer}
        if subscription:
            params["subscription"] = subscription
        elif price_id:
            params["subscription_details"] = {"items": [{"price": price_id, "quantity": 1}]}
        invoice = self._client.v1.invoices.create_preview(params)  # type: ignore[arg-type]
        return {
            "currency": invoice.currency,
            "total_cents": invoice.total,
            "period_end": invoice.period_end,
            "lines": [
                {"description": line.description or "", "amount_cents": line.amount}
                for line in invoice.lines.data
            ],
        }

    def report_usage(
        self, *, kind: str, customer: str, value: int, identifier: str, timestamp: int
    ) -> None:
        self._client.v1.billing.meter_events.create(
            {
                "event_name": METER_EVENT_PREFIX + kind,
                "payload": {"stripe_customer_id": customer, "value": str(value)},
                "identifier": identifier,
                "timestamp": timestamp,
            }
        )

    def draft_invoice(
        self, *, customer: str, description: str, amount_cents: int, currency: str
    ) -> str:
        """A *draft* invoice for a one-off engagement. It is never finalised or sent here."""
        v1 = self._client.v1
        invoice = v1.invoices.create(
            {
                "customer": customer,
                "collection_method": "send_invoice",
                "days_until_due": 30,
                "auto_advance": False,
                "pending_invoice_items_behavior": "exclude",
            }
        )
        v1.invoice_items.create(
            {
                "customer": customer,
                "invoice": invoice.id,
                "amount": amount_cents,
                "currency": currency,
                "description": description,
            }
        )
        return str(invoice.id)


# ------------------------------------------------------------------------- the service
class Billing:
    def __init__(
        self,
        db: Database,
        plans: Plans,
        api: StripeApi,
        *,
        webhook_secret: str | None,
        public_url: str,
        dashboard_url: str,
    ) -> None:
        self.db, self.plans, self.api = db, plans, api
        self.webhook_secret = webhook_secret
        self.public_url, self.dashboard_url = public_url, dashboard_url
        self._prices: dict[str, str] | None = None

    async def prices(self) -> dict[str, str]:
        if self._prices is None:
            self._prices = await asyncio.to_thread(self.api.ensure_catalog, self.plans)
        return self._prices

    async def customer_for(self, principal: store.Principal) -> str:
        """The org's Stripe customer, created on first use."""
        async with self.db.tenant(principal.org_id) as conn:
            existing = await conn.fetchval("SELECT stripe_customer_id FROM subscriptions")
            if existing:
                return str(existing)
            email = await conn.fetchval("SELECT email FROM users WHERE id = $1", principal.user_id)
            name = await conn.fetchval("SELECT name FROM orgs")
        created = await asyncio.to_thread(
            self.api.create_customer,
            email=str(email),
            name=str(name),
            org_id=str(principal.org_id),
        )
        async with self.db.tenant(principal.org_id) as conn:
            await conn.execute(
                "UPDATE subscriptions SET stripe_customer_id = COALESCE(stripe_customer_id, $1)",
                created,
            )
            return str(await conn.fetchval("SELECT stripe_customer_id FROM subscriptions"))

    async def checkout(self, principal: store.Principal, plan_key: str) -> str:
        plan = self.plans.get(plan_key)
        if plan.price is None:
            raise ApiProblem(422, "invalid_request", f"The {plan.name} plan is not sold online.")
        prices = await self.prices()
        customer = await self.customer_for(principal)
        return await asyncio.to_thread(
            lambda: self.api.checkout_url(
                customer=customer,
                price_id=prices[plan.price.lookup_key],  # type: ignore[union-attr]
                org_id=str(principal.org_id),
                plan=plan.key,
                trial_days=plan.trial_days,
                success_url=f"{self.dashboard_url}/usage?checkout=success",
                cancel_url=f"{self.dashboard_url}/usage?checkout=cancelled",
            )
        )

    async def portal(self, principal: store.Principal) -> str:
        customer = await self.customer_for(principal)
        return await asyncio.to_thread(
            lambda: self.api.portal_url(customer=customer, return_url=f"{self.dashboard_url}/usage")
        )

    async def preview(self, principal: store.Principal) -> dict[str, Any]:
        """The upcoming invoice, or what the Startup plan would invoice if none is active yet."""
        customer = await self.customer_for(principal)
        async with self.db.tenant(principal.org_id) as conn:
            subscription = await conn.fetchval("SELECT stripe_subscription_id FROM subscriptions")
        price_id = None
        if not subscription:
            startup = next((p for p in self.plans.plans.values() if p.price), None)
            if startup is None or startup.price is None:
                raise ApiProblem(404, "not_found", "There is no invoice to preview.")
            price_id = (await self.prices())[startup.price.lookup_key]
        return await asyncio.to_thread(
            lambda: self.api.preview(
                customer=customer, subscription=subscription or None, price_id=price_id
            )
        )

    async def status(self, org_id: UUID) -> dict[str, Any]:
        async with self.db.tenant(org_id) as conn:
            row = await conn.fetchrow(
                "SELECT plan, status, trial_end, current_period_end, stripe_customer_id, "
                "cancel_at_period_end FROM subscriptions"
            )
        if row is None:
            raise ApiProblem(404, "not_found", "No subscription.")
        return {
            "plan": row["plan"],
            "status": row["status"],
            "trial_end": row["trial_end"],
            "current_period_end": row["current_period_end"],
            "cancel_at_period_end": row["cancel_at_period_end"],
            "has_customer": row["stripe_customer_id"] is not None,
        }

    # ------------------------------------------------------------------- usage reporting
    async def report_usage(self, *, batch: int = 500) -> int:
        """Send committed-but-unreported usage to Stripe meters. Returns how many were sent."""
        async with self.db.anonymous() as conn:
            orgs = [r["org_id"] for r in await conn.fetch("SELECT org_id FROM cp_list_orgs()")]
        sent = 0
        for org_id in orgs:
            async with self.db.tenant(org_id) as conn:
                customer = await conn.fetchval("SELECT stripe_customer_id FROM subscriptions")
                if not customer:
                    continue
                rows = await conn.fetch(
                    "SELECT id, kind, quantity, occurred_at FROM usage_events "
                    "WHERE reported_at IS NULL ORDER BY id LIMIT $1",
                    batch,
                )
            for row in rows:
                identifier = f"tycheon-{org_id}-{row['id']}"
                await asyncio.to_thread(
                    functools.partial(
                        self.api.report_usage,
                        kind=row["kind"],
                        customer=str(customer),
                        value=int(row["quantity"]),
                        identifier=identifier,
                        timestamp=int(row["occurred_at"].timestamp()),
                    )
                )
                async with self.db.tenant(org_id) as conn:
                    await conn.execute(
                        "UPDATE usage_events SET reported_at = now(), stripe_identifier = $2 "
                        "WHERE id = $1",
                        row["id"],
                        identifier,
                    )
                sent += 1
        return sent

    # ---------------------------------------------------------------------- webhooks
    def verify(self, payload: bytes, signature: str | None) -> dict[str, Any]:
        if not self.webhook_secret:
            raise ApiProblem(503, "billing_unavailable", "Webhooks are not configured.")
        try:
            event = stripe.Webhook.construct_event(
                payload, signature or "", self.webhook_secret, tolerance=SIGNATURE_TOLERANCE_SECONDS
            )
        except (ValueError, stripe.SignatureVerificationError) as exc:
            raise ApiProblem(
                400, "invalid_signature", "The webhook signature is not valid."
            ) from exc
        return dict(json.loads(str(event)))

    async def handle(self, event: dict[str, Any]) -> str:
        """Apply one verified event. Returns ``duplicate``, ``ignored`` or ``applied``."""
        async with self.db.anonymous() as conn:
            fresh = await conn.fetchval("SELECT cp_record_stripe_event($1)", str(event["id"]))
        if not fresh:
            return "duplicate"
        kind = str(event["type"])
        obj = event["data"]["object"]
        created = int(event.get("created", 0))
        handlers = {
            "checkout.session.completed": self._checkout_completed,
            "customer.subscription.created": self._subscription_changed,
            "customer.subscription.updated": self._subscription_changed,
            "customer.subscription.deleted": self._subscription_deleted,
            "invoice.paid": self._invoice_paid,
            "invoice.payment_failed": self._invoice_failed,
            "customer.subscription.trial_will_end": self._trial_will_end,
        }
        handler = handlers.get(kind)
        if handler is None:
            return "ignored"
        return await handler(obj, created)

    async def _org_for(self, customer: str | None) -> UUID | None:
        if not customer:
            return None
        async with self.db.anonymous() as conn:
            found = await conn.fetchval("SELECT cp_org_for_stripe_customer($1)", customer)
        return found  # type: ignore[no-any-return]

    def _plan_for(self, subscription: dict[str, Any]) -> str | None:
        """The plan a subscription's *price* belongs to; ``None`` when it matches no plan."""
        by_key = {p.price.lookup_key: p.key for p in self.plans.plans.values() if p.price}
        for item in subscription.get("items", {}).get("data", []):
            plan = by_key.get(item.get("price", {}).get("lookup_key") or "")
            if plan:
                return plan
        return None

    async def _checkout_completed(self, session: dict[str, Any], _created: int) -> str:
        org_id = session.get("client_reference_id")
        customer, subscription = session.get("customer"), session.get("subscription")
        if not (org_id and customer):
            return "ignored"
        try:
            org = UUID(str(org_id))
        except ValueError:
            return "ignored"
        async with self.db.tenant(org) as conn:
            # only a customer we created for this very org can be linked to it
            updated = await conn.execute(
                "UPDATE subscriptions SET "
                "stripe_subscription_id = COALESCE($2, stripe_subscription_id), "
                "updated_at = now() WHERE stripe_customer_id = $1",
                str(customer),
                str(subscription) if subscription else None,
            )
        return "applied" if updated == "UPDATE 1" else "ignored"

    async def _subscription_changed(self, sub: dict[str, Any], created: int) -> str:
        org = await self._org_for(sub.get("customer"))
        plan = self._plan_for(sub)
        if org is None or plan is None:
            return "ignored"
        trial_end = sub.get("trial_end")
        period_end = sub.get("current_period_end") or next(
            (i.get("current_period_end") for i in sub.get("items", {}).get("data", [])), None
        )
        async with self.db.tenant(org) as conn:
            updated = await conn.execute(
                "UPDATE subscriptions SET plan = $1, status = $2, stripe_subscription_id = $3, "
                "trial_end = $4, current_period_end = $5, cancel_at_period_end = $6, "
                "stripe_event_created = $7, updated_at = now() "
                "WHERE stripe_event_created <= $7",
                plan,
                str(sub.get("status", "active")),
                str(sub["id"]),
                _ts(trial_end),
                _ts(period_end),
                bool(sub.get("cancel_at_period_end", False)),
                created,
            )
            if updated == "UPDATE 1":
                await conn.execute("UPDATE orgs SET plan = $1::text", plan)
        return "applied" if updated == "UPDATE 1" else "ignored"

    async def _subscription_deleted(self, sub: dict[str, Any], created: int) -> str:
        org = await self._org_for(sub.get("customer"))
        if org is None:
            return "ignored"
        async with self.db.tenant(org) as conn:
            updated = await conn.execute(
                "UPDATE subscriptions SET status = 'canceled', stripe_event_created = $2, "
                "updated_at = now() WHERE stripe_event_created <= $2 "
                "AND (stripe_subscription_id IS NULL OR stripe_subscription_id = $1)",
                str(sub["id"]),
                created,
            )
            if updated == "UPDATE 1":
                await conn.execute("UPDATE orgs SET plan = 'developer'")
        return "applied" if updated == "UPDATE 1" else "ignored"

    async def _set_status(self, invoice: dict[str, Any], status: str, created: int) -> str:
        org = await self._org_for(invoice.get("customer"))
        if org is None:
            return "ignored"
        async with self.db.tenant(org) as conn:
            updated = await conn.execute(
                "UPDATE subscriptions SET status = $1, updated_at = now() "
                "WHERE status IN ('active','trialing','past_due') AND stripe_event_created <= $2",
                status,
                created,
            )
        return "applied" if updated == "UPDATE 1" else "ignored"

    async def _invoice_paid(self, invoice: dict[str, Any], created: int) -> str:
        return await self._set_status(invoice, "active", created)

    async def _invoice_failed(self, invoice: dict[str, Any], created: int) -> str:
        return await self._set_status(invoice, "past_due", created)

    async def _trial_will_end(self, sub: dict[str, Any], _created: int) -> str:
        org = await self._org_for(sub.get("customer"))
        if org is None:
            return "ignored"
        async with self.db.tenant(org) as conn:
            await conn.execute(
                "INSERT INTO audit_log (org_id, actor, action, target) VALUES ($1, 'stripe', "
                "'billing.trial_will_end', $2)",
                org,
                str(sub.get("id")),
            )
        return "applied"


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=UTC) if value else None

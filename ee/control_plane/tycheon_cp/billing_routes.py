"""Billing and operator routes.

Customers see their plan, start a checkout, open the Stripe customer portal and preview their next
invoice. *Operators* (Tycheon staff, authenticated by a separate token, never by a customer
credential) put organisations on negotiated Enterprise terms and raise one-off invoices for custom
build engagements: a customer cannot invoice itself or upgrade itself.

Proprietary: see ee/LICENSE.
"""

# NOTE: no ``from __future__ import annotations``: FastAPI reads the route annotations.
import asyncio
import hmac
import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from tycheon_cp import store
from tycheon_cp.app import ControlPlane, _Deps
from tycheon_cp.billing import Billing, BillingError, RealStripe, StripeApi
from tycheon_cp.errors import ApiProblem, not_found
from tycheon_cp.plans import PlanError

MAX_INVOICE_CENTS = 100_000_000  # $1,000,000: an operator typo guard, not a business rule


class CheckoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: Literal["startup"]


class OperatorSubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str = Field(pattern="^[a-z][a-z0-9_]{1,30}$")
    status: Literal["active", "trialing", "past_due", "canceled"] = "active"
    limits_override: dict[str, int] = Field(default_factory=dict, max_length=20)


class OneOffInvoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=300)
    amount_cents: int = Field(gt=0, le=MAX_INVOICE_CENTS)
    currency: Literal["usd", "eur", "gbp"] = "usd"


def build_billing(cp: ControlPlane, api: StripeApi | None = None) -> Billing:
    """Billing over the real Stripe SDK (test keys only), or a stand-in supplied by a test."""
    if api is None:
        try:
            api = RealStripe(cp.settings.stripe_secret_key)
        except BillingError as exc:
            raise ApiProblem(503, "billing_unavailable", "Billing is not configured.") from exc
    return Billing(
        cp.db,
        cp.plans,
        api,
        webhook_secret=cp.settings.stripe_webhook_secret,
        public_url=cp.settings.public_url,
        dashboard_url=cp.settings.dashboard_url,
    )


def register_billing(app: FastAPI, cp: ControlPlane, api: StripeApi | None = None) -> None:
    deps = _Deps(cp)
    Viewer = Annotated[store.Principal, Depends(deps.session_principal("viewer"))]  # noqa: N806
    Admin = Annotated[store.Principal, Depends(deps.session_principal("admin"))]  # noqa: N806
    Owner = Annotated[store.Principal, Depends(deps.session_principal("owner"))]  # noqa: N806
    router = APIRouter(prefix="/v1/billing", tags=["billing"])

    def billing() -> Billing:
        service = cp.extras.get("billing")
        if service is None:
            service = cp.extras["billing"] = build_billing(cp, api)
        return service

    @router.get("/status")
    async def status(who: Viewer) -> dict[str, Any]:
        return await billing().status(who.org_id)

    @router.post("/checkout")
    async def checkout(body: CheckoutIn, who: Owner) -> dict[str, str]:
        url = await billing().checkout(who, body.plan)
        async with cp.db.tenant(who.org_id) as conn:
            await store.audit(conn, who, "billing.checkout", body.plan, {})
        return {"url": url}

    @router.post("/portal")
    async def portal(who: Owner) -> dict[str, str]:
        return {"url": await billing().portal(who)}

    @router.get("/preview")
    async def preview(who: Admin) -> dict[str, Any]:
        return await billing().preview(who)

    @router.get("/one-off-invoices")
    async def one_off(who: Admin) -> list[dict[str, Any]]:
        async with cp.db.tenant(who.org_id) as conn:
            rows = await conn.fetch(
                "SELECT id, description, amount_cents, currency, status, created_at "
                "FROM oneoff_invoices ORDER BY created_at DESC"
            )
        return [{**dict(r), "id": str(r["id"])} for r in rows]

    @router.post("/webhook", include_in_schema=False)
    async def webhook(
        request: Request,
        stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
    ) -> dict[str, str]:
        service = billing()
        event = service.verify(await request.body(), stripe_signature)
        return {"result": await service.handle(event)}

    app.include_router(router)
    _register_operator(app, cp, billing)


def _register_operator(app: FastAPI, cp: ControlPlane, billing: Any) -> None:
    router = APIRouter(prefix="/operator", tags=["operator"], include_in_schema=False)

    async def operator(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = cp.settings.operator_token
        presented = (
            (authorization or "")[7:] if (authorization or "").lower().startswith("bearer ") else ""
        )
        # The operator API does not exist unless a token is configured; the check is constant time.
        if not expected or not hmac.compare_digest(presented.encode(), expected.encode()):
            raise not_found("resource")

    async def org_of(slug: str) -> Any:
        async with cp.db.anonymous() as conn:
            row = await conn.fetchrow("SELECT * FROM cp_org_by_slug($1)", slug)
        if row is None:
            raise not_found("organisation")
        return row["org_id"]

    @router.put("/orgs/{slug}/subscription", dependencies=[Depends(operator)])
    async def set_subscription(slug: str, body: OperatorSubscriptionIn) -> dict[str, Any]:
        try:
            cp.plans.get(body.plan)
        except PlanError as exc:
            raise ApiProblem(422, "invalid_request", "Unknown plan.") from exc
        org_id = await org_of(slug)
        async with cp.db.tenant(org_id) as conn:
            await conn.execute(
                "UPDATE subscriptions SET plan = $1, status = $2, limits_override = $3::jsonb, "
                "updated_at = now()",
                body.plan,
                body.status,
                json.dumps(body.limits_override),
            )
            await conn.execute("UPDATE orgs SET plan = $1", body.plan)
            await conn.execute(
                "INSERT INTO audit_log (org_id, actor, action, target, details) "
                "VALUES ($1, 'operator', 'subscription.set', $2, $3::jsonb)",
                org_id,
                body.plan,
                json.dumps({"override": body.limits_override, "status": body.status}),
            )
        return {"org_id": str(org_id), "plan": body.plan}

    @router.post("/orgs/{slug}/one-off-invoices", status_code=201, dependencies=[Depends(operator)])
    async def one_off_invoice(slug: str, body: OneOffInvoiceIn) -> dict[str, Any]:
        """Track a custom build engagement as a one-off invoice (a Stripe *draft*, never sent)."""
        org_id = await org_of(slug)
        service: Billing = billing()
        async with cp.db.tenant(org_id) as conn:
            customer = await conn.fetchval("SELECT stripe_customer_id FROM subscriptions")
            email = name = None
            if not customer:
                email = await conn.fetchval(
                    "SELECT email FROM users WHERE role = 'owner' ORDER BY created_at LIMIT 1"
                )
                name = await conn.fetchval("SELECT name FROM orgs")
        if not customer:
            customer = await asyncio.to_thread(
                service.api.create_customer, email=str(email), name=str(name), org_id=str(org_id)
            )
            async with cp.db.tenant(org_id) as conn:
                await conn.execute("UPDATE subscriptions SET stripe_customer_id = $1", customer)
        stripe_id = await asyncio.to_thread(
            lambda: service.api.draft_invoice(
                customer=str(customer),
                description=body.description,
                amount_cents=body.amount_cents,
                currency=body.currency,
            )
        )
        async with cp.db.tenant(org_id) as conn:
            invoice_id = await conn.fetchval(
                "INSERT INTO oneoff_invoices (org_id, description, amount_cents, currency, "
                "status, stripe_invoice_id) VALUES ($1,$2,$3,$4,'draft',$5) RETURNING id",
                org_id,
                body.description,
                body.amount_cents,
                body.currency,
                stripe_id,
            )
            await conn.execute(
                "INSERT INTO audit_log (org_id, actor, action, target, details) "
                "VALUES ($1, 'operator', 'invoice.draft', $2, $3::jsonb)",
                org_id,
                str(invoice_id),
                json.dumps({"amount_cents": body.amount_cents}),
            )
        return {"id": str(invoice_id), "status": "draft", "stripe_invoice_id": stripe_id}

    app.include_router(router)

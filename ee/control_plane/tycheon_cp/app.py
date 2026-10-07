"""The Tycheon Cloud control-plane API.

Two kinds of caller, two kinds of credential:

* **API keys** (``tyk_...``) call the data plane: forecast, calibrate, risk, backtest, report, MCP.
  A key belongs to one organisation and carries scopes.
* **Sessions** (a signed token from sign-up, password login or OIDC SSO) manage the organisation:
  keys, data sources, credentials, billing, fine-tuning. Management actions need a role.

The organisation always comes from the credential, never from the URL or the body, and every
database access runs under row-level security for that organisation.

Proprietary: see ee/LICENSE.
"""

# NOTE: no ``from __future__ import annotations``: FastAPI reads route annotations at definition
# time and the ``Annotated`` aliases below are local to the builder functions.
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from tycheon.governance import GovernedRuntime
from tycheon.models.base import DISCLAIMER
from tycheon.services.schemas import (
    BacktestIn,
    BacktestOut,
    CalibrationIn,
    CalibrationOut,
    ForecastIn,
    ForecastOut,
    RiskIn,
    RiskOut,
)
from tycheon_cp import store
from tycheon_cp.blob import BlobStore, LocalBlobStore, org_key
from tycheon_cp.bridge import AnalyticsGateway, PrivateModels
from tycheon_cp.config import Settings
from tycheon_cp.crypto import AwsKms, Kms, LocalKms
from tycheon_cp.datasources import DataSourceError, validate_csv
from tycheon_cp.db import Database
from tycheon_cp.errors import ApiProblem, forbidden, not_found, unauthorized
from tycheon_cp.metering import Meter, clean_idempotency_key
from tycheon_cp.plans import Plans, load_plans
from tycheon_cp.ratelimit import RateLimiter
from tycheon_cp.security import (
    ROLE_RANK,
    AuthError,
    Role,
    Session,
    issue_session,
    read_session,
    require_role,
)

MAX_JSON_BYTES = 1_000_000
_REPORT_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
MCP_PROTOCOL = "2025-06-18"


@dataclass
class ControlPlane:
    """Everything the routes share. Built once per process; tests build their own."""

    settings: Settings
    db: Database
    plans: Plans
    meter: Meter
    limiter: RateLimiter
    kms: Kms
    blobs: BlobStore
    runtime: GovernedRuntime
    gateway: AnalyticsGateway
    extras: dict[str, Any] = field(default_factory=dict)  # billing, finetune, sso, ...

    @property
    def session_secret(self) -> str:
        return self.settings.session_secret


def make_kms(settings: Settings) -> Kms:
    if settings.kms == "aws":
        return AwsKms(settings.aws_kms_key_id or "")
    return LocalKms(settings.local_kms_key or "")


def make_control_plane(
    settings: Settings,
    *,
    db: Database,
    runtime: GovernedRuntime | None = None,
    private_models: PrivateModels | None = None,
    clock: Callable[[], datetime] | None = None,
    limiter: RateLimiter | None = None,
) -> ControlPlane:
    """Assemble the control plane around a database (which may connect lazily)."""
    plans = load_plans()
    meter = Meter(db, plans, clock=clock)
    rate = limiter or RateLimiter()
    blobs = LocalBlobStore(settings.storage_root)
    governed = runtime or GovernedRuntime()
    gateway = AnalyticsGateway(
        db=db,
        plans=plans,
        meter=meter,
        limiter=rate,
        runtime=governed,
        blobs=blobs,
        private_models=private_models,
        clock=clock,
    )
    return ControlPlane(
        settings=settings,
        db=db,
        plans=plans,
        meter=meter,
        limiter=rate,
        kms=make_kms(settings),
        blobs=blobs,
        runtime=governed,
        gateway=gateway,
    )


async def build_control_plane(
    settings: Settings,
    *,
    db: Database | None = None,
    runtime: GovernedRuntime | None = None,
    private_models: PrivateModels | None = None,
    clock: Callable[[], datetime] | None = None,
    limiter: RateLimiter | None = None,
) -> ControlPlane:
    database = db or await Database.connect(settings.database_url)
    return make_control_plane(
        settings,
        db=database,
        runtime=runtime,
        private_models=private_models,
        clock=clock,
        limiter=limiter,
    )


# ------------------------------------------------------------------------------ schemas
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignupIn(_Strict):
    org_name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=39)
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)


class LoginIn(_Strict):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=256)


class SessionOut(_Strict):
    token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - the OAuth scheme name
    org_id: str
    user_id: str
    role: str


class ApiKeyIn(_Strict):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["analytics"], max_length=4)


class CredentialIn(_Strict):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=40)
    secret: str = Field(min_length=1, max_length=8192)


class DataSourceIn(_Strict):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["csv", "provider"] = "csv"
    credential_id: UUID | None = None
    default: bool = False


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[str] = Field(default_factory=list)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
    429: {"model": ErrorEnvelope},
}


# ------------------------------------------------------------------------------- app
def create_app(cp: ControlPlane, *, lifespan: Any | None = None) -> FastAPI:
    app = FastAPI(
        title="Tycheon Cloud API",
        version="0.1.0",
        description=(
            "Multi-tenant calibrated forecasting and risk. API keys call the analytics; "
            f"sessions manage the organisation. {DISCLAIMER}"
        ),
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.cp = cp
    _install_errors(app)
    deps = _Deps(cp)
    app.include_router(_public_routes(cp))
    app.include_router(_account_routes(cp, deps))
    app.include_router(_analytics_routes(cp, deps))
    app.include_router(_mcp_routes(cp, deps))
    return app


def _install_errors(app: FastAPI) -> None:
    @app.middleware("http")
    async def harden(request: Request, call_next: Callable[..., Any]) -> Response:
        cap = (
            _upload_cap(request)
            if request.method == "PUT" and "/files/" in request.url.path
            else MAX_JSON_BYTES
        )
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cap:
            return JSONResponse(
                {
                    "error": {
                        "code": "payload_too_large",
                        "message": "The request body is too large.",
                    }
                },
                status_code=413,
            )
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(ApiProblem)
    async def _problem(_: Request, exc: ApiProblem) -> JSONResponse:
        return JSONResponse(exc.body(), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(AuthError)
    async def _auth(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "unauthorized", "message": str(exc), "details": []}}, status_code=401
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()][:20]
        body = ErrorEnvelope(
            error=ErrorBody(
                code="invalid_request", message="The request is not valid.", details=details
            )
        )
        return JSONResponse(body.model_dump(), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "error"
        )
        body = ErrorEnvelope(error=ErrorBody(code=code, message=str(exc.detail)[:200]))
        return JSONResponse(body.model_dump(), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, __: Exception) -> JSONResponse:
        body = ErrorEnvelope(
            error=ErrorBody(code="internal_error", message="The request could not be processed.")
        )
        return JSONResponse(body.model_dump(), status_code=500)


def _upload_cap(request: Request) -> int:
    cp: ControlPlane = request.app.state.cp
    return cp.settings.max_upload_bytes


def _bearer(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


class _Deps:
    """FastAPI dependencies bound to one control plane."""

    def __init__(self, cp: ControlPlane) -> None:
        self.cp = cp

    async def principal(
        self,
        authorization: Annotated[str | None, Header()] = None,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> store.Principal:
        token = _bearer(authorization, x_api_key)
        if not token:
            raise unauthorized()
        if token.startswith("tyk_"):
            found = await store.authenticate_key(self.cp.db, token)
            if found is None:
                raise unauthorized("That API key is not valid.")
            return found
        try:
            session = read_session(self.cp.session_secret, token)
        except AuthError as exc:
            raise unauthorized(str(exc)) from exc
        scopes = {"analytics", "mcp"} | ({"admin"} if ROLE_RANK[session.role] >= 2 else set())
        return store.Principal(
            org_id=UUID(session.org_id),
            kind="session",
            scopes=frozenset(scopes),
            role=session.role,
            user_id=UUID(session.user_id),
        )

    def session_principal(self, minimum: Role) -> Callable[..., Any]:
        """A dependency that accepts only a signed-in user with at least ``minimum`` role."""

        async def dependency(
            principal: Annotated[store.Principal, Depends(self.principal)],
        ) -> store.Principal:
            if principal.kind != "session" or principal.role is None:
                raise forbidden("This action needs a signed-in user, not an API key.")
            try:
                require_role(
                    Session(str(principal.user_id), str(principal.org_id), principal.role), minimum
                )
            except AuthError as exc:
                raise forbidden(str(exc)) from exc
            return principal

        return dependency


# ------------------------------------------------------------------------ public routes
def _session_out(
    cp: ControlPlane, org_id: UUID | str, user_id: UUID | str, role: Role
) -> SessionOut:
    token = issue_session(
        cp.session_secret,
        Session(str(user_id), str(org_id), role),
        ttl_seconds=cp.settings.session_ttl_seconds,
    )
    return SessionOut(token=token, org_id=str(org_id), user_id=str(user_id), role=role)


def _public_routes(cp: ControlPlane) -> APIRouter:
    router = APIRouter()

    @router.get("/health", tags=["service"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "plans": sorted(cp.plans.plans)}

    @router.get("/v1/plans", tags=["billing"])
    async def list_plans() -> list[dict[str, Any]]:
        """The public plan catalogue (from configuration)."""
        out = []
        for plan in cp.plans.plans.values():
            out.append(
                {
                    "key": plan.key,
                    "name": plan.name,
                    "description": plan.description,
                    "features": sorted(plan.features),
                    "limits": plan.limits,
                    "price": (
                        {
                            "amount_cents": plan.price.amount_cents,
                            "currency": plan.price.currency,
                            "interval": plan.price.interval,
                        }
                        if plan.price
                        else None
                    ),
                    "trial_days": plan.trial_days,
                    "research_use_only": plan.research_use_only,
                    "data_frequencies": list(plan.data_frequencies),
                }
            )
        return out

    @router.post("/auth/signup", response_model=SessionOut, status_code=201, tags=["auth"])
    async def signup(body: SignupIn) -> SessionOut:
        org_id, user_id = await store.signup(
            cp.db, org_name=body.org_name, slug=body.slug, email=body.email, password=body.password
        )
        return _session_out(cp, org_id, user_id, "owner")

    @router.post("/auth/login", response_model=SessionOut, tags=["auth"])
    async def login(body: LoginIn) -> SessionOut:
        session = await store.login(cp.db, email=body.email, password=body.password)
        return _session_out(cp, session.org_id, session.user_id, session.role)

    return router


# ----------------------------------------------------------------------- account routes
def _account_routes(cp: ControlPlane, deps: _Deps) -> APIRouter:
    router = APIRouter(prefix="/v1")
    Viewer = Annotated[store.Principal, Depends(deps.session_principal("viewer"))]  # noqa: N806
    Member = Annotated[store.Principal, Depends(deps.session_principal("member"))]  # noqa: N806
    Admin = Annotated[store.Principal, Depends(deps.session_principal("admin"))]  # noqa: N806

    @router.get("/me", tags=["account"], responses=_ERRORS)
    async def me(who: Viewer) -> dict[str, Any]:
        org = await store.load_org(cp.db, who.org_id, cp.plans)
        user = await store.me(
            cp.db, Session(str(who.user_id), str(who.org_id), who.role or "viewer")
        )
        return {
            **user,
            "org": {"id": str(org.org_id), "slug": org.slug, "name": org.name},
            "plan": {
                "key": org.plan.key,
                "name": org.plan.name,
                "status": org.status,
                "features": sorted(org.plan.features),
                "research_use_only": org.plan.research_use_only,
            },
        }

    @router.get("/usage", tags=["account"], responses=_ERRORS)
    async def usage(who: Viewer) -> dict[str, Any]:
        org = await store.load_org(cp.db, who.org_id, cp.plans)
        return {
            "plan": org.plan.key,
            "meters": await cp.meter.summary(who.org_id, org.plan, override=org.override),
        }

    @router.get("/audit", tags=["account"], responses=_ERRORS)
    async def audit(who: Admin, limit: int = 100) -> list[dict[str, Any]]:
        return await store.list_audit(cp.db, who.org_id, limit)

    @router.get("/api-keys", tags=["keys"], responses=_ERRORS)
    async def keys(who: Admin) -> list[dict[str, Any]]:
        return await store.list_api_keys(cp.db, who.org_id)

    @router.post("/api-keys", status_code=201, tags=["keys"], responses=_ERRORS)
    async def create_key(body: ApiKeyIn, who: Admin) -> dict[str, Any]:
        """Create a key. The secret is returned once and cannot be retrieved again."""
        key_id, key = await store.create_api_key(cp.db, who, name=body.name, scopes=body.scopes)
        return {"id": str(key_id), "name": body.name, "prefix": key.prefix, "key": key.token}

    @router.delete("/api-keys/{key_id}", status_code=204, tags=["keys"], responses=_ERRORS)
    async def revoke_key(key_id: UUID, who: Admin) -> Response:
        await store.revoke_api_key(cp.db, who, key_id)
        return Response(status_code=204)

    @router.get("/credentials", tags=["data"], responses=_ERRORS)
    async def credentials(who: Admin) -> list[dict[str, Any]]:
        return await store.list_credentials(cp.db, who.org_id)

    @router.post("/credentials", status_code=201, tags=["data"], responses=_ERRORS)
    async def create_credential(body: CredentialIn, who: Admin) -> dict[str, Any]:
        """Store a bring-your-own market-data credential, sealed under the KMS. Never echoed."""
        credential_id = await store.create_credential(
            cp.db, cp.kms, who, name=body.name, provider=body.provider, secret=body.secret
        )
        return {"id": str(credential_id), "name": body.name, "provider": body.provider}

    @router.delete(
        "/credentials/{credential_id}", status_code=204, tags=["data"], responses=_ERRORS
    )
    async def delete_credential(credential_id: UUID, who: Admin) -> Response:
        await store.delete_credential(cp.db, who, credential_id)
        return Response(status_code=204)

    @router.get("/data-sources", tags=["data"], responses=_ERRORS)
    async def data_sources(who: Viewer) -> list[dict[str, Any]]:
        return await store.list_data_sources(cp.db, who.org_id)

    @router.post("/data-sources", status_code=201, tags=["data"], responses=_ERRORS)
    async def create_data_source(body: DataSourceIn, who: Member) -> dict[str, Any]:
        if body.kind == "provider" and body.credential_id is None:
            raise ApiProblem(422, "invalid_request", "A provider data source needs a credential.")
        source_id = await store.create_data_source(
            cp.db,
            who,
            name=body.name,
            kind=body.kind,
            credential_id=body.credential_id,
            make_default=body.default,
        )
        return {"id": str(source_id), "name": body.name, "kind": body.kind}

    @router.put("/data-sources/{source_id}/files/{symbol}", tags=["data"], responses=_ERRORS)
    async def upload_file(
        source_id: UUID, symbol: str, request: Request, who: Member
    ) -> dict[str, Any]:
        """Upload ``<symbol>.csv`` (bars with a timestamp column) into a CSV data source."""
        source = await store.get_data_source(cp.db, who.org_id, source_id)
        if source["kind"] != "csv":
            raise ApiProblem(422, "invalid_request", "Files can only be uploaded to a CSV source.")
        raw = await request.body()
        if len(raw) > cp.settings.max_upload_bytes:
            raise ApiProblem(413, "payload_too_large", "The file is too large.")
        org = await store.load_org(cp.db, who.org_id, cp.plans)
        try:
            from tycheon.data.schema import validate_symbol  # noqa: PLC0415

            validate_symbol(symbol)
            info = validate_csv(raw, max_bytes=cp.settings.max_upload_bytes)
        except (DataSourceError, ValueError) as exc:
            raise ApiProblem(422, "invalid_data", str(exc)) from exc
        if info.frequency not in org.plan.data_frequencies:
            raise ApiProblem(
                403,
                "plan_feature",
                f"The {org.plan.name} plan covers {', '.join(org.plan.data_frequencies)} data; "
                f"this file is {info.frequency}.",
            )
        key = org_key(str(who.org_id), "ds", str(source_id), f"{symbol}.csv")
        cp.blobs.put(key, raw)
        await store.record_data_file(
            cp.db,
            who,
            source_id=source_id,
            symbol=symbol,
            blob_key=key,
            n_rows=info.n_rows,
            first_ts=info.first_ts,
            last_ts=info.last_ts,
            sha256=info.sha256,
        )
        return {
            "symbol": symbol,
            "n_rows": info.n_rows,
            "first_ts": info.first_ts.isoformat(),
            "last_ts": info.last_ts.isoformat(),
            "frequency": info.frequency,
        }

    @router.delete("/data-sources/{source_id}", status_code=204, tags=["data"], responses=_ERRORS)
    async def delete_data_source(source_id: UUID, who: Admin) -> Response:
        await store.delete_data_source(cp.db, who, source_id)
        cp.blobs.delete_prefix(org_key(str(who.org_id), "ds", str(source_id)))
        return Response(status_code=204)

    return router


# ---------------------------------------------------------------------- analytics routes
def _analytics_routes(cp: ControlPlane, deps: _Deps) -> APIRouter:
    router = APIRouter(prefix="/v1")
    Caller = Annotated[store.Principal, Depends(deps.principal)]  # noqa: N806
    IdemKey = Annotated[str | None, Header(alias="Idempotency-Key")]  # noqa: N806

    async def run(
        who: store.Principal, tool: str, body: BaseModel | dict[str, Any], key: str | None
    ) -> dict[str, Any]:
        if "analytics" not in who.scopes:
            raise forbidden("This credential is not allowed to call the analytics.")
        arguments = body.model_dump(mode="json") if isinstance(body, BaseModel) else body
        return await cp.gateway.run(
            who, tool, arguments, idempotency_key=clean_idempotency_key(key)
        )

    @router.post("/forecast", response_model=ForecastOut, tags=["analytics"], responses=_ERRORS)
    async def forecast(body: ForecastIn, who: Caller, key: IdemKey = None) -> dict[str, Any]:
        """A calibrated forecast distribution. Meters one forecast call."""
        return await run(who, "forecast_distribution", body, key)

    @router.post("/calibrate", response_model=CalibrationOut, tags=["analytics"], responses=_ERRORS)
    async def calibrate(body: CalibrationIn, who: Caller, key: IdemKey = None) -> dict[str, Any]:
        """A calibration report (Startup and above)."""
        return await run(who, "calibration_report", body, key)

    @router.post("/risk", response_model=RiskOut, tags=["analytics"], responses=_ERRORS)
    async def risk(body: RiskIn, who: Caller, key: IdemKey = None) -> dict[str, Any]:
        """VaR, Expected Shortfall, drawdown and stress. Meters one risk report."""
        return await run(who, "portfolio_risk", body, key)

    @router.post("/backtest", response_model=BacktestOut, tags=["analytics"], responses=_ERRORS)
    async def backtest(body: BacktestIn, who: Caller, key: IdemKey = None) -> dict[str, Any]:
        """Walk-forward evaluation against the random walk. Meters compute seconds."""
        return await run(who, "backtest_summary", body, key)

    @router.get("/report/{report_id}", tags=["analytics"], responses=_ERRORS)
    async def report(
        report_id: str, who: Caller, fmt: Literal["json", "html"] = "json"
    ) -> Response:
        try:
            out = await run(who, "risk_report", {"report_id": report_id}, None)
        except ApiProblem as exc:
            if exc.code == "tool_failed":
                raise not_found("report") from exc
            raise
        if fmt == "html":
            return HTMLResponse(
                out.get("html") or "", headers={"Content-Security-Policy": _REPORT_CSP}
            )
        return JSONResponse(out["report"])

    return router


# ------------------------------------------------------------------------------- MCP
_MCP_TOOLS: dict[str, tuple[str, type[BaseModel] | None, str]] = {
    "forecast": ("forecast_distribution", ForecastIn, "A calibrated forecast distribution."),
    "calibrate": ("calibration_report", CalibrationIn, "Raw versus calibrated coverage."),
    "risk": ("portfolio_risk", RiskIn, "VaR, Expected Shortfall, drawdown and stress."),
    "backtest": (
        "backtest_summary",
        BacktestIn,
        "Walk-forward evaluation against the random walk.",
    ),
}


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _mcp_routes(cp: ControlPlane, deps: _Deps) -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.post("/mcp", tags=["mcp"], responses=_ERRORS)
    async def mcp(
        request: Request,
        who: Annotated[store.Principal, Depends(deps.principal)],
        idem: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> Response:
        """A stateless MCP endpoint (JSON-RPC over HTTP): the same governed, metered tools.

        Needs a plan that includes MCP and a credential with the ``mcp`` scope.
        """
        if "mcp" not in who.scopes:
            raise forbidden("This credential does not have the mcp scope.")
        org = await store.load_org(cp.db, who.org_id, cp.plans)
        if not org.plan.has("mcp"):  # the whole endpoint, not just the tools, is a plan feature
            raise ApiProblem(
                403, "plan_feature", f"Your {org.plan.name} plan does not include the MCP endpoint."
            )
        try:
            message = json.loads(await request.body())
        except ValueError:
            return JSONResponse(_rpc_error(None, -32700, "Parse error"), status_code=400)
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return JSONResponse(_rpc_error(None, -32600, "Invalid Request"), status_code=400)
        request_id, method = message.get("id"), message.get("method")
        params = message.get("params") or {}
        if request_id is None:  # a notification: acknowledge and say nothing
            return Response(status_code=202)
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": MCP_PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "tycheon-cloud", "version": "0.1.0"},
                "instructions": DISCLAIMER,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": name,
                        "description": f"{text} {DISCLAIMER}",
                        "inputSchema": model.model_json_schema() if model else {"type": "object"},
                    }
                    for name, (_, model, text) in _MCP_TOOLS.items()
                ]
            }
        elif method == "tools/call":
            result = await _mcp_call(cp, who, params, idem)
        else:
            return JSONResponse(_rpc_error(request_id, -32601, "Method not found"))
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

    return router


async def _mcp_call(
    cp: ControlPlane, who: store.Principal, params: Any, idem: str | None
) -> dict[str, Any]:
    """Run one MCP tool call. A refusal is a tool *result* with ``isError``, per the protocol."""

    def refusal(message: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": message}], "isError": True}

    name = params.get("name") if isinstance(params, dict) else None
    if name not in _MCP_TOOLS:
        return refusal("Unknown tool.")
    tool, model, _ = _MCP_TOOLS[name]
    arguments = params.get("arguments") or {}
    try:
        validated = model.model_validate(arguments) if model else arguments
    except ValidationError as exc:
        return refusal("Invalid arguments: " + "; ".join(e["msg"] for e in exc.errors()[:5]))
    payload = validated.model_dump(mode="json") if isinstance(validated, BaseModel) else validated
    try:
        output = await cp.gateway.run(
            who, tool, payload, idempotency_key=clean_idempotency_key(idem), via_mcp=True
        )
    except ApiProblem as exc:
        return refusal(f"{exc.code}: {exc.message}")
    return {"content": [{"type": "text", "text": json.dumps(output)}], "isError": False}


__all__ = ["ControlPlane", "build_control_plane", "create_app", "make_control_plane"]

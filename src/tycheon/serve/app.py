"""The REST API: forecast, calibrate, risk, report, backtest, async jobs, and the approvals API.

Every analytic endpoint is a **governed tool call** made through the governed runtime as the
read-only ``api-reader`` principal: capability grant, policy and audit apply exactly as they do
for an agent or an MCP client. There is no second code path to the analytics.

Security properties, each with a test:

* a key identifies a **tenant**; the tenant is never taken from the request, so one tenant cannot
  address another;
* ``as_of`` is **not a request field**: the server decides the date (a client or model cannot
  ask for the future), and every response says which ``as_of`` it used;
* inputs are bounded (see ``tycheon.services.schemas``) and the body size is capped;
* errors are a fixed ``{"error": {"code", "message"}}`` envelope with no stack traces or input
  echoed back;
* the report page is served with a restrictive Content-Security-Policy (inline SVG only).

For research and risk analytics. Not investment advice.
"""

# NOTE: no ``from __future__ import annotations`` here: FastAPI reads the route annotations at
# definition time, and the ``Tenant`` alias below is local to ``create_app``.
import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

import tycheon
from tycheon.agents.protocols import ToolResult
from tycheon.governance import AGENT_API, GovernedRuntime
from tycheon.models.base import DISCLAIMER
from tycheon.serve.auth import ApiKeys, extract_key
from tycheon.serve.jobs import Job, JobManager, TooManyJobsError
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

MAX_BODY_BYTES = 1_000_000
_REPORT_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)

#: Gateway error codes and the HTTP status each maps to.
_STATUS = {
    "capability_denied": 403,
    "not_authorised": 403,
    "policy_denied": 403,
    "execution_mode_forbidden": 403,
    "side_effect_not_permitted": 403,
    "invalid_arguments": 422,
    "unknown_tool": 404,
    "budget_exceeded": 429,
    "tool_timeout": 504,
    "tool_failed": 400,
}

JobKind = Literal["forecast", "calibrate", "risk", "backtest"]
_JOB_TOOLS: dict[str, tuple[str, type[BaseModel]]] = {
    "forecast": ("forecast_distribution", ForecastIn),
    "calibrate": ("calibration_report", CalibrationIn),
    "risk": ("portfolio_risk", RiskIn),
    "backtest": ("backtest_summary", BacktestIn),
}


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, details: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or []


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[str] = Field(default_factory=list)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class JobIn(BaseModel):
    kind: JobKind
    input: dict[str, Any] = Field(default_factory=dict)


class JobAccepted(BaseModel):
    job_id: str
    status: str
    status_url: str


class JobOut(BaseModel):
    job_id: str
    kind: str
    status: str
    created_at: str
    finished_at: str | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    disclaimer: str = DISCLAIMER


def _job_out(job: Job) -> JobOut:
    return JobOut(
        job_id=job.id,
        kind=job.kind,
        status=job.status,
        created_at=job.created_at.isoformat(),
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        result=job.result,
        error=job.error,
    )


def _fail(result: ToolResult) -> ApiError:
    code = result.error_code or result.status.lower()
    status = _STATUS.get(code, 400 if result.status == "ERROR" else 403)
    return ApiError(
        status,
        code,
        result.message or "The request could not be completed.",
        list(result.policy_reasons),
    )


class _Service:
    """What the route functions share: the runtime, jobs, the server's ``as_of`` and auth."""

    def __init__(
        self,
        runtime: GovernedRuntime,
        manager: JobManager,
        as_of: Callable[[], datetime],
        authenticate: Callable[..., Any],
        max_concurrency: int,
    ) -> None:
        self.runtime = runtime
        self.jobs = manager
        self.as_of = as_of
        self.authenticate = authenticate
        #: Bounds simultaneous analytics (they are CPU-heavy): further requests wait their turn.
        self.limit = asyncio.Semaphore(max_concurrency)

    async def call(
        self, tenant: str, tool: str, payload: BaseModel | dict[str, Any]
    ) -> dict[str, Any]:
        arguments = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        async with self.limit:
            result = await self.runtime.call(
                AGENT_API, tool, arguments, as_of=self.as_of(), tenant_id=tenant
            )
        if result.ok and result.output is not None:
            return result.output
        raise _fail(result)


_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
}


def _install_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def harden(request: Request, call_next: Callable[..., Any]) -> Response:
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
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

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        body = ErrorEnvelope(
            error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
        )
        return JSONResponse(body.model_dump(), status_code=exc.status)

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


def _register_analytics(app: FastAPI, svc: _Service) -> None:
    Tenant = Annotated[str, Depends(svc.authenticate)]  # noqa: N806

    @app.get("/health", tags=["service"])
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": tycheon.__version__,
            "tools": len(svc.runtime.tool_names()),
        }

    @app.post("/v1/forecast", response_model=ForecastOut, tags=["analytics"], responses=_ERRORS)
    async def forecast(body: ForecastIn, tenant: Tenant) -> dict[str, Any]:
        """A calibrated forecast distribution for one symbol."""
        return await svc.call(tenant, "forecast_distribution", body)

    @app.post("/v1/calibrate", response_model=CalibrationOut, tags=["analytics"], responses=_ERRORS)
    async def calibrate(body: CalibrationIn, tenant: Tenant) -> dict[str, Any]:
        """Raw versus calibrated coverage for a model on one series."""
        return await svc.call(tenant, "calibration_report", body)

    @app.post("/v1/risk", response_model=RiskOut, tags=["analytics"], responses=_ERRORS)
    async def risk(body: RiskIn, tenant: Tenant) -> dict[str, Any]:
        """VaR, Expected Shortfall, drawdown, volatility and stress. Multi-asset: uncalibrated."""
        return await svc.call(tenant, "portfolio_risk", body)

    @app.post("/v1/backtest", response_model=BacktestOut, tags=["analytics"], responses=_ERRORS)
    async def backtest(body: BacktestIn, tenant: Tenant) -> dict[str, Any]:
        """Walk-forward evaluation against the random walk, with Diebold-Mariano tests."""
        return await svc.call(tenant, "backtest_summary", body)

    @app.get(
        "/v1/report/{report_id}",
        tags=["analytics"],
        responses={**_ERRORS, 404: {"model": ErrorEnvelope}},
    )
    async def report(
        report_id: str,
        tenant: Tenant,
        fmt: Annotated[Literal["json", "html"], Query(alias="format")] = "json",
    ) -> Response:
        """The full report behind a risk result: JSON, or a self-contained HTML page."""
        try:
            out = await svc.call(tenant, "risk_report", {"report_id": report_id})
        except ApiError as exc:
            if exc.code == "tool_failed":
                raise ApiError(404, "not_found", "No such report for this tenant.") from exc
            raise
        if fmt == "html":
            return HTMLResponse(
                out.get("html") or "", headers={"Content-Security-Policy": _REPORT_CSP}
            )
        return JSONResponse(out["report"])


def _register_jobs(app: FastAPI, svc: _Service) -> None:
    Tenant = Annotated[str, Depends(svc.authenticate)]  # noqa: N806

    @app.post(
        "/v1/jobs", response_model=JobAccepted, status_code=202, tags=["jobs"], responses=_ERRORS
    )
    async def submit_job(body: JobIn, tenant: Tenant, response: Response) -> JobAccepted:
        """Run an analytic in the background; poll `GET /v1/jobs/{job_id}` for the result."""
        tool, model = _JOB_TOOLS[body.kind]
        try:
            validated = TypeAdapter(model).validate_python(body.input)
        except ValidationError as exc:
            details = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()][
                :20
            ]
            raise ApiError(422, "invalid_request", "The job input is not valid.", details) from exc
        arguments = validated.model_dump(mode="json")
        when = svc.as_of()  # fixed at submission, so a slow job still answers as of that moment

        async def work() -> ToolResult:
            async with svc.limit:
                return await svc.runtime.call(
                    AGENT_API, tool, arguments, as_of=when, tenant_id=tenant
                )

        try:
            job = svc.jobs.submit(tenant, body.kind, work)
        except TooManyJobsError as exc:
            raise ApiError(
                429, "too_many_jobs", "Too many unfinished jobs; try again later."
            ) from exc
        url = f"/v1/jobs/{job.id}"
        response.headers["Location"] = url
        return JobAccepted(job_id=job.id, status=job.status, status_url=url)

    @app.get("/v1/jobs/{job_id}", response_model=JobOut, tags=["jobs"], responses=_ERRORS)
    async def get_job(job_id: str, tenant: Tenant) -> JobOut:
        job = svc.jobs.get(tenant, job_id)
        if job is None:
            raise ApiError(404, "not_found", "No such job.")
        return _job_out(job)

    @app.get("/v1/jobs", response_model=list[JobOut], tags=["jobs"], responses=_ERRORS)
    async def list_jobs(tenant: Tenant) -> list[JobOut]:
        return [_job_out(j) for j in svc.jobs.list(tenant)]


def create_app(
    runtime: GovernedRuntime,
    keys: ApiKeys | None,
    *,
    as_of: Callable[[], datetime],
    approvals_app: Any | None = None,
    jobs: JobManager | None = None,
    insecure_dev_tenant: str | None = None,
    max_concurrency: int = 4,
) -> FastAPI:
    """Build the API over a governed runtime.

    ``keys`` authenticates callers. ``insecure_dev_tenant`` (local development only) accepts
    unauthenticated requests as that tenant; the CLI refuses it on a non-loopback address.
    """
    if not keys and insecure_dev_tenant is None:
        raise ValueError("API keys are required unless insecure_dev_tenant is set")
    key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def authenticate(
        x_api_key: Annotated[str | None, Depends(key_scheme)] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> str:
        tenant = keys.tenant_for(extract_key(x_api_key, authorization)) if keys else None
        if tenant is None and insecure_dev_tenant is not None and not keys:
            tenant = insecure_dev_tenant
        if tenant is None:
            raise ApiError(401, "unauthorized", "A valid API key is required.")
        return tenant

    app = FastAPI(
        title="Tycheon API",
        version=tycheon.__version__,
        description=(
            "Calibrated forecasts and decision-ready risk. Every call is a governed tool call "
            "(capability grant, policy, audit). The server chooses `as_of`; clients cannot. "
            f"{DISCLAIMER}"
        ),
        docs_url="/docs",
        redoc_url=None,
    )
    manager = jobs or JobManager()
    service = _Service(runtime, manager, as_of, authenticate, max_concurrency)
    _install_error_handling(app)
    _register_analytics(app, service)
    _register_jobs(app, service)
    if approvals_app is not None:
        app.mount("/v1", approvals_app)  # Keelgate's approvals REST app: /v1/approvals...
    app.state.jobs = manager
    return app

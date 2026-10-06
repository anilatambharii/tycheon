"""The REST API and the CLI entry points: auth, tenant scoping, jobs, errors, OpenAPI, MCP stdio."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx
import pytest

from tycheon.data.sample import sample_end
from tycheon.governance import ApproverToken, GovernedRuntime, RuntimeConfig, build_approvals_app
from tycheon.serve import ApiKeyError, ApiKeys, JobManager, create_app
from tycheon.serve.cli import mcp_main, serve_main

AS_OF = sample_end("SYN-GBM")
KEY_A = "acme-key-0123456789abcdef"
KEY_B = "globex-key-0123456789abcdef"
KEYS = ApiKeys({KEY_A: "acme", KEY_B: "globex"})
HDR_A = {"X-API-Key": KEY_A}
HDR_B = {"X-API-Key": KEY_B}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def runtime(tmp_path):
    rt = GovernedRuntime(RuntimeConfig(state_dir=tmp_path / "state", tenant_id="acme"))
    yield rt
    rt.close()


def client(app, **kw) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test", **kw)


def make_app(runtime, **kw):
    return create_app(runtime, KEYS, as_of=lambda: AS_OF, **kw)


# ----------------------------------------------------------------------------- keys
def test_api_keys_parse_both_formats_and_map_each_key_to_one_tenant() -> None:
    pairs = ApiKeys.parse(f"acme:{KEY_A}, globex:{KEY_B}")
    as_json = ApiKeys.parse(json.dumps({KEY_A: "acme", KEY_B: "globex"}))
    for keys in (pairs, as_json):
        assert len(keys) == 2
        assert keys.tenant_for(KEY_A) == "acme" and keys.tenant_for(KEY_B) == "globex"
        assert keys.tenant_for("nope") is None and keys.tenant_for(None) is None
        assert keys.tenant_for(KEY_A + "x") is None and keys.tenant_for(KEY_A[:-1]) is None
    assert len(ApiKeys.parse(None)) == 0 and len(ApiKeys.parse("  ")) == 0


@pytest.mark.parametrize(
    "bad",
    [
        "acme:short",
        f"acme:{KEY_A},other:{KEY_A}",
        "no-colon-here",
        f"bad tenant!:{KEY_A}",
        "{not json",
        '["a", "b"]',
        '{"k": 5}',
    ],
)
def test_unusable_keys_are_refused_with_a_clear_error(bad) -> None:
    with pytest.raises(ApiKeyError):
        ApiKeys.parse(bad)


# ------------------------------------------------------------------------------ auth
def test_every_analytic_endpoint_requires_a_valid_key(runtime) -> None:
    body = {"symbol": "SYN-GBM"}

    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            for method, url, kw in (
                ("POST", "/v1/forecast", {"json": body}),
                ("POST", "/v1/calibrate", {"json": body}),
                ("POST", "/v1/risk", {"json": {"positions": {"SYN-GBM": 1.0}}}),
                ("POST", "/v1/backtest", {"json": body}),
                ("GET", "/v1/report/abc", {}),
                ("POST", "/v1/jobs", {"json": {"kind": "forecast", "input": body}}),
                ("GET", "/v1/jobs", {}),
                ("GET", "/v1/jobs/abc", {}),
            ):
                for headers in (
                    {},
                    {"X-API-Key": "wrong-key-0123456789"},
                    {"Authorization": "Bearer nope"},
                ):
                    r = await c.request(method, url, headers=headers, **kw)
                    assert r.status_code == 401, (method, url, headers)
                    assert r.json() == {
                        "error": {
                            "code": "unauthorized",
                            "message": "A valid API key is required.",
                            "details": [],
                        }
                    }

    run(scenario())


def test_both_header_styles_authenticate_and_health_is_open(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            assert (await c.get("/health")).json()["status"] == "ok"
            a = await c.post("/v1/forecast", json={"symbol": "SYN-GBM"}, headers=HDR_A)
            b = await c.post(
                "/v1/forecast",
                json={"symbol": "SYN-GBM"},
                headers={"Authorization": f"Bearer {KEY_A}"},
            )
            assert a.status_code == b.status_code == 200 and a.json() == b.json()

    run(scenario())


def test_an_app_without_keys_or_a_dev_tenant_cannot_be_built(runtime) -> None:
    with pytest.raises(ValueError, match="API keys are required"):
        create_app(runtime, ApiKeys({}), as_of=lambda: AS_OF)
    app = create_app(runtime, ApiKeys({}), as_of=lambda: AS_OF, insecure_dev_tenant="dev")

    async def scenario() -> None:
        async with client(app) as c:
            assert (await c.post("/v1/forecast", json={"symbol": "SYN-GBM"})).status_code == 200

    run(scenario())


# ----------------------------------------------------------------- the trusted as_of
def test_the_server_chooses_as_of_and_the_client_cannot(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            r = await c.post("/v1/forecast", json={"symbol": "SYN-GBM"}, headers=HDR_A)
            assert r.status_code == 200 and r.json()["as_of"] == AS_OF.isoformat()
            smuggled = await c.post(
                "/v1/forecast",
                json={"symbol": "SYN-GBM", "as_of": "2099-01-01T00:00:00+00:00"},
                headers=HDR_A,
            )
            assert smuggled.status_code == 422
            assert smuggled.json()["error"]["code"] == "invalid_request"
            assert "2099" not in smuggled.text  # the offending input is not echoed back

    run(scenario())


def test_the_tenant_comes_from_the_key_never_from_the_request(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            await c.post("/v1/forecast", json={"symbol": "SYN-GBM"}, headers=HDR_A)
            await c.post("/v1/forecast", json={"symbol": "SYN-GBM"}, headers=HDR_B)
            r = await c.post(
                "/v1/forecast?tenant=acme",
                json={"symbol": "SYN-GBM", "tenant": "acme"},
                headers=HDR_B,
            )
            assert r.status_code == 422  # an extra field is refused, not obeyed

    run(scenario())
    assert runtime.verify_audit("acme").ok and runtime.verify_audit("globex").ok
    assert {e["actor"] for e in runtime.audit_events("acme")} >= {"api-reader"}
    assert runtime.verify_audit("globex").records_checked > 0


# ----------------------------------------------------------------------- endpoints
def test_forecast_calibrate_risk_and_backtest_return_their_documented_shapes(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            f = await c.post(
                "/v1/forecast", json={"symbol": "SYN-GBM", "model": "drift"}, headers=HDR_A
            )
            assert f.status_code == 200
            fj = f.json()
            assert fj["calibration_status"] in ("calibrated", "stale", "uncalibrated")
            assert fj["model_mix"] == {"drift": 1.0} and fj["disclaimer"].endswith(
                "Not investment advice."
            )
            cal = await c.post(
                "/v1/calibrate", json={"symbol": "SYN-GARCH", "model": "garch"}, headers=HDR_A
            )
            assert cal.status_code == 200 and cal.json()["coverage"]
            risk = await c.post(
                "/v1/risk",
                json={"positions": {"SYN-GBM": 600000, "SYN-GARCH": 400000}, "n_samples": 500},
                headers=HDR_A,
            )
            assert risk.status_code == 200
            assert risk.json()["portfolio_calibration_status"] == "uncalibrated"
            bt = await c.post(
                "/v1/backtest", json={"symbol": "SYN-GBM", "models": ["drift"]}, headers=HDR_A
            )
            assert bt.status_code == 200 and bt.json()["rows"][0]["model_id"] == "random-walk"

    run(scenario())


def test_bad_input_is_a_422_envelope_that_names_the_field_but_not_the_value(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            r = await c.post("/v1/forecast", json={"symbol": "../etc/passwd"}, headers=HDR_A)
            assert r.status_code == 422
            err = r.json()["error"]
            assert err["code"] == "invalid_request" and any("symbol" in d for d in err["details"])
            assert "passwd" not in r.text
            bad_horizon = await c.post(
                "/v1/forecast", json={"symbol": "SYN-GBM", "horizon": 999}, headers=HDR_A
            )
            assert bad_horizon.status_code == 422
            notjson = await c.post(
                "/v1/forecast",
                content=b"{oops",
                headers={**HDR_A, "content-type": "application/json"},
            )
            assert notjson.status_code == 422

    run(scenario())


def test_an_unknown_symbol_is_a_clean_400_not_a_stack_trace(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            r = await c.post("/v1/forecast", json={"symbol": "NOPE"}, headers=HDR_A)
            assert r.status_code == 400 and r.json()["error"]["code"] == "tool_failed"
            assert "Traceback" not in r.text and "NOPE" not in r.text

    run(scenario())


def test_the_report_endpoint_serves_json_and_a_locked_down_html_page_per_tenant(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            risk = await c.post("/v1/risk", json={"positions": {"SYN-GBM": 1000.0}}, headers=HDR_A)
            rid = risk.json()["report_id"]
            as_json = await c.get(f"/v1/report/{rid}", headers=HDR_A)
            assert as_json.status_code == 200 and as_json.json()["schema_version"] == 1
            page = await c.get(f"/v1/report/{rid}?format=html", headers=HDR_A)
            assert page.status_code == 200 and "Not investment advice" in page.text
            csp = page.headers["content-security-policy"]
            assert (
                "default-src 'none'" in csp
                and "script-src" not in csp
                and "<script" not in page.text.lower()
            )
            assert (
                await c.get(f"/v1/report/{rid}", headers=HDR_B)
            ).status_code == 404  # other tenant
            assert (await c.get("/v1/report/does-not-exist", headers=HDR_A)).status_code == 404
            assert (await c.get("/v1/report/..%2Fetc", headers=HDR_A)).status_code in (404, 422)
            assert (await c.get(f"/v1/report/{rid}?format=exe", headers=HDR_A)).status_code == 422

    run(scenario())


# -------------------------------------------------------------------------- hardening
def test_security_headers_and_body_size_cap(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            r = await c.get("/health")
            assert (
                r.headers["x-content-type-options"] == "nosniff"
                and r.headers["cache-control"] == "no-store"
            )
            big = await c.post("/v1/forecast", content=b"x" * 2_000_000, headers=HDR_A)
            assert big.status_code == 413 and big.json()["error"]["code"] == "payload_too_large"

    run(scenario())


def test_unexpected_errors_never_leak_internals(runtime, monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise RuntimeError("SECRET INTERNAL DETAIL /home/user/.ssh")

    monkeypatch.setattr(runtime, "call", boom)

    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            r = await c.post("/v1/forecast", json={"symbol": "SYN-GBM"}, headers=HDR_A)
            assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
            assert "SECRET" not in r.text and ".ssh" not in r.text

    run(scenario())


def test_unknown_paths_and_methods_use_the_same_envelope(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            assert (await c.get("/nope")).json()["error"]["code"] == "not_found"
            assert (await c.get("/v1/forecast", headers=HDR_A)).json()["error"][
                "code"
            ] == "method_not_allowed"

    run(scenario())


# ------------------------------------------------------------------------------ jobs
def poll(c: httpx.AsyncClient, url: str, headers: dict[str, str], timeout: float = 60.0):
    async def go():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = await c.get(url, headers=headers)
            if r.json()["status"] in ("succeeded", "failed"):
                return r
            await asyncio.sleep(0.1)
        raise AssertionError("the job did not finish")

    return go()


def test_a_job_runs_the_same_governed_call_and_returns_the_same_result(runtime) -> None:
    async def scenario() -> None:
        app = make_app(runtime)
        async with client(app) as c:
            sync = await c.post("/v1/forecast", json={"symbol": "SYN-GBM"}, headers=HDR_A)
            accepted = await c.post(
                "/v1/jobs", json={"kind": "forecast", "input": {"symbol": "SYN-GBM"}}, headers=HDR_A
            )
            assert accepted.status_code == 202
            job = accepted.json()
            assert accepted.headers["location"] == job["status_url"] == f"/v1/jobs/{job['job_id']}"
            done = await poll(c, job["status_url"], HDR_A)
            body = done.json()
            assert body["status"] == "succeeded" and body["kind"] == "forecast"
            assert body["result"] == sync.json()  # same governed call, same answer
            assert (await c.get("/v1/jobs", headers=HDR_A)).json()[0]["job_id"] == job["job_id"]

    run(scenario())


def test_jobs_are_tenant_scoped(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            job = (
                await c.post(
                    "/v1/jobs",
                    json={"kind": "forecast", "input": {"symbol": "SYN-GBM"}},
                    headers=HDR_A,
                )
            ).json()
            assert (await c.get(job["status_url"], headers=HDR_B)).status_code == 404
            assert (await c.get("/v1/jobs", headers=HDR_B)).json() == []
            await poll(c, job["status_url"], HDR_A)

    run(scenario())


def test_a_failed_job_ends_failed_with_a_safe_error(runtime) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            job = (
                await c.post(
                    "/v1/jobs",
                    json={"kind": "forecast", "input": {"symbol": "NOPE"}},
                    headers=HDR_A,
                )
            ).json()
            done = (await poll(c, job["status_url"], HDR_A)).json()
            assert (
                done["status"] == "failed"
                and done["error"]["code"] == "tool_failed"
                and done["result"] is None
            )

    run(scenario())


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "forecast", "input": {"symbol": "../x"}},
        {"kind": "forecast", "input": {"symbol": "SYN-GBM", "as_of": "2099-01-01T00:00:00Z"}},
        {"kind": "wire_money", "input": {}},
        {"kind": "risk", "input": {"positions": {}}},
        {"input": {}},
    ],
)
def test_a_job_with_invalid_input_is_refused_at_submission(runtime, body) -> None:
    async def scenario() -> None:
        async with client(make_app(runtime)) as c:
            r = await c.post("/v1/jobs", json=body, headers=HDR_A)
            assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"

    run(scenario())


def test_a_tenant_cannot_queue_unbounded_jobs(runtime) -> None:
    app = make_app(runtime, jobs=JobManager(max_unfinished_per_tenant=1))
    slow = {"kind": "risk", "input": {"positions": {"SYN-GBM": 1.0, "SYN-GARCH": 1.0}}}

    async def scenario() -> None:
        async with client(app) as c:
            first = await c.post("/v1/jobs", json=slow, headers=HDR_A)
            second = await c.post("/v1/jobs", json=slow, headers=HDR_A)
            assert first.status_code == 202 and second.status_code == 429
            assert second.json()["error"]["code"] == "too_many_jobs"
            other = await c.post(
                "/v1/jobs", json={"kind": "forecast", "input": {"symbol": "SYN-GBM"}}, headers=HDR_B
            )
            assert other.status_code == 202  # another tenant is unaffected
            await app.state.jobs.drain()

    run(scenario())


# ------------------------------------------------------------------------- OpenAPI
def test_the_openapi_document_describes_the_api_and_hides_as_of(runtime) -> None:
    async def scenario() -> dict:
        async with client(make_app(runtime)) as c:
            r = await c.get("/openapi.json")
            assert r.status_code == 200
            return r.json()

    spec = run(scenario())
    assert {
        "/v1/forecast",
        "/v1/calibrate",
        "/v1/risk",
        "/v1/backtest",
        "/v1/jobs",
        "/v1/jobs/{job_id}",
        "/v1/report/{report_id}",
    } <= set(spec["paths"])
    assert "Not investment advice" in spec["info"]["description"]
    assert any(s["type"] == "apiKey" for s in spec["components"]["securitySchemes"].values())
    for name in ("ForecastIn", "CalibrationIn", "RiskIn", "BacktestIn", "JobIn"):
        assert "as_of" not in spec["components"]["schemas"][name]["properties"], name
        assert (
            spec["components"]["schemas"][name].get("additionalProperties") is False
            or name == "JobIn"
        )


# --------------------------------------------------------------------- approvals mount
def test_the_approvals_api_can_be_mounted_beside_the_analytics(runtime) -> None:
    approvals = build_approvals_app(
        runtime, {"approver-token-123456": ApproverToken("alice", "acme")}
    )
    app = make_app(runtime, approvals_app=approvals)

    async def scenario() -> None:
        async with client(app) as c:
            assert (await c.get("/v1/approvals")).status_code == 401
            ok = await c.get(
                "/v1/approvals", headers={"Authorization": "Bearer approver-token-123456"}
            )
            assert ok.status_code == 200 and ok.json() == []
            # an analytics key is not an approver token
            assert (await c.get("/v1/approvals", headers=HDR_A)).status_code == 401

    run(scenario())


# ---------------------------------------------------------------------------- the CLI
def test_the_server_refuses_to_start_without_keys(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TYCHEON_API_KEYS", raising=False)
    assert serve_main([]) == 2
    assert "TYCHEON_API_KEYS" in capsys.readouterr().err


def test_insecure_dev_is_loopback_only(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TYCHEON_API_KEYS", raising=False)
    assert serve_main(["--insecure-dev", "--host", "0.0.0.0"]) == 2  # noqa: S104 - the refusal under test
    assert "loopback" in capsys.readouterr().err


def test_malformed_keys_stop_the_server_with_a_clear_message(monkeypatch, capsys) -> None:
    monkeypatch.setenv("TYCHEON_API_KEYS", "acme:tooshort")
    assert serve_main([]) == 2
    assert "at least 16" in capsys.readouterr().err


def test_a_naive_as_of_is_refused_by_the_cli() -> None:
    with pytest.raises(SystemExit, match="UTC offset"):
        mcp_main(["--as-of", "2023-10-02T14:30:00"])


# ------------------------------------------------------------------------ MCP stdio
def test_the_mcp_server_speaks_the_protocol_over_stdio(tmp_path) -> None:
    """Start the real console entry point and run an MCP session over its stdin and stdout."""
    code = "import sys; from tycheon.serve.cli import mcp_main; sys.exit(mcp_main(sys.argv[1:]))"
    proc = subprocess.Popen(  # noqa: S603 - fixed arguments, the interpreter running this test
        [sys.executable, "-c", code, "--state-dir", str(tmp_path / "mcp")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def rpc(message: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def read(expected_id: int) -> dict:
        assert proc.stdout is not None
        for _ in range(50):
            line = proc.stdout.readline()
            if not line:
                break
            data = json.loads(line)
            if data.get("id") == expected_id:
                return data
        raise AssertionError("no response from the MCP server")

    try:
        rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"}}})  # fmt: skip
        init = read(1)
        assert init["result"]["serverInfo"]["name"] == "tycheon"
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = {t["name"] for t in read(2)["result"]["tools"]}
        assert {"forecast_distribution", "portfolio_risk", "propose_paper_trade"} <= tools
        rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "news_signals", "arguments": {"symbol": "SYN-GBM"}}})  # fmt: skip
        reply = read(3)["result"]
        payload = json.loads(reply["content"][0]["text"])
        assert payload["status"] == "OK" and "untrusted_tool_output" in payload
    finally:
        proc.terminate()
        proc.wait(timeout=20)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


def test_the_default_sample_as_of_is_the_end_of_the_bundled_series() -> None:
    assert sample_end("SYN-GBM") == AS_OF and AS_OF.tzinfo is not None
    assert datetime(2023, 9, 30, tzinfo=UTC) == AS_OF

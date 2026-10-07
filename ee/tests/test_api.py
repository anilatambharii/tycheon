"""The control-plane API end to end, on a real Postgres: accounts, keys, data, metering, gating.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.anyio]

FORECAST = {"symbol": "SYN-GBM", "horizon": 3, "n_samples": 80, "n_origins": 30}


# ----------------------------------------------------------------------------- accounts
async def test_signup_login_me_round_trip(client, signup_org) -> None:
    a = await signup_org()
    me = await client.get("/v1/me", headers=a.session)
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == a.email and body["role"] == "owner"
    assert body["plan"]["key"] == "developer" and body["plan"]["research_use_only"] is True
    login = await client.post(
        "/auth/login", json={"email": a.email.upper(), "password": "correct horse battery"}
    )
    assert login.status_code == 200 and login.json()["org_id"] == a.org_id


async def test_login_failures_are_indistinguishable(client, signup_org) -> None:
    a = await signup_org()
    wrong = await client.post(
        "/auth/login", json={"email": a.email, "password": "wrong password!!"}
    )
    nobody = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "wrong password!!"}
    )
    assert wrong.status_code == nobody.status_code == 401
    assert wrong.json() == nobody.json()


async def test_duplicate_email_or_slug_is_a_conflict_not_a_leak(client, signup_org) -> None:
    a = await signup_org()
    again = await client.post(
        "/auth/signup",
        json={"org_name": "Other", "email": a.email, "password": "correct horse battery"},
    )
    assert again.status_code == 409
    assert "already registered" in again.json()["error"]["message"]


@pytest.mark.parametrize(
    "payload",
    [
        {"org_name": "x", "email": "not-an-email", "password": "correct horse battery"},
        {"org_name": "x", "email": "a@example.com", "password": "short"},
        {"org_name": "", "email": "a@example.com", "password": "correct horse battery"},
        {
            "org_name": "x",
            "slug": "Bad Slug!",
            "email": "a@example.com",
            "password": "correct horse battery",
        },
        {
            "org_name": "x",
            "email": "a@example.com",
            "password": "correct horse battery",
            "role": "owner",
        },
    ],
)
async def test_bad_signups_are_refused(client, payload) -> None:
    res = await client.post("/auth/signup", json=payload)
    assert res.status_code in (401, 422)


async def test_a_tampered_or_missing_session_is_refused(client, signup_org) -> None:
    a = await signup_org()
    for header in (
        {},
        {"Authorization": "Bearer nonsense"},
        {"Authorization": f"Bearer {a.token[:-4]}AAAA"},
    ):
        assert (await client.get("/v1/me", headers=header)).status_code == 401


# -------------------------------------------------------------------------------- keys
async def test_an_api_key_works_and_its_secret_is_shown_only_once(
    client, signup_org, owner
) -> None:
    a = await signup_org()
    token = await a.make_key()
    assert token.startswith("tyk_")
    listing = (await client.get("/v1/api-keys", headers=a.session)).json()
    assert len(listing) == 1 and "key" not in listing[0] and token not in json.dumps(listing)
    stored = await owner.fetchval(
        "SELECT secret_hash FROM api_keys WHERE org_id = $1::uuid", a.org_id
    )
    assert token not in stored and len(stored) == 64
    ok = await client.post("/v1/forecast", json=FORECAST, headers=a.key)
    assert ok.status_code == 200, ok.text


async def test_a_revoked_key_stops_working_immediately(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key()
    key_id = (await client.get("/v1/api-keys", headers=a.session)).json()[0]["id"]
    assert (await client.delete(f"/v1/api-keys/{key_id}", headers=a.session)).status_code == 204
    assert (await client.post("/v1/forecast", json=FORECAST, headers=a.key)).status_code == 401


async def test_forged_and_malformed_keys_are_refused(client, signup_org) -> None:
    a = await signup_org()
    real = await a.make_key()
    forged = real[:-3] + ("AAA" if not real.endswith("AAA") else "BBB")
    for token in (forged, "tyk_aaaaaaaaaaaa_" + "A" * 43, "tyk_short", "nope"):
        res = await client.post("/v1/forecast", json=FORECAST, headers={"X-API-Key": token})
        assert res.status_code == 401


async def test_an_api_key_cannot_manage_the_organisation(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key(("analytics", "mcp"))
    for method, url in (
        ("GET", "/v1/api-keys"),
        ("POST", "/v1/api-keys"),
        ("GET", "/v1/credentials"),
        ("GET", "/v1/data-sources"),
        ("GET", "/v1/audit"),
        ("GET", "/v1/me"),
    ):
        res = await client.request(
            method, url, json={"name": "x"} if method == "POST" else None, headers=a.key
        )
        assert res.status_code == 403, (method, url, res.text)


async def test_scopes_are_enforced(client, signup_org, set_org_plan) -> None:
    a = await signup_org()
    await set_org_plan(a.org_id, "startup")
    await a.make_key(("analytics",))
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert (await client.post("/v1/mcp", json=rpc, headers=a.key)).status_code == 403


async def test_a_viewer_cannot_do_what_an_admin_can(client, signup_org, owner, cp) -> None:
    from tycheon_cp.security import Session, issue_session

    a = await signup_org()
    viewer = issue_session(
        cp.session_secret, Session(a.user_id, a.org_id, "viewer"), ttl_seconds=60
    )
    headers = {"Authorization": f"Bearer {viewer}"}
    assert (await client.get("/v1/usage", headers=headers)).status_code == 200
    assert (
        await client.post("/v1/api-keys", json={"name": "x"}, headers=headers)
    ).status_code == 403
    assert (await client.get("/v1/audit", headers=headers)).status_code == 403


# ---------------------------------------------------------------------------- data sources
async def test_a_csv_upload_is_validated_stored_and_listed(client, signup_org, csv_factory) -> None:
    a = await signup_org()
    uploaded = await a.upload("MYCO", csv_factory())
    assert uploaded["n_rows"] == 400 and uploaded["frequency"] == "1D"
    sources = (await client.get("/v1/data-sources", headers=a.session)).json()
    assert sources[0]["is_default"] is True and sources[0]["files"][0]["symbol"] == "MYCO"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "timestamp,close\n2024-01-01,1\n",  # too few rows and missing columns
        "a,b,c\n1,2,3\n" * 100,  # no timestamp column
        "timestamp,open,high,low,close\n"
        + "\n".join(f"2024-01-{d:02d},nan,1,1,1" for d in range(1, 29)),
    ],
)
async def test_a_bad_file_is_a_clear_422(client, signup_org, bad) -> None:
    a = await signup_org()
    src = (await client.post("/v1/data-sources", json={"name": "s"}, headers=a.session)).json()[
        "id"
    ]
    res = await client.put(f"/v1/data-sources/{src}/files/MYCO", content=bad, headers=a.session)
    assert res.status_code == 422 and res.json()["error"]["code"] == "invalid_data"


async def test_hostile_symbols_cannot_escape_the_tenant_directory(
    client, signup_org, csv_factory
) -> None:
    a = await signup_org()
    src = (await client.post("/v1/data-sources", json={"name": "s"}, headers=a.session)).json()[
        "id"
    ]
    for symbol in ("..", "a..b%2f..%2fx", "%2e%2e", "x" * 40, "a b", "-lead"):
        res = await client.put(
            f"/v1/data-sources/{src}/files/{symbol}", content=csv_factory(), headers=a.session
        )
        assert res.status_code in (404, 405, 422), (symbol, res.status_code)


async def test_the_free_plan_takes_end_of_day_data_only(
    client, signup_org, csv_factory, set_org_plan
) -> None:
    a = await signup_org()
    intraday = csv_factory(n=300, start="2024-01-02 09:30", freq="1h")
    src = (await client.post("/v1/data-sources", json={"name": "s"}, headers=a.session)).json()[
        "id"
    ]
    refused = await client.put(
        f"/v1/data-sources/{src}/files/INTRA", content=intraday, headers=a.session
    )
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "plan_feature"
    await set_org_plan(a.org_id, "enterprise")
    allowed = await client.put(
        f"/v1/data-sources/{src}/files/INTRA", content=intraday, headers=a.session
    )
    assert allowed.status_code == 200 and allowed.json()["frequency"] == "1H"


async def test_an_oversized_upload_is_rejected_before_it_is_read(client, signup_org, cp) -> None:
    a = await signup_org()
    src = (await client.post("/v1/data-sources", json={"name": "s"}, headers=a.session)).json()[
        "id"
    ]
    big = "x" * (cp.settings.max_upload_bytes + 10)
    res = await client.put(f"/v1/data-sources/{src}/files/BIG", content=big, headers=a.session)
    assert res.status_code == 413


# ------------------------------------------------------------- tenant data isolation
async def test_each_tenants_forecast_uses_only_its_own_data(
    client, signup_org, csv_factory
) -> None:
    a, b = await signup_org(), await signup_org()
    await a.upload("MYCO", csv_factory(seed=1, base=100.0))
    await b.upload("MYCO", csv_factory(seed=2, base=5000.0))
    await a.make_key()
    await b.make_key()
    body = {**FORECAST, "symbol": "MYCO", "calibrate": False}
    ra = (await client.post("/v1/forecast", json=body, headers=a.key)).json()
    rb = (await client.post("/v1/forecast", json=body, headers=b.key)).json()
    assert ra["last_close"] != rb["last_close"]
    assert 20 < ra["last_close"] < 600 and 1000 < rb["last_close"] < 100_000


async def test_one_tenants_symbol_does_not_exist_for_another(
    client, signup_org, csv_factory
) -> None:
    a, b = await signup_org(), await signup_org()
    await a.upload("SECRETCO", csv_factory())
    await a.make_key()
    await b.make_key()
    body = {**FORECAST, "symbol": "SECRETCO", "calibrate": False}
    assert (await client.post("/v1/forecast", json=body, headers=a.key)).status_code == 200
    stolen = await client.post("/v1/forecast", json=body, headers=b.key)
    assert stolen.status_code >= 400 and "SECRETCO" not in stolen.text.replace("symbol", "")


async def test_one_tenant_cannot_list_or_upload_into_anothers_source(
    client, signup_org, csv_factory
) -> None:
    a, b = await signup_org(), await signup_org()
    info = await a.upload("MYCO", csv_factory())
    assert (await client.get("/v1/data-sources", headers=b.session)).json() == []
    res = await client.put(
        f"/v1/data-sources/{info['source_id']}/files/PWN", content=csv_factory(), headers=b.session
    )
    assert res.status_code == 404
    assert (
        await client.delete(f"/v1/data-sources/{info['source_id']}", headers=b.session)
    ).status_code == 404
    still = (await client.get("/v1/data-sources", headers=a.session)).json()
    assert [f["symbol"] for f in still[0]["files"]] == ["MYCO"]


async def test_stored_files_live_under_the_owning_org_only(signup_org, csv_factory, cp) -> None:
    a, b = await signup_org(), await signup_org()
    info = await a.upload("MYCO", csv_factory())
    root = cp.blobs.root
    found = list(root.rglob("MYCO.csv"))
    assert len(found) == 1 and a.org_id in found[0].parts and b.org_id not in found[0].parts
    assert info["symbol"] == "MYCO"


# ------------------------------------------------------------------------- credentials
async def test_a_credential_is_sealed_and_never_returned(client, signup_org, owner) -> None:
    a = await signup_org()
    res = await client.post(
        "/v1/credentials",
        json={"name": "vendor", "provider": "polygon", "secret": "pk-live-SUPER-SECRET-value"},
        headers=a.session,
    )
    assert res.status_code == 201 and "SUPER-SECRET" not in res.text
    listing = await client.get("/v1/credentials", headers=a.session)
    assert "SUPER-SECRET" not in listing.text and "ciphertext" not in listing.text
    row = await owner.fetchrow("SELECT * FROM credentials WHERE org_id = $1::uuid", a.org_id)
    blob = b"".join(bytes(row[c]) for c in ("wrapped_dek", "nonce", "ciphertext"))
    assert b"SUPER-SECRET" not in blob and row["kms_key_id"] == "local-dev"


async def test_credentials_are_isolated_between_tenants(client, signup_org, cp, owner) -> None:
    from tycheon_cp import store
    from tycheon_cp.crypto import CryptoError
    from tycheon_cp.errors import ApiProblem

    a, b = await signup_org(), await signup_org()
    made = await client.post(
        "/v1/credentials",
        json={"name": "vendor", "provider": "polygon", "secret": "alpha-secret"},
        headers=a.session,
    )
    cid = made.json()["id"]
    assert (await client.get("/v1/credentials", headers=b.session)).json() == []
    assert (await client.delete(f"/v1/credentials/{cid}", headers=b.session)).status_code == 404
    from uuid import UUID

    # another tenant's context cannot even see the row ...
    with pytest.raises(ApiProblem):
        await store.open_credential(cp.db, cp.kms, UUID(b.org_id), UUID(cid))
    # ... and a row copied into that tenant by a database administrator cannot be opened
    row = await owner.fetchrow("SELECT * FROM credentials WHERE id = $1::uuid", cid)
    await owner.execute(
        "INSERT INTO credentials "
        "(org_id, name, provider, kms_key_id, wrapped_dek, nonce, ciphertext) "
        "VALUES ($1::uuid, 'moved', 'x', $2, $3, $4, $5)",
        b.org_id,
        row["kms_key_id"],
        row["wrapped_dek"],
        row["nonce"],
        row["ciphertext"],
    )
    moved = await owner.fetchval(
        "SELECT id FROM credentials WHERE name = 'moved' AND org_id = $1::uuid", b.org_id
    )
    with pytest.raises(CryptoError):
        await store.open_credential(cp.db, cp.kms, UUID(b.org_id), moved)
    assert await store.open_credential(cp.db, cp.kms, UUID(a.org_id), UUID(cid)) == "alpha-secret"


# ------------------------------------------------------------------------------ metering
async def usage_of(client, tenant, kind="forecast_calls"):
    rows = (await client.get("/v1/usage", headers=tenant.session)).json()["meters"]
    return next(r for r in rows if r["kind"] == kind)


async def test_every_successful_call_is_metered_and_failures_are_not(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key()
    for _ in range(5):
        assert (await client.post("/v1/forecast", json=FORECAST, headers=a.key)).status_code == 200
    for _ in range(3):
        bad = await client.post("/v1/forecast", json={**FORECAST, "symbol": "NOPE"}, headers=a.key)
        assert bad.status_code >= 400
    row = await usage_of(client, a)
    assert row["used"] == 5 and row["limit"] == 1000


async def test_the_same_idempotency_key_is_billed_once(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key()
    headers = {**a.key, "Idempotency-Key": "retry-me-0001"}
    for _ in range(3):
        assert (
            await client.post("/v1/forecast", json=FORECAST, headers=headers)
        ).status_code == 200
    assert (await usage_of(client, a))["used"] == 1


async def test_a_quota_stops_the_api_with_429(client, signup_org, set_org_plan) -> None:
    a = await signup_org()
    await set_org_plan(a.org_id, "developer", {"forecast_calls": 2})
    await a.make_key()
    codes = [
        (await client.post("/v1/forecast", json=FORECAST, headers=a.key)).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 429, 429]
    refused = await client.post("/v1/forecast", json=FORECAST, headers=a.key)
    assert refused.json()["error"]["code"] == "quota_exceeded"
    assert (await usage_of(client, a))["used"] == 2


async def test_usage_is_per_organisation(client, signup_org) -> None:
    a, b = await signup_org(), await signup_org()
    await a.make_key()
    await b.make_key()
    for _ in range(3):
        await client.post("/v1/forecast", json=FORECAST, headers=a.key)
    await client.post("/v1/forecast", json=FORECAST, headers=b.key)
    assert (await usage_of(client, a))["used"] == 3 and (await usage_of(client, b))["used"] == 1


async def test_risk_reports_and_backtests_are_metered_on_their_own_meters(
    client, signup_org
) -> None:
    a = await signup_org()
    await a.make_key()
    risk = await client.post(
        "/v1/risk",
        json={"positions": {"SYN-GBM": 100000.0}, "horizon": 3, "n_samples": 100, "n_origins": 20},
        headers=a.key,
    )
    assert risk.status_code == 200, risk.text
    assert (await usage_of(client, a, "risk_reports"))["used"] == 1
    assert (await usage_of(client, a))["used"] == 0
    bt = await client.post(
        "/v1/backtest",
        json={"symbol": "SYN-GBM", "horizon": 3, "folds": 1, "test_window": 20},
        headers=a.key,
    )
    assert bt.status_code == 200, bt.text
    assert (await usage_of(client, a, "backtest_compute_seconds"))["used"] >= 1


# ------------------------------------------------------------------------- plan gating
async def test_plan_features_gate_calibration_reports_and_mcp(
    client, signup_org, set_org_plan
) -> None:
    a = await signup_org()
    await a.make_key(("analytics", "mcp"))
    calibrate = {"symbol": "SYN-GBM", "horizon": 3, "n_origins": 30, "n_samples": 80}
    refused = await client.post("/v1/calibrate", json=calibrate, headers=a.key)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "plan_feature"
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert (await client.post("/v1/mcp", json=rpc, headers=a.key)).status_code == 403
    await set_org_plan(a.org_id, "startup")
    assert (await client.post("/v1/calibrate", json=calibrate, headers=a.key)).status_code == 200
    assert (await client.post("/v1/mcp", json=rpc, headers=a.key)).status_code == 200


async def test_a_lapsed_subscription_falls_back_to_the_free_plan(client, signup_org, owner) -> None:
    a = await signup_org()
    await owner.execute(
        "UPDATE subscriptions SET plan = 'startup', status = 'canceled' WHERE org_id = $1::uuid",
        a.org_id,
    )
    assert (await client.get("/v1/me", headers=a.session)).json()["plan"]["key"] == "developer"


async def test_the_rate_limit_applies_per_organisation(client, signup_org, cp) -> None:
    from tycheon_cp.ratelimit import RateLimiter

    a, b = await signup_org(), await signup_org()
    await a.make_key()
    await b.make_key()
    cp.limiter = cp.gateway.limiter = RateLimiter(clock=lambda: 0.0)
    ok = 0
    for _ in range(40):  # the free plan allows 30 a minute
        res = await client.post("/v1/forecast", json={**FORECAST, "symbol": "NOPE"}, headers=a.key)
        if res.status_code != 429:
            ok += 1
    assert ok == 30
    limited = await client.post("/v1/forecast", json=FORECAST, headers=a.key)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    assert (await client.post("/v1/forecast", json=FORECAST, headers=b.key)).status_code == 200


# --------------------------------------------------------------------------------- MCP
async def rpc(client, tenant, method, params=None, id_=1):
    res = await client.post(
        "/v1/mcp",
        json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
        headers=tenant.key,
    )
    return res


async def test_mcp_lists_tools_and_runs_them_metered(client, signup_org, set_org_plan) -> None:
    a = await signup_org()
    await set_org_plan(a.org_id, "startup")
    await a.make_key(("analytics", "mcp"))
    init = (await rpc(client, a, "initialize", {})).json()["result"]
    assert init["serverInfo"]["name"] == "tycheon-cloud" and "tools" in init["capabilities"]
    tools = (await rpc(client, a, "tools/list")).json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"forecast", "calibrate", "risk", "backtest"}
    assert all("Not investment advice" in t["description"] for t in tools)
    called = (
        await rpc(client, a, "tools/call", {"name": "forecast", "arguments": FORECAST})
    ).json()["result"]
    assert called["isError"] is False
    assert json.loads(called["content"][0]["text"])["symbol"] == "SYN-GBM"
    assert (await usage_of(client, a))["used"] == 1


async def test_mcp_refusals_are_tool_errors_and_never_billed(
    client, signup_org, set_org_plan
) -> None:
    a = await signup_org()
    await set_org_plan(a.org_id, "startup")
    await a.make_key(("analytics", "mcp"))
    for params in (
        {
            "name": "forecast",
            "arguments": {"symbol": "SYN-GBM", "as_of": "2030-01-01"},
        },  # smuggled as_of
        {"name": "forecast", "arguments": {"symbol": "NOPE"}},
        {"name": "does-not-exist", "arguments": {}},
    ):
        res = (await rpc(client, a, "tools/call", params)).json()["result"]
        assert res["isError"] is True
    assert (await usage_of(client, a))["used"] == 0
    assert (await rpc(client, a, "bogus/method")).json()["error"]["code"] == -32601
    garbage = await client.post("/v1/mcp", content="{not json", headers=a.key)
    assert garbage.status_code == 400


# ------------------------------------------------------------------------- governance
async def test_the_client_cannot_choose_as_of_or_the_tenant(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key()
    for extra in (
        {"as_of": "2030-01-01T00:00:00Z"},
        {"tenant_id": "someone-else"},
        {"org_id": "x"},
    ):
        res = await client.post("/v1/forecast", json={**FORECAST, **extra}, headers=a.key)
        assert res.status_code == 422


async def test_every_call_is_a_governed_audited_tool_call_for_that_tenant(
    client, signup_org, cp
) -> None:
    a, b = await signup_org(), await signup_org()
    await a.make_key()
    assert not cp.runtime.audit_events(a.org_id)
    assert (await client.post("/v1/forecast", json=FORECAST, headers=a.key)).status_code == 200
    mine = cp.runtime.audit_events(a.org_id)
    assert mine and any("tool" in e["event"] for e in mine)
    assert cp.runtime.verify_audit(a.org_id).ok
    assert not cp.runtime.audit_events(b.org_id)  # another tenant's chain is separate and empty


async def test_errors_never_leak_internals(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key()
    res = await client.post("/v1/forecast", json={**FORECAST, "symbol": "NOPE"}, headers=a.key)
    text = res.text
    for leak in ("Traceback", "asyncpg", 'File "', "SELECT "):
        assert leak not in text
    assert set(res.json()) == {"error"}


async def test_audit_log_records_management_actions(client, signup_org) -> None:
    a = await signup_org()
    await a.make_key()
    await client.post(
        "/v1/credentials", json={"name": "v", "provider": "p", "secret": "s"}, headers=a.session
    )
    log = (await client.get("/v1/audit", headers=a.session)).json()
    actions = {e["action"] for e in log}
    assert {"org.signup", "api_key.create", "credential.create"} <= actions
    assert "secret" not in json.dumps(log).replace("credential", "")

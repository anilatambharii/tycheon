# Serving: REST and MCP

**For research and risk analytics. Not investment advice.**

Needs the `serve` extra. Both surfaces run the **same governed tools** as the agents (capability
grant, policy, audit); there is no second code path to the analytics.

## REST API

```bash
export TYCHEON_API_KEYS=...        # tenant:key pairs, keys of 16+ characters
tycheon-serve --port 8080          # refuses to start without keys
tycheon-serve --insecure-dev       # local development only: no auth, loopback only
```

OpenAPI is at `/docs` and `/openapi.json`.

| Endpoint | What it does |
|---|---|
| `POST /v1/forecast` | A calibrated forecast distribution for one symbol |
| `POST /v1/calibrate` | Raw versus calibrated coverage for a model on a series |
| `POST /v1/risk` | VaR, ES, drawdown, volatility, stress; returns a `report_id` |
| `GET /v1/report/{id}?format=json\|html` | The full report (HTML is self-contained, served with a strict CSP) |
| `POST /v1/backtest` | Walk-forward evaluation against the random walk, with Diebold-Mariano |
| `POST /v1/jobs`, `GET /v1/jobs/{id}`, `GET /v1/jobs` | The same analytics as background jobs |
| `/v1/approvals` | Keelgate's approvals API, when `TYCHEON_APPROVER_TOKENS` is set |
| `GET /health` | Liveness (no auth) |

### Security properties

- **The tenant comes from the API key**, never from the request. A key belongs to one tenant;
  jobs, reports, approvals, audit chains and paper books are all scoped to it.
- **`as_of` is not a request field.** The server chooses the date (`--as-of`, or the end of the
  bundled sample series, or now for your own data) and every response states it. Sending an `as_of`
  is a 422.
- Inputs are bounded and unknown fields are rejected; the body is capped at 1 MB; unexpected
  errors return a fixed envelope with no stack trace and no echoed input.
- Keys are compared in constant time and never logged. Access logging is off by default.
- The server refuses to run `--insecure-dev` on a non-loopback address.

Not built: rate limiting (put the API behind a proxy that does it), TLS (terminate it at the
proxy), and persistent jobs (they live in memory).

## MCP

```bash
tycheon-mcp        # a client launches this and speaks MCP over stdin/stdout
```

Exposes the same nine tools to Claude, Cursor, ChatGPT-style clients, or any MCP client. The MCP
principal can read analytics and *propose* a paper trade, which always waits for a human. It cannot
save reports.

**stdio only.** Keelgate's HTTP transport shares one grant across every caller and gives the
per-request hook no caller identity, which would make the server a confused deputy on a network.
Tycheon does not expose it over HTTP until that is fixed upstream.

Tool output is labelled as untrusted data in the text the client receives.

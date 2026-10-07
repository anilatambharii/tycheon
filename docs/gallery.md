# Examples gallery

**For research and risk analytics. Not investment advice.**

Four things you can run. Every script prints the disclaimer, uses bundled **synthetic** data unless
you point it at your own licensed files, and says plainly what has and has not been verified.
Nothing here is a trading strategy.

| Example | File | Verified how |
|---|---|---|
| [MCP in Claude Desktop and Cursor](#mcp-in-claude-desktop-and-cursor) | [`examples/mcp_smoke.py`](https://github.com/anilatambharii/tycheon/blob/main/examples/mcp_smoke.py) | Handshake run for real (output below); the Claude Desktop and Cursor configs were **not** loaded into those apps by us |
| [Portfolio risk report](#portfolio-risk-report) | [`examples/risk_report.py`](https://github.com/anilatambharii/tycheon/blob/main/examples/risk_report.py) | Run for real (output below) |
| [Fine-tune on your own data](#fine-tune-on-your-own-data-tycheon-cloud) | [`examples/cloud_finetune.py`](https://github.com/anilatambharii/tycheon/blob/main/examples/cloud_finetune.py) | **Not verified against a live service**; lint-checked, and its stop paths were exercised against a local stand-in |
| Forecast beside the random walk | [`examples/forecast.py`](https://github.com/anilatambharii/tycheon/blob/main/examples/forecast.py) | See the [calibrated Kronos tutorial](tutorials/calibrated-kronos.md) |

## MCP in Claude Desktop and Cursor

`tycheon-mcp` is a console script that speaks MCP over **stdio**: the client launches it and talks over
stdin/stdout. It exposes nine governed tools (forecast, calibration report, portfolio risk, backtest
summary, news and fundamentals snapshots, saved reports, and a *paper* trade proposal that always
waits for a human). It cannot place real trades and it is not exposed over HTTP; see
[serving](serving.md).

The `serve` extra depends on Keelgate, which this repository pins to a commit, so the setup below runs
from a clone of the repository (the path we test):

```bash
git clone https://github.com/anilatambharii/tycheon.git
cd tycheon
uv sync --extra serve
uv run tycheon-mcp --help          # prints usage: --state-dir, --tenant, --as-of, --data-dir
uv run --extra serve python examples/mcp_smoke.py
```

Replace `/ABSOLUTE/PATH/TO/tycheon` below with your clone (on Windows use forward slashes, for
example `C:/Users/you/tycheon`). `--state-dir` matters because a desktop app starts the server in
an arbitrary working directory; without it the server writes `.tycheon-state` there.

### Claude Desktop

Edit `claude_desktop_config.json` (Settings, Developer, Edit Config; on macOS
`~/Library/Application Support/Claude/`, on Windows `%APPDATA%\Claude\`) and restart the app:

```json
{
  "mcpServers": {
    "tycheon": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/ABSOLUTE/PATH/TO/tycheon",
        "--extra",
        "serve",
        "tycheon-mcp",
        "--state-dir",
        "/ABSOLUTE/PATH/TO/tycheon-state"
      ]
    }
  }
}
```

### Cursor

Put the same server in `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project). Cursor's
file uses the same `mcpServers` shape:

```json
{
  "mcpServers": {
    "tycheon": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/ABSOLUTE/PATH/TO/tycheon",
        "--extra",
        "serve",
        "tycheon-mcp",
        "--state-dir",
        "/ABSOLUTE/PATH/TO/tycheon-state"
      ]
    }
  }
}
```

If `uv` is not on the PATH the app sees, give its absolute path as `command`. Both file locations and
the `mcpServers` shape come from each vendor's documentation as we understand it; **we did not load
these files into Claude Desktop or Cursor**. What we did run is the exact `uv run --directory ... tycheon-mcp`
command below, driven by the official `mcp` client SDK, which performs the same `initialize` and
`tools/list` handshake those apps do.

### The handshake, run for real

`examples/mcp_smoke.py` starts the console script, calls `initialize` and `tools/list`, calls no tool,
and exits non-zero if either fails. Output on the commit this page shipped with (Windows, Python 3.11,
`mcp` 2.3.0):

```text
For research and risk analytics. Not investment advice.
initialize ok: server=tycheon 0.1.0
  protocol version: 2025-11-25
tools/list ok: 9 tools
  - backtest_summary: Walk-forward evaluation of baselines against the random walk.
  - calibration_report: Raw versus calibrated interval coverage for a model on one series.
  - forecast_distribution: Forecast distribution for one symbol, calibrated when history allows.
  - fundamentals_snapshot: Latest fundamentals published by the review date (restatements do not leak).
  - news_signals: Numeric sentiment signals from untrusted news published by the review date.
  - portfolio_risk: VaR, Expected Shortfall, drawdown, volatility and stress for a long-only portfolio.
  - propose_paper_trade: Propose a PAPER trade; it runs only after a human approves it. Never real money.
  - risk_report: The full JSON and HTML report behind an earlier portfolio_risk result (same tenant).
  - save_report: Save a verified report as Markdown under the tenant's report directory.
For research and risk analytics. Not investment advice.
```

(The `0.1.0` is the server version string the MCP adapter reports; it is not the Tycheon package
version.) Tool output is labelled as untrusted data in the text the client receives. With no
`--data-dir`, the server answers about the bundled synthetic series, with `as_of` fixed by the
server, not by the model or the client.

## Portfolio risk report

[`examples/risk_report.py`](https://github.com/anilatambharii/tycheon/blob/main/examples/risk_report.py)
forecasts three synthetic assets, calibrates each with adaptive conformal prediction on a holdout,
aggregates them with a correlation estimated from data known at `as_of`, and writes
`risk_report.json` and a self-contained `risk_report.html`.

```bash
uv sync --extra report
uv run python examples/risk_report.py          # about 30 seconds on a laptop CPU
```

Real output (the three `summary()` lines are shortened here; the script prints each in full):

```text
SYN-GBM: scoring candidates, routing, calibrating ...
  regime-ensemble [calibrated, holdout 44% at nominal 50%, sample paths] as_of 2023-09-30T00:00:00+00:00: last close 149.4; step 10 median 149.3, 90% interval [139.2, 161.4]. ...
SYN-GARCH: scoring candidates, routing, calibrating ...
  regime-ensemble [calibrated, holdout 44% at nominal 50%, sample paths] as_of 2023-09-30T00:00:00+00:00: last close 90.81; step 10 median 91.95, 90% interval [85.91, 99.18]. ...
SYN-REGIME: scoring candidates, routing, calibrating ...
  regime-ensemble [calibrated, holdout 39% at nominal 50%, sample paths] as_of 2023-09-30T00:00:00+00:00: last close 75.46; step 10 median 74.65, 90% interval [68.01, 84.46]. ...
wrote examples/output/risk_report.json and examples/output/risk_report.html
  warning: the portfolio is UNCALIBRATED as a whole: dependence between assets is assumed (gaussian-copula (rank coupling of end-of-horizon returns)), not measured; the lower tail is likely understated.
  warning: the model-implied tail scenario is not calibrated; it shows what the model considers a bad outcome, not a measured probability.
```

From that run's `risk_report.json` (a 10-bar horizon, 1,000 paths, a USD 1,000,000 synthetic portfolio):
`calibration_status` is `uncalibrated`, 95% VaR is 4.45% of portfolio value and 95% Expected Shortfall is
5.99% (standard errors 0.33% and 0.31%). The two things to read first are the warnings: the per-asset
status is `calibrated` only against a 20-origin holdout, and the portfolio-level number is always
labelled `uncalibrated` because dependence is assumed. These figures describe synthetic series and say
nothing about any real portfolio. Add `--kronos mini` (needs `uv sync --extra kronos`) to put Kronos
among the candidates; the router decides how much to trust it.

## Fine-tune on your own data (Tycheon Cloud)

!!! warning "Unverified against a live service"
    [`examples/cloud_finetune.py`](https://github.com/anilatambharii/tycheon/blob/main/examples/cloud_finetune.py)
    was written from the source of the Tycheon Cloud control plane and fine-tuning routes and has
    **not** been run against a live backend. It is syntax- and lint-checked, and its "plan lacks
    fine-tuning", "missing environment" and "server unreachable" paths were exercised against a local
    stand-in HTTP server that answers only `GET /v1/me`. Everything after that point is untested.

[Tycheon Cloud](cloud.md) (proprietary, in `ee/`) can fine-tune Kronos on bars you upload. The Enterprise
plan includes it; the other plans do not, and the script checks `GET /v1/me` first and **stops with an
explanation** if `finetune` is not among your plan's features, before uploading anything.

```bash
pip install httpx
export TYCHEON_CLOUD_URL=https://your-tycheon-cloud-host
export TYCHEON_CLOUD_SESSION=...          # session token from POST /auth/login; an admin role is needed
python examples/cloud_finetune.py --csv-dir my_csvs --symbols MYSYM
```

The flow, with the endpoints it calls:

1. `GET /v1/me` to read the plan.
2. `POST /v1/data-sources` (`kind: csv`), then `PUT /v1/data-sources/{id}/files/{SYMBOL}` per CSV.
   A file needs a timestamp column and at least 60 bars, in increasing order.
3. `POST /v1/finetune/jobs` (answers 202), then poll `GET /v1/finetune/jobs/{id}` until `succeeded`,
   `failed` or `cancelled`.
4. `GET /v1/models/{id}`: print the promotion gate.

A fine-tuned model is a **candidate**. It becomes servable (`model="ft:<id>"`) only if it passes the
promotion gate on your own walk-forward test region: clearly lower CRPS than the random walk, a
significant Diebold-Mariano test, and no worse than the base model. If it fails, the script says so and
shows the reasons; that is a normal and honest outcome, not an error. If you also set
`TYCHEON_CLOUD_API_KEY` (a `tyk_...` data-plane key) and the model was promoted, it requests one forecast
with `ft:<id>`.

Your CSV files stay under your own data licence; Tycheon does not redistribute them.

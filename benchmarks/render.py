"""Render published benchmark results into a static leaderboard page for the docs.

The page is generated from the versioned JSON and nothing else: no number is typed by hand.
It always shows the random-walk row, Diebold-Mariano p-values, the models that could not run
and why, and a plain summary of where the baselines win, because a leaderboard that only shows
wins is advertising.

    python -m benchmarks.render
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
DEFAULT_OUT = ROOT.parent / "docs" / "leaderboard" / "index.md"
RANDOM_WALK = "random-walk"
DISCLAIMER = "For research and risk analytics. Not investment advice."

VERDICT_SHORT = {
    "benchmark": "baseline",
    "beats the random walk": "beats RW",
    "indistinguishable from the random walk": "= RW",
    "worse than the random walk": "worse than RW",
    "not tested": "n/a",
}


def _esc(value: object) -> str:
    return html.escape(str(value), quote=False).replace("|", "\\|")


def _num(x: float | None, digits: int = 3) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}f}"


def _pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:.{digits}f}%"


def _p(x: float | None) -> str:
    if x is None:
        return "n/a"
    return "<0.001" if x < 0.001 else f"{x:.3f}"


def latest_results(results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    """The newest result document of each benchmark, ordered by name."""
    newest: dict[str, dict[str, Any]] = {}
    for path in sorted(results_dir.glob("*/*.json")):
        if path.parent.name == "local":
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        name = doc["benchmark"]
        if name not in newest or doc["created_at"] > newest[name]["created_at"]:
            newest[name] = doc
    return [newest[k] for k in sorted(newest)]


def _order(doc: dict[str, Any]) -> list[str]:
    cfg = doc["config"]
    ids = [RANDOM_WALK, *[b for b in cfg["baselines"] if b != RANDOM_WALK], *cfg["models"]]
    return ids


def _main_table(models: dict[str, Any], order: list[str]) -> str:
    rows = [
        "| Model | vs random walk | MASE | RMSE (%) | CRPS (%) | CRPS skill | Direction | IC "
        "| DM p, sq. error | DM p, CRPS |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    rw_crps = models[RANDOM_WALK]["pooled"]["crps"]
    for mid in order:
        if mid not in models:
            continue
        m = models[mid]["pooled"]
        dm = m["diebold_mariano_vs_random_walk"]
        bench = m["is_benchmark"]
        skill = None if bench else 1 - m["crps"] / rw_crps
        approx = "\\*" if m["crps_approximate"] else ""
        name = f"**{_esc(mid)}** (baseline)" if bench else _esc(mid)
        rows.append(
            f"| {name} | {_esc(VERDICT_SHORT[m['verdict']])} | {_num(m['mase'])} "
            f"| {_num(100 * m['rmse'], 3)} | {_num(100 * m['crps'], 3)}{approx} "
            f"| {'-' if skill is None else _pct(skill)} | {_pct(m['directional_accuracy'])} "
            f"| {_num(m['ic'], 3)} "
            f"| {'-' if bench else _p(dm['squared_error']['p_model_better'])} "
            f"| {'-' if bench else _p(dm['crps']['p_model_better'])} |"
        )
    return "\n".join(rows)


def _calibration_table(models: dict[str, Any], order: list[str]) -> str:
    rows = [
        "| Model | 50% interval | 80% interval | 90% interval | 90% width (log %) |",
        "|---|--:|--:|--:|--:|",
    ]
    for mid in order:
        if mid not in models:
            continue
        cov = models[mid]["pooled"]["coverage"]

        def cell(level: str, cov: dict[str, Any] = cov) -> str:
            got = cov.get(level)
            return "n/a" if got is None else _pct(got["achieved"])

        width = cov.get("0.9", {}).get("mean_width")
        rows.append(
            f"| {_esc(mid)} | {cell('0.5')} | {cell('0.8')} | {cell('0.9')} "
            f"| {'n/a' if width is None else _num(100 * width, 2)} |"
        )
    return "\n".join(rows)


def _series_table(models: dict[str, Any], order: list[str], series: list[str]) -> str:
    rows = [
        "| Model | " + " | ".join(_esc(s) for s in series) + " |",
        "|---|" + "--:|" * len(series),
    ]
    for mid in order:
        if mid not in models or mid == RANDOM_WALK:
            continue
        cells = []
        for s in series:
            e = models[mid]["by_series"][s]
            cells.append(f"{_num(e['mase'])} ({_esc(VERDICT_SHORT[e['verdict']])})")
        rows.append(f"| {_esc(mid)} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _strategy_table(models: dict[str, Any], order: list[str]) -> str | None:
    if not all("strategy" in models[m] for m in order if m in models):
        return None
    rows = [
        "| Model | gross return / period | net of costs | turnover / period | hit rate |",
        "|---|--:|--:|--:|--:|",
    ]
    for mid in order:
        if mid not in models:
            continue
        s = models[mid]["strategy"]
        rows.append(
            f"| {_esc(mid)} | {_pct(s['gross_mean'], 3)} | {_pct(s['net_mean'], 3)} "
            f"| {_num(s['turnover_per_period'], 2)} | {_pct(s['hit_rate'])} |"
        )
    return "\n".join(rows)


def where_baselines_win(models: dict[str, Any], baselines: set[str]) -> list[str]:
    """Plain statements about the metrics a baseline leads, and how the verdicts split."""
    lines: list[str] = []
    others = [m for m in models if m not in baselines]
    verdicts = [models[m]["pooled"]["verdict"] for m in others]
    if others:
        beat = verdicts.count("beats the random walk")
        worse = verdicts.count("worse than the random walk")
        same = len(others) - beat - worse
        lines.append(
            f"Of {len(others)} non-baseline models, {beat} beat the random walk "
            f"(both Diebold-Mariano tests, one-sided p < 0.05), {same} were statistically "
            f"indistinguishable from it and {worse} were worse."
        )
    metrics = [
        ("MASE", lambda p: p["mase"], min),
        ("RMSE", lambda p: p["rmse"], min),
        ("CRPS", lambda p: p["crps"], min),
        ("directional accuracy", lambda p: p["directional_accuracy"], max),
    ]
    for label, getter, pick in metrics:
        scored = {
            m: getter(models[m]["pooled"])
            for m in models
            if getter(models[m]["pooled"]) is not None
            and not math.isnan(getter(models[m]["pooled"]))
        }
        if not scored:
            continue
        best = pick(scored, key=lambda k: scored[k])
        if best in baselines:
            lines.append(f"Best {label}: **{best}**, a baseline.")
    if not lines[1:]:
        lines.append("No baseline leads any headline metric in this run.")
    return lines


def _section(doc: dict[str, Any]) -> str:
    cfg = doc["config"]
    series = [s["symbol"] for s in doc["dataset"]["series"]]
    order = _order(doc)
    baselines = set(cfg["baselines"])
    env = doc["environment"]
    out = [
        f"## Benchmark: {_esc(doc['benchmark'])}",
        "",
        f"{_esc(cfg.get('description', '')).strip()}",
        "",
        "| | |",
        "|---|---|",
        f"| Tycheon version | {_esc(doc['tycheon_version'])} |",
        f"| Run | {_esc(doc['created_at'])}, commit `{_esc((doc['git']['sha'] or 'unknown')[:10])}`"
        f"{' (uncommitted changes)' if doc['git']['dirty'] else ''} |",
        f"| Dataset | {_esc(doc['dataset']['name'])} "
        f"({'synthetic' if doc['dataset']['synthetic'] else 'real, user-licensed'}), "
        f"{len(series)} series |",
        f"| Config / manifest hash | `{doc['config_sha256'][:12]}` / "
        f"`{doc['dataset']['manifest_sha256'][:12]}` |",
        f"| Environment | Python {_esc(env['python'])}, {_esc(env['device'])}, "
        f"{_esc(env['platform'])}, torch {_esc(env.get('torch'))} |",
        f"| Runtime | {doc['runtime_seconds']} s |",
        "",
    ]
    for w in doc["warnings"]:
        out += [f"!!! warning\n    {_esc(w)}", ""]
    if doc["dataset"]["synthetic"]:
        out += [
            '!!! note "Synthetic data"',
            "    These series are generated, not market data. A result here shows the machinery "
            "works and how models behave on known processes. It says nothing about markets.",
            "",
        ]

    for block in doc["results"]:
        models = block["models"]
        h = block["horizon"]
        out += [
            f"### Horizon {h} bars",
            "",
            f"{block['n_origins_per_series']} forecast origins per series, pooled across "
            f"{len(series)} series, non-overlapping. MASE is the model's MAE over the random "
            "walk's MAE on the same origins (below 1 beats it). CRPS skill is 1 minus the ratio "
            "to the random walk's CRPS. DM p is the one-sided Diebold-Mariano p-value that the "
            "model has lower loss than the random walk (HLN-corrected). \\* CRPS approximated "
            "from the quantile grid (the model has no sample paths).",
            "",
            _main_table(models, order),
            "",
            "**Where the baselines win**",
            "",
            *[f"- {line}" for line in where_baselines_win(models, baselines)],
            "",
            "**Interval coverage** (achieved share of outcomes inside the central interval; "
            "closer to nominal is better, narrower is better at equal coverage)",
            "",
            _calibration_table(models, order),
            "",
            "**Per series** (MASE and verdict)",
            "",
            _series_table(models, order, series),
            "",
        ]
        strat = _strategy_table(models, order)
        if strat:
            out += [
                "**Cost-aware diagnostic** (naive one-horizon sign strategy with spread and "
                "slippage; a research diagnostic, not a recommendation)",
                "",
                strat,
                "",
            ]

    skipped = {m: s for m, s in doc["model_status"].items() if s["status"] != "ran"}
    if skipped:
        out += ["### Models that did not run", "", "| Model | Status | Reason |", "|---|---|---|"]
        out += [f"| {_esc(m)} | {s['status']} | {_esc(s['reason'])} |" for m, s in skipped.items()]
        out.append("")
    out += [
        "Reproduce: "
        f"`python -m benchmarks.run --config benchmarks/configs/{_esc(doc['benchmark'])}.yaml "
        "--execute`. The raw document is "
        f"`benchmarks/results/{_esc(doc['benchmark'])}/{_esc(doc['tycheon_version'])}.json`.",
        "",
    ]
    return "\n".join(out)


def render_leaderboard(results_dir: Path = RESULTS_DIR, out: Path = DEFAULT_OUT) -> Path:
    docs = latest_results(results_dir)
    parts = [
        "# Leaderboard",
        "",
        f"**{DISCLAIMER}**",
        "",
        "Every model is scored by the same leakage-guarded walk-forward engine at the same "
        "origins and read against the **random walk**, with a Diebold-Mariano test. Results "
        "are published whichever way they fall; see the [methodology](../benchmark-methodology.md) "
        "for the leakage controls, the baselines and the limits of what this shows.",
        "",
        "*This page is generated by `python -m benchmarks.render` from the versioned JSON in "
        "`benchmarks/results/`. No number on it is typed by hand.*",
        "",
    ]
    if not docs:
        parts.append("No published results yet. Run `make benchmark-small`.")
    parts += [_section(d) for d in docs]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts).rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return out


if __name__ == "__main__":
    print(render_leaderboard())

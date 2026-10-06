"""Deterministic stand-ins for a language model, for CI, tests and the offline example.

A real model plans and drafts; in CI a script must. These helpers write a plan as JSON and a
draft *from the evidence digest in the prompt*, so the draft contains the real numbers the tools
returned. With ``plant_error`` the draft states one wrong number, which is how the verifier's
rejection and the revise loop are demonstrated and tested without a model.

Nothing here is used outside tests and the example, and nothing here is intelligent: it formats
numbers it is shown.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tycheon.models.base import DISCLAIMER

_LINE = re.compile(
    r"^\[(?P<id>E\d+)\] (?P<kind>\w+)(?: (?P<symbol>[\w.\-]+))?(?: \((?P<status>\w+)\))?: "
    r"(?P<body>.*?)(?: flags=(?P<flags>\S+))?$"
)


def plan_reply(symbols: list[str], *, trade: dict[str, Any] | None = None) -> str:
    """The JSON a well-behaved planning model would return for ``symbols``."""
    steps: list[dict[str, Any]] = []
    for symbol in symbols:
        steps += [
            {"kind": "forecast", "symbol": symbol, "model": "random-walk"},
            {"kind": "news", "symbol": symbol},
            {"kind": "fundamentals", "symbol": symbol},
        ]
    steps.append({"kind": "risk"})
    body: dict[str, Any] = {
        "steps": steps,
        "budget": {"max_tool_calls": 16, "max_tokens": 24000, "max_revisions": 2},
        "trade": trade,
        "rationale": "forecast, news and fundamentals per holding, then portfolio risk",
    }
    return json.dumps(body)


def parse_digest(prompt: str) -> list[dict[str, Any]]:
    """Evidence entries (id, kind, symbol, status, numbers, flags) parsed from a writer prompt."""
    entries = []
    for raw in prompt.splitlines():
        match = _LINE.match(raw.strip())
        if not match:
            continue
        numbers: dict[str, float] = {}
        for part in match.group("body").split("; "):
            key, sep, value = part.partition("=")
            if sep:
                try:
                    numbers[key] = float(value)
                except ValueError:
                    continue
        entries.append(
            {
                "id": match.group("id"),
                "kind": match.group("kind"),
                "symbol": match.group("symbol"),
                "status": match.group("status"),
                "numbers": numbers,
                "flags": (match.group("flags") or "").split(","),
            }
        )
    return entries


def _pct(x: float, wrong: bool = False) -> str:
    return f"{x * 100 * (1.37 if wrong else 1.0):.2f}%"


def _sentences(entry: dict[str, Any], *, wrong: bool) -> list[str]:
    n, cid = entry["numbers"], entry["id"]
    status, symbol = entry["status"], entry["symbol"] or "the portfolio"
    out: list[str] = []
    if entry["kind"] == "forecast" and "horizon_end.median" in n:
        word = "calibrated" if status == "calibrated" else f"{status}"
        out.append(
            f"For {symbol}, the {word} median forecast at the horizon is "
            f"{n['horizon_end.median']:.2f}, with a 90% interval from "
            f"{n['horizon_end.lower_90']:.2f} to {n['horizon_end.upper_90']:.2f} [{cid}]."
        )
    elif entry["kind"] == "risk" and "var_es[0].loss_fraction" in n:
        var95 = n["var_es[0].loss_fraction"]
        out.append(
            f"The portfolio's 95% VaR is {_pct(var95, wrong)} of its value and the 95% ES is "
            f"{_pct(n['var_es[1].loss_fraction'])} [{cid}]."
        )
        if status in ("uncalibrated", "stale"):
            out.append(
                f"This portfolio result is {status}: dependence between the assets is assumed, "
                f"not measured, so the tail is likely understated [{cid}]."
            )
        if "unreliable_tail" in entry["flags"]:
            out.append(f"The 99% tail estimates rest on few paths and are not reliable [{cid}].")
    elif entry["kind"] == "news":
        used = int(n.get("n_documents_used", 0))
        if "mean_sentiment" in n:
            out.append(
                f"News sentiment for {symbol} averages {n['mean_sentiment']:.2f} over "
                f"{used} documents [{cid}]."
            )
        else:
            out.append(f"No usable news was available for {symbol} [{cid}].")
        flagged = int(n.get("n_injection_suspected", 0))
        if flagged:
            out.append(
                f"{flagged} document(s) looked suspicious (instruction-like) and were excluded "
                f"from that score [{cid}]."
            )
    elif entry["kind"] == "fundamentals" and "metrics.pe_ratio.value" in n:
        out.append(f"{symbol} trades at a P/E of {n['metrics.pe_ratio.value']:.2f} [{cid}].")
    return out


def draft_from_prompt(prompt: str, *, plant_error: bool = False) -> str:
    """A draft that states the numbers in ``prompt``'s evidence digest, citing each entry.

    With ``plant_error`` the first risk number is wrong by 37%, which the verifier must catch.
    """
    sentences: list[str] = []
    wrong_pending = plant_error
    for entry in parse_digest(prompt):
        sentences += _sentences(entry, wrong=wrong_pending and entry["kind"] == "risk")
        if entry["kind"] == "risk":
            wrong_pending = False
    if not sentences:
        sentences.append("No evidence could be collected for this review.")
    return " ".join(sentences) + " " + DISCLAIMER

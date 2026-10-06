"""Acceptance: examples/agentic_risk_review.py runs end to end offline (scripted model, no key).

It must show a verifier rejection and the revision that follows, a report, an approval request for
a paper trade (never an executed one), and a verified audit chain.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.eval

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "agentic_risk_review.py"


@pytest.fixture(scope="module")
def output() -> str:
    done = subprocess.run(  # noqa: S603 - fixed arguments: the interpreter running this test
        [sys.executable, str(EXAMPLE)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, f"example failed:\n{done.stdout}\n{done.stderr}"
    return done.stdout


def test_it_runs_offline_with_the_scripted_model(output) -> None:
    assert "model: scripted" in output and "synthetic data, paper only" in output


def test_the_trace_shows_one_rejection_with_a_reason_and_then_an_accepted_revision(output) -> None:
    rejected = re.search(r"verifier\s+draft 1: REVISE (.+)", output)
    assert rejected and "does not match any number" in rejected.group(1)
    accepted = re.search(r"verifier\s+draft 2: ACCEPT", output)
    assert accepted and rejected.start() < accepted.start()


def test_a_report_is_produced_with_evidence_links_and_the_disclaimer(output) -> None:
    assert "Status: verified" in output
    assert re.search(r"\[E1\]\(#e1\)", output) and "## Evidence" in output
    assert output.count("Not investment advice.") >= 1
    assert "## Verification" in output


def test_a_paper_trade_becomes_an_approval_request_and_nothing_executes(output) -> None:
    assert "propose_paper_trade APPROVAL_REQUIRED approval_id=" in output
    assert re.search(r"propose_paper_trade\s+tier=ONE_CLICK\s+status=PENDING", output)
    assert "paper orders executed: 0" in output


def test_the_audit_chain_verifies(output) -> None:
    assert re.search(r"AUDIT chain: OK \(\d+ records\)", output)


def test_only_the_verified_draft_is_in_the_report_and_the_trace_is_ordered(output) -> None:
    order = [
        output.index(marker) for marker in ("TRACE", "REPORT", "APPROVAL REQUESTS", "AUDIT chain")
    ]
    assert order == sorted(order)
    saved = output.index("report-composer    save_report OK")
    assert (
        output.index("draft 2: ACCEPT")
        < saved
        < output.index("propose_paper_trade APPROVAL_REQUIRED")
    )

"""The verifier: planted errors must be caught, and a correct report must pass."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tycheon.agents.models import EvidenceBook, flatten_numbers
from tycheon.agents.plan import PlanStep, ResearchPlan
from tycheon.agents.verifier import (
    ReportVerifier,
    extract_numbers,
    plan_facts,
    split_sentences,
    uncited_evidence,
)
from tycheon.models.base import DISCLAIMER

AS_OF = datetime(2023, 10, 2, 14, 30, tzinfo=UTC)
PORTFOLIO = {"SYN-GBM": 400_000.0, "SYN-GARCH": 350_000.0, "SYN-REGIME": 250_000.0}


@pytest.fixture
def plan() -> ResearchPlan:
    return ResearchPlan(
        goal="Review the portfolio",
        as_of=AS_OF,
        horizon=5,
        portfolio=PORTFOLIO,
        steps=[PlanStep(kind="risk")],
    )


@pytest.fixture
def book() -> EvidenceBook:
    b = EvidenceBook()
    common = {"agent": "a", "as_of": AS_OF, "published_at": AS_OF}
    b.add(  # E1
        kind="forecast", tool="forecast_distribution", symbol="SYN-GBM",
        calibration_status="calibrated",
        numbers=flatten_numbers({"median": 149.71, "lower_90": 141.67, "upper_90": 154.38,
                                 "last_close": 149.07, "median_return": 0.0043, "horizon": 5}),
        **common,
    )  # fmt: skip
    b.add(  # E2
        kind="risk", tool="portfolio_risk", calibration_status="uncalibrated",
        numbers=flatten_numbers({"var95": 0.0304, "es95": 0.0441, "var99": 0.0499,
                                 "total_value": 1_000_000.0, "n_assets": 3}),
        flags=["uncalibrated_output", "unreliable_tail"], **common,
    )  # fmt: skip
    b.add(  # E3
        kind="news", tool="news_signals", symbol="SYN-GBM",
        numbers=flatten_numbers({"mean_sentiment": -0.01, "n_documents_used": 5,
                                 "n_injection_suspected": 1}),
        flags=["injection_suspected"], **common,
    )  # fmt: skip
    return b


GOOD = (
    "SYN-GBM's calibrated median forecast is 149.71, with a 90% interval of 141.67 to 154.38 [E1]. "
    "Across the 3 holdings and a 5-bar horizon, the 95% VaR is 3.04% and the 95% ES is 4.41% [E2]. "
    "This portfolio result is uncalibrated because dependence is assumed, and the 99% tail "
    "estimate of 4.99% is not reliable [E2]. "
    "One news document looked suspicious and was excluded from the sentiment of -0.01 [E3]. "
    f"{DISCLAIMER}"
)


def verify(draft: str, plan: ResearchPlan, book: EvidenceBook):
    return ReportVerifier().verify(draft, book, plan)


# ----------------------------------------------------------------------- accepting
def test_a_correct_cited_report_is_accepted(plan, book) -> None:
    v = verify(GOOD, plan, book)
    assert v.decision == "ACCEPT", v.reasons
    assert v.numbers_checked > 5 and v.numbers_supported == v.numbers_checked


def test_rounding_to_the_displayed_precision_is_accepted(plan, book) -> None:
    draft = GOOD.replace("3.04%", "3.0%").replace("4.41%", "4%").replace("149.71", "149.7")
    assert verify(draft, plan, book).decision == "ACCEPT"


def test_plan_facts_may_be_stated_without_a_citation(plan, book) -> None:
    draft = (
        "The review covers 3 holdings worth $1,000,000 over a 5-bar horizon, as of 2023-10-02. "
        + GOOD
    )
    assert verify(draft, plan, book).decision == "ACCEPT"


def test_nominal_levels_beside_a_descriptor_are_not_claims(plan, book) -> None:
    assert verify(GOOD, plan, book).decision == "ACCEPT"  # "90% interval", "95% VaR"
    bare = GOOD.replace("a 90% interval of", "an interval of")
    assert verify(bare, plan, book).decision == "ACCEPT"


# ------------------------------------------------------------- planted number errors
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("3.04%", "4.07%"),  # a plausible but wrong VaR
        ("149.71", "158.20"),
        ("141.67", "131.67"),
        ("4.41%", "44.1%"),  # a decimal slip
        ("-0.01", "0.41"),
    ],
)
def test_a_planted_wrong_number_is_caught_with_the_number_in_the_reason(
    plan, book, old, new
) -> None:
    v = verify(GOOD.replace(old, new), plan, book)
    assert v.decision == "REVISE" and "number_mismatch" in v.flags
    assert any(new in reason for reason in v.reasons)


def test_a_number_supported_only_by_other_evidence_is_flagged(plan, book) -> None:
    draft = GOOD.replace(
        "is 3.04% and the 95% ES is 4.41% [E2]", "is 3.04% and the 95% ES is 4.41% [E1]"
    )
    v = verify(draft, plan, book)
    assert v.decision == "REVISE" and "number_not_in_cited_evidence" in v.flags
    assert any("appears in E2" in reason for reason in v.reasons)


def test_a_number_with_no_citation_is_flagged(plan, book) -> None:
    v = verify(GOOD + " Volatility is 17.3% a year.", plan, book)
    assert "uncited_number" in v.flags and v.decision == "REVISE"


def test_a_citation_after_the_period_stays_with_its_sentence(plan, book) -> None:
    draft = GOOD.replace(
        "is 3.04% and the 95% ES is 4.41% [E2].", "is 3.04% and the 95% ES is 4.41%. [E2]"
    )
    assert verify(draft, plan, book).decision == "ACCEPT"


# ------------------------------------------------------------------------- citations
def test_a_citation_to_nothing_is_flagged(plan, book) -> None:
    v = verify(GOOD + " Another point [E9].", plan, book)
    assert "unknown_citation" in v.flags


def test_evidence_dated_after_as_of_cannot_be_cited(plan, book) -> None:
    book.add(
        kind="news", tool="news_signals", symbol="SYN-GARCH", agent="a", as_of=AS_OF,
        published_at=AS_OF + timedelta(days=1), numbers={"mean_sentiment": 0.2},
    )  # fmt: skip
    v = verify(GOOD + " Sentiment of 0.2 [E4].", plan, book)
    assert "citation_after_as_of" in v.flags and v.decision == "REVISE"


def test_a_report_with_no_citations_at_all_is_flagged(plan, book) -> None:
    v = verify("Things look fine overall. " + DISCLAIMER, plan, book)
    assert "no_citations" in v.flags


# ------------------------------------------------------------------ uncalibrated outputs
def test_uncalibrated_evidence_must_be_called_uncalibrated(plan, book) -> None:
    draft = GOOD.replace(
        "This portfolio result is uncalibrated because dependence is assumed, and the", "The"
    )
    v = verify(draft, plan, book)
    assert "uncalibrated_not_disclosed" in v.flags and v.decision == "REVISE"


def test_calling_uncalibrated_evidence_calibrated_is_flagged(plan, book) -> None:
    draft = GOOD.replace("is uncalibrated because", "is calibrated and uncalibrated because")
    v = verify(GOOD + " The risk model is calibrated [E2].", plan, book)
    assert "calibrated_claim_on_uncalibrated_evidence" in v.flags
    assert verify(draft, plan, book).decision == "REVISE"


def test_calibrated_evidence_may_be_called_calibrated(plan, book) -> None:
    assert "calibrated_claim_on_uncalibrated_evidence" not in verify(GOOD, plan, book).flags


def test_unreliable_tails_must_be_disclosed(plan, book) -> None:
    v = verify(
        GOOD.replace(
            ", and the 99% tail estimate of 4.99% is not reliable", ", and the 99% tail is 4.99%"
        ),
        plan,
        book,
    )
    assert "unreliable_tail_not_disclosed" in v.flags


# --------------------------------------------------------------- hostile news evidence
def test_instruction_like_news_must_be_disclosed(plan, book) -> None:
    draft = GOOD.replace(
        "One news document looked suspicious and was excluded from the sentiment of -0.01 [E3]. ",
        "",
    )
    v = verify(draft, plan, book)
    assert "injection_not_disclosed" in v.flags and v.decision == "REVISE"


# --------------------------------------------------------------- disclaimer and advice
def test_the_disclaimer_is_required_verbatim(plan, book) -> None:
    v = verify(GOOD.replace(DISCLAIMER, "Not advice."), plan, book)
    assert "missing_disclaimer" in v.flags


@pytest.mark.parametrize(
    "advice",
    ["You should buy SYN-GBM now.", "This is a guaranteed gain.", "A risk-free trade."],
)
def test_advice_language_is_a_rejection(plan, book, advice) -> None:
    v = verify(GOOD + " " + advice, plan, book)
    assert v.decision == "REJECT" and "advice_language" in v.flags


def test_an_empty_draft_is_not_accepted(plan, book) -> None:
    assert verify("   ", plan, book).decision == "REVISE"
    assert verify("", plan, book).flags == ("empty_draft",)


# --------------------------------------------------------------------------- the parts
def test_numbers_exclude_citations_dates_years_and_list_markers() -> None:
    tokens = extract_numbers("1. On 2023-10-02 (in 2023) the value was $1,250.50 [E12] or 3.5%.")
    assert [(t.raw, t.value) for t in tokens] == [("$1,250.50", 1250.5), ("3.5%", 3.5)] or (
        [t.value for t in tokens][-2:] == [1250.5, 3.5]
    )
    assert all("12" not in t.raw for t in tokens)


def test_suffixes_scale_numbers() -> None:
    assert extract_numbers("worth $1.5M and 250k")[0].value == 1_500_000
    assert extract_numbers("worth $1.5M and 250k")[1].value == 250_000


def test_sentences_split_without_breaking_decimals() -> None:
    parts = split_sentences("VaR is 3.04% [E1]. ES is 4.41% [E1]. [E2]\n- a bullet 5.5 [E1]")
    assert parts[0].startswith("VaR is 3.04%") and len(parts) == 3
    assert "[E2]" in parts[1]


def test_plan_facts_include_positions_and_weights(plan) -> None:
    facts = plan_facts(plan)
    assert 5.0 in facts and 3.0 in facts and 1_000_000.0 in facts and 400_000.0 in facts
    assert any(abs(f - 0.4) < 1e-9 for f in facts)


def test_unused_evidence_can_be_listed(plan, book) -> None:
    unused = uncited_evidence(book, "Only this [E1].")
    assert {e.id for e in unused} == {"E2", "E3"}


def test_the_verifier_is_deterministic_and_independent_of_any_model(plan, book) -> None:
    """No shared code with the composer: it imports no model protocol and no harness."""
    import ast

    import tycheon.agents.verifier as module

    assert verify(GOOD, plan, book) == verify(GOOD, plan, book)
    tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
    imported = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    } | {a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names}
    assert not {m for m in imported if "protocols" in m or "keelgate" in m or "composer" in m}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "TextModel" not in names

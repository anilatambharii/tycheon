"""The independent verifier: checks a draft report against the evidence, never against itself.

It is deliberately *not* a language model and shares nothing with the composer. Given only the
draft text, the evidence book and the plan, it decides ACCEPT, REVISE or REJECT by rules a
reviewer can read:

1. **Every number is supported.** Each numeric token must match (to its displayed precision) a
   number in the evidence the *same sentence cites*, or be a plan fact (horizon, position sizes).
   A number that is only in other evidence, or nowhere, is a rejection with the reason.
2. **Citations exist and are pre-``as_of``.** Every ``[E<n>]`` must be in the book, and no
   cited evidence may be dated after the review's ``as_of``.
3. **Uncalibrated outputs are flagged.** A cited evidence whose calibration status is
   ``uncalibrated`` or ``stale`` must be called that in a sentence citing it, and no sentence may
   call such evidence "calibrated".
4. **Hostile or fragile evidence is disclosed.** News flagged as instruction-like, and risk
   numbers flagged unreliable, must be mentioned as such.
5. **The disclaimer is present, and there is no advice language.**

The verifier can only reject. It never authorises an action: the gateway does that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from tycheon.models.base import DISCLAIMER

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tycheon.agents.models import Evidence, EvidenceBook
    from tycheon.agents.plan import ResearchPlan

Decision = Literal["ACCEPT", "REVISE", "REJECT"]

_CITATION = re.compile(r"\[\s*(E\d+(?:\s*,\s*E\d+)*)\s*\]")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.+\-Z]+)?")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b(?!%|\.\d)")
_LIST_MARKER = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+")
_NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-+]?)(?P<cur>\$)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<pct>%)?(?P<suffix>[kKmMbB](?![A-Za-z]))?"
)
_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}
#: Nominal levels people quote next to a descriptor ("90% interval"). These describe the
#: method, not a measured value, so they are accepted only beside such a word.
_NOMINAL = {50.0, 80.0, 90.0, 95.0, 97.5, 99.0}
_DESCRIPTOR_AFTER = re.compile(
    r"^\s*(?:-|\s)?(?:central\s+)?(?:interval|intervals|confidence|level|quantile|percentile|var|es|"
    r"expected shortfall|value at risk|tail|band)\b",
    re.IGNORECASE,
)
_DESCRIPTOR_BEFORE = re.compile(
    r"(?:var|es|level|quantile|percentile|shortfall)\s*(?:at|of)?\s*$", re.I
)
_UNCALIBRATED_WORD = re.compile(r"\b(?:uncalibrated|not calibrated|stale)\b", re.IGNORECASE)
_CALIBRATED_CLAIM = re.compile(r"(?<!un)(?<!not )\bcalibrated\b", re.IGNORECASE)
_INJECTION_WORD = re.compile(
    r"\b(?:suspicious|suspected|excluded|instruction-like|injection)\b", re.I
)
_UNRELIABLE_WORD = re.compile(r"\b(?:unreliable|not reliable|few tail|thin tail)\b", re.IGNORECASE)
_ADVICE = re.compile(
    r"\b(?:you should (?:buy|sell|hold)|we recommend (?:buying|selling|that you)|i recommend|"
    r"guaranteed|risk[- ]free|sure thing|cannot lose|will (?:definitely|certainly) )",
    re.IGNORECASE,
)
MAX_REASON = 300


@dataclass(frozen=True)
class Number:
    raw: str
    value: float
    decimals: int
    percent: bool
    currency: bool
    start: int
    end: int


@dataclass(frozen=True)
class VerifierVerdict:
    decision: Decision
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    numbers_checked: int = 0
    numbers_supported: int = 0

    @property
    def accepted(self) -> bool:
        return self.decision == "ACCEPT"


_LEADING_CITATION = re.compile(r"^\s*(\[\s*E\d+(?:\s*,\s*E\d+)*\s*\])\s*")


def split_sentences(text: str) -> list[str]:
    """Sentences. A citation written after the full stop belongs to the sentence before it."""
    cleaned = _LIST_MARKER.sub("", text)
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", cleaned) if p.strip()]
    merged: list[str] = []
    for part in parts:
        lead = _LEADING_CITATION.match(part)
        rest = part
        if lead and merged:
            merged[-1] = merged[-1] + " " + lead.group(1)
            rest = part[lead.end() :]
        if rest and not (_CITATION.fullmatch(rest.rstrip(".")) and merged):
            merged.append(rest)
    return merged


def scan_numbers(sentence: str) -> tuple[list[Number], str]:
    """Numeric claims in a sentence (not citations, dates, years or list markers), and the
    scrubbed text their positions refer to."""
    scrub = _CITATION.sub(lambda m: " " * len(m.group(0)), sentence)
    scrub = _ISO_DATE.sub(lambda m: " " * len(m.group(0)), scrub)
    scrub = _YEAR.sub(lambda m: " " * len(m.group(0)), scrub)
    found = []
    for m in _NUMBER.finditer(scrub):
        text = m.group("num").replace(",", "")
        value = float(text)
        suffix = m.group("suffix")
        if suffix:
            value *= _SUFFIX[suffix.lower()]
        decimals = len(text.split(".")[1]) if "." in text else 0
        found.append(
            Number(
                raw=m.group(0).strip(),
                value=value,
                decimals=decimals,
                percent=m.group("pct") is not None,
                currency=m.group("cur") is not None,
                start=m.start(),
                end=m.end(),
            )
        )
    return found, scrub


def extract_numbers(sentence: str) -> list[Number]:
    return scan_numbers(sentence)[0]


def _is_descriptor(token: Number, scrubbed: str) -> bool:
    if not token.percent and token.value not in _NOMINAL:
        return False
    if token.value not in _NOMINAL:
        return False
    after = scrubbed[token.end :]
    before = scrubbed[: token.start]
    return bool(_DESCRIPTOR_AFTER.match(after) or _DESCRIPTOR_BEFORE.search(before))


#: The writer is shown evidence rounded to six significant digits (``Evidence.line``), so a
#: number it quotes can differ from the stored value by that rounding on top of its own display
#: rounding. This is the relative slack for the first; it is far too small to admit a wrong one.
DIGEST_RELATIVE_SLACK = 5e-6


def _matches(token: Number, candidate: float) -> bool:
    """Equal to the precision the token displays (a percentage rounds like any decimal)."""
    shown, target = abs(token.value), abs(candidate)
    if token.percent or token.decimals:
        tolerance = 0.5 / (10.0**token.decimals)
    else:
        tolerance = 0.5 if target >= 10 else 1e-9  # a small whole number must be exact
    tolerance += DIGEST_RELATIVE_SLACK * max(shown, target)
    return abs(shown - target) <= tolerance + 1e-12


def _supported(token: Number, numbers: Iterable[float]) -> bool:
    for x in numbers:
        if token.percent:
            if _matches(token, x * 100.0) or _matches(token, x):
                return True
        elif _matches(token, x):
            return True
    return False


def plan_facts(plan: ResearchPlan) -> list[float]:
    """Numbers a sentence may state without citing evidence: facts of the request itself."""
    total = sum(plan.portfolio.values())
    facts = [float(plan.horizon), float(len(plan.portfolio)), total]
    for value in plan.portfolio.values():
        facts += [value, value / total if total else 0.0]
    for budget_value in (plan.budget.max_revisions, plan.budget.max_tool_calls):
        facts.append(float(budget_value))
    return facts


class _Findings:
    """Issues collected during one verification, in the order found."""

    def __init__(self) -> None:
        self.issues: list[tuple[str, str, Decision]] = []
        self.checked = 0
        self.supported = 0

    def add(self, flag: str, reason: str, decision: Decision = "REVISE") -> None:
        self.issues.append((flag, reason[:MAX_REASON], decision))

    def verdict(self) -> VerifierVerdict:
        if not self.issues:
            return VerifierVerdict(
                "ACCEPT",
                ("every number and citation checked",),
                (),
                self.checked,
                self.supported,
            )
        decision: Decision = "REJECT" if any(d == "REJECT" for _, _, d in self.issues) else "REVISE"
        return VerifierVerdict(
            decision,
            tuple(dict.fromkeys(r for _, r, _ in self.issues)),
            tuple(dict.fromkeys(f for f, _, _ in self.issues)),
            self.checked,
            self.supported,
        )


class ReportVerifier:
    """Checks a draft against the evidence and the plan. See the module docstring."""

    def verify(self, draft: str, book: EvidenceBook, plan: ResearchPlan) -> VerifierVerdict:
        text = draft or ""
        if not text.strip():
            return VerifierVerdict("REVISE", ("the draft is empty",), ("empty_draft",))
        found = _Findings()
        if DISCLAIMER not in text:
            found.add("missing_disclaimer", f'include this exact sentence: "{DISCLAIMER}"')
        if _ADVICE.search(text):
            found.add(
                "advice_language",
                "remove advice or guarantee language: this is analytics only",
                "REJECT",
            )
        facts = plan_facts(plan)
        sentences = split_sentences(text)
        cited_overall: set[str] = set()
        for sentence in sentences:
            ids = _cited_ids(sentence)
            cited_overall.update(ids)
            self._check_citations(ids, book, plan, found)
            self._check_numbers(sentence, ids, book, facts, found)
        if not cited_overall:
            found.add("no_citations", "cite the evidence behind each claim as [E1], [E2], ...")
        self._check_disclosures(sentences, book, found)
        return found.verdict()

    @staticmethod
    def _check_citations(
        ids: list[str], book: EvidenceBook, plan: ResearchPlan, found: _Findings
    ) -> None:
        for evidence_id in ids:
            evidence = book.get(evidence_id)
            if evidence is None:
                found.add("unknown_citation", f"{evidence_id} is not in the evidence")
            elif evidence.published_at > plan.as_of or evidence.as_of > plan.as_of:
                found.add("citation_after_as_of", f"{evidence_id} is dated after the review as_of")

    @staticmethod
    def _check_numbers(
        sentence: str,
        ids: list[str],
        book: EvidenceBook,
        facts: list[float],
        found: _Findings,
    ) -> None:
        tokens, scrubbed = scan_numbers(sentence)
        pool = [n for i in ids if (e := book.get(i)) is not None for n in e.numbers.values()]
        for token in tokens:
            if _is_descriptor(token, scrubbed):
                continue
            found.checked += 1
            if _supported(token, facts) or _supported(token, pool):
                found.supported += 1
                continue
            elsewhere = [
                e.id
                for e in book.items
                if e.id not in ids and _supported(token, e.numbers.values())
            ]
            if elsewhere:
                found.add(
                    "number_not_in_cited_evidence",
                    f"{token.raw} appears in {elsewhere[0]}, but this sentence does not cite it",
                )
            elif not ids:
                found.add("uncited_number", f"{token.raw} has no citation and is not a stated fact")
            else:
                found.add(
                    "number_mismatch",
                    f"{token.raw} does not match any number in {', '.join(ids)}",
                )

    @staticmethod
    def _check_disclosures(sentences: list[str], book: EvidenceBook, found: _Findings) -> None:
        for evidence in book.items:
            mine = [s for s in sentences if evidence.id in _cited_ids(s)]
            if (
                evidence.is_uncalibrated
                and mine
                and not any(_UNCALIBRATED_WORD.search(s) for s in mine)
            ):
                found.add(
                    "uncalibrated_not_disclosed",
                    f"{evidence.id} is {evidence.calibration_status}: "
                    "say so in a sentence that cites it",
                )
            if "injection_suspected" in evidence.flags and not any(
                _INJECTION_WORD.search(s) for s in mine
            ):
                found.add(
                    "injection_not_disclosed",
                    f"{evidence.id} contained instruction-like documents that were excluded: "
                    "mention it and cite it",
                )
            if (
                "unreliable_tail" in evidence.flags
                and mine
                and not any(_UNRELIABLE_WORD.search(s) for s in mine)
            ):
                found.add(
                    "unreliable_tail_not_disclosed",
                    f"{evidence.id} has tail estimates marked unreliable: say so where it is used",
                )
        uncalibrated = {e.id for e in book.items if e.is_uncalibrated}
        for sentence in sentences:
            ids = set(_cited_ids(sentence))
            if ids and ids <= uncalibrated and _CALIBRATED_CLAIM.search(sentence):
                found.add(
                    "calibrated_claim_on_uncalibrated_evidence",
                    f"do not call {', '.join(sorted(ids))} calibrated: "
                    "its status is not calibrated",
                )


def _cited_ids(sentence: str) -> list[str]:
    return [i.strip() for group in _CITATION.findall(sentence) for i in group.split(",")]


def uncited_evidence(book: EvidenceBook, draft: str) -> list[Evidence]:
    """Evidence the draft never cites (for the trace; not an error)."""
    used = {i for s in split_sentences(draft) for i in _cited_ids(s)}
    return [e for e in book.items if e.id not in used]

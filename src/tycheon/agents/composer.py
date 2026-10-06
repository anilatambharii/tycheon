"""The report composer: a drafting prompt for the model, and the final report with evidence links.

Two separate jobs:

* :class:`DraftWriter` asks a language model for a draft **from the evidence digest only**. The
  digest contains ids and numbers; it never contains news text or any other untrusted document
  text. The draft is a proposal: the independent verifier decides whether it is acceptable.
* :class:`ReportComposer` assembles the *final* report deterministically around a draft the
  verifier accepted: evidence table with links back to the audit trail, evidence gaps, the
  verifier trace, pending approvals and the disclaimer. A draft that was never accepted is not
  published: the report is marked ``withheld`` with the reasons instead.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime  # noqa: TC003 - pydantic needs it at runtime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from tycheon.agents.models import EvidenceGap  # noqa: TC001 - pydantic needs it at runtime
from tycheon.models.base import DISCLAIMER

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tycheon.agents.models import EvidenceBook
    from tycheon.agents.plan import ResearchPlan
    from tycheon.agents.protocols import TextModel, TextResult, ToolResult

_CITATION = re.compile(r"\[(E\d+)\]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

WRITER_SYSTEM = (
    "You write a short portfolio risk review for analysts. Use ONLY the evidence given. "
    "Cite the evidence behind every number as [E1], [E2] (several as [E1, E2]) in the same "
    "sentence. Quote numbers exactly as given, to at most their shown precision. "
    "Say plainly when evidence is uncalibrated or stale, and when documents were excluded as "
    "instruction-like. Do not give advice, forecasts of certainty or recommendations. "
    "End with the disclaimer sentence exactly as given."
)


class DraftWriter:
    """Drafts (and re-drafts, given the verifier's reasons) the review text."""

    def __init__(self, model: TextModel) -> None:
        self._model = model

    @staticmethod
    def prompt(plan: ResearchPlan, book: EvidenceBook, feedback: Sequence[str]) -> str:
        positions = ", ".join(f"{s} {v:,.0f}" for s, v in plan.portfolio.items())
        lines = [
            f"Goal: {plan.goal}",
            f"As of: {plan.as_of.isoformat()}; horizon: {plan.horizon} bars",
            f"Positions: {positions}",
            "Evidence (cite by id):",
            book.digest() or "(none)",
        ]
        if book.gaps:
            lines.append("Evidence that could not be collected:")
            lines += [f"- {g.step}: {g.reason}" for g in book.gaps]
        if feedback:
            lines.append("Your previous draft was rejected. Fix every one of these problems:")
            lines += [f"- {reason}" for reason in feedback]
        lines.append(f'Final sentence, exactly: "{DISCLAIMER}"')
        return "\n".join(lines)

    async def draft(
        self, plan: ResearchPlan, book: EvidenceBook, feedback: Sequence[str] = ()
    ) -> TextResult:
        return await self._model.complete(
            system=WRITER_SYSTEM, prompt=self.prompt(plan, book, feedback), purpose="draft"
        )


# --------------------------------------------------------------------------- the report
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerdictTrace(_Strict):
    iteration: int
    decision: str
    reasons: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class EvidenceRow(_Strict):
    id: str
    kind: str
    tool: str
    symbol: str | None
    calibration_status: str | None
    as_of: datetime
    audit_seq: int | None
    flags: list[str]


class ApprovalRow(_Strict):
    approval_id: str
    tier: str | None
    tool: str
    status: str
    note: str


class ReportTrace(_Strict):
    verdicts: list[VerdictTrace]
    revisions: int
    stop_reason: str
    tokens: int


class Report(_Strict):
    title: str
    as_of: datetime
    goal: str
    status: Literal["verified", "withheld"]
    body: str
    withheld_reasons: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRow]
    gaps: list[EvidenceGap] = Field(default_factory=list)
    trace: ReportTrace
    approvals: list[ApprovalRow] = Field(default_factory=list)
    saved_to: str | None = None
    disclaimer: str = DISCLAIMER

    # ---------------------------------------------------------------- renderings
    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=2)

    def to_markdown(self) -> str:
        def link(text: str) -> str:
            return _CITATION.sub(lambda m: f"[{m.group(1)}](#{m.group(1).lower()})", text)

        out = [
            f"# {self.title}",
            "",
            f"*As of {self.as_of.isoformat()}. Status: {self.status}.*",
            "",
        ]
        out += [link(self.body), "", "## Evidence", ""]
        for row in self.evidence:
            status = f", {row.calibration_status}" if row.calibration_status else ""
            audit = f", audit #{row.audit_seq}" if row.audit_seq is not None else ""
            subject = f" {row.symbol}" if row.symbol else ""
            anchor = f'<a id="{row.id.lower()}"></a>'
            out.append(f"- {anchor}**{row.id}** {row.kind}{subject} ({row.tool}{status}{audit})")
        if self.gaps:
            out += ["", "## Evidence not collected", ""]
            out += [f"- {g.step}: {g.reason}" for g in self.gaps]
        if self.approvals:
            out += ["", "## Pending human approval", ""]
            out += [
                f"- `{a.approval_id}` ({a.tool}, {a.tier}): {a.status}. {a.note}"
                for a in self.approvals
            ]
        out += ["", "## Verification", ""]
        for v in self.trace.verdicts:
            detail = "; ".join(v.reasons) if v.reasons else ""
            out.append(f"- draft {v.iteration}: {v.decision}" + (f" - {detail}" if detail else ""))
        out += ["", f"**{self.disclaimer}**", ""]
        return "\n".join(out)

    def to_html(self) -> str:
        """A self-contained page: everything dynamic is escaped, citations link to the evidence."""

        def esc(value: object) -> str:
            return html.escape(str(value), quote=True)

        def link(text: str) -> str:
            return _CITATION.sub(lambda m: f'<a href="#{m.group(1)}">[{m.group(1)}]</a>', esc(text))

        rows = "".join(
            f'<li id="{esc(r.id)}"><strong>{esc(r.id)}</strong> {esc(r.kind)} '
            f"{esc(r.symbol or '')} "
            f"({esc(r.tool)}{', ' + esc(r.calibration_status) if r.calibration_status else ''}"
            f"{', audit #' + esc(r.audit_seq) if r.audit_seq is not None else ''})</li>"
            for r in self.evidence
        )
        gaps = "".join(f"<li>{esc(g.step)}: {esc(g.reason)}</li>" for g in self.gaps)
        approvals = "".join(
            f"<li><code>{esc(a.approval_id)}</code> ({esc(a.tool)}, {esc(a.tier)}): "
            f"{esc(a.status)}</li>"
            for a in self.approvals
        )
        verdicts = "".join(
            f"<li>draft {v.iteration}: {esc(v.decision)} {esc('; '.join(v.reasons))}</li>"
            for v in self.trace.verdicts
        )
        paragraphs = "".join(f"<p>{link(p)}</p>" for p in self.body.split("\n\n") if p.strip())
        return (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{esc(self.title)}</title>"
            "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;"
            "padding:0 1rem}.s{color:#666}</style></head><body>"
            f"<h1>{esc(self.title)}</h1><p class='s'>As of {esc(self.as_of.isoformat())}. "
            f"Status: {esc(self.status)}.</p>{paragraphs}"
            f"<h2>Evidence</h2><ul>{rows}</ul>"
            + (f"<h2>Evidence not collected</h2><ul>{gaps}</ul>" if gaps else "")
            + (f"<h2>Pending human approval</h2><ul>{approvals}</ul>" if approvals else "")
            + f"<h2>Verification</h2><ul>{verdicts}</ul>"
            f"<p><strong>{esc(self.disclaimer)}</strong></p></body></html>"
        )


class ReportComposer:
    """Builds the final :class:`Report` around a verified draft (or a withheld notice)."""

    @staticmethod
    def finalize(
        *,
        plan: ResearchPlan,
        book: EvidenceBook,
        final_text: str | None,
        verdicts: Sequence[VerdictTrace],
        stop_reason: str,
        tokens: int,
        approvals: Sequence[ToolResult] = (),
        saved_to: str | None = None,
    ) -> Report:
        verified = final_text is not None
        last_reasons = list(verdicts[-1].reasons) if verdicts and not verified else []
        body = (
            _CONTROL.sub("", final_text)
            if final_text is not None
            else "This report is withheld: no draft passed independent verification, so no "
            "analysis text is published."
        )
        rows = [
            EvidenceRow(
                id=e.id,
                kind=e.kind,
                tool=e.tool,
                symbol=e.symbol,
                calibration_status=e.calibration_status,
                as_of=e.as_of,
                audit_seq=e.audit_seq,
                flags=e.flags,
            )
            for e in book.items
        ]
        pending = [
            ApprovalRow(
                approval_id=a.approval_id or "",
                tier=a.approval_tier,
                tool=a.tool,
                status="awaiting a human decision",
                note="paper trade proposal; nothing executes until a human approves",
            )
            for a in approvals
            if a.status == "APPROVAL_REQUIRED" and a.approval_id
        ]
        return Report(
            title="Portfolio risk review",
            as_of=plan.as_of,
            goal=plan.goal,
            status="verified" if verified else "withheld",
            body=body,
            withheld_reasons=last_reasons,
            evidence=rows,
            gaps=list(book.gaps),
            trace=ReportTrace(
                verdicts=list(verdicts),
                revisions=max(0, sum(1 for v in verdicts if v.decision != "ACCEPT")),
                stop_reason=stop_reason,
                tokens=tokens,
            ),
            approvals=pending,
            saved_to=saved_to,
        )

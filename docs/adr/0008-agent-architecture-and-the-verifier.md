# 0008. Agent architecture and the independent verifier

- **Status:** Accepted
- **Date:** 2026-10-06
- **Phase:** T4

## Context

An agent that reads documents and writes risk reports fails in ways a forecasting library does
not: it can state a number it invented, cite evidence it did not use, call an uncalibrated result
calibrated, obey an instruction hidden in a news article, or be steered toward an action. The
design has to make each of those structurally hard, not merely discouraged in a prompt.

## Decision

1. **Models propose; code decides, at every step.** A model drafts a plan and drafts text. It
   never calls a tool. The plan is validated and **clamped**: the goal, `as_of`, portfolio and
   horizon belong to the request, budgets are limited to hard ceilings, unknown steps and symbols
   are dropped, and a trade proposal is dropped unless the request allows one. Every adjustment is
   recorded in the trace; an unusable plan falls back to a deterministic default.
2. **Specialists are deterministic.** Each maps a plan step to one governed tool call under its own
   grant and records the result as evidence. A refusal or error is an *evidence gap*, reported,
   never papered over.
3. **Only tool results are evidence.** The composer sees an evidence digest: ids and numbers. It
   cannot add evidence, and no document text is in it.
4. **The verifier is independent and rule-based.** It shares no code and no model with the
   composer. It checks each number against the evidence the same sentence cites (at displayed
   precision), that citations exist and are not dated after `as_of`, that uncalibrated or stale
   outputs are labelled and never called calibrated, that instruction-like documents and unreliable
   tails are disclosed, the disclaimer, and the absence of advice language. It can only reject or
   ask for a revision; it authorises nothing.
5. **An unverified report is withheld.** If no draft is accepted within the revision budget, nothing
   is published or saved and **no trade is proposed**. A proposal needs a verified analysis.
6. **News is untrusted data, structurally.** Only deterministic code reads document text; only
   bounded numbers leave. Instruction-like documents are flagged, excluded from the aggregate they
   target, and disclosed. This is checked by inspecting every request sent to the model.
7. **Trades are proposals.** The policy resolves every paper trade to a human approval bound to the
   exact arguments; the agent receives an approval id, not a fill.

## Consequences

- A rule-based verifier cannot judge a misleading sentence that is numerically correct. It is a
  floor, not a proof of quality.
- Strictness has a cost: a real model may exhaust its revisions. That yields a withheld report,
  which is the intended failure mode.
- The verifier needed one calibration: the writer sees evidence rounded to six significant
  digits, so quoted numbers can double-round. The match allows that known slack (5e-6 relative),
  which is far too small to admit a wrong number.

## Alternatives considered

An LLM verifier (non-deterministic and itself injectable; kept available in Keelgate for later as
a second opinion, never as the only check); letting the composer call tools (it would then hold
grants); passing document excerpts to the model inside an untrusted fence (a mitigation, not a
control: the text would still be in the prompt).

// Proprietary: see ee/LICENSE
import type { TunedModel } from "./api";
import { formatNumber } from "./format";

/** Plain-language lines explaining a model's promotion-gate outcome. */
export function explainGate(model: TunedModel): string[] {
  const gate = model.gate;
  const lines: string[] = [];
  if (model.status === "promoted") {
    lines.push("Promoted: it passed the gate and beat the baseline on held-out data.");
  } else if (model.status === "rejected") {
    lines.push("Rejected: this model did not pass the promotion gate, so it will not be used for forecasts.");
  } else if (model.status === "candidate") {
    lines.push("Candidate: waiting for the promotion gate decision.");
  } else {
    lines.push("Archived: no longer available for routing.");
  }
  if (gate) {
    if (gate.passed === false && model.status !== "rejected") lines.push("The gate check did not pass.");
    if (Array.isArray(gate.reasons) && gate.reasons.length > 0) {
      for (const r of gate.reasons) lines.push(`Reason: ${String(r)}`);
    } else if (model.status === "rejected") {
      lines.push("No specific reason was recorded by the gate.");
    }
    const b = scalar(gate.baseline);
    const c = scalar(gate.candidate);
    if (b !== null && c !== null) {
      lines.push(`Held-out score (lower is better): baseline ${formatNumber(b)} vs this model ${formatNumber(c)}.`);
    }
  }
  return lines;
}

function scalar(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    for (const k of ["crps", "score", "value"]) if (typeof o[k] === "number") return o[k] as number;
  }
  return null;
}

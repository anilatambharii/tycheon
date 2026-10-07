// Proprietary: see ee/LICENSE
import type { Dict } from "./api";

export const BASELINE = "random-walk";

/** Random-walk baseline row first; other rows keep API order. */
export function orderRows(rows: Dict[]): Dict[] {
  const base = rows.filter((r) => r.model_id === BASELINE);
  const rest = rows.filter((r) => r.model_id !== BASELINE);
  return [...base, ...rest];
}

export interface Verdict {
  baselineWins: boolean | null;
  message: string;
}

/** Plain-language verdict on CRPS: lower is better. */
export function baselineVerdict(rows: Dict[]): Verdict {
  const base = rows.find((r) => r.model_id === BASELINE);
  const others = rows.filter((r) => r.model_id !== BASELINE);
  if (!base || typeof base.crps !== "number") {
    return { baselineWins: null, message: "The random-walk baseline row was not returned, so no baseline comparison can be made." };
  }
  const scored = others.filter((r) => typeof r.crps === "number");
  if (scored.length === 0) return { baselineWins: null, message: "Only the random-walk baseline was evaluated." };
  const better = scored.filter((r) => (r.crps as number) < (base.crps as number));
  if (better.length === 0) {
    return {
      baselineWins: true,
      message: "The random-walk baseline won: no tested model had a lower CRPS than it. Treat the other models' forecasts with caution on this data.",
    };
  }
  const sig = better.filter((r) => typeof r.dm_pvalue === "number" && (r.dm_pvalue as number) < 0.05);
  const names = better.map((r) => String(r.model_id)).join(", ");
  return {
    baselineWins: false,
    message:
      sig.length > 0
        ? `${names} had lower CRPS than the baseline; ${sig.map((r) => String(r.model_id)).join(", ")} also differ from it at p < 0.05 (Diebold-Mariano).`
        : `${names} had lower CRPS than the baseline, but no Diebold-Mariano p-value is below 0.05, so the difference may be noise.`,
  };
}

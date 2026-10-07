// Proprietary: see ee/LICENSE
import { fanGeometry, linearScale, niceTicks } from "@/lib/chart";
import type { CoverageRow, ForecastOut } from "@/lib/api";
import { formatNumber } from "@/lib/format";

const W = 640;
const H = 320;

export function FanChart({ forecast }: { forecast: ForecastOut }) {
  const he = forecast.horizon_end;
  const g = fanGeometry({
    lastClose: forecast.last_close,
    medianPath: forecast.median_path,
    lower50: he.lower_50,
    upper50: he.upper_50,
    lower90: he.lower_90,
    upper90: he.upper_90,
    width: W,
    height: H,
  });
  const desc = `Median path over ${g.horizon} steps from ${formatNumber(forecast.last_close)} to ${formatNumber(he.median)}. 50% band ${formatNumber(he.lower_50)} to ${formatNumber(he.upper_50)}; 90% band ${formatNumber(he.lower_90)} to ${formatNumber(he.upper_90)}.`;
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Forecast fan chart" className="h-auto w-full max-w-3xl">
        <desc>{desc}</desc>
        {g.yTicks.map((t) => (
          <g key={t}>
            <line x1={g.plot.left} x2={g.plot.right} y1={g.yScale(t)} y2={g.yScale(t)} stroke="var(--border)" />
            <text x={g.plot.left - 6} y={g.yScale(t) + 4} textAnchor="end" fontSize="11" fill="var(--muted)">
              {formatNumber(t, 5)}
            </text>
          </g>
        ))}
        {g.xTicks.map((t) => (
          <text key={t} x={g.xScale(t)} y={H - 8} textAnchor="middle" fontSize="11" fill="var(--muted)">
            {t}
          </text>
        ))}
        <polygon points={g.band90} fill="var(--band90)" />
        <polygon points={g.band50} fill="var(--band50)" />
        <polyline points={g.medianPoints} fill="none" stroke="var(--accent)" strokeWidth="2" />
        <circle cx={g.xScale(0)} cy={g.yScale(forecast.last_close)} r="3.5" fill="var(--text)" />
      </svg>
      <figcaption className="muted text-xs">
        Line: median path (step 0 = last close). Shaded wedges: 50% (dark) and 90% (light) intervals, drawn linearly from the last
        close to the horizon-end quantiles; only the horizon-end values come from the model.
      </figcaption>
    </figure>
  );
}

const RW = 360;
const RH = 320;

/** Observed coverage vs nominal for raw and calibrated intervals; the diagonal is perfect calibration. */
export function ReliabilityChart({ rows }: { rows: CoverageRow[] }) {
  const m = { left: 44, right: 12, top: 12, bottom: 36 };
  const x = linearScale([0, 1], [m.left, RW - m.right]);
  const y = linearScale([0, 1], [RH - m.bottom, m.top]);
  const sorted = [...rows].sort((a, b) => a.nominal - b.nominal);
  const pts = (k: "raw_coverage" | "calibrated_coverage") => sorted.map((r) => `${x(r.nominal).toFixed(1)},${y(r[k]).toFixed(1)}`).join(" ");
  const ticks = niceTicks([0, 1], 5);
  return (
    <figure>
      <svg viewBox={`0 0 ${RW} ${RH}`} role="img" aria-label="Reliability chart: observed versus nominal coverage" className="h-auto w-full max-w-md">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={m.left} x2={RW - m.right} y1={y(t)} y2={y(t)} stroke="var(--border)" />
            <text x={m.left - 6} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--muted)">
              {t}
            </text>
            <text x={x(t)} y={RH - m.bottom + 14} textAnchor="middle" fontSize="11" fill="var(--muted)">
              {t}
            </text>
          </g>
        ))}
        <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="var(--muted)" strokeDasharray="4 3" />
        <polyline points={pts("raw_coverage")} fill="none" stroke="var(--warn)" strokeWidth="2" />
        <polyline points={pts("calibrated_coverage")} fill="none" stroke="var(--accent)" strokeWidth="2" />
        {sorted.map((r) => (
          <g key={r.nominal}>
            <circle cx={x(r.nominal)} cy={y(r.raw_coverage)} r="3" fill="var(--warn)" />
            <rect x={x(r.nominal) - 3} y={y(r.calibrated_coverage) - 3} width="6" height="6" fill="var(--accent)" />
          </g>
        ))}
        <text x={(m.left + RW - m.right) / 2} y={RH - 4} textAnchor="middle" fontSize="11" fill="var(--muted)">
          Nominal coverage
        </text>
        <text transform={`translate(11 ${RH / 2}) rotate(-90)`} textAnchor="middle" fontSize="11" fill="var(--muted)">
          Observed coverage
        </text>
      </svg>
      <figcaption className="muted text-xs">
        Dashed diagonal = perfect calibration. Circles/orange: raw. Squares/indigo: calibrated.
      </figcaption>
    </figure>
  );
}

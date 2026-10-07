// Proprietary: see ee/LICENSE
// Small scale helpers for the hand-written SVG charts.

export type Scale = (v: number) => number;

/** Linear map from [d0,d1] to [r0,r1]. A degenerate domain maps to the range midpoint. */
export function linearScale(domain: [number, number], range: [number, number]): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  if (d0 === d1) return () => (r0 + r1) / 2;
  const k = (r1 - r0) / (d1 - d0);
  return (v) => r0 + (v - d0) * k;
}

export function extent(values: number[]): [number, number] {
  const finite = values.filter((v) => Number.isFinite(v));
  if (finite.length === 0) return [0, 1];
  return [Math.min(...finite), Math.max(...finite)];
}

export function padDomain([lo, hi]: [number, number], frac = 0.05): [number, number] {
  if (lo === hi) {
    const d = Math.abs(lo) * 0.01 || 1;
    return [lo - d, hi + d];
  }
  const pad = (hi - lo) * frac;
  return [lo - pad, hi + pad];
}

/** Roughly `count` round tick values covering the domain. */
export function niceTicks([lo, hi]: [number, number], count = 5): number[] {
  if (!(hi > lo)) return [lo];
  const rawStep = (hi - lo) / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const norm = rawStep / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + step * 1e-9; t += step) {
    ticks.push(Number(t.toPrecision(12)));
  }
  return ticks;
}

export interface FanInput {
  lastClose: number;
  medianPath: number[];
  lower50: number;
  upper50: number;
  lower90: number;
  upper90: number;
  width: number;
  height: number;
  margin?: { top: number; right: number; bottom: number; left: number };
}

export interface FanGeometry {
  xScale: Scale;
  yScale: Scale;
  yDomain: [number, number];
  horizon: number;
  medianPoints: string;
  band90: string;
  band50: string;
  yTicks: number[];
  xTicks: number[];
  plot: { left: number; right: number; top: number; bottom: number };
}

/**
 * Geometry for the fan chart. Step 0 is the last close; step i is median_path[i-1].
 * The 50% and 90% bands are linear wedges from the last close to the horizon-end
 * quantiles, since the API only provides horizon-end intervals.
 */
export function fanGeometry(input: FanInput): FanGeometry {
  const m = input.margin ?? { top: 12, right: 16, bottom: 28, left: 56 };
  const horizon = Math.max(1, input.medianPath.length);
  const left = m.left;
  const right = input.width - m.right;
  const top = m.top;
  const bottom = input.height - m.bottom;
  const yDomain = padDomain(
    extent([input.lastClose, ...input.medianPath, input.lower90, input.upper90, input.lower50, input.upper50]),
  );
  const xScale = linearScale([0, horizon], [left, right]);
  const yScale = linearScale(yDomain, [bottom, top]);
  const f = (x: number, y: number) => `${x.toFixed(2)},${y.toFixed(2)}`;
  const medianPoints = [f(xScale(0), yScale(input.lastClose))]
    .concat(input.medianPath.map((v, i) => f(xScale(i + 1), yScale(v))))
    .join(" ");
  const wedge = (lo: number, hi: number) =>
    [f(xScale(0), yScale(input.lastClose)), f(xScale(horizon), yScale(hi)), f(xScale(horizon), yScale(lo))].join(" ");
  const xTicks = niceTicks([0, horizon], Math.min(horizon, 6)).filter((t) => Number.isInteger(t));
  return {
    xScale,
    yScale,
    yDomain,
    horizon,
    medianPoints,
    band90: wedge(input.lower90, input.upper90),
    band50: wedge(input.lower50, input.upper50),
    yTicks: niceTicks(yDomain, 5),
    xTicks,
    plot: { left, right, top, bottom },
  };
}

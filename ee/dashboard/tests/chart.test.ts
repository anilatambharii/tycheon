// Proprietary: see ee/LICENSE
import { describe, expect, it } from "vitest";
import { baselineVerdict, orderRows } from "@/lib/backtest";
import { extent, fanGeometry, linearScale, niceTicks, padDomain } from "@/lib/chart";
import { explainGate } from "@/lib/gate";
import { isLocalHttp, sessionCookieOptions } from "@/lib/session";

describe("linearScale", () => {
  it("maps domain ends to range ends (inverted y works)", () => {
    const y = linearScale([0, 10], [100, 0]);
    expect(y(0)).toBe(100);
    expect(y(10)).toBe(0);
    expect(y(5)).toBe(50);
  });
  it("handles a degenerate domain", () => {
    expect(linearScale([3, 3], [0, 10])(3)).toBe(5);
  });
  it("pads and ticks", () => {
    expect(padDomain([10, 10])[0]).toBeLessThan(10);
    const [lo, hi] = padDomain([0, 100], 0.1);
    expect([lo, hi]).toEqual([-10, 110]);
    expect(niceTicks([0, 100], 5)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(extent([NaN, 2, 5])).toEqual([2, 5]);
  });
});

describe("fanGeometry", () => {
  const g = fanGeometry({
    lastClose: 100,
    medianPath: [101, 102, 103],
    lower50: 100,
    upper50: 106,
    lower90: 96,
    upper90: 110,
    width: 400,
    height: 200,
    margin: { top: 10, right: 10, bottom: 20, left: 40 },
  });
  it("starts at the last close on the left edge and ends at the horizon on the right", () => {
    expect(g.xScale(0)).toBe(40);
    expect(g.xScale(3)).toBe(390);
    expect(g.horizon).toBe(3);
    expect(g.medianPoints.split(" ")).toHaveLength(4);
  });
  it("keeps every value inside the plot area (higher price = smaller y)", () => {
    expect(g.yScale(110)).toBeGreaterThanOrEqual(g.plot.top);
    expect(g.yScale(96)).toBeLessThanOrEqual(g.plot.bottom);
    expect(g.yScale(110)).toBeLessThan(g.yScale(96));
  });
  it("band polygons widen from the last close to the horizon-end quantiles", () => {
    const pts = g.band90.split(" ");
    expect(pts).toHaveLength(3);
    expect(pts[1]).toBe(`${g.xScale(3).toFixed(2)},${g.yScale(110).toFixed(2)}`);
    expect(pts[2]).toBe(`${g.xScale(3).toFixed(2)},${g.yScale(96).toFixed(2)}`);
  });
});

describe("backtest helpers", () => {
  const rows = [
    { model_id: "garch", crps: 1.2, dm_pvalue: 0.4 },
    { model_id: "random-walk", crps: 1.0 },
    { model_id: "drift", crps: 1.1, dm_pvalue: 0.6 },
  ];
  it("puts the baseline first", () => {
    expect(orderRows(rows).map((r) => r.model_id)).toEqual(["random-walk", "garch", "drift"]);
  });
  it("says plainly when the baseline wins", () => {
    const v = baselineVerdict(rows);
    expect(v.baselineWins).toBe(true);
    expect(v.message).toContain("baseline won");
  });
  it("flags non-significant wins", () => {
    const v = baselineVerdict([{ model_id: "random-walk", crps: 1 }, { model_id: "garch", crps: 0.9, dm_pvalue: 0.3 }]);
    expect(v.baselineWins).toBe(false);
    expect(v.message).toContain("noise");
  });
});

describe("gate explanation", () => {
  it("explains a rejected model with reasons", () => {
    const lines = explainGate({
      id: "m1",
      name: "x",
      base_model: "kronos-mini",
      status: "rejected",
      created_at: "",
      promoted_at: null,
      gate: { passed: false, reasons: ["CRPS not better than baseline"], baseline: { crps: 1 }, candidate: { crps: 1.2 } },
    });
    expect(lines[0]).toContain("Rejected");
    expect(lines.join("\n")).toContain("CRPS not better than baseline");
    expect(lines.join("\n")).toContain("baseline 1 vs this model 1.2");
  });
});

describe("session cookie options", () => {
  it("is httpOnly, Lax, and Secure except on http localhost", () => {
    expect(sessionCookieOptions("https:", "app.example.com")).toMatchObject({ httpOnly: true, sameSite: "lax", secure: true });
    expect(sessionCookieOptions("http:", "localhost").secure).toBe(false);
    expect(sessionCookieOptions("http:", "app.example.com").secure).toBe(true);
    expect(isLocalHttp("https:", "localhost")).toBe(false);
  });
});

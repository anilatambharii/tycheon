// Proprietary: see ee/LICENSE
import { describe, expect, it } from "vitest";
import { ApiError, describeError, errorFromResponse, parseRetryAfter } from "@/lib/errors";

const env = (code: string, message: string, details: unknown[] = []) => JSON.stringify({ error: { code, message, details } });

describe("errorFromResponse", () => {
  it("parses the JSON envelope", () => {
    const e = errorFromResponse(422, env("invalid_data", "bad csv", [{ loc: ["body", "symbol"], msg: "required" }]));
    expect(e.status).toBe(422);
    expect(e.code).toBe("invalid_data");
    expect(e.details).toHaveLength(1);
  });
  it("tolerates non-JSON bodies", () => {
    const e = errorFromResponse(502, "<html>bad gateway</html>");
    expect(e.code).toBe("http_error");
    expect(e.message).toContain("502");
  });
  it("defaults 401 code", () => {
    expect(errorFromResponse(401, "").code).toBe("unauthorized");
  });
  it("reads Retry-After seconds", () => {
    expect(errorFromResponse(429, env("rate_limited", "slow"), "12").retryAfter).toBe(12);
    expect(parseRetryAfter(null)).toBeNull();
    expect(parseRetryAfter("garbage")).toBeNull();
  });
});

describe("describeError", () => {
  it("401 -> sign in", () => {
    const v = describeError(errorFromResponse(401, env("unauthorized", "no")));
    expect(v.kind).toBe("auth");
    expect(v.action?.href).toBe("/login");
  });
  it("403 plan_feature -> Not in your plan with upgrade link", () => {
    const v = describeError(errorFromResponse(403, env("plan_feature", "calibration needs Startup")));
    expect(v.kind).toBe("plan");
    expect(v.title).toBe("Not in your plan");
    expect(v.action?.href).toBe("/usage");
  });
  it("403 with another code is not treated as a plan error", () => {
    expect(describeError(errorFromResponse(403, env("forbidden", "no"))).kind).toBe("other");
  });
  it("429 quota_exceeded", () => {
    expect(describeError(errorFromResponse(429, env("quota_exceeded", "used up"))).kind).toBe("quota");
  });
  it("429 rate_limited mentions the wait", () => {
    const v = describeError(errorFromResponse(429, env("rate_limited", "slow"), "30"));
    expect(v.kind).toBe("rate");
    expect(v.message).toContain("30 seconds");
  });
  it("422 renders details", () => {
    const v = describeError(errorFromResponse(422, env("invalid_request", "bad", [{ loc: ["body", "horizon"], msg: "too big" }, "plain"])));
    expect(v.kind).toBe("validation");
    expect(v.lines).toEqual(["body.horizon: too big", "plain"]);
  });
  it("non-ApiError becomes a generic error", () => {
    expect(describeError(new Error("boom")).message).toBe("boom");
    expect(describeError(new ApiError(0, "network_error", "offline")).kind).toBe("other");
  });
});

// Proprietary: see ee/LICENSE
import { describe, expect, it } from "vitest";
import {
  buildForwardHeaders,
  filterResponseHeaders,
  isCsvUploadPath,
  isSameOriginRequest,
  MAX_BODY_CSV,
  MAX_BODY_DEFAULT,
  maxBodyBytes,
  normalizeProxyPath,
  readLimitedBody,
} from "@/lib/proxy-rules";

describe("normalizeProxyPath (allow-list)", () => {
  it("allows /v1/ paths", () => {
    expect(normalizeProxyPath(["v1", "me"])).toBe("v1/me");
    expect(normalizeProxyPath(["v1", "data-sources", "abc", "files", "AAPL"])).toBe("v1/data-sources/abc/files/AAPL");
  });
  it("refuses /auth/ and other prefixes", () => {
    expect(normalizeProxyPath(["auth", "login"])).toBeNull();
    expect(normalizeProxyPath(["admin", "x"])).toBeNull();
    expect(normalizeProxyPath(["v10", "x"])).toBeNull();
    expect(normalizeProxyPath(["v1"])).toBeNull();
    expect(normalizeProxyPath(undefined)).toBeNull();
  });
  it("refuses traversal and smuggling", () => {
    expect(normalizeProxyPath(["v1", "..", "auth", "login"])).toBeNull();
    expect(normalizeProxyPath(["v1", ".", "me"])).toBeNull();
    expect(normalizeProxyPath(["v1", "", "me"])).toBeNull();
    expect(normalizeProxyPath(["v1", "a/b"])).toBeNull();
    expect(normalizeProxyPath(["v1", "a\\b"])).toBeNull();
    expect(normalizeProxyPath(["v1", "%2e%2e"])).toBeNull();
    expect(normalizeProxyPath(["v1", "me?x=1"])).toBeNull();
    expect(normalizeProxyPath(["v1", "me#x"])).toBeNull();
    expect(normalizeProxyPath(["v1", "me\u0000"])).toBeNull();
  });
});

describe("header handling", () => {
  it("never forwards the cookie or browser authorization, sets Bearer", () => {
    const incoming = new Headers({
      cookie: "tycheon_session=secret",
      authorization: "Bearer attacker",
      "content-type": "application/json",
      connection: "keep-alive",
      "transfer-encoding": "chunked",
      host: "dash.example",
      origin: "https://dash.example",
      "x-forwarded-for": "1.2.3.4",
    });
    const out = buildForwardHeaders(incoming, "TOKEN");
    expect(out.get("cookie")).toBeNull();
    expect(out.get("authorization")).toBe("Bearer TOKEN");
    expect(out.get("content-type")).toBe("application/json");
    expect(out.get("connection")).toBeNull();
    expect(out.get("transfer-encoding")).toBeNull();
    expect(out.get("host")).toBeNull();
    expect(out.get("origin")).toBeNull();
    expect(out.get("x-forwarded-for")).toBeNull();
  });
  it("strips hop-by-hop, cookies and encoding headers from responses but keeps retry-after", () => {
    const up = new Headers({
      "content-type": "application/json",
      "retry-after": "7",
      "set-cookie": "x=1",
      connection: "close",
      "content-encoding": "gzip",
      "content-length": "10",
    });
    const out = filterResponseHeaders(up);
    expect(out.get("retry-after")).toBe("7");
    expect(out.get("content-type")).toBe("application/json");
    expect(out.get("set-cookie")).toBeNull();
    expect(out.get("connection")).toBeNull();
    expect(out.get("content-encoding")).toBeNull();
    expect(out.get("content-length")).toBeNull();
  });
});

describe("origin check", () => {
  it("always allows safe methods", () => {
    expect(isSameOriginRequest("GET", new Headers({ origin: "https://evil.example", host: "a.example" }))).toBe(true);
  });
  it("allows same-origin state changes", () => {
    expect(isSameOriginRequest("POST", new Headers({ origin: "https://a.example", host: "a.example" }))).toBe(true);
    expect(isSameOriginRequest("DELETE", new Headers({ origin: "http://localhost:3000", host: "localhost:3000" }))).toBe(true);
  });
  it("rejects cross-site and malformed origins", () => {
    expect(isSameOriginRequest("POST", new Headers({ origin: "https://evil.example", host: "a.example" }))).toBe(false);
    expect(isSameOriginRequest("PUT", new Headers({ origin: "https://a.example:444", host: "a.example" }))).toBe(false);
    expect(isSameOriginRequest("POST", new Headers({ origin: "null", host: "a.example" }))).toBe(false);
  });
  it("rejects a missing Origin unless the browser says same-origin", () => {
    expect(isSameOriginRequest("POST", new Headers({ host: "a.example" }))).toBe(false);
    expect(isSameOriginRequest("POST", new Headers({ host: "a.example", "sec-fetch-site": "cross-site" }))).toBe(false);
    expect(isSameOriginRequest("POST", new Headers({ host: "a.example", "sec-fetch-site": "same-origin" }))).toBe(true);
  });
  it("prefers x-forwarded-host", () => {
    expect(isSameOriginRequest("POST", new Headers({ origin: "https://public.example", host: "internal:3000", "x-forwarded-host": "public.example" }))).toBe(true);
  });
});

describe("body size limits", () => {
  it("uses 25 MB only for CSV upload PUT", () => {
    expect(isCsvUploadPath("PUT", "v1/data-sources/abc/files/AAPL")).toBe(true);
    expect(maxBodyBytes("PUT", "v1/data-sources/abc/files/AAPL")).toBe(MAX_BODY_CSV);
    expect(maxBodyBytes("POST", "v1/data-sources/abc/files/AAPL")).toBe(MAX_BODY_DEFAULT);
    expect(maxBodyBytes("PUT", "v1/routing")).toBe(MAX_BODY_DEFAULT);
    expect(MAX_BODY_CSV).toBe(25 * 1024 * 1024);
    expect(MAX_BODY_DEFAULT).toBe(1024 * 1024);
  });
  it("readLimitedBody rejects by declared length", async () => {
    const r = await readLimitedBody(null, "2000000", MAX_BODY_DEFAULT);
    expect(r).toEqual({ ok: false, reason: "too_large" });
  });
  it("readLimitedBody aborts streams that exceed the limit", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new Uint8Array(60));
        c.enqueue(new Uint8Array(60));
        c.close();
      },
    });
    expect(await readLimitedBody(stream, null, 100)).toEqual({ ok: false, reason: "too_large" });
  });
  it("readLimitedBody returns bodies within the limit", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new Uint8Array([1, 2]));
        c.enqueue(new Uint8Array([3]));
        c.close();
      },
    });
    const r = await readLimitedBody(stream, null, 100);
    expect(r.ok && Array.from(r.body)).toEqual([1, 2, 3]);
  });
});

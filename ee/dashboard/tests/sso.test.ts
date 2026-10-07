// Proprietary: see ee/LICENSE
import { describe, expect, it } from "vitest";
import { dashboardUrl, isValidSsoSlug, isValidTicket, ssoLoginUrl, SSO_FAILURE_PATH } from "@/lib/sso";

describe("sso slug validation", () => {
  it("accepts valid slugs", () => {
    for (const s of ["ab", "acme", "acme-corp", "a1", "a".repeat(39)]) expect(isValidSsoSlug(s)).toBe(true);
  });
  it("rejects invalid slugs", () => {
    for (const s of ["", "a", "-acme", "Acme", "acme_corp", "a/b", "../x", "a b", "acme?x=1", "a".repeat(40)]) expect(isValidSsoSlug(s)).toBe(false);
  });
});

describe("sso ticket check", () => {
  it("rejects missing, empty and oversized tickets", () => {
    expect(isValidTicket(null)).toBe(false);
    expect(isValidTicket(undefined)).toBe(false);
    expect(isValidTicket("")).toBe(false);
    expect(isValidTicket("x".repeat(4001))).toBe(false);
  });
  it("accepts tickets up to 4000 chars", () => {
    expect(isValidTicket("x".repeat(4000))).toBe(true);
    expect(isValidTicket("a.b.c")).toBe(true);
  });
});

describe("redirect targets", () => {
  it("builds the control-plane login URL from the public base", () => {
    expect(ssoLoginUrl("http://localhost:8080", "acme")).toBe("http://localhost:8080/auth/oidc/acme/login");
    expect(ssoLoginUrl("https://api.example.com/", "acme-corp")).toBe("https://api.example.com/auth/oidc/acme-corp/login");
  });
  it("builds dashboard URLs on the request origin", () => {
    expect(dashboardUrl("http://localhost:3000/api/auth/sso/complete?ticket=x", new Headers({ host: "localhost:3000" }), "/")).toBe("http://localhost:3000/");
  });
  it("honours forwarded host and proto and never includes the ticket", () => {
    const h = new Headers({ host: "internal:3000", "x-forwarded-host": "dash.example.com", "x-forwarded-proto": "https" });
    const url = dashboardUrl("http://internal:3000/api/auth/sso/complete?ticket=SECRET", h, SSO_FAILURE_PATH);
    expect(url).toBe("https://dash.example.com/login?error=sso");
    expect(url).not.toContain("SECRET");
  });
});

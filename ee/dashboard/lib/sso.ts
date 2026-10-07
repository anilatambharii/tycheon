// Proprietary: see ee/LICENSE
// Pure helpers for the SSO flow. The ticket is a secret: never log or echo it.

export const SSO_SLUG_RE = /^[a-z0-9][a-z0-9-]{1,38}$/;
export const MAX_TICKET_LENGTH = 4000;

export function isValidSsoSlug(slug: string): boolean {
  return SSO_SLUG_RE.test(slug);
}

/** A ticket is acceptable if present and at most 4000 characters. */
export function isValidTicket(ticket: string | null | undefined): ticket is string {
  return typeof ticket === "string" && ticket.length > 0 && ticket.length <= MAX_TICKET_LENGTH;
}

export function defaultPublicUrl(): string {
  return (process.env.CONTROL_PLANE_PUBLIC_URL || "http://localhost:8080").replace(/\/+$/, "");
}

/** Control-plane login URL the browser is sent to directly (the BFF must not proxy this hop). */
export function ssoLoginUrl(publicBase: string, slug: string): string {
  return `${publicBase.replace(/\/+$/, "")}/auth/oidc/${encodeURIComponent(slug)}/login`;
}

/** Absolute URL on the dashboard's own origin, honouring a reverse proxy's forwarded headers. */
export function dashboardUrl(reqUrl: string, headers: Headers, path: string): string {
  const u = new URL(reqUrl);
  const host = (headers.get("x-forwarded-host") ?? headers.get("host") ?? u.host).split(",")[0].trim();
  const proto = (headers.get("x-forwarded-proto") ?? u.protocol.replace(":", "")).split(",")[0].trim();
  const safeProto = proto === "https" ? "https" : "http";
  return `${safeProto}://${host}${path.startsWith("/") ? path : `/${path}`}`;
}

export const SSO_FAILURE_PATH = "/login?error=sso";

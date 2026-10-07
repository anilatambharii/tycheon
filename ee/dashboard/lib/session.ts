// Proprietary: see ee/LICENSE
// Server-side session cookie helpers. The token never reaches browser JavaScript.

export const SESSION_COOKIE = "tycheon_session";
export const SESSION_MAX_AGE = 60 * 60 * 24 * 7;

export function controlPlaneUrl(): string {
  return (process.env.CONTROL_PLANE_URL || "http://localhost:8080").replace(/\/+$/, "");
}

export function isLocalHttp(protocol: string, hostname: string): boolean {
  const local = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]" || hostname === "::1";
  return local && protocol.replace(":", "") === "http";
}

export interface CookieOptions {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  maxAge: number;
}

/** Secure everywhere except plain-http localhost. */
export function sessionCookieOptions(protocol: string, hostname: string): CookieOptions {
  return {
    httpOnly: true,
    secure: !isLocalHttp(protocol, hostname),
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE,
  };
}

/** Protocol as seen by the client (honours a reverse proxy). */
export function requestProtocol(url: URL, headers: Headers): string {
  const fwd = headers.get("x-forwarded-proto");
  return fwd ? `${fwd.split(",")[0].trim()}:` : url.protocol;
}

export function requestHostname(url: URL, headers: Headers): string {
  const host = headers.get("x-forwarded-host") ?? headers.get("host");
  if (!host) return url.hostname;
  const first = host.split(",")[0].trim();
  // Strip port, keep bracketed IPv6.
  if (first.startsWith("[")) return first.slice(0, first.indexOf("]") + 1);
  return first.split(":")[0];
}

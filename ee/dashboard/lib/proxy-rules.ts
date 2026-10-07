// Proprietary: see ee/LICENSE
// Pure helpers used by the BFF proxy (app/api/cp/[...path]/route.ts).

export const ALLOWED_PREFIXES = ["v1/"] as const;

export const MAX_BODY_DEFAULT = 1 * 1024 * 1024;
export const MAX_BODY_CSV = 25 * 1024 * 1024;

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "proxy-connection",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

/** Request headers the browser may influence upstream. Everything else is dropped. */
const FORWARD_REQUEST_HEADERS = new Set([
  "content-type",
  "accept",
  "accept-language",
  "idempotency-key",
]);

/** Response headers never copied back to the browser. */
const DROP_RESPONSE_HEADERS = new Set([
  ...HOP_BY_HOP,
  "set-cookie",
  "set-cookie2",
  "content-encoding", // fetch() already decoded the body
  "content-length",
  "content-security-policy",
  "x-frame-options",
]);

/**
 * Validate and normalise the catch-all path segments.
 * Returns the upstream path (no leading slash, segments re-encoded) or null if refused.
 */
export function normalizeProxyPath(segments: string[] | undefined): string | null {
  if (!segments || segments.length < 2) return null;
  for (const seg of segments) {
    if (seg === "" || seg === "." || seg === "..") return null;
    // Reject separators, encoded leftovers, control characters and query/fragment smuggling.
    if (/[/\\%?#\u0000-\u001f\u007f]/.test(seg)) return null;
  }
  const joined = segments.map((s) => encodeURIComponent(s)).join("/");
  if (!ALLOWED_PREFIXES.some((p) => joined.startsWith(p))) return null;
  return joined;
}

export function isHopByHop(name: string): boolean {
  return HOP_BY_HOP.has(name.toLowerCase());
}

/** Build upstream request headers: allow-list only, never the cookie, always our Bearer. */
export function buildForwardHeaders(incoming: Headers, token: string): Headers {
  const out = new Headers();
  incoming.forEach((value, name) => {
    const n = name.toLowerCase();
    if (isHopByHop(n)) return;
    if (!FORWARD_REQUEST_HEADERS.has(n)) return;
    out.set(n, value);
  });
  out.set("authorization", `Bearer ${token}`);
  return out;
}

export function filterResponseHeaders(upstream: Headers): Headers {
  const out = new Headers();
  upstream.forEach((value, name) => {
    if (DROP_RESPONSE_HEADERS.has(name.toLowerCase())) return;
    out.set(name, value);
  });
  return out;
}

/** CSV upload: PUT /v1/data-sources/{id}/files/{SYMBOL}. */
export function isCsvUploadPath(method: string, path: string): boolean {
  return method.toUpperCase() === "PUT" && /^v1\/data-sources\/[^/]+\/files\/[^/]+$/.test(path);
}

export function maxBodyBytes(method: string, path: string): number {
  return isCsvUploadPath(method, path) ? MAX_BODY_CSV : MAX_BODY_DEFAULT;
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/**
 * CSRF guard. Non-GET requests must come from our own origin.
 * `Origin` is compared with the Host the request arrived on.
 */
export function isSameOriginRequest(method: string, headers: Headers): boolean {
  if (SAFE_METHODS.has(method.toUpperCase())) return true;
  const origin = headers.get("origin");
  const host = headers.get("x-forwarded-host") ?? headers.get("host");
  if (!origin) {
    // Some same-origin fetches omit Origin; the browser still labels them.
    return headers.get("sec-fetch-site") === "same-origin" && !!host;
  }
  if (!host) return false;
  try {
    return new URL(origin).host.toLowerCase() === host.toLowerCase();
  } catch {
    return false;
  }
}

export type BodyReadResult =
  | { ok: true; body: Uint8Array }
  | { ok: false; reason: "too_large" };

/** Read a request body, aborting as soon as it exceeds `limit` bytes. */
export async function readLimitedBody(
  stream: ReadableStream<Uint8Array> | null,
  declaredLength: string | null,
  limit: number,
): Promise<BodyReadResult> {
  if (declaredLength !== null) {
    const n = Number(declaredLength);
    if (Number.isFinite(n) && n > limit) return { ok: false, reason: "too_large" };
  }
  if (!stream) return { ok: true, body: new Uint8Array(0) };
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > limit) {
      await reader.cancel().catch(() => undefined);
      return { ok: false, reason: "too_large" };
    }
    chunks.push(value);
  }
  const body = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    body.set(c, offset);
    offset += c.byteLength;
  }
  return { ok: true, body };
}

export function errorEnvelope(code: string, message: string, details: unknown[] = []) {
  return { error: { code, message, details } };
}

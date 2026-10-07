// Proprietary: see ee/LICENSE
// Server-only helpers shared by the auth route handlers. Never log tokens.
import { NextRequest, NextResponse } from "next/server";
import {
  controlPlaneUrl,
  requestHostname,
  requestProtocol,
  SESSION_COOKIE,
  sessionCookieOptions,
} from "./session";
import { errorEnvelope, isSameOriginRequest, MAX_BODY_DEFAULT, readLimitedBody } from "./proxy-rules";

export function jsonError(status: number, code: string, message: string, extra?: HeadersInit) {
  return NextResponse.json(errorEnvelope(code, message), { status, headers: extra });
}

export function setSessionCookie(res: NextResponse, req: NextRequest, token: string) {
  const url = new URL(req.url);
  res.cookies.set(SESSION_COOKIE, token, sessionCookieOptions(requestProtocol(url, req.headers), requestHostname(url, req.headers)));
}

export function clearSessionCookie(res: NextResponse, req: NextRequest) {
  const url = new URL(req.url);
  res.cookies.set(SESSION_COOKIE, "", {
    ...sessionCookieOptions(requestProtocol(url, req.headers), requestHostname(url, req.headers)),
    maxAge: 0,
  });
}

interface AuthSuccess {
  token: string;
  org_id?: string;
  user_id?: string;
  role?: string;
}

/** Convert a control-plane auth response into a cookie-setting response without exposing the token. */
export async function finishAuth(req: NextRequest, upstream: Response, successStatus = 200): Promise<NextResponse> {
  const text = await upstream.text();
  if (!upstream.ok) {
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      body = errorEnvelope("upstream_error", `Authentication failed (HTTP ${upstream.status}).`);
    }
    const headers: Record<string, string> = {};
    const ra = upstream.headers.get("retry-after");
    if (ra) headers["retry-after"] = ra;
    return NextResponse.json(body, { status: upstream.status, headers });
  }
  let parsed: AuthSuccess;
  try {
    parsed = JSON.parse(text) as AuthSuccess;
  } catch {
    return jsonError(502, "bad_upstream", "The control plane returned an unreadable response.");
  }
  if (typeof parsed.token !== "string" || parsed.token === "") {
    return jsonError(502, "bad_upstream", "The control plane did not return a session.");
  }
  const res = NextResponse.json({ ok: true, role: parsed.role ?? null, org_id: parsed.org_id ?? null }, { status: successStatus });
  setSessionCookie(res, req, parsed.token);
  return res;
}

export async function proxyAuthJson(req: NextRequest, upstreamPath: string, successStatus: number): Promise<NextResponse> {
  if (!isSameOriginRequest(req.method, req.headers)) {
    return jsonError(403, "cross_site_request", "Cross-site requests are not allowed.");
  }
  const body = await readLimitedBody(req.body, req.headers.get("content-length"), MAX_BODY_DEFAULT);
  if (!body.ok) return jsonError(413, "payload_too_large", "Request body is too large.");
  let upstream: Response;
  try {
    upstream = await fetch(`${controlPlaneUrl()}${upstreamPath}`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: Buffer.from(body.body),
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
  } catch {
    return jsonError(502, "upstream_unavailable", "The control plane is not reachable.");
  }
  return finishAuth(req, upstream, successStatus);
}

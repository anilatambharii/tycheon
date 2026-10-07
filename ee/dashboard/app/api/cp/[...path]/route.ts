// Proprietary: see ee/LICENSE
// Backend-for-frontend proxy: browser -> /api/cp/v1/... -> control plane with Bearer from the httpOnly cookie.
import { NextRequest, NextResponse } from "next/server";
import {
  buildForwardHeaders,
  errorEnvelope,
  filterResponseHeaders,
  isSameOriginRequest,
  maxBodyBytes,
  normalizeProxyPath,
  readLimitedBody,
} from "@/lib/proxy-rules";
import { controlPlaneUrl, SESSION_COOKIE } from "@/lib/session";
import { clearSessionCookie, jsonError } from "@/lib/server-auth";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ path: string[] }> };

async function handle(req: NextRequest, ctx: Ctx): Promise<NextResponse> {
  const { path: segments } = await ctx.params;
  const path = normalizeProxyPath(segments);
  if (!path) return jsonError(404, "not_found", "Unknown path.");

  if (!isSameOriginRequest(req.method, req.headers)) {
    return jsonError(403, "cross_site_request", "Cross-site requests are not allowed.");
  }

  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return jsonError(401, "unauthorized", "Not signed in.");

  let body: Uint8Array | undefined;
  if (req.method !== "GET" && req.method !== "HEAD") {
    const read = await readLimitedBody(req.body, req.headers.get("content-length"), maxBodyBytes(req.method, path));
    if (!read.ok) return jsonError(413, "payload_too_large", "Request body is too large.");
    if (read.body.byteLength > 0) body = read.body;
  }

  const search = new URL(req.url).search;
  let upstream: Response;
  try {
    upstream = await fetch(`${controlPlaneUrl()}/${path}${search}`, {
      method: req.method,
      headers: buildForwardHeaders(req.headers, token),
      body: body ? Buffer.from(body) : undefined,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(120_000),
    });
  } catch {
    return jsonError(502, "upstream_unavailable", "The control plane is not reachable.");
  }

  if (upstream.status >= 300 && upstream.status < 400) {
    return NextResponse.json(errorEnvelope("bad_upstream", "Unexpected redirect from the control plane."), { status: 502 });
  }

  const headers = filterResponseHeaders(upstream.headers);
  headers.set("x-content-type-options", "nosniff");
  headers.set("content-security-policy", "sandbox");
  headers.set("cache-control", "no-store");
  const noBody = upstream.status === 204 || upstream.status === 205 || upstream.status === 304;
  const res = new NextResponse(noBody ? null : upstream.body, { status: upstream.status, headers });
  if (upstream.status === 401) clearSessionCookie(res, req);
  return res;
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const DELETE = handle;

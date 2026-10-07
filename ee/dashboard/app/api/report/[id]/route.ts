// Proprietary: see ee/LICENSE
// Serves a risk report's self-contained HTML for display inside a sandboxed iframe only.
import { NextRequest, NextResponse } from "next/server";
import { controlPlaneUrl, SESSION_COOKIE } from "@/lib/session";
import { jsonError } from "@/lib/server-auth";

export const dynamic = "force-dynamic";

const ID_RE = /^[A-Za-z0-9_-]{1,128}$/;

export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  if (!ID_RE.test(id)) return jsonError(404, "not_found", "Unknown report.");
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return jsonError(401, "unauthorized", "Not signed in.");

  let upstream: Response;
  try {
    upstream = await fetch(`${controlPlaneUrl()}/v1/report/${encodeURIComponent(id)}?fmt=html`, {
      headers: { authorization: `Bearer ${token}`, accept: "text/html" },
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
  } catch {
    return jsonError(502, "upstream_unavailable", "The control plane is not reachable.");
  }
  if (!upstream.ok) {
    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: { "content-type": "application/json", "x-content-type-options": "nosniff", "content-security-policy": "sandbox" },
    });
  }
  const html = await upstream.text();
  return new NextResponse(html, {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      // Opaque origin, no scripts, no network: the report is static content.
      "content-security-policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:",
      "x-content-type-options": "nosniff",
      "x-frame-options": "SAMEORIGIN",
      "cache-control": "no-store",
    },
  });
}

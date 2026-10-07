// Proprietary: see ee/LICENSE
import { NextRequest, NextResponse } from "next/server";
import { isSameOriginRequest } from "@/lib/proxy-rules";
import { clearSessionCookie, jsonError } from "@/lib/server-auth";

export const dynamic = "force-dynamic";

export function POST(req: NextRequest) {
  if (!isSameOriginRequest(req.method, req.headers)) {
    return jsonError(403, "cross_site_request", "Cross-site requests are not allowed.");
  }
  const res = NextResponse.json({ ok: true });
  clearSessionCookie(res, req);
  return res;
}

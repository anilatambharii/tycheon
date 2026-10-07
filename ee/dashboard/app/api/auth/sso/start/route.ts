// Proprietary: see ee/LICENSE
// Starts Enterprise SSO: asks the control plane for the IdP redirect and sends the browser there.
import { NextRequest, NextResponse } from "next/server";
import { SLUG_RE } from "@/lib/format";
import { controlPlaneUrl } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const slug = (new URL(req.url).searchParams.get("slug") ?? "").trim().toLowerCase();
  if (!SLUG_RE.test(slug)) {
    return NextResponse.redirect(new URL("/sso?error=slug", req.url), 303);
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${controlPlaneUrl()}/auth/oidc/${encodeURIComponent(slug)}/login`, {
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    return NextResponse.redirect(new URL("/sso?error=unavailable", req.url), 303);
  }
  const location = upstream.headers.get("location");
  if (upstream.status >= 300 && upstream.status < 400 && location) {
    try {
      const target = new URL(location, controlPlaneUrl());
      if (target.protocol === "https:" || target.protocol === "http:") {
        return NextResponse.redirect(target.toString(), 302);
      }
    } catch {
      /* fall through */
    }
  }
  return NextResponse.redirect(new URL("/sso?error=notfound", req.url), 303);
}

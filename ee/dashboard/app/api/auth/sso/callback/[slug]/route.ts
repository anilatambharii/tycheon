// Proprietary: see ee/LICENSE
// SSO callback: exchange the IdP response with the control plane and set the session cookie.
import { NextRequest, NextResponse } from "next/server";
import { SLUG_RE } from "@/lib/format";
import { finishAuth } from "@/lib/server-auth";
import { controlPlaneUrl } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest, ctx: { params: Promise<{ slug: string }> }) {
  const { slug } = await ctx.params;
  if (!SLUG_RE.test(slug)) return NextResponse.redirect(new URL("/sso?error=slug", req.url), 303);
  const search = new URL(req.url).search;
  let upstream: Response;
  try {
    upstream = await fetch(`${controlPlaneUrl()}/auth/oidc/${encodeURIComponent(slug)}/callback${search}`, {
      redirect: "manual",
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(30_000),
    });
  } catch {
    return NextResponse.redirect(new URL("/sso?error=unavailable", req.url), 303);
  }
  const result = await finishAuth(req, upstream);
  if (!result.ok) return NextResponse.redirect(new URL("/sso?error=failed", req.url), 303);
  // Reuse the cookie finishAuth set, but navigate to the app.
  const redirect = NextResponse.redirect(new URL("/", req.url), 303);
  for (const c of result.cookies.getAll()) redirect.cookies.set(c);
  return redirect;
}

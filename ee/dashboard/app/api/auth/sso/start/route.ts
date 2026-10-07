// Proprietary: see ee/LICENSE
// Starts Enterprise SSO by sending the browser straight to the control plane's public login URL.
// The control plane sets its state cookie on its own origin, so this hop is NOT proxied.
import { NextRequest, NextResponse } from "next/server";
import { dashboardUrl, defaultPublicUrl, isValidSsoSlug, ssoLoginUrl } from "@/lib/sso";

export const dynamic = "force-dynamic";

export function GET(req: NextRequest) {
  const slug = (new URL(req.url).searchParams.get("slug") ?? "").trim().toLowerCase();
  if (!isValidSsoSlug(slug)) {
    return NextResponse.redirect(dashboardUrl(req.url, req.headers, "/sso?error=slug"), 303);
  }
  return NextResponse.redirect(ssoLoginUrl(defaultPublicUrl(), slug), 302);
}

// Proprietary: see ee/LICENSE
// The control plane redirects the browser here with a single-use ticket (valid 60 s).
// Exchange it server-side and set the session cookie. Never log or echo the ticket.
import { NextRequest, NextResponse } from "next/server";
import { setSessionCookie } from "@/lib/server-auth";
import { controlPlaneUrl } from "@/lib/session";
import { dashboardUrl, isValidTicket, SSO_FAILURE_PATH } from "@/lib/sso";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const fail = () => NextResponse.redirect(dashboardUrl(req.url, req.headers, SSO_FAILURE_PATH), 303);
  const ticket = new URL(req.url).searchParams.get("ticket");
  if (!isValidTicket(ticket)) return fail();

  let upstream: Response;
  try {
    upstream = await fetch(`${controlPlaneUrl()}/auth/sso/exchange`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ ticket }),
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    return fail();
  }
  if (upstream.status !== 200) return fail();

  let token: unknown;
  try {
    token = ((await upstream.json()) as { token?: unknown }).token;
  } catch {
    return fail();
  }
  if (typeof token !== "string" || token === "") return fail();

  const res = NextResponse.redirect(dashboardUrl(req.url, req.headers, "/"), 303);
  res.headers.set("cache-control", "no-store");
  res.headers.set("referrer-policy", "no-referrer");
  setSessionCookie(res, req, token);
  return res;
}

// Proprietary: see ee/LICENSE
import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = "tycheon_session";
const PUBLIC_PAGES = new Set(["/login", "/signup", "/sso"]);

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  // API routes enforce their own auth and return JSON 401s.
  if (pathname.startsWith("/api/")) return NextResponse.next();
  if (PUBLIC_PAGES.has(pathname)) return NextResponse.next();
  if (!req.cookies.get(SESSION_COOKIE)?.value) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

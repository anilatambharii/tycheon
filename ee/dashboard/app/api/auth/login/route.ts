// Proprietary: see ee/LICENSE
import type { NextRequest } from "next/server";
import { proxyAuthJson } from "@/lib/server-auth";

export const dynamic = "force-dynamic";

export function POST(req: NextRequest) {
  return proxyAuthJson(req, "/auth/login", 200);
}

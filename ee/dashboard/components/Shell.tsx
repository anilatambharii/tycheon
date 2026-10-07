// Proprietary: see ee/LICENSE
"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useState, type ReactNode } from "react";
import { api, authPost, type Me } from "@/lib/api";
import { ApiError } from "@/lib/errors";
import { DEVELOPER_BANNER } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { ErrorBox, Loading, StatusBadge } from "./ui";

const MeContext = createContext<Me | null>(null);
export const useMe = () => useContext(MeContext);

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/forecast", label: "Forecast" },
  { href: "/risk", label: "Risk" },
  { href: "/calibration", label: "Calibration" },
  { href: "/backtest", label: "Backtest" },
  { href: "/finetune", label: "Fine-tuning" },
  { href: "/data", label: "Data" },
  { href: "/keys", label: "API keys" },
  { href: "/usage", label: "Usage and billing" },
  { href: "/leaderboard", label: "Leaderboard" },
];

export default function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const me = useAsync(() => api.me());

  async function signOut() {
    try {
      await authPost("/api/auth/logout");
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  const unauth = me.error instanceof ApiError && me.error.status === 401;

  return (
    <div className="flex min-h-full flex-col md:flex-row">
      <header className="flex items-center justify-between border-b p-3 md:hidden" style={{ borderColor: "var(--border)" }}>
        <span className="font-semibold">Tycheon Cloud</span>
        <button type="button" className="btn" aria-expanded={open} aria-controls="main-nav" onClick={() => setOpen((o) => !o)}>
          Menu
        </button>
      </header>
      <aside
        className={`${open ? "block" : "hidden"} border-b p-3 md:block md:w-56 md:shrink-0 md:border-b-0 md:border-r`}
        style={{ borderColor: "var(--border)", background: "var(--surface)" }}
      >
        <p className="mb-3 hidden text-lg font-semibold md:block">Tycheon Cloud</p>
        {me.data && (
          <div className="mb-3 text-sm">
            <p className="truncate font-medium" title={me.data.org.name}>
              {me.data.org.name}
            </p>
            <p className="muted truncate" title={me.data.email}>
              {me.data.email}
            </p>
            <p className="mt-1">
              <StatusBadge tone="neutral">Plan: {me.data.plan.name}</StatusBadge>
            </p>
          </div>
        )}
        <nav id="main-nav" aria-label="Main">
          <ul className="space-y-1">
            {NAV.map((n) => {
              const active = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
              return (
                <li key={n.href}>
                  <Link
                    href={n.href}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setOpen(false)}
                    className="block rounded-md px-2 py-1.5 text-sm no-underline"
                    style={{ background: active ? "var(--bg)" : "transparent", fontWeight: active ? 600 : 400, color: "var(--text)" }}
                  >
                    {n.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <button type="button" className="btn mt-4 w-full" onClick={signOut}>
          Sign out
        </button>
      </aside>
      <main className="min-w-0 flex-1 p-4 md:p-6">
        {me.data?.plan.research_use_only && (
          <div role="note" className="card mb-4 text-sm font-medium" style={{ borderColor: "var(--warn)", color: "var(--warn)" }}>
            {DEVELOPER_BANNER}
          </div>
        )}
        {me.loading && <Loading what="Loading your account" />}
        {unauth ? (
          <ErrorBox error={me.error} />
        ) : me.error ? (
          <ErrorBox error={me.error} />
        ) : null}
        <MeContext.Provider value={me.data}>{!unauth && children}</MeContext.Provider>
      </main>
    </div>
  );
}

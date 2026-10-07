// Proprietary: see ee/LICENSE
"use client";
import Link from "next/link";
import type { ReactNode } from "react";
import { describeError } from "@/lib/errors";

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const v = describeError(error);
  return (
    <div role="alert" className="card my-3 border-l-4" style={{ borderLeftColor: v.kind === "plan" ? "var(--warn)" : "var(--bad)" }}>
      <p className="font-semibold">{v.title}</p>
      <p className="text-sm">{v.message}</p>
      {v.lines.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-sm">
          {v.lines.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
        </ul>
      )}
      {v.action && (
        <p className="mt-2 text-sm">
          <Link href={v.action.href} className="font-medium underline">
            {v.action.label}
          </Link>
        </p>
      )}
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p role="status" aria-live="polite" className="muted text-sm">
      {what}...
    </p>
  );
}

export function Field({
  label,
  id,
  hint,
  children,
}: {
  label: string;
  id: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="mb-3">
      <label htmlFor={id} className="label">
        {label}
      </label>
      {children}
      {hint && (
        <p id={`${id}-hint`} className="muted mt-1 text-xs">
          {hint}
        </p>
      )}
    </div>
  );
}

export function PageTitle({ children }: { children: ReactNode }) {
  return <h1 className="h1">{children}</h1>;
}

export function Table({ head, children, caption }: { head: string[]; children: ReactNode; caption?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[32rem] border-collapse">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col" className="th">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function StatusBadge({ tone, children }: { tone: "ok" | "warn" | "bad" | "neutral"; children: ReactNode }) {
  const color = tone === "ok" ? "var(--ok)" : tone === "warn" ? "var(--warn)" : tone === "bad" ? "var(--bad)" : "var(--muted)";
  return (
    <span className="badge" style={{ color, borderColor: color }}>
      {children}
    </span>
  );
}

export function ProgressBar({ value, max, label }: { value: number; max: number | null; label: string }) {
  const pct = max && max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const hot = max !== null && max > 0 && value / max >= 0.9;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max ?? undefined}
      aria-valuenow={Math.min(value, max ?? value)}
      aria-valuetext={max === null ? `${value} used, no limit` : `${value} of ${max}`}
      className="h-2 w-full overflow-hidden rounded-full"
      style={{ background: "var(--border)" }}
    >
      <div className="h-full" style={{ width: `${pct}%`, background: hot ? "var(--bad)" : "var(--accent)" }} />
    </div>
  );
}

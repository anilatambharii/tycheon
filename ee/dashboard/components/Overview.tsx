// Proprietary: see ee/LICENSE
"use client";
import Link from "next/link";
import { api, type Meter } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { useMe } from "./Shell";
import { ErrorBox, Loading, ProgressBar } from "./ui";

const METER_LABELS: Record<string, string> = {
  forecast_calls: "Forecast calls",
  calibration_reports: "Calibration reports",
  risk_reports: "Risk reports",
  backtest_compute_seconds: "Backtest compute",
  gpu_seconds: "GPU time",
};

export function MeterList({ meters }: { meters: Meter[] }) {
  if (meters.length === 0) return <p className="muted text-sm">No metered usage yet.</p>;
  return (
    <ul className="space-y-4">
      {meters.map((m) => {
        const label = METER_LABELS[m.kind] ?? m.kind;
        return (
          <li key={m.kind}>
            <div className="mb-1 flex justify-between gap-2 text-sm">
              <span>{label}</span>
              <span className="muted">
                {m.used.toLocaleString()} / {m.limit === null || m.limit === undefined ? "no limit" : m.limit.toLocaleString()} {m.unit}
                {m.period ? ` (${m.period})` : ""}
              </span>
            </div>
            <ProgressBar value={m.used} max={m.limit ?? null} label={label} />
          </li>
        );
      })}
    </ul>
  );
}

export function Checklist() {
  const sources = useAsync(() => api.listDataSources());
  const keys = useAsync(() => api.listKeys());
  const usage = useAsync(() => api.usage());
  const loading = sources.loading || keys.loading || usage.loading;
  const forecasted = (usage.data?.meters.find((m) => m.kind === "forecast_calls")?.used ?? 0) > 0;
  const steps = [
    { done: (sources.data?.length ?? 0) > 0, label: "Add a CSV data source", href: "/data" },
    { done: (keys.data?.filter((k) => !k.revoked_at).length ?? 0) > 0, label: "Create an API key", href: "/keys" },
    { done: forecasted, label: "Make your first forecast", href: "/forecast" },
  ];
  return (
    <section aria-labelledby="checklist-title" className="card">
      <h2 id="checklist-title" className="h2">
        Getting started
      </h2>
      {loading && <Loading />}
      <ol className="space-y-2">
        {steps.map((s, i) => (
          <li key={s.href} className="flex items-center gap-2 text-sm">
            <span aria-hidden="true">{s.done ? "✓" : `${i + 1}.`}</span>
            <Link href={s.href}>{s.label}</Link>
            <span className="sr-only">{s.done ? "(done)" : "(to do)"}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function Overview() {
  const usage = useAsync(() => api.usage());
  const meData = useMe();
  const me = { data: meData, loading: meData === null, error: null as unknown };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="card" aria-labelledby="plan-title">
        <h2 id="plan-title" className="h2">
          Plan
        </h2>
        {me.loading && <Loading />}
        <ErrorBox error={me.error} />
        {me.data && (
          <div className="text-sm">
            <p className="text-base font-semibold">{me.data.plan.name}</p>
            <p className="muted">Status: {me.data.plan.status}</p>
            <p className="mt-2">Features: {me.data.plan.features.length ? me.data.plan.features.join(", ") : "none listed"}</p>
            <p className="mt-2">
              <Link href="/usage">Usage and billing</Link>
            </p>
          </div>
        )}
      </section>
      <Checklist />
      <section className="card lg:col-span-2" aria-labelledby="usage-title">
        <h2 id="usage-title" className="h2">
          Usage this period
        </h2>
        {usage.loading && <Loading />}
        <ErrorBox error={usage.error} />
        {usage.data && <MeterList meters={usage.data.meters} />}
      </section>
    </div>
  );
}

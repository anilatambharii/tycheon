// Proprietary: see ee/LICENSE
"use client";
import { useEffect, useState, type FormEvent } from "react";
import { ErrorBox, Field, Loading, PageTitle, StatusBadge, Table } from "@/components/ui";
import { api, type FinetuneJob, type JobStatus, type TunedModel } from "@/lib/api";
import { formatDate, SYMBOL_RE } from "@/lib/format";
import { explainGate } from "@/lib/gate";
import { useAction, useAsync } from "@/lib/hooks";

const POLL_MS = 5000;
const ACTIVE: JobStatus[] = ["queued", "running"];

function jobTone(s: JobStatus): "ok" | "warn" | "bad" | "neutral" {
  return s === "succeeded" ? "ok" : s === "failed" ? "bad" : s === "cancelled" ? "neutral" : "warn";
}
function modelTone(s: TunedModel["status"]): "ok" | "warn" | "bad" | "neutral" {
  return s === "promoted" ? "ok" : s === "rejected" ? "bad" : s === "candidate" ? "warn" : "neutral";
}

export default function FinetunePage() {
  const sources = useAsync(() => api.listDataSources());
  const jobs = useAsync(() => api.listJobs());
  const models = useAsync(() => api.listModels());
  const routing = useAsync(() => api.routing());

  // Poll every 5 s while any job is queued or running.
  const hasActive = jobs.data?.some((j: FinetuneJob) => ACTIVE.includes(j.status)) ?? false;
  const reloadJobs = jobs.reload;
  const reloadModels = models.reload;
  useEffect(() => {
    if (!hasActive) return;
    const t = setInterval(() => {
      reloadJobs();
      reloadModels();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [hasActive, reloadJobs, reloadModels]);

  const [dataSource, setDataSource] = useState("");
  const [symbols, setSymbols] = useState("");
  const [epochs, setEpochs] = useState(3);
  const [horizon, setHorizon] = useState(5);
  const [localError, setLocalError] = useState<string | null>(null);
  const create = useAction(async () => {
    const list = symbols.split(/[\s,]+/).filter(Boolean).map((s) => s.toUpperCase());
    await api.createJob({ data_source_id: dataSource, symbols: list, epochs, horizon });
    jobs.reload();
  });
  const cancel = useAction(async (id: string) => {
    await api.cancelJob(id);
    jobs.reload();
  });
  const archive = useAction(async (id: string) => {
    await api.archiveModel(id);
    models.reload();
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    const list = symbols.split(/[\s,]+/).filter(Boolean);
    if (!dataSource) return setLocalError("Choose a data source.");
    if (list.length === 0 || list.some((s) => !SYMBOL_RE.test(s))) return setLocalError("Enter one or more valid symbols separated by commas.");
    if (epochs < 1 || epochs > 20) return setLocalError("Epochs must be between 1 and 20.");
    if (horizon < 1 || horizon > 30) return setLocalError("Horizon must be between 1 and 30.");
    setLocalError(null);
    void create.run();
  }

  // Routing editor
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<string | null>(null);
  const [routeError, setRouteError] = useState<string | null>(null);
  const saveRouting = useAction(async () => {
    const map: Record<string, string> = {};
    for (const line of (overrides ?? "").split("\n")) {
      const t = line.trim();
      if (!t) continue;
      const [sym, model] = t.split("=").map((x) => x.trim());
      if (!sym || !model) throw new Error(`Use SYMBOL=model on each line (got "${t}").`);
      map[sym.toUpperCase()] = model;
    }
    await api.setRouting({ default_model: (defaultModel ?? routing.data?.default_model ?? "").trim(), overrides: map });
    routing.reload();
    setDefaultModel(null);
    setOverrides(null);
  });

  const planError = jobs.error ?? models.error;

  return (
    <>
      <PageTitle>Fine-tuning</PageTitle>
      <p className="muted mb-3 max-w-2xl text-sm">
        Enterprise feature. Fine-tuned models are only promoted if they beat the baseline on held-out data; otherwise they are rejected and not used.
      </p>
      {planError ? <ErrorBox error={planError} /> : null}

      {!planError && (
        <>
          <section className="card mb-6 max-w-xl" aria-labelledby="new-job-h">
            <h2 id="new-job-h" className="h2">
              New fine-tuning job
            </h2>
            <form onSubmit={submit}>
              <Field label="Data source" id="j-source">
                <select id="j-source" required className="input" value={dataSource} onChange={(e) => setDataSource(e.target.value)}>
                  <option value="">Select a data source</option>
                  {sources.data?.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Symbols (comma separated)" id="j-symbols">
                <input id="j-symbols" required className="input" value={symbols} onChange={(e) => setSymbols(e.target.value)} />
              </Field>
              <div className="grid grid-cols-2 gap-x-4">
                <Field label="Epochs (1-20)" id="j-epochs">
                  <input id="j-epochs" type="number" min={1} max={20} className="input" value={epochs} onChange={(e) => setEpochs(Number(e.target.value))} />
                </Field>
                <Field label="Horizon (1-30)" id="j-horizon">
                  <input id="j-horizon" type="number" min={1} max={30} className="input" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
                </Field>
              </div>
              {localError && (
                <p role="alert" className="mb-2 text-sm" style={{ color: "var(--bad)" }}>
                  {localError}
                </p>
              )}
              <ErrorBox error={create.error} />
              <button type="submit" className="btn btn-primary" disabled={create.busy}>
                Start job
              </button>
            </form>
          </section>

          <section className="mb-6" aria-labelledby="jobs-h">
            <h2 id="jobs-h" className="h2">
              Jobs
            </h2>
            <p role="status" aria-live="polite" className="muted mb-2 text-xs">
              {hasActive ? "Refreshing every 5 seconds while jobs are queued or running." : "No active jobs."}
            </p>
            {jobs.loading && !jobs.data && <Loading />}
            <ErrorBox error={cancel.error} />
            {jobs.data?.length === 0 && <p className="muted text-sm">No jobs yet.</p>}
            {jobs.data && jobs.data.length > 0 && (
              <Table head={["Created", "Status", "Symbols", "GPU seconds", "Model", "Details", ""]} caption="Fine-tuning jobs">
                {jobs.data.map((j) => (
                  <tr key={j.id}>
                    <td className="td">{formatDate(j.created_at)}</td>
                    <td className="td">
                      <StatusBadge tone={jobTone(j.status)}>{j.status}</StatusBadge>
                    </td>
                    <td className="td">{j.symbols.join(", ")}</td>
                    <td className="td">{j.gpu_seconds ?? "n/a"}</td>
                    <td className="td font-mono text-xs">{j.model_id ?? "n/a"}</td>
                    <td className="td text-xs">{j.error ?? ""}</td>
                    <td className="td">
                      {ACTIVE.includes(j.status) && (
                        <button type="button" className="btn" disabled={cancel.busy} aria-label={`Cancel job ${j.id}`} onClick={() => void cancel.run(j.id)}>
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </Table>
            )}
          </section>

          <section className="mb-6" aria-labelledby="models-h">
            <h2 id="models-h" className="h2">
              Models
            </h2>
            {models.loading && !models.data && <Loading />}
            <ErrorBox error={archive.error} />
            {models.data?.length === 0 && <p className="muted text-sm">No fine-tuned models yet.</p>}
            <div className="space-y-3">
              {models.data?.map((m) => (
                <article key={m.id} className="card" aria-label={`Model ${m.name}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="font-semibold">
                      {m.name} <StatusBadge tone={modelTone(m.status)}>{m.status}</StatusBadge>
                    </h3>
                    {m.status !== "archived" && (
                      <button type="button" className="btn" disabled={archive.busy} aria-label={`Archive model ${m.name}`} onClick={() => void archive.run(m.id)}>
                        Archive
                      </button>
                    )}
                  </div>
                  <p className="muted text-xs">
                    id <span className="font-mono">ft:{m.id}</span>; base {m.base_model}; created {formatDate(m.created_at)}
                    {m.promoted_at ? `; promoted ${formatDate(m.promoted_at)}` : ""}
                  </p>
                  <ul className="mt-2 list-disc pl-5 text-sm">
                    {explainGate(m).map((l, i) => (
                      <li key={i}>{l}</li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          </section>

          <section className="card max-w-xl" aria-labelledby="routing-h">
            <h2 id="routing-h" className="h2">
              Routing
            </h2>
            {routing.loading && !routing.data && <Loading />}
            <ErrorBox error={routing.error} />
            {routing.data && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setRouteError(null);
                  void saveRouting.run().catch(() => undefined);
                }}
              >
                <Field label="Default model" id="rt-default">
                  <input id="rt-default" className="input" value={defaultModel ?? routing.data.default_model} onChange={(e) => setDefaultModel(e.target.value)} />
                </Field>
                <Field label="Per-symbol overrides" id="rt-over" hint="One per line: SYMBOL=model">
                  <textarea
                    id="rt-over"
                    rows={4}
                    aria-describedby="rt-over-hint"
                    className="input font-mono"
                    value={overrides ?? Object.entries(routing.data.overrides).map(([k, v]) => `${k}=${v}`).join("\n")}
                    onChange={(e) => setOverrides(e.target.value)}
                  />
                </Field>
                {routeError && (
                  <p role="alert" className="mb-2 text-sm" style={{ color: "var(--bad)" }}>
                    {routeError}
                  </p>
                )}
                <ErrorBox error={saveRouting.error} />
                <button type="submit" className="btn btn-primary" disabled={saveRouting.busy}>
                  Save routing
                </button>
              </form>
            )}
          </section>
        </>
      )}
    </>
  );
}

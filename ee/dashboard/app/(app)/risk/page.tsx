// Proprietary: see ee/LICENSE
"use client";
import { useState, type FormEvent } from "react";
import { GenericData } from "@/components/GenericData";
import { MODELS } from "@/components/model-bits";
import { ErrorBox, Field, PageTitle } from "@/components/ui";
import { api, type RiskOut } from "@/lib/api";
import { SYMBOL_RE } from "@/lib/format";
import { useAction } from "@/lib/hooks";

interface Row {
  id: number;
  symbol: string;
  value: string;
}

export default function RiskPage() {
  const [rows, setRows] = useState<Row[]>([
    { id: 1, symbol: "", value: "" },
    { id: 2, symbol: "", value: "" },
  ]);
  const [nextId, setNextId] = useState(3);
  const [horizon, setHorizon] = useState(5);
  const [model, setModel] = useState("routed");
  const [calibrate, setCalibrate] = useState(true);
  const [levels, setLevels] = useState<number[]>([0.95, 0.99]);
  const [coupling, setCoupling] = useState<"terminal" | "stepwise">("terminal");
  const [localError, setLocalError] = useState<string | null>(null);
  const [result, setResult] = useState<RiskOut | null>(null);
  const [showReport, setShowReport] = useState(false);

  const run = useAction(async (positions: Record<string, number>) => {
    setResult(null);
    setShowReport(false);
    setResult(await api.risk({ positions, horizon, model: model.trim(), calibrate, levels, coupling }));
  });

  function update(id: number, patch: Partial<Row>) {
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const positions: Record<string, number> = {};
    for (const r of rows) {
      if (!r.symbol.trim() && !r.value.trim()) continue;
      const sym = r.symbol.trim().toUpperCase();
      const val = Number(r.value);
      if (!SYMBOL_RE.test(sym)) return setLocalError(`"${r.symbol}" is not a valid symbol.`);
      if (!Number.isFinite(val) || val === 0) return setLocalError(`Enter a non-zero market value for ${sym}.`);
      if (sym in positions) return setLocalError(`${sym} appears twice.`);
      positions[sym] = val;
    }
    if (Object.keys(positions).length === 0) return setLocalError("Add at least one position.");
    if (levels.length === 0) return setLocalError("Select at least one confidence level.");
    setLocalError(null);
    void run.run(positions);
  }

  const reportId = typeof result?.report_id === "string" ? result.report_id : null;
  const { report_id: _omit, ...rest } = result ?? {};
  void _omit;

  return (
    <>
      <PageTitle>Risk</PageTitle>
      <form onSubmit={submit} className="card mb-4 max-w-3xl">
        <fieldset className="mb-3">
          <legend className="label">Positions (market value; negative for short)</legend>
          {rows.map((r, i) => (
            <div key={r.id} className="mb-2 flex items-end gap-2">
              <div className="flex-1">
                <label htmlFor={`sym-${r.id}`} className="sr-only">
                  Symbol {i + 1}
                </label>
                <input id={`sym-${r.id}`} placeholder="Symbol" className="input" value={r.symbol} onChange={(e) => update(r.id, { symbol: e.target.value })} />
              </div>
              <div className="flex-1">
                <label htmlFor={`val-${r.id}`} className="sr-only">
                  Market value {i + 1}
                </label>
                <input id={`val-${r.id}`} placeholder="Market value" inputMode="decimal" className="input" value={r.value} onChange={(e) => update(r.id, { value: e.target.value })} />
              </div>
              <button type="button" className="btn" aria-label={`Remove position ${i + 1}`} disabled={rows.length === 1} onClick={() => setRows((rs) => rs.filter((x) => x.id !== r.id))}>
                Remove
              </button>
            </div>
          ))}
          <button
            type="button"
            className="btn"
            onClick={() => {
              setRows((rs) => [...rs, { id: nextId, symbol: "", value: "" }]);
              setNextId((n) => n + 1);
            }}
          >
            Add position
          </button>
        </fieldset>
        <div className="grid gap-x-4 sm:grid-cols-2">
          <Field label="Horizon (steps, 1-30)" id="r-horizon">
            <input id="r-horizon" type="number" min={1} max={30} className="input" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
          </Field>
          <Field label="Model" id="r-model">
            <input id="r-model" list="r-model-list" className="input" value={model} onChange={(e) => setModel(e.target.value)} />
            <datalist id="r-model-list">
              {MODELS.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </Field>
          <Field label="Coupling" id="r-coupling">
            <select id="r-coupling" className="input" value={coupling} onChange={(e) => setCoupling(e.target.value as "terminal" | "stepwise")}>
              <option value="terminal">terminal</option>
              <option value="stepwise">stepwise</option>
            </select>
          </Field>
          <fieldset className="mb-3">
            <legend className="label">Confidence levels</legend>
            {[0.95, 0.99].map((l) => (
              <label key={l} className="mr-4 inline-flex items-center gap-1 text-sm">
                <input type="checkbox" checked={levels.includes(l)} onChange={() => setLevels((cur) => (cur.includes(l) ? cur.filter((x) => x !== l) : [...cur, l].sort()))} />
                {l * 100}%
              </label>
            ))}
          </fieldset>
        </div>
        <label className="mb-3 flex items-center gap-2 text-sm">
          <input type="checkbox" checked={calibrate} onChange={(e) => setCalibrate(e.target.checked)} /> Calibrate
        </label>
        {localError && (
          <p role="alert" className="mb-2 text-sm" style={{ color: "var(--bad)" }}>
            {localError}
          </p>
        )}
        <ErrorBox error={run.error} />
        <button type="submit" className="btn btn-primary" disabled={run.busy}>
          {run.busy ? "Computing..." : "Compute VaR / ES"}
        </button>
      </form>

      <div aria-live="polite">
        {run.busy && <p className="muted text-sm">Computing risk...</p>}
        {result && (
          <section className="card" aria-labelledby="risk-h">
            <h2 id="risk-h" className="h2">
              Result
            </h2>
            <GenericData value={rest} />
            {reportId && (
              <div className="mt-4">
                <button type="button" className="btn" aria-expanded={showReport} onClick={() => setShowReport((s) => !s)}>
                  {showReport ? "Hide report" : "View full report"}
                </button>{" "}
                <a href={`/api/report/${encodeURIComponent(reportId)}`} target="_blank" rel="noopener noreferrer" className="text-sm">
                  Open in new tab
                </a>
                {showReport && (
                  <iframe
                    title="Risk report"
                    src={`/api/report/${encodeURIComponent(reportId)}`}
                    sandbox=""
                    referrerPolicy="no-referrer"
                    className="mt-3 h-[70vh] w-full rounded-md border"
                    style={{ borderColor: "var(--border)", background: "#fff" }}
                  />
                )}
              </div>
            )}
          </section>
        )}
      </div>
    </>
  );
}

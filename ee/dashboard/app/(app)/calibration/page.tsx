// Proprietary: see ee/LICENSE
"use client";
import { useState, type FormEvent } from "react";
import { ReliabilityChart } from "@/components/charts";
import { GenericData } from "@/components/GenericData";
import { MODELS } from "@/components/model-bits";
import { ErrorBox, Field, PageTitle, Table } from "@/components/ui";
import { api, type CalibrationOut } from "@/lib/api";
import { formatNumber, formatPct, SYMBOL_RE } from "@/lib/format";
import { useAction } from "@/lib/hooks";

export default function CalibrationPage() {
  const [symbol, setSymbol] = useState("");
  const [horizon, setHorizon] = useState(5);
  const [model, setModel] = useState("routed");
  const [localError, setLocalError] = useState<string | null>(null);
  const [result, setResult] = useState<CalibrationOut | null>(null);

  const run = useAction(async () => {
    setResult(null);
    setResult(await api.calibrate({ symbol: symbol.trim().toUpperCase(), horizon, model: model.trim() }));
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!SYMBOL_RE.test(symbol.trim())) return setLocalError("Enter a valid symbol.");
    if (!Number.isInteger(horizon) || horizon < 1 || horizon > 30) return setLocalError("Horizon must be between 1 and 30.");
    setLocalError(null);
    void run.run();
  }

  return (
    <>
      <PageTitle>Calibration</PageTitle>
      <p className="muted mb-3 max-w-2xl text-sm">
        Checks whether forecast intervals contain the outcome as often as they claim. Available on Startup plans and above.
      </p>
      <form onSubmit={submit} className="card mb-4 grid max-w-2xl gap-x-4 sm:grid-cols-3">
        <Field label="Symbol" id="c-symbol">
          <input id="c-symbol" required className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
        </Field>
        <Field label="Horizon (1-30)" id="c-horizon">
          <input id="c-horizon" type="number" min={1} max={30} className="input" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
        </Field>
        <Field label="Model" id="c-model">
          <input id="c-model" list="c-model-list" className="input" value={model} onChange={(e) => setModel(e.target.value)} />
          <datalist id="c-model-list">
            {MODELS.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </Field>
        <div className="sm:col-span-3">
          {localError && (
            <p role="alert" className="mb-2 text-sm" style={{ color: "var(--bad)" }}>
              {localError}
            </p>
          )}
          <ErrorBox error={run.error} />
          <button type="submit" className="btn btn-primary" disabled={run.busy}>
            {run.busy ? "Calibrating..." : "Run calibration report"}
          </button>
        </div>
      </form>

      <div aria-live="polite">
        {run.busy && <p className="muted text-sm">Running calibration report...</p>}
        {result && (
          <section className="space-y-4" aria-labelledby="cal-h">
            <h2 id="cal-h" className="h2">
              {result.symbol}: {result.method} calibration ({result.status})
            </h2>
            <p className="text-sm">
              as of {result.as_of}; model {result.model_id}; {result.n_scores} calibration scores, {result.holdout_n} holdout points; tolerance{" "}
              {formatNumber(result.tolerance)}.
            </p>
            <div className="grid gap-4 lg:grid-cols-[1fr_auto]">
              <div className="card">
                <Table head={["Nominal", "Raw coverage", "Calibrated coverage", "Raw width", "Calibrated width"]} caption="Coverage: nominal versus raw versus calibrated">
                  {result.coverage.map((c) => (
                    <tr key={c.nominal}>
                      <td className="td">{formatPct(c.nominal, 0)}</td>
                      <td className="td">{formatPct(c.raw_coverage)}</td>
                      <td className="td">{formatPct(c.calibrated_coverage)}</td>
                      <td className="td">{formatNumber(c.raw_width)}</td>
                      <td className="td">{formatNumber(c.calibrated_width)}</td>
                    </tr>
                  ))}
                </Table>
                <p className="mt-2 text-sm">
                  CRPS raw {formatNumber(result.crps_raw)} vs calibrated {formatNumber(result.crps_calibrated)} (lower is better).
                </p>
              </div>
              <div className="card">
                <ReliabilityChart rows={result.coverage} />
              </div>
            </div>
            {result.notes && result.notes.length > 0 && (
              <div className="card">
                <h3 className="h2">Notes</h3>
                <ul className="list-disc pl-5 text-sm">
                  {result.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              </div>
            )}
            <details className="card">
              <summary className="cursor-pointer font-medium">Model card</summary>
              <div className="mt-2">
                <GenericData value={result.model_card} />
              </div>
            </details>
          </section>
        )}
      </div>
    </>
  );
}

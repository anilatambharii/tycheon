// Proprietary: see ee/LICENSE
"use client";
import { useState, type FormEvent } from "react";
import { FanChart } from "@/components/charts";
import { GenericData } from "@/components/GenericData";
import { CalibrationBadge, MODELS } from "@/components/model-bits";
import { ErrorBox, Field, PageTitle } from "@/components/ui";
import { api, type ForecastOut } from "@/lib/api";
import { formatNumber, formatPct, SYMBOL_RE } from "@/lib/format";
import { useAction } from "@/lib/hooks";

export default function ForecastPage() {
  const [symbol, setSymbol] = useState("");
  const [horizon, setHorizon] = useState(5);
  const [model, setModel] = useState("routed");
  const [calibrate, setCalibrate] = useState(true);
  const [localError, setLocalError] = useState<string | null>(null);
  const [result, setResult] = useState<ForecastOut | null>(null);

  const run = useAction(async () => {
    setResult(null);
    setResult(await api.forecast({ symbol: symbol.trim().toUpperCase(), horizon, model: model.trim(), calibrate }));
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!SYMBOL_RE.test(symbol.trim())) return setLocalError("Enter a valid symbol.");
    if (!Number.isInteger(horizon) || horizon < 1 || horizon > 30) return setLocalError("Horizon must be between 1 and 30.");
    setLocalError(null);
    void run.run();
  }

  const he = result?.horizon_end;
  return (
    <>
      <PageTitle>Forecast</PageTitle>
      <form onSubmit={submit} className="card mb-4 grid max-w-2xl gap-x-4 sm:grid-cols-2">
        <Field label="Symbol" id="f-symbol">
          <input id="f-symbol" required className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
        </Field>
        <Field label="Horizon (steps, 1-30)" id="f-horizon">
          <input id="f-horizon" type="number" min={1} max={30} required className="input" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
        </Field>
        <Field label="Model" id="f-model" hint="Or enter ft:<model-id> for a fine-tuned model.">
          <input id="f-model" list="model-list" required className="input" aria-describedby="f-model-hint" value={model} onChange={(e) => setModel(e.target.value)} />
          <datalist id="model-list">
            {MODELS.map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
        </Field>
        <label className="flex items-center gap-2 self-center text-sm">
          <input type="checkbox" checked={calibrate} onChange={(e) => setCalibrate(e.target.checked)} /> Calibrate intervals
        </label>
        <div className="sm:col-span-2">
          {localError && (
            <p role="alert" className="mb-2 text-sm" style={{ color: "var(--bad)" }}>
              {localError}
            </p>
          )}
          <ErrorBox error={run.error} />
          <button type="submit" className="btn btn-primary" disabled={run.busy}>
            {run.busy ? "Forecasting..." : "Run forecast"}
          </button>
        </div>
      </form>

      <div aria-live="polite">
        {run.busy && <p className="muted text-sm">Running forecast...</p>}
        {result && he && (
          <section aria-labelledby="result-h" className="space-y-4">
            <h2 id="result-h" className="h2">
              {result.symbol}: {result.horizon}-step forecast
            </h2>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <CalibrationBadge status={result.calibration_status} />
              <span>
                as of <strong>{result.as_of}</strong>
              </span>
              <span>
                model <strong>{result.model_id}</strong>
              </span>
            </div>
            {result.calibration_status === "uncalibrated" && (
              <div role="alert" className="card text-sm font-medium" style={{ borderColor: "var(--bad)", color: "var(--bad)" }}>
                These intervals are NOT calibrated. Their stated 50% and 90% coverage has not been verified, so treat the bands as
                indicative only.
              </div>
            )}
            {result.calibration_status === "stale" && (
              <div role="status" className="card text-sm" style={{ borderColor: "var(--warn)", color: "var(--warn)" }}>
                The calibration is stale (it was fitted on older data). Re-run calibration before relying on the intervals.
              </div>
            )}
            <div className="card">
              <FanChart forecast={result} />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="card">
                <h3 className="h2">Horizon end</h3>
                <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
                  <dt className="muted">Last close</dt>
                  <dd>{formatNumber(result.last_close)}</dd>
                  <dt className="muted">Median</dt>
                  <dd>
                    {formatNumber(he.median)}
                    {he.median_return !== undefined && ` (${formatPct(he.median_return, 2)})`}
                  </dd>
                  <dt className="muted">50% interval</dt>
                  <dd>
                    {formatNumber(he.lower_50)} to {formatNumber(he.upper_50)}
                  </dd>
                  <dt className="muted">90% interval</dt>
                  <dd>
                    {formatNumber(he.lower_90)} to {formatNumber(he.upper_90)}
                  </dd>
                </dl>
              </div>
              <div className="card">
                <h3 className="h2">Model mix</h3>
                <ul className="text-sm">
                  {Object.entries(result.model_mix ?? {}).map(([k, v]) => (
                    <li key={k}>
                      {k}: {typeof v === "number" ? formatPct(v, 0) : String(v)}
                    </li>
                  ))}
                </ul>
                <h3 className="mt-3 text-sm font-semibold">Model card</h3>
                <div className="text-sm">
                  <GenericData value={result.model_card} />
                </div>
              </div>
            </div>
            {result.calibration && (
              <details className="card">
                <summary className="cursor-pointer font-medium">Calibration details</summary>
                <div className="mt-2">
                  <GenericData value={result.calibration} />
                </div>
              </details>
            )}
          </section>
        )}
      </div>
    </>
  );
}

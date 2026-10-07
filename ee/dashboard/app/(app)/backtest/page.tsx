// Proprietary: see ee/LICENSE
"use client";
import { useState, type FormEvent } from "react";
import { GenericData, Scalar } from "@/components/GenericData";
import { ErrorBox, Field, PageTitle, StatusBadge } from "@/components/ui";
import { api, type BacktestOut, type Dict } from "@/lib/api";
import { baselineVerdict, BASELINE, orderRows } from "@/lib/backtest";
import { SYMBOL_RE } from "@/lib/format";
import { useAction } from "@/lib/hooks";

const CANDIDATES = ["random-walk", "drift", "seasonal-naive", "garch"];
const LEAD_COLS = ["model_id", "n_origins", "crps", "mase_vs_rw", "dm_stat", "dm_pvalue"];

function isScalar(v: unknown) {
  return v === null || typeof v !== "object";
}

export default function BacktestPage() {
  const [symbol, setSymbol] = useState("");
  const [horizon, setHorizon] = useState(5);
  const [models, setModels] = useState<string[]>(["random-walk", "drift", "garch"]);
  const [folds, setFolds] = useState(3);
  const [testWindow, setTestWindow] = useState(60);
  const [localError, setLocalError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestOut | null>(null);

  const run = useAction(async () => {
    setResult(null);
    // The baseline is always evaluated, whatever the user ticks.
    const ms = models.includes(BASELINE) ? models : [BASELINE, ...models];
    setResult(await api.backtest({ symbol: symbol.trim().toUpperCase(), horizon, models: ms, folds, test_window: testWindow }));
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!SYMBOL_RE.test(symbol.trim())) return setLocalError("Enter a valid symbol.");
    if (horizon < 1 || horizon > 20) return setLocalError("Horizon must be between 1 and 20.");
    if (folds < 1 || folds > 4) return setLocalError("Folds must be between 1 and 4.");
    if (testWindow < 20 || testWindow > 120) return setLocalError("Test window must be between 20 and 120.");
    setLocalError(null);
    void run.run();
  }

  const rows: Dict[] = Array.isArray(result?.rows) ? orderRows(result.rows as Dict[]) : [];
  const verdict = baselineVerdict(rows);
  const extraCols = Array.from(new Set(rows.flatMap((r) => Object.keys(r).filter((k) => !LEAD_COLS.includes(k) && isScalar(r[k])))));
  const cols = [...LEAD_COLS.filter((c) => rows.some((r) => c in r)), ...extraCols];
  const { rows: _rows, ...meta } = result ?? {};
  void _rows;

  return (
    <>
      <PageTitle>Backtest</PageTitle>
      <form onSubmit={submit} className="card mb-4 max-w-2xl">
        <div className="grid gap-x-4 sm:grid-cols-2">
          <Field label="Symbol" id="b-symbol">
            <input id="b-symbol" required className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          </Field>
          <Field label="Horizon (1-20)" id="b-horizon">
            <input id="b-horizon" type="number" min={1} max={20} className="input" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
          </Field>
          <Field label="Folds (1-4)" id="b-folds">
            <input id="b-folds" type="number" min={1} max={4} className="input" value={folds} onChange={(e) => setFolds(Number(e.target.value))} />
          </Field>
          <Field label="Test window (20-120)" id="b-window">
            <input id="b-window" type="number" min={20} max={120} className="input" value={testWindow} onChange={(e) => setTestWindow(Number(e.target.value))} />
          </Field>
        </div>
        <fieldset className="mb-3">
          <legend className="label">Models</legend>
          {CANDIDATES.map((m) => (
            <label key={m} className="mr-4 inline-flex items-center gap-1 text-sm">
              <input
                type="checkbox"
                checked={m === BASELINE || models.includes(m)}
                disabled={m === BASELINE}
                onChange={() => setModels((cur) => (cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m]))}
              />{" "}
              {m}
            </label>
          ))}
          <p className="muted mt-1 text-xs">The random-walk baseline is always included.</p>
        </fieldset>
        {localError && (
          <p role="alert" className="mb-2 text-sm" style={{ color: "var(--bad)" }}>
            {localError}
          </p>
        )}
        <ErrorBox error={run.error} />
        <button type="submit" className="btn btn-primary" disabled={run.busy}>
          {run.busy ? "Running..." : "Run backtest"}
        </button>
      </form>

      <div aria-live="polite">
        {run.busy && <p className="muted text-sm">Running walk-forward backtest. This can take a while.</p>}
        {result && (
          <section className="space-y-4" aria-labelledby="bt-h">
            <h2 id="bt-h" className="h2">
              Results
            </h2>
            <div
              role="status"
              className="card text-sm font-medium"
              style={{ borderColor: verdict.baselineWins ? "var(--warn)" : "var(--border)", color: verdict.baselineWins ? "var(--warn)" : "var(--text)" }}
            >
              {verdict.message}
            </div>
            {rows.length > 0 && (
              <div className="card overflow-x-auto">
                <table className="w-full border-collapse">
                  <caption className="sr-only">Backtest results, random-walk baseline first</caption>
                  <thead>
                    <tr>
                      {cols.map((c) => (
                        <th key={c} scope="col" className="th">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} style={r.model_id === BASELINE ? { background: "var(--bg)" } : undefined}>
                        {cols.map((c) => (
                          <td key={c} className="td">
                            {c === "model_id" && r.model_id === BASELINE ? (
                              <>
                                {String(r.model_id)} <StatusBadge tone="neutral">baseline</StatusBadge>
                              </>
                            ) : c === "dm_pvalue" && typeof r[c] === "number" ? (
                              <>
                                <Scalar v={r[c]} />
                                {(r[c] as number) < 0.05 ? " *" : ""}
                              </>
                            ) : (
                              <Scalar v={r[c]} />
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="muted mt-2 text-xs">
                  CRPS: lower is better. MASE vs RW below 1 means better than the random walk. dm_pvalue: Diebold-Mariano test against the
                  baseline; * means p &lt; 0.05.
                </p>
              </div>
            )}
            {Object.keys(meta).length > 0 && (
              <details className="card">
                <summary className="cursor-pointer font-medium">Other details</summary>
                <div className="mt-2">
                  <GenericData value={meta} />
                </div>
              </details>
            )}
          </section>
        )}
      </div>
    </>
  );
}

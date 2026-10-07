// Proprietary: see ee/LICENSE
"use client";
import { useState, type FormEvent } from "react";
import { ErrorBox, Field, Loading, PageTitle, Table } from "@/components/ui";
import { api, type DataSource } from "@/lib/api";
import { MAX_BODY_CSV } from "@/lib/proxy-rules";
import { formatDate, SYMBOL_RE } from "@/lib/format";
import { useAction, useAsync } from "@/lib/hooks";

function UploadForm({ source, onDone }: { source: DataSource; onDone: () => void }) {
  const [symbol, setSymbol] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const up = useAction(async () => {
    const res = await api.uploadCsv(source.id, symbol.trim().toUpperCase(), file as File);
    setOkMsg(`Uploaded ${res.symbol}: ${res.n_rows} rows.`);
    setFile(null);
    onDone();
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    setOkMsg(null);
    const sym = symbol.trim().toUpperCase();
    if (!SYMBOL_RE.test(sym)) return setLocalError("Enter a symbol (letters, digits, . ^ = _ -).");
    if (!file) return setLocalError("Choose a CSV file.");
    if (file.size > MAX_BODY_CSV) return setLocalError(`File is ${(file.size / 1048576).toFixed(1)} MB; the limit is 25 MB.`);
    setLocalError(null);
    void up.run();
  }
  return (
    <form onSubmit={submit} className="mt-3 grid gap-2 sm:grid-cols-[8rem_1fr_auto] sm:items-end" aria-label={`Upload CSV to ${source.name}`}>
      <div>
        <label htmlFor={`sym-${source.id}`} className="label">
          Symbol
        </label>
        <input id={`sym-${source.id}`} className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
      </div>
      <div>
        <label htmlFor={`file-${source.id}`} className="label">
          CSV file (max 25 MB)
        </label>
        <input id={`file-${source.id}`} type="file" accept=".csv,text/csv" className="input" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </div>
      <button type="submit" className="btn" disabled={up.busy}>
        {up.busy ? "Uploading..." : "Upload"}
      </button>
      <div className="sm:col-span-3" aria-live="polite">
        {localError && (
          <p role="alert" className="text-sm" style={{ color: "var(--bad)" }}>
            {localError}
          </p>
        )}
        {okMsg && <p className="text-sm" style={{ color: "var(--ok)" }}>{okMsg}</p>}
        <ErrorBox error={up.error} />
      </div>
    </form>
  );
}

export default function DataPage() {
  const sources = useAsync(() => api.listDataSources());
  const creds = useAsync(() => api.listCredentials());

  const [dsName, setDsName] = useState("");
  const [dsKind, setDsKind] = useState<"csv" | "provider">("csv");
  const [dsCred, setDsCred] = useState("");
  const [dsDefault, setDsDefault] = useState(false);
  const createSource = useAction(async () => {
    await api.createDataSource({
      name: dsName.trim(),
      kind: dsKind,
      ...(dsKind === "provider" && dsCred ? { credential_id: dsCred } : {}),
      ...(dsDefault ? { default: true } : {}),
    });
    setDsName("");
    sources.reload();
  });
  const delSource = useAction(async (id: string) => {
    await api.deleteDataSource(id);
    sources.reload();
  });

  const [cName, setCName] = useState("");
  const [cProvider, setCProvider] = useState("");
  const [cSecret, setCSecret] = useState("");
  const createCred = useAction(async () => {
    await api.createCredential(cName.trim(), cProvider.trim(), cSecret);
    setCName("");
    setCProvider("");
    setCSecret(""); // the secret is never kept after submission
    creds.reload();
  });
  const delCred = useAction(async (id: string) => {
    await api.deleteCredential(id);
    creds.reload();
  });

  return (
    <>
      <PageTitle>Data</PageTitle>

      <section className="mb-8" aria-labelledby="sources-h">
        <h2 id="sources-h" className="h2">
          Data sources
        </h2>
        <p className="muted mb-3 text-sm">You bring your own data. Upload CSVs (one per symbol) or connect a provider with your own credential.</p>
        {sources.loading && <Loading />}
        <ErrorBox error={sources.error} />
        <ErrorBox error={delSource.error} />
        {sources.data?.length === 0 && <p className="muted text-sm">No data sources yet.</p>}
        <div className="space-y-4">
          {sources.data?.map((s) => (
            <article key={s.id} className="card" aria-label={`Data source ${s.name}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-semibold">
                  {s.name} <span className="badge muted">{s.kind}</span> {s.is_default && <span className="badge">default</span>}
                </h3>
                <button
                  type="button"
                  className="btn btn-danger"
                  disabled={delSource.busy}
                  aria-label={`Delete data source ${s.name}`}
                  onClick={() => {
                    if (window.confirm(`Delete data source "${s.name}" and its files?`)) void delSource.run(s.id);
                  }}
                >
                  Delete
                </button>
              </div>
              {s.files.length > 0 && (
                <Table head={["Symbol", "Rows", "First", "Last"]} caption={`Files in ${s.name}`}>
                  {s.files.map((f) => (
                    <tr key={f.symbol}>
                      <td className="td font-mono">{f.symbol}</td>
                      <td className="td">{f.n_rows.toLocaleString()}</td>
                      <td className="td">{formatDate(f.first_ts)}</td>
                      <td className="td">{formatDate(f.last_ts)}</td>
                    </tr>
                  ))}
                </Table>
              )}
              {s.kind === "csv" && <UploadForm source={s} onDone={sources.reload} />}
            </article>
          ))}
        </div>

        <form
          className="card mt-4 max-w-md"
          aria-labelledby="new-source-h"
          onSubmit={(e) => {
            e.preventDefault();
            void createSource.run();
          }}
        >
          <h3 id="new-source-h" className="h2">
            New data source
          </h3>
          <Field label="Name" id="ds-name">
            <input id="ds-name" required className="input" value={dsName} onChange={(e) => setDsName(e.target.value)} />
          </Field>
          <Field label="Kind" id="ds-kind">
            <select id="ds-kind" className="input" value={dsKind} onChange={(e) => setDsKind(e.target.value as "csv" | "provider")}>
              <option value="csv">CSV upload</option>
              <option value="provider">Provider (uses a credential)</option>
            </select>
          </Field>
          {dsKind === "provider" && (
            <Field label="Credential" id="ds-cred">
              <select id="ds-cred" required className="input" value={dsCred} onChange={(e) => setDsCred(e.target.value)}>
                <option value="">Select a credential</option>
                {creds.data?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.provider})
                  </option>
                ))}
              </select>
            </Field>
          )}
          <label className="mb-3 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={dsDefault} onChange={(e) => setDsDefault(e.target.checked)} /> Use as default source
          </label>
          <ErrorBox error={createSource.error} />
          <button type="submit" className="btn btn-primary" disabled={createSource.busy}>
            Create data source
          </button>
        </form>
      </section>

      <section aria-labelledby="creds-h">
        <h2 id="creds-h" className="h2">
          Provider credentials
        </h2>
        <p className="muted mb-3 text-sm">Secrets are stored encrypted by the control plane and are never shown again.</p>
        {creds.loading && <Loading />}
        <ErrorBox error={creds.error} />
        <ErrorBox error={delCred.error} />
        {creds.data && creds.data.length > 0 && (
          <Table head={["Name", "Provider", "Created", ""]} caption="Credentials">
            {creds.data.map((c) => (
              <tr key={c.id}>
                <td className="td">{c.name}</td>
                <td className="td">{c.provider}</td>
                <td className="td">{formatDate(c.created_at)}</td>
                <td className="td">
                  <button
                    type="button"
                    className="btn btn-danger"
                    disabled={delCred.busy}
                    aria-label={`Delete credential ${c.name}`}
                    onClick={() => {
                      if (window.confirm(`Delete credential "${c.name}"?`)) void delCred.run(c.id);
                    }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </Table>
        )}
        <form
          className="card mt-4 max-w-md"
          aria-labelledby="new-cred-h"
          onSubmit={(e) => {
            e.preventDefault();
            void createCred.run();
          }}
        >
          <h3 id="new-cred-h" className="h2">
            Add credential
          </h3>
          <Field label="Name" id="c-name">
            <input id="c-name" required className="input" value={cName} onChange={(e) => setCName(e.target.value)} />
          </Field>
          <Field label="Provider" id="c-provider">
            <input id="c-provider" required className="input" value={cProvider} onChange={(e) => setCProvider(e.target.value)} />
          </Field>
          <Field label="Secret" id="c-secret">
            <input id="c-secret" type="password" required autoComplete="off" className="input" value={cSecret} onChange={(e) => setCSecret(e.target.value)} />
          </Field>
          <ErrorBox error={createCred.error} />
          <button type="submit" className="btn btn-primary" disabled={createCred.busy}>
            Save credential
          </button>
        </form>
      </section>
    </>
  );
}

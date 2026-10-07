// Proprietary: see ee/LICENSE
"use client";
import { useState, type FormEvent } from "react";
import OneTimeSecret from "@/components/OneTimeSecret";
import { ErrorBox, Field, Loading, PageTitle, StatusBadge, Table } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useAction, useAsync } from "@/lib/hooks";

export default function KeysPage() {
  const keys = useAsync(() => api.listKeys());
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["analytics"]);
  const [secret, setSecret] = useState<string | null>(null);

  const create = useAction(async () => {
    const created = await api.createKey(name.trim(), scopes);
    setSecret(created.key);
    setName("");
    keys.reload();
  });
  const revoke = useAction(async (id: string) => {
    await api.revokeKey(id);
    keys.reload();
  });

  function toggle(s: string) {
    setScopes((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s]));
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    if (scopes.length === 0) return;
    void create.run();
  }

  return (
    <>
      <PageTitle>API keys</PageTitle>
      {secret && <OneTimeSecret label="API key" secret={secret} onDismiss={() => setSecret(null)} />}

      <section className="card mb-6" aria-labelledby="new-key">
        <h2 id="new-key" className="h2">
          Create a key
        </h2>
        <form onSubmit={submit} className="max-w-md">
          <Field label="Name" id="key-name">
            <input id="key-name" required maxLength={80} className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <fieldset className="mb-3">
            <legend className="label">Scopes</legend>
            {["analytics", "mcp"].map((s) => (
              <label key={s} className="mr-4 inline-flex items-center gap-1 text-sm">
                <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} /> {s}
              </label>
            ))}
            {scopes.length === 0 && <p className="text-xs" style={{ color: "var(--bad)" }}>Select at least one scope.</p>}
          </fieldset>
          <ErrorBox error={create.error} />
          <button type="submit" className="btn btn-primary" disabled={create.busy || scopes.length === 0}>
            {create.busy ? "Creating..." : "Create key"}
          </button>
        </form>
      </section>

      <section aria-labelledby="existing-keys">
        <h2 id="existing-keys" className="h2">
          Existing keys
        </h2>
        {keys.loading && <Loading />}
        <ErrorBox error={keys.error} />
        <ErrorBox error={revoke.error} />
        {keys.data && keys.data.length === 0 && <p className="muted text-sm">No API keys yet.</p>}
        {keys.data && keys.data.length > 0 && (
          <Table head={["Name", "Prefix", "Scopes", "Created", "Last used", "Status", ""]} caption="API keys">
            {keys.data.map((k) => (
              <tr key={k.id}>
                <td className="td">{k.name}</td>
                <td className="td font-mono">{k.prefix}...</td>
                <td className="td">{k.scopes.join(", ")}</td>
                <td className="td">{formatDate(k.created_at)}</td>
                <td className="td">{formatDate(k.last_used_at)}</td>
                <td className="td">{k.revoked_at ? <StatusBadge tone="bad">revoked</StatusBadge> : <StatusBadge tone="ok">active</StatusBadge>}</td>
                <td className="td">
                  {!k.revoked_at && (
                    <button
                      type="button"
                      className="btn btn-danger"
                      disabled={revoke.busy}
                      aria-label={`Revoke key ${k.name}`}
                      onClick={() => {
                        if (window.confirm(`Revoke "${k.name}"? Anything using it will stop working.`)) void revoke.run(k.id);
                      }}
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </section>
    </>
  );
}

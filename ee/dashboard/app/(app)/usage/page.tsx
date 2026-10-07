// Proprietary: see ee/LICENSE
"use client";
import { MeterList } from "@/components/Overview";
import { ErrorBox, Loading, PageTitle, StatusBadge, Table } from "@/components/ui";
import { api } from "@/lib/api";
import { ApiError } from "@/lib/errors";
import { formatCents, formatDate, safeExternalUrl } from "@/lib/format";
import { useAction, useAsync } from "@/lib/hooks";

export default function UsagePage() {
  const usage = useAsync(() => api.usage());
  const me = useAsync(() => api.me());
  const plans = useAsync(() => api.plans());
  const status = useAsync(() => api.billingStatus());
  const preview = useAsync(() => api.invoicePreview());
  const invoices = useAsync(() => api.oneOffInvoices());

  const redirect = useAction(async (kind: "checkout" | "portal") => {
    const res = kind === "checkout" ? await api.checkout("startup") : await api.portal();
    const url = safeExternalUrl(res.url);
    if (!url) throw new ApiError(502, "bad_response", "The billing provider returned an invalid link.");
    window.location.assign(url);
  });

  const current = plans.data?.find((p) => p.key === me.data?.plan.key);
  const noPreview = preview.error instanceof ApiError && preview.error.status === 404;

  return (
    <>
      <PageTitle>Usage and billing</PageTitle>

      <section className="card mb-4" aria-labelledby="meters-h">
        <h2 id="meters-h" className="h2">
          Usage this period
        </h2>
        {usage.loading && <Loading />}
        <ErrorBox error={usage.error} />
        {usage.data && <MeterList meters={usage.data.meters} />}
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card" aria-labelledby="plan-h">
          <h2 id="plan-h" className="h2">
            Your plan
          </h2>
          {(me.loading || plans.loading) && <Loading />}
          <ErrorBox error={me.error} />
          {me.data && (
            <p className="text-sm">
              <strong>{me.data.plan.name}</strong> <StatusBadge tone="neutral">{me.data.plan.status}</StatusBadge>
            </p>
          )}
          {current && (
            <>
              <p className="muted mt-1 text-sm">{current.description}</p>
              <h3 className="mt-3 text-sm font-semibold">Limits per period</h3>
              <ul className="list-disc pl-5 text-sm">
                {Object.entries(current.limits).map(([k, v]) => (
                  <li key={k}>
                    {k.replace(/_/g, " ")}: {v.toLocaleString()}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>

        <section className="card" aria-labelledby="billing-h">
          <h2 id="billing-h" className="h2">
            Billing
          </h2>
          {status.loading && <Loading />}
          <ErrorBox error={status.error} />
          {status.data && (
            <dl className="mb-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="muted">Status</dt>
              <dd>{status.data.status}</dd>
              <dt className="muted">Trial ends</dt>
              <dd>{formatDate(status.data.trial_end)}</dd>
              <dt className="muted">Current period ends</dt>
              <dd>{formatDate(status.data.current_period_end)}</dd>
            </dl>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn btn-primary" disabled={redirect.busy} onClick={() => void redirect.run("checkout")}>
              Upgrade to Startup
            </button>
            <button
              type="button"
              className="btn"
              disabled={redirect.busy || status.data?.has_customer === false}
              onClick={() => void redirect.run("portal")}
            >
              Manage billing
            </button>
          </div>
          {status.data?.has_customer === false && <p className="muted mt-1 text-xs">The billing portal is available after your first checkout.</p>}
          <ErrorBox error={redirect.error} />
        </section>
      </div>

      <section className="card mt-4" aria-labelledby="preview-h">
        <h2 id="preview-h" className="h2">
          Upcoming invoice
        </h2>
        {preview.loading && <Loading />}
        {noPreview && <p className="muted text-sm">No upcoming invoice.</p>}
        {!noPreview && <ErrorBox error={preview.error} />}
        {preview.data && (
          <>
            <Table head={["Description", "Amount"]} caption="Upcoming invoice lines">
              {preview.data.lines.map((l, i) => (
                <tr key={i}>
                  <td className="td">{l.description}</td>
                  <td className="td">{formatCents(l.amount_cents, preview.data?.currency)}</td>
                </tr>
              ))}
            </Table>
            <p className="mt-2 text-sm font-semibold">
              Total: {formatCents(preview.data.total_cents, preview.data.currency)} (period ends {formatDate(preview.data.period_end)})
            </p>
          </>
        )}
      </section>

      <section className="card mt-4" aria-labelledby="oneoff-h">
        <h2 id="oneoff-h" className="h2">
          One-off invoices
        </h2>
        {invoices.loading && <Loading />}
        <ErrorBox error={invoices.error} />
        {invoices.data?.length === 0 && <p className="muted text-sm">No one-off invoices.</p>}
        {invoices.data && invoices.data.length > 0 && (
          <Table head={["Date", "Description", "Amount", "Status"]} caption="One-off invoices">
            {invoices.data.map((i) => (
              <tr key={i.id}>
                <td className="td">{formatDate(i.created_at)}</td>
                <td className="td">{i.description}</td>
                <td className="td">{formatCents(i.amount_cents, i.currency)}</td>
                <td className="td">{i.status}</td>
              </tr>
            ))}
          </Table>
        )}
      </section>

      {plans.data && (
        <section className="mt-4" aria-labelledby="plans-h">
          <h2 id="plans-h" className="h2">
            Available plans
          </h2>
          <div className="grid gap-3 md:grid-cols-3">
            {plans.data.map((p) => (
              <article key={p.key} className="card" aria-label={`${p.name} plan`}>
                <h3 className="font-semibold">{p.name}</h3>
                <p className="text-sm">{p.price ? `${formatCents(p.price.amount_cents, p.price.currency)} / ${p.price.interval}` : "Contact us"}</p>
                <p className="muted text-sm">{p.description}</p>
                {p.research_use_only && <p className="mt-1 text-xs">Research use only.</p>}
              </article>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

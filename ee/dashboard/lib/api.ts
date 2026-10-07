// Proprietary: see ee/LICENSE
// Typed browser-side client. Everything goes through the BFF proxy at /api/cp/...;
// the session token lives in an httpOnly cookie and is never visible here.
// Types describe only the fields the UI renders; extra fields are tolerated.

import { ApiError, errorFromResponse } from "./errors";

export const PROXY_BASE = "/api/cp";

export type Dict = Record<string, unknown>;

export interface Me {
  user_id: string;
  email: string;
  role: string;
  org: { id: string; slug: string; name: string };
  plan: { key: string; name: string; status: string; features: string[]; research_use_only: boolean };
}

export interface Plan {
  key: string;
  name: string;
  description?: string;
  features: string[];
  limits: Record<string, number>;
  price: { amount_cents: number; currency: string; interval: string } | null;
  trial_days?: number;
  research_use_only?: boolean;
  data_frequencies?: string[];
}

export interface Meter {
  kind: string;
  unit: string;
  used: number;
  limit: number | null;
  period?: string;
}
export interface Usage {
  plan: unknown;
  meters: Meter[];
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}
export interface CreatedApiKey {
  id: string;
  name: string;
  prefix: string;
  key: string;
}

export interface Credential {
  id: string;
  name: string;
  provider: string;
  created_at: string;
}

export interface DataFile {
  symbol: string;
  n_rows: number;
  first_ts: string;
  last_ts: string;
}
export interface DataSource {
  id: string;
  name: string;
  kind: "csv" | "provider";
  is_default: boolean;
  credential_id: string | null;
  created_at: string;
  files: DataFile[];
}

export type CalibrationStatus = "calibrated" | "stale" | "uncalibrated";

export interface ForecastOut {
  symbol: string;
  as_of: string;
  horizon: number;
  model_id: string;
  model_mix: Record<string, number>;
  model_card: unknown;
  calibration_status: CalibrationStatus;
  calibration: Dict | null;
  last_close: number;
  horizon_end: {
    median: number;
    lower_50: number;
    upper_50: number;
    lower_90: number;
    upper_90: number;
    median_return?: number;
    lower_90_return?: number;
    upper_90_return?: number;
  };
  median_path: number[];
  disclaimer?: string;
}

export interface CoverageRow {
  nominal: number;
  raw_coverage: number;
  calibrated_coverage: number;
  raw_width?: number;
  calibrated_width?: number;
}
export interface CalibrationOut {
  symbol: string;
  as_of: string;
  horizon: number;
  model_id: string;
  model_card: unknown;
  method: string;
  status: string;
  n_scores: number;
  holdout_n: number;
  tolerance: number;
  coverage: CoverageRow[];
  crps_raw?: number;
  crps_calibrated?: number;
  notes?: string[];
  disclaimer?: string;
}

export interface RiskOut extends Dict {
  report_id?: string;
}
export interface BacktestOut extends Dict {
  rows?: Dict[];
}

export interface BillingStatus {
  plan: string;
  status: string;
  trial_end: string | null;
  current_period_end: string | null;
  has_customer: boolean;
}
export interface InvoicePreview {
  currency: string;
  total_cents: number;
  period_end: string | null;
  lines: { description: string; amount_cents: number }[];
}
export interface OneOffInvoice {
  id: string;
  description: string;
  amount_cents: number;
  currency: string;
  status: string;
  created_at: string;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export interface FinetuneJob {
  id: string;
  status: JobStatus;
  symbols: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  gpu_seconds: number | null;
  error: string | null;
  model_id: string | null;
  params?: Dict;
}
export interface ModelGate {
  passed?: boolean;
  reasons?: string[];
  baseline?: unknown;
  candidate?: unknown;
  [k: string]: unknown;
}
export interface TunedModel {
  id: string;
  name: string;
  base_model: string;
  status: "candidate" | "promoted" | "rejected" | "archived";
  created_at: string;
  promoted_at: string | null;
  metrics?: Dict;
  gate?: ModelGate | null;
}
export interface Routing {
  default_model: string;
  overrides: Record<string, string>;
}

async function request<T>(method: string, path: string, body?: BodyInit | null, headers?: HeadersInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${PROXY_BASE}${path}`, {
      method,
      body: body ?? undefined,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "network_error", "Could not reach the dashboard server. Check your connection and try again.");
  }
  const text = await res.text();
  if (!res.ok) throw errorFromResponse(res.status, text, res.headers.get("retry-after"));
  if (res.status === 204 || text === "") return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(res.status, "bad_response", "The server returned an unexpected response.");
  }
}

const json = (data: unknown) => ({ body: JSON.stringify(data), headers: { "content-type": "application/json" } });
const post = <T>(path: string, data?: unknown) =>
  data === undefined ? request<T>("POST", path) : request<T>("POST", path, json(data).body, json(data).headers);
const put = <T>(path: string, data: unknown) => request<T>("PUT", path, json(data).body, json(data).headers);
const get = <T>(path: string) => request<T>("GET", path);
const del = <T = void>(path: string) => request<T>("DELETE", path);

export const api = {
  me: () => get<Me>("/v1/me"),
  plans: () => get<Plan[]>("/v1/plans"),
  usage: () => get<Usage>("/v1/usage"),
  audit: () => get<Dict[]>("/v1/audit"),

  listKeys: () => get<ApiKey[]>("/v1/api-keys"),
  createKey: (name: string, scopes: string[]) => post<CreatedApiKey>("/v1/api-keys", { name, scopes }),
  revokeKey: (id: string) => del(`/v1/api-keys/${encodeURIComponent(id)}`),

  listCredentials: () => get<Credential[]>("/v1/credentials"),
  createCredential: (name: string, provider: string, secret: string) =>
    post<{ id: string; name: string; provider: string }>("/v1/credentials", { name, provider, secret }),
  deleteCredential: (id: string) => del(`/v1/credentials/${encodeURIComponent(id)}`),

  listDataSources: () => get<DataSource[]>("/v1/data-sources"),
  createDataSource: (data: { name: string; kind: "csv" | "provider"; credential_id?: string; default?: boolean }) =>
    post<{ id: string; name: string; kind: string }>("/v1/data-sources", data),
  uploadCsv: (id: string, symbol: string, file: Blob) =>
    request<DataFile & { frequency?: string }>(
      "PUT",
      `/v1/data-sources/${encodeURIComponent(id)}/files/${encodeURIComponent(symbol)}`,
      file,
      { "content-type": "text/csv" },
    ),
  deleteDataSource: (id: string) => del(`/v1/data-sources/${encodeURIComponent(id)}`),

  forecast: (data: { symbol: string; horizon: number; model: string; calibrate: boolean; n_samples?: number; n_origins?: number }) =>
    post<ForecastOut>("/v1/forecast", data),
  calibrate: (data: { symbol: string; horizon: number; model: string; n_origins?: number; n_samples?: number }) =>
    post<CalibrationOut>("/v1/calibrate", data),
  risk: (data: {
    positions: Record<string, number>;
    horizon: number;
    model: string;
    calibrate: boolean;
    levels: number[];
    coupling: "terminal" | "stepwise";
    n_samples?: number;
    n_origins?: number;
  }) => post<RiskOut>("/v1/risk", data),
  backtest: (data: { symbol: string; horizon: number; models: string[]; folds: number; test_window: number }) =>
    post<BacktestOut>("/v1/backtest", data),
  reportJson: (id: string) => get<Dict>(`/v1/report/${encodeURIComponent(id)}?fmt=json`),

  billingStatus: () => get<BillingStatus>("/v1/billing/status"),
  checkout: (plan: string) => post<{ url: string }>("/v1/billing/checkout", { plan }),
  portal: () => post<{ url: string }>("/v1/billing/portal"),
  invoicePreview: () => get<InvoicePreview>("/v1/billing/preview"),
  oneOffInvoices: () => get<OneOffInvoice[]>("/v1/billing/one-off-invoices"),

  createJob: (data: { data_source_id: string; symbols: string[]; epochs: number; horizon: number }) =>
    post<FinetuneJob>("/v1/finetune/jobs", data),
  listJobs: () => get<FinetuneJob[]>("/v1/finetune/jobs"),
  cancelJob: (id: string) => post<FinetuneJob>(`/v1/finetune/jobs/${encodeURIComponent(id)}/cancel`),
  listModels: () => get<TunedModel[]>("/v1/models"),
  archiveModel: (id: string) => post<unknown>(`/v1/models/${encodeURIComponent(id)}/archive`),
  routing: () => get<Routing>("/v1/routing"),
  setRouting: (r: Routing) => put<Routing>("/v1/routing", r),
};

/** Same-origin BFF auth calls (cookie is set server-side; no token is returned). */
export async function authPost(path: "/api/auth/login" | "/api/auth/signup" | "/api/auth/logout", data?: unknown): Promise<void> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(data ?? {}),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "network_error", "Could not reach the dashboard server.");
  }
  if (!res.ok) throw errorFromResponse(res.status, await res.text(), res.headers.get("retry-after"));
}

// Proprietary: see ee/LICENSE
// API error type and mapping from control-plane envelopes to UI messages.

export interface ErrorEnvelope {
  error: { code: string; message: string; details?: unknown[] };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown[];
  readonly retryAfter: number | null;

  constructor(status: number, code: string, message: string, details: unknown[] = [], retryAfter: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

export function parseRetryAfter(value: string | null | undefined): number | null {
  if (!value) return null;
  const n = Number(value);
  if (Number.isFinite(n) && n >= 0) return Math.ceil(n);
  const date = Date.parse(value);
  if (!Number.isNaN(date)) return Math.max(0, Math.ceil((date - Date.now()) / 1000));
  return null;
}

/** Build an ApiError from an HTTP status + raw body text; tolerant of non-JSON bodies. */
export function errorFromResponse(status: number, bodyText: string, retryAfterHeader?: string | null): ApiError {
  let code = "http_error";
  let message = `Request failed (HTTP ${status}).`;
  let details: unknown[] = [];
  try {
    const parsed: unknown = JSON.parse(bodyText);
    const env = (parsed as { error?: unknown } | null)?.error;
    if (env && typeof env === "object") {
      const e = env as { code?: unknown; message?: unknown; details?: unknown };
      if (typeof e.code === "string") code = e.code;
      if (typeof e.message === "string") message = e.message;
      if (Array.isArray(e.details)) details = e.details;
    }
  } catch {
    /* non-JSON error body: keep defaults */
  }
  if (status === 401 && code === "http_error") code = "unauthorized";
  return new ApiError(status, code, message, details, parseRetryAfter(retryAfterHeader));
}

export type ErrorKind = "auth" | "plan" | "quota" | "rate" | "validation" | "network" | "other";

export interface ErrorView {
  kind: ErrorKind;
  title: string;
  message: string;
  /** Extra lines (validation details), already stringified. */
  lines: string[];
  action?: { label: string; href: string };
  retryAfter?: number | null;
}

export function stringifyDetail(d: unknown): string {
  if (typeof d === "string") return d;
  if (d && typeof d === "object") {
    const o = d as Record<string, unknown>;
    const loc = Array.isArray(o.loc) ? o.loc.join(".") : typeof o.field === "string" ? o.field : "";
    const msg = typeof o.msg === "string" ? o.msg : typeof o.message === "string" ? o.message : "";
    if (loc || msg) return [loc, msg].filter(Boolean).join(": ");
    try {
      return JSON.stringify(d);
    } catch {
      return String(d);
    }
  }
  return String(d);
}

export function describeError(err: unknown): ErrorView {
  if (!(err instanceof ApiError)) {
    return {
      kind: "network",
      title: "Something went wrong",
      message: err instanceof Error ? err.message : "Unexpected error.",
      lines: [],
    };
  }
  const lines = err.details.map(stringifyDetail);
  if (err.status === 401) {
    return {
      kind: "auth",
      title: "Please sign in",
      message: "Your session is missing or has expired.",
      lines: [],
      action: { label: "Sign in", href: "/login" },
    };
  }
  if (err.status === 403 && err.code === "plan_feature") {
    return {
      kind: "plan",
      title: "Not in your plan",
      message: err.message || "This feature is not included in your current plan.",
      lines,
      action: { label: "View plans and upgrade", href: "/usage" },
    };
  }
  if (err.status === 429 && err.code === "quota_exceeded") {
    return {
      kind: "quota",
      title: "Quota exceeded",
      message: err.message || "You have used all of your allowance for this period.",
      lines,
      action: { label: "Review usage and plan", href: "/usage" },
    };
  }
  if (err.status === 429) {
    const wait = err.retryAfter;
    return {
      kind: "rate",
      title: "Rate limited",
      message: wait !== null ? `Too many requests. Try again in ${wait} second${wait === 1 ? "" : "s"}.` : "Too many requests. Please slow down and try again shortly.",
      lines,
      retryAfter: wait,
    };
  }
  if (err.status === 422 || err.code === "invalid_data" || err.code === "invalid_request") {
    return {
      kind: "validation",
      title: err.code === "invalid_data" ? "The data was not usable" : "Invalid request",
      message: err.message,
      lines,
    };
  }
  return { kind: "other", title: `Error (${err.code})`, message: err.message, lines };
}

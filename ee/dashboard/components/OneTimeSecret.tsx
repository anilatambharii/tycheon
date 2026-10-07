// Proprietary: see ee/LICENSE
"use client";
import { useState } from "react";

export interface OneTimeSecretProps {
  /** Human label, e.g. "API key". */
  label: string;
  secret: string;
  /** Called when the user confirms they saved it; the parent must drop the secret from state. */
  onDismiss: () => void;
}

/**
 * Shows a secret exactly once. It is held only in the parent's state; after `onDismiss`
 * the parent removes it and it cannot be recovered (the server never returns it again).
 */
export default function OneTimeSecret({ label, secret, onDismiss }: OneTimeSecretProps) {
  const [copied, setCopied] = useState<"idle" | "ok" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied("ok");
    } catch {
      setCopied("failed");
    }
  }

  return (
    <section aria-labelledby="secret-title" className="card my-4 border-l-4" style={{ borderLeftColor: "var(--warn)" }}>
      <h2 id="secret-title" className="h2">
        Your new {label}
      </h2>
      <p role="alert" className="mb-2 text-sm font-medium" style={{ color: "var(--warn)" }}>
        Copy this {label} now. It is shown only once and cannot be retrieved later. If you lose it, revoke it and create a new one.
      </p>
      <label htmlFor="secret-value" className="label">
        {label}
      </label>
      <input id="secret-value" readOnly value={secret} className="input font-mono" onFocus={(e) => e.currentTarget.select()} autoComplete="off" />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-primary" onClick={copy}>
          Copy
        </button>
        <button type="button" className="btn" onClick={onDismiss}>
          I have saved it
        </button>
        <span role="status" aria-live="polite" className="text-sm">
          {copied === "ok" && "Copied to clipboard."}
          {copied === "failed" && "Copy failed; select the text and copy it manually."}
        </span>
      </div>
    </section>
  );
}

// Proprietary: see ee/LICENSE
"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { authPost } from "@/lib/api";
import { SLUG_RE } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import { Checklist } from "./Overview";
import { ErrorBox, Field } from "./ui";

export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-md px-4 py-10">
      <p className="mb-1 text-center text-sm font-semibold muted">Tycheon Cloud</p>
      <h1 className="h1 text-center">{title}</h1>
      <div className="card">{children}</div>
    </main>
  );
}

export function LoginForm() {
  const router = useRouter();
  const ssoFailed = useSearchParams().get("error") === "sso";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const action = useAction(async () => {
    await authPost("/api/auth/login", { email, password });
    router.replace("/");
    router.refresh();
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    void action.run();
  }
  return (
    <AuthCard title="Sign in">
      {ssoFailed && (
        <p role="alert" className="mb-3 text-sm" style={{ color: "var(--bad)" }}>
          Single sign-on failed or expired. Please try again.
        </p>
      )}
      <form onSubmit={submit}>
        <Field label="Email" id="email">
          <input id="email" type="email" required autoComplete="username" className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password" id="password">
          <input id="password" type="password" required autoComplete="current-password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <div aria-live="polite">
          <ErrorBox error={action.error} />
        </div>
        <button type="submit" className="btn btn-primary w-full" disabled={action.busy}>
          {action.busy ? "Signing in..." : "Sign in"}
        </button>
      </form>
      <p className="mt-4 text-sm">
        <Link href="/signup">Create an account</Link> | <Link href="/sso">Single sign-on</Link>
      </p>
    </AuthCard>
  );
}

export function SignupForm() {
  const [orgName, setOrgName] = useState("");
  const [slug, setSlug] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const action = useAction(async () => {
    await authPost("/api/auth/signup", {
      org_name: orgName.trim(),
      ...(slug.trim() ? { slug: slug.trim().toLowerCase() } : {}),
      email: email.trim(),
      password,
    });
    setDone(true);
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    if (password.length < 12) return setLocalError("Password must be at least 12 characters.");
    if (slug.trim() && !SLUG_RE.test(slug.trim().toLowerCase())) return setLocalError("Slug may use lowercase letters, digits and hyphens.");
    setLocalError(null);
    void action.run();
  }
  if (done) {
    return (
      <main className="mx-auto w-full max-w-xl px-4 py-10">
        <h1 className="h1">Welcome to Tycheon Cloud</h1>
        <p className="mb-4 text-sm">Your organization is ready. Three quick steps to get going:</p>
        <Checklist />
        <p className="mt-4 text-sm">
          <Link href="/">Go to the dashboard</Link>
        </p>
      </main>
    );
  }
  return (
    <AuthCard title="Create your organization">
      <form onSubmit={submit}>
        <Field label="Organization name" id="org">
          <input id="org" required className="input" value={orgName} onChange={(e) => setOrgName(e.target.value)} />
        </Field>
        <Field label="Organization slug (optional)" id="slug" hint="Used for SSO sign-in. Lowercase letters, digits, hyphens.">
          <input id="slug" className="input" aria-describedby="slug-hint" value={slug} onChange={(e) => setSlug(e.target.value)} />
        </Field>
        <Field label="Email" id="email">
          <input id="email" type="email" required autoComplete="username" className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password" id="password" hint="At least 12 characters.">
          <input id="password" type="password" required minLength={12} autoComplete="new-password" aria-describedby="password-hint" className="input" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <div aria-live="polite">
          {localError && (
            <p role="alert" className="mb-2 text-sm" style={{ color: "var(--bad)" }}>
              {localError}
            </p>
          )}
          <ErrorBox error={action.error} />
        </div>
        <button type="submit" className="btn btn-primary w-full" disabled={action.busy}>
          {action.busy ? "Creating..." : "Create account"}
        </button>
      </form>
      <p className="mt-4 text-sm">
        Already have an account? <Link href="/login">Sign in</Link>
      </p>
    </AuthCard>
  );
}

const SSO_ERRORS: Record<string, string> = {
  slug: "That organization slug is not valid.",
  unavailable: "The control plane is not reachable.",
  notfound: "SSO is not configured for that organization.",
  failed: "Single sign-on failed. Try again or sign in with a password.",
};

export function SsoForm() {
  const [slug, setSlug] = useState("");
  const err = useSearchParams().get("error");
  return (
    <AuthCard title="Single sign-on">
      {err && (
        <p role="alert" className="mb-3 text-sm" style={{ color: "var(--bad)" }}>
          {SSO_ERRORS[err] ?? "Single sign-on failed."}
        </p>
      )}
      <form action="/api/auth/sso/start" method="get">
        <Field label="Organization slug" id="slug" hint="Enterprise plan only. Ask your admin if you do not know it.">
          <input
            id="slug"
            name="slug"
            required
            pattern="[a-z0-9][a-z0-9\-]{1,38}"
            maxLength={39}
            aria-describedby="slug-hint"
            className="input"
            value={slug}
            onChange={(e) => setSlug(e.target.value.toLowerCase())}
          />
        </Field>
        <button type="submit" className="btn btn-primary w-full">
          Continue to your identity provider
        </button>
      </form>
      <p className="mt-4 text-sm">
        <Link href="/login">Back to password sign-in</Link>
      </p>
    </AuthCard>
  );
}

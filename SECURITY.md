# Security Policy

## Reporting a vulnerability

**Do not open a public issue.**

Report privately through
[GitHub Security Advisories](https://github.com/anilatambharii/tycheon/security/advisories/new).

Please include: affected version or commit, a description, reproduction steps,
and the impact you believe it has. We will acknowledge within 3 business days
and aim to ship a fix or a mitigation plan within 30 days. We will credit you
in the advisory unless you prefer otherwise.

## Supported versions

Tycheon is pre-1.0. Only the latest `main` and the latest release receive
security fixes.

## What we consider a vulnerability

Beyond the usual (RCE, injection, authentication bypass, secret disclosure,
dependency CVEs reachable from our code), the following are security issues in
this project specifically:

| Class | Why it matters |
|---|---|
| **Lookahead leakage** | Any path that lets data published after `as_of` reach a forecast or an evaluation. This silently inflates every result downstream, so we treat it as a vulnerability, not a bug. |
| **Prompt injection via market text** | News, filings and other external text are untrusted data. Any path where instructions inside them change system behaviour is a vulnerability. |
| **Governance bypass** | From Phase T4, any path that performs a governed action without passing a Keelgate policy gate, including a "degrade gracefully when the harness is missing" fallback. |
| **Uncertainty stripping** | Any path that emits a forecast without its intervals, calibration status, model mix and `as_of`. |
| **Licensed-data redistribution** | Any path that causes Tycheon to redistribute licensed exchange data, or that enables `yfinance` in a cloud deployment. |
| **Tenant crossing** | In Tycheon Cloud, any read or write that crosses a tenant boundary. |

## What is not in scope

- Forecast accuracy. A model being wrong is not a vulnerability; a model
  claiming calibration it does not have is.
- Findings against the local dev stack (`docker-compose.dev.yml`). It ships
  deliberately weak, localhost-only placeholder credentials and must never be
  exposed.
- Anything requiring `TYCHEON_ALLOW_LIVE_EXECUTION=true`, which is unsupported
  in v1. There is no live brokerage execution: paper and simulation only.

## Hardening in CI

Every PR runs secret scanning (detect-secrets against a committed baseline, and
gitleaks over history) and CodeQL with the security-extended query set.
Pre-commit blocks private keys and large files. `.env` is gitignored;
`.env.example` is committed with every secret value empty, and a test asserts it
stays that way.

GitHub dependency review is wired up but **not yet gating**: it needs the
repository Dependency graph setting enabled, tracked in issue #2. Until then,
new dependencies are reviewed by a human reading the PR.

## Disclaimer

Tycheon is for research and risk analytics. It is not investment advice.

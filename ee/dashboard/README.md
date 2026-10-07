# Tycheon Cloud dashboard

Customer dashboard for Tycheon Cloud. Proprietary: see [`ee/LICENSE`](../LICENSE).

Stack: Next.js 15 (App Router), React 19, TypeScript (strict), Tailwind CSS 3.4, Vitest.

For research and risk analytics. Not investment advice.

## Run

```bash
cd ee/dashboard
cp .env.example .env.local      # adjust if needed
npm install
npm run dev                     # http://localhost:3000
```

The control plane (`ee/control_plane`) must be running at `CONTROL_PLANE_URL` (default `http://localhost:8080`).
Without it, pages load but every API call shows an "unreachable" error.

## Environment

| Variable | Default | Notes |
| --- | --- | --- |
| `CONTROL_PLANE_URL` | `http://localhost:8080` | Server-side only. Never sent to the browser. |
| `CONTROL_PLANE_PUBLIC_URL` | `http://localhost:8080` | Browser-reachable control plane URL; used only to start SSO. |
| `NEXT_PUBLIC_LEADERBOARD_URL` | `https://anilatambharii.github.io/tycheon/leaderboard/` | Embedded on `/leaderboard`; its origin is added to the CSP `frame-src` at build time. |

## Scripts

| Script | What it does |
| --- | --- |
| `npm run dev` | Dev server |
| `npm run build` / `npm start` | Production build / server |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run lint` | `next lint` |
| `npm test` | `vitest run` |

## Security design

- **The browser never holds the API session token.** `/api/auth/login`, `/api/auth/signup` and the SSO ticket exchange call
  the control plane and store the returned token in an `httpOnly`, `SameSite=Lax` cookie (`Secure` except on
  plain-http localhost). Responses to the browser contain no token. `/api/auth/logout` clears it.
- **Proxy**: `app/api/cp/[...path]/route.ts` forwards browser calls to the control plane with
  `Authorization: Bearer <cookie token>`. It only forwards `/v1/...` (never `/auth/...`; traversal and encoded
  separators are refused), forwards an allow-list of request headers (never the cookie), strips hop-by-hop and
  `Set-Cookie` response headers, caps bodies at 1 MB (25 MB for the CSV upload `PUT`), refuses non-GET requests
  whose `Origin` does not match the host, and passes status codes and JSON error envelopes through. A 401 from the
  control plane clears the session cookie. The pure rules live in `lib/proxy-rules.ts` and are unit tested.
- **Middleware** redirects unauthenticated page requests to `/login`.
- **Risk reports** are served by `/api/report/[id]` and shown only in an iframe with `sandbox=""` and a
  `sandbox` CSP on the response. No `dangerouslySetInnerHTML` is used anywhere (the `react/no-danger` lint rule is on).
- **API key secrets** are shown once (`components/OneTimeSecret.tsx`), with a copy button and a warning, and are dropped
  from state when dismissed.
- Tokens are never logged. A strict CSP is set in `next.config.mjs` (`script-src` needs `'unsafe-inline'` for
  Next.js bootstrap scripts unless nonces are added).

## SSO

1. `/sso` takes an organization slug and sends the browser to `/api/auth/sso/start`. The slug is validated against
   `^[a-z0-9][a-z0-9-]{1,38}$`, then the browser is redirected straight to
   `${CONTROL_PLANE_PUBLIC_URL}/auth/oidc/{slug}/login`. This hop is not proxied, because the control plane sets
   its state cookie on its own origin.
2. The identity provider redirects back to the control plane, which redirects the browser to
   `/api/auth/sso/complete?ticket=<jwt>` on this dashboard.
3. `/api/auth/sso/complete` (server-side) rejects a missing ticket or one longer than 4000 characters, POSTs
   `{ticket}` to `${CONTROL_PLANE_URL}/auth/sso/exchange`, sets the same httpOnly session cookie as password login
   and redirects to `/`. Any failure redirects to `/login?error=sso` without echoing the ticket or the upstream
   message. The ticket is single-use, valid for 60 s, and never logged.

Not verified against a live control plane.

## Layout

- `app/(auth)` login, signup, sso. `app/(app)` the authenticated pages inside the shared nav layout.
- `lib/api.ts` typed client (calls `/api/cp/...`); `lib/errors.ts` error mapping; `lib/chart.ts` SVG scale helpers.
- `components/` UI, hand-written SVG charts (no chart library).
- `tests/` Vitest suites.

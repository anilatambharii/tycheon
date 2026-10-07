# syntax=docker/dockerfile:1.7
# Tycheon Cloud dashboard (proprietary, see ee/LICENSE). Build from ee/dashboard:
#   docker build -t tycheon-dashboard:dev ee/dashboard
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund

FROM ${NODE_IMAGE} AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* values are inlined at build time
ARG NEXT_PUBLIC_LEADERBOARD_URL=https://anilatambharii.github.io/tycheon/leaderboard/
ENV NEXT_PUBLIC_LEADERBOARD_URL=${NEXT_PUBLIC_LEADERBOARD_URL}
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Security fixes published since the pinned digest, then remove what a running server never uses:
# the package managers (npm bundles dozens of packages that scanners rightly flag) and their caches.
RUN apt-get update  && apt-get upgrade -y --no-install-recommends  && rm -rf /var/lib/apt/lists/*  && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack            /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /opt/yarn-* /usr/local/bin/yarn*
RUN find / -xdev -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true
WORKDIR /app
# the standalone output is self-contained: no node_modules, no source, no build toolchain
COPY --from=build --chown=0:0 /app/.next/standalone ./
COPY --from=build --chown=0:0 /app/.next/static ./.next/static
# Next bundles its own PostCSS for the *build*; the production server never runs it, and the copy it
# ships has published vulnerabilities that scanners flag
RUN rm -rf node_modules/next/node_modules/postcss
LABEL org.opencontainers.image.title="tycheon-dashboard" \
      org.opencontainers.image.description="Tycheon Cloud dashboard (proprietary, see ee/LICENSE). For research and risk analytics. Not investment advice." \
      org.opencontainers.image.licenses="LicenseRef-Tycheon-Enterprise" \
      org.opencontainers.image.source="https://github.com/anilatambharii/tycheon"
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]

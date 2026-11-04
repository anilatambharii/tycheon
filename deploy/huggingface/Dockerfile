# Tycheon demo Space. Built from the staging folder that deploy.py assembles, not from the repo root:
# it holds this file, start.sh, app/, README.md and one wheel built from the commit being deployed.
# Only the open-source package is installed; nothing from ee/ is ever in the staging folder.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin user

COPY wheel/ /tmp/wheel/
RUN pip install --no-cache-dir "$(ls /tmp/wheel/tycheon-*.whl)[serve]" \
 && rm -rf /tmp/wheel

COPY --chown=1000:1000 app/ /app/
COPY --chown=1000:1000 start.sh /app/start.sh

ENV PYTHONUNBUFFERED=1 \
    TYCHEON_ENV=production \
    TYCHEON_ALLOW_YFINANCE=false \
    TYCHEON_ALLOW_LIVE_EXECUTION=false
USER 1000
WORKDIR /app
EXPOSE 7860
CMD ["sh", "/app/start.sh"]

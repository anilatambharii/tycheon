# syntax=docker/dockerfile:1.7
#
# Tycheon images. One parametric Dockerfile, two products, two hardware variants.
#
#   --target api     the open-source forecast/risk server        (tycheon-serve, port 8080)
#   --target cloud   Tycheon Cloud control plane, MCP endpoint and fine-tune worker (proprietary)
#
#   --build-arg TORCH_BACKEND=cpu     CPU-only PyTorch (default; small)
#   --build-arg TORCH_BACKEND=cu124   PyTorch with the CUDA 12.4 runtime libraries bundled in the
#                                     wheels (large). The host needs an NVIDIA driver and the
#                                     NVIDIA container runtime; no CUDA base image is required.
#
# Build from the repository root, for example:
#   docker build --target api   -t tycheon-api:dev .
#   docker build --target cloud -t tycheon-cloud:dev --build-arg TORCH_BACKEND=cu124 .
#
# Properties, each checked by `deploy/docker/verify_image.sh`: the process runs as an unprivileged
# user (uid 10001), nothing is writable except /data, /tmp and the model cache, there is no
# package manager cache or build toolchain in the final image, and base images are pinned by digest.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG UV_IMAGE=ghcr.io/astral-sh/uv@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7

FROM ${UV_IMAGE} AS uv

# ----------------------------------------------------------------------------- builder
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY third_party ./third_party
# the wheel is exactly what is published to PyPI: the proprietary code is never in it
RUN uv build --wheel --out-dir /dist

ARG TORCH_BACKEND=cpu
ARG API_EXTRAS=serve,report,agents,kronos
ARG CLOUD_EXTRAS=ee,kronos
RUN uv venv /opt/api && \
    WHL="$(ls /dist/tycheon-*.whl)" && \
    VIRTUAL_ENV=/opt/api uv pip install --torch-backend "${TORCH_BACKEND}" \
        "tycheon[${API_EXTRAS}] @ file://${WHL}"
RUN uv venv /opt/cloud && \
    WHL="$(ls /dist/tycheon-*.whl)" && \
    VIRTUAL_ENV=/opt/cloud uv pip install --torch-backend "${TORCH_BACKEND}" \
        "tycheon[${CLOUD_EXTRAS}] @ file://${WHL}"

# ------------------------------------------------------------------------ runtime base
FROM ${PYTHON_IMAGE} AS runtime
# libgomp1: LightGBM and PyTorch's CPU kernels need the OpenMP runtime.
# libatomic1: Keelgate's Rego engine (regopy) is linked against it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 libatomic1 ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 tycheon \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin tycheon \
 && install -d -o 10001 -g 10001 -m 0750 /data /cache
# no setuid/setgid binaries: nothing in this image has a use for them
RUN find / -xdev -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/cache/huggingface \
    XDG_CACHE_HOME=/cache \
    TYCHEON_ENV=production \
    TYCHEON_ALLOW_YFINANCE=false \
    TYCHEON_ALLOW_LIVE_EXECUTION=false
WORKDIR /app
USER 10001:10001
EXPOSE 8080

# -------------------------------------------------------------------------------- api
FROM runtime AS api
COPY --from=builder --chown=0:0 /opt/api /opt/api
ENV PATH=/opt/api/bin:$PATH VIRTUAL_ENV=/opt/api
LABEL org.opencontainers.image.title="tycheon-api" \
      org.opencontainers.image.description="Tycheon open-source forecast and risk API. For research and risk analytics. Not investment advice." \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/anilatambharii/tycheon"
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"]
# API keys come from the environment (TYCHEON_API_KEYS); the server refuses to start without them
ENTRYPOINT ["tycheon-serve"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--state-dir", "/data/state"]

# ------------------------------------------------------------------------------ cloud
FROM runtime AS cloud
COPY --from=builder --chown=0:0 /opt/cloud /opt/cloud
COPY --chown=0:0 ee/control_plane /app/ee/control_plane
COPY --chown=0:0 ee/finetune /app/ee/finetune
ENV PATH=/opt/cloud/bin:$PATH \
    VIRTUAL_ENV=/opt/cloud \
    PYTHONPATH=/app/ee/control_plane:/app/ee/finetune \
    TYCHEON_CP_STORAGE_ROOT=/data
LABEL org.opencontainers.image.title="tycheon-cloud" \
      org.opencontainers.image.description="Tycheon Cloud control plane, MCP endpoint and fine-tune worker (proprietary, see ee/LICENSE)." \
      org.opencontainers.image.licenses="LicenseRef-Tycheon-Enterprise" \
      org.opencontainers.image.source="https://github.com/anilatambharii/tycheon"
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"]
# default: the API. The same image runs `python -m tycheon_cp.cli migrate|purge|report-usage` as
# Jobs and `python -m tycheon_ft.worker` as the fine-tune worker.
CMD ["python", "-m", "uvicorn", "tycheon_cp.cli:app_factory", "--factory", \
     "--host", "0.0.0.0", "--port", "8080", "--no-server-header", "--proxy-headers"]

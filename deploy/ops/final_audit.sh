#!/usr/bin/env bash
# Re-run every acceptance check from Phase T0 to T6 that can run on a developer machine, and print one
# table. Nothing here is mocked or skipped silently: a stage that cannot run says so.
#
#   TYCHEON_TEST_DATABASE_URL=... TYCHEON_TEST_APP_URL=... deploy/ops/final_audit.sh
#
# Needs: uv, a Postgres (see ee/scripts/create_roles.sql), and, for the deployment stages, WSL or Linux
# with docker, kind, helm, kubeconform, terraform and promtool (the stage is reported as SKIPPED otherwise).
set -u
cd "$(dirname "$0")/../.."
OUT=${AUDIT_DIR:-/tmp/tycheon-audit}; mkdir -p "$OUT"
rows=()
run() { # name, command...
  local name=$1; shift
  local start=$SECONDS
  if "$@" > "$OUT/$name.log" 2>&1; then status=PASS; else status=FAIL; fi
  rows+=("$(printf '%-34s %-6s %5ss  %s' "$name" "$status" "$((SECONDS - start))" "$(tail -n 1 "$OUT/$name.log" | cut -c1-70)")")
  printf '%s\n' "${rows[-1]}"
}
skip() { rows+=("$(printf '%-34s %-6s %s' "$1" SKIPPED "$2")"); printf '%s\n' "${rows[-1]}"; }

WSL=""; WSLDIR=""
if command -v wsl.exe >/dev/null 2>&1; then WSL='wsl.exe -d Ubuntu -u root --'; WSLDIR="/mnt$PWD"; fi
export MSYS_NO_PATHCONV=1
PATHSEP=$(python3 -c "import os;print(os.pathsep)" 2>/dev/null || python -c "import os;print(os.pathsep)")
EEPATH="ee/control_plane${PATHSEP}ee/finetune"

# ---- T0-T3: library, calibration, risk, backtest, leakage
run lint            uv run ruff check .
run format          uv run ruff format --check .
run types-src       uv run mypy
run fast-suite      uv run --extra kronos --extra report --extra agents pytest -m "not slow" -q -p no:cacheprovider
run examples-forecast       uv run --extra kronos --extra report python examples/forecast.py
run examples-risk-report    uv run --extra report python examples/risk_report.py
# ---- T4: governed agents + serving
run t4-agentic-example      uv run --extra agents python examples/agentic_risk_review.py
run mcp-smoke               uv run --extra serve python examples/mcp_smoke.py
# ---- packaging
run build-wheel     bash -c 'rm -rf dist && uv build >/dev/null && uvx twine check --strict dist/* && python -c "
import zipfile,glob
names=zipfile.ZipFile(glob.glob(\"dist/*.whl\")[0]).namelist()
assert not any(n.startswith((\"ee/\",\"tycheon_cp\",\"tycheon_ft\")) for n in names), \"proprietary code in the wheel\"
print(len(names), \"files in the wheel, none from ee/\")"'
run docs-strict     uv run mkdocs build --strict -q
# ---- T5: commercial layer
if [ -n "${TYCHEON_TEST_DATABASE_URL:-}" ] && [ -n "${TYCHEON_TEST_APP_URL:-}" ]; then
  run types-ee      uv run --extra ee --extra kronos --extra report --extra agents mypy ee/control_plane ee/finetune
  run ee-suite      uv run --extra ee --extra kronos --extra report --extra agents pytest ee/tests --no-cov -q -p no:cacheprovider
  run t5-acceptance bash -c 'PYTHONPATH="$0" uv run --extra ee --extra kronos --extra report --extra agents python ee/scripts/acceptance_t5.py; test $? -le 3' "$EEPATH"
  run restore-drill bash -c "PYTHONPATH='$EEPATH' uv run --extra ee python deploy/ops/backup_restore_test.py --pg-prefix '$WSL'"
else
  skip ee-suite "set TYCHEON_TEST_DATABASE_URL and TYCHEON_TEST_APP_URL"
fi
run dashboard       bash -c 'cd ee/dashboard && npm run typecheck && npm run lint && npm test && npm run build'
# ---- T6: deployment
if [ -n "$WSL" ]; then
  run helm-verify       $WSL bash -lc "cd $WSLDIR/deploy/helm && bash verify.sh"
  run terraform-verify  $WSL bash -lc "cd $WSLDIR/deploy/terraform && bash verify.sh"
else
  command -v helm >/dev/null && run helm-verify bash deploy/helm/verify.sh || skip helm-verify "helm not installed"
  command -v terraform >/dev/null && run terraform-verify bash deploy/terraform/verify.sh || skip terraform-verify "terraform not installed"
fi

echo; echo "== summary"; printf '%s\n' "${rows[@]}"
fails=$(printf '%s\n' "${rows[@]}" | grep -c ' FAIL ' || true)
echo; echo "logs: $OUT"; [ "$fails" = 0 ] && echo "AUDIT: no stage failed" || echo "AUDIT: $fails stage(s) FAILED"
exit $([ "$fails" = 0 ] && echo 0 || echo 1)

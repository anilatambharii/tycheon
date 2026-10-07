#!/usr/bin/env bash
# Offline verification of every Tycheon Terraform module and example.
#
# For each target it runs:
#   terraform fmt -check -recursive
#   terraform init -backend=false
#   terraform validate
#   terraform plan  (examples only; credential-less, see below)
# and prints a pass/fail table. Exit code is non-zero if any step fails.
#
# Offline plan: dummy credentials are exported, examples get -var offline_validation=true
# (AWS provider skip_* flags) and -refresh=false. No real cloud API is contacted by the
# steps that succeed; the AWS/GCP providers are never given real credentials.
#
# Usage:  ./verify.sh            # run everything
#         ./verify.sh aws        # only targets whose path starts with "aws"
# Requires: terraform >= 1.9 on PATH (run inside WSL/Linux/macOS; not Git Bash on Windows).

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILTER="${1:-}"
DATA_ROOT="${TF_VERIFY_DATA_DIR:-$(mktemp -d)}"
export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-${DATA_ROOT}/plugin-cache}"
mkdir -p "$TF_PLUGIN_CACHE_DIR"
export TF_IN_AUTOMATION=1 TF_INPUT=0 CHECKPOINT_DISABLE=1

# target | kind (module|example)
TARGETS=(
  "aws|module"
  "aws/examples/complete|example"
  "aws/examples/customer-vpc|example"
  "gcp|module"
  "gcp/examples/complete|example"
)

declare -a ROWS
FAILED=0

# status helper: PASS / FAIL / n/a
run_step() { # name, logfile, cmd...
  local log="$1"; shift
  if "$@" >"$log" 2>&1; then echo "PASS"; else echo "FAIL"; fi
}

for entry in "${TARGETS[@]}"; do
  target="${entry%%|*}"; kind="${entry##*|}"
  [[ -n "$FILTER" && "$target" != "$FILTER"* ]] && continue
  dir="$ROOT/$target"
  slug="${target//\//_}"
  log="$DATA_ROOT/$slug"
  export TF_DATA_DIR="$DATA_ROOT/data-$slug"
  mkdir -p "$TF_DATA_DIR"

  cloud="${target%%/*}"
  fmt_s="n/a"; init_s="n/a"; val_s="n/a"; plan_s="n/a"; plan_detail="-"

  pushd "$dir" >/dev/null || continue
  fmt_s=$(run_step "$log.fmt" terraform fmt -check -recursive)
  init_s=$(run_step "$log.init" terraform init -backend=false)
  if [[ "$init_s" == "PASS" ]]; then
    val_s=$(run_step "$log.validate" terraform validate)
  else
    val_s="SKIP"
  fi

  if [[ "$kind" == "example" && "$val_s" == "PASS" ]]; then
    plan_args=(-no-color -input=false -refresh=false -lock=false -var-file=terraform.tfvars.example -var offline_validation=true)
    if [[ "$cloud" == "aws" ]]; then
      env AWS_ACCESS_KEY_ID=AKIADUMMYDUMMYDUMMY AWS_SECRET_ACCESS_KEY=dummydummydummydummydummydummydummydummy \
          AWS_EC2_METADATA_DISABLED=true AWS_REGION= AWS_DEFAULT_REGION= \
          terraform plan "${plan_args[@]}" >"$log.plan" 2>&1
    else
      env GOOGLE_OAUTH_ACCESS_TOKEN=dummy-token GOOGLE_APPLICATION_CREDENTIALS= GCE_METADATA_HOST=127.0.0.1:9 \
          terraform plan "${plan_args[@]}" >"$log.plan" 2>&1
    fi
    if grep -Eq "^Plan: [0-9]+ to add, 0 to change, 0 to destroy" "$log.plan"; then
      plan_s="PASS"; plan_detail=$(grep -E "^Plan: " "$log.plan" | tail -1)
    else
      plan_s="FAIL"; plan_detail="see $log.plan"
    fi
  fi
  popd >/dev/null

  ROWS+=("$(printf '%-30s %-6s %-6s %-9s %-6s %s' "$target" "$fmt_s" "$init_s" "$val_s" "$plan_s" "$plan_detail")")
  for s in "$fmt_s" "$init_s" "$val_s" "$plan_s"; do [[ "$s" == "FAIL" ]] && FAILED=1; done
done

echo
printf '%-30s %-6s %-6s %-9s %-6s %s\n' TARGET FMT INIT VALIDATE PLAN DETAIL
printf '%-30s %-6s %-6s %-9s %-6s %s\n' ------------------------------ ------ ------ --------- ------ ------
for r in "${ROWS[@]}"; do echo "$r"; done
echo
echo "Logs: $DATA_ROOT  (set TF_VERIFY_DATA_DIR to keep them somewhere specific)"
exit $FAILED

#!/usr/bin/env bash
# Lint and schema-check the chart for every profile. Needs helm and kubeconform.
#   deploy/helm/verify.sh
set -u
cd "$(dirname "$0")"
fail=0
KUBE_VERSIONS=${KUBE_VERSIONS:-"1.29.0 1.31.0"}
profiles=("" "values-saas.yaml" "values-customer-vpc.yaml" "values-kind.yaml")
# a fully-enabled render exercises every template (ingress hosts, both worker pools, ...)
EXTRA=(--set controlPlane.enabled=true --set dashboard.enabled=true --set workers.cpu.enabled=true
       --set workers.gpu.enabled=true --set ingress.enabled=true
       --set ingress.hosts.api=forecast.example.com --set ingress.hosts.controlPlane=api.example.com
       --set ingress.hosts.dashboard=app.example.com --set otel.serviceMonitor.enabled=true)
printf '%-28s %-8s %-10s\n' profile lint kubeconform
for p in "${profiles[@]}"; do
  name=${p:-default}
  args=(); [ -n "$p" ] && args=(-f "tycheon/$p")
  if helm lint tycheon "${args[@]}" --strict >/dev/null 2>&1; then lint=pass; else lint=FAIL; fail=1; fi
  conf=pass
  for v in $KUBE_VERSIONS; do
    if ! helm template t tycheon "${args[@]}" "${EXTRA[@]}" 2>/dev/null \
        | kubeconform -strict -summary -kubernetes-version "$v" -skip ServiceMonitor >/tmp/kc.out 2>&1; then
      conf="FAIL($v)"; fail=1; cat /tmp/kc.out | head -20
    fi
  done
  printf '%-28s %-8s %-10s\n' "$name" "$lint" "$conf"
done
exit $fail

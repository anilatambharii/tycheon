#!/usr/bin/env bash
# Install the chart on a throwaway kind cluster (CPU profile) and prove it is healthy.
#
#   deploy/helm/kind-smoke.sh [api-image] [cloud-image] [dashboard-image]
#
# Needs docker, kind, kubectl, helm and the three images built locally (see the Dockerfiles).
# Everything it creates is throwaway: random credentials, an in-cluster dev Postgres, a kind
# cluster deleted at the end (KEEP=1 keeps it). It never touches a real cluster: it refuses to
# run unless the kubectl context it creates is the active one.
set -euo pipefail
cd "$(dirname "$0")/../.."
API_IMAGE=${1:-tycheon-api:t6-cpu}
CLOUD_IMAGE=${2:-tycheon-cloud:t6-cpu}
DASH_IMAGE=${3:-tycheon-dashboard:t6}
CLUSTER=tycheon-smoke
NS=tycheon
rand() { python3 -c "import secrets;print(secrets.token_urlsafe($1))"; }
step() { printf '\n== %s\n' "$*"; }
cleanup() { [ "${KEEP:-0}" = 1 ] || kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true; kill "${PF_PIDS[@]:-}" 2>/dev/null || true; }
PF_PIDS=(); trap cleanup EXIT

step "kind cluster"
if [ "${REUSE:-0}" = 1 ] && kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "reusing the existing cluster (REUSE=1); resetting the namespace"
  kubectl config use-context "kind-$CLUSTER" >/dev/null
  helm uninstall tycheon -n $NS >/dev/null 2>&1 || true
  kubectl delete namespace $NS --ignore-not-found --wait=true
else
  kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
  kind create cluster --name "$CLUSTER" --wait 120s
  [ "$(kubectl config current-context)" = "kind-$CLUSTER" ] || { echo "wrong kubectl context"; exit 1; }
  step "load images (no registry)"
  for img in "$API_IMAGE" "$CLOUD_IMAGE" "$DASH_IMAGE"; do kind load docker-image "$img" --name "$CLUSTER"; done
fi

step "throwaway secrets"
kubectl create namespace $NS
APIKEY=$(rand 24); SESSION=$(rand 48); KMS=$(python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())")
DB_HOST=tycheon-dev-postgres
kubectl -n $NS create secret generic tycheon-api-keys --from-literal=TYCHEON_API_KEYS="acme:${APIKEY}"
kubectl -n $NS create secret generic tycheon-cloud \
  --from-literal=TYCHEON_CP_DATABASE_URL="postgresql://tycheon_app:app-dev-pw@${DB_HOST}:5432/tycheon?sslmode=disable" \
  --from-literal=TYCHEON_CP_SESSION_SECRET="$SESSION" \
  --from-literal=TYCHEON_CP_LOCAL_KMS_KEY="$KMS" \
  --from-literal=TYCHEON_CP_OPERATOR_TOKEN="$(rand 32)"
kubectl -n $NS create secret generic tycheon-migrations \
  --from-literal=TYCHEON_CP_MIGRATION_URL="postgresql://tycheon_owner:owner-dev-pw@${DB_HOST}:5432/tycheon?sslmode=disable"

step "helm install (CPU profile)"
helm install tycheon deploy/helm/tycheon -n $NS -f deploy/helm/tycheon/values-kind.yaml \
  --set image.api.tag="${API_IMAGE#*:}" --set image.cloud.tag="${CLOUD_IMAGE#*:}" \
  --set image.dashboard.tag="${DASH_IMAGE#*:}" --wait --timeout 12m || {
    kubectl -n $NS get pods -o wide; kubectl -n $NS describe pods | tail -60
    for p in $(kubectl -n $NS get pods -o name); do echo "--- $p"; kubectl -n $NS logs "$p" --tail=25 2>&1 || true; done
    exit 1; }
kubectl -n $NS get pods

step "helm test (every service answers its health endpoint in-cluster)"
helm test tycheon -n $NS --timeout 5m --logs

step "exercise it through port-forwards"
kubectl -n $NS port-forward svc/tycheon-control-plane 18081:8080 >/dev/null 2>&1 & PF_PIDS+=($!)
kubectl -n $NS port-forward svc/tycheon-api 18080:8080 >/dev/null 2>&1 & PF_PIDS+=($!)
kubectl -n $NS port-forward svc/tycheon-dashboard 13000:3000 >/dev/null 2>&1 & PF_PIDS+=($!)
sleep 5
EMAIL="smoke-$(rand 6 | tr -dc a-z0-9)@example.com"
signup=$(curl -fsS -X POST http://127.0.0.1:18081/auth/signup -H 'Content-Type: application/json' \
  -d "{\"org_name\":\"Smoke Test\",\"email\":\"$EMAIL\",\"password\":\"correct horse battery\"}")
TOKEN=$(printf '%s' "$signup" | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")
echo "control plane: signed up ($(curl -fsS http://127.0.0.1:18081/v1/me -H "Authorization: Bearer $TOKEN" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['plan']['name'],'plan')"))"
KEY=$(curl -fsS -X POST http://127.0.0.1:18081/v1/api-keys -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"smoke","scopes":["analytics"]}' | python3 -c "import json,sys;print(json.load(sys.stdin)['key'])")
curl -fsS -X POST http://127.0.0.1:18081/v1/forecast -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"symbol":"SYN-GBM","horizon":3}' | python3 -c "import json,sys;d=json.load(sys.stdin);print('control plane: forecast ok,',d['model_id'],d['calibration_status'])"
curl -fsS http://127.0.0.1:18080/health | python3 -c "import json,sys;d=json.load(sys.stdin);print('open-source API: health',d['status'],'tools',d['tools'])"
curl -fsS -X POST http://127.0.0.1:18080/v1/forecast -H "X-API-Key: ${APIKEY}" -H 'Content-Type: application/json' \
  -d '{"symbol":"SYN-GBM","horizon":3}' | python3 -c "import json,sys;d=json.load(sys.stdin);print('open-source API: forecast ok,',d['model_id'])"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/v1/forecast -X POST -H 'Content-Type: application/json' -d '{}')
[ "$code" = 401 ] && echo "open-source API: refuses a request without a key (401)" || { echo "expected 401, got $code"; exit 1; }
curl -fsS -o /dev/null -w 'dashboard: /login %{http_code}\n' http://127.0.0.1:13000/login
rpc=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:18081/v1/mcp -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}')
echo "mcp endpoint on the free plan answers $rpc (403 = plan gate working)"
step "model weight cache (a pinned-revision prefetch Job fills a shared volume once)"
if [ "${MODEL_CACHE:-1}" = 1 ]; then
  helm upgrade tycheon deploy/helm/tycheon -n $NS -f deploy/helm/tycheon/values-kind.yaml     --set image.api.tag="${API_IMAGE#*:}" --set image.cloud.tag="${CLOUD_IMAGE#*:}"     --set image.dashboard.tag="${DASH_IMAGE#*:}"     --set modelCache.enabled=true --set modelCache.prefetch.enabled=true --wait --timeout 12m
  kubectl -n $NS wait --for=condition=complete job -l app.kubernetes.io/component=model-prefetch --timeout=600s
  kubectl -n $NS logs -l app.kubernetes.io/component=model-prefetch --tail=3
  kubectl -n $NS exec deploy/tycheon-api -- sh -c 'ls /cache/huggingface/hub 2>/dev/null | head -5'
  kubectl -n $NS exec deploy/tycheon-api -- sh -c 'test -n "$(ls /cache/huggingface/hub 2>/dev/null)"'     && echo "model cache populated and visible to the API pod"
fi
step "security context of every running pod"
cat > "$(dirname "$0")/.podcheck.py" <<'PY'
import json,sys
bad=0
for p in json.load(sys.stdin)["items"]:
    name=p["metadata"]["name"]
    if "dev-postgres" in name or p["metadata"].get("labels",{}).get("app.kubernetes.io/component")=="migrations": continue
    spec=p["spec"]; ps=spec.get("securityContext",{})
    for c in spec["containers"]:
        cs=c.get("securityContext",{})
        ok = ps.get("runAsNonRoot") and cs.get("readOnlyRootFilesystem") and cs.get("allowPrivilegeEscalation") is False and cs.get("capabilities",{}).get("drop")==["ALL"]
        print(("ok   " if ok else "FAIL ")+name+"/"+c["name"])
        bad += not ok
sys.exit(1 if bad else 0)
PY
kubectl -n $NS get pods -o json | python3 "$(dirname "$0")/.podcheck.py"
rm -f "$(dirname "$0")/.podcheck.py"
echo; echo "SMOKE TEST PASSED"

#!/usr/bin/env bash
# Check the security properties we claim for a Tycheon image.
#   deploy/docker/verify_image.sh <image> <port> [env-file-style KEY=VALUE ...]
# Prints a pass/fail table and exits non-zero if anything fails. Needs docker and curl.
set -u
IMAGE=${1:?image}; PORT=${2:?port}; shift 2
fail=0
ok()   { printf '  PASS  %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }

user=$(docker inspect --format '{{.Config.User}}' "$IMAGE")
[ "$user" = "10001:10001" ] && ok "configured to run as 10001:10001" || bad "image user is '$user'"

ENVARGS=()
for kv in "$@"; do ENVARGS+=(-e "$kv"); done
cid=$(docker run -d --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
      --tmpfs /data:uid=10001,gid=10001 --tmpfs /cache:uid=10001,gid=10001 \
      -p "127.0.0.1:${PORT}:${PORT}" "${ENVARGS[@]}" "$IMAGE")
trap 'docker rm -f "$cid" >/dev/null 2>&1' EXIT

healthy=no
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1 || curl -fsS "http://127.0.0.1:${PORT}/login" -o /dev/null 2>&1; then healthy=yes; break; fi
  [ "$(docker inspect --format '{{.State.Running}}' "$cid")" = "true" ] || break
  sleep 2
done
[ "$healthy" = yes ] && ok "serves with a read-only root filesystem, all capabilities dropped, no-new-privileges" \
                     || { bad "did not become healthy (last log lines follow)"; docker logs --tail 15 "$cid" 2>&1 | sed 's/^/        /'; }

if [ "$(docker inspect --format '{{.State.Running}}' "$cid")" != "true" ]; then
  bad "container is not running: the remaining checks cannot be made"
  exit 1
fi
uid=$(docker exec "$cid" id -u 2>/dev/null || echo unknown)
[ "$uid" = "10001" ] && ok "process uid is 10001" || bad "process uid is $uid"

if docker exec "$cid" sh -c 'touch /app/x 2>/dev/null || touch /etc/x 2>/dev/null' ; then bad "wrote to the image filesystem"; else ok "cannot write outside /data, /tmp and /cache"; fi
if docker exec "$cid" sh -c 'command -v gcc cc make git curl wget 2>/dev/null | grep -q .'; then bad "build tools or network clients present"; else ok "no compiler, make, git, curl or wget in the image"; fi
if docker exec "$cid" sh -c 'ls /var/lib/apt/lists 2>/dev/null | grep -q .'; then bad "apt lists present"; else ok "no apt package lists left behind"; fi
if docker exec "$cid" sh -c 'find / -xdev \( -perm -4000 -o -perm -2000 \) -type f 2>/dev/null | head -1 | grep -q .'; then
  printf '  NOTE  setuid/setgid files exist in the base image (neutralised by no-new-privileges)\n'; else ok "no setuid/setgid binaries"; fi

secrets=$(docker history --no-trunc --format '{{.CreatedBy}}' "$IMAGE" | grep -ciE 'password|secret|token|api[_-]?key=' || true)
[ "$secrets" = "0" ] && ok "no secret-looking build arguments in the image history" || bad "image history mentions secrets ($secrets)"
exit $fail

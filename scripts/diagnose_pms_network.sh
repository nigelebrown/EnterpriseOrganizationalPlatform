#!/usr/bin/env bash
# Read-only host-vs-Docker connectivity check for the PMS/HRIS SQL source.
# No passwords displayed or loaded into a container. No configuration changes.
set -Eeuo pipefail
CFG=/etc/jcf-registry
fail(){ printf '[PMS diagnostic] ERROR: %s\n' "$*" >&2; exit 1; }
log(){ printf '[PMS diagnostic] %s\n' "$*"; }
[[ "$EUID" == 0 ]] || fail 'Run as root with sudo'
[[ -f "$CFG/pms-source.env" && -f "$CFG/deployment.env" ]] ||
  fail 'PMS credentials/deployment not yet configured'
for bin in python3 docker ip;do command -v "$bin" >/dev/null || fail "Missing $bin";done
HOST="$(sed -n 's/^PMS_SOURCE_HOST=//p' "$CFG/pms-source.env" | head -1)"
[[ -n "$HOST" ]] || fail 'PMS_SOURCE_HOST not set'
# SQL Server host format is "IPv4" or "IPv4,port". Use Bash rather than
# awk here: the previously used compact awk ternary was invalid on CentOS 9.
IP="${HOST%%,*}"
PORT=1433
if [[ "$HOST" == *,* ]]; then PORT="${HOST##*,}"; fi
[[ "$IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || fail 'Expected PMS SQL IPv4 address'
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || fail 'Invalid PMS SQL TCP port'
log "Testing SQL endpoint $IP:$PORT (password never read)"
log 'Host route to PMS:'
ip -4 route get "$IP" || true
log 'Host Docker bridge routes (potential 172.17.0.0/16 overlap):'
ip -4 route show | grep -E 'docker0|br-' || true
log 'Docker default bridge subnet:'
docker network inspect bridge --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' || true
log 'JCF-EMP Compose network subnet:'
docker network inspect jcf-enterprise_default \
  --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' || true
log 'Testing host TCP connectivity, 5-second timeout:'
python3 - "$IP" "$PORT" <<'PY'
import socket,sys
ip=sys.argv[1]
port=int(sys.argv[2])
try:
    with socket.create_connection((ip,port),timeout=5):
        print("[PMS diagnostic] HOST TCP PASS")
except OSError as e:
    print("[PMS diagnostic] HOST TCP FAIL:",type(e).__name__,str(e))
PY
TAG="$(sed -n 's/^JCF_IMAGE_TAG=//p' "$CFG/deployment.env" | head -1)"
[[ "$TAG" =~ ^[a-zA-Z0-9_.-]+$ ]] || fail 'Cannot identify installed application image tag'
IMAGE="jcf/enterprise-organizational-platform:$TAG"
docker image inspect "$IMAGE" >/dev/null || fail 'Installed application image missing'
log 'Testing isolated import-container TCP connectivity, 5-second timeout:'
docker run --rm --network jcf-enterprise_default --entrypoint python "$IMAGE" \
  -c 'import socket,sys
try:
    with socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=5):
        print("[PMS diagnostic] CONTAINER TCP PASS")
except OSError as e:
    print("[PMS diagnostic] CONTAINER TCP FAIL:",type(e).__name__,str(e))' "$IP" "$PORT"
log 'If HOST or CONTAINER TCP FAIL, check route/ACL/firewall before attempting SQL login.'
log 'If both PASS, rerun preview and inspect the final SQLSTATE/ODBC message (redact secrets).'

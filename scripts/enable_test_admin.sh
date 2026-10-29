#!/usr/bin/env bash
# One-command admin/admin TEST login. Only for isolated SQL Server Express development.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONF=/etc/jcf-registry/runtime.env
MODE=/etc/jcf-registry/mode
say(){ printf '[JCF-EMP TEST ADMIN] %s\n' "$*"; }
fail(){ printf '[JCF-EMP TEST ADMIN] ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$EUID" == 0 ]] || fail 'Run as root: sudo bash scripts/enable_test_admin.sh'
[[ -f "$CONF" && -f "$MODE" ]] || fail 'Install the local Express test application first'
grep -qx 'INSTALL_MODE=local-express' "$MODE" ||
  fail 'Local test admin is unavailable outside the isolated Express installer mode'
grep -qx 'APP_ENV=development' "$CONF" || fail 'Only APP_ENV=development is permitted'
grep -qx 'DB_HOST=sqlserver,1433' "$CONF" || fail 'Refusing non-local SQL Server target'
grep -qx 'DB_NAME=JcfEnterpriseRegistry' "$CONF" || fail 'Unexpected database target'
grep -qx 'DB_AG_ENABLED=false' "$CONF" || fail 'Refusing availability-group SQL target'
python3 - "$CONF" <<'PY'
import hashlib
import os
from pathlib import Path
import secrets
import sys
import tempfile

path = Path(sys.argv[1])
lines = path.read_text().splitlines()
values = dict(line.split("=",1) for line in lines
              if line and not line.lstrip().startswith("#") and "=" in line)
old_key = values.get("ADMIN_LOCAL_TEST_SIGNING_KEY", "")
if old_key:
    try:
        assert len(bytes.fromhex(old_key)) >= 32
    except (ValueError,AssertionError):
        raise SystemExit("Invalid existing test signing key; review runtime.env")
salt = secrets.token_bytes(24)
password_digest = hashlib.pbkdf2_hmac("sha256", b"admin", salt, 310000)
password_hash = f"pbkdf2_sha256:310000:{salt.hex()}:{password_digest.hex()}"
reviewer_salt = secrets.token_bytes(24)
reviewer_digest = hashlib.pbkdf2_hmac("sha256", b"reviewer", reviewer_salt, 310000)
reviewer_hash = f"pbkdf2_sha256:310000:{reviewer_salt.hex()}:{reviewer_digest.hex()}"
updates = {
    "ADMIN_LOCAL_TEST_REVIEWER_USERNAME": "reviewer",
    "ADMIN_LOCAL_TEST_REVIEWER_PASSWORD_HASH": reviewer_hash,
    "ADMIN_LOCAL_TEST_ENABLED": "true",
    "ADMIN_LOCAL_TEST_USERNAME": "admin",
    "ADMIN_LOCAL_TEST_PASSWORD_HASH": password_hash,
    "ADMIN_LOCAL_TEST_SIGNING_KEY": old_key or secrets.token_hex(32),
}
rewritten = [line for line in lines if line.partition("=")[0] not in updates]
rewritten.extend(f"{key}={value}" for key,value in updates.items())
fd, name = tempfile.mkstemp(prefix=".runtime.env.", dir=str(path.parent))
try:
    os.fchmod(fd,0o600)
    with os.fdopen(fd,"w") as output:
        output.write("\n".join(rewritten)+"\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(name,path)
finally:
    if os.path.exists(name): os.unlink(name)
print("[JCF-EMP TEST ADMIN] Root-only test identity settings saved.")
PY
say 'Rebuilding/restarting application with test login; preserving SQL and seeded data'
bash "$ROOT/install.sh"
say 'Verifying local test identity advertised through API'
curl -fsS --max-time 12 http://127.0.0.1:8080/api/v1/admin/oidc-config | \
  python3 -c 'import json,sys; assert json.load(sys.stdin)["localTestEnabled"] is True' ||
  fail 'Local test login is not enabled; inspect application logs'
say 'Performing authenticated end-to-end directory check'
python3 - <<'PY'
import json
import urllib.request

base = "http://127.0.0.1:8080"
login = urllib.request.Request(
    base+"/api/v1/admin/local-test-login",
    data=json.dumps({"username":"admin","password":"admin"}).encode("utf-8"),
    headers={"Content-Type":"application/json"},
    method="POST",
)
with urllib.request.urlopen(login,timeout=10) as response:
    token = json.load(response)["access_token"]
request = urllib.request.Request(
    base+"/api/v1/registry/locations?limit=1",
    headers={"Authorization":"Bearer "+token},
)
with urllib.request.urlopen(request,timeout=10) as response:
    locations = json.load(response)
assert "items" in locations and isinstance(locations["items"],list)
print("[JCF-EMP TEST ADMIN] Login accepted; protected Location API returned successfully.")
PY
say 'SUCCESS. Local development test identities:'
say '  Maker:    admin / admin'
say '  Reviewer: reviewer / reviewer'
say 'Use Local test login on https://172.17.102.101/admin, sign out and switch accounts to approve corrections.'
say 'Production/AG deployments cannot enable local test identities.'

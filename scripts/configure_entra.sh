#!/usr/bin/env bash
# Configure JCF-EMP's existing Entra ID app registration WITHOUT disabling local test login.
# Requires: a registered SPA redirect URI and delegated API scopes on the app registration.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONF=/etc/jcf-registry/runtime.env
TENANT=1ea2e2cf-0bfb-42f2-96a9-52a3a96317e1
CLIENT=9eb6b06d-0975-4991-bfff-6a8937d68fbd
REDIRECT=''
usage(){
 cat <<'HELP'
Configure JCF-EMP Microsoft Entra ID for the existing CentOS 9 development app,
without removing the local admin/admin login.
Usage:
  sudo bash scripts/configure_entra.sh --redirect-uri https://approved-jcf-dns.example/admin
  sudo bash scripts/configure_entra.sh --redirect-uri https://172.17.102.101/admin

Before login works, register EXACTLY that URI under a Single-page application
in Microsoft Entra ID (NOT just Web), and expose/consent the app's four
delegated API scopes: registry.read, facilities.read, reporting.read, contacts.read.
The Entra app registration is not modified by this script.
HELP
}
while (($#));do
 case "$1" in
  --redirect-uri) [[ $# -ge 2 ]] || { usage; exit 2; }; REDIRECT="$2";shift 2;;
  --help|-h)usage;exit 0;;
  *)echo "Unexpected argument: $1" >&2;usage;exit 2;;
 esac
done
[[ "$EUID" -eq 0 ]] || { echo "Run with sudo/root" >&2;exit 1; }
[[ -f "$CONF" ]] || { echo "Missing $CONF" >&2;exit 1; }
[[ -n "$REDIRECT" ]] || { usage;exit 2; }
python3 - "$CONF" "$TENANT" "$CLIENT" "$REDIRECT" <<'PY'
import os,sys,tempfile
from pathlib import Path
from urllib.parse import urlsplit

path=Path(sys.argv[1]); tenant,client,redirect=sys.argv[2:]
url=urlsplit(redirect)
if url.scheme!='https' or not url.netloc or url.path!='/admin' or url.query or url.fragment or url.username or url.password:
    raise SystemExit("Redirect URI must be a valid HTTPS URL ending in /admin, without query or fragment")
lines=path.read_text().splitlines()
values=dict(line.split('=',1) for line in lines if '=' in line and not line.lstrip().startswith('#'))
if values.get('APP_ENV')!='development' or values.get('DB_AG_ENABLED')!='false' or values.get('DB_HOST')!='sqlserver,1433':
    raise SystemExit("Script intended only for local development Express deployment")
base=f'https://login.microsoftonline.com/{tenant}'
api=f'api://{client}'
updates={
 'AUTH_ISSUER':base+'/v2.0',
 'AUTH_AUDIENCE':client,
 'AUTH_JWKS_URL':base+'/discovery/v2.0/keys',
 'ADMIN_OIDC_CLIENT_ID':client,
 'ADMIN_OIDC_AUTHORIZATION_ENDPOINT':base+'/oauth2/v2.0/authorize',
 'ADMIN_OIDC_TOKEN_ENDPOINT':base+'/oauth2/v2.0/token',
 'ADMIN_REDIRECT_URI':redirect,
 'ADMIN_OIDC_SCOPE': 'openid profile '+ ' '.join(api+'/'+name for name in (
   'registry.read','facilities.read','reporting.read','contacts.read')),
}
new_lines=[line for line in lines if line.partition('=')[0] not in updates]
new_lines.extend(f'{key}={value}' for key,value in updates.items())
fd,tmp=tempfile.mkstemp(prefix='.runtime.entra.',dir=path.parent)
try:
    os.fchmod(fd,0o600)
    with os.fdopen(fd,'w') as out:
        out.write('\n'.join(new_lines)+'\n')
        out.flush();os.fsync(out.fileno())
    os.replace(tmp,path)
finally:
    if os.path.exists(tmp):os.unlink(tmp)
print("[JCF-EMP Entra] Entra settings saved; local admin settings retained unchanged.")
PY
bash "$ROOT/install.sh"
python3 - <<'PY'
import json,urllib.request
with urllib.request.urlopen('http://127.0.0.1:8080/api/v1/admin/oidc-config',timeout=10) as res:
    config=json.load(res)
assert config['clientId']=='9eb6b06d-0975-4991-bfff-6a8937d68fbd'
print("[JCF-EMP Entra] Client settings active. Local test login enabled:",config['localTestEnabled'])
PY
echo "[JCF-EMP Entra] App restarted. Configure SPA redirect and delegated API scopes in Entra; both sign-in choices will be visible when enabled."

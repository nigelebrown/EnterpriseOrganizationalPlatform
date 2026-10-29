#!/usr/bin/env bash
# JCF-EMP: CentOS Stream 9 HTTPS Nginx installer (test or CA-signed certificate).
set -Eeuo pipefail
umask 077
IP=172.17.102.101
DNS_NAME=''
TEST_CERT=false
CERT=''
KEY=''
CONF=/etc/nginx/conf.d/jcf-emp.conf
log(){ printf '[JCF-EMP HTTPS] %s\n' "$*"; }
die(){ printf '[JCF-EMP HTTPS] ERROR: %s\n' "$*" >&2; exit 1; }
usage(){
  cat <<'HELP'
Install Nginx HTTPS proxy for JCF-EMP on CentOS Stream 9:
  --test-self-signed          Create/reuse a self-signed 90-day TEST certificate
  --ip ADDRESS                Host IPv4 (default 172.17.102.101)
  --hostname FQDN             Optional DNS name
  --cert PATH --key PATH      CA-signed certificate and matching private key
  --help                      Show help
Uses 127.0.0.1:8080 backend; opens only HTTPS in interface firewalld zone.
Does not modify SQL, application configuration, or Docker volumes.
HELP
}
while (($#)); do
  case "$1" in
    --ip) [[ $# -ge 2 ]] || die 'Missing IP'; IP="$2"; shift 2;;
    --hostname) [[ $# -ge 2 ]] || die 'Missing hostname'; DNS_NAME="$2"; shift 2;;
    --cert) [[ $# -ge 2 ]] || die 'Missing certificate'; CERT="$2"; shift 2;;
    --key) [[ $# -ge 2 ]] || die 'Missing key'; KEY="$2"; shift 2;;
    --test-self-signed) TEST_CERT=true; shift;;
    -h|--help) usage; exit 0;;
    *) die "Unknown argument: $1";;
  esac
done
[[ "$EUID" == 0 ]] || die 'Run with sudo/root'
[[ -r /etc/os-release ]] || die 'Cannot identify OS'
. /etc/os-release
[[ "$ID" == centos && "$VERSION_ID" == 9* ]] || die 'Only CentOS Stream 9 is supported'
[[ "$IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || die 'Invalid IPv4 address'
if [[ -n "$DNS_NAME" ]]; then
  [[ "$DNS_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ && "$DNS_NAME" != *..* ]] ||
    die 'Invalid hostname'
fi
if [[ "$TEST_CERT" == true ]]; then
  [[ -z "$CERT" && -z "$KEY" ]] || die 'Do not mix certificate modes'
else
  [[ -n "$CERT" && -n "$KEY" ]] || die 'Use --test-self-signed or --cert plus --key'
fi
command -v curl >/dev/null && command -v ip >/dev/null &&
  command -v flock >/dev/null && command -v ss >/dev/null ||
  die 'Missing host dependencies (curl/ip/flock/ss)'
exec 8>/run/lock/jcf-emp-proxy.lock
flock -n 8 || die 'Another proxy installation is in progress'
DEVICE="$(ip -o -4 addr show | awk -v addr="$IP" 'index($4,addr"/")==1 {print $2;exit}')"
[[ -n "$DEVICE" ]] || die "Server IP $IP not found on a local interface"
curl -fsS --max-time 8 http://127.0.0.1:8080/health/ready | grep -q '"ready"' ||
  die 'JCF-EMP backend is not healthy at 127.0.0.1:8080'
if ss -H -lnt '( sport = :443 )' | grep -q . && [[ ! -f "$CONF" ]]; then
  die 'TCP port 443 is in use by another service; refusing to replace it'
fi
log 'Installing Nginx and TLS dependencies'
dnf -y install nginx openssl policycoreutils firewalld >/dev/null
command -v nginx >/dev/null || die 'Nginx unavailable'
SITE="$IP"
[[ -z "$DNS_NAME" ]] || SITE="$DNS_NAME"
if [[ "$TEST_CERT" == true ]]; then
  CERT=/etc/pki/tls/certs/jcf-emp-test.crt
  KEY=/etc/pki/tls/private/jcf-emp-test.key
  if [[ -e "$CERT" || -e "$KEY" ]]; then
    [[ -f "$CERT" && -f "$KEY" ]] || die 'Incomplete existing test certificate pair'
  else
    log 'Creating a self-signed test-only TLS certificate (browser trust warning expected)'
    mkdir -p /etc/pki/tls/certs /etc/pki/tls/private
    SAN="IP:$IP"
    [[ -z "$DNS_NAME" ]] || SAN="$SAN,DNS:$DNS_NAME"
    openssl req -x509 -newkey rsa:3072 -sha256 -days 90 -nodes \
      -keyout "$KEY" -out "$CERT" -subj "/CN=$SITE" \
      -addext "subjectAltName=$SAN" -addext "extendedKeyUsage=serverAuth" \
      -addext "basicConstraints=critical,CA:FALSE" >/dev/null 2>&1
    chmod 0600 "$KEY"
    chmod 0644 "$CERT"
  fi
else
  [[ -r "$CERT" && -r "$KEY" ]] || die 'TLS certificate/key unavailable'
fi
openssl x509 -in "$CERT" -noout -checkend 0 >/dev/null || die 'TLS certificate expired'
if [[ -n "$DNS_NAME" ]]; then
  openssl x509 -in "$CERT" -noout -checkhost "$SITE" | grep -q 'does match' ||
    die 'Certificate does not match DNS hostname'
else
  openssl x509 -in "$CERT" -noout -checkip "$IP" | grep -q 'does match' ||
    die 'Certificate does not include the IP subjectAltName'
fi
[[ "$(openssl x509 -in "$CERT" -pubkey -noout | sha256sum | cut -d' ' -f1)" == \
   "$(openssl pkey -in "$KEY" -pubout | sha256sum | cut -d' ' -f1)" ]] ||
  die 'TLS private key does not match certificate'
OLD_CONF=''
if [[ -f "$CONF" ]]; then
  grep -Fq '# Managed by JCF-EMP HTTPS installer' "$CONF" ||
    die 'An unmanaged config already exists at JCF-EMP path'
  OLD_CONF="$(mktemp /etc/nginx/conf.d/jcf-emp.backup.XXXXXX)"
  cp -p "$CONF" "$OLD_CONF"
fi
TMP="$(mktemp /etc/nginx/conf.d/.jcf-emp.tmp.XXXXXX)"
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<NGINX
# Managed by JCF-EMP HTTPS installer
server {
    listen $IP:443 ssl;
    server_name $SITE;
    ssl_certificate $CERT;
    ssl_certificate_key $KEY;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
    server_tokens off;
    client_max_body_size 25m;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port 443;
        proxy_connect_timeout 10s;
        proxy_read_timeout 75s;
        proxy_send_timeout 75s;
    }
}
NGINX
install -m 0644 "$TMP" "$CONF"
rollback(){
  if [[ -n "$OLD_CONF" ]]; then cp -p "$OLD_CONF" "$CONF"; else rm -f "$CONF"; fi
  nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
}
if ! nginx -t; then rollback; die 'Invalid Nginx configuration'; fi
if command -v getenforce >/dev/null && [[ "$(getenforce)" == Enforcing ]]; then
  if getsebool httpd_can_network_connect | grep -q 'off$'; then
    log 'Applying SELinux httpd_can_network_connect policy for reverse proxy'
    setsebool -P httpd_can_network_connect on
  fi
fi
if ! systemctl enable --now nginx || ! systemctl reload nginx; then
  rollback
  die 'Nginx failed to start/reload'
fi
# Insecure verification is confined to an on-host transport smoke test.
# Production client certificate validation must still be checked separately.
if ! curl -ksSf --noproxy '*' --max-time 12 "https://$IP/health/ready" | grep -q '"ready"'; then
  rollback
  die 'Nginx HTTPS route is not healthy; inspect journalctl -u nginx'
fi
systemctl is-active --quiet firewalld || die 'firewalld must be running to open port 443'
ZONE="$(firewall-cmd --get-zone-of-interface="$DEVICE" 2>/dev/null || true)"
[[ -n "$ZONE" && "$ZONE" != no ]] || ZONE="$(firewall-cmd --get-default-zone)"
log "Enabling HTTPS only in firewalld zone $ZONE ($DEVICE)"
firewall-cmd --zone="$ZONE" --add-service=https >/dev/null
firewall-cmd --permanent --zone="$ZONE" --add-service=https >/dev/null
firewall-cmd --zone="$ZONE" --query-service=https >/dev/null ||
  die 'HTTPS firewall service is not active'
rm -f "$OLD_CONF"
log "SUCCESS: Browser address https://$IP/admin"
if [[ -n "$DNS_NAME" ]]; then log "DNS name (once DNS is configured): https://$DNS_NAME/admin"; fi
if [[ "$TEST_CERT" == true ]]; then
  log 'WARNING: Browser trust warning is expected; replace the test cert with an approved certificate.'
fi
log 'Sign-in still requires JCF OIDC/Entra ID configuration.'

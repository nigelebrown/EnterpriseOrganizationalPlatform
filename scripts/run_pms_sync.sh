#!/usr/bin/env bash
# PMS/HRIS reference-table sync into isolated JCF-EMP test SQL Server.
# sudo bash scripts/run_pms_sync.sh setup|schema|relationships|inspect|preview|apply|verify|sync
set -Eeuo pipefail
ACTION=preview
if [[ $# -gt 0 ]];then ACTION="$1";fi
CONFIG_DIR=/etc/jcf-registry
SECRETS="$CONFIG_DIR/pms-source.env"
DEPLOY="$CONFIG_DIR/deployment.env"
MODE="$CONFIG_DIR/mode"
fail(){ echo "[PMS SYNC] ERROR: $*" >&2; exit 1; }
log(){ echo "[PMS SYNC] $*"; }
[[ "$EUID" -eq 0 ]] || fail "Run as root/sudo"
case "$ACTION" in setup|schema|relationships|inspect|preview|apply|verify|sync);; *)fail "Use setup, schema, relationships, inspect, preview, apply, verify or sync";;esac
[[ -r "$MODE" ]] || fail "JCF-EMP is not installed"
grep -qx INSTALL_MODE=local-express "$MODE" || fail "Cannot import into external/production SQL"
[[ -r "$CONFIG_DIR/migration.env" ]] || fail "Missing migration DB settings"
grep -qx APP_ENV=development "$CONFIG_DIR/migration.env" || fail "Requires local development SQL"
command -v docker >/dev/null || fail "Docker missing"
if [[ "$ACTION" == setup || ! -f "$SECRETS" ]]; then
  if [[ ! -t 0 ]];then
    fail "PMS secret config missing. Use python3 scripts/configure_pms_source.py --from-zip <archive> or --password-stdin instead."
  fi
  read -r -p "PMS SQL hostname/IP [AD Reset settings: 172.17.51.49]: " host
  if [[ -z "$host" ]];then host=172.17.51.49;fi
  read -r -p "PMS SQL database [supplied connection: HRIS_Duplicate]: " database
  if [[ -z "$database" ]];then database=HRIS_Duplicate;fi
  read -r -p "PMS SQL login [AD Reset settings: HRISUser]: " login
  if [[ -z "$login" ]];then login=HRISUser;fi
  read -r -s -p "PMS READ-ONLY SQL password: " password
  printf '\n'
  [[ -n "$login" && -n "$password" ]] || fail "Read-only SQL login and password required"
  for s in "$host" "$database" "$login" "$password"; do
    [[ "$s" != *$'\n'* && "$s" != *$'\r'* ]] || fail "Newlines not supported"
  done
  [[ "$host" != *';'* && "$database" != *';'* ]] || fail "Invalid SQL host or database"
  read -r -p "Trust PMS SQL Server certificate (matches supplied connection string)? [Y/n]: " valid
  if [[ -z "$valid" ]];then valid=Y;fi
  trust=true
  if [[ "$valid" == n || "$valid" == N ]]; then
    trust=false
  else
    log "TEST ONLY: PMS SQL certificate validation is disabled, matching supplied AD Reset connection settings"
  fi
  umask 077
  tmp="$(mktemp "$CONFIG_DIR/.pms-source.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  {
    printf 'PMS_SOURCE_HOST=%s\n' "$host"
    printf 'PMS_SOURCE_DB=%s\n' "$database"
    printf 'PMS_SOURCE_USER=%s\n' "$login"
    printf 'PMS_SOURCE_PASSWORD=%s\n' "$password"
    printf 'PMS_SOURCE_TRUST_CERT=%s\n' "$trust"
  } > "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$SECRETS"
  unset password
  trap - EXIT
  log "Read-only source connection saved to root-only $SECRETS (never Git)"
  if [[ "$ACTION" == setup ]]; then exit 0; fi
fi
[[ -f "$DEPLOY" ]] || fail "Missing managed deployment.env. Run bash install.sh first."
tag="$(sed -n 's/^JCF_IMAGE_TAG=//p' "$DEPLOY" | head -1)"
[[ "$tag" =~ ^[A-Za-z0-9_.-]+$ ]] || fail "Invalid deployment image tag"
image="jcf/enterprise-organizational-platform:$tag"
docker image inspect "$image" >/dev/null || fail "Application image missing. Run bash install.sh first."
docker network inspect jcf-enterprise_default >/dev/null ||
 fail "JCF-EMP Compose network missing. Run installer and inspect compose networking."
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run(){
 # Mount current checked-out scripts read-only: importer fixes and schema probes
 # require only git pull, not a rebuilt application container/database upgrade.
 docker run --rm --network jcf-enterprise_default \
    --mount "type=bind,src=$ROOT/scripts,dst=/app/scripts,readonly" \
    --env-file "$CONFIG_DIR/migration.env" \
    --env-file "$SECRETS" \
    --entrypoint python "$image" -m scripts.pms_registry_sync "$@"
}
schema(){
 docker run --rm --network jcf-enterprise_default \
    --mount "type=bind,src=$ROOT/scripts,dst=/app/scripts,readonly" \
    --env-file "$CONFIG_DIR/migration.env" \
    --env-file "$SECRETS" \
    --entrypoint python "$image" -m scripts.probe_pms_schema --compare HRIS_Copy
}
case "$ACTION" in
 schema)schema;;
 relationships)run relationships;;
 inspect)run inspect;;
 preview)run preview --include-pdf;;
 verify)run verify;;
 apply)run apply --confirm-test-import --include-pdf;;
 sync)
    log "Extracting PMS read-only organizational data; reporting unresolved relationships"
    run preview --include-pdf
    log "Applying TEST sync; unresolved hierarchy links remain unassigned"
    run apply --confirm-test-import --include-pdf
    run verify;;
esac

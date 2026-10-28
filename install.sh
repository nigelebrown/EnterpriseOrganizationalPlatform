#!/usr/bin/env bash
# JCF Enterprise Organizational Platform: fresh Linux VM install AND safe repeat upgrade.
# Usage: sudo bash install.sh [--mode local-express|external] [--runtime-file FILE --migration-file FILE]
set -Eeuo pipefail
umask 077

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
COMPOSE_FILE="$ROOT/deploy/compose.yaml"
CONFIG_DIR=/etc/jcf-registry
STATE_FILE="$CONFIG_DIR/deployment.env"
LOCK_FILE=/var/lock/jcf-registry-install.lock
MODE=''
RUNTIME_SOURCE=''
MIGRATION_SOURCE=''
BACKUP_REFERENCE=''
DRY_RUN=false
INSTALL_DOCKER=true
APP_PORT=8080
CANDIDATE=''
STARTED_DEPLOY=false
OLD_TAG=''

usage() {
  cat <<'HELP'
JCF Enterprise Organizational Platform — repeatable Linux installer.

Fresh isolated TEST VM (creates persistent SQL Server 2019 Express):
  sudo bash install.sh --mode local-express

After git pull (preserves SQL data and config; backup, migration, health validation):
  sudo bash install.sh

External SQL Server 2019 / Always On AG (do not install SQL on the app VM):
  sudo bash install.sh --mode external --runtime-file /root/jcf-runtime.env \
    --migration-file /root/jcf-migration.env --backup-reference DBA-verified-backup-id

Options:
  --mode local-express|external  Initial database mode; immutable after first install
  --runtime-file FILE           Existing app credential/OIDC environment file (external mode)
  --migration-file FILE         Separately privileged schema migration environment file
  --backup-reference REF        Mandatory DBA-confirmed recoverable backup for external mode
  --port 8080                   Bound only to 127.0.0.1; provide trusted HTTPS reverse proxy
  --skip-docker-install         Do not add Docker packages if not already installed
  --dry-run                     Display intended operations, make no changes
  -h, --help                    Print this help

Secrets are never echoed. Config remains root-only in /etc/jcf-registry.
No data volume is deleted or schema downgraded automatically.
Administrative login needs JCF OIDC; local Express bootstrap does not bypass authentication.
HELP
}
log(){ printf '[JCF install] %s\n' "$*"; }
die(){ printf '[JCF install] ERROR: %s\n' "$*" >&2; exit 1; }
value_from_env(){
  local name="$1" path="$2"
  [ -f "$path" ] || return 1
  sed -n "s/^${name}=//p" "$path" | tail -n 1
}

while (($#)); do
  case "$1" in
    --mode) MODE="${2:?Missing --mode value}"; shift 2;;
    --runtime-file) RUNTIME_SOURCE="${2:?Missing file}"; shift 2;;
    --migration-file) MIGRATION_SOURCE="${2:?Missing file}"; shift 2;;
    --backup-reference) BACKUP_REFERENCE="${2:?Missing backup reference}"; shift 2;;
    --port) APP_PORT="${2:?Missing port}"; shift 2;;
    --skip-docker-install) INSTALL_DOCKER=false; shift;;
    --dry-run) DRY_RUN=true; shift;;
    -h|--help) usage; exit 0;;
    *) die "Unknown option: $1";;
  esac
done
[[ "$MODE" == '' || "$MODE" == 'local-express' || "$MODE" == 'external' ]] || die 'Mode must be local-express or external'
[[ "$APP_PORT" =~ ^[0-9]+$ ]] && ((APP_PORT>=1024 && APP_PORT<=65535)) || die 'Invalid --port (1024..65535)'
[[ -f "$COMPOSE_FILE" && -f "$ROOT/alembic.ini" ]] || die 'Installer must be run from the full checked-out repository'
if [[ "$DRY_RUN" == true ]]; then
  log "Plan: detect OS/Docker, initialize or preserve /etc/jcf-registry config; build image; verify database; backup; Alembic upgrade; start health-checked service. Requested mode: ${MODE:-auto}. No changes made."
  exit 0
fi
[[ "${EUID}" == 0 ]] || die 'Run as root: sudo bash install.sh'
[[ "$(uname -s)" == Linux ]] || die 'Linux required'
[[ "$(uname -m)" == x86_64 ]] || die 'x86_64 host required for SQL Server 2019 container/ODBC image'
[[ -r /etc/os-release ]] || die 'Cannot determine Linux distribution'
command -v python3 >/dev/null || die 'Python 3 is required on the installation host'
. /etc/os-release
case "${ID}:${VERSION_ID}" in
  ubuntu:22.04|ubuntu:24.04|debian:12) ;;
  *) die "Unsupported host ${ID}:${VERSION_ID}; supported Ubuntu 22.04/24.04 or Debian 12";;
esac
command -v flock >/dev/null || die 'flock missing (install util-linux)'
exec 9>"$LOCK_FILE"
flock -n 9 || die 'Another installation is running'

ensure_docker(){
  if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then return; fi
  [[ "$INSTALL_DOCKER" == true ]] || die 'Docker Engine and Compose plugin required'
  log 'Installing Docker Engine and Compose plugin from the official apt repository'
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl --fail --silent --show-error --location "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/${ID}
Suites: ${VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  docker compose version >/dev/null
}

mkdir -p "$CONFIG_DIR"
chmod 0700 "$CONFIG_DIR"
if [[ -f "$CONFIG_DIR/mode" ]]; then
  CURRENT_MODE="$(value_from_env INSTALL_MODE "$CONFIG_DIR/mode")"
  [[ "$CURRENT_MODE" == 'external' || "$CURRENT_MODE" == 'local-express' ]] || die 'Existing mode file invalid'
  [[ -z "$MODE" || "$MODE" == "$CURRENT_MODE" ]] || die 'Changing database mode is a controlled migration, not an in-place installation'
  MODE="$CURRENT_MODE"
else
  MODE="${MODE:-local-express}"
  if [[ "$MODE" == 'local-express' ]]; then
    log 'Generating isolated SQL Express development credentials (first installation only)'
    python3 "$ROOT/scripts/init_install_config.py" "$CONFIG_DIR"
  else
    [[ -f "$RUNTIME_SOURCE" && -f "$MIGRATION_SOURCE" ]] || die 'External mode requires --runtime-file and --migration-file'
    install -m 0600 "$RUNTIME_SOURCE" "$CONFIG_DIR/runtime.env"
    install -m 0600 "$MIGRATION_SOURCE" "$CONFIG_DIR/migration.env"
    : > "$CONFIG_DIR/sqlserver.env"
    : > "$CONFIG_DIR/setup.env"
    printf 'INSTALL_MODE=external\n' > "$CONFIG_DIR/mode"
    chmod 600 "$CONFIG_DIR"/*
  fi
fi
for f in runtime.env migration.env sqlserver.env setup.env mode; do
  [[ -f "$CONFIG_DIR/$f" ]] || die "Incomplete config: missing $CONFIG_DIR/$f"
  chmod 0600 "$CONFIG_DIR/$f"
done
runtime="$CONFIG_DIR/runtime.env"; migration="$CONFIG_DIR/migration.env"
[[ -n "$(value_from_env DB_HOST "$runtime")" && -n "$(value_from_env DB_USER "$runtime")" && -n "$(value_from_env DB_PASSWORD "$runtime")" ]] || die 'Incomplete runtime DB settings'
[[ -n "$(value_from_env DB_HOST "$migration")" && -n "$(value_from_env DB_USER "$migration")" && -n "$(value_from_env DB_PASSWORD "$migration")" ]] || die 'Incomplete separate migration DB account'
[[ "$(value_from_env DB_NAME "$runtime")" == "$(value_from_env DB_NAME "$migration")" ]] || die 'Runtime/migration database names differ'
if [[ "$MODE" == external ]]; then
  [[ "$(value_from_env APP_ENV "$runtime")" == production ]] || die 'External runtime must use APP_ENV=production'
  [[ "$(value_from_env DB_AG_ENABLED "$runtime")" == true ]] || die 'External mode requires DB_AG_ENABLED=true (AG listener)'
  [[ "$(value_from_env DB_TRUST_SERVER_CERT "$runtime")" != true ]] || die 'Production SQL certificate validation cannot be disabled'
  for key in AUTH_ISSUER AUTH_AUDIENCE AUTH_JWKS_URL ADMIN_OIDC_CLIENT_ID ADMIN_OIDC_AUTHORIZATION_ENDPOINT ADMIN_OIDC_TOKEN_ENDPOINT ADMIN_REDIRECT_URI TRUSTED_PROXY_IPS; do
    [[ -n "$(value_from_env "$key" "$runtime")" ]] || die "External production config is missing $key"
  done
  [[ -n "$BACKUP_REFERENCE" ]] || die 'External SQL/AG install or upgrade requires --backup-reference of DBA-verified restore point'
else
  [[ "$(value_from_env APP_ENV "$runtime")" == development ]] || die 'Local Express runtime must use APP_ENV=development'
  [[ "$(value_from_env DB_HOST "$runtime")" == 'sqlserver,1433' ]] || die 'Local Express runtime must connect to isolated compose service'
  [[ "$(value_from_env DB_AG_ENABLED "$runtime")" == false ]] || die 'Local Express cannot join an AG'
  available_mem_kib=$(awk '/MemTotal:/ {print $2}' /proc/meminfo)
  (( available_mem_kib >= 3600000 )) || die 'Local SQL Server 2019 container requires at least 4 GB RAM (use external SQL mode on smaller VMs)'
fi

ensure_docker
if [[ ! -f "$STATE_FILE" ]] && command -v ss >/dev/null; then
  if ss -ltn "sport = :$APP_PORT" 2>/dev/null | grep -qE '^LISTEN'; then
    die "Port $APP_PORT is already listening but this install has no managed deployment record; review prior application deployment before proceeding"
  fi
fi
# Never copy secrets into the repository; Compose reads only root-protected /etc files.
if [[ -f "$STATE_FILE" ]]; then OLD_TAG="$(value_from_env JCF_IMAGE_TAG "$STATE_FILE")"; fi
if command -v git >/dev/null && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  tag="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
  if ! git -C "$ROOT" diff --quiet || ! git -C "$ROOT" diff --cached --quiet; then
    die 'Uncommitted tracked changes: commit and pull the verified release before installing'
  fi
else
  tag="$(sha256sum "$ROOT/Dockerfile" "$ROOT/pyproject.toml" "$ROOT/install.sh" | sha256sum | cut -c1-12)"
fi
CANDIDATE="$(mktemp "$CONFIG_DIR/deployment.candidate.XXXXXXXX")"
chmod 0600 "$CANDIDATE"
cat > "$CANDIDATE" <<EOF
JCF_IMAGE_TAG=$tag
JCF_RUNTIME_ENV=$runtime
JCF_MIGRATE_ENV=$migration
JCF_SQL_ENV=$CONFIG_DIR/sqlserver.env
JCF_SETUP_ENV=$CONFIG_DIR/setup.env
JCF_APP_PORT=$APP_PORT
EOF
compose(){ docker compose --project-name jcf-enterprise --env-file "$CANDIDATE" -f "$COMPOSE_FILE" "$@"; }
rollback(){
  if [[ "$STARTED_DEPLOY" == true && -n "$OLD_TAG" && -f "$STATE_FILE" ]]; then
    log 'Attempting application-only rollback to previous image (database schema is NOT downgraded)'
    docker compose --project-name jcf-enterprise --env-file "$STATE_FILE" -f "$COMPOSE_FILE" up -d --no-build --no-deps registry || true
  fi
}
cleanup(){
  local status=$?
  if ((status!=0)); then rollback; log 'Installation failed. Existing configuration and SQL data were preserved.' >&2; fi
  [[ -z "$CANDIDATE" ]] || rm -f "$CANDIDATE"
  exit "$status"
}
trap cleanup EXIT

log "Deploying release $tag in $MODE mode"
compose build registry
if [[ "$MODE" == local-express ]]; then
  compose --profile local-db up -d --no-build sqlserver
  compose --profile maintenance run --rm --no-deps bootstrap scripts/db_admin.py wait
  compose --profile maintenance run --rm --no-deps bootstrap scripts/db_admin.py provision
fi
compose --profile maintenance run --rm --no-deps migrate scripts/preflight_migration.py
# Quiesce writes during the backup and upgrade. No schema migration occurs in the web process.
if [[ -n "$OLD_TAG" ]]; then compose stop registry || true; fi
STARTED_DEPLOY=true
if [[ "$MODE" == local-express ]]; then
  log 'Creating and verifying a native SQL Server database backup before schema migration'
  compose --profile maintenance run --rm --no-deps bootstrap scripts/db_admin.py backup
else
  log "DBA restore-point evidence provided: $BACKUP_REFERENCE (not independently verifiable by this script)"
fi
log 'Applying forward-only Alembic migrations using the separate migration account'
compose --profile maintenance run --rm --no-deps migrate -m alembic upgrade head
log 'Starting updated application and checking database readiness'
compose up -d --no-build --no-deps registry
ready=false
for _ in $(seq 1 60); do
  container_id="$(compose ps -q registry)"
  if [[ -n "$container_id" ]]; then
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' "$container_id" 2>/dev/null || echo missing)"
    if [[ "$health" == healthy ]]; then ready=true; break; fi
    if [[ "$health" == unhealthy ]]; then break; fi
  fi
  sleep 3
done
[[ "$ready" == true ]] || { compose logs --tail=60 registry >&2 || true; die 'Application failed readiness; old app image will be restored if available'; }
install -m 0600 "$CANDIDATE" "$STATE_FILE"
STARTED_DEPLOY=false
log "SUCCESS — version $tag healthy on 127.0.0.1:$APP_PORT (publish only behind trusted HTTPS reverse proxy)."
if [[ "$MODE" == local-express && -z "$(value_from_env AUTH_ISSUER "$runtime")" ]]; then
  log 'IMPORTANT: OIDC has not been configured. Protected APIs/admin remain inaccessible until JCF identity settings are configured.'
fi
log 'Credentials and database retained on subsequent executions. See docs/INSTALLATION.md for operational checks.'

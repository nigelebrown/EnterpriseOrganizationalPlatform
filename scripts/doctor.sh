#!/usr/bin/env bash
# JCF-EMP CentOS 9 deployment inspector and safe resume helper.
set -Eeo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
CFG=/etc/jcf-registry
STATE="$CFG/deployment.env"
BRANCH=feat/initial-modular-platform
MODE="$1"
[ -n "$MODE" ] || MODE=--check
case "$MODE" in
 --help|-h) echo 'Usage: bash scripts/doctor.sh --check | --repair'; exit 0 ;;
 --check|--repair) ;;
 *) echo 'Usage: bash scripts/doctor.sh --check | --repair' >&2; exit 64 ;;
esac
say(){ echo "[JCF-EMP doctor] $*"; }
ok(){ say "OK: $*"; }
bad(){ say "INCOMPLETE: $*"; errors=$((errors+1)); }
value(){ sed -n "s/^$1=//p" "$2" | tail -1; }
# Do not compete with a running installer.
if [ "$EUID" -eq 0 ] && ! (exec 9>/var/lock/jcf-registry-install.lock; flock -n 9); then
 say 'An installation is still running; do not run a second installer.'
 exit 3
fi
check(){
 errors=0
 local mode='' tag='' current='' port=8080 sql='' app='' health='' f
 say 'Checking host tools, Docker, database, app and test seed'
 [ -f "$ROOT/install.sh" ] && [ -f "$ROOT/deploy/compose.yaml" ] && ok 'Installer files available' || bad 'Installer files missing'
 for f in git python3 flock curl; do
  command -v "$f" >/dev/null 2>&1 && ok "$f installed" || bad "$f missing"
 done
 if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  current="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
  ok "Source revision $current"
 else bad 'Git checkout not found'; fi
 if [ -r "$CFG/mode" ]; then mode="$(value INSTALL_MODE "$CFG/mode")"; fi
 [ "$mode" = local-express ] || [ "$mode" = external ] && ok "Database mode $mode" || bad 'Database mode not initialized'
 for f in runtime.env migration.env; do
  [ -s "$CFG/$f" ] && ok "$f exists" || bad "$f missing"
 done
 for f in setup.env sqlserver.env; do
  if [ "$mode" = external ]; then [ -f "$CFG/$f" ] && ok "$f exists" || bad "$f missing"
  else [ -s "$CFG/$f" ] && ok "$f exists" || bad "$f missing"; fi
 done
 if [ -f "$STATE" ]; then
  tag="$(value JCF_IMAGE_TAG "$STATE")"
  local p
  p="$(value JCF_APP_PORT "$STATE")"
  [[ "$p" =~ ^[0-9]+$ ]] && port="$p"
  [ -n "$tag" ] && ok "Installed image $tag" || bad 'Installed image tag missing'
  [ -n "$tag" ] && [ "$tag" = "$current" ] && ok 'Application version current' || bad 'Updated source not installed'
 else bad 'No completed deployment state (partial install)'; fi
 if ! command -v docker >/dev/null; then bad 'Docker missing'; say "RESULT: $errors incomplete"; return; fi
 ok 'Docker CLI installed'
 if ! docker compose version >/dev/null 2>&1; then bad 'Docker Compose missing'; say "RESULT: $errors incomplete"; return; fi
 ok 'Docker Compose installed'
 if ! docker info >/dev/null 2>&1; then bad 'Docker daemon not running'; say "RESULT: $errors incomplete"; return; fi
 ok 'Docker daemon running'
 sql="$(docker ps --filter label=com.docker.compose.project=jcf-enterprise --filter label=com.docker.compose.service=sqlserver --format '{{.ID}}' | head -1)"
 app="$(docker ps --filter label=com.docker.compose.project=jcf-enterprise --filter label=com.docker.compose.service=registry --format '{{.ID}}' | head -1)"
 if [ "$mode" = local-express ]; then [ -n "$sql" ] && ok 'SQL Server container running' || bad 'SQL Server not running'; fi
 if [ -n "$tag" ]; then
  docker image inspect "jcf/enterprise-organizational-platform:$tag" >/dev/null 2>&1 && ok 'Application image exists' || bad 'Application image missing'
 fi
 if [ -z "$app" ]; then bad 'Application container not running'
 else
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' "$app")"
  [ "$health" = healthy ] && ok 'App container healthy' || bad "App health $health"
  curl -fsS --max-time 5 "http://127.0.0.1:$port/health/ready" >/dev/null 2>&1 && ok 'Database health API ready' || bad 'Database health API unavailable'
 fi
 if [ "$mode" = local-express ] && [ -n "$tag" ] && [ -n "$app" ] && [ -n "$sql" ]; then
  docker compose --project-name jcf-enterprise --env-file "$STATE" -f "$ROOT/deploy/compose.yaml" --profile maintenance run --rm --no-deps migrate scripts/seed_test_data.py --verify >/dev/null 2>&1 && ok 'Test data verified' || bad 'Test seeding incomplete'
 fi
 say "RESULT: $errors incomplete checks"
}
if [ "$MODE" = --check ]; then check; [ "$errors" -eq 0 ]; exit $?; fi
[ "$EUID" -eq 0 ] || { say 'Run as root'; exit 1; }
[ -f "$ROOT/install.sh" ] || { say 'Installer missing'; exit 1; }
if [ -r "$CFG/mode" ] && [ "$(value INSTALL_MODE "$CFG/mode")" = external ]; then
 say 'Production AG requires DBA backup reference; use install.sh --backup-reference.'
 exit 4
fi
[ "$(git -C "$ROOT" branch --show-current)" = "$BRANCH" ] || { say 'Unexpected Git branch'; exit 4; }
[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ] || { say 'Uncommitted changes; refusing upgrade'; exit 4; }
git -C "$ROOT" pull --ff-only origin "$BRANCH" || exit 1
check
if [ "$errors" -gt 0 ]; then
 say 'Resuming existing installation without resetting SQL data or credentials'
 bash "$ROOT/install.sh" --mode local-express || exit 1
 check
fi
[ "$errors" -eq 0 ] || { say 'Review remaining incomplete checks'; exit 2; }
say 'SUCCESS: App, SQL Server, and test seed verified.'

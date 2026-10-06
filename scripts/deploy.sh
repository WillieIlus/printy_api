#!/usr/bin/env bash
#
# Printy API - production deploy.
#
# Encodes docs/PRODUCTION_DEPLOYMENT_RUNBOOK.md into one repeatable command so a
# deploy is never retyped from memory. Run it as the app user:
#
#     sudo su - <app-user>
#     ./deploy_printy.sh
#
# Every step is fail-fast. On failure the script prints the failing step, the
# relevant service logs, and the command to roll back to the previous commit.
#
# Overridable via environment:
#   PRINTY_APP_DIR      checkout to deploy          (default: ~/printy_api)
#   PRINTY_BRANCH       branch to deploy            (default: main)
#   PRINTY_HEALTH_URL   post-restart health probe   (default: https://api.printy.ke/api/public/products/)
#   PRINTY_RELOAD_NGINX 1 to also reload nginx      (default: skip; see note below)
#   PRINTY_MIGRATE_PLAN 1 to stop after listing pending migrations, changing nothing
#   PRINTY_SKIP_PIP     1 to skip dependency install (offline / fast redeploys)
#   PRINTY_DEPLOY_LOG   log file                    (default: <app dir>/deploy.log)

set -Eeuo pipefail

APP_DIR="${PRINTY_APP_DIR:-$HOME/printy_api}"
BRANCH="${PRINTY_BRANCH:-main}"
HEALTH_URL="${PRINTY_HEALTH_URL:-https://api.printy.ke/api/public/products/}"
RELOAD_NGINX="${PRINTY_RELOAD_NGINX:-0}"
MIGRATE_PLAN="${PRINTY_MIGRATE_PLAN:-0}"
SKIP_PIP="${PRINTY_SKIP_PIP:-0}"
GUNICORN_UNIT="${PRINTY_GUNICORN_UNIT:-gunicorn}"
STARTED_AT="$(date +%s)"
STEP="startup"
PREV_SHA=""

fail()  { printf '\n\033[1;31m!!! %s\033[0m\n' "$1" >&2; }

# Refuse to do anything if the checkout is not where we expect it. This runs
# before logging is set up, because the log lives inside the checkout.
[ -d "$APP_DIR" ] || { fail "app dir not found: $APP_DIR (set PRINTY_APP_DIR)"; exit 1; }

LOG_FILE="${PRINTY_DEPLOY_LOG:-$APP_DIR/deploy.log}"
exec > >(tee -a "$LOG_FILE") 2>&1

step() { STEP="$1"; printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
info() { printf '    %s\n' "$1"; }

# Two concurrent deploys would race on git pull, migrate and the gunicorn
# restart. Serialise them. flock ships with util-linux, so it is present on
# Ubuntu/Debian; if it is somehow missing, warn and continue rather than block.
LOCK_FILE="${PRINTY_DEPLOY_LOCK:-/tmp/printy_deploy.lock}"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    fail "another deploy is already running (lock: $LOCK_FILE)"
    exit 1
  fi
else
  printf '    WARNING: flock unavailable, cannot guard against concurrent deploys\n'
fi

on_error() {
  # $1 when called explicitly, otherwise the exit status inherited from ERR.
  local code="${1:-$?}"
  fail "deploy FAILED at step: ${STEP} (exit ${code})"
  printf '\n--- last 40 lines: journalctl -u %s ---\n' "$GUNICORN_UNIT"
  sudo journalctl -u "$GUNICORN_UNIT" -n 40 --no-pager 2>/dev/null || true
  printf '\n--- last 40 lines: /var/log/nginx/error.log ---\n'
  sudo tail -n 40 /var/log/nginx/error.log 2>/dev/null || true
  if [ -n "$PREV_SHA" ]; then
    printf '\nRollback (code only - do NOT reverse the schema blindly):\n'
    printf '    git -C %s checkout %s\n' "$APP_DIR" "$PREV_SHA"
    printf '    source %s/env/bin/activate && python manage.py collectstatic --noinput\n' "$APP_DIR"
    printf '    sudo systemctl restart %s\n' "$GUNICORN_UNIT"
    printf 'Review the applied migrations first - see the runbook "Rollback notes".\n'
  fi
  exit "$code"
}
trap on_error ERR

# ------------------------------------------------------------------ guards --

cd "$APP_DIR"

# Cache sudo once so restarts late in the deploy do not block on a password
# prompt halfway through, after migrations have already run.
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
  sudo -v || { fail "sudo unavailable; cannot restart services"; exit 1; }
fi

[ -f .env ] || { fail ".env missing in $APP_DIR - refusing to deploy"; exit 1; }
[ -s .env ] || { fail ".env is empty in $APP_DIR - refusing to deploy"; exit 1; }
[ -f env/bin/activate ] || { fail "virtualenv missing: $APP_DIR/env"; exit 1; }

# shellcheck disable=SC1091
source env/bin/activate
PY=python

PREV_SHA="$(git rev-parse HEAD)"
info "current commit: ${PREV_SHA}"

# -------------------------------------------------------------------- pull ---

step "Fetching ${BRANCH}"
git fetch --quiet origin "$BRANCH"

if git diff --quiet HEAD "origin/$BRANCH" 2>/dev/null; then
  info "already up to date"
else
  info "commits incoming:"
  git log --oneline --no-decorate "HEAD..origin/$BRANCH" | sed 's/^/      /'
fi

# --ff-only: never let a merge commit be created on the production box.
step "Updating to origin/${BRANCH}"
git pull --ff-only origin "$BRANCH"
NEW_SHA="$(git rev-parse HEAD)"
if [ "$NEW_SHA" != "$PREV_SHA" ]; then
  info "now deploying: ${NEW_SHA}"
fi

# ------------------------------------------------------------- dependencies --

if [ "$SKIP_PIP" = "1" ]; then
  step "Skipping dependency install (PRINTY_SKIP_PIP=1)"
else
  # Omitted from the naive version of this script: a deploy that pulls a commit
  # adding a dependency crashes on boot if this is not run.
  step "Installing dependencies"
  "$PY" -m pip install --quiet --disable-pip-version-check -r requirements.txt
  "$PY" -m pip check
fi

# ------------------------------------------------------------------ checks ---

# Only CRITICAL/ERROR fail the command (--fail-level defaults to ERROR), so the
# stock security warnings do not block a deploy. Do NOT add --fail-level WARNING:
# it would make every deploy abort on advisory warnings.
step "System checks (errors block, warnings do not)"
"$PY" manage.py check --deploy

# --------------------------------------------------------------- migrations --

# Backward-incompatible migrations need maintenance mode; forward-compatible ones
# are safe to apply while the old code still serves traffic.
step "Migrations"
# Distinguish "nothing to do" from "could not read the plan". Swallowing a failure
# here would report a database outage as "no pending migrations" and then restart
# gunicorn for nothing.
if ! PENDING="$("$PY" manage.py migrate --plan 2>&1)"; then
  fail "could not read the migration plan - is the database reachable?"
  printf '%s\n' "$PENDING" | sed 's/^/      /'
  exit 1
fi
if ! printf '%s\n' "$PENDING" | grep -qE '^\[ \]'; then
  info "no pending migrations"
elif [ "$MIGRATE_PLAN" = "1" ]; then
  info "PRINTY_MIGRATE_PLAN=1 - stopping before applying:"
  printf '%s\n' "$PENDING" | sed 's/^/      /'
  info "nothing was changed"
  exit 0
else
  printf '%s\n' "$PENDING" | grep -E '^\[ \]' | sed 's/^/      /'
  "$PY" manage.py migrate --noinput
fi

# --------------------------------------------------------------- site / i18n --

# Idempotent. Required after SITE_DOMAIN changes so confirmation and password
# reset links render against the live frontend.
step "Django Sites framework"
"$PY" manage.py configure_site

step "Collecting static files"
"$PY" manage.py collectstatic --noinput

# One-time setup NOT done here, by design: create_printy_manager_user and
# create_printy_house_broker_user, then pin PRINTY_MANAGER_USER_ID /
# PRINTY_HOUSE_BROKER_USER_ID in .env so those accounts stay stable across
# deploys. See the runbook.

# ----------------------------------------------------------------- restart ---

step "Restarting ${GUNICORN_UNIT}"
sudo systemctl restart "$GUNICORN_UNIT"

# nginx only needs a reload when its config changes; restarting it on every code
# deploy is needless downtime. Opt in with PRINTY_RELOAD_NGINX=1.
if [ "$RELOAD_NGINX" = "1" ]; then
  step "Reloading nginx"
  sudo systemctl reload nginx
fi

# ------------------------------------------------------------------- verify --

# systemctl status exits 3 for an inactive unit and prints no useful cause, and
# as the final command it would mask the real exit code. Probe the real endpoint
# instead: it exercises routing, the app, and the database in one request.
step "Verifying ${HEALTH_URL}"
ok=0
code="000"
for _ in {1..30}; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$HEALTH_URL" 2>/dev/null)" || code="000"
  if [ "$code" = "200" ]; then
    ok=1
    break
  fi
  sleep 2
done

if [ "$ok" != "1" ]; then
  STEP="health check (last status ${code})"
  on_error 1
fi

# ----------------------------------------------------------------- summary ---

ELAPSED=$(( $(date +%s) - STARTED_AT ))
STEP="summary"
# The health probe above is the real gate. Report the unit state without letting
# it fail the script, so a successful deploy is never reported as a failure.
SVC="$(systemctl is-active "$GUNICORN_UNIT" 2>/dev/null || true)"
printf '\n\033[1;32m=== deployed OK in %ss ===\033[0m\n' "$ELAPSED"
printf 'commit : %s\n' "$(git rev-parse --short HEAD)"
printf 'service: %s\n' "${SVC:-unknown}"
if [ "$SVC" != "active" ]; then
  printf 'WARNING: %s is not active but the health probe passed - check the unit name\n' "$GUNICORN_UNIT"
fi
printf 'log    : %s\n' "$LOG_FILE"
printf '\nRecent service log:\n'
sudo journalctl -u "$GUNICORN_UNIT" -n 15 --no-pager 2>/dev/null || true

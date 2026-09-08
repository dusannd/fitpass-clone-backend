#!/usr/bin/env bash
#
# Restore a backup produced by scripts/backup.sh.
#
# THIS IS DESTRUCTIVE. It drops and recreates every table in the target database
# before loading the dump, so anything currently live is gone. That is the point
# - a half-merged restore is worse than either state - but it means the script
# refuses to do anything until you type the database name back to it.
#
# USAGE (from the backend repo root, with the stack running)
#     ./scripts/restore.sh backups/db-2026-08-30_031500.dump
#     ./scripts/restore.sh backups/db-....dump backups/avatars-....tar.gz
#
# RESTORE DRILL: do this once, on purpose, before you need it. A backup nobody
# has ever restored is a rumour. The cheapest drill is to restore yesterday's
# dump onto a throwaway copy of the stack and log in.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"

DB_FILE="${1:-}"
AVATAR_FILE="${2:-}"

if [ -z "$DB_FILE" ]; then
    echo "usage: $0 <db-*.dump> [avatars-*.tar.gz]" >&2
    exit 1
fi
[ -f "$DB_FILE" ] || { echo "error: no such file: $DB_FILE" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "error: $ENV_FILE not found" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-fitpass_db}"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

# --- 1. MAKE THE USER SAY IT OUT LOUD ----------------------------------------
echo
echo "About to REPLACE the contents of database '$POSTGRES_DB' with:"
echo "    $DB_FILE"
[ -n "$AVATAR_FILE" ] && echo "and replace the avatar volume with:"
[ -n "$AVATAR_FILE" ] && echo "    $AVATAR_FILE"
echo
echo "Everything currently in there will be destroyed."
printf "Type the database name (%s) to continue: " "$POSTGRES_DB"
read -r CONFIRM
[ "$CONFIRM" = "$POSTGRES_DB" ] || { echo "aborted."; exit 1; }

# --- 2. TAKE THE APP DOWN FIRST ----------------------------------------------
# The backend holds a connection pool and would keep writing into a database
# that is being rebuilt underneath it. It also blocks the DROPs.
echo "==> stopping backend"
"${COMPOSE[@]}" stop backend frontend >/dev/null

# --- 3. THE DATABASE ---------------------------------------------------------
# --clean --if-exists drops each object before recreating it, so this works on a
# populated database as well as an empty one. Without --if-exists the first DROP
# of something absent aborts the whole restore.
#
# pg_restore reports non-fatal notices as errors on a clean database (dropping
# things that were never there), so its exit code is checked loosely and the
# verification below is what actually decides success.
echo "==> restoring database"
"${COMPOSE[@]}" exec -T db \
    pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner \
    < "$DB_FILE" || echo "   (pg_restore reported warnings - checking the result)"

echo "==> verifying"
"${COMPOSE[@]}" exec -T db \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) AS users FROM users;"

# --- 4. THE AVATARS ----------------------------------------------------------
if [ -n "$AVATAR_FILE" ]; then
    [ -f "$AVATAR_FILE" ] || { echo "error: no such file: $AVATAR_FILE" >&2; exit 1; }

    AVATAR_VOLUME="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/app/static"}}{{.Name}}{{end}}{{end}}' fitpass_backend_prod 2>/dev/null || true)"
    AVATAR_VOLUME="${AVATAR_VOLUME:-fitpass-prod_avatar_data}"

    echo "==> restoring avatars into $AVATAR_VOLUME"
    # Emptied first, or files deleted since the backup would come back to life.
    docker run --rm -i -v "$AVATAR_VOLUME:/data" alpine \
        sh -c 'rm -rf /data/* /data/..?* 2>/dev/null; tar -xzf - -C /data' < "$AVATAR_FILE"
fi

# --- 5. BACK UP ---------------------------------------------------------------
echo "==> starting the stack again"
"${COMPOSE[@]}" start backend frontend

echo
echo "done. Check the app, then confirm you can still log in."

#!/usr/bin/env bash
#
# Back up everything that cannot be rebuilt from git.
#
# WHAT IS AT RISK
# All production state lives in two Docker named volumes: `postgres_data` (every
# user, subscription, entry log and workout) and `avatar_data` (uploaded profile
# pictures). `docker compose down` keeps them. `docker compose down -v` deletes
# them permanently, with no confirmation prompt and no undo, and so does moving
# the stack to a new host. This script is the only thing standing between a
# mistyped flag and starting the gym over from zero.
#
# USAGE (from the backend repo root, with the stack running)
#     ./scripts/backup.sh                # writes into ./backups
#     BACKUP_DIR=/mnt/backups ./scripts/backup.sh
#     RETENTION_DAYS=30 ./scripts/backup.sh
#
# Put it on a cron so it is not a thing you have to remember:
#     0 3 * * *  cd /home/opc/fitpass-clone && ./scripts/backup.sh >> backup.log 2>&1
#
# A backup you have never restored is a rumour, not a backup - see restore.sh.

set -euo pipefail

# --- 1. WHERE WE ARE ---------------------------------------------------------
# Resolve paths from the script's own location, so cron (which starts in $HOME)
# behaves exactly like running it by hand from the repo.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

[ -f "$ENV_FILE" ] || { echo "error: $ENV_FILE not found (run this from the backend repo)" >&2; exit 1; }

# --- 2. CREDENTIALS ----------------------------------------------------------
# Read from .env.prod rather than duplicated here, so a rotated password cannot
# leave the backup silently failing against the old one. `set -a` exports every
# variable the file defines; the subshell-free form keeps them for pg_dump below.
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-fitpass_db}"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

mkdir -p "$BACKUP_DIR"

# A database dump holds every email address, password hash and entry log in the
# gym. The default location is inside the repo because that is where you will
# look for it, so make the directory ignore itself rather than editing the
# repository's .gitignore - one `git add -A` on a tired evening is all it would
# take to publish the lot.
if [ ! -e "$BACKUP_DIR/.gitignore" ]; then
    printf '*
' > "$BACKUP_DIR/.gitignore"
fi

STAMP="$(date +%Y-%m-%d_%H%M%S)"

# --- 3. THE DATABASE ---------------------------------------------------------
# -Fc is Postgres' custom format: compressed, and restorable selectively by
# pg_restore. A plain .sql dump would be twice the size and all-or-nothing.
#
# `exec -T` disables TTY allocation. Without it the dump is fine interactively
# and produces a corrupt file under cron, because Docker injects carriage
# returns into the stream - a failure that only shows up on restore day.
DB_FILE="$BACKUP_DIR/db-$STAMP.dump"
echo "==> dumping database '$POSTGRES_DB'"
"${COMPOSE[@]}" exec -T db \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$DB_FILE"

# A pg_dump that fails mid-stream still leaves a file behind, so check that the
# result is actually readable rather than trusting the exit code alone.
if ! docker run --rm -i postgres:15-alpine pg_restore --list - < "$DB_FILE" > /dev/null 2>&1; then
    echo "error: the dump at $DB_FILE is not a valid archive - NOT deleting old backups" >&2
    exit 1
fi

# --- 4. THE AVATARS ----------------------------------------------------------
# These live in a named volume, not on the host filesystem, so there is nothing
# to tar directly. A throwaway container mounts the volume read-only and streams
# the archive to stdout. Read-only matters: a backup must never be able to
# damage the thing it is backing up.
AVATAR_FILE="$BACKUP_DIR/avatars-$STAMP.tar.gz"

# Ask the running backend which volume is actually mounted at the avatar path,
# rather than hardcoding a name. A renamed compose project would otherwise
# silently produce an empty archive that nobody notices until restore day.
AVATAR_VOLUME="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/app/static"}}{{.Name}}{{end}}{{end}}' fitpass_backend_prod 2>/dev/null || true)"
AVATAR_VOLUME="${AVATAR_VOLUME:-fitpass-prod_avatar_data}"

echo "==> archiving avatars from volume $AVATAR_VOLUME"
docker run --rm -v "$AVATAR_VOLUME:/data:ro" alpine \
    tar -czf - -C /data . > "$AVATAR_FILE"

# --- 5. PRUNE ----------------------------------------------------------------
# Only reached when the dump above verified, so a run of broken backups can
# never quietly age out the last good one.
if [ "$RETENTION_DAYS" -gt 0 ]; then
    echo "==> removing backups older than $RETENTION_DAYS days"
    find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'db-*.dump' -o -name 'avatars-*.tar.gz' \) \
        -mtime +"$RETENTION_DAYS" -print -delete
fi

echo
echo "done:"
ls -lh "$DB_FILE" "$AVATAR_FILE"
echo
echo "These files are only safe once they are somewhere OTHER than this server."
echo "Copy them off, e.g.:  scp $BACKUP_DIR/*-$STAMP.* you@yourmachine:~/fitpass-backups/"

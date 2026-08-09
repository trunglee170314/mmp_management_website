#!/bin/sh
set -eu
if [ "$#" != 2 ] || [ "$2" != --yes ]; then
    echo 'Usage: sh deploy/restore.sh BUNDLE_ID_OR_FOLDER --yes' >&2
    echo 'Replaces database and media. Writers remain stopped afterward.' >&2
    exit 2
fi
source=$1
if [ -d "$source" ]; then
    source=$(CDPATH= cd -- "$source" && pwd)
fi
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
docker compose up -d --wait db backup_verify
if [ -d "$source" ]; then
    id=$(basename "$source")
    docker compose run --rm --no-deps -v "$source:/import:ro" backup import "$id"
else
    id=$source
fi
# Validate BEFORE any interruption or destructive operation.
docker compose run --rm --no-deps backup verify "$id"
docker compose stop proxy web maintenance backup
if docker compose run --rm --no-deps restore restore "$id"; then
    echo 'Restore complete. Select matching application version, then run: docker compose up -d --build'
    echo 'WARNING: starting web applies migrations. Do not start an incompatible application version.'
else
    echo 'Restore failed; writers remain STOPPED. Inspect logs and restore the printed recovery bundle.' >&2
    exit 1
fi

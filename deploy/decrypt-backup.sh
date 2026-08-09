#!/bin/sh
set -eu
if [ "$#" != 3 ]; then
    echo 'Usage: sh deploy/decrypt-backup.sh ARCHIVE.tar.gz.age AGE_IDENTITY EXISTING_OUTPUT_FOLDER' >&2
    exit 2
fi
[ -f "$1" ] && [ -f "$1.sha256" ] && [ -f "$2" ] && [ -d "$3" ] || { echo 'Input, checksum, identity or output folder missing' >&2; exit 1; }
archive_dir=$(CDPATH= cd -- "$(dirname "$1")" && pwd)
archive_name=$(basename "$1")
identity_dir=$(CDPATH= cd -- "$(dirname "$2")" && pwd)
identity="$identity_dir/$(basename "$2")"
output=$(CDPATH= cd -- "$3" && pwd)
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
docker build -t mmp-backup-tools:local -f deploy/backup.Dockerfile .
docker run --rm --network none --user "$(id -u):$(id -g)" \
    --mount "type=bind,src=$archive_dir,dst=/input,readonly" \
    --mount "type=bind,src=$identity,dst=/run/secrets/backup_age_identity,readonly" \
    --mount "type=bind,src=$output,dst=/output" \
    --entrypoint /bin/sh mmp-backup-tools:local /opt/backup-decrypt.sh "/input/$archive_name"

#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
case "${1:-}" in ''|--quiesce) ;; *) echo 'Usage: sh deploy/backup.sh [--quiesce]' >&2; exit 2;; esac
docker compose up -d --wait db backup_verify
running=
resume() {
    # Only restart services that were running before a quiesced backup.
    [ -z "$running" ] || docker compose start $running
}
if [ "${1:-}" = --quiesce ]; then
    running=$(docker compose ps --status running --services | grep -E '^(web|proxy|maintenance|backup)$' || true)
    trap resume EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    [ -z "$running" ] || docker compose stop $running
    docker compose run --rm --no-deps -e BACKUP_CAPTURE_MODE=quiesced backup once
else
    docker compose run --rm --no-deps backup once
fi

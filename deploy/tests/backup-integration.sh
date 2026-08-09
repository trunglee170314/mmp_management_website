#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
# Generated project prefix guarantees cleanup cannot target the production project.
project="mmp_backup_test_$(date +%s)_$$"
dc() { docker compose --env-file /dev/null -p "$project" -f deploy/tests/compose.yml "$@"; }
readonly_dc() { docker compose --env-file /dev/null -p "$project" -f deploy/tests/compose.yml -f deploy/tests/export-readonly.compose.yml "$@"; }
cleanup() { dc down --volumes --remove-orphans >/dev/null; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
expect_failure() {
    if "$@"; then echo 'Expected failure, but command succeeded' >&2; exit 1; fi
}
dc up -d --wait db backup_verify
dc exec -T db psql -U fixture -d fixture -c "CREATE TABLE sample (id integer, value text); INSERT INTO sample VALUES (1, 'original');"
dc exec -T db sh -ec 'mkdir -p /fixture-media/nested; printf original > /fixture-media/nested/file.txt; printf hidden > /fixture-media/.hidden'
dc run --rm -T -e BACKUP_EXPORT_ENABLED=1 backup once
id=$(dc run --rm -T backup list | tail -n 1 | sed 's|.*/||')
dc run --rm -T backup verify "$id"
dc run --rm -T --entrypoint sh backup -ec 'cmp "/backups/$1/database.dump" "/exports/$1/database.dump"; cmp "/backups/$1/media.tar.gz" "/exports/$1/media.tar.gz"' sh "$id"
dc exec -T db psql -U fixture -d fixture -c "UPDATE sample SET value='changed'; CREATE TABLE later_migration(id integer);"
dc exec -T db sh -ec 'printf changed > /fixture-media/nested/file.txt; printf extra > /fixture-media/extra.txt'
dc run --rm -T restore restore "$id"
[ "$(dc exec -T db psql -U fixture -d fixture -Atc 'SELECT value FROM sample')" = original ]
[ "$(dc exec -T db psql -U fixture -d fixture -Atc "SELECT count(*) FROM pg_tables WHERE tablename='later_migration'")" = 0 ]
dc exec -T db sh -ec 'test "$(cat /fixture-media/nested/file.txt)" = original; test "$(cat /fixture-media/.hidden)" = hidden; test ! -e /fixture-media/extra.txt'
# Checked moves must fail nonzero and leave both staged media and current files.
expect_failure dc run --rm -T --entrypoint sh restore -ec 'mkdir /tmp/fault; cp /opt/fail-media-move.sh /tmp/fault/mv; chmod +x /tmp/fault/mv; PATH="/tmp/fault:$PATH" sh /opt/backup-worker.sh restore "$1"' sh "$id"
dc exec -T db sh -ec 'test "$(cat /fixture-media/nested/file.txt)" = original; test -n "$(find /fixture-media -maxdepth 1 -type d -name .mmp-restore.\*)"'
# Only remove generated staging in this disposable fixture, never production media.
dc exec -T db sh -ec 'for folder in /fixture-media/.mmp-restore.*; do test -d "$folder" || continue; rm -rf -- "$folder"; done'
# Retention deletes old completed bundles, but protects the target during recovery.
dc run --rm -T --entrypoint sh backup -ec 'touch -d "2000-01-01" "/backups/$1"' sh "$id"
dc run --rm -T restore restore "$id"
dc run --rm -T backup verify "$id"
expect_failure dc run --rm -T backup verify '../../etc'
# Checksum corruption must fail before any mutation.
dc run --rm -T --entrypoint sh backup -ec 'printf corruption >> "/backups/$1/database.dump"' sh "$id"
expect_failure dc run --rm -T restore restore "$id"
[ "$(dc exec -T db psql -U fixture -d fixture -Atc 'SELECT value FROM sample')" = original ]
# A structurally invalid dump with matching checksums must fail full validation too.
dc run --rm -T --entrypoint sh backup -ec 'cd "/backups/$1"; printf invalid > database.dump; sha256sum database.dump media.tar.gz manifest.txt > checksums.sha256' sh "$id"
expect_failure dc run --rm -T backup verify "$id"
# Recover the exported original into an empty local slot; refuse differing IDs.
expect_failure dc run --rm -T --entrypoint sh backup -ec 'mkdir /import; cp "/exports/$1/"* /import/; sh /opt/backup-worker.sh import "$1"' sh "$id"
dc run --rm -T --entrypoint sh backup -ec 'mkdir /import; cp "/exports/$1/"* /import/; rm -rf -- "/backups/$1"; sh /opt/backup-worker.sh import "$1"' sh "$id"
dc run --rm -T backup verify "$id"
# A failed scheduled attempt must not publish a bundle or health success.
expect_failure dc run --rm -T -e BACKUP_INTERVAL_SECONDS=00 backup health
# Export failure is visible and cannot advance success or prune a good local copy.
success=$(dc run --rm -T --entrypoint cat backup /backups/.last-success)
expect_failure readonly_dc run --rm -T -e BACKUP_EXPORT_ENABLED=1 backup once
[ "$(dc run --rm -T --entrypoint cat backup /backups/.last-success)" = "$success" ]
expect_failure dc run --rm -T backup health
dc run --rm -T backup verify "$id"
before=$(dc run --rm -T backup list | wc -l)
dc run -d --name "${project}_watch" -e POSTGRES_DB=does_not_exist backup watch
sleep 3
docker exec "${project}_watch" sh -ec 'test -f /backups/.last-failure; test -z "$(find /backups -maxdepth 1 -name .partial.\*)"'
expect_failure docker exec "${project}_watch" sh /opt/backup-worker.sh health
[ "$(dc run --rm -T backup list | wc -l)" = "$before" ]
docker stop "${project}_watch" >/dev/null
docker rm "${project}_watch" >/dev/null
# Success clears failure, and concurrent manual jobs produce unique valid bundles.
dc run --rm -T backup once &
first=$!
dc run --rm -T backup once &
second=$!
wait "$first"
wait "$second"
dc run --rm -T backup health
[ "$(dc run --rm -T backup list | wc -l)" -eq "$((before + 2))" ]
echo 'PASS: DB/media round-trip, export/import, exact rollback, media failure preservation, retention, corruption rejection, export/scheduler failure, cleanup, locking, health'

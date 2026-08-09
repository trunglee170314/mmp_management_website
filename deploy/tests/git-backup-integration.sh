#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
project="mmp_git_backup_test_$(date +%s)_$$"
host_recovery=
dc() { docker compose --env-file /dev/null -p "$project" -f deploy/tests/compose.yml -f deploy/tests/git.compose.yml "$@"; }
readonly_dc() { docker compose --env-file /dev/null -p "$project" -f deploy/tests/compose.yml -f deploy/tests/git.compose.yml -f deploy/tests/git-export-readonly.compose.yml "$@"; }
cleanup() {
    dc down --volumes --remove-orphans >/dev/null
    [ -z "$host_recovery" ] || rm -rf -- "$host_recovery"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
expect_failure() {
    if "$@"; then echo 'Expected failure, but command succeeded' >&2; exit 1; fi
}
remote_git() { dc exec -T --user git git-server git --git-dir=/remote.git "$@"; }
docker build -t mmp-backup-tools:local -f deploy/backup.Dockerfile .
dc build git-server
dc up -d --wait db backup_verify git-server
dc exec -T db psql -U fixture -d fixture -c "CREATE TABLE sample(value text); INSERT INTO sample VALUES ('git-original');"
dc exec -T db sh -ec 'printf git-media > /fixture-media/file.txt'
dc run --rm -T backup once
id=$(dc run --rm -T backup list | tail -n 1 | sed 's|.*/||')
dc run --rm -T backup health
remote_git ls-tree -r --name-only main
[ "$(remote_git ls-tree -r --name-only main | wc -l)" -eq 3 ]
# Exactly one commit for repeated pushes of the same ID, no re-encryption/overwrite.
dc run --rm -T backup push "$id"
dc run --rm -T backup push "$id"
[ "$(remote_git rev-list --count main)" -eq 1 ]
dc run --rm -T --entrypoint sh backup -ec 'sh /opt/backup-decrypt.sh "/backup-git/repo/archives/$1/$1.tar.gz.age"; cmp "/output/$1/database.dump" "/backups/$1/database.dump"; cmp "/output/$1/media.tar.gz" "/backups/$1/media.tar.gz"; mv "/output/$1" /output/verified' sh "$id"
# Exercise the documented host wrapper, including non-root ownership and offline
# decryption into a host folder. These are generated fixture keys, never real ones.
host_recovery=$(mktemp -d /tmp/mmp-git-recovery.XXXXXX)
dc exec -T --user git git-server git clone /remote.git /tmp/host-recovery
dc cp "git-server:/tmp/host-recovery/archives/$id/$id.tar.gz.age" "$host_recovery/"
dc cp "git-server:/tmp/host-recovery/archives/$id/$id.tar.gz.age.sha256" "$host_recovery/"
dc cp git-server:/run/secrets/backup_age_identity "$host_recovery/identity"
mkdir "$host_recovery/output"
sh deploy/decrypt-backup.sh "$host_recovery/$id.tar.gz.age" "$host_recovery/identity" "$host_recovery/output"
dc run --rm -T -v "$host_recovery/output/$id:/import:ro" -e BACKUP_GIT_ENABLED=0 backup import "$id"
# Simulate GitLab branch permission rejection, keep local backup and pending commit.
success=$(dc run --rm -T --entrypoint cat backup /backups/.last-success)
before=$(dc run --rm -T backup list | wc -l)
dc exec -T git-server touch /remote.git/reject-push
expect_failure dc run --rm -T backup once
[ "$(dc run --rm -T --entrypoint cat backup /backups/.last-success)" = "$success" ]
expect_failure dc run --rm -T backup health
pending_id=$(dc run --rm -T --entrypoint sh backup -ec 'for flag in /backups/mmp-*/.git-pending; do test -f "$flag" || continue; basename "$(dirname "$flag")"; done')
dc run --rm -T backup verify "$pending_id"
expect_failure dc run --rm -T backup push
[ "$(dc run --rm -T backup list | wc -l)" -eq "$((before + 1))" ]
# Retention cannot delete an old queued bundle, even while Git is temporarily disabled.
dc run --rm -T --entrypoint sh backup -ec 'touch -d "2000-01-01" "/backups/$1"' sh "$pending_id"
dc run --rm -T -e BACKUP_GIT_ENABLED=0 backup once
dc run --rm -T backup verify "$pending_id"
expect_failure dc run --rm -T backup health
# Advance the remote independently while an unpushed local commit exists.
dc exec -T --user git git-server sh -ec 'git clone /remote.git /tmp/other; cd /tmp/other; printf fixture > operator-note.txt; git add operator-note.txt; git -c user.name=Fixture -c user.email=fixture@localhost commit -m fixture'
expect_failure dc exec -T --user git git-server sh -ec 'cd /tmp/other; git push origin main'
dc exec -T git-server rm /remote.git/reject-push
dc exec -T --user git git-server sh -ec 'cd /tmp/other; git push origin main'
dc run --rm -T backup once
dc run --rm -T backup health
remote_git cat-file -e "main:archives/$pending_id/$pending_id.tar.gz.age"
remote_git cat-file -e main:operator-note.txt
# Known-host failure and wrong private key must not silently fall back to agent/password.
dc exec -T git-server sh -ec 'cp /run/secrets/backup_known_hosts /run/secrets/saved_hosts; printf invalid > /run/secrets/backup_known_hosts'
expect_failure dc run --rm -T backup once
dc exec -T git-server sh -ec 'cp /run/secrets/saved_hosts /run/secrets/backup_known_hosts'
dc exec -T git-server sh -ec 'mv /run/secrets/backup_ssh_key /run/secrets/saved_key; ssh-keygen -q -t ed25519 -N "" -f /run/secrets/wrong_key; cp /run/secrets/wrong_key /run/secrets/backup_ssh_key'
expect_failure dc run --rm -T backup push
dc exec -T git-server sh -ec 'cp /run/secrets/saved_key /run/secrets/backup_ssh_key'
dc run --rm -T backup once
dc run --rm -T backup health
expect_failure dc run --rm -T -e BACKUP_GIT_REPO=https://gitlab.example.com/backup.git backup push "$id"
expect_failure dc run --rm -T -e BACKUP_GIT_REPO=git@different.example.com:backup.git backup push "$id"
# Restore from the off-host repository, not the original local bundle.
dc run --rm -T --entrypoint sh backup -ec 'export GIT_SSH_COMMAND="ssh -F /dev/null -i /run/secrets/backup_ssh_key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/secrets/backup_known_hosts"; git clone "$BACKUP_GIT_REPO" /tmp/fresh-clone; rm -rf -- "/backups/$1"; mkdir /import; sh /opt/backup-decrypt.sh "/tmp/fresh-clone/archives/$1/$1.tar.gz.age"; cp "/output/$1/"* /import/; BACKUP_GIT_ENABLED=0 sh /opt/backup-worker.sh import "$1"' sh "$id"
dc exec -T db psql -U fixture -d fixture -c "UPDATE sample SET value='changed';"
dc run --rm -T restore restore "$id"
[ "$(dc exec -T db psql -U fixture -d fixture -Atc 'SELECT value FROM sample')" = git-original ]
dc exec -T db sh -ec 'test "$(cat /fixture-media/file.txt)" = git-media'
# Tampered archive and wrong decryption identity leave no published output.
dc run --rm -T --entrypoint sh backup -ec 'mv "/output/$1" /output/restored; cp "/backup-git/repo/archives/$1/$1.tar.gz.age" /tmp/"$1.tar.gz.age"; cp "/backup-git/repo/archives/$1/$1.tar.gz.age.sha256" /tmp/"$1.tar.gz.age.sha256"; printf corrupt >> /tmp/"$1.tar.gz.age"; if sh /opt/backup-decrypt.sh /tmp/"$1.tar.gz.age"; then exit 1; fi; test ! -e "/output/$1"' sh "$id"
dc exec -T git-server sh -ec 'cp /run/secrets/backup_age_identity /run/secrets/saved_identity; age-keygen -o /run/secrets/wrong_identity; cp /run/secrets/wrong_identity /run/secrets/backup_age_identity'
expect_failure dc run --rm -T --entrypoint sh backup -ec 'sh /opt/backup-decrypt.sh "/backup-git/repo/archives/$1/$1.tar.gz.age"' sh "$id"
dc exec -T git-server sh -ec 'cp /run/secrets/saved_identity /run/secrets/backup_age_identity'
# A correctly encrypted archive with unexpected paths must still be refused.
dc run --rm -T --entrypoint sh backup -ec 'printf unsafe > /tmp/escape; tar -czf /tmp/unexpected.tar.gz -C /tmp escape; age -R /run/secrets/backup_age_recipients -o "/tmp/$1.tar.gz.age" /tmp/unexpected.tar.gz; cd /tmp; sha256sum "$1.tar.gz.age" > "$1.tar.gz.age.sha256"; if sh /opt/backup-decrypt.sh "/tmp/$1.tar.gz.age"; then exit 1; fi; test ! -e "/output/$1"' sh "$id"
# A later Git retry must not conceal a NAS export failure.
expect_failure readonly_dc run --rm -T backup once
dc run --rm -T backup push
expect_failure dc run --rm -T backup health
dc run --rm -T backup once
dc run --rm -T backup health
# The scheduler still captures locally on its interval while the remote rejects
# writes. Poll a bounded fixture for two new captures; no real 24h wait needed.
before=$(dc run --rm -T backup list | wc -l)
dc exec -T git-server touch /remote.git/reject-push
dc run -d --name "${project}_watch" -e BACKUP_INTERVAL_SECONDS=3 backup watch
enough=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    count=$(docker exec "${project}_watch" sh -ec 'find /backups -mindepth 1 -maxdepth 1 -type d -name "mmp-*" | wc -l')
    if [ "$count" -ge "$((before + 2))" ]; then enough=1; break; fi
    sleep 2
done
[ "$enough" = 1 ] || { docker logs "${project}_watch"; echo 'Scheduler failed to capture during outage' >&2; exit 1; }
expect_failure docker exec "${project}_watch" sh /opt/backup-worker.sh health
docker stop "${project}_watch" >/dev/null
docker rm "${project}_watch" >/dev/null
dc exec -T git-server rm /remote.git/reject-push
dc run --rm -T backup push
dc run --rm -T backup health
echo 'PASS: real SSH authentication, encrypted push/decrypt/restore, host recovery helper, idempotency, rejected push/retry, queue retention, remote advance, wrong key/host, corruption/unsafe archive, destination guard, NAS health, capture during outage'

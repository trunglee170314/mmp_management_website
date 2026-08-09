#!/bin/sh
# Called under the worker's bundle lock, after full PostgreSQL verification.
set -eu
umask 077
die() { echo "$*" >&2; exit 1; }
id=${1:?Bundle ID required}
printf '%s\n' "$id" | grep -Eq '^mmp-[0-9]{8}T[0-9]{6}Z-[a-zA-Z0-9]{6}$' || die 'Invalid bundle ID'
remote=${BACKUP_GIT_REPO:?BACKUP_GIT_REPO required}
# Only SSH URLs: no passwords, shell syntax or option injection.
printf '%s\n' "$remote" | grep -Eq '^(git@[a-zA-Z0-9][a-zA-Z0-9.-]*:[a-zA-Z0-9_./-]+\.git|ssh://git@[a-zA-Z0-9][a-zA-Z0-9.-]*(:[0-9]+)?/[a-zA-Z0-9_./-]+\.git)$' || die 'Use git@HOST:GROUP/REPO.git or ssh://git@HOST:PORT/GROUP/REPO.git'
branch=${BACKUP_GIT_BRANCH:-main}
git check-ref-format --branch "$branch" >/dev/null || die 'Invalid backup branch'
limit=${BACKUP_GIT_TIMEOUT_SECONDS:-120}
printf '%s\n' "$limit" | grep -Eq '^[1-9][0-9]*$' || die 'Invalid Git timeout'
for secret in backup_ssh_key backup_known_hosts backup_age_recipients; do
    [ -f "/run/secrets/$secret" ] && [ -s "/run/secrets/$secret" ] || die "Missing $secret"
done
bundle="/backups/$id"
[ -d "$bundle" ] && [ ! -L "$bundle" ] || die 'Bundle missing or unsafe'
mkdir -p /backup-git
exec 8>/backup-git/.upload.lock
flock -x 8
stage=$(mktemp -d /backup-git/.upload.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
for file in database.dump media.tar.gz manifest.txt checksums.sha256; do
    [ -f "$bundle/$file" ] && [ ! -L "$bundle/$file" ] || die "Unsafe bundle file: $file"
done
(cd "$bundle"; sha256sum database.dump media.tar.gz manifest.txt) > "$stage/source-checksums"
cmp "$stage/source-checksums" "$bundle/checksums.sha256" || die 'Bundle checksum mismatch'
export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export GIT_SSH_COMMAND='ssh -F /dev/null -i /run/secrets/backup_ssh_key -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/secrets/backup_known_hosts -o ConnectTimeout=15'
repo=/backup-git/repo
g() {
    timeout "$limit" git -C "$repo" -c core.hooksPath=/dev/null \
        -c protocol.file.allow=never -c user.name=MMP-Backup \
        -c user.email=backup@localhost "$@"
}
if [ ! -e "$repo" ]; then
    mkdir "$stage/new-repo"
    git -C "$stage/new-repo" init -b "$branch" >/dev/null
    git -C "$stage/new-repo" remote add origin "$remote"
    mv "$stage/new-repo" "$repo"
fi
[ -d "$repo/.git" ] && [ ! -L "$repo/.git" ] && [ ! -L "$repo" ] || die 'Uploader checkout is invalid'
[ "$(g remote get-url origin)" = "$remote" ] || die 'Backup URL changed: use a new dedicated backup_git volume'
[ "$(g symbolic-ref --short HEAD)" = "$branch" ] || die 'Backup branch changed: use a new dedicated backup_git volume'
archive="$id.tar.gz.age"
# A crash after publishing files but before commit can be retried. Other changes
# are never discarded; this volume must be reserved for this uploader.
g status --porcelain --untracked-files=all > "$stage/status"
while IFS= read -r change; do
    path=$(printf '%s' "$change" | cut -c4-)
    case "$path" in
        "archives/$id/$archive"|"archives/$id/$archive.sha256"|"archives/$id/$id.bundle.sha256") ;;
        *) die 'Uploader checkout has unrelated changes; inspect it before retrying';;
    esac
done < "$stage/status"
g ls-remote "$remote" > "$stage/refs"
if awk -v ref="refs/heads/$branch" '$2 == ref { found=1 } END {exit !found}' "$stage/refs"; then
    g fetch --no-tags "$remote" "refs/heads/$branch:refs/remotes/origin/$branch"
    if g rev-parse --verify HEAD >/dev/null 2>&1; then
        # Normal merge if another uploader advanced the remote; never force push.
        if ! g merge --no-edit FETCH_HEAD; then
            g merge --abort || true
            die 'Backup merge conflict; inspect the dedicated uploader checkout'
        fi
    else
        g checkout -B "$branch" FETCH_HEAD
    fi
elif g rev-parse --verify HEAD >/dev/null 2>&1 && g rev-parse --verify refs/remotes/origin/"$branch" >/dev/null 2>&1; then
    die 'Remote branch disappeared; refusing to recreate it automatically'
fi
archives="$repo/archives"
[ ! -L "$archives" ] || die 'Unsafe archives folder'
mkdir -p "$archives"
destination="$archives/$id"
[ ! -L "$destination" ] || die 'Unsafe encrypted bundle folder'
if [ -e "$destination" ]; then
    for file in "$archive" "$archive.sha256" "$id.bundle.sha256"; do
        [ -f "$destination/$file" ] && [ ! -L "$destination/$file" ] || die 'Incomplete or unsafe encrypted bundle'
    done
    cmp "$bundle/checksums.sha256" "$destination/$id.bundle.sha256" || die 'Same-ID backup differs; refusing overwrite'
    (cd "$destination"; sha256sum "$archive") > "$stage/cipher-checksums"
    cmp "$stage/cipher-checksums" "$destination/$archive.sha256" || die 'Ciphertext checksum mismatch'
else
    # Plaintext backup/private keys never enter the Git checkout.
    tar -czf "$stage/bundle.tar.gz" -C /backups \
        "$id/database.dump" "$id/media.tar.gz" "$id/manifest.txt" "$id/checksums.sha256"
    mkdir "$stage/encrypted"
    age -R /run/secrets/backup_age_recipients -o "$stage/encrypted/$archive" "$stage/bundle.tar.gz"
    cp "$bundle/checksums.sha256" "$stage/encrypted/$id.bundle.sha256"
    (cd "$stage/encrypted"; sha256sum "$archive" > "$archive.sha256")
    mv "$stage/encrypted" "$destination"
fi
g add -- "archives/$id/$archive" "archives/$id/$archive.sha256" "archives/$id/$id.bundle.sha256"
if ! g diff --cached --quiet; then
    g commit -m "Backup $id"
fi
g push "$remote" "HEAD:refs/heads/$branch"
echo "Encrypted backup pushed: $id"

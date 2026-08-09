#!/bin/sh
# Offline tool: archive/identity read-only, no Git configuration loaded.
set -eu
umask 077
die() { echo "$*" >&2; exit 1; }
archive=${1:?Encrypted archive path required}
name=$(basename "$archive")
id=${name%.tar.gz.age}
printf '%s\n' "$id" | grep -Eq '^mmp-[0-9]{8}T[0-9]{6}Z-[a-zA-Z0-9]{6}$' || die 'Invalid encrypted bundle name'
[ "$name" = "$id.tar.gz.age" ] || die 'Expected .tar.gz.age archive'
[ -f "$archive" ] && [ ! -L "$archive" ] || die 'Archive missing or unsafe'
[ -f "$archive.sha256" ] && [ ! -L "$archive.sha256" ] || die 'Ciphertext checksum missing or unsafe'
[ -d /output ] && [ ! -e "/output/$id" ] && [ ! -L "/output/$id" ] || die 'Output folder missing or bundle already exists'
stage=$(mktemp -d /output/.decrypt.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
(cd "$(dirname "$archive")"; sha256sum "$name") > "$stage/cipher-checksums"
cmp "$stage/cipher-checksums" "$archive.sha256" || die 'Ciphertext checksum mismatch'
age -d -i /run/secrets/backup_age_identity -o "$stage/bundle.tar.gz" "$archive"
tar -tzf "$stage/bundle.tar.gz" > "$stage/raw-names"
sort "$stage/raw-names" > "$stage/names"
printf '%s\n' "$id/database.dump" "$id/media.tar.gz" "$id/manifest.txt" "$id/checksums.sha256" | sort > "$stage/expected"
cmp "$stage/names" "$stage/expected" || die 'Unsafe or unexpected encrypted archive contents'
tar -tvzf "$stage/bundle.tar.gz" > "$stage/types"
awk 'substr($0,1,1) != "-" {exit 1}' "$stage/types" || die 'Only regular bundle files are allowed'
mkdir "$stage/unpacked"
tar -xzf "$stage/bundle.tar.gz" -C "$stage/unpacked"
(cd "$stage/unpacked/$id"; sha256sum database.dump media.tar.gz manifest.txt) > "$stage/checksums"
cmp "$stage/checksums" "$stage/unpacked/$id/checksums.sha256" || die 'Decrypted bundle checksum mismatch'
mv "$stage/unpacked/$id" "/output/$id"
echo "Decrypted: /output/$id (restore.sh validates PostgreSQL/media before restoring)"

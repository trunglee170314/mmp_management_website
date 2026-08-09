# Backup, restore and recovery

[README](../README.md) · [Deployment](deployment.md)

Run from the repository root on the Docker host in Linux/WSL. No `.venv` needed.
If your Linux account needs sudo for Docker, use `sudo docker ...` for Docker
commands and `sudo sh deploy/...` for host scripts that invoke Docker. The GitLab
setup and new-host examples below show this `admin` + sudo case explicitly.
Only restore trusted backups: checksums detect corruption, not tampering, and
PostgreSQL dumps can execute SQL.

## Create and inspect backups

```bash
docker compose up -d backup                         # enable scheduler
sh deploy/backup.sh                                 # online capture
sh deploy/backup.sh --quiesce                       # stop application writers briefly
docker compose run --rm --no-deps backup list        # service need not be running
docker compose logs --tail=100 backup
docker compose ps
```

The scheduler captures on startup and every `BACKUP_INTERVAL_SECONDS` (86400).
Each `mmp-YYYYMMDDTHHMMSSZ-XXXXXX` bundle contains:

- `database.dump`: PostgreSQL custom-format dump.
- `media.tar.gz`: media, including hidden files.
- `manifest.txt`: creation time, database, capture mode and `app_version`.
- `checksums.sha256`: checksums for those three files.

Copy an actual ID from the list; do not include the printed `/backups/` prefix
when using an ID. Inspect a manifest without starting the scheduler:

```bash
docker compose run --rm --no-deps --entrypoint cat backup /backups/BUNDLE_ID/manifest.txt
```

Online capture gives a consistent database snapshot, but media may change
between captures. Use `--quiesce` for a coordinated snapshot; it restarts only
services previously running. Also stop any external writers.

## Restore

**This replaces the current database and media**, including removal of tables
introduced after the backup. Replace `BUNDLE_ID` below with an actual ID:

```bash
sh deploy/restore.sh BUNDLE_ID --yes
# Or import from an existing complete folder:
sh deploy/restore.sh /mnt/secure-backups/mmp/BUNDLE_ID --yes
```

The script validates checksums, extracts media safely and performs a full
database restore on an isolated verifier **before stopping traffic**.
It then stops proxy/web/maintenance/backup and creates a mandatory recovery
bundle before replacing data.

After success, select a release compatible with the backup's `app_version` and
your release records; only then restart:

```bash
docker compose up -d --build
docker compose exec -T web python manage.py check
```

Verify login, Tasks and media through the website too. Web startup applies
migrations; do not restart an incompatible version.

## Restore on a new host

Source and data are separate. A source clone does not include Docker volumes,
`.env` or backups. This procedure applies to the complete supported bundle format;
an old standalone database dump or a backup made while media was changing is not
equivalent to a coordinated database/media snapshot.

1. Install Docker Engine/Compose and clone the application source. Preserve access
   to the current restore tooling and choose an application release compatible
   with the bundle's `manifest.txt` / `app_version` before starting web.

   ```bash
   git clone git@github.com:trunglee170314/mmp_management_website.git
   cd mmp_management_website
   ```

   Configure source-repo access separately if needed. Stay in this source checkout
   for the following commands; cloning the backup repo does not change directory.
2. Obtain an off-host backup and keep its folder name `mmp-...`. It must contain
   `database.dump`, `media.tar.gz`, `manifest.txt` and `checksums.sha256`.
   If a separate GitLab backup repo contains encrypted archives,
   clone it into a different directory from the source checkout:

   ```bash
   # Set this shell variable yourself; do not put a credential in the URL.
   BACKUP_GIT_REPO=git@gitlab.example.com:trungvanle/mmp-backups.git
   BACKUP_GIT_BRANCH=main
   # Existing authorized private key and independently verified host keys on THIS machine:
   export GIT_SSH_COMMAND='ssh -F /dev/null -i /home/admin/.ssh/mmp_gitlab_trungvanle -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/admin/.ssh/mmp_gitlab_known_hosts'
   git clone --branch "$BACKUP_GIT_BRANCH" "$BACKUP_GIT_REPO" /absolute/path/to/backup-checkout
   unset GIT_SSH_COMMAND
   ```

   Use the branch configured for backup, which may differ from the repo's default.
   The `.env` file is not automatically exported to your interactive shell.
   GitLab SSH access needs its own authorized key and verified host key. Never
   reuse the source checkout as the backup checkout. If the old host/key is lost,
   generate a new SSH key on this machine and register its public key with an
   account allowed to read the backup repo; follow setup steps 1–5 below, changing
   local paths if the Linux user is not `admin` and skipping creation of a new repo:
   clone the **existing backup repo**, not a new empty one. You do not need the lost host's
   SSH key. A new SSH key **cannot decrypt old backups**: you still need the
   original matching age identity. For an encrypted Git backup,
   recover the age identity from independent secure storage and decrypt first:

   ```bash
   umask 077
   mkdir -p /absolute/path/to/recovered-bundles
   sudo sh deploy/decrypt-backup.sh \
     /absolute/path/to/backup-checkout/archives/BUNDLE_ID/BUNDLE_ID.tar.gz.age \
     /secure/off-host-copy/age-identity.txt /absolute/path/to/recovered-bundles
   ```

   Replace both occurrences of `BUNDLE_ID` with the real full ID. The helper builds
   the tools image, then decrypts offline with no container network; it checks
   ciphertext, archive paths/types and inner checksums before publishing a folder.
   Keep its adjacent `.tar.gz.age.sha256` file with the archive. Decryption needs
   Docker, not a Python `.venv`. It refuses to overwrite an existing output bundle.
   With sudo, recovered files are root-owned; the sudo restore below can read them.
3. In the source checkout, recover `.env` from secure off-host storage or recreate
   it from `.env.example`. Do not commit secrets to either repo. Set the new
   `ALLOWED_HOSTS` / port and `ADMIN_RESET_PASSWORD=0`; retain `SECRET_KEY` where
   possible. If you generate a new secret, expect old sessions/signed tokens to
   become invalid. Choose secure database credentials for the new database;
   the dump does not recreate PostgreSQL roles/passwords. Leave `POSTGRES_HOST=db`.
   Remove unavailable NAS/Git overlays from `COMPOSE_FILE` for disaster recovery;
   use the base compose file until all destinations/credential files are available.
   Re-enable remote backup after the restored site is verified.

   If no recovered `.env` exists, create one on this **new host only**:

   ```bash
   # Do not overwrite an existing .env.
   if [ ! -e .env ]; then cp .env.example .env; fi
   chmod 600 .env
   nano .env
   ```

4. Restore before starting application services:

   ```bash
   sudo sh deploy/restore.sh /absolute/path/to/recovered-bundles/BUNDLE_ID --yes
   ```

   Replace `BUNDLE_ID` with an actual full bundle folder name. The script
   starts only db/verifier first, initializes fresh volumes, validates/imports the
   external bundle and takes a recovery backup of the empty/new database before
   replacement. No manually pre-created database or Python `.venv` is needed.
   Ensure enough disk/RAM for import, verification and the additional safety copy.
5. Only after success, start the compatible application version:

   ```bash
   sudo docker compose up -d --build
   sudo docker compose exec -T web python manage.py check
   sudo docker compose ps
   ```

   Check login, user accounts, Tasks/Action Items, Boards, Meeting Minutes/history
   and media. These database records are restored with the snapshot, not recreated
   from Git. Data added after the chosen backup is not included. External resources
   referenced by Links, such as another task server, are not backed up by this app.
   Keep the off-host backup until the restored site is verified; if restore fails,
   follow the recovery procedure below and keep writers stopped.

## Separate GitLab backup repository

Implemented as an optional Compose overlay. The worker verifies the full local
bundle, compresses/encrypts it with **age public recipients**, commits to its own
Docker-volume checkout and performs a normal SSH `git push`. It never uses or
modifies the application source checkout and never force-pushes.

### One-time setup

This example uses **Linux `admin` to run Docker with sudo** and **GitLab account
`trungvanle` to authorize push**. These are separate identities, unrelated to the
website's Admin role. All paths/hostnames are examples: replace `gitlab.example.com`
and the repo URL; change `/home/admin` if the actual Linux home differs.

1. Check the current Linux user:

   ```bash
   whoami
   ```

   If it is not `admin` and you want to use that existing Linux account, open its
   login shell first, then run the remaining host steps there:

   ```bash
   sudo -iu admin
   ```

   Create the host directories as `admin`, **not root**:

   ```bash
   umask 077
   mkdir -p /home/admin/.ssh
   mkdir -p /home/admin/.config/mmp-backup
   chmod 700 /home/admin/.ssh /home/admin/.config/mmp-backup
   ```

   No Linux `trungvanle` account or `/home/trungvanle/.ssh/` is required.
2. Sign in to GitLab as `trungvanle`. Create a **private, separate** repo, e.g.
   `trungvanle/mmp-backups`, on storage independent of the web host. A group repo
   also works if this account can push to the configured branch. Use its exact
   **Clone with SSH** URL. Do not use the source repo. It can be empty or have a
   README; disable CI/CD for this data-only project if not needed.

   On the Linux host, generate a dedicated key (do not overwrite existing files):

   ```bash
   ssh-keygen -t ed25519 -N '' \
     -f /home/admin/.ssh/mmp_gitlab_trungvanle -C mmp-backup-trungvanle
   chmod 600 /home/admin/.ssh/mmp_gitlab_trungvanle
   cat /home/admin/.ssh/mmp_gitlab_trungvanle.pub
   ```

   The current unattended uploader needs a key without a passphrase: it uses
   non-interactive SSH with no agent fallback. Keep this private key restricted.
3. While signed in to GitLab as **`trungvanle`**, open your avatar > **Edit profile >
   Access > SSH keys > Add new key**. Paste **only the `.pub` output**, choose a
   usage including Authentication, and save. Use a suitable expiration and plan
   renewal before it expires. This is a **personal account SSH key**, not the
   project's **Settings > Repository > Deploy keys**.

   Registering the public key determines the GitLab account; the Linux username,
   key filename/comment and Git commit author do not. The SSH URL normally uses
   `git@HOST`, **not `trungvanle@HOST`**. A personal key has this account's SSH
   repository permissions, not just the backup repo's: protect it and revoke it
   promptly if exposed. Account access/branch push rules must permit the upload.
   See [GitLab account SSH keys](https://docs.gitlab.com/user/ssh/).

   A project-scoped write deploy key is an alternative if you later want narrower
   access; it is not the personal-account workflow shown here.
4. Prepare verified host keys. Example for port 22; do not overwrite an existing
   verified host file without checking it:

   ```bash
   ssh-keyscan -t ed25519 gitlab.example.com > /home/admin/.ssh/mmp_gitlab_hosts_candidate
   ssh-keygen -lf /home/admin/.ssh/mmp_gitlab_hosts_candidate
   ```

   **Stop and independently compare the fingerprint with your GitLab administrator
   or trusted instance configuration. `ssh-keyscan` alone does not establish trust.**
   For self-managed GitLab, trusted fingerprints are available from
   `https://YOUR_GITLAB_HOST/help/instance_configuration#ssh-host-keys-fingerprints`.
   Only after a match:

   ```bash
   mv /home/admin/.ssh/mmp_gitlab_hosts_candidate /home/admin/.ssh/mmp_gitlab_known_hosts
   chmod 600 /home/admin/.ssh/mmp_gitlab_known_hosts
   ```

   A custom SSH port needs `ssh-keyscan -p PORT ...` and
   `ssh://git@HOST:PORT/GROUP/REPO.git`. The uploader uses strict host checking,
   no interactive password/agent fallback. Mount individual files, never all `.ssh`.
5. Verify which GitLab account the host key authenticates as:

   ```bash
   ssh -F /dev/null -i /home/admin/.ssh/mmp_gitlab_trungvanle \
     -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes \
     -o StrictHostKeyChecking=yes \
     -o UserKnownHostsFile=/home/admin/.ssh/mmp_gitlab_known_hosts \
     -T git@gitlab.example.com
   ```

   Expect **`Welcome to GitLab, trungvanle!`**. If another account appears, correct
   the key registration. This checks authentication, not permission to push to the
   backup branch; the real backup upload in step 8 verifies that. For a custom SSH
   port, add `-p PORT` to this command too.
6. On a **trusted recovery machine** install `age` and generate an identity and
   public recipient file (Ubuntu example):

   ```bash
   sudo apt update
   sudo apt install -y age
   umask 077
   recovery_keys_dir="$HOME/.config/mmp-backup-recovery"
   mkdir -p "$recovery_keys_dir"
   age-keygen -o "$recovery_keys_dir/age-identity.txt"
   age-keygen -y "$recovery_keys_dir/age-identity.txt" > "$recovery_keys_dir/recipients.txt"
   ```

   Save the private `age-identity.txt` in independent secure off-host storage and
   test recovery. Copy **only `recipients.txt`** to
   `/home/admin/.config/mmp-backup/recipients.txt` on the host (directory created in
   step 1). Transfer it securely by your available method; if you already copied
   the public file onto the host, install it as `admin`:

   ```bash
   install -m 600 /path/to/copied/recipients.txt /home/admin/.config/mmp-backup/recipients.txt
   ```

   SSH keys authorize Git access; the **separate age identity decrypts data**.
   Do not upload either private key to GitLab. The running backup container does
   not need the decryption key. The recipient file
   can contain multiple `age1...` public recipients, one per line. Losing all
   matching identities makes backups unusable. Keep old keys for old archives.
7. Return to the application checkout and edit its **existing** `.env`. Do not copy
   `.env.example` over a configured host's `.env` or lose its database credentials:

   ```bash
   cd /path/to/mmp_management_website
   nano .env
   ```

   Set these values (absolute existing Linux paths, not Windows paths):

   ```env
   COMPOSE_FILE=docker-compose.yml:deploy/backup-git.compose.yml
   BACKUP_GIT_REPO=git@gitlab.example.com:trungvanle/mmp-backups.git
   BACKUP_GIT_BRANCH=main
   BACKUP_GIT_TIMEOUT_SECONDS=120
   BACKUP_GIT_SSH_KEY_PATH=/home/admin/.ssh/mmp_gitlab_trungvanle
   BACKUP_GIT_KNOWN_HOSTS_PATH=/home/admin/.ssh/mmp_gitlab_known_hosts
   BACKUP_AGE_RECIPIENTS_PATH=/home/admin/.config/mmp-backup/recipients.txt
   ```

   For NAS **and** Git, use one value containing both overlays:
   `COMPOSE_FILE=docker-compose.yml:deploy/backup-export.compose.yml:deploy/backup-git.compose.yml`
   and configure `BACKUP_EXPORT_PATH` too. Plain `BACKUP_GIT_REPO` alone does not
   enable the uploader; the Git overlay is required.
8. Build/start and make a coordinated backup. `--quiesce` briefly stops application
   writers and restarts those previously running; stop external writers too:

   ```bash
   sudo docker compose up -d --build backup
   sudo sh deploy/backup.sh --quiesce
   sudo docker compose logs --tail=100 backup
   sudo docker compose ps
   sudo docker compose run --rm --no-deps backup health
   ```

   Check for `Encrypted backup pushed: mmp-...` and `Backup verified: mmp-...`,
   then verify that same bundle exists under `archives/` in GitLab. Authentication
   alone is not proof of a successful push. Backups then run every 24 hours by
   default; local retention is 30 days. Test decryption and restore on an isolated
   machine using the new-host steps above before relying on the off-host copy.

### Files, retry and recovery

Each remote `archives/BUNDLE_ID/` contains `BUNDLE_ID.tar.gz.age`, its
`.tar.gz.age.sha256` checksum and `BUNDLE_ID.bundle.sha256` (inner checksums, no
database/media contents). No plaintext backups, `.env`, private SSH keys or age
identities are committed. Git author is `MMP-Backup <backup@localhost>`; this is
not the authentication identity. The local uploader checkout is `/backup-git/repo`
in the `backup_git` Docker volume, separate from `/backups` in `backup_data`.

New captures continue on the configured interval even if Git is unavailable;
pending bundles carry `.git-pending` flags and are protected from retention.
Scheduled uploads retry after at most 60 seconds of waiting between attempts;
Git commands have a bounded timeout. Failures mark health as failed. Health only
recovers when the latest capture reached all configured destinations, including
NAS if enabled. A failed NAS export is not repaired by Git push alone: fix the
NAS and rerun `sh deploy/backup.sh` for a complete capture/export (otherwise the
next scheduled capture will retry the NAS). Monitor health/logs and free space:
an extended outage can fill
the queue, and no alerts are sent automatically. Retrying the same ID reuses the
same encrypted artifact/commit. Conflicts or unrelated checkout changes fail
safely rather than overwrite them.

```bash
# Retry queued bundles without capturing another snapshot:
sudo docker compose run --rm --no-deps backup push
# Upload one existing complete local bundle (replace BUNDLE_ID):
sudo docker compose run --rm --no-deps backup push BUNDLE_ID
```

Existing pre-Git bundles are not automatically uploaded; use `push BUNDLE_ID`
for those you want. Local retention continues separately; it does **not** delete
Git files/history. Git repository size and per-file/server push limits still
apply to encrypted archives. No LFS, pruning of remote files, force-push or history
rewrite is configured. Monitor GitLab quotas and plan periodic new archival repos
or a dedicated backup store if data grows large.

Changing the repo URL or branch refuses to reuse an old uploader checkout. Keep
that volume (it may hold unpushed commits), inspect pending backups, then configure
a **new** named volume under `backup_git` in the overlay and recreate backup.
Do not delete old volumes or copy the source checkout into this volume.

To recover on another machine: clone the backup repo with authorized SSH access,
recover the age identity independently, run `decrypt-backup.sh`, then `restore.sh`
as shown in [Restore on a new host](#restore-on-a-new-host). Disabling the optional
Git overlay allows local restore even when GitLab is down. Restore's mandatory
pre-replacement safety backup is local (and NAS if configured), not blocked by
Git authentication. Keep configuration/source release records securely off-host
too; data-only backups do not contain them.

## Recover after a failed restore

Do not delete staging, data volumes or backups, and do not blindly restart web.
Database/media replacement is not one atomic transaction.

1. **Keep writers stopped and inspect the error.**

   ```bash
   docker compose stop proxy web maintenance backup
   docker compose ps -a
   docker compose logs --tail=100 db backup_verify
   ```

   Preserve the restore command's terminal output (one-off restore errors may not
   appear in service logs). If failure happened before `Recovery backup:`, the
   script has not started database/media replacement: fix the cause and retry,
   or resume the unchanged release. Failure after that message may leave partial
   state; use the printed recovery ID, not a guessed newest bundle.

2. **Validate that recovery bundle and preserve an incident copy.**
   Replace the example below with the exact printed ID:

   ```bash
   RECOVERY_ID=mmp-YYYYMMDDTHHMMSSZ-XXXXXX
   docker compose up -d --wait db backup_verify
   docker compose run --rm --no-deps backup verify "$RECOVERY_ID"

   umask 077
   mkdir -p backups
   incident_dir=$(mktemp -d backups/recovery-XXXXXX)
   docker compose run --rm -T --no-deps -e RECOVERY_ID="$RECOVERY_ID" \
     --entrypoint sh backup -ec 'tar -czf - -C /backups "$RECOVERY_ID"' \
     > "$incident_dir/recovery-bundle.tar.gz"
   docker compose run --rm -T --no-deps --entrypoint sh restore \
     -ec 'tar -czf - -C /media .' > "$incident_dir/media-before-recovery.tar.gz"
   ```

   Continue only if these commands succeed. The second archive preserves media
   and any `/media/.mmp-restore.*` staging. Copy the incident folder off-host when
   possible. Do not remove the originals until recovery is verified.

3. **Fix the cause**, such as full disk, insufficient verifier RAM, unavailable
   NAS, wrong credentials or media permissions. Recovery needs free space for
   another safety backup and media staging.

   If logs specifically show that the configured production database is **missing**
   (a failed restore dropped it but could not recreate it), recreate an empty
   database using the same configured identity:

   ```bash
   docker compose run --rm --no-deps --entrypoint sh restore -ec \
     'createdb -h db -U "$POSTGRES_USER" --maintenance-db=template1 --template=template0 "$POSTGRES_DB"'
   ```

   Run that only for a confirmed missing database; it is not a general repair
   command. If an existing database remains unreadable by `pg_dump`, stop here
   and arrange a DBA-assisted restore from the preserved recovery bundle.
   Do not bypass the safety backup or erase volumes.

4. **Restore the saved recovery point**, then verify the matching release:

   ```bash
   sh deploy/restore.sh "$RECOVERY_ID" --yes
   # Only after success, with the compatible release:
   docker compose up -d --build
   docker compose exec -T web python manage.py check
   ```

   This restores the database/media from immediately before the failed attempt.
   Keep the incident copy until website checks pass. If it fails again, keep
   writers stopped and preserve the new output/staging too.

## External copy: another disk or NAS

Local Docker volumes do not survive host/disk loss. Prepare an access-controlled
folder on another disk or mounted NAS, then set both values in `.env`:

```env
COMPOSE_FILE=docker-compose.yml:deploy/backup-export.compose.yml
BACKUP_EXPORT_PATH=/mnt/secure-backups/mmp
```

```bash
docker compose up -d backup
```

Compose refuses a nonexistent folder. For NAS, verify it is **actually mounted**;
an existing empty mountpoint does not prove connectivity. The app does not
provision a storage account, mount NAS or perform remote transfers for you.

Each verified bundle is copied and checked before success is recorded. Export
failure marks backup health as failed even if a local copy succeeded. Retention
applies to local and exported completed bundles after a successful job.

Manual export (requires an existing backup service container):

```bash
umask 077
mkdir -p backups
docker compose cp backup:/backups/BUNDLE_ID backups/
```

Restore the copied folder using `sh deploy/restore.sh /absolute/path/BUNDLE_ID --yes`.
It is imported and verified before traffic stops. Same-ID different-content
bundles are never overwritten. After host loss, restore your checkout/`.env`
first; if the NAS export is unavailable, disable its optional `COMPOSE_FILE`/
`BACKUP_EXPORT_PATH` settings before using a local recovered folder.

## Limits and monitoring

- Bundles do not include source, `.env`, TLS keys or PostgreSQL roles/passwords.
  Keep those separately in secure storage and retain release records.
- Media supports regular files/directories, not symlinks or special files.
- Verification uses isolated PostgreSQL RAM storage (tmpfs), no production
  volumes or published ports. Leave RAM for the restored database; for large
  databases, provision separate scratch storage, never the production volume.
- Concurrent jobs serialize. Failed scheduled attempts retry after 60 seconds.
  Healthchecks report failure/stale backups but do not send alerts automatically.
- Retention uses `BACKUP_RETENTION_DAYS=30`; completed older bundles are pruned
  only after success. The new bundle and selected restore target are protected.
  Legacy single dumps are untouched; tooling requires the complete new format.
- Schedule/retention values must be positive integers without leading zeros.
  Recreate backup after changing them: `docker compose up -d backup`.
- Never run `docker compose down --volumes` / `down -v` on production.

## Test the workflow

```bash
sh deploy/tests/backup-integration.sh
sh deploy/tests/git-backup-integration.sh
```

Uses a uniquely named disposable project with separate databases/volumes.
Tests DB/media round-trip, exact rollback, export/import, checksum rejection,
failed media moves, retention, export/scheduler failure, locking and health.
The Git suite uses a disposable real SSH server/bare repo and generated test keys:
encrypted push/decryption/restore, idempotency, rejected pushes/retry, pending
retention, another writer advancing the remote, wrong SSH key/host, corruption
and wrong decryption identity, host recovery helper and capture scheduling during
remote outage. Cleanup removes only the test projects. CI runs both.

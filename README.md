# MMP Management

Manage Tasks, Action Items, Meeting Minutes, Boards and System Maps on a trusted
laboratory network. Stack: Django, PostgreSQL 16, Gunicorn and Nginx.

## Quick start

Requires Ubuntu 22.04+, Git and Docker Compose.
[Install Docker / deployment details](docs/deployment.md).
Run all commands from the repository root in a Linux/WSL shell; no `.venv` needed.
If Docker requires sudo, use `sudo docker ...` and `sudo sh deploy/...`.

```bash
git clone git@github.com:trunglee170314/mmp_management_website.git
cd mmp_management_website
cp .env.example .env
chmod 600 .env
nano .env
```

Before starting, replace `SECRET_KEY`, `POSTGRES_PASSWORD` and `ADMIN_PASSWORD`;
set `ALLOWED_HOSTS` to the host IP/name. Keep `POSTGRES_HOST=db`.
Generate a secret with `openssl rand -base64 48`.

```bash
docker compose up -d --build
docker compose ps
docker compose exec -T web python manage.py check
```

Open `http://HOST_IP:8080` (or your `APP_PORT`), then log in with the configured
Admin account. HTTP is for the trusted network only; remote access needs TLS/VPN.
Web startup applies migrations automatically.

## Daily commands

```bash
docker compose ps                         # service status
docker compose logs -f --tail=100          # logs
docker compose restart                    # restart; does not reload .env
docker compose down                       # stop; keep data volumes
```

**Never use `docker compose down --volumes` / `down -v` on production.**
It deletes the database, media and local backups. Do not commit `.env`.

## Update

```bash
sh deploy/backup.sh --quiesce
git pull --ff-only
# Set APP_VERSION in .env to the new release before starting it.
docker compose up -d --build --remove-orphans
docker compose exec -T web python manage.py check
```

Keep the old `APP_VERSION` until the pre-update backup completes.
For rollback, restore its matching backup before starting the compatible release.

## Backup / restore

Automatic backup: database + media on startup and every 24 hours, retained for
30 days. Every bundle is checksum-checked and restored on an isolated verifier.

```bash
docker compose up -d backup                         # enable automatic backups
sh deploy/backup.sh                                 # online backup
sh deploy/backup.sh --quiesce                       # stop writers during capture
docker compose run --rm --no-deps backup list        # also works if service stopped
```

**Restore replaces the current database and media.** Replace `BUNDLE_ID` with an
actual `mmp-...` ID from the list, or supply a complete backup folder:

```bash
sh deploy/restore.sh BUNDLE_ID --yes
# Or: sh deploy/restore.sh /mnt/secure-backups/mmp/BUNDLE_ID --yes
# Only after success, with a compatible application release:
docker compose up -d --build
docker compose exec -T web python manage.py check
```

Restore validates first and takes a recovery backup before replacement.
If restore fails after writers stop, **keep them stopped** and follow
[recovery steps](docs/backup-restore.md#recover-after-a-failed-restore).
See [backup details / NAS / limits](docs/backup-restore.md).

## Restore on another machine

**Yes, a complete backup can be restored on a new host. Cloning source alone
does not restore data:** Docker volumes, `.env` and backups are not in the source repo.

1. Install Docker Compose; clone the source and use a compatible release with the
   restore scripts. Get the complete `mmp-...` bundle from off-host storage (or a
   separate backup repo that already contains exported bundles).
2. Restore/recreate `.env` securely, set `ALLOWED_HOSTS` for the new host and keep
   `ADMIN_RESET_PASSWORD=0`. If the backup is encrypted, decrypt/extract it first
   using the key saved separately outside the failed host.
3. From the source checkout, restore **before starting web**:

   ```bash
   sh deploy/restore.sh /absolute/path/to/mmp-BUNDLE_ID --yes
   # Only after success, with the compatible release:
   docker compose up -d --build
   docker compose exec -T web python manage.py check
   ```

The script initializes the database/verifier on a fresh host and imports the
bundle. Check login, Tasks, Boards, Meeting Minutes and media after startup.
See [new-host checklist](docs/backup-restore.md#restore-on-a-new-host).

## Separate GitLab backup repo

Optional automatic **encrypted** backup push to a separate private repo.
Example: Linux user `admin` runs Docker with sudo; GitLab account `trungvanle`
authorizes push. Create the key under `/home/admin/.ssh/` and register its **public
key in `trungvanle`'s personal GitLab SSH Keys**, not project Deploy keys. No Linux
user `trungvanle` is required. Follow the [complete setup commands](docs/backup-restore.md#one-time-setup)
first, then set these in the existing `.env`:

```env
COMPOSE_FILE=docker-compose.yml:deploy/backup-git.compose.yml
BACKUP_GIT_REPO=git@gitlab.example.com:trungvanle/mmp-backups.git
BACKUP_GIT_SSH_KEY_PATH=/home/admin/.ssh/mmp_gitlab_trungvanle
BACKUP_GIT_KNOWN_HOSTS_PATH=/home/admin/.ssh/mmp_gitlab_known_hosts
BACKUP_AGE_RECIPIENTS_PATH=/home/admin/.config/mmp-backup/recipients.txt
```

```bash
sudo docker compose up -d --build backup
sudo sh deploy/backup.sh --quiesce
sudo docker compose logs --tail=100 backup
sudo docker compose run --rm --no-deps backup health
```

Only verified bundles are pushed; failure keeps a protected local queue for retry.
The personal SSH key has the account's repository permissions; protect it.
**Store the age decryption key separately off-host**; without it,
the encrypted repo cannot restore data. GitLab must be on independent storage.
Git history grows independently of local retention; no force-push is performed.
See [GitLab setup, recovery and limits](docs/backup-restore.md#separate-gitlab-backup-repository).

## Import / test

```bash
docker compose cp /path/to/issues.csv web:/tmp/issues.csv
docker compose exec -T web python manage.py import_tasks_csv /tmp/issues.csv --dry-run
# After checking the summary:
docker compose exec -T web python manage.py import_tasks_csv /tmp/issues.csv

docker compose exec -T web python manage.py test
sh deploy/tests/backup-integration.sh
sh deploy/tests/git-backup-integration.sh
```

Missing dates stay empty; unknown people become inactive legacy accounts.
The importer skips duplicate Links. See [CSV options](docs/deployment.md#import-tasks-from-csv).
CI runs Django checks/tests, JavaScript checks/tests, HTTP smoke and isolated backup tests.

## More information

- [Deployment, configuration and troubleshooting](docs/deployment.md)
- [Backup, restore, recovery and external storage](docs/backup-restore.md)
- [User guide](docs/user-guide.md) · [Release notes](docs/releases/v1.0.0.md)

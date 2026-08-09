# Deployment and operations

[README](../README.md) · [Backup / recovery](backup-restore.md)

Commands assume the repository root, a Linux shell and access to Docker.
Use a fixed IP/hostname for the Ubuntu host. No Python virtual environment is
needed for Docker commands. Git and repository access are required.

## Install Docker on Ubuntu

Skip if `docker compose version` already works. These service commands require
systemd; they do not work in a WSL session whose PID 1 is not systemd.
In WSL, use a working Docker Engine/WSL integration before continuing.

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Sign out/in, then check `docker version` and `docker compose version`.
For Docker socket permission errors, sign in again after joining the Docker
group, or temporarily use `sudo`.

## Configuration

Copy `.env.example` to `.env`; it contains all defaults. Keep permissions restricted
(`chmod 600 .env`) and never commit it.

| Setting | Purpose |
| --- | --- |
| `DEBUG=0` | Production mode |
| `SECRET_KEY` | Long random secret; generate with `openssl rand -base64 48` |
| `ALLOWED_HOSTS` | Comma-separated host IPs/names, plus localhost if used |
| `APP_PORT=8080` | Host HTTP port |
| `APP_VERSION` | Deployed release; recorded in backup manifests |
| `TIME_ZONE` | Default: `Asia/Ho_Chi_Minh` |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Database identity and password |
| `POSTGRES_HOST=db`, `POSTGRES_PORT=5432` | Container database connection |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_DISPLAY_NAME` | Initial Admin account |
| `ADMIN_RESET_PASSWORD=0` | Leave off except for intentional password reset |
| `BACKUP_INTERVAL_SECONDS=86400`, `BACKUP_RETENTION_DAYS=30` | Backup schedule/retention |
| `COMPOSE_FILE`, `BACKUP_EXPORT_PATH` | Optional disk/NAS export overlay; see backup guide |
| `BACKUP_GIT_REPO`, `BACKUP_GIT_BRANCH=main` | Optional independent SSH backup repo/branch |
| `BACKUP_GIT_SSH_KEY_PATH`, `BACKUP_GIT_KNOWN_HOSTS_PATH` | Dedicated private SSH key / verified host keys, absolute Linux host paths |
| `BACKUP_AGE_RECIPIENTS_PATH` | age **public** recipients file; keep private decryption identity off-host |
| `BACKUP_GIT_TIMEOUT_SECONDS=120` | Timeout for each Git command; retry on failure |
| `MAINTENANCE_INTERVAL_SECONDS=86400` | Purge System Maps in Trash for 30 days |
| `GUNICORN_*` | Worker, timeout and request-recycling settings in `.env.example` |
| `TIMELINE_MAX_FUTURE_YEARS=10` | Timeline future-year limit |

Changing `.env` requires recreating affected containers; `docker compose restart`
does not reload environment variables. See password rotation below for database
credentials; simply editing `.env` does not change the stored PostgreSQL password.

The bundled proxy serves HTTP only. Keep it on the trusted laboratory network;
use a TLS reverse proxy or VPN for remote access.

## Startup and updates

Follow [Quick start](../README.md#quick-start) and [Update](../README.md#update).
Web startup runs migrations, static collection and Admin initialization before
Gunicorn. Source/template/CSS/JavaScript changes require an image rebuild.
Capture the pre-update backup with the old `APP_VERSION`; change it to the new
release only after that backup succeeds and before recreating the stack.

```bash
docker compose up -d --build web maintenance
docker compose exec -T web python manage.py check
docker compose exec -T web python manage.py shell -c \
  "from django.db import connection; print(connection.vendor, connection.settings_dict['NAME'])"
```

The database check should show `postgresql` and your configured database name.
Check `/login/` through the host's configured HTTP port after an update.

## Roll back a release

If migrations ran, restore the matching backup first using the **current**
restore tooling. Restore leaves writers stopped. Then choose the compatible
release identified by the backup's `app_version` and your release records:

```bash
git log --oneline -10
git switch --detach PREVIOUS_COMMIT
docker compose up -d --build --remove-orphans
docker compose ps
docker compose exec -T web python manage.py check
```

`PREVIOUS_COMMIT` is a placeholder. Migrations are not always reversible.
Do not start a newer/incompatible release against the restored database.
Use `git switch main` (or your deployment branch) when returning to that branch.
Older releases may lack the new backup services; retain a copy of the recovery
tooling and backups separately.

## Diagnose startup / restart loops

```bash
docker compose config --quiet
docker compose ps
docker compose logs --tail=200 db web proxy backup
docker compose exec -T db sh -c \
  'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose run --rm --no-deps --entrypoint python web manage.py check
```

Check the first error: placeholder/short secrets, wrong database password, missing
`ALLOWED_HOSTS`, or migration failure are common causes. The one-off diagnostic
container bypasses the normal entrypoint; it does not apply migrations.
`exec web` requires a running web container.

## Rotate the PostgreSQL password

This briefly interrupts traffic. Never delete data volumes to rotate credentials.
`POSTGRES_PASSWORD` initializes a new volume only; on an existing database, change
the PostgreSQL role password **and** `.env`.

1. Take a verified backup, then stop writers:

   ```bash
   sh deploy/backup.sh --quiesce
   docker compose stop proxy web maintenance backup
   docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
   ```

2. At the PostgreSQL prompt, run `\password` (changes the current connected role).
   Enter the new password twice, then `\q`. This avoids putting it in shell history.
3. Set the same new `POSTGRES_PASSWORD` in `.env`, then recreate **all credential
   consumers**, not just web/proxy. Recreate db too to keep its environment aligned:

   ```bash
   docker compose up -d --force-recreate db web maintenance backup proxy
   docker compose exec -T web python manage.py check
   sh deploy/backup.sh
   docker compose ps
   ```

The final backup checks the new credentials. `backup_verify` has separate scratch
credentials; one-off `restore` containers read the updated environment on creation.
If any step fails, keep writers stopped and align the role password and `.env`
before trying again.

## Reset the initial Admin password

The configured Admin password is normally applied only when the account is first
created. For an intentional reset, set `ADMIN_PASSWORD` and
`ADMIN_RESET_PASSWORD=1` in `.env`, then recreate web:

```bash
docker compose up -d --force-recreate web
```

After confirming login, set `ADMIN_RESET_PASSWORD=0` and recreate web again so a
later restart cannot repeat the reset.

## Import tasks from CSV

Expected columns: `Link`, `Status`, `Priority`, `Subject`, `Author`, `Assignee`,
`Start date`, `Due date`, `Complete date`. Missing dates stay empty. Unknown people
become inactive legacy accounts with unusable passwords. Duplicate Links are skipped.

```bash
docker compose cp /path/to/issues.csv web:/tmp/issues.csv
docker compose exec -T web python manage.py import_tasks_csv /tmp/issues.csv --dry-run
# Review the summary before saving:
docker compose exec -T web python manage.py import_tasks_csv /tmp/issues.csv
```

| Option | Behavior |
| --- | --- |
| `--scope NAME` | Fallback Scope; default: `Uncategorized` |
| `--map-user 'CSV Name=username'` | Map a CSV person to an existing account |
| `--unknown-users error` | Reject unknown people |
| `--link-base-url https://tasks.example.com` | Replace the exported host |

## Tests and maintenance

```bash
docker compose exec -T web python manage.py test
docker compose exec -T web python manage.py showmigrations core
sh deploy/tests/backup-integration.sh
```

Run tests on a test deployment where possible. The backup integration test uses
its own disposable project, never production volumes. CI runs Django checks/tests
on PostgreSQL 16, missing-migration checks, static collection, JavaScript syntax
and tests, a Compose HTTP smoke test and backup integration tests.

These fixture commands **create/reset data**; use only on a test deployment:

```bash
docker compose exec -T web python manage.py seed_test_tasks --count 20
docker compose exec -T web python manage.py seed_test_users --password 'replace-with-a-test-password'
```

The maintenance service permanently purges System Maps in Trash for 30 days.
See [User guide](user-guide.md) for application behavior.

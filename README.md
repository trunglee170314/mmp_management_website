# MMP Management

Django application for managing users, scopes, tasks, shared boards, Action Items, and weekly Meeting Minutes on a local laboratory network.

Supported deployment: Ubuntu, Docker Compose, Nginx, Gunicorn, and PostgreSQL 16.

## Requirements

- Ubuntu 22.04 or later
- Git
- Docker Engine with the Docker Compose plugin
- Repository access
- A fixed IP address or hostname for the Ubuntu PC

## Install Docker

Skip this section if `docker compose version` already works.

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

Sign out and sign in again, then verify:

```bash
docker version
docker compose version
```

## Setup

Clone the repository:

```bash
mkdir -p ~/projects
cd ~/projects
git clone git@github.com:trunglee170314/mmp_management_website.git
cd mmp_management_website
```

Create the environment file:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Set the deployment values:

```env
DEBUG=0
SECRET_KEY=replace-with-a-long-random-secret
ALLOWED_HOSTS=127.0.0.1,localhost,192.168.1.10
TIME_ZONE=Asia/Ho_Chi_Minh
APP_PORT=8080

POSTGRES_DB=mmp_management
POSTGRES_USER=mmp_management
POSTGRES_PASSWORD=replace-with-a-strong-database-password
POSTGRES_HOST=db
POSTGRES_PORT=5432

ADMIN_USERNAME=admin
ADMIN_PASSWORD=replace-with-a-strong-admin-password
ADMIN_DISPLAY_NAME=System Admin
ADMIN_RESET_PASSWORD=0
GUNICORN_WORKERS=3
GUNICORN_TIMEOUT=60
GUNICORN_GRACEFUL_TIMEOUT=30
GUNICORN_MAX_REQUESTS=1000
GUNICORN_MAX_REQUESTS_JITTER=100
TIMELINE_MAX_FUTURE_YEARS=10
```

Generate `SECRET_KEY` with:

```bash
openssl rand -base64 48
```

Add the Ubuntu PC IP address or hostname to `ALLOWED_HOSTS`. Keep `POSTGRES_HOST=db`. Do not commit `.env`.

## Run

Build and start all services:

```bash
docker compose up -d --build
docker compose ps
```

Verify Django:

```bash
docker compose exec web python manage.py check
```

Verify PostgreSQL:

```bash
docker compose exec web python manage.py shell -c \
  "from django.db import connection; print(connection.vendor, connection.settings_dict['NAME'])"
```

Expected output:

```text
postgresql mmp_management
```

Open the application:

```text
http://localhost:8080
http://UBUNTU_PC_IP:8080
```

Log in with `ADMIN_USERNAME` and `ADMIN_PASSWORD` from `.env`.

The bundled Nginx configuration serves HTTP only. Keep port `8080` on the
trusted laboratory network. Do not expose it directly to the internet; use a
TLS reverse proxy or a VPN if remote access is required.

## Update

Create a backup before applying migrations, then update and verify the stack:

```bash
mkdir -p backups
backup_stamp=$(date +%Y%m%d-%H%M%S)
docker compose exec -T db sh -c \
  'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  > "backups/postgres-${backup_stamp}.dump"
docker compose run --rm --no-deps --entrypoint sh web -c \
  'tar -C /app/media -czf - .' \
  > "backups/media-${backup_stamp}.tar.gz"

git pull --ff-only
docker compose up -d --build --remove-orphans
docker compose ps
docker compose exec -T web python manage.py check
curl --fail http://127.0.0.1:${APP_PORT:-8080}/login/ >/dev/null
```

Python, template, CSS, and JavaScript changes require rebuilding the `web` image:

```bash
docker compose up -d --build web
```

### Restore a backup

The restore replaces the current database and media files. Confirm the backup
filenames first and stop application traffic:

```bash
docker compose stop web proxy
docker compose exec -T db sh -c \
  'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < backups/postgres-YYYYMMDD-HHMMSS.dump
docker compose run --rm --no-deps --entrypoint sh web -c \
  'find /app/media -mindepth 1 -delete; tar -C /app/media -xzf -' \
  < backups/media-YYYYMMDD-HHMMSS.tar.gz
docker compose up -d
docker compose exec -T web python manage.py check
```

### Roll back an application update

Database migrations are not always reversible. If the new version migrated the
database, restore the matching backup above. Then run the previous known-good
commit:

```bash
git log --oneline -10
git switch --detach PREVIOUS_COMMIT
docker compose up -d --build --remove-orphans
docker compose ps
```

Return to the tracked branch after the problem is resolved with
`git switch main` (or the deployment branch in use).

## Commands

```bash
# Service status
docker compose ps

# Logs
docker compose logs -f --tail=200

# Run tests
docker compose exec web python manage.py test

# Show migrations
docker compose exec web python manage.py showmigrations core

# Create 20 random test tasks
docker compose exec web python manage.py seed_test_tasks --count 20

# Create or reset 2 Admin and 18 Member test accounts
docker compose exec web python manage.py seed_test_users --password 'replace-with-a-test-password'

# Restart services
docker compose restart

# Stop services without deleting PostgreSQL data
docker compose down
```

## Diagnose a restart loop

Start with service state and the first application error:

```bash
docker compose config --quiet
docker compose ps
docker compose logs --tail=200 db web proxy
docker compose exec -T db sh -c \
  'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose run --rm --no-deps --entrypoint python web manage.py check
```

Common causes are a placeholder or short `SECRET_KEY`, an incorrect
`POSTGRES_PASSWORD`, a missing `ALLOWED_HOSTS` entry, or a failed migration.
`docker compose exec web ...` cannot run while `web` is restarting; the
`docker compose run ... --entrypoint python` command above creates a one-off
diagnostic container instead.

## Import tasks from CSV

The importer accepts task CSV exports with these columns: `Link`, `Status`,
`Priority`, `Subject`, `Author`, `Assignee`, `Start date`, `Due date`, and
`Complete date`. Missing Start and Due dates are stored as empty values. Unknown
people are preserved as inactive legacy accounts with unusable passwords.

Copy the CSV into the running container and validate it without saving changes:

```bash
docker compose cp /path/to/issues.csv web:/tmp/issues.csv
docker compose exec web python manage.py import_tasks_csv /tmp/issues.csv --dry-run
```

Run the import after reviewing the dry-run summary:

```bash
docker compose exec web python manage.py import_tasks_csv /tmp/issues.csv
```

Tasks use the `Uncategorized` Scope by default. Use `--scope NAME` to select a
different fallback Scope. The command skips existing tasks with the same
Link. Use `--map-user 'CSV Name=username'` to map a CSV name to an existing
account, `--unknown-users error` to reject unknown people, or
`--link-base-url https://tasks.example.com` to replace the exported host.

Migrations and static file collection run automatically when the `web` container starts. The Admin password is initialized only when the account is first created. Set `ADMIN_RESET_PASSWORD=1` for one restart only when an intentional reset is required, then return it to `0`.

`POSTGRES_PASSWORD` initializes the database only when the `postgres_data` volume is first created. Changing only this value in `.env` later will not change the password stored by PostgreSQL and will make the `web` container fail authentication. To rotate it without deleting data, use the same new password in both places:

```bash
docker compose exec db psql -U mmp_management -d mmp_management
```

At the PostgreSQL prompt, run `\password mmp_management`, enter the new password twice, run `\q`, update `POSTGRES_PASSWORD` in `.env`, and recreate the application containers with `docker compose up -d --build --force-recreate web proxy`.

Never run `docker compose down -v` unless the PostgreSQL data may be permanently deleted.

If Docker reports permission denied for `/var/run/docker.sock`, sign out and sign in after joining the `docker` group, or temporarily prefix the command with `sudo`.

## Continuous integration

`.github/workflows/ci.yml` runs Django checks and tests against PostgreSQL 16,
checks for missing migrations, collects static files, and starts the complete
Docker Compose stack for an HTTP smoke test. The workflow uses disposable CI
credentials and never reads the deployment `.env` file.

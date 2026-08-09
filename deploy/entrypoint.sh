#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
    chown -R app:app /app/staticfiles /app/media
    exec gosu app "$0" "$@"
fi

python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py bootstrap_admin
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
    --max-requests "${GUNICORN_MAX_REQUESTS:-1000}" \
    --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-100}" \
    --access-logfile - \
    --error-logfile -

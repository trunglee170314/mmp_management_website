FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install -r requirements.txt
RUN addgroup --system app && adduser --system --ingroup app app
COPY --chown=app:app . .
RUN mkdir -p /app/staticfiles /app/media \
    && chmod +x /app/deploy/entrypoint.sh \
    && chown -R app:app /app
ENTRYPOINT ["/app/deploy/entrypoint.sh"]

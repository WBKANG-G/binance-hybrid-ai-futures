FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
ENV UV_LINK_MODE=copy UV_CACHE_DIR=/tmp/uv-cache PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
RUN --mount=type=secret,id=cloud_ca \
    if [ -f /run/secrets/cloud_ca ]; then export PIP_CERT=/run/secrets/cloud_ca; fi; \
    pip install --no-cache-dir uv==0.12.23
COPY --chown=10001:10001 pyproject.toml uv.lock ./
RUN --mount=type=secret,id=cloud_ca \
    if [ -f /run/secrets/cloud_ca ]; then export SSL_CERT_FILE=/run/secrets/cloud_ca UV_SYSTEM_CERTS=true; fi; \
    uv sync --frozen --no-dev && rm -rf /tmp/uv-cache
COPY --chown=10001:10001 apps/__init__.py apps/__init__.py
COPY --chown=10001:10001 apps/api apps/api
COPY --chown=10001:10001 packages packages
COPY --chown=10001:10001 configs configs
COPY --chown=10001:10001 integrations integrations
COPY --chown=10001:10001 services services
COPY --chown=10001:10001 backtest backtest
COPY --chown=10001:10001 scripts scripts
COPY --chown=10001:10001 migrations migrations
COPY --chown=10001:10001 alembic.ini alembic.ini
RUN install -d -o 10001 -g 10001 -m 0700 /app/demo-export-state
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

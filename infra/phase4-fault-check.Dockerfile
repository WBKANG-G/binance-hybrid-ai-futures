FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
COPY infra/phase4-fault-test-dependencies.tar.gz /tmp/fault-dependencies.tar.gz
RUN echo "365f99ab023fb675ba67e271092ccdbc014a2f5bb6c21b8568d78ecc2a1bab85  /tmp/fault-dependencies.tar.gz" | sha256sum -c - \
    && mkdir -p /usr/local/lib/python3.12/site-packages \
    && tar -xzf /tmp/fault-dependencies.tar.gz -C /usr/local/lib/python3.12/site-packages \
    && chown -R 10001:10001 /usr/local/lib/python3.12/site-packages \
    && rm /tmp/fault-dependencies.tar.gz
COPY --chown=10001:10001 pyproject.toml uv.lock alembic.ini ./
COPY --chown=10001:10001 packages packages
COPY --chown=10001:10001 integrations integrations
COPY --chown=10001:10001 services services
COPY --chown=10001:10001 scripts scripts
COPY --chown=10001:10001 configs configs
COPY --chown=10001:10001 migrations migrations
COPY --chown=10001:10001 tests tests
USER 10001:10001
CMD ["python", "-m", "scripts.run_isolated_phase4_fault_check"]

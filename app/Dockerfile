FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Testy a lint: závislosti pro vývoj, zdrojáky se připojí z repa.
FROM base AS dev
RUN uv sync --frozen --no-install-project

FROM base AS runtime
COPY --from=litestream/litestream:0.5.18 /usr/local/bin/litestream /usr/local/bin/litestream
COPY deploy ./deploy
COPY vmarket ./vmarket
ENV DATABASE_PATH=/data/vmarket.db MALLOC_MMAP_THRESHOLD_=1048576
RUN useradd --system --uid 10001 vmarket && mkdir /data && chown vmarket /data
USER vmarket
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --start-interval=2s \
	CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]
CMD ["/app/deploy/start.sh"]

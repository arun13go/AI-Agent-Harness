FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM python:3.12.13-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && .venv/bin/python -m agent_harness.app.agent_http_server --help \
    && groupadd --gid 10001 harness \
    && useradd --uid 10001 --gid harness --create-home harness

COPY config ./config
USER 10001:10001

EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/readiness', timeout=3).close()"]
ENTRYPOINT ["/app/.venv/bin/python", "-m", "agent_harness.app.agent_http_server"]
CMD ["--hosted", "--telemetry-exporter", "application-insights"]

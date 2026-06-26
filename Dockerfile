FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENOYSTER_WORKSPACE=/app/workspace \
    OPENOYSTER_INBOX_DIR=/app/workspace/inbox

WORKDIR /app

RUN groupadd --system --gid 10001 openoyster \
    && useradd --system --uid 10001 --gid openoyster --home-dir /app openoyster

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN python -m pip install --upgrade pip \
    && python -m pip install ".[postgres]"

RUN mkdir -p /app/workspace/inbox /app/workspace/archive \
    && chown -R openoyster:openoyster /app
USER openoyster

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()" || exit 1

CMD ["openoyster", "serve", "--host", "0.0.0.0", "--port", "8080"]

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-dev

# second stage
FROM python:${PYTHON_VERSION}-slim

RUN groupadd -g 10001 malitan && \
    useradd -u 10001 -g malitan -r -s /usr/sbin/nologin malitan && \
    mkdir /data /data/exports /data/backups && \
    chown -R malitan:malitan /data

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY . /app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

ENV DATA_DIR=/data
ENV EXPORTS_DIR=/data/exports
ENV BACKUPS_DIR=/data/backups

ENV FLASK_APP=flask_app

EXPOSE 5001

USER 10001
CMD ["gunicorn", "-w", "1", "--threads", "2", "--access-logfile", "-", "-b", "0.0.0.0:5001", "flask_app:app"]

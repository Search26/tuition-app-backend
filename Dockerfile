# Two targets from one base. `dev` (the last stage, so the default) is what docker-compose.yml
# builds: dev dependencies and the whole repo, for `make test` and `make lint` in the container.
# `prod` is what compose.prod.yml builds: runtime dependencies and only the code that runs.
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# The transcode worker shells out to ffmpeg/ffprobe; one image serves both api and worker.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /srv
RUN useradd --create-home --uid 1000 app
EXPOSE 8000


FROM base AS prod

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
# seed and make_admin run inside the container on the VPS; upload_video runs on the admin's machine.
COPY scripts/__init__.py scripts/seed.py scripts/make_admin.py ./scripts/

USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]


FROM base AS dev

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project

COPY . .

USER app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

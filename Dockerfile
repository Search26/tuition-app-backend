FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# Dev dependencies are included so `make test` and `make lint` run inside the container.
# The production image in Sprint 8 adds --no-dev.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project

COPY . .

RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

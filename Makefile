.PHONY: up down clean migrate test lint fmt seed admin upload logs worker-logs

COMPOSE := docker compose
RUN_API := $(COMPOSE) exec -T api

up:
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v

migrate:
	$(RUN_API) alembic upgrade head

test:
	$(RUN_API) pytest

lint:
	$(RUN_API) ruff check .
	$(RUN_API) ruff format --check .

fmt:
	$(RUN_API) ruff check --fix .
	$(RUN_API) ruff format .

seed:
	$(RUN_API) python -m scripts.seed

admin:
	@test -n "$(EMAIL)" || (echo "usage: make admin EMAIL=user@example.com" && exit 1)
	$(RUN_API) python -m scripts.make_admin $(EMAIL)

# Runs on the host (not in the api container): the presigned URL points at localhost:9000.
upload:
	@test -n "$(LESSON)" -a -n "$(FILE)" || (echo "usage: make upload LESSON=<lesson uuid> FILE=<path>" && exit 1)
	uv run python -m scripts.upload_video --lesson-id $(LESSON) "$(FILE)"

logs:
	$(COMPOSE) logs -f api

worker-logs:
	$(COMPOSE) logs -f worker

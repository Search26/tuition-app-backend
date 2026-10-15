.PHONY: up down clean migrate test lint fmt seed admin upload logs worker-logs \
	prod-build prod-up prod-migrate prod-ps prod-logs prod-admin prod-seed prod-down backup smoke

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

# Production (on the VPS). See docs/deploy.md. Every target reads .env.prod.
PROD := docker compose --env-file .env.prod -f compose.prod.yml

prod-build:
	$(PROD) build

# Data services first, then migrations, then everything else.
prod-up:
	$(PROD) up -d --wait postgres redis minio
	$(PROD) run --rm api alembic upgrade head
	$(PROD) up -d --wait

prod-migrate:
	$(PROD) run --rm api alembic upgrade head

prod-ps:
	$(PROD) ps

prod-logs:
	$(PROD) logs -f --tail=100 $(SERVICE)

prod-admin:
	@test -n "$(EMAIL)" || (echo "usage: make prod-admin EMAIL=user@example.com" && exit 1)
	$(PROD) exec -T api python -m scripts.make_admin $(EMAIL)

prod-seed:
	$(PROD) exec -T api python -m scripts.seed

prod-down:
	$(PROD) down

backup:
	scripts/backup.sh

smoke:
	@test -n "$(URL)" || (echo "usage: make smoke URL=https://api.yourdomain.com" && exit 1)
	scripts/smoke.sh $(URL)

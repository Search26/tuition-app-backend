.PHONY: up down clean migrate test lint fmt seed admin logs

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

logs:
	$(COMPOSE) logs -f api

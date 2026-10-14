# tuition-app-backend

FastAPI backend for the tuition iOS learning app. Self-hosted with Docker Compose:
API, PostgreSQL 16, Redis, MinIO and nginx.

## Quick start

```bash
cp .env.example .env      # dev values; change them before deploying anywhere
make up                   # build and start everything, waits until healthy
make migrate              # alembic upgrade head
curl -i localhost:8080/health
```

The first `make up` builds MinIO from source (its official image is no longer published),
which takes a few minutes. Later runs use the cached image.

## Make targets

| Target | Does |
| --- | --- |
| `make up` | Build and start the stack |
| `make down` | Stop the stack, keep data |
| `make clean` | Stop the stack and delete the Postgres and MinIO volumes |
| `make migrate` | Run Alembic migrations |
| `make test` | Run pytest against the `tuition_test` database |
| `make lint` / `make fmt` | ruff check and format |
| `make seed` | Load dev data (from Sprint 2) |
| `make admin EMAIL=...` | Make a registered user an admin |
| `make logs` | Follow API logs |

## Layout

- `app/api` routes, `app/services` logic, `app/models` tables, `app/schemas` request and response models
- `app/core` settings and the error envelope, `app/db` and `app/clients` connections
- `alembic/versions` migrations, each with a working `downgrade()`

## Errors

Every error response looks like:

```json
{ "error": { "code": "NOT_FOUND", "message": "Resource not found" } }
```

Validation errors (422, `VALIDATION_ERROR`) add a `details` list of `{field, message}`.

## Auth

- `POST /auth/register` `{email, password, name}` → 201 user
- `POST /auth/login` `{email, password}` → `{access_token, refresh_token, token_type, expires_in}`
- `POST /auth/refresh` `{refresh_token}` → a new pair; each refresh token works once, and reusing
  one revokes every token from that login
- `GET /me` with `Authorization: Bearer <access_token>`

Access tokens last 15 minutes, refresh tokens 30 days. Protected routes depend on
`get_current_user` or `require_admin` from `app/api/deps.py`.

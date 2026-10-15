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
| `make seed` | Load 2 sample courses with 9 lessons (safe to re-run) |
| `make admin EMAIL=...` | Make a registered user an admin |
| `make upload LESSON=... FILE=...` | Upload a source video to a lesson (runs on the host) |
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

## Courses

- `GET /courses?page=1&page_size=20&q=` published courses, newest first; `q` ignores case and
  accents, so `lap trinh` finds "Lập trình"
- `GET /courses/{id}` a published course with its lessons in order; `hls_url` is set only when a
  lesson is `ready`
- `POST /courses/{id}/enroll` (logged in) → 201 the first time, 200 after that

Admin routes live under `/admin` and all require `require_admin`: course CRUD at
`/admin/courses`, lesson create at `/admin/courses/{id}/lessons`, lesson update and delete at
`/admin/lessons/{id}`. A course with enrollments can't be deleted; unpublish it instead.

## Video upload

Source videos go straight from the uploader to a private MinIO bucket; the API only signs the
URL and checks the result. All three calls need an admin token.

1. `POST /admin/lessons/{id}/upload-request` `{content_type, size_bytes}` → 201
   `{asset_id, upload_url, method, headers, expires_at}`. Only `video/mp4` and
   `video/quicktime` up to `UPLOAD_MAX_BYTES` (2 GB); the URL lasts 15 minutes.
2. `PUT upload_url` with the file and exactly the returned `headers`.
3. `POST /admin/lessons/{id}/upload-complete` `{asset_id}` → 200 the media asset, status
   `uploaded`. 409 `UPLOAD_NOT_FOUND` if the file isn't there yet; a file that is too large or
   has a different Content-Type is deleted and the asset marked `failed` (422).

Keys are built by the server: `original/{course_id}/{lesson_id}/{asset_id}.mp4|mov`. A completed
upload replaces the lesson's earlier pending, uploaded or failed assets. Presigned URLs are signed
for `MINIO_PUBLIC_ENDPOINT` (`localhost:9000` in dev), so the CLI runs on the host:

```bash
make admin EMAIL=you@example.com
ADMIN_EMAIL=you@example.com make upload LESSON=<lesson uuid> FILE=sample.mp4
```

# tuition-app-backend

FastAPI backend for the tuition iOS learning app. Self-hosted with Docker Compose:
API, PostgreSQL 16, Redis, MinIO and nginx.

## Quick start

```bash
cp .env.example .env      # dev values; for a real server see docs/deploy.md
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
| `make worker-logs` | Follow transcode worker logs |

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

## Transcoding

A completed upload queues one `transcode` job (arq on Redis) for the `worker` service, which runs
`arq app.worker.WorkerSettings` from the same image as the API and shells out to ffmpeg.

- One ffmpeg run makes H.264/AAC HLS (fMP4 segments, 6 s, keyframe-aligned) at 480p, 720p and
  1080p on the video's short side, never upscaled; portrait videos stay portrait. Smaller sources
  get one rendition at their own size. A thumbnail comes from 10% in (at most 5 s).
- Output goes to `hls/{course_id}/{lesson_id}/{run_id}/` (`master.m3u8`, `v0/`, `v1/`, …,
  `thumbnail.jpg`). Only the finishing transaction points the lesson at it (`hls_path`,
  `thumbnail_path`, `duration_seconds`, status `ready`); then the previous video's prefix and
  original are deleted (the old video's folder after `MEDIA_URL_TTL_SECONDS`, so people already
  watching it finish). A lesson with a live video keeps playing it while a new one encodes.
- Asset status: `uploaded → processing → ready | failed`. An ffmpeg or storage error is retried
  once after `TRANSCODE_RETRY_DELAY_SECONDS`; a file ffprobe can't read fails at once. `error_msg`
  holds the reason and the full ffmpeg log is kept at `logs/transcode/{asset_id}/{attempt}.log`.
  A lesson with no live video goes `processing`, then back to `draft` on failure.
- `GET /admin/lessons/{id}/assets` lists a lesson's assets, newest first.
- `POST /admin/assets/{id}/retranscode` → 202, re-queues a `ready` or `failed` asset. 409
  `ASSET_PROCESSING` while it runs, 409 `UPLOAD_NOT_COMPLETE` for a `pending` one, 503
  `QUEUE_UNAVAILABLE` if Redis is down.

Settings: `WORKER_MAX_JOBS` (1), `TRANSCODE_TIMEOUT_SECONDS` (3600), `TRANSCODE_THREADS` (0 lets
ffmpeg decide), `TRANSCODE_TMP_DIR`, `TRANSCODE_RETRY_DELAY_SECONDS` (30). Tests use Redis
database 1 (`TEST_REDIS_URL`) so the running worker never takes their jobs.

## Playback (signed HLS)

`GET /lessons/{id}/stream` (logged in, enrolled or admin) → `{url, expires_at, duration_seconds}`.
403 `NOT_ENROLLED`, 404 `LESSON_NOT_FOUND`, 409 `VIDEO_NOT_READY`. The URL is valid for
`MEDIA_URL_TTL_SECONDS` (4 h):

```
{MEDIA_BASE_URL}/v/{exp}/{sig}/hls/{course}/{lesson}/{run}/master.m3u8
```

The token sits in the path in front of the video's folder, and the playlists use relative URIs,
so every variant playlist, init file and segment the player asks for carries it; no playlist is
rewritten. `sig` is an HMAC-SHA256 over the kind, expiry and folder with `MEDIA_SIGNING_SECRET`
(not the JWT secret). Lesson JSON has a signed `thumbnail_url` (`/media/f/...`, one file, stable
for an hour); there is no `hls_url`.

nginx serves `/media/`: for every request it asks the API (`/internal/media-auth`, an HMAC check
with no database call) and gets back a 60-second presigned path, then fetches that from MinIO. The
client's path never reaches MinIO and the bucket stays private. A wrong, expired or out-of-scope
token is 403, a missing file 404, and `/internal/` is 404 from outside.

Try it in a browser: Safari plays the `url` directly. For other browsers, serve the dev player from
the `CORS_ORIGINS` origin:

```bash
python -m http.server 3000 -d scripts   # then open http://localhost:3000/dev-player.html
```

## Progress sync

`PUT /me/progress` takes `{"items": [{lesson_id, position_seconds, completed, client_updated_at}]}`,
1 to 500 items, and answers 200 with one result per item, in order: `applied`, `skipped_stale`
or `rejected` (with `reason` `LESSON_NOT_FOUND` or `NOT_ENROLLED`), plus the row the server now
holds. Timestamps must carry an offset (422 otherwise).

The merge is one `INSERT ... ON CONFLICT DO UPDATE`: the position with the newest
`client_updated_at` wins, and `completed` stays true once any item reported it, so resending a
batch, or receiving items in any order, ends in the same row. The server decides `completed`
itself (position ≥ 90% of the lesson's duration) and clamps a client clock more than
`PROGRESS_MAX_CLOCK_SKEW_SECONDS` (1 day) ahead to server time.

- `GET /me/progress?course_id=` → `{items: [...]}`, my rows for that course in lesson order. 403
  `NOT_ENROLLED`, 404 `COURSE_NOT_FOUND`.
- `GET /me/courses` → a page of my enrolled, published courses with `lesson_count`,
  `completed_count`, `percent_complete`, `enrolled_at` and `last_watched_at`, computed per request.

```bash
curl -X PUT localhost:8080/me/progress -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"items":[{"lesson_id":"<id>","position_seconds":42.5,"client_updated_at":"2026-10-15T10:00:00+07:00"}]}'
```

## Logs, request ids and rate limits

Every response carries `X-Request-ID`. It comes from the proxy in front (Caddy in production,
nginx in dev) when that sends a safe one, otherwise the API makes one. Every log line written
while handling the request carries the same id, and so do the worker's lines for a transcode the
request enqueued.

`LOG_FORMAT=json` writes one JSON object per line (`ts`, `level`, `logger`, `msg`,
`request_id`, plus fields such as `status`, `duration_ms`, `asset_id`); `text` (the dev default)
is the same with a readable layout. The API writes one `app.access` line per request; `/health`
and the per-file `/internal/media-auth` checks log at DEBUG only. Passwords, tokens and presigned
URLs are never logged, and settings errors never echo their input.

nginx logs JSON to stdout with the media signature replaced by `-`, and limits each client IP:

| Paths | Rate | Burst |
| --- | --- | --- |
| `/auth/` | 5 req/s | 10 |
| other API paths | 20 req/s | 60 |
| `/media/` | 50 req/s | 200 |

Over the limit is `429` with `Retry-After: 1` and `{"error":{"code":"RATE_LIMITED",...}}`.
`/health` is never limited. With `APP_ENV=prod` the API refuses to start while any secret still
contains `change-me`, `MEDIA_BASE_URL` isn't `https://`, or `CORS_ORIGINS` lists localhost.

## Deploying

[docs/deploy.md](docs/deploy.md) takes one VPS from nothing to HTTPS: `compose.prod.yml` (Caddy with
Let's Encrypt in front of the same nginx, API, worker, Postgres, Redis and MinIO),
`scripts/gen-prod-env.sh` for secrets, nightly `scripts/backup.sh`, `scripts/restore.sh` for drills,
and `make smoke URL=https://...` to check a live server end to end.

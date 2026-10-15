"""JSON log lines, the request id, and what must never be logged (Sprint 8 design doc)."""

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import queue
from app.core.logging import JsonFormatter, request_id_var
from app.models import Enrollment, LessonStatus
from app.services import transcode_job
from tests.auth_helpers import PASSWORD, bearer, register_and_login
from tests.factories import auth_headers, make_course, make_lesson, make_user


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    def parsed(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.lines]


@pytest.fixture
def logs(app: FastAPI) -> Iterator[_Lines]:
    """Every log line, as the production formatter writes it. Depends on `app` so it's attached
    after create_app() configures logging."""
    root = logging.getLogger()
    handler, level = _Lines(), root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield handler
    root.removeHandler(handler)
    root.setLevel(level)


def test_json_line_has_the_fields_and_extras() -> None:
    record = logging.makeLogRecord(
        {
            "name": "app.x",
            "levelname": "INFO",
            "msg": "hello %s",
            "args": ("there",),
            "lesson_id": "L1",
        }
    )
    token = request_id_var.set("req-1")
    try:
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert line["msg"] == "hello there"
    assert (line["level"], line["logger"], line["request_id"], line["lesson_id"]) == (
        "INFO",
        "app.x",
        "req-1",
        "L1",
    )
    assert line["ts"].endswith("+00:00")


async def test_valid_incoming_id_is_kept_and_echoed(client: AsyncClient, logs: _Lines) -> None:
    response = await client.get("/courses", headers={"X-Request-ID": "caddy-3f2a.9_x"})

    assert response.headers["x-request-id"] == "caddy-3f2a.9_x"
    access = [line for line in logs.parsed() if line["logger"] == "app.access"]
    assert access[-1]["request_id"] == "caddy-3f2a.9_x"
    assert (access[-1]["method"], access[-1]["path"], access[-1]["status"]) == (
        "GET",
        "/courses",
        200,
    )
    assert isinstance(access[-1]["duration_ms"], float)
    assert request_id_var.get() is None  # cleared once the request is done


@pytest.mark.parametrize("incoming", [None, "", "has space", "x" * 65, 'quote"', "a/b"])
async def test_missing_or_unsafe_id_is_replaced(client: AsyncClient, incoming: str | None) -> None:
    headers = {"X-Request-ID": incoming} if incoming is not None else {}

    response = await client.get("/courses", headers=headers)

    rid = response.headers["x-request-id"]
    assert rid != incoming
    assert len(rid) == 32 and int(rid, 16) >= 0


async def test_error_responses_carry_the_id_too(
    app: FastAPI, client: AsyncClient, logs: _Lines
) -> None:
    async def boom() -> None:
        raise RuntimeError("kaboom")

    app.add_api_route("/boom", boom)

    not_found = await client.get("/nope", headers={"X-Request-ID": "rid-404"})
    crashed = await client.get("/boom", headers={"X-Request-ID": "rid-500"})

    assert not_found.headers["x-request-id"] == "rid-404"
    assert crashed.status_code == 500
    assert crashed.headers["x-request-id"] == "rid-500"
    errors = [line for line in logs.parsed() if line["level"] == "ERROR"]
    assert errors[-1]["request_id"] == "rid-500"
    assert "kaboom" in errors[-1]["exc"]
    access = [line for line in logs.parsed() if line["logger"] == "app.access"]
    assert (access[-1]["status"], access[-1]["request_id"]) == (500, "rid-500")


async def test_health_polls_stay_out_of_the_info_log(client: AsyncClient, logs: _Lines) -> None:
    await client.get("/health")

    access = [line for line in logs.parsed() if line["logger"] == "app.access"]
    assert access and all(line["level"] == "DEBUG" for line in access)


async def test_enqueued_transcode_carries_the_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class FakeQueue:
        async def enqueue_job(self, *args: Any, **kwargs: Any) -> None:
            calls.append((args, kwargs))

    async def fake_get_queue() -> FakeQueue:
        return FakeQueue()

    monkeypatch.setattr(queue, "get_queue", fake_get_queue)
    asset_id = uuid.uuid4()
    token = request_id_var.set("rid-upload")
    try:
        await queue.enqueue_transcode(asset_id)
    finally:
        request_id_var.reset(token)

    ((args, kwargs),) = calls
    assert args == ("transcode", str(asset_id))
    assert kwargs["request_id"] == "rid-upload"


@pytest.mark.usefixtures("db")
async def test_worker_lines_carry_the_request_id_of_the_upload(logs: _Lines) -> None:
    asset_id = str(uuid.uuid4())  # unknown asset: the job skips, but still logs start and end

    result = await transcode_job.transcode({"job_try": 1}, asset_id, request_id="rid-upload")

    assert result == "skipped"
    lines = [line for line in logs.parsed() if line["logger"] == "app.services.transcode_job"]
    assert [line["msg"] for line in lines] == ["Transcode started", "Transcode skipped"]
    assert all(
        line["request_id"] == "rid-upload" and line["asset_id"] == asset_id for line in lines
    )
    assert lines[-1]["result"] == "skipped" and "duration_s" in lines[-1]


@pytest.mark.usefixtures("bucket")
async def test_no_password_token_or_signed_url_reaches_the_log(
    client: AsyncClient, db: AsyncSession, logs: _Lines
) -> None:
    tokens = await register_and_login(client, "logcheck@example.com")
    refreshed = (
        await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).json()
    await client.post(
        "/auth/login", json={"email": "logcheck@example.com", "password": "wrong-pass-123"}
    )

    course = await make_course(db)
    run = f"hls/{course.id}/{uuid.uuid4()}/{uuid.uuid4()}/"
    lesson = await make_lesson(
        db, course, 1, status=LessonStatus.READY, hls_path=run + "master.m3u8", duration_seconds=10
    )
    admin = await make_user(db, is_admin=True)
    student = await make_user(db)
    db.add(Enrollment(user_id=student.id, course_id=course.id))
    await db.commit()
    stream = (
        await client.get(f"/lessons/{lesson.id}/stream", headers=auth_headers(student))
    ).json()
    ticket = (
        await client.post(
            f"/admin/lessons/{lesson.id}/upload-request",
            json={"content_type": "video/mp4", "size_bytes": 1024},
            headers=auth_headers(admin),
        )
    ).json()
    await client.get("/me", headers=bearer(refreshed["access_token"]))

    text = "\n".join(logs.lines)
    secrets = [
        PASSWORD,
        "wrong-pass-123",
        tokens["access_token"],
        tokens["refresh_token"],
        refreshed["access_token"],
        refreshed["refresh_token"],
        stream["url"].split("/")[6],  # the signature segment
        ticket["upload_url"].split("?", 1)[1],  # the presigned query
    ]
    assert logs.lines, "expected some log lines to check"
    for secret in secrets:
        assert secret not in text

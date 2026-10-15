"""Upload a source video to a lesson through the API: `make upload LESSON=<uuid> FILE=<path>`.

Runs on the host: the presigned URL points at MINIO_PUBLIC_ENDPOINT (localhost:9000 in dev).
Credentials come from ADMIN_EMAIL / ADMIN_PASSWORD or a prompt; they are never printed.
"""

import argparse
import getpass
import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx

CONTENT_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime"}
CHUNK_BYTES = 1024 * 1024


class UploadError(Exception):
    pass


def _check(response: httpx.Response, expected: int) -> dict:
    if response.status_code != expected:
        try:
            error = response.json()["error"]
            detail = f"{error['code']}: {error['message']}"
        except (ValueError, KeyError, TypeError):
            detail = response.text[:200]
        raise UploadError(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code} {detail}"
        )
    return response.json()


def _chunks(path: Path, size: int) -> Iterator[bytes]:
    sent = 0
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_BYTES):
            sent += len(chunk)
            print(f"\r  uploading {sent * 100 // size}%", end="", file=sys.stderr, flush=True)
            yield chunk
    print(file=sys.stderr)


def upload(api: str, lesson_id: uuid.UUID, path: Path, email: str, password: str) -> dict:
    content_type = CONTENT_TYPES.get(path.suffix.lower())
    if content_type is None:
        raise UploadError(f"Unsupported file type {path.suffix!r}; use .mp4 or .mov")
    size = path.stat().st_size

    with httpx.Client(base_url=api, timeout=30) as client:
        tokens = _check(
            client.post("/auth/login", json={"email": email, "password": password}), 200
        )
        auth = {"Authorization": f"Bearer {tokens['access_token']}"}
        ticket = _check(
            client.post(
                f"/admin/lessons/{lesson_id}/upload-request",
                json={"content_type": content_type, "size_bytes": size},
                headers=auth,
            ),
            201,
        )
        print(f"asset {ticket['asset_id']}: URL valid until {ticket['expires_at']}")

        # No read/write timeout: a 2 GB file takes a while. Content-Length avoids chunked encoding.
        headers = ticket["headers"] | {"Content-Length": str(size)}
        put = httpx.put(
            ticket["upload_url"],
            content=_chunks(path, size),
            headers=headers,
            timeout=httpx.Timeout(30, write=None, read=None),
        )
        if put.status_code != 200:
            raise UploadError(f"PUT to storage -> {put.status_code} {put.text[:200]}")

        return _check(
            client.post(
                f"/admin/lessons/{lesson_id}/upload-complete",
                json={"asset_id": ticket["asset_id"]},
                headers=auth,
            ),
            200,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lesson-id", type=uuid.UUID, required=True)
    parser.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8080"))
    parser.add_argument("file", type=Path)
    args = parser.parse_args()

    if not args.file.is_file():
        sys.exit(f"error: {args.file} is not a file")
    email = os.environ.get("ADMIN_EMAIL") or input("Admin email: ")
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")

    try:
        asset = upload(args.api, args.lesson_id, args.file, email, password)
    except (UploadError, httpx.HTTPError) as exc:
        sys.exit(f"error: {exc}")
    print(f"asset {asset['id']}: {asset['status']}, {asset['size_bytes']} bytes")


if __name__ == "__main__":
    main()

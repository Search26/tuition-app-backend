"""nginx's auth subrequest for /media/. nginx returns 404 for /internal/ from outside."""

from fastapi import APIRouter, Request, Response, status

from app.clients import storage
from app.core.config import get_settings
from app.services import media_urls

router = APIRouter(prefix="/internal", include_in_schema=False)

# Long enough for nginx to start the fetch; a Range request or a retry asks again.
_PRESIGN_SECONDS = 60


@router.get("/media-auth")
async def media_auth(request: Request) -> Response:
    """204 + the presigned MinIO path nginx should fetch, or a bare 403.

    No database or network call: an HMAC and a local presign, run once per playlist or segment.
    """
    try:
        key = media_urls.verify(request.headers.get("x-original-uri", ""))
    except media_urls.InvalidMediaPath:
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    headers = {"X-Media-Path": storage.presign_get_path(key, _PRESIGN_SECONDS)}
    origin = request.headers.get("origin", "").rstrip("/")
    if origin and origin in get_settings().cors_origin_list:
        headers["X-Media-CORS"] = origin
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=headers)

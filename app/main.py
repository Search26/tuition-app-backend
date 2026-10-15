from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.clients.queue import close_queue
from app.clients.redis import get_redis
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware
from app.db.session import get_engine
from app.schemas.error import ErrorResponse


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_settings()  # fail fast on missing env vars
    yield
    await get_engine().dispose()
    await get_redis().aclose()
    await close_queue()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_format, settings.log_level)
    app = FastAPI(
        title="Tuition App API",
        version="0.1.0",
        lifespan=lifespan,
        responses={"default": {"model": ErrorResponse}},
    )
    register_exception_handlers(app)
    origins = settings.cors_origin_list
    if origins:
        # Dev web pages (hls.js) calling /lessons/{id}/stream; /media/ CORS is set by nginx.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )
    # Added last, so it wraps CORS too and every response carries the id.
    app.add_middleware(RequestIdMiddleware)
    app.include_router(api_router)
    return app


app = create_app()

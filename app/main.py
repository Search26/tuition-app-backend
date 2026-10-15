from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.clients.redis import get_redis
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.db.session import get_engine
from app.schemas.error import ErrorResponse


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_settings()  # fail fast on missing env vars
    yield
    await get_engine().dispose()
    await get_redis().aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Tuition App API",
        version="0.1.0",
        lifespan=lifespan,
        responses={"default": {"model": ErrorResponse}},
    )
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()

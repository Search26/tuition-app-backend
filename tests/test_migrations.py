import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.db.session import get_engine


@pytest.fixture(autouse=True)
async def _fresh_app_connections() -> AsyncIterator[None]:
    yield
    # Downgrades drop and recreate enum types; pooled connections cache the old type OIDs.
    await get_engine().dispose()


def _alembic_config() -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = os.environ["TEST_DATABASE_URL"]
    return config


async def test_upgrade_downgrade_upgrade_round_trip() -> None:
    config = _alembic_config()

    # env.py calls asyncio.run, so run each command in a worker thread with its own loop.
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")


async def test_courses_migration_downgrade_removes_everything() -> None:
    config = _alembic_config()
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.downgrade, config, "0002")

    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        async with engine.connect() as conn:
            tables = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_tables "
                    "WHERE tablename IN ('courses', 'lessons', 'enrollments')"
                )
            )
            extensions = await conn.scalar(
                text("SELECT count(*) FROM pg_extension WHERE extname IN ('unaccent', 'pg_trgm')")
            )
            enum_type = await conn.scalar(
                text("SELECT count(*) FROM pg_type WHERE typname = 'lesson_status'")
            )
    finally:
        await engine.dispose()
        await asyncio.to_thread(command.upgrade, config, "head")

    assert (tables, extensions, enum_type) == (0, 0, 0)


async def test_media_assets_migration_downgrade_removes_table_and_enum() -> None:
    config = _alembic_config()
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.downgrade, config, "0003")

    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        async with engine.connect() as conn:
            table = await conn.scalar(
                text("SELECT count(*) FROM pg_tables WHERE tablename = 'media_assets'")
            )
            enum_type = await conn.scalar(
                text("SELECT count(*) FROM pg_type WHERE typname = 'media_asset_status'")
            )
    finally:
        await engine.dispose()
        await asyncio.to_thread(command.upgrade, config, "head")

    assert (table, enum_type) == (0, 0)

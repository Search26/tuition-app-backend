import asyncio
import os

from alembic.config import Config

from alembic import command


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

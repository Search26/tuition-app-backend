"""Give an existing user admin rights: `python -m scripts.make_admin user@example.com`."""

import asyncio
import sys

from sqlalchemy import update

from app.db.session import get_engine, get_sessionmaker
from app.models import User


async def make_admin(email: str) -> bool:
    async with get_sessionmaker()() as session:
        result = await session.execute(
            update(User).where(User.email == email.strip().lower()).values(is_admin=True)
        )
        await session.commit()
    await get_engine().dispose()
    return result.rowcount > 0  # type: ignore[attr-defined]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m scripts.make_admin <email>")
    email = sys.argv[1]
    if not asyncio.run(make_admin(email)):
        sys.exit(f"No user with email {email}; register first.")
    print(f"{email} is now an admin.")


if __name__ == "__main__":
    main()

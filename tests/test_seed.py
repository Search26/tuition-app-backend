import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, Lesson
from scripts.seed import seed


@pytest.mark.usefixtures("db")
async def test_seed_twice_creates_each_row_once(db: AsyncSession) -> None:
    await seed(db)
    await seed(db)

    assert await db.scalar(select(func.count()).select_from(Course)) == 2
    assert await db.scalar(select(func.count()).select_from(Lesson)) == 9
    assert await db.scalar(select(func.count()).where(Course.is_published.is_(False))) == 0

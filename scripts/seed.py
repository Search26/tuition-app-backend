"""Seed 2 published courses and 9 lessons for local development: `make seed`.

Additive and idempotent: fixed ids plus ON CONFLICT DO NOTHING, so running it again changes
nothing and never touches rows an admin has edited. Lessons stay draft until Sprint 4 adds video.
"""

import asyncio
import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_engine, get_sessionmaker
from app.models import Course, Lesson

_NAMESPACE = uuid.UUID("5b0d7c1e-2f3a-4c5d-9e8f-0a1b2c3d4e5f")

COURSES: list[tuple[str, str, list[str]]] = [
    (
        "Lập trình Python cơ bản",
        "Học Python từ con số 0: cú pháp, kiểu dữ liệu, điều kiện, vòng lặp và hàm.",
        ["Cài đặt môi trường", "Biến và kiểu dữ liệu", "Câu lệnh điều kiện", "Vòng lặp", "Hàm"],
    ),
    (
        "Tiếng Anh giao tiếp",
        "Những mẫu câu tiếng Anh dùng hằng ngày cho người mới bắt đầu.",
        ["Chào hỏi", "Giới thiệu bản thân", "Hỏi đường", "Đặt món ăn"],
    ),
]


def _id(*parts: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, "/".join(parts))


async def seed(session: AsyncSession) -> None:
    for title, description, lessons in COURSES:
        course_id = _id(title)
        await session.execute(
            insert(Course)
            .values(id=course_id, title=title, description=description, is_published=True)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        for position, lesson_title in enumerate(lessons, start=1):
            await session.execute(
                insert(Lesson)
                .values(
                    id=_id(title, lesson_title),
                    course_id=course_id,
                    title=lesson_title,
                    position=position,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
    await session.commit()


async def _main() -> None:
    async with get_sessionmaker()() as session:
        await seed(session)
    await get_engine().dispose()
    lesson_total = sum(len(lessons) for _, _, lessons in COURSES)
    print(f"Seeded {len(COURSES)} courses and {lesson_total} lessons (existing rows kept).")


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()

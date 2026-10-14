import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession
from app.schemas.course import AdminCourse, AdminCourseSummary, CourseCreate, CourseUpdate
from app.schemas.lesson import AdminLesson, LessonCreate
from app.schemas.pagination import CourseListParams, Page
from app.services import courses as course_service
from app.services import lessons as lesson_service

router = APIRouter(prefix="/courses")


@router.get("")
async def list_courses(
    params: Annotated[CourseListParams, Query()], session: DbSession
) -> Page[AdminCourseSummary]:
    rows, total = await course_service.list_courses(session, params, params.q, published_only=False)
    items = [AdminCourseSummary.from_row(course, count) for course, count in rows]
    return Page(items=items, page=params.page, page_size=params.page_size, total=total)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_course(body: CourseCreate, session: DbSession) -> AdminCourse:
    course = await course_service.create_course(session, body.model_dump())
    return AdminCourse.model_validate(course)


@router.get("/{course_id}")
async def get_course(course_id: uuid.UUID, session: DbSession) -> AdminCourse:
    course = await course_service.get_course(session, course_id, published_only=False)
    return AdminCourse.model_validate(course)


@router.patch("/{course_id}")
async def update_course(
    course_id: uuid.UUID, body: CourseUpdate, session: DbSession
) -> AdminCourse:
    course = await course_service.update_course(
        session, course_id, body.model_dump(exclude_unset=True)
    )
    return AdminCourse.model_validate(course)


@router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(course_id: uuid.UUID, session: DbSession) -> None:
    await course_service.delete_course(session, course_id)


@router.post("/{course_id}/lessons", status_code=status.HTTP_201_CREATED)
async def create_lesson(
    course_id: uuid.UUID, body: LessonCreate, session: DbSession
) -> AdminLesson:
    lesson = await lesson_service.create_lesson(session, course_id, body.model_dump())
    return AdminLesson.model_validate(lesson)

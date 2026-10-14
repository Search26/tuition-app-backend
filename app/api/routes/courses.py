"""Public course reads and enrollment. Never returns an unpublished course."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.course import CourseDetail, CourseSummary
from app.schemas.enrollment import EnrollmentOut
from app.schemas.pagination import CourseListParams, Page
from app.services import courses as course_service
from app.services import enrollments as enrollment_service

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("")
async def list_courses(
    params: Annotated[CourseListParams, Query()], session: DbSession
) -> Page[CourseSummary]:
    rows, total = await course_service.list_courses(session, params, params.q, published_only=True)
    items = [CourseSummary.from_row(course, count) for course, count in rows]
    return Page(items=items, page=params.page, page_size=params.page_size, total=total)


@router.get("/{course_id}")
async def get_course(course_id: uuid.UUID, session: DbSession) -> CourseDetail:
    course = await course_service.get_course(session, course_id, published_only=True)
    return CourseDetail.model_validate(course)


@router.post(
    "/{course_id}/enroll",
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": EnrollmentOut, "description": "Already enrolled"}},
)
async def enroll(
    course_id: uuid.UUID, user: CurrentUser, session: DbSession, response: Response
) -> EnrollmentOut:
    enrolled_at, created = await enrollment_service.enroll(session, user.id, course_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return EnrollmentOut(course_id=course_id, enrolled_at=enrolled_at)

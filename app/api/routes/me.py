import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.pagination import Page, PageParams
from app.schemas.progress import MyCourse, ProgressBatch, ProgressBatchResult, ProgressList
from app.schemas.user import UserOut
from app.services import progress as progress_service

router = APIRouter(tags=["me"])


@router.get("/me")
async def read_me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.put("/me/progress")
async def sync_progress(
    batch: ProgressBatch, user: CurrentUser, session: DbSession
) -> ProgressBatchResult:
    """Merge a batch from an offline client; each item is applied, skipped_stale or rejected."""
    results = await progress_service.sync(session, user, batch)
    return ProgressBatchResult(results=results)


@router.get("/me/progress")
async def read_progress(
    course_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> ProgressList:
    return ProgressList(items=await progress_service.course_progress(session, user, course_id))


@router.get("/me/courses")
async def my_courses(
    params: Annotated[PageParams, Query()], user: CurrentUser, session: DbSession
) -> Page[MyCourse]:
    items, total = await progress_service.my_courses(session, user, params)
    return Page(items=items, page=params.page, page_size=params.page_size, total=total)

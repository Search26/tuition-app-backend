"""Admin routes. The guard sits on this router, so no admin route can be added without it."""

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.routes.admin import assets, courses, lessons

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
router.include_router(courses.router)
router.include_router(lessons.router)
router.include_router(assets.router)

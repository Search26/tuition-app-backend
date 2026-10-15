from fastapi import APIRouter

from app.api.routes import admin, auth, courses, health, internal, lessons, me

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(courses.router)
api_router.include_router(lessons.router)
api_router.include_router(admin.router)
api_router.include_router(internal.router)

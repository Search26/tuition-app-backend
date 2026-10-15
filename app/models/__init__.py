"""Import every model module here so Alembic autogenerate can see it."""

from app.db.base import Base
from app.models.course import Course, Lesson, LessonStatus
from app.models.enrollment import Enrollment
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = ["Base", "Course", "Enrollment", "Lesson", "LessonStatus", "RefreshToken", "User"]

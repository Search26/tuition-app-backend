import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, computed_field, field_validator

from app.models import Course
from app.schemas._text import Description, Title, reject_explicit_null
from app.schemas.lesson import AdminLesson, LessonPublic


class _CourseBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    created_at: datetime


class CourseSummary(_CourseBase):
    lesson_count: int

    @classmethod
    def from_row(cls, course: Course, lesson_count: int) -> Self:
        """Build from a list query row, where the count comes from a subquery."""
        names = cls.model_fields.keys() - {"lesson_count"}
        return cls(**{name: getattr(course, name) for name in names}, lesson_count=lesson_count)


class CourseDetail(_CourseBase):
    lessons: list[LessonPublic]


class AdminCourseSummary(CourseSummary):
    is_published: bool
    updated_at: datetime


class AdminCourse(_CourseBase):
    is_published: bool
    updated_at: datetime
    lessons: list[AdminLesson]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def lesson_count(self) -> int:
        return len(self.lessons)


class CourseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    description: Description = ""
    is_published: bool = False


class CourseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Description | None = None
    is_published: bool | None = None

    _no_null = field_validator("title", "description", "is_published", mode="before")(
        reject_explicit_null
    )

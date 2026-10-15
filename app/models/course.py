import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LessonStatus(enum.StrEnum):
    DRAFT = "draft"
    PROCESSING = "processing"
    READY = "ready"


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        Index("ix_courses_is_published_created_at", "is_published", "created_at"),
        # Trigram index for accent-insensitive title search; created by migration 0003. Declared
        # here so autogenerate doesn't propose dropping it.
        Index(
            "ix_courses_title_search",
            text("f_unaccent(lower(title)) gin_trgm_ops"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, server_default="", default="")
    is_published: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lessons: Mapped[list["Lesson"]] = relationship(
        back_populates="course",
        order_by="(Lesson.position, Lesson.created_at, Lesson.id)",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (
        Index("ix_lessons_course_id_position", "course_id", "position"),
        CheckConstraint("position >= 1", name="position_positive"),
        CheckConstraint("duration_seconds >= 0", name="duration_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, server_default="", default="")
    # The plan calls this `order`; renamed because ORDER is a reserved SQL word.
    position: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[LessonStatus] = mapped_column(
        Enum(LessonStatus, name="lesson_status", values_callable=lambda e: [m.value for m in e]),
        server_default=LessonStatus.DRAFT.value,
        default=LessonStatus.DRAFT,
    )
    # Storage key of master.m3u8, written by the transcode worker in Sprint 4.
    hls_path: Mapped[str | None] = mapped_column(Text)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    course: Mapped[Course] = relationship(back_populates="lessons")

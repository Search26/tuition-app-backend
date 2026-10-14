"""Courses, lessons, enrollments, and accent-insensitive title search.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


lesson_status = postgresql.ENUM("draft", "processing", "ready", name="lesson_status")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # unaccent() is only STABLE, so it can't back an index; this wrapper pins the dictionary.
    op.execute(
        """
        CREATE FUNCTION f_unaccent(text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
        """
    )

    op.create_table(
        "courses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_courses")),
    )
    op.create_index("ix_courses_is_published_created_at", "courses", ["is_published", "created_at"])
    op.execute(
        "CREATE INDEX ix_courses_title_search ON courses "
        "USING gin (f_unaccent(lower(title)) gin_trgm_ops)"
    )

    lesson_status.create(op.get_bind())
    op.create_table(
        "lessons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="lesson_status", create_type=False),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("hls_path", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("position >= 1", name=op.f("ck_lessons_position_positive")),
        sa.CheckConstraint("duration_seconds >= 0", name=op.f("ck_lessons_duration_non_negative")),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_lessons_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lessons")),
    )
    op.create_index("ix_lessons_course_id_position", "lessons", ["course_id", "position"])

    op.create_table(
        "enrollments",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_enrollments_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_enrollments_course_id_courses"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", "course_id", name=op.f("pk_enrollments")),
    )
    op.create_index(op.f("ix_enrollments_course_id"), "enrollments", ["course_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_enrollments_course_id"), table_name="enrollments")
    op.drop_table("enrollments")
    op.drop_index("ix_lessons_course_id_position", table_name="lessons")
    op.drop_table("lessons")
    lesson_status.drop(op.get_bind())
    op.drop_index("ix_courses_title_search", table_name="courses")
    op.drop_index("ix_courses_is_published_created_at", table_name="courses")
    op.drop_table("courses")
    op.execute("DROP FUNCTION f_unaccent(text)")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS unaccent")

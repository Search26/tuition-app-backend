"""Media assets: a lesson's source video in MinIO.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

media_asset_status = postgresql.ENUM(
    "pending", "uploaded", "processing", "ready", "failed", name="media_asset_status"
)


def upgrade() -> None:
    media_asset_status.create(op.get_bind())
    op.create_table(
        "media_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("lesson_id", sa.Uuid(), nullable=False),
        sa.Column("original_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="media_asset_status", create_type=False),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_media_assets_size_non_negative")),
        sa.ForeignKeyConstraint(
            ["lesson_id"],
            ["lessons.id"],
            name=op.f("fk_media_assets_lesson_id_lessons"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_assets")),
        sa.UniqueConstraint("original_key", name=op.f("uq_media_assets_original_key")),
    )
    op.create_index(
        "ix_media_assets_lesson_id_created_at", "media_assets", ["lesson_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_media_assets_lesson_id_created_at", table_name="media_assets")
    op.drop_table("media_assets")
    media_asset_status.drop(op.get_bind())

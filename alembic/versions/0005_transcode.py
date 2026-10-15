"""Transcode: output prefix and attempt count on media assets, lesson thumbnail.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("output_prefix", sa.Text(), nullable=True))
    op.add_column(
        "media_assets",
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("lessons", sa.Column("thumbnail_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lessons", "thumbnail_path")
    op.drop_column("media_assets", "attempts")
    op.drop_column("media_assets", "output_prefix")

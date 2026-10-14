import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RefreshToken(Base):
    """One issued refresh token. Only the SHA-256 of its secret is stored."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Every token rotated from the same login shares a family, so reuse can revoke them all.
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(CHAR(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

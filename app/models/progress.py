import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Double, ForeignKey, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Progress(Base):
    """How far a user got in a lesson. Merged by client timestamp; see services/progress.py."""

    __tablename__ = "progress"
    __table_args__ = (CheckConstraint("position_seconds >= 0", name="position_non_negative"),)

    # The composite primary key is the unique (user_id, lesson_id) the upsert conflicts on.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Indexed so deleting a lesson can find its progress rows to cascade.
    lesson_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    position_seconds: Mapped[float] = mapped_column(Double)
    completed: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)
    # The merge clock: the device's time of the change, not the server's.
    client_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

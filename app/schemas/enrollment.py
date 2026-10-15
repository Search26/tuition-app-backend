import uuid
from datetime import datetime

from pydantic import BaseModel


class EnrollmentOut(BaseModel):
    course_id: uuid.UUID
    enrolled_at: datetime

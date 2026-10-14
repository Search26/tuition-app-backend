from pydantic import BaseModel, Field

from app.schemas._text import SearchQuery


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int


class CourseListParams(PageParams):
    # Empty or whitespace-only means no filter.
    q: SearchQuery | None = None

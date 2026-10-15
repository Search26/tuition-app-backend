from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.schemas.user import UserOut

router = APIRouter(tags=["me"])


@router.get("/me")
async def read_me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)

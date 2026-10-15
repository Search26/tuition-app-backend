import uuid

from fastapi import APIRouter, status

from app.api.deps import DbSession
from app.schemas.media import MediaAssetOut
from app.services import assets as asset_service

router = APIRouter(prefix="/assets")


@router.post("/{asset_id}/retranscode", status_code=status.HTTP_202_ACCEPTED)
async def retranscode(asset_id: uuid.UUID, session: DbSession) -> MediaAssetOut:
    return MediaAssetOut.model_validate(await asset_service.retranscode(session, asset_id))

from __future__ import annotations

from fastapi import APIRouter, Response

from app.models import VisionStatusSnapshot
from app.vision_diagnostics import probe_vision_status

router = APIRouter()


@router.get("/vision-status", response_model=VisionStatusSnapshot)
async def get_vision_status(response: Response) -> VisionStatusSnapshot:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return await probe_vision_status()

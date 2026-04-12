from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.job_manager import get_job, get_slide_image_path
from app.models import FinalElement, GuideAnnotation
from app.session_store import (
    load_revision,
    load_session,
    load_slide_session,
    save_final_elements,
    save_guides,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
# asset_id is used as a filename component under output/<job>/assets/.
# Restrict to a safe charset to prevent path traversal from user-supplied
# FinalElement.asset_id values posted via /final-elements.
_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class GuidesPayload(BaseModel):
    guides: list[GuideAnnotation] = Field(default_factory=list)


class FinalElementsPayload(BaseModel):
    final_elements: list[FinalElement] = Field(default_factory=list)


def _validate_job_id(job_id: str) -> None:
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")


def _require_job(job_id: str):
    _validate_job_id(job_id)
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def _require_slide(job_id: str, slide_num: int):
    if slide_num < 1:
        raise HTTPException(status_code=404, detail="Slide not found.")
    slide = load_slide_session(job_id, slide_num)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide session not found.")
    return slide


@router.get("/session/{job_id}")
async def get_session(job_id: str) -> dict:
    _require_job(job_id)
    try:
        session = load_session(job_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load session %s", job_id)
        raise HTTPException(status_code=500, detail="세션을 불러오지 못했습니다.") from exc
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session.model_dump()


@router.get("/slide/{job_id}/{slide_num}")
async def get_slide(job_id: str, slide_num: int) -> dict:
    _require_job(job_id)
    slide = _require_slide(job_id, slide_num)
    try:
        latest_revision = (
            load_revision(job_id, slide_num, slide.latest_revision_id)
            if slide.latest_revision_id
            else None
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load latest revision for %s/%s", job_id, slide_num)
        raise HTTPException(status_code=500, detail="리비전을 불러오지 못했습니다.") from exc
    return {
        "slide_number": slide.slide_number,
        "source_image_url": f"/api/source-image/{job_id}/{slide_num}",
        "guides": [guide.model_dump() for guide in slide.guides],
        "latest_revision": latest_revision.model_dump() if latest_revision else None,
        "final_elements": [element.model_dump() for element in slide.final_elements],
        "locked_ids": list(slide.locked_ids),
        "status": slide.status.value,
    }


@router.get("/source-image/{job_id}/{slide_num}")
@router.get("/slide-image/{job_id}/{slide_num}")
async def get_source_image(job_id: str, slide_num: int):
    _require_job(job_id)
    if slide_num < 1:
        raise HTTPException(status_code=404, detail="Slide not found.")
    path = get_slide_image_path(job_id, slide_num)
    if not path:
        raise HTTPException(status_code=404, detail="Slide image not found.")
    return FileResponse(path, media_type="image/png")


@router.put("/guides/{job_id}/{slide_num}")
async def put_guides(job_id: str, slide_num: int, payload: GuidesPayload) -> dict:
    _require_job(job_id)
    slide = _require_slide(job_id, slide_num)
    slide = save_guides(job_id, slide_num, payload.guides)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide session not found.")
    return {"saved": True, "guides_count": len(slide.guides)}


@router.patch("/guides/{job_id}/{slide_num}/{guide_id}")
async def patch_guide(job_id: str, slide_num: int, guide_id: str, guide: GuideAnnotation) -> dict:
    _require_job(job_id)
    slide = _require_slide(job_id, slide_num)
    updated = []
    replaced = False
    for current in slide.guides:
        if current.id == guide_id:
            updated.append(guide.model_copy(update={"id": guide_id}))
            replaced = True
        else:
            updated.append(current)
    if not replaced:
        raise HTTPException(status_code=404, detail="Guide not found.")
    slide = save_guides(job_id, slide_num, updated)
    return {"saved": True, "guides_count": len(slide.guides)}


@router.delete("/guides/{job_id}/{slide_num}/{guide_id}")
async def delete_guide(job_id: str, slide_num: int, guide_id: str) -> dict:
    _require_job(job_id)
    slide = _require_slide(job_id, slide_num)
    filtered = [guide for guide in slide.guides if guide.id != guide_id]
    if len(filtered) == len(slide.guides):
        raise HTTPException(status_code=404, detail="Guide not found.")
    slide = save_guides(job_id, slide_num, filtered)
    return {"deleted": True, "guides_count": len(slide.guides)}


@router.put("/final-elements/{job_id}/{slide_num}")
async def put_final_elements(job_id: str, slide_num: int, payload: FinalElementsPayload) -> dict:
    _require_job(job_id)
    slide = _require_slide(job_id, slide_num)
    for element in payload.final_elements:
        if element.asset_id is not None and not _ASSET_ID_RE.match(element.asset_id):
            raise HTTPException(
                status_code=400,
                detail="유효하지 않은 asset_id 형식입니다.",
            )
    slide = save_final_elements(job_id, slide_num, payload.final_elements)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide session not found.")
    return {"saved": True, "final_elements_count": len(slide.final_elements)}

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.fidelity_assets import ensure_assets_for_slide
from app.job_manager import get_job, update_job
from app.models import JobStatus
from app.pptx_builder import build_presentation
from app.session_store import (
    build_presentation_from_finals,
    get_asset_path,
    get_result_path,
    load_all_slide_sessions,
    load_session,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_OUTPUT_FILENAME = "ManualFirst_Slides.pptx"
_PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


def _require_job(job_id: str):
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.post("/generate/{job_id}")
async def generate_pptx(job_id: str) -> dict:
    job = _require_job(job_id)
    session = load_session(job_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")

    missing = [slide.slide_number for slide in session.slides if not slide.ready_for_generate]
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Not all slides are ready for generation.",
                "missing_slides": missing,
            },
        )

    data = build_presentation_from_finals(job_id)
    if not data or not data.slides:
        raise HTTPException(status_code=400, detail="No accepted final elements available.")

    update_job(job_id, status=JobStatus.GENERATING, message="Building fidelity-first PPTX.")
    try:
        asset_lookup: dict[str, str] = {}
        for slide in load_all_slide_sessions(job_id):
            updated = ensure_assets_for_slide(job_id, slide.slide_number, slide.final_elements)
            for element in updated:
                if element.asset_id:
                    asset_lookup[element.asset_id] = str(get_asset_path(job_id, element.asset_id))

        pptx_bytes = build_presentation(data, asset_lookup=asset_lookup)
        result_path = get_result_path(job_id)
        result_path.write_bytes(pptx_bytes)
        update_job(job_id, status=JobStatus.COMPLETE, message="PPTX generated.")
        return {
            "ready": True,
            "filename": _OUTPUT_FILENAME,
            "slides_count": len(data.slides),
            "size_bytes": len(pptx_bytes),
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("PPT generation failed for job %s", job_id)
        update_job(
            job_id,
            status=JobStatus.EDITING_REVIEW,
            error=str(exc),
            message="PPT generation failed.",
        )
        raise HTTPException(
            status_code=500,
            detail="PPT 생성 중 오류가 발생했습니다.",
        ) from exc


@router.get("/download/{job_id}")
async def download_pptx(job_id: str) -> FileResponse:
    _require_job(job_id)
    result_path = get_result_path(job_id)
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="Result PPTX not found.")
    return FileResponse(
        result_path,
        media_type=_PPTX_MIME,
        filename=_OUTPUT_FILENAME,
        headers={"Content-Disposition": f'attachment; filename="{_OUTPUT_FILENAME}"'},
    )

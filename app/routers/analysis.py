from __future__ import annotations

import logging
import re

from anthropic import APIError as AnthropicAPIError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.analysis_pipeline import run_guided_analysis
from app.job_manager import get_job, update_job
from app.models import BoundingBox, JobStatus
from app.session_store import apply_revision, load_revision, set_locked_ids

logger = logging.getLogger(__name__)
router = APIRouter()

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_REVISION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _extract_anthropic_message(exc: AnthropicAPIError) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return str(exc)


def _validate_revision_id(revision_id: str) -> None:
    if not _REVISION_ID_RE.match(revision_id):
        raise HTTPException(status_code=404, detail="Revision not found.")


def _validate_slide_num(slide_num: int) -> None:
    if slide_num < 1:
        raise HTTPException(status_code=404, detail="Slide not found.")


class AnalyzeRequest(BaseModel):
    guide_ids: list[str] = Field(default_factory=list)
    scope: str = "slide"
    region: BoundingBox | None = None


class ApplyRevisionRequest(BaseModel):
    candidate_ids: list[str] = Field(default_factory=list)
    accept_all: bool = False


class LockRequest(BaseModel):
    element_ids: list[str] = Field(default_factory=list)
    locked: bool = True


def _require_job(job_id: str):
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.post("/analyze/{job_id}/{slide_num}")
async def analyze_slide(job_id: str, slide_num: int, payload: AnalyzeRequest) -> dict:
    _require_job(job_id)
    _validate_slide_num(slide_num)
    update_job(
        job_id,
        status=JobStatus.ANALYZING,
        current_slide=slide_num,
        message=f"Running guided analysis for slide {slide_num}.",
    )
    try:
        revision = await run_guided_analysis(
            job_id,
            slide_num,
            guide_ids=payload.guide_ids or None,
            scope=payload.scope,
            region=payload.region,
        )
    except AnthropicAPIError as exc:
        message = _extract_anthropic_message(exc)
        logger.warning("Anthropic API error during analyze: %s", message)
        update_job(
            job_id,
            status=JobStatus.EDITING_GUIDANCE,
            error=message,
            message=f"Claude Vision 호출 실패: {message}",
        )
        raise HTTPException(
            status_code=502,
            detail=f"Claude Vision 호출 실패: {message}",
        ) from exc
    except ValueError as exc:
        update_job(job_id, status=JobStatus.EDITING_GUIDANCE, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected analyze failure for %s/%s", job_id, slide_num)
        update_job(job_id, status=JobStatus.EDITING_GUIDANCE, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"분석 중 예기치 못한 오류: {exc}",
        ) from exc
    update_job(
        job_id,
        status=JobStatus.EDITING_REVIEW,
        current_slide=slide_num,
        message=f"Review candidates for slide {slide_num}.",
    )
    return {
        "revision_id": revision.revision_id,
        "status": JobStatus.EDITING_REVIEW.value,
    }


@router.get("/analysis/{job_id}/{slide_num}/{revision_id}")
async def get_analysis(job_id: str, slide_num: int, revision_id: str) -> dict:
    _require_job(job_id)
    _validate_slide_num(slide_num)
    _validate_revision_id(revision_id)
    try:
        revision = load_revision(job_id, slide_num, revision_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to load revision %s for %s/%s", revision_id, job_id, slide_num)
        raise HTTPException(status_code=500, detail="리비전을 불러오지 못했습니다.") from exc
    if not revision:
        raise HTTPException(status_code=404, detail="Revision not found.")
    return revision.model_dump()


@router.post("/review/{job_id}/{slide_num}/{revision_id}/apply")
async def apply_analysis(job_id: str, slide_num: int, revision_id: str, payload: ApplyRevisionRequest) -> dict:
    _require_job(job_id)
    _validate_slide_num(slide_num)
    _validate_revision_id(revision_id)
    slide = apply_revision(
        job_id,
        slide_num,
        revision_id,
        candidate_ids=payload.candidate_ids,
        accept_all=payload.accept_all or not payload.candidate_ids,
    )
    if not slide:
        raise HTTPException(status_code=404, detail="Slide or revision not found.")
    return {
        "final_elements": [element.model_dump() for element in slide.final_elements],
        "locked_ids": list(slide.locked_ids),
        "status": slide.status.value,
    }


@router.post("/lock/{job_id}/{slide_num}")
async def lock_elements(job_id: str, slide_num: int, payload: LockRequest) -> dict:
    _require_job(job_id)
    _validate_slide_num(slide_num)
    slide = set_locked_ids(job_id, slide_num, payload.element_ids, payload.locked)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found.")
    return {"locked_ids": list(slide.locked_ids)}

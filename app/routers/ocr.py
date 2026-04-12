from __future__ import annotations

import logging
import re

from anthropic import APIError as AnthropicAPIError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.analysis_pipeline import run_guided_analysis
from app.job_manager import get_job, update_job
from app.models import BoundingBox, JobStatus
from app.region_ocr import analyze_region, ocr_region
from app.session_store import get_source_image_path

logger = logging.getLogger(__name__)

router = APIRouter()

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _extract_anthropic_message(exc: AnthropicAPIError) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return str(exc)


class OcrRegionRequest(BaseModel):
    job_id: str
    slide_num: int
    region: BoundingBox


def _require_job(job_id: str):
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def _validate_slide_num(slide_num: int) -> None:
    if slide_num < 1:
        raise HTTPException(status_code=404, detail="Slide not found.")


def _load_slide_image_bytes(job_id: str, slide_num: int) -> bytes:
    path = get_source_image_path(job_id, slide_num)
    if not path:
        raise HTTPException(status_code=404, detail="Slide image not found.")
    return path.read_bytes()


@router.post("/ocr-region")
async def ocr_region_endpoint(req: OcrRegionRequest) -> dict:
    _require_job(req.job_id)
    _validate_slide_num(req.slide_num)
    image_bytes = _load_slide_image_bytes(req.job_id, req.slide_num)
    try:
        return await ocr_region(image_bytes, req.region)
    except AnthropicAPIError as exc:
        message = _extract_anthropic_message(exc)
        logger.warning("Anthropic API error during OCR: %s", message)
        raise HTTPException(
            status_code=502,
            detail=f"Claude Vision 호출 실패: {message}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Unexpected OCR failure for %s/%s", req.job_id, req.slide_num
        )
        raise HTTPException(
            status_code=500,
            detail="OCR 중 예기치 못한 오류가 발생했습니다.",
        ) from exc


@router.post("/analyze-region")
async def analyze_region_endpoint(req: OcrRegionRequest) -> dict:
    _require_job(req.job_id)
    _validate_slide_num(req.slide_num)
    image_bytes = _load_slide_image_bytes(req.job_id, req.slide_num)
    try:
        result = await analyze_region(image_bytes, req.region)
    except AnthropicAPIError as exc:
        message = _extract_anthropic_message(exc)
        logger.warning(
            "Anthropic API error during region analysis: %s", message
        )
        raise HTTPException(
            status_code=502,
            detail=f"Claude Vision 호출 실패: {message}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Unexpected region analysis failure for %s/%s",
            req.job_id,
            req.slide_num,
        )
        raise HTTPException(
            status_code=500,
            detail="영역 분석 중 예기치 못한 오류가 발생했습니다.",
        ) from exc
    result["bbox"] = req.region.model_dump()
    result["source"] = "manual"
    return result


@router.post("/reanalyze/{job_id}/{slide_num}")
async def reanalyze_slide(job_id: str, slide_num: int) -> dict:
    _require_job(job_id)
    _validate_slide_num(slide_num)
    try:
        revision = await run_guided_analysis(job_id, slide_num)
    except AnthropicAPIError as exc:
        message = _extract_anthropic_message(exc)
        logger.warning("Anthropic API error during reanalyze: %s", message)
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
        logger.exception(
            "Unexpected reanalyze failure for %s/%s", job_id, slide_num
        )
        update_job(job_id, status=JobStatus.EDITING_GUIDANCE, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="재분석 중 예기치 못한 오류가 발생했습니다.",
        ) from exc
    return {
        "revision_id": revision.revision_id,
        "candidates": [c.model_dump() for c in revision.candidates],
    }

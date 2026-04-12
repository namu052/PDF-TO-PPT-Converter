from __future__ import annotations

import logging
import re

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from app.config import settings
from app.job_manager import create_job, get_job, update_job
from app.models import JobStatus
from app.pdf_to_images import PDFProcessingError, validate_pdf
from app.pipeline import extract_all_slides

logger = logging.getLogger(__name__)

router = APIRouter()

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_ALLOWED_PDF_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "binary/octet-stream",
    "",
    None,
}


@router.post("/convert")
async def convert_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")
    if file.content_type not in _ALLOWED_PDF_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    try:
        contents = await file.read()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to read upload stream")
        raise HTTPException(
            status_code=400,
            detail="업로드 파일을 읽지 못했습니다.",
        ) from exc
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file upload.")

    size_mb = len(contents) / (1024 * 1024)
    if size_mb > settings.max_pdf_size_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size_mb:.1f}MB). Max {settings.max_pdf_size_mb}MB.",
        )

    try:
        page_count = validate_pdf(contents)
    except PDFProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected PDF validation failure")
        raise HTTPException(
            status_code=400,
            detail="PDF 파일을 열 수 없습니다. 손상되었거나 지원되지 않는 형식일 수 있습니다.",
        ) from exc

    if page_count > settings.max_slides:
        raise HTTPException(
            status_code=413,
            detail=f"페이지 수가 너무 많습니다 ({page_count}). 최대 {settings.max_slides}페이지까지 지원합니다.",
        )

    job = create_job(total_slides=page_count)
    update_job(
        job.job_id,
        status=JobStatus.RASTERIZING,
        message="Preparing slide images for manual guidance.",
    )
    background_tasks.add_task(extract_all_slides, job.job_id, contents)

    logger.info("Manual-first job %s created for %s", job.job_id, file.filename)
    return {
        "job_id": job.job_id,
        "total_slides": page_count,
        "status": JobStatus.RASTERIZING.value,
        "next_url": f"/editor?job_id={job.job_id}",
    }


@router.get("/status/{job_id}")
async def get_status(job_id: str) -> dict:
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")

    return {
        "status": job.status.value,
        "total_slides": job.total_slides,
        "current_slide": job.current_slide,
        "ready_slides": job.ready_slides,
        "message": job.message,
        "error": job.error,
    }

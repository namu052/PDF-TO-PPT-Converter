"""Upload pipeline for the manual-first workflow.

The pipeline now stops after PDF validation and slide rasterization:

PDF -> slide images -> editing_guidance
"""

from __future__ import annotations

import logging

from app.job_manager import save_slide_image, update_job
from app.models import JobStatus
from app.pdf_to_images import PDFProcessingError, pdf_to_images, validate_pdf

logger = logging.getLogger(__name__)


def extract_all_slides(job_id: str, pdf_bytes: bytes) -> None:
    try:
        update_job(
            job_id,
            status=JobStatus.RASTERIZING,
            message="Rasterizing PDF into slide images.",
        )

        page_count = validate_pdf(pdf_bytes)
        update_job(job_id, total_slides=page_count)

        images = pdf_to_images(pdf_bytes)
        for idx, img_bytes in enumerate(images, start=1):
            save_slide_image(job_id, idx, img_bytes)
            update_job(
                job_id,
                current_slide=idx,
                message=f"Prepared slide image {idx}/{page_count}.",
            )

        update_job(
            job_id,
            status=JobStatus.EDITING_GUIDANCE,
            current_slide=page_count,
            message="Add guidance overlays before running Claude Vision.",
        )
    except PDFProcessingError as exc:
        logger.warning("PDF processing failed for job %s: %s", job_id, exc)
        update_job(
            job_id,
            status=JobStatus.FAILED,
            error=str(exc),
            message=f"PDF processing failed: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Rasterization pipeline failed for job %s", job_id)
        update_job(
            job_id,
            status=JobStatus.FAILED,
            error=str(exc),
            message=f"Unexpected error: {exc}",
        )

from __future__ import annotations

from pathlib import Path

from app.models import Job, JobStatus, PresentationData
from app.session_store import (
    build_presentation_from_finals,
    cleanup_expired_sessions,
    create_session,
    get_result_path,
    get_source_image_path,
    load_session,
    save_source_image,
    update_session,
)


def create_job(total_slides: int = 0) -> Job:
    return create_session(total_slides=total_slides)


def get_job(job_id: str) -> Job | None:
    return load_session(job_id)


def update_job(job_id: str, **kwargs) -> None:
    update_session(job_id, **kwargs)


def save_slides_json(job_id: str, data: PresentationData) -> None:
    # Legacy compatibility: save directly as accepted final elements.
    from app.models import FinalElement
    from app.session_store import save_final_elements

    for slide in data.slides:
        finals = [FinalElement(**element.model_dump()) for element in slide.elements]
        save_final_elements(job_id, slide.slide_number, finals)


def load_slides_json(job_id: str) -> PresentationData | None:
    return build_presentation_from_finals(job_id)


def save_slide_image(job_id: str, slide_num: int, png_bytes: bytes) -> None:
    save_source_image(job_id, slide_num, png_bytes)


def get_slide_image_path(job_id: str, slide_num: int) -> Path | None:
    return get_source_image_path(job_id, slide_num)


def cleanup_expired_jobs(ttl_hours: int) -> None:
    cleanup_expired_sessions(ttl_hours)


def get_result_file_path(job_id: str) -> Path:
    return get_result_path(job_id)


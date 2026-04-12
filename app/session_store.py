from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4

from app.config import settings
from app.models import (
    AnalysisRevision,
    CandidateElement,
    FinalElement,
    JobSession,
    JobStatus,
    PresentationData,
    SlideData,
    SlideSession,
    SlideSummary,
    SlideWorkflowStatus,
)

_LOCK = Lock()


def _session_dir(job_id: str) -> Path:
    return settings.output_dir / job_id


def _manifest_path(job_id: str) -> Path:
    return _session_dir(job_id) / "manifest.json"


def _slides_dir(job_id: str) -> Path:
    return _session_dir(job_id) / "slides"


def _slide_dir(job_id: str, slide_num: int) -> Path:
    return _slides_dir(job_id) / f"{slide_num:03d}"


def _slide_session_path(job_id: str, slide_num: int) -> Path:
    return _slide_dir(job_id, slide_num) / "session.json"


def _source_image_path(job_id: str, slide_num: int) -> Path:
    return _slide_dir(job_id, slide_num) / "source.png"


def _overlay_image_path(job_id: str, slide_num: int) -> Path:
    return _slide_dir(job_id, slide_num) / "guide_overlay.png"


def _revision_dir(job_id: str, slide_num: int, revision_id: str) -> Path:
    return _slide_dir(job_id, slide_num) / "revisions" / revision_id


def _revision_json_path(job_id: str, slide_num: int, revision_id: str) -> Path:
    return _revision_dir(job_id, slide_num, revision_id) / "revision.json"


def _raw_response_path(job_id: str, slide_num: int, revision_id: str) -> Path:
    return _revision_dir(job_id, slide_num, revision_id) / "raw_response.txt"


def _assets_dir(job_id: str) -> Path:
    return _session_dir(job_id) / "assets"


def _result_path(job_id: str) -> Path:
    return _session_dir(job_id) / "result.pptx"


def _to_relative(job_id: str, path: Path) -> str:
    return path.relative_to(_session_dir(job_id)).as_posix()


def _from_relative(job_id: str, relative_path: str | None) -> Path | None:
    if not relative_path:
        return None
    return _session_dir(job_id) / Path(relative_path)


def _summarize_slide(slide: SlideSession) -> SlideSummary:
    ready = bool(slide.final_elements)
    status = slide.status
    if ready:
        status = SlideWorkflowStatus.READY
    elif slide.latest_revision_id:
        status = SlideWorkflowStatus.REVIEW
    else:
        status = SlideWorkflowStatus.GUIDANCE
    return SlideSummary(
        slide_number=slide.slide_number,
        status=status,
        guides_count=len(slide.guides),
        revisions_count=len(slide.revisions),
        final_elements_count=len(slide.final_elements),
        locked_ids=list(slide.locked_ids),
        ready_for_generate=ready,
        latest_revision_id=slide.latest_revision_id,
    )


def _write_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def create_session(total_slides: int) -> JobSession:
    job_id = uuid4().hex[:12]
    session_dir = _session_dir(job_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    _slides_dir(job_id).mkdir(parents=True, exist_ok=True)
    _assets_dir(job_id).mkdir(parents=True, exist_ok=True)

    slides = [SlideSummary(slide_number=i) for i in range(1, total_slides + 1)]
    session = JobSession(
        job_id=job_id,
        total_slides=total_slides,
        output_dir=str(session_dir),
        status=JobStatus.UPLOADING,
        message="Upload received.",
        manifest_version=settings.manifest_version,
        slides=slides,
    )

    with _LOCK:
        save_session(session)
        for slide_num in range(1, total_slides + 1):
            save_slide_session(
                job_id,
                SlideSession(slide_number=slide_num),
                update_manifest=False,
            )
        _refresh_session(job_id)
    return load_session(job_id)  # type: ignore[return-value]


def load_session(job_id: str) -> JobSession | None:
    path = _manifest_path(job_id)
    if not path.exists():
        return None
    return JobSession.model_validate_json(path.read_text(encoding="utf-8"))


def save_session(session: JobSession) -> None:
    _write_json(_manifest_path(session.job_id), session.model_dump_json(indent=2))


def update_session(job_id: str, **kwargs) -> JobSession | None:
    with _LOCK:
        session = load_session(job_id)
        if not session:
            return None
        for key, value in kwargs.items():
            setattr(session, key, value)
        save_session(session)
        return session


def load_slide_session(job_id: str, slide_num: int) -> SlideSession | None:
    path = _slide_session_path(job_id, slide_num)
    if not path.exists():
        return None
    return SlideSession.model_validate_json(path.read_text(encoding="utf-8"))


def save_slide_session(
    job_id: str,
    slide: SlideSession,
    *,
    update_manifest: bool = True,
) -> SlideSession:
    _write_json(
        _slide_session_path(job_id, slide.slide_number),
        slide.model_dump_json(indent=2),
    )
    if update_manifest:
        _refresh_session(job_id)
    return slide


def _refresh_session(job_id: str) -> JobSession | None:
    session = load_session(job_id)
    if not session:
        return None

    slides: list[SlideSummary] = []
    for slide_num in range(1, session.total_slides + 1):
        slide = load_slide_session(job_id, slide_num)
        if slide:
            slides.append(_summarize_slide(slide))
        else:
            slides.append(SlideSummary(slide_number=slide_num))

    session.slides = slides
    session.ready_slides = sum(1 for slide in slides if slide.ready_for_generate)
    # Status transitions are owned by explicit callers (pipeline.py,
    # analysis_pipeline.py, routers/generate.py). Do not overwrite session.status
    # here — doing so would clobber transient states like RASTERIZING/ANALYZING
    # as a side effect of any slide-level save.

    save_session(session)
    return session


def save_source_image(job_id: str, slide_num: int, png_bytes: bytes) -> SlideSession | None:
    session = load_session(job_id)
    if not session:
        return None

    path = _source_image_path(job_id, slide_num)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes)

    slide = load_slide_session(job_id, slide_num) or SlideSession(slide_number=slide_num)
    slide.source_image_path = _to_relative(job_id, path)
    save_slide_session(job_id, slide)
    return slide


def get_source_image_path(job_id: str, slide_num: int) -> Path | None:
    slide = load_slide_session(job_id, slide_num)
    if not slide:
        return None
    path = _from_relative(job_id, slide.source_image_path)
    if not path or not path.exists():
        return None
    return path


def save_overlay(job_id: str, slide_num: int, overlay_bytes: bytes) -> Path:
    path = _overlay_image_path(job_id, slide_num)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(overlay_bytes)
    slide = load_slide_session(job_id, slide_num) or SlideSession(slide_number=slide_num)
    slide.guide_overlay_path = _to_relative(job_id, path)
    save_slide_session(job_id, slide)
    return path


def get_overlay_path(job_id: str, slide_num: int) -> Path | None:
    slide = load_slide_session(job_id, slide_num)
    if not slide:
        return None
    path = _from_relative(job_id, slide.guide_overlay_path)
    if not path or not path.exists():
        return None
    return path


def save_guides(job_id: str, slide_num: int, guides) -> SlideSession | None:
    slide = load_slide_session(job_id, slide_num)
    if not slide:
        return None
    slide.guides = list(guides)
    save_slide_session(job_id, slide)
    return slide


def load_revision(job_id: str, slide_num: int, revision_id: str) -> AnalysisRevision | None:
    path = _revision_json_path(job_id, slide_num, revision_id)
    if not path.exists():
        return None
    return AnalysisRevision.model_validate_json(path.read_text(encoding="utf-8"))


def save_revision(
    job_id: str,
    slide_num: int,
    revision: AnalysisRevision,
    *,
    raw_response: str = "",
) -> AnalysisRevision:
    revision_dir = _revision_dir(job_id, slide_num, revision.revision_id)
    revision_dir.mkdir(parents=True, exist_ok=True)
    raw_path = _raw_response_path(job_id, slide_num, revision.revision_id)
    raw_path.write_text(raw_response, encoding="utf-8")
    revision.raw_response_path = _to_relative(job_id, raw_path)
    revision.candidates = [
        candidate.model_copy(update={"revision_id": revision.revision_id})
        for candidate in revision.candidates
    ]
    _write_json(
        _revision_json_path(job_id, slide_num, revision.revision_id),
        revision.model_dump_json(indent=2),
    )

    slide = load_slide_session(job_id, slide_num) or SlideSession(slide_number=slide_num)
    if revision.revision_id not in slide.revisions:
        slide.revisions.append(revision.revision_id)
    slide.latest_revision_id = revision.revision_id
    slide.status = SlideWorkflowStatus.REVIEW
    save_slide_session(job_id, slide)
    return revision


def set_locked_ids(job_id: str, slide_num: int, element_ids: list[str], locked: bool) -> SlideSession | None:
    slide = load_slide_session(job_id, slide_num)
    if not slide:
        return None
    locked_ids = set(slide.locked_ids)
    if locked:
        locked_ids.update(element_ids)
    else:
        locked_ids.difference_update(element_ids)
    slide.locked_ids = sorted(locked_ids)
    for element in slide.final_elements:
        element.locked = element.final_id in slide.locked_ids
    save_slide_session(job_id, slide)
    return slide


def save_final_elements(job_id: str, slide_num: int, elements: list[FinalElement]) -> SlideSession | None:
    slide = load_slide_session(job_id, slide_num)
    if not slide:
        return None
    slide.final_elements = elements
    slide.locked_ids = [element.final_id for element in elements if element.locked]
    slide.status = SlideWorkflowStatus.READY if elements else SlideWorkflowStatus.REVIEW
    save_slide_session(job_id, slide)
    return slide


def apply_revision(
    job_id: str,
    slide_num: int,
    revision_id: str,
    *,
    candidate_ids: list[str] | None = None,
    accept_all: bool = False,
) -> SlideSession | None:
    slide = load_slide_session(job_id, slide_num)
    revision = load_revision(job_id, slide_num, revision_id)
    if not slide or not revision:
        return None

    selected_candidates = revision.candidates
    if not accept_all and candidate_ids is not None:
        selected = set(candidate_ids)
        selected_candidates = [
            candidate for candidate in revision.candidates if candidate.candidate_id in selected
        ]

    preserved = [element for element in slide.final_elements if element.final_id in slide.locked_ids]
    accepted: list[FinalElement] = []
    for candidate in selected_candidates:
        payload = candidate.model_dump(
            exclude={"candidate_id", "revision_id"},
        )
        accepted.append(
            FinalElement(
                **payload,
                accepted_from_revision=revision_id,
                accepted_from_candidate=candidate.candidate_id,
            )
        )

    slide.final_elements = preserved + accepted
    slide.locked_ids = [element.final_id for element in preserved]
    for element in slide.final_elements:
        element.locked = element.final_id in slide.locked_ids
    slide.status = SlideWorkflowStatus.READY if slide.final_elements else SlideWorkflowStatus.REVIEW
    save_slide_session(job_id, slide)
    return slide


def load_all_slide_sessions(job_id: str) -> list[SlideSession]:
    session = load_session(job_id)
    if not session:
        return []
    slides: list[SlideSession] = []
    for slide_num in range(1, session.total_slides + 1):
        slide = load_slide_session(job_id, slide_num)
        if slide:
            slides.append(slide)
    return slides


def build_presentation_from_finals(job_id: str) -> PresentationData | None:
    session = load_session(job_id)
    if not session:
        return None

    slides: list[SlideData] = []
    for slide in load_all_slide_sessions(job_id):
        slide_elements = [element.model_copy() for element in slide.final_elements]
        slides.append(
            SlideData(
                slide_number=slide.slide_number,
                elements=slide_elements,
            )
        )

    return PresentationData(
        slide_width_inches=settings.default_slide_width,
        slide_height_inches=settings.default_slide_height,
        slides=slides,
    )


def get_result_path(job_id: str) -> Path:
    return _result_path(job_id)


def get_asset_path(job_id: str, asset_id: str) -> Path:
    return _assets_dir(job_id) / f"{asset_id}.png"


def save_asset(job_id: str, asset_id: str, png_bytes: bytes) -> Path:
    path = get_asset_path(job_id, asset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes)
    return path


def cleanup_expired_sessions(ttl_hours: int) -> None:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.utcnow() - timedelta(hours=ttl_hours)
    for manifest in settings.output_dir.glob("*/manifest.json"):
        try:
            session = JobSession.model_validate_json(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        if session.created_at < cutoff:
            shutil.rmtree(manifest.parent, ignore_errors=True)

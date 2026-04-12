from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import pipeline
from app.config import settings
from app.job_manager import create_job, get_job, save_slide_image, update_job
from app.main import app
from app.models import (
    AnalysisRevision,
    BoundingBox,
    CandidateElement,
    ElementType,
    FinalElement,
    GuideAnnotation,
    GuideIntent,
    GuideType,
    JobStatus,
    SlideWorkflowStatus,
    TextParagraph,
    TextRun,
)
from app.session_store import (
    load_revision,
    load_session,
    load_slide_session,
    save_final_elements,
    save_guides,
    save_revision,
)
from tests.test_pdf_to_images import MINIMAL_PDF


def _make_png(width: int = 640, height: int = 360, color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _sample_guide(guide_id: str = "guide-1") -> dict:
    return {
        "id": guide_id,
        "guide_type": "region_box",
        "bbox": {"x_pct": 10, "y_pct": 10, "width_pct": 40, "height_pct": 20},
        "note": "Analyze this title area.",
        "intent_tag": "title",
        "stroke_color_hex": "#FF6B00",
        "fill_color_hex": "#FFD1A3",
        "fill_opacity": 0.2,
        "stroke_width_pt": 2,
        "vision_visible": True,
    }


def _sample_candidate(candidate_id: str = "cand-1") -> CandidateElement:
    return CandidateElement(
        candidate_id=candidate_id,
        revision_id="rev-1",
        element_type=ElementType.TEXT_BOX,
        bbox=BoundingBox(x_pct=10, y_pct=10, width_pct=40, height_pct=15),
        source="manual",
        paragraphs=[TextParagraph(runs=[TextRun(text="Guided Title")])],
        style_confidence=0.92,
        fidelity_note="Use editable text.",
    )


def _sample_final(final_id: str = "final-1", *, locked: bool = False) -> FinalElement:
    return FinalElement(
        final_id=final_id,
        element_type=ElementType.TEXT_BOX,
        bbox=BoundingBox(x_pct=10, y_pct=10, width_pct=40, height_pct=15),
        source="manual",
        paragraphs=[TextParagraph(runs=[TextRun(text="Accepted Title")])],
        style_confidence=0.95,
        fidelity_note="Accepted from review.",
        locked=locked,
    )


def _create_slide_job(total_slides: int = 1):
    job = create_job(total_slides=total_slides)
    for slide_num in range(1, total_slides + 1):
        save_slide_image(job.job_id, slide_num, _make_png())
    update_job(
        job.job_id,
        status=JobStatus.EDITING_GUIDANCE,
        current_slide=total_slides,
        message="Add guidance overlays before analysis.",
    )
    return job


@pytest.fixture()
def tmp_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    yield tmp_path


@pytest.fixture()
def client(tmp_output_dir):
    with TestClient(app) as test_client:
        yield test_client


def test_convert_accepts_minimal_pdf(client):
    with patch("app.routers.upload.extract_all_slides", new=AsyncMock(return_value=None)):
        response = client.post(
            "/api/convert",
            files={"file": ("slides.pdf", MINIMAL_PDF, "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["job_id"]) == 12
    assert body["total_slides"] == 1
    assert body["status"] == "rasterizing"
    assert body["next_url"].endswith(body["job_id"])


def test_convert_rejects_invalid_file(client):
    response = client.post(
        "/api/convert",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_status_returns_snapshot_for_existing_job(client):
    job = _create_slide_job(total_slides=2)
    update_job(job.job_id, status=JobStatus.RASTERIZING, current_slide=1, message="Working.")

    response = client.get(f"/api/status/{job.job_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "rasterizing"
    assert response.json()["current_slide"] == 1


def test_get_session_returns_slide_summaries(client):
    job = _create_slide_job(total_slides=2)

    response = client.get(f"/api/session/{job.job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job.job_id
    assert len(body["slides"]) == 2
    assert body["slides"][0]["status"] == "guidance"


def test_get_slide_returns_source_image_and_empty_state(client):
    job = _create_slide_job()

    response = client.get(f"/api/slide/{job.job_id}/1")

    assert response.status_code == 200
    body = response.json()
    assert body["slide_number"] == 1
    assert body["source_image_url"].endswith(f"/api/source-image/{job.job_id}/1")
    assert body["guides"] == []
    assert body["latest_revision"] is None
    assert body["final_elements"] == []


def test_guides_roundtrip_patch_and_delete(client):
    job = _create_slide_job()

    response = client.put(
        f"/api/guides/{job.job_id}/1",
        json={"guides": [_sample_guide()]},
    )
    assert response.status_code == 200
    assert response.json()["guides_count"] == 1

    slide = client.get(f"/api/slide/{job.job_id}/1").json()
    assert slide["guides"][0]["intent_tag"] == "title"

    patched = _sample_guide()
    patched["note"] = "Updated note"
    patched["intent_tag"] = "body"
    response = client.patch(
        f"/api/guides/{job.job_id}/1/guide-1",
        json=patched,
    )
    assert response.status_code == 200
    slide = client.get(f"/api/slide/{job.job_id}/1").json()
    assert slide["guides"][0]["note"] == "Updated note"
    assert slide["guides"][0]["intent_tag"] == "body"

    response = client.delete(f"/api/guides/{job.job_id}/1/guide-1")
    assert response.status_code == 200
    slide = client.get(f"/api/slide/{job.job_id}/1").json()
    assert slide["guides"] == []


def test_analyze_route_returns_revision_id(client):
    job = _create_slide_job()
    save_guides(job.job_id, 1, [GuideAnnotation.model_validate(_sample_guide())])
    revision = AnalysisRevision(
        revision_id="rev-1",
        summary="One candidate generated.",
        confidence="high",
        candidates=[_sample_candidate()],
    )

    with patch("app.routers.analysis.run_guided_analysis", new=AsyncMock(return_value=revision)):
        response = client.post(
            f"/api/analyze/{job.job_id}/1",
            json={"guide_ids": ["guide-1"], "scope": "slide"},
        )

    assert response.status_code == 200
    assert response.json() == {"revision_id": "rev-1", "status": "editing_review"}
    assert get_job(job.job_id).status == JobStatus.EDITING_REVIEW


def test_get_analysis_returns_saved_revision(client):
    job = _create_slide_job()
    revision = AnalysisRevision(
        revision_id="rev-1",
        summary="Review me.",
        confidence="medium",
        candidates=[_sample_candidate()],
    )
    save_revision(job.job_id, 1, revision, raw_response="raw")

    response = client.get(f"/api/analysis/{job.job_id}/1/rev-1")

    assert response.status_code == 200
    body = response.json()
    assert body["revision_id"] == "rev-1"
    assert len(body["candidates"]) == 1


def test_apply_revision_accepts_candidates(client):
    job = _create_slide_job()
    revision = AnalysisRevision(
        revision_id="rev-1",
        summary="Acceptable candidate.",
        confidence="high",
        candidates=[_sample_candidate()],
    )
    save_revision(job.job_id, 1, revision, raw_response="raw")

    response = client.post(
        f"/api/review/{job.job_id}/1/rev-1/apply",
        json={"accept_all": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["final_elements"]) == 1
    slide = load_slide_session(job.job_id, 1)
    assert slide is not None
    assert slide.status == SlideWorkflowStatus.READY
    session = load_session(job.job_id)
    assert session is not None
    assert session.ready_slides == 1


def test_lock_elements_updates_locked_ids(client):
    job = _create_slide_job()
    save_final_elements(job.job_id, 1, [_sample_final()])

    response = client.post(
        f"/api/lock/{job.job_id}/1",
        json={"element_ids": ["final-1"], "locked": True},
    )

    assert response.status_code == 200
    assert response.json()["locked_ids"] == ["final-1"]
    slide = load_slide_session(job.job_id, 1)
    assert slide is not None
    assert slide.locked_ids == ["final-1"]
    assert slide.final_elements[0].locked is True


def test_put_final_elements_overwrites_current_finals(client):
    job = _create_slide_job()

    response = client.put(
        f"/api/final-elements/{job.job_id}/1",
        json={"final_elements": [_sample_final(final_id="final-a").model_dump()]},
    )

    assert response.status_code == 200
    slide = load_slide_session(job.job_id, 1)
    assert slide is not None
    assert slide.final_elements[0].final_id == "final-a"


def test_generate_rejects_when_not_ready(client):
    job = _create_slide_job()

    response = client.post(f"/api/generate/{job.job_id}")

    assert response.status_code == 409
    assert "Not all slides are ready" in response.text


def test_generate_and_download_pptx(client):
    job = _create_slide_job()
    save_final_elements(job.job_id, 1, [_sample_final()])

    response = client.post(f"/api/generate/{job.job_id}")
    assert response.status_code == 200
    assert response.json()["ready"] is True

    result_path = Path(job.output_dir) / "result.pptx"
    assert result_path.exists()

    download = client.get(f"/api/download/{job.job_id}")
    assert download.status_code == 200
    assert "attachment" in download.headers.get("content-disposition", "")


def test_source_image_endpoint_streams_png(client):
    job = _create_slide_job()

    response = client.get(f"/api/source-image/{job.job_id}/1")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_ocr_and_region_analysis_routes(client):
    job = _create_slide_job()
    region = {"x_pct": 10, "y_pct": 10, "width_pct": 30, "height_pct": 20}

    with patch(
        "app.routers.ocr.ocr_region",
        new=AsyncMock(return_value={"paragraphs": [], "confidence": "high"}),
    ):
        response = client.post(
            "/api/ocr-region",
            json={"job_id": job.job_id, "slide_num": 1, "region": region},
        )
    assert response.status_code == 200
    assert response.json()["confidence"] == "high"

    with patch(
        "app.routers.ocr.analyze_region",
        new=AsyncMock(
            return_value={"element_type": "text_box", "paragraphs": []}
        ),
    ):
        response = client.post(
            "/api/analyze-region",
            json={"job_id": job.job_id, "slide_num": 1, "region": region},
        )
    assert response.status_code == 200
    assert response.json()["source"] == "manual"
    assert response.json()["bbox"]["x_pct"] == 10


def test_reanalyze_route_returns_candidates(client):
    job = _create_slide_job()
    revision = AnalysisRevision(
        revision_id="rev-2",
        summary="Reanalyzed.",
        confidence="medium",
        candidates=[_sample_candidate("cand-2")],
    )

    with patch("app.routers.ocr.run_guided_analysis", new=AsyncMock(return_value=revision)):
        response = client.post(f"/api/reanalyze/{job.job_id}/1")

    assert response.status_code == 200
    assert response.json()["revision_id"] == "rev-2"
    assert len(response.json()["candidates"]) == 1


def test_root_and_editor_routes_are_served(client):
    assert client.get("/").status_code == 200
    assert client.get("/editor").status_code == 200


def test_extract_all_slides_rasterizes_and_sets_guidance_status(tmp_output_dir):
    job = create_job(total_slides=1)

    pipeline.extract_all_slides(job.job_id, MINIMAL_PDF)

    reloaded = get_job(job.job_id)
    assert reloaded is not None
    assert reloaded.status == JobStatus.EDITING_GUIDANCE
    assert reloaded.current_slide == 1
    slide = load_slide_session(job.job_id, 1)
    assert slide is not None
    assert slide.source_image_path
    assert (Path(job.output_dir) / slide.source_image_path).exists()


def test_extract_all_slides_marks_failed_for_bad_pdf(tmp_output_dir):
    job = create_job(total_slides=1)

    pipeline.extract_all_slides(job.job_id, b"not a pdf")

    reloaded = get_job(job.job_id)
    assert reloaded is not None
    assert reloaded.status == JobStatus.FAILED
    assert reloaded.error

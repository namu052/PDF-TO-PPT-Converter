from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.analysis_pipeline import run_guided_analysis
from app.config import settings
from app.models import (
    BoundingBox,
    GuideAnnotation,
    GuideIntent,
    GuideType,
    RenderStrategy,
)
from app.session_store import (
    create_session,
    load_slide_session,
    save_guides,
    save_source_image,
)


def _make_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 225), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture()
def tmp_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    yield tmp_path


@pytest.mark.asyncio
async def test_run_guided_analysis_creates_revision_and_overlay(tmp_output_dir):
    session = create_session(total_slides=1)
    save_source_image(session.job_id, 1, _make_png())
    guides = [
        GuideAnnotation(
            id="keep-image",
            guide_type=GuideType.HIGHLIGHT,
            bbox=BoundingBox(x_pct=10, y_pct=10, width_pct=30, height_pct=25),
            note="Preserve as image",
            intent_tag=GuideIntent.KEEP_AS_IMAGE,
        )
    ]
    save_guides(session.job_id, 1, guides)

    analysis_result = {
        "summary": "One image candidate.",
        "confidence": "high",
        "revision_hint": "Prefer raster.",
        "raw": "raw-response",
        "elements": [
            {
                "element_type": "image",
                "bbox": {
                    "x_pct": 10,
                    "y_pct": 10,
                    "width_pct": 30,
                    "height_pct": 25,
                },
                "image_description": "diagram",
                "style_confidence": 0.95,
            }
        ],
    }

    with patch(
        "app.analysis_pipeline.analyze_slide_with_guidance",
        new=AsyncMock(return_value=analysis_result),
    ):
        revision = await run_guided_analysis(
            session.job_id, 1, guide_ids=["keep-image"]
        )

    slide = load_slide_session(session.job_id, 1)
    assert slide is not None
    assert slide.latest_revision_id == revision.revision_id
    assert len(revision.candidates) == 1
    assert revision.candidates[0].render_strategy == RenderStrategy.RASTER
    assert (Path(session.output_dir) / "slides/001/guide_overlay.png").exists()
    assert (
        Path(session.output_dir)
        / f"slides/001/revisions/{revision.revision_id}/revision.json"
    ).exists()


@pytest.mark.asyncio
async def test_run_guided_analysis_region_scope_uses_region_analyzer(tmp_output_dir):
    session = create_session(total_slides=1)
    save_source_image(session.job_id, 1, _make_png())
    region = BoundingBox(x_pct=15, y_pct=20, width_pct=20, height_pct=15)

    with patch(
        "app.analysis_pipeline.analyze_region",
        new=AsyncMock(
            return_value={
                "element_type": "text_box",
                "paragraphs": [],
                "confidence": "medium",
            }
        ),
    ):
        revision = await run_guided_analysis(
            session.job_id, 1, scope="region", region=region
        )

    assert revision.scope == "region"
    assert len(revision.candidates) == 1
    assert revision.candidates[0].bbox == region


@pytest.mark.asyncio
async def test_run_guided_analysis_normalizes_alt_schema(tmp_output_dir):
    session = create_session(total_slides=1)
    save_source_image(session.job_id, 1, _make_png())

    analysis_result = {
        "summary": "Alternate schema response.",
        "confidence": "medium",
        "revision_hint": "Prefer hybrid.",
        "raw": "raw-response",
        "elements": [
            {
                "type": "text",
                "content": "Bullet line",
                "position": {
                    "x": 40,
                    "y": 45,
                    "width": 200,
                    "height": 90,
                },
                "style": {
                    "font_size": 24,
                    "font_weight": "bold",
                    "color": "white",
                    "font_family": "Arial",
                    "text_align": "center",
                },
            }
        ],
    }

    with patch(
        "app.analysis_pipeline.analyze_slide_with_guidance",
        new=AsyncMock(return_value=analysis_result),
    ):
        revision = await run_guided_analysis(session.job_id, 1)

    assert len(revision.candidates) == 1
    candidate = revision.candidates[0]
    assert candidate.element_type == "text_box"
    assert candidate.bbox.x_pct == pytest.approx(10.0)
    assert candidate.bbox.y_pct == pytest.approx(20.0)
    assert candidate.bbox.width_pct == pytest.approx(50.0)
    assert candidate.bbox.height_pct == pytest.approx(40.0)
    assert candidate.paragraphs[0].alignment == "center"
    assert candidate.paragraphs[0].runs[0].font.color_hex == "#FFFFFF"

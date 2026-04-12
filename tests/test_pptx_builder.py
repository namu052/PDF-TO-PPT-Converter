"""Phase 4 — PPTX Builder 테스트.

PLAN.md §21.3 기반. sample_vision_response.json fixture를 사용해 build_presentation의
정상 동작을 검증한다.
"""
import io
import json
from pathlib import Path

import pytest
from pptx import Presentation

from app.models import PresentationData
from app.pptx_builder import build_presentation

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_vision_response.json"


@pytest.fixture
def sample_presentation() -> PresentationData:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        slide_data = json.load(f)

    return PresentationData(
        slides=[{
            "slide_number": 1,
            **slide_data
        }]
    )


def test_build_creates_valid_pptx(sample_presentation):
    result = build_presentation(sample_presentation)
    assert isinstance(result, bytes)
    assert len(result) > 0

    # python-pptx로 열 수 있는지 확인
    prs = Presentation(io.BytesIO(result))
    assert len(prs.slides) == 1


def test_build_correct_slide_count(sample_presentation):
    # 3장으로 확장
    sample_presentation.slides.append(
        sample_presentation.slides[0].model_copy(update={"slide_number": 2})
    )
    sample_presentation.slides.append(
        sample_presentation.slides[0].model_copy(update={"slide_number": 3})
    )

    result = build_presentation(sample_presentation)
    prs = Presentation(io.BytesIO(result))
    assert len(prs.slides) == 3


def test_build_has_text_boxes(sample_presentation):
    result = build_presentation(sample_presentation)
    prs = Presentation(io.BytesIO(result))
    slide = prs.slides[0]

    # 최소 하나의 텍스트를 가진 shape이 있어야 함
    texts = []
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                text = "".join(run.text for run in para.runs)
                if text:
                    texts.append(text)

    assert "인공지능의 미래" in texts


def test_build_empty_presentation():
    prs_data = PresentationData(slides=[])
    result = build_presentation(prs_data)
    prs = Presentation(io.BytesIO(result))
    assert len(prs.slides) == 0


def test_build_slide_dimensions():
    prs_data = PresentationData(
        slide_width_inches=10.0,
        slide_height_inches=7.5,
        slides=[{"slide_number": 1, "elements": []}]
    )
    result = build_presentation(prs_data)
    prs = Presentation(io.BytesIO(result))
    from pptx.util import Inches
    assert abs(prs.slide_width - Inches(10.0)) < 100  # EMU 오차 허용


def test_build_all_element_types_smoke():
    """Exercises text_box, shape (with fill+border+opacity+bullets),
    line (with arrows+dash), image placeholder, table, and custom bg."""
    payload = {
        "slide_width_inches": 13.333,
        "slide_height_inches": 7.5,
        "slides": [
            {
                "slide_number": 1,
                "background_color_hex": "#F0F0F0",
                "notes": "speaker notes",
                "elements": [
                    {
                        "id": "t1",
                        "element_type": "text_box",
                        "bbox": {"x_pct": 5, "y_pct": 5, "width_pct": 50, "height_pct": 15},
                        "paragraphs": [
                            {
                                "runs": [
                                    {
                                        "text": "Title",
                                        "font": {
                                            "family": "Arial",
                                            "size_pt": 24,
                                            "bold": True,
                                            "italic": True,
                                            "underline": True,
                                            "color_hex": "#333333",
                                        },
                                        "hyperlink": "https://example.com",
                                    }
                                ],
                                "alignment": "center",
                                "bullet": True,
                                "bullet_char": "*",
                                "indent_level": 1,
                                "line_spacing": 1.2,
                            }
                        ],
                    },
                    {
                        "id": "s1",
                        "element_type": "shape",
                        "shape_kind": "rounded_rectangle",
                        "bbox": {"x_pct": 60, "y_pct": 5, "width_pct": 30, "height_pct": 15},
                        "fill_color_hex": "#3366CC",
                        "fill_opacity": 0.5,
                        "border_color_hex": "#000000",
                        "border_width_pt": 2,
                        "paragraphs": [{"runs": [{"text": "inside"}]}],
                    },
                    {
                        "id": "l1",
                        "element_type": "line",
                        "bbox": {"x_pct": 5, "y_pct": 30, "width_pct": 80, "height_pct": 5},
                        "line_color_hex": "#FF0000",
                        "line_width_pt": 2,
                        "line_style": "dashed",
                        "start_arrow": "triangle",
                        "end_arrow": "stealth",
                    },
                    {
                        "id": "i1",
                        "element_type": "image",
                        "bbox": {"x_pct": 5, "y_pct": 45, "width_pct": 40, "height_pct": 30},
                        "image_description": "chart",
                        "image_placeholder_color": "#DDEEFF",
                    },
                    {
                        "id": "tb1",
                        "element_type": "table",
                        "bbox": {"x_pct": 50, "y_pct": 45, "width_pct": 45, "height_pct": 30},
                        "table_rows": 2,
                        "table_cols": 2,
                        "table_data": [["a", "b"], ["c", "d"]],
                    },
                ],
            }
        ],
    }
    prs_data = PresentationData.model_validate(payload)
    result = build_presentation(prs_data)
    assert isinstance(result, bytes) and len(result) > 1000
    prs = Presentation(io.BytesIO(result))
    assert len(prs.slides) == 1
    # At least the text_box, shape, connector, image-placeholder, and table
    # shapes should be on the slide.
    assert len(prs.slides[0].shapes) >= 5

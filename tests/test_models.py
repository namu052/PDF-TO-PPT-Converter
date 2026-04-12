"""Unit tests for ``app.models`` (Pydantic data contracts).

Spec source: PLAN.md §21.2. Covers BoundingBox validation, ElementType /
SlideElement defaults, FontInfo color validation, SlideData serialization
roundtrip, and PresentationData defaults.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    BoundingBox,
    ElementType,
    FontInfo,
    PresentationData,
    ShapeKind,
    SlideData,
    SlideElement,
    TextParagraph,
    TextRun,
)


# ---------------------------------------------------------------------------
# BoundingBox
# ---------------------------------------------------------------------------


def test_bounding_box_valid():
    bb = BoundingBox(x_pct=10, y_pct=20, width_pct=30, height_pct=40)
    assert bb.x_pct == 10
    assert bb.y_pct == 20
    assert bb.width_pct == 30
    assert bb.height_pct == 40


def test_bounding_box_negative_x_rejected():
    with pytest.raises(ValidationError):
        BoundingBox(x_pct=-1, y_pct=20, width_pct=30, height_pct=40)


def test_bounding_box_zero_width_rejected():
    # width/height must be > 0
    with pytest.raises(ValidationError):
        BoundingBox(x_pct=10, y_pct=20, width_pct=0, height_pct=40)


def test_bounding_box_over_100_rejected():
    with pytest.raises(ValidationError):
        BoundingBox(x_pct=10, y_pct=20, width_pct=30, height_pct=120)


# ---------------------------------------------------------------------------
# ElementType / ShapeKind enums
# ---------------------------------------------------------------------------


def test_element_type_values():
    assert ElementType.TEXT_BOX.value == "text_box"
    assert ElementType.SHAPE.value == "shape"
    assert ElementType.LINE.value == "line"
    assert ElementType.IMAGE.value == "image"
    assert ElementType.TABLE.value == "table"


def test_shape_kind_accessible():
    assert ShapeKind.RECTANGLE.value == "rectangle"
    assert ShapeKind.ELLIPSE.value == "ellipse"


# ---------------------------------------------------------------------------
# SlideElement
# ---------------------------------------------------------------------------


def test_slide_element_defaults():
    elem = SlideElement(
        element_type=ElementType.TEXT_BOX,
        bbox=BoundingBox(x_pct=0, y_pct=0, width_pct=50, height_pct=10),
    )
    assert elem.source == "auto"
    assert elem.z_order == 0
    assert elem.rotation == 0.0
    assert elem.opacity == 1.0
    assert len(elem.id) == 8
    assert elem.paragraphs == []


def test_slide_element_rejects_bad_rotation():
    with pytest.raises(ValidationError):
        SlideElement(
            element_type=ElementType.SHAPE,
            bbox=BoundingBox(x_pct=0, y_pct=0, width_pct=10, height_pct=10),
            rotation=360,  # strict upper bound (lt=360)
        )


# ---------------------------------------------------------------------------
# FontInfo
# ---------------------------------------------------------------------------


def test_font_info_defaults():
    font = FontInfo()
    assert font.family == "Calibri"
    assert font.size_pt == 18.0
    assert font.bold is False
    assert font.color_hex == "#000000"


def test_font_info_color_validation():
    font = FontInfo(color_hex="#FF0000")
    assert font.color_hex == "#FF0000"

    with pytest.raises(ValidationError):
        FontInfo(color_hex="not-a-color")

    with pytest.raises(ValidationError):
        FontInfo(color_hex="#ZZZZZZ")


def test_font_info_size_must_be_positive():
    with pytest.raises(ValidationError):
        FontInfo(size_pt=0)


# ---------------------------------------------------------------------------
# SlideData serialization roundtrip
# ---------------------------------------------------------------------------


def test_slide_data_serialization_roundtrip():
    slide = SlideData(
        slide_number=1,
        background_color_hex="#FFFFFF",
        elements=[
            SlideElement(
                element_type=ElementType.TEXT_BOX,
                bbox=BoundingBox(x_pct=10, y_pct=10, width_pct=80, height_pct=10),
                paragraphs=[TextParagraph(runs=[TextRun(text="Hello")])],
            )
        ],
    )
    json_str = slide.model_dump_json()
    loaded = SlideData.model_validate_json(json_str)
    assert loaded.slide_number == 1
    assert loaded.elements[0].element_type == ElementType.TEXT_BOX
    assert loaded.elements[0].paragraphs[0].runs[0].text == "Hello"


def test_text_paragraph_requires_at_least_one_run():
    with pytest.raises(ValidationError):
        TextParagraph(runs=[])


# ---------------------------------------------------------------------------
# PresentationData
# ---------------------------------------------------------------------------


def test_presentation_data_defaults():
    # slides는 이제 필수 필드(데이터 손실 방어). 명시적 빈 리스트만 허용.
    prs = PresentationData(slides=[])
    assert prs.slide_width_inches == 13.333
    assert prs.slide_height_inches == 7.5
    assert prs.slides == []


def test_presentation_data_rejects_missing_slides():
    # slides 키가 없으면 422로 거부되어야 한다 (데이터 손실 방어).
    with pytest.raises(ValidationError):
        PresentationData()


def test_presentation_data_rejects_extra_fields():
    # 알 수 없는 키를 가진 페이로드는 거부되어야 한다 (PUT /api/slides 공격 벡터 차단).
    with pytest.raises(ValidationError):
        PresentationData.model_validate({"not_a_valid": "structure"})


def test_presentation_data_accepts_slides():
    prs = PresentationData(
        slides=[
            SlideData(slide_number=1),
            SlideData(slide_number=2),
        ]
    )
    assert len(prs.slides) == 2
    assert prs.slides[1].slide_number == 2

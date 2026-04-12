from __future__ import annotations

import io

from PIL import Image

from app.annotation_composer import compose_guide_overlay, summarize_guides
from app.models import BoundingBox, GuideAnnotation, GuideIntent, GuideType


def _make_source_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_compose_guide_overlay_draws_visible_markup():
    guide = GuideAnnotation(
        id="g1",
        guide_type=GuideType.RECT,
        bbox=BoundingBox(x_pct=10, y_pct=10, width_pct=40, height_pct=40),
        note="Important block",
        intent_tag=GuideIntent.SHAPE,
        stroke_color_hex="#FF0000",
        fill_color_hex="#FFAAAA",
        fill_opacity=0.5,
    )

    result = compose_guide_overlay(_make_source_png(), [guide])

    image = Image.open(io.BytesIO(result)).convert("RGBA")
    inside = image.getpixel((40, 30))
    outside = image.getpixel((180, 80))
    assert inside != outside


def test_summarize_guides_includes_id_type_and_note():
    guides = [
        GuideAnnotation(
            id="g1",
            guide_type=GuideType.TEXT_NOTE,
            bbox=BoundingBox(x_pct=5, y_pct=5, width_pct=20, height_pct=10),
            note="Title text",
            intent_tag=GuideIntent.TITLE,
        )
    ]

    summary = summarize_guides(guides)

    assert "g1" in summary
    assert "text_note" in summary
    assert "Title text" in summary

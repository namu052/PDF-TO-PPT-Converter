from __future__ import annotations

import io
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from app.models import GuideAnnotation, GuideType, PointPct


def _pct_to_px(image: Image.Image, value_x: float, value_y: float) -> tuple[int, int]:
    return (
        int((value_x / 100.0) * image.width),
        int((value_y / 100.0) * image.height),
    )


def _bbox_to_px(image: Image.Image, guide: GuideAnnotation) -> tuple[int, int, int, int]:
    left, top = _pct_to_px(image, guide.bbox.x_pct, guide.bbox.y_pct)
    right, bottom = _pct_to_px(
        image,
        guide.bbox.x_pct + guide.bbox.width_pct,
        guide.bbox.y_pct + guide.bbox.height_pct,
    )
    return left, top, right, bottom


def _load_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", 18)
    except Exception:
        return ImageFont.load_default()


def compose_guide_overlay(source_bytes: bytes, guides: Iterable[GuideAnnotation]) -> bytes:
    source = Image.open(io.BytesIO(source_bytes)).convert("RGBA")
    overlay = Image.new("RGBA", source.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    font = _load_font()

    for guide in guides:
        if not guide.vision_visible:
            continue

        line_color = guide.stroke_color_hex
        fill_alpha = int(255 * guide.fill_opacity)
        fill_color = guide.fill_color_hex or guide.stroke_color_hex
        fill_rgba = _hex_to_rgba(fill_color, fill_alpha)
        line_rgba = _hex_to_rgba(line_color, 255)
        left, top, right, bottom = _bbox_to_px(source, guide)
        width = max(1, int(round(guide.stroke_width_pt)))

        if guide.guide_type in {GuideType.RECT, GuideType.REGION_BOX, GuideType.HIGHLIGHT}:
            draw.rectangle((left, top, right, bottom), outline=line_rgba, fill=fill_rgba, width=width)
        elif guide.guide_type == GuideType.ELLIPSE:
            draw.ellipse((left, top, right, bottom), outline=line_rgba, fill=fill_rgba, width=width)
        elif guide.guide_type in {GuideType.LINE, GuideType.ARROW}:
            start = guide.start_point or PointPct(
                x_pct=guide.bbox.x_pct,
                y_pct=guide.bbox.y_pct,
            )
            end = guide.end_point or PointPct(
                x_pct=guide.bbox.x_pct + guide.bbox.width_pct,
                y_pct=guide.bbox.y_pct + guide.bbox.height_pct,
            )
            sx, sy = _pct_to_px(source, start.x_pct, start.y_pct)
            ex, ey = _pct_to_px(source, end.x_pct, end.y_pct)
            draw.line((sx, sy, ex, ey), fill=line_rgba, width=width)
            if guide.guide_type == GuideType.ARROW:
                _draw_arrow_head(draw, sx, sy, ex, ey, line_rgba, width)
        elif guide.guide_type == GuideType.TEXT_NOTE:
            draw.rectangle((left, top, right, bottom), outline=line_rgba, fill=fill_rgba, width=width)

        if guide.text or guide.note:
            label = guide.text or guide.note
            label_pos = (left + 6, max(0, top - 22))
            draw.rounded_rectangle(
                (label_pos[0] - 4, label_pos[1] - 2, label_pos[0] + 220, label_pos[1] + 18),
                radius=4,
                fill=_hex_to_rgba(guide.stroke_color_hex, 220),
            )
            draw.text(label_pos, label[:32], fill=(255, 255, 255, 255), font=font)

    composite = Image.alpha_composite(source, overlay)
    buffer = io.BytesIO()
    composite.save(buffer, format="PNG")
    return buffer.getvalue()


def summarize_guides(guides: Iterable[GuideAnnotation]) -> str:
    parts: list[str] = []
    for guide in guides:
        parts.append(
            f"{guide.id}:{guide.guide_type.value}:{guide.intent_tag.value}:{guide.note or guide.text or 'no-note'}"
        )
    return "\n".join(parts)


def _draw_arrow_head(draw: ImageDraw.ImageDraw, sx: int, sy: int, ex: int, ey: int, fill, width: int) -> None:
    dx = ex - sx
    dy = ey - sy
    if dx == 0 and dy == 0:
        return
    length = max((dx * dx + dy * dy) ** 0.5, 1)
    ux = dx / length
    uy = dy / length
    size = max(8, width * 4)
    left = (
        ex - ux * size - uy * size * 0.5,
        ey - uy * size + ux * size * 0.5,
    )
    right = (
        ex - ux * size + uy * size * 0.5,
        ey - uy * size - ux * size * 0.5,
    )
    draw.polygon([(ex, ey), left, right], fill=fill)


def _hex_to_rgba(value: str, alpha: int) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha

from __future__ import annotations

import io
import logging

from PIL import Image, ImageColor

from app.annotation_composer import compose_guide_overlay, summarize_guides
from app.models import (
    AnalysisRevision,
    ArrowType,
    BoundingBox,
    CandidateElement,
    ElementType,
    FinalElement,
    GuideAnnotation,
    GuideIntent,
    LineStyle,
    RenderStrategy,
    ShapeKind,
)
from app.region_ocr import analyze_region
from app.session_store import (
    get_source_image_path,
    load_slide_session,
    save_overlay,
    save_revision,
)
from app.vision_analyzer import analyze_slide_with_guidance

logger = logging.getLogger(__name__)


async def run_guided_analysis(
    job_id: str,
    slide_num: int,
    *,
    guide_ids: list[str] | None = None,
    scope: str = "slide",
    region: BoundingBox | None = None,
) -> AnalysisRevision:
    slide = load_slide_session(job_id, slide_num)
    if not slide:
        raise ValueError("Slide session not found.")

    source_path = get_source_image_path(job_id, slide_num)
    if not source_path:
        raise ValueError("Source slide image not found.")

    source_bytes = source_path.read_bytes()
    slide_size = _get_image_size(source_bytes)
    active_guides = _select_guides(slide.guides, guide_ids)
    overlay_bytes = compose_guide_overlay(source_bytes, active_guides)
    save_overlay(job_id, slide_num, overlay_bytes)

    locked_finals = [element for element in slide.final_elements if element.final_id in slide.locked_ids]
    guide_prompt = summarize_guides(active_guides)

    if scope == "region" and region is not None:
        region_result = await analyze_region(overlay_bytes, region)
        raw = str(region_result)
        candidates = [_candidate_from_region_result(region_result, region)]
        confidence = region_result.get("confidence", "medium")
        summary = "Region analysis completed."
    else:
        analysis = await analyze_slide_with_guidance(
            source_bytes=source_bytes,
            overlay_bytes=overlay_bytes,
            guide_summary=guide_prompt,
            slide_number=slide_num,
            locked_elements=locked_finals,
        )
        raw = analysis["raw"]
        confidence = analysis["confidence"]
        summary = analysis["summary"]
        candidates = _candidate_list_from_elements(
            analysis["elements"],
            active_guides,
            analysis["revision_hint"],
            slide_size,
        )

    revision = AnalysisRevision(
        scope="region" if scope == "region" and region is not None else "slide",
        guide_ids=[guide.id for guide in active_guides],
        summary=summary,
        confidence=confidence,
        candidates=candidates,
        locked_ids_snapshot=list(slide.locked_ids),
    )
    save_revision(job_id, slide_num, revision, raw_response=raw)
    return revision


def _select_guides(guides: list[GuideAnnotation], guide_ids: list[str] | None) -> list[GuideAnnotation]:
    if not guide_ids:
        return list(guides)
    wanted = set(guide_ids)
    return [guide for guide in guides if guide.id in wanted]


def _candidate_from_region_result(result: dict, region: BoundingBox) -> CandidateElement:
    payload = dict(result)
    payload.setdefault("element_type", "text_box")
    payload["bbox"] = region.model_dump()
    payload.setdefault("source_region", region.model_dump())
    payload.setdefault("render_strategy", _strategy_for_element(payload.get("element_type"), [], 0.6).value)
    payload.setdefault("style_confidence", 0.6)
    return CandidateElement.model_validate(payload)


def _candidate_from_element_dict(
    element_dict: dict,
    guides: list[GuideAnnotation],
    revision_hint: str,
    slide_size: tuple[int, int],
) -> CandidateElement:
    payload = _normalize_element_payload(element_dict, slide_size)
    element_type = payload.get("element_type", "shape")
    bbox = payload.get("bbox")
    if bbox and "source_region" not in payload:
        payload["source_region"] = bbox
    payload.setdefault("style_confidence", 0.6)
    payload["render_strategy"] = _strategy_for_element(
        element_type,
        guides,
        payload.get("style_confidence", 0.6),
    ).value
    payload.setdefault("fidelity_note", revision_hint)
    return CandidateElement.model_validate(payload)


def _candidate_list_from_elements(
    elements: list[dict],
    guides: list[GuideAnnotation],
    revision_hint: str,
    slide_size: tuple[int, int],
) -> list[CandidateElement]:
    candidates: list[CandidateElement] = []
    for idx, element in enumerate(elements):
        try:
            candidates.append(
                _candidate_from_element_dict(element, guides, revision_hint, slide_size)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping invalid guided element %s: %s", idx, exc)
    return candidates


def _normalize_element_payload(
    element_dict: dict,
    slide_size: tuple[int, int],
) -> dict:
    payload = dict(element_dict)
    if "element_type" in payload and "bbox" in payload:
        return payload

    raw_type = str(payload.get("type", "")).lower()
    position = payload.get("position")
    if not raw_type or not isinstance(position, dict):
        return payload

    bbox = _position_to_bbox(position, slide_size)
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    normalized: dict = {
        "element_type": _map_element_type(raw_type, payload, bbox),
        "bbox": bbox,
        "name": _element_name(payload, raw_type),
        "opacity": _clamp_float(style.get("opacity"), default=1.0),
        "z_order": max(0, int(_coerce_float(style.get("z_index"), 0.0))),
    }

    if normalized["element_type"] == ElementType.TEXT_BOX.value:
        normalized["paragraphs"] = _paragraphs_from_alt_text(payload, style)
        normalized["shape_kind"] = None
    elif normalized["element_type"] == ElementType.IMAGE.value:
        normalized["image_description"] = (
            payload.get("image_description")
            or payload.get("icon_type")
            or payload.get("content")
            or raw_type
        )
        normalized["image_placeholder_color"] = _normalize_color(
            style.get("color") or style.get("fill_color")
        )
    elif normalized["element_type"] == ElementType.LINE.value:
        normalized["line_color_hex"] = _normalize_color(
            style.get("stroke_color") or style.get("fill_color") or style.get("color")
        )
        normalized["line_width_pt"] = max(
            1.0,
            _coerce_float(
                style.get("stroke_width"),
                min(
                    _coerce_float(position.get("width"), 1.0),
                    _coerce_float(position.get("height"), 1.0),
                ),
            ),
        )
        normalized["line_style"] = _line_style_from_value(style.get("stroke_type")).value
        normalized["start_arrow"] = ArrowType.NONE.value
        normalized["end_arrow"] = ArrowType.NONE.value
    elif normalized["element_type"] == ElementType.TABLE.value:
        normalized["table_data"] = payload.get("table_data")
        normalized["table_rows"] = payload.get("table_rows")
        normalized["table_cols"] = payload.get("table_cols")
    else:
        normalized["shape_kind"] = _shape_kind_from_alt_payload(payload, style)
        normalized["fill_color_hex"] = _normalize_color(style.get("fill_color"))
        normalized["border_color_hex"] = _normalize_color(
            style.get("stroke_color") or style.get("border_color")
        )
        normalized["border_width_pt"] = _coerce_float(
            style.get("stroke_width") or style.get("border_width"),
            None,
        )
        normalized["corner_radius_pct"] = _corner_radius_pct(
            style.get("corner_radius"),
            position,
        )

    return normalized


def _get_image_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(image_bytes)) as image:
        return image.size


def _position_to_bbox(position: dict, slide_size: tuple[int, int]) -> dict:
    x = _coerce_float(position.get("x", position.get("left")), 0.0)
    y = _coerce_float(position.get("y", position.get("top")), 0.0)
    width = _coerce_float(position.get("width"), 0.0)
    height = _coerce_float(position.get("height"), 0.0)

    if max(x, y, width, height) <= 100 and x + width <= 100.5 and y + height <= 100.5:
        x_pct = x
        y_pct = y
        width_pct = width
        height_pct = height
    else:
        slide_width, slide_height = slide_size
        x_pct = (x / max(slide_width, 1)) * 100
        y_pct = (y / max(slide_height, 1)) * 100
        width_pct = (width / max(slide_width, 1)) * 100
        height_pct = (height / max(slide_height, 1)) * 100

    x_pct = max(0.0, min(x_pct, 100.0))
    y_pct = max(0.0, min(y_pct, 100.0))
    width_pct = max(0.1, min(width_pct, 100.0 - x_pct))
    height_pct = max(0.1, min(height_pct, 100.0 - y_pct))
    return {
        "x_pct": round(x_pct, 3),
        "y_pct": round(y_pct, 3),
        "width_pct": round(width_pct, 3),
        "height_pct": round(height_pct, 3),
    }


def _map_element_type(raw_type: str, payload: dict, bbox: dict) -> str:
    if raw_type == "text":
        return ElementType.TEXT_BOX.value
    if raw_type == "table":
        return ElementType.TABLE.value
    if raw_type in {"image", "icon"}:
        return ElementType.IMAGE.value
    if raw_type == "connector":
        return ElementType.CONNECTOR.value
    if raw_type == "line":
        return ElementType.LINE.value
    if raw_type == "shape":
        shape_type = str(payload.get("shape_type", "")).lower()
        if shape_type != "arrow" and min(bbox["width_pct"], bbox["height_pct"]) <= 0.2:
            return ElementType.LINE.value
    return ElementType.SHAPE.value


def _paragraphs_from_alt_text(payload: dict, style: dict) -> list[dict]:
    content = str(payload.get("content") or "").replace("\r\n", "\n")
    if not content:
        return []

    font = {
        "family": str(style.get("font_family") or "Calibri"),
        "size_pt": _coerce_float(style.get("font_size"), 18.0),
        "bold": _is_bold(style.get("font_weight")),
        "italic": False,
        "underline": False,
        "color_hex": _normalize_color(style.get("color")) or "#000000",
    }
    alignment = _alignment_from_value(style.get("text_align"))
    line_spacing = max(0.8, _coerce_float(style.get("line_height"), 1.0))
    paragraphs: list[dict] = []
    for raw_line in content.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        bullet = False
        bullet_char = None
        if line[0] in {"•", "-", "*"}:
            bullet = True
            bullet_char = line[0]
            line = line[1:].lstrip() or raw_line.strip()
        paragraphs.append(
            {
                "runs": [{"text": line, "font": font}],
                "alignment": alignment,
                "bullet": bullet,
                "bullet_char": bullet_char,
                "line_spacing": line_spacing,
            }
        )
    return paragraphs


def _element_name(payload: dict, raw_type: str) -> str:
    content = str(payload.get("content") or "").strip()
    if content:
        return content[:40]
    if payload.get("icon_type"):
        return str(payload["icon_type"])
    if payload.get("shape_type"):
        return str(payload["shape_type"])
    return raw_type


def _shape_kind_from_alt_payload(payload: dict, style: dict) -> str | None:
    shape_type = str(payload.get("shape_type", "")).lower()
    direction = str(style.get("direction", "right")).lower()
    mapping = {
        "rectangle": ShapeKind.RECTANGLE.value,
        "rounded_rectangle": ShapeKind.ROUNDED_RECTANGLE.value,
        "circle": ShapeKind.ELLIPSE.value,
        "ellipse": ShapeKind.ELLIPSE.value,
        "diamond": ShapeKind.DIAMOND.value,
        "hexagon": ShapeKind.HEXAGON.value,
        "cloud": ShapeKind.CLOUD.value,
        "star": ShapeKind.STAR.value,
        "chevron": ShapeKind.CHEVRON.value,
        "triangle": ShapeKind.TRIANGLE.value,
        "callout": ShapeKind.CALLOUT.value,
    }
    if shape_type == "arrow":
        return {
            "left": ShapeKind.LEFT_ARROW.value,
            "up": ShapeKind.UP_ARROW.value,
            "down": ShapeKind.DOWN_ARROW.value,
        }.get(direction, ShapeKind.RIGHT_ARROW.value)
    return mapping.get(shape_type)


def _line_style_from_value(value: object) -> LineStyle:
    normalized = str(value or "").lower()
    if normalized in {"dash", "dashed"}:
        return LineStyle.DASHED
    if normalized in {"dot", "dotted"}:
        return LineStyle.DOTTED
    return LineStyle.SOLID


def _alignment_from_value(value: object) -> str:
    normalized = str(value or "").lower()
    if normalized in {"center", "centre"}:
        return "center"
    if normalized == "right":
        return "right"
    if normalized == "justify":
        return "justify"
    return "left"


def _corner_radius_pct(value: object, position: dict) -> float | None:
    radius = _coerce_float(value, None)
    if radius is None:
        return None
    width = _coerce_float(position.get("width"), 0.0)
    height = _coerce_float(position.get("height"), 0.0)
    shortest = max(min(width, height), 1.0)
    return max(0.0, min((radius / shortest) * 100, 100.0))


def _normalize_color(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "transparent"}:
        return None
    try:
        red, green, blue = ImageColor.getrgb(text)
    except ValueError:
        return None
    return f"#{red:02X}{green:02X}{blue:02X}"


def _is_bold(value: object) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    if text == "bold":
        return True
    try:
        return float(text) >= 600
    except ValueError:
        return False


def _coerce_float(value: object, default: float | None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_float(
    value: object,
    *,
    default: float,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    number = _coerce_float(value, default)
    assert number is not None
    return max(minimum, min(number, maximum))


def _strategy_for_element(
    element_type: str,
    guides: list[GuideAnnotation],
    style_confidence: float,
) -> RenderStrategy:
    if element_type in {"image", "table", "group"}:
        return RenderStrategy.RASTER
    if any(guide.intent_tag == GuideIntent.KEEP_AS_IMAGE for guide in guides):
        return RenderStrategy.RASTER
    if style_confidence < 0.8:
        return RenderStrategy.HYBRID
    return RenderStrategy.PRIMITIVE

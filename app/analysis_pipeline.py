from __future__ import annotations

import logging

from app.annotation_composer import compose_guide_overlay, summarize_guides
from app.models import (
    AnalysisRevision,
    BoundingBox,
    CandidateElement,
    FinalElement,
    GuideAnnotation,
    GuideIntent,
    RenderStrategy,
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
        candidates = [
            _candidate_from_element_dict(elem, active_guides, analysis["revision_hint"])
            for elem in analysis["elements"]
        ]

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
) -> CandidateElement:
    payload = dict(element_dict)
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

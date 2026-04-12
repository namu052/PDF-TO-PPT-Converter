from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _short_id(length: int = 8) -> str:
    return uuid4().hex[:length]


class BoundingBox(BaseModel):
    x_pct: float = Field(ge=0, le=100)
    y_pct: float = Field(ge=0, le=100)
    width_pct: float = Field(gt=0, le=100)
    height_pct: float = Field(gt=0, le=100)


class PointPct(BaseModel):
    x_pct: float = Field(ge=0, le=100)
    y_pct: float = Field(ge=0, le=100)


class ElementType(str, Enum):
    TEXT_BOX = "text_box"
    SHAPE = "shape"
    LINE = "line"
    CONNECTOR = "connector"
    IMAGE = "image"
    TABLE = "table"
    GROUP = "group"


class ShapeKind(str, Enum):
    RECTANGLE = "rectangle"
    ROUNDED_RECTANGLE = "rounded_rectangle"
    ELLIPSE = "ellipse"
    TRIANGLE = "triangle"
    RIGHT_ARROW = "right_arrow"
    LEFT_ARROW = "left_arrow"
    UP_ARROW = "up_arrow"
    DOWN_ARROW = "down_arrow"
    DIAMOND = "diamond"
    PENTAGON = "pentagon"
    HEXAGON = "hexagon"
    CLOUD = "cloud"
    STAR = "star"
    CHEVRON = "chevron"
    CALLOUT = "callout"


class LineStyle(str, Enum):
    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"


class ArrowType(str, Enum):
    NONE = "none"
    TRIANGLE = "triangle"
    STEALTH = "stealth"
    OPEN = "open"


class GuideType(str, Enum):
    LINE = "line"
    ARROW = "arrow"
    RECT = "rect"
    ELLIPSE = "ellipse"
    TEXT_NOTE = "text_note"
    REGION_BOX = "region_box"
    HIGHLIGHT = "highlight"


class GuideIntent(str, Enum):
    GENERAL = "general"
    TITLE = "title"
    BODY = "body"
    TEXT = "text"
    SHAPE = "shape"
    LINE = "line"
    CONNECTOR = "connector"
    IMAGE = "image"
    TABLE = "table"
    KEEP_AS_IMAGE = "keep_as_image"


class RenderStrategy(str, Enum):
    PRIMITIVE = "primitive"
    RASTER = "raster"
    HYBRID = "hybrid"


class SlideWorkflowStatus(str, Enum):
    GUIDANCE = "guidance"
    REVIEW = "review"
    READY = "ready"


class FontInfo(BaseModel):
    family: str = "Calibri"
    size_pt: float = Field(default=18.0, gt=0, le=200)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color_hex: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")


class TextRun(BaseModel):
    text: str
    font: FontInfo = Field(default_factory=FontInfo)
    hyperlink: str | None = None


class TextParagraph(BaseModel):
    runs: list[TextRun] = Field(default_factory=list, min_length=1)
    alignment: Literal["left", "center", "right", "justify"] = "left"
    bullet: bool = False
    bullet_char: str | None = None
    indent_level: int = Field(default=0, ge=0, le=5)
    line_spacing: float = Field(default=1.0, gt=0)


class SlideElement(BaseModel):
    id: str = Field(default_factory=_short_id)
    element_type: ElementType
    bbox: BoundingBox
    source: Literal["auto", "manual"] = "auto"

    paragraphs: list[TextParagraph] = Field(default_factory=list)

    shape_kind: ShapeKind | None = None
    fill_color_hex: str | None = None
    fill_opacity: float = Field(default=1.0, ge=0, le=1)
    border_color_hex: str | None = None
    border_width_pt: float | None = None
    corner_radius_pct: float | None = None

    line_color_hex: str | None = None
    line_width_pt: float = 1.0
    line_style: LineStyle = LineStyle.SOLID
    start_arrow: ArrowType = ArrowType.NONE
    end_arrow: ArrowType = ArrowType.NONE

    image_description: str | None = None
    image_placeholder_color: str | None = "#E0E0E0"

    table_rows: int | None = None
    table_cols: int | None = None
    table_data: list[list[str]] | None = None

    rotation: float = Field(default=0.0, ge=0, lt=360)
    z_order: int = Field(default=0, ge=0)
    opacity: float = Field(default=1.0, ge=0, le=1)
    name: str = ""

    render_strategy: RenderStrategy = RenderStrategy.HYBRID
    style_confidence: float = Field(default=0.6, ge=0, le=1)
    asset_id: str | None = None
    source_region: BoundingBox | None = None
    fidelity_note: str = ""


class CandidateElement(SlideElement):
    candidate_id: str = Field(default_factory=lambda: _short_id(10))
    revision_id: str | None = None


class FinalElement(SlideElement):
    final_id: str = Field(default_factory=lambda: _short_id(10))
    accepted_from_revision: str | None = None
    accepted_from_candidate: str | None = None
    locked: bool = False


class GuideAnnotation(BaseModel):
    id: str = Field(default_factory=_short_id)
    guide_type: GuideType
    bbox: BoundingBox
    start_point: PointPct | None = None
    end_point: PointPct | None = None
    text: str = ""
    note: str = ""
    intent_tag: GuideIntent = GuideIntent.GENERAL
    stroke_color_hex: str = Field(default="#FF6B00", pattern=r"^#[0-9a-fA-F]{6}$")
    stroke_width_pt: float = Field(default=2.0, ge=0)
    fill_color_hex: str | None = Field(default="#FFB36B", pattern=r"^#[0-9a-fA-F]{6}$")
    fill_opacity: float = Field(default=0.15, ge=0, le=1)
    vision_visible: bool = True


class RenderAsset(BaseModel):
    asset_id: str
    kind: Literal["crop", "overlay", "composite"] = "crop"
    relative_path: str
    bbox: BoundingBox
    reason: str = ""


class AnalysisRevision(BaseModel):
    revision_id: str = Field(default_factory=lambda: _short_id(10))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    scope: Literal["slide", "region"] = "slide"
    guide_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    raw_response_path: str = ""
    candidates: list[CandidateElement] = Field(default_factory=list)
    locked_ids_snapshot: list[str] = Field(default_factory=list)


class SlideSummary(BaseModel):
    slide_number: int = Field(ge=1)
    status: SlideWorkflowStatus = SlideWorkflowStatus.GUIDANCE
    guides_count: int = 0
    revisions_count: int = 0
    final_elements_count: int = 0
    locked_ids: list[str] = Field(default_factory=list)
    ready_for_generate: bool = False
    latest_revision_id: str | None = None


class SlideSession(BaseModel):
    slide_number: int = Field(ge=1)
    source_image_path: str = ""
    guide_overlay_path: str | None = None
    guides: list[GuideAnnotation] = Field(default_factory=list)
    revisions: list[str] = Field(default_factory=list)
    latest_revision_id: str | None = None
    final_elements: list[FinalElement] = Field(default_factory=list)
    locked_ids: list[str] = Field(default_factory=list)
    status: SlideWorkflowStatus = SlideWorkflowStatus.GUIDANCE


class SlideData(BaseModel):
    slide_number: int = Field(ge=1)
    background_color_hex: str = "#FFFFFF"
    background_gradient: dict | None = None
    elements: list[SlideElement] = Field(default_factory=list)
    notes: str = ""


class PresentationData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slide_width_inches: float = 13.333
    slide_height_inches: float = 7.5
    slides: list[SlideData]


class JobStatus(str, Enum):
    UPLOADING = "uploading"
    RASTERIZING = "rasterizing"
    EDITING_GUIDANCE = "editing_guidance"
    ANALYZING = "analyzing"
    EDITING_REVIEW = "editing_review"
    READY_FOR_GENERATE = "ready_for_generate"
    GENERATING = "generating"
    COMPLETE = "complete"
    FAILED = "failed"


class VisionServiceStatus(str, Enum):
    OK = "ok"
    MISSING_API_KEY = "missing_api_key"
    AUTH_FAILED = "auth_failed"
    BILLING_BLOCKED = "billing_blocked"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    UNKNOWN_ERROR = "unknown_error"


class VisionStatusSnapshot(BaseModel):
    provider: Literal["anthropic"] = "anthropic"
    model: str = ""
    configured: bool = False
    can_analyze: bool = False
    status: VisionServiceStatus = VisionServiceStatus.UNKNOWN_ERROR
    status_code: int | None = None
    message: str = ""
    detail: str | None = None
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    live_check: bool = True


class JobSession(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.UPLOADING
    total_slides: int = 0
    current_slide: int = 0
    ready_slides: int = 0
    message: str = ""
    output_dir: str = ""
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    manifest_version: int = 2
    slides: list[SlideSummary] = Field(default_factory=list)


class Job(JobSession):
    """Legacy alias for older imports."""

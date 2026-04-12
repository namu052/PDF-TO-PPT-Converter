"""편집기에서 사용자가 지정한 영역에 대한 크롭 + Claude OCR/분석.

PLAN.md §3.2, §3.3, §10.6 참조.
`ocr_region`은 텍스트 추출 전용, `analyze_region`은 요소 전체 분석.
"""

from __future__ import annotations

import base64
import io
import json
import logging

import anthropic
from PIL import Image

from app.config import settings
from app.models import BoundingBox

logger = logging.getLogger(__name__)


REGION_OCR_SYSTEM_PROMPT = """You are an OCR specialist. Given a cropped image from a presentation slide, extract the text exactly as it appears.

Return ONLY valid JSON:
{
  "paragraphs": [
    {
      "runs": [
        {
          "text": "exact text",
          "font": {
            "family": "detected font name",
            "size_pt": 18.0,
            "bold": false,
            "italic": false,
            "underline": false,
            "color_hex": "#000000"
          }
        }
      ],
      "alignment": "left | center | right",
      "bullet": false,
      "bullet_char": null,
      "indent_level": 0
    }
  ],
  "confidence": "high | medium | low"
}

Rules:
- Preserve exact spelling, punctuation, and case
- If the image contains no readable text, return {"paragraphs": [], "confidence": "low"}
- Each visual line of text = one paragraph
- Different formatting within a line = separate runs
"""

REGION_OCR_USER_PROMPT = "Read all text in this cropped image. Preserve formatting."


REGION_ANALYSIS_SYSTEM_PROMPT = """You are a slide element analyzer. Given a cropped region of a presentation slide, identify what element is shown and describe its properties.

Return ONLY valid JSON matching the SlideElement schema:
{
  "element_type": "text_box | shape | line | connector | image | table",
  "paragraphs": [...],
  "shape_kind": "rectangle | rounded_rectangle | ellipse | triangle | right_arrow | diamond | hexagon | cloud | star | chevron | callout | null",
  "fill_color_hex": "#hex or null",
  "fill_opacity": 1.0,
  "border_color_hex": "#hex or null",
  "border_width_pt": 1.0,
  "line_color_hex": null,
  "line_width_pt": 1.0,
  "line_style": "solid | dashed | dotted",
  "start_arrow": "none | triangle | stealth | open",
  "end_arrow": "none | triangle | stealth | open",
  "image_description": null,
  "image_placeholder_color": "#E0E0E0",
  "name": "descriptive element name"
}

Note: bbox will be set by the caller based on the crop region. Do not include bbox.
Only include fields relevant to the detected element type.
"""

REGION_ANALYSIS_USER_PROMPT = (
    "Analyze this element. What type is it and what are its properties?"
)


# 너무 작은 크롭은 OCR 품질이 낮으므로 low confidence로 즉시 반환
_MIN_CROP_WIDTH_PX = 20
_MIN_CROP_HEIGHT_PX = 10


def _get_client() -> anthropic.AsyncAnthropic:
    """Anthropic 비동기 클라이언트를 생성한다. API 키는 settings에서만 로드."""
    api_key = settings.anthropic_api_key
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."
        )
    return anthropic.AsyncAnthropic(api_key=api_key)


def _strip_code_fences(text: str) -> str:
    """마크다운 코드 펜스 제거."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def crop_image(image_bytes: bytes, region: BoundingBox) -> bytes:
    """슬라이드 이미지에서 % 좌표로 지정된 영역을 크롭한다.

    region의 픽셀 좌표는 이미지 경계로 클램핑된다. 반환값은 PNG 바이트.
    Pillow의 손상된 이미지 예외는 상위로 전파한다.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
    except Exception as e:
        raise ValueError(f"유효하지 않은 이미지: {e}") from e

    w, h = img.size
    if w <= 0 or h <= 0:
        raise ValueError("이미지 크기가 0입니다")

    left = int((region.x_pct / 100) * w)
    top = int((region.y_pct / 100) * h)
    right = int(((region.x_pct + region.width_pct) / 100) * w)
    bottom = int(((region.y_pct + region.height_pct) / 100) * h)

    # 경계 클램핑 (최소 1px 보장)
    left = max(0, min(left, w - 1))
    top = max(0, min(top, h - 1))
    right = max(left + 1, min(right, w))
    bottom = max(top + 1, min(bottom, h))

    cropped = img.crop((left, top, right, bottom))

    # PNG가 아닌 입력(RGBA 등)도 안전하게 PNG로 저장
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def _is_too_small(cropped_bytes: bytes) -> bool:
    """크롭 결과가 너무 작아 OCR 가치가 없는지 확인."""
    try:
        img = Image.open(io.BytesIO(cropped_bytes))
        return img.width < _MIN_CROP_WIDTH_PX or img.height < _MIN_CROP_HEIGHT_PX
    except Exception:
        return True


async def _call_vision(
    client: anthropic.AsyncAnthropic,
    image_b64: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
) -> str:
    """Claude Vision API 호출 헬퍼. 원시 응답 텍스트를 반환."""
    message = await client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": user_prompt},
                ],
            }
        ],
    )
    return message.content[0].text


async def ocr_region(image_bytes: bytes, region: BoundingBox) -> dict:
    """지정 영역의 텍스트를 OCR로 추출한다.

    Returns:
        {"paragraphs": [...], "confidence": "high|medium|low"}
        실패/빈 결과 시 paragraphs=[], confidence="low"
    """
    try:
        cropped = crop_image(image_bytes, region)
    except ValueError as e:
        logger.warning(f"OCR crop 실패: {e}")
        return {"paragraphs": [], "confidence": "low"}

    if _is_too_small(cropped):
        return {"paragraphs": [], "confidence": "low"}

    try:
        client = _get_client()
        image_b64 = base64.standard_b64encode(cropped).decode()
        raw = await _call_vision(
            client,
            image_b64,
            REGION_OCR_SYSTEM_PROMPT,
            REGION_OCR_USER_PROMPT,
        )
        parsed = json.loads(_strip_code_fences(raw))

        # 최소 스키마 검증: paragraphs 키 존재 여부
        if not isinstance(parsed, dict) or "paragraphs" not in parsed:
            logger.warning(f"OCR 응답 스키마 불일치: {parsed}")
            return {"paragraphs": [], "confidence": "low"}

        parsed.setdefault("confidence", "medium")
        return parsed

    except json.JSONDecodeError as e:
        logger.warning(f"OCR JSON 파싱 실패: {e}")
        return {"paragraphs": [], "confidence": "low"}
    except anthropic.APIStatusError as e:
        if getattr(e, "status_code", None) == 401:
            raise
        logger.warning(f"OCR API 오류: {e}")
        return {"paragraphs": [], "confidence": "low"}
    except Exception as e:
        logger.warning(f"OCR 실패: {type(e).__name__}: {e}")
        return {"paragraphs": [], "confidence": "low"}


async def analyze_region(image_bytes: bytes, region: BoundingBox) -> dict:
    """지정 영역의 요소를 전체 분석한다 (텍스트 + 도형 + 색상 등).

    Returns:
        SlideElement 속성 딕셔너리 (bbox 제외). 실패 시 빈 text_box 대체.
    """
    try:
        cropped = crop_image(image_bytes, region)
    except ValueError as e:
        logger.warning(f"Region crop 실패: {e}")
        return {"element_type": "text_box", "paragraphs": []}

    try:
        client = _get_client()
        image_b64 = base64.standard_b64encode(cropped).decode()
        raw = await _call_vision(
            client,
            image_b64,
            REGION_ANALYSIS_SYSTEM_PROMPT,
            REGION_ANALYSIS_USER_PROMPT,
        )
        parsed = json.loads(_strip_code_fences(raw))

        if not isinstance(parsed, dict):
            logger.warning(f"Region 분석 응답이 dict가 아님: {parsed}")
            return {"element_type": "text_box", "paragraphs": []}

        # bbox는 호출자가 세팅하므로 모델 응답에 있더라도 제거
        parsed.pop("bbox", None)
        parsed.setdefault("element_type", "text_box")
        parsed.setdefault("paragraphs", [])
        return parsed

    except json.JSONDecodeError as e:
        logger.warning(f"Region 분석 JSON 파싱 실패: {e}")
        return {"element_type": "text_box", "paragraphs": []}
    except anthropic.APIStatusError as e:
        if getattr(e, "status_code", None) == 401:
            raise
        logger.warning(f"Region 분석 API 오류: {e}")
        return {"element_type": "text_box", "paragraphs": []}
    except Exception as e:
        logger.warning(f"Region 분석 실패: {type(e).__name__}: {e}")
        return {"element_type": "text_box", "paragraphs": []}

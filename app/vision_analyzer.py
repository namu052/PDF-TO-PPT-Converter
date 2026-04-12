"""Claude Vision 기반 슬라이드 전체 분석기.

PLAN.md §3, §10.6, §22 참조.
슬라이드 PNG 이미지를 Claude Vision API로 전송하여 구조화된 JSON(배경,
텍스트 박스, 도형, 이미지 등)으로 변환한다.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re

import anthropic

from app.config import settings
from app.models import FinalElement, SlideData, SlideElement

logger = logging.getLogger(__name__)


FULL_ANALYSIS_SYSTEM_PROMPT = """You are an expert slide element extractor. Given a presentation slide image, identify every visual element and output ONLY valid JSON (no markdown fences, no commentary).

## Output Schema

{
  "background_color_hex": "#RRGGBB",
  "elements": [
    {
      "element_type": "text_box | shape | line | connector | image | table",
      "bbox": {
        "x_pct": <float, left edge as % of slide width, 0-100>,
        "y_pct": <float, top edge as % of slide height, 0-100>,
        "width_pct": <float, width as % of slide width>,
        "height_pct": <float, height as % of slide height>
      },
      "paragraphs": [
        {
          "runs": [
            {
              "text": "exact text content",
              "font": {
                "family": "font name",
                "size_pt": 18.0,
                "bold": false,
                "italic": false,
                "underline": false,
                "color_hex": "#000000"
              }
            }
          ],
          "alignment": "left | center | right | justify",
          "bullet": false,
          "bullet_char": null,
          "indent_level": 0,
          "line_spacing": 1.2
        }
      ],
      "shape_kind": "rectangle | rounded_rectangle | ellipse | triangle | right_arrow | diamond | hexagon | cloud | star | chevron | callout | null",
      "fill_color_hex": "#hex or null",
      "fill_opacity": 1.0,
      "border_color_hex": "#hex or null",
      "border_width_pt": 1.0,
      "corner_radius_pct": null,
      "line_color_hex": null,
      "line_width_pt": 1.0,
      "line_style": "solid | dashed | dotted",
      "start_arrow": "none | triangle | stealth | open",
      "end_arrow": "none | triangle | stealth | open",
      "image_description": "description of the image/icon or null",
      "image_placeholder_color": "#hex or null",
      "table_rows": null,
      "table_cols": null,
      "table_data": null,
      "rotation": 0,
      "z_order": 0,
      "opacity": 1.0,
      "name": "descriptive element name"
    }
  ]
}

## 분석 규칙

### 위치/크기
- 모든 좌표는 슬라이드 전체 크기 대비 %로 추정
- 가능한 한 정확하게 추정하되, 요소 간 겹침이 없도록 주의
- 작은 여백(padding)은 bbox에 포함하지 않음 — 텍스트 경계에 가깝게

### 텍스트
- 텍스트를 한 글자도 빠뜨리지 말 것
- 줄바꿈은 별도 paragraph로 분리
- 같은 줄에서 서식이 다른 부분은 별도 run으로 분리 (예: 일부만 볼드)
- 불릿 포인트는 bullet=true, bullet_char에 실제 기호 기록
- 들여쓰기 레벨 구분 (0=최상위, 1=1단계 들여쓰기...)
- 폰트: 확신 없으면 "Calibri" 사용. NotebookLM은 주로 Google Sans 사용
- 폰트 크기 추정: 제목 28-36pt, 부제목 20-24pt, 본문 14-18pt, 캡션 10-12pt

### 도형
- 텍스트가 들어간 도형 → element_type="shape" + paragraphs 채움
- 빈 도형 → element_type="shape" + paragraphs 비움
- 둥근 모서리 사각형: shape_kind="rounded_rectangle", corner_radius_pct 추정 (5-20%)
- 그라데이션 채움 → fill_color_hex에 지배적 색상 사용

### 선/커넥터
- 수평/수직/대각선 → element_type="line", bbox는 선의 바운딩 사각형
- 화살표 끝 → end_arrow 설정
- 도형을 연결하는 선 → element_type="connector"

### 이미지/아이콘
- 사진, 차트, 복잡한 아이콘 → element_type="image"
- image_description에 "bar chart showing revenue by quarter" 같이 구체적으로 기술
- image_placeholder_color: 이미지 요소일 때만 지배적 색상(#RRGGBB) 지정. 이미지가 아닌 요소(text_box, shape, line, connector, table)에서는 반드시 null

### 테이블
- element_type="table", table_rows/table_cols 지정
- table_data에 [row][col] 형태로 각 셀 텍스트

### z-order
- 배경 요소: 0
- 주요 내용: 1-5
- 전경/오버레이: 6+
- 요소가 겹칠 경우 앞에 보이는 것이 더 큰 z_order

### name
- 각 요소에 설명적 이름 부여: "slide_title", "body_text", "blue_rectangle_1", "arrow_right" 등
"""

FULL_ANALYSIS_USER_PROMPT = (
    "Extract all visual elements from this presentation slide. "
    "Be thorough — do not miss any element, no matter how small."
)


def _get_client() -> anthropic.AsyncAnthropic:
    """Anthropic 비동기 클라이언트를 생성한다.

    API 키는 settings를 통해 환경 변수에서만 로드한다 (하드코딩 금지).
    """
    api_key = settings.anthropic_api_key
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."
        )
    return anthropic.AsyncAnthropic(api_key=api_key)


def _strip_code_fences(text: str) -> str:
    """응답에서 마크다운 코드 펜스를 제거한다."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _repair_json(raw_text: str) -> str | None:
    """Claude 응답에서 깨진 JSON을 최대한 복구한다.

    흔한 문제:
    1. 마크다운 코드 펜스 감싸기
    2. 마지막 쉼표 (trailing comma)
    3. 한줄 주석 포함
    4. 불완전한 JSON (토큰 제한으로 잘림)
    """
    text = _strip_code_fences(raw_text)

    # trailing comma 제거: ,} → }, ,] → ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # 한줄 주석 제거: // ...
    text = re.sub(r"//[^\n]*", "", text)

    # 불완전한 JSON 복구: 브래킷 카운트 후 마지막 완전 } 까지 자르기
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")

    if open_braces > 0 or open_brackets > 0:
        last_complete = text.rfind("}")
        if last_complete > 0:
            text = text[: last_complete + 1]
            open_braces = text.count("{") - text.count("}")
            open_brackets = text.count("[") - text.count("]")
            text += "]" * max(0, open_brackets)
            text += "}" * max(0, open_braces)

    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        return None


def _parse_response(raw: str) -> dict:
    """Claude 응답 텍스트를 dict로 파싱한다. 실패 시 복구 시도."""
    try:
        return json.loads(_strip_code_fences(raw))
    except json.JSONDecodeError:
        repaired = _repair_json(raw)
        if repaired:
            return json.loads(repaired)
        raise


def _build_slide_data(slide_number: int, parsed: dict) -> SlideData:
    """파싱된 dict를 SlideData로 변환. 개별 요소 실패는 skip."""
    raw_elements = parsed.get("elements", [])
    valid_elements: list[SlideElement] = []
    for elem_dict in raw_elements:
        try:
            valid_elements.append(SlideElement(**elem_dict))
        except Exception as e:
            logger.warning(
                f"Slide {slide_number}: 요소 무시 — {type(e).__name__}: {e}"
            )

    dropped = len(raw_elements) - len(valid_elements)
    if dropped > 0:
        logger.warning(
            f"Slide {slide_number}: {dropped}/{len(raw_elements)} 요소 드롭됨 "
            f"(유효 {len(valid_elements)}개 유지)"
        )

    return SlideData(
        slide_number=slide_number,
        background_color_hex=parsed.get("background_color_hex", "#FFFFFF"),
        elements=valid_elements,
    )


async def _call_vision_api(
    client: anthropic.AsyncAnthropic, image_b64: str
) -> str:
    """Claude Vision API 1회 호출. 원시 응답 텍스트를 반환."""
    message = await client.messages.create(
        model=settings.claude_model,
        max_tokens=settings.claude_max_tokens,
        system=FULL_ANALYSIS_SYSTEM_PROMPT,
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
                    {"type": "text", "text": FULL_ANALYSIS_USER_PROMPT},
                ],
            }
        ],
    )
    return message.content[0].text


async def analyze_slide(image_bytes: bytes, slide_number: int) -> SlideData:
    """슬라이드 이미지를 Claude Vision으로 분석한다.

    최대 3회 재시도. JSON 파싱 실패 시 _repair_json으로 복구 시도.
    모든 시도가 실패하면 빈 슬라이드를 반환 (파이프라인은 계속 진행).
    """
    client = _get_client()
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    last_error: Exception | None = None

    for attempt in range(3):
        try:
            raw = await _call_vision_api(client, image_b64)
            parsed = _parse_response(raw)
            raw_count = len(parsed.get("elements", []))
            slide = _build_slide_data(slide_number, parsed)
            logger.info(
                f"Slide {slide_number}: {len(slide.elements)}/{raw_count} "
                f"elements extracted"
            )
            return slide

        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(
                f"Slide {slide_number}: JSON 파싱 실패 (시도 {attempt + 1}/3)"
            )
            if attempt < 2:
                await asyncio.sleep(1)

        except anthropic.RateLimitError as e:
            last_error = e
            wait = 2**attempt
            logger.warning(f"Rate limit hit, waiting {wait}s...")
            await asyncio.sleep(wait)

        except anthropic.APIStatusError as e:
            # 401 인증 실패는 즉시 중단 (재시도 무의미)
            if getattr(e, "status_code", None) == 401:
                raise
            last_error = e
            logger.warning(f"API error (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                await asyncio.sleep(2**attempt)

        except Exception as e:
            last_error = e
            logger.exception(f"Unexpected error on slide {slide_number}: {e}")
            if attempt < 2:
                await asyncio.sleep(2**attempt)

    logger.error(f"Slide {slide_number}: 모든 시도 실패 — {last_error}")
    return SlideData(slide_number=slide_number, elements=[])


GUIDED_ANALYSIS_SYSTEM_PROMPT = """You are assisting a manual-first PDF to PPT workflow.

Inputs:
1. The original slide image.
2. A user-authored guide overlay image.
3. A text summary of the guidance annotations.
4. A list of locked elements that must be preserved.

Rules:
- The overlay is user intent, not the final visual itself.
- Prefer fidelity-first reconstruction.
- If a visual is hard to reproduce precisely with editable PPT primitives, set render_strategy to "hybrid" or "raster".
- Do not regenerate locked elements.
- Return only valid JSON.

Schema:
{
  "summary": "short summary",
  "confidence": "high | medium | low",
  "elements": [slide element objects]
}
"""


async def analyze_slide_with_guidance(
    *,
    source_bytes: bytes,
    overlay_bytes: bytes,
    guide_summary: str,
    slide_number: int,
    locked_elements: list[FinalElement],
) -> dict:
    client = _get_client()
    source_b64 = base64.standard_b64encode(source_bytes).decode()
    overlay_b64 = base64.standard_b64encode(overlay_bytes).decode()
    locked_summary = "\n".join(
        f"- {element.final_id}: {element.element_type.value} {element.bbox.model_dump()}"
        for element in locked_elements
    ) or "- none"

    message = await client.messages.create(
        model=settings.claude_model,
        max_tokens=settings.claude_max_tokens,
        system=GUIDED_ANALYSIS_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": source_b64,
                        },
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": overlay_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Slide number: {slide_number}\n"
                            f"Guide summary:\n{guide_summary or '- none'}\n\n"
                            f"Locked elements:\n{locked_summary}\n\n"
                            "Return candidate elements for only the remaining content."
                        ),
                    },
                ],
            }
        ],
    )
    raw = message.content[0].text
    parsed = _parse_response(raw)
    return {
        "summary": parsed.get("summary", "Guided analysis completed."),
        "confidence": parsed.get("confidence", "medium"),
        "elements": parsed.get("elements", []),
        "revision_hint": "Prefer hybrid or raster output when visual fidelity is uncertain.",
        "raw": raw,
    }

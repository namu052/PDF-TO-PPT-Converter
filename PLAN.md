# PDF-to-PPT Converter 상세 구현 계획

## Context
Google NotebookLM에서 생성한 슬라이드를 PDF로 내보낸 뒤, 각 페이지의 이미지 속 요소(텍스트, 도형, 선, 아이콘 등)를 개별 PowerPoint 구성요소로 분리/재구성하는 웹 앱. Claude Vision API로 자동 추출 + 사용자가 Canvas 기반 편집기에서 수동으로 요소를 지정/수정/삭제할 수 있다.

## Tech Stack
- **Python 3.11+** + **FastAPI** (백엔드)
- **Anthropic SDK** (`anthropic` 패키지) — Claude Vision API
- **python-pptx** — 네이티브 PPT 생성
- **PyMuPDF (fitz)** — PDF → PNG 변환 (외부 바이너리 불필요)
- **Pillow** — 이미지 크롭 (수동 OCR 영역)
- **프론트엔드** — Vanilla HTML/CSS/JS, HTML5 Canvas (빌드 도구 없음)

---

## 프로젝트 구조

```
PDF_TO_PPT_Converter/
├── requirements.txt
├── .env.example                  # ANTHROPIC_API_KEY=sk-ant-...
├── .env                          # (gitignored)
├── .gitignore
├── run.py                        # uvicorn 실행 진입점
├── app/
│   ├── __init__.py
│   ├── main.py                   # FastAPI 앱 팩토리, 라우터 등록, CORS
│   ├── config.py                 # pydantic-settings: Settings 클래스
│   ├── models.py                 # 전체 Pydantic 데이터 모델
│   ├── job_manager.py            # Job 상태 관리 (in-memory dict)
│   ├── pipeline.py               # 3단계 오케스트레이터
│   ├── pdf_to_images.py          # PDF → PNG 리스트
│   ├── vision_analyzer.py        # Claude Vision 프롬프트 + 파싱
│   ├── region_ocr.py             # 수동 영역 크롭 → Claude OCR
│   ├── pptx_builder.py           # SlideData → python-pptx
│   ├── color_utils.py            # hex ↔ RGBColor 변환
│   ├── font_utils.py             # 폰트 이름 매핑/폴백
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── upload.py             # POST /api/convert, GET /api/status
│   │   ├── editor_api.py         # GET/PUT /api/slides, GET /api/slide-image
│   │   ├── ocr.py                # POST /api/ocr-region
│   │   └── generate.py           # POST /api/generate, GET /api/download
│   └── static/
│       ├── index.html            # 업로드 페이지
│       ├── editor.html           # 인터랙티브 편집기
│       ├── css/
│       │   ├── common.css        # 공통 스타일 (색상 변수, 타이포그래피)
│       │   ├── index.css         # 업로드 페이지 스타일
│       │   └── editor.css        # 편집기 레이아웃, 패널, 도구 모음
│       └── js/
│           ├── upload.js         # 업로드 + 진행률 폴링
│           ├── editor-app.js     # 편집기 메인 진입점, 상태 관리
│           ├── canvas-renderer.js # Canvas 렌더링 (이미지 + 오버레이)
│           ├── element-tools.js  # 선택/그리기/리사이즈 도구
│           ├── property-panel.js # 우측 속성 패널 바인딩
│           ├── slide-nav.js      # 슬라이드 목록/썸네일 내비게이션
│           └── api-client.js     # fetch 래퍼 (모든 API 호출)
├── tests/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_pdf_to_images.py
│   ├── test_vision_analyzer.py
│   ├── test_pptx_builder.py
│   ├── test_region_ocr.py
│   └── fixtures/
│       ├── sample_slide.pdf      # 테스트용 1페이지 PDF
│       └── sample_vision_response.json
└── output/                       # job별 하위 폴더 (gitignored)
    └── {job_id}/
        ├── images/               # 슬라이드 PNG들
        ├── slides.json           # 현재 SlideData (자동추출 or 편집 후)
        └── result.pptx           # 최종 생성 파일
```

---

## 1. 데이터 모델 상세 (`app/models.py`)

### 1.1 좌표 시스템

```python
class BoundingBox(BaseModel):
    """슬라이드 전체 크기 대비 % 좌표 (0.0 ~ 100.0)"""
    x_pct: float = Field(ge=0, le=100, description="좌측 가장자리로부터의 % 위치")
    y_pct: float = Field(ge=0, le=100, description="상단 가장자리로부터의 % 위치")
    width_pct: float = Field(gt=0, le=100, description="너비 %")
    height_pct: float = Field(gt=0, le=100, description="높이 %")
```

### 1.2 요소 타입 열거

```python
class ElementType(str, Enum):
    TEXT_BOX = "text_box"       # 순수 텍스트 상자
    SHAPE = "shape"             # 도형 (내부 텍스트 가능)
    LINE = "line"               # 직선/화살표
    CONNECTOR = "connector"     # 도형 연결선
    IMAGE = "image"             # 사진/아이콘 (래스터 — placeholder로 변환)
    TABLE = "table"             # 표
    GROUP = "group"             # 그룹화된 요소들의 컨테이너

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
```

### 1.3 텍스트 모델

```python
class FontInfo(BaseModel):
    family: str = "Calibri"
    size_pt: float = Field(default=18.0, gt=0, le=200)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color_hex: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")

class TextRun(BaseModel):
    """하나의 서식이 동일한 텍스트 조각"""
    text: str
    font: FontInfo = Field(default_factory=FontInfo)
    hyperlink: str | None = None  # URL

class TextParagraph(BaseModel):
    runs: list[TextRun] = Field(min_length=1)
    alignment: Literal["left", "center", "right", "justify"] = "left"
    bullet: bool = False
    bullet_char: str | None = None  # "•", "◦", "1.", "a." 등
    indent_level: int = Field(default=0, ge=0, le=5)
    line_spacing: float = Field(default=1.0, gt=0)  # 배수
```

### 1.4 슬라이드 요소

```python
class SlideElement(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:8])  # 프론트엔드 추적용
    element_type: ElementType
    bbox: BoundingBox
    source: Literal["auto", "manual"] = "auto"  # 자동 검출 vs 수동 추가 구분

    # 텍스트 (text_box, shape 내부 텍스트)
    paragraphs: list[TextParagraph] = []

    # 도형
    shape_kind: ShapeKind | None = None
    fill_color_hex: str | None = None
    fill_opacity: float = Field(default=1.0, ge=0, le=1)  # 0=투명, 1=불투명
    border_color_hex: str | None = None
    border_width_pt: float | None = None
    corner_radius_pct: float | None = None  # rounded_rectangle 모서리 반경

    # 선/커넥터
    line_color_hex: str | None = None
    line_width_pt: float = 1.0
    line_style: LineStyle = LineStyle.SOLID
    start_arrow: ArrowType = ArrowType.NONE
    end_arrow: ArrowType = ArrowType.NONE

    # 이미지 (래스터 복원 불가 → 설명만)
    image_description: str | None = None
    image_placeholder_color: str = "#E0E0E0"

    # 테이블
    table_rows: int | None = None
    table_cols: int | None = None
    table_data: list[list[str]] | None = None  # [row][col] 텍스트

    # 공통
    rotation: float = Field(default=0.0, ge=0, lt=360)
    z_order: int = Field(default=0, ge=0)
    opacity: float = Field(default=1.0, ge=0, le=1)
    name: str = ""  # 사용자 지정 이름 (편집기에서 표시)
```

### 1.5 슬라이드 & 프레젠테이션

```python
class SlideData(BaseModel):
    slide_number: int = Field(ge=1)
    background_color_hex: str = "#FFFFFF"
    background_gradient: dict | None = None  # 향후 확장
    elements: list[SlideElement] = []
    notes: str = ""  # 발표자 노트

class PresentationData(BaseModel):
    slide_width_inches: float = 13.333   # 16:9
    slide_height_inches: float = 7.5
    slides: list[SlideData] = []
```

### 1.6 Job 모델

```python
class JobStatus(str, Enum):
    UPLOADING = "uploading"
    EXTRACTING = "extracting"     # 자동 추출 중
    EDITING = "editing"           # 편집기에서 대기 중
    GENERATING = "generating"     # PPTX 생성 중
    COMPLETE = "complete"
    FAILED = "failed"

class Job(BaseModel):
    job_id: str
    status: JobStatus = JobStatus.UPLOADING
    total_slides: int = 0
    current_slide: int = 0
    message: str = ""
    output_dir: str = ""
    error: str | None = None
    created_at: datetime
```

---

## 2. API 엔드포인트 상세

### 2.1 업로드 & 자동 추출 (`app/routers/upload.py`)

```
POST /api/convert
  Request: multipart/form-data { file: PDF }
  Validation:
    - 확장자 .pdf 확인
    - 파일 크기 ≤ 50MB
    - PyMuPDF로 열기 시도 → 실패 시 400
    - 페이지 수 ≤ 50
  Response 202: { job_id: "abc12345", total_slides: 12 }
  Background: pipeline.extract_all_slides(job_id) 시작

GET /api/status/{job_id}
  Response 200: {
    status: "extracting",
    total_slides: 12,
    current_slide: 5,
    message: "슬라이드 5/12 분석 중..."
  }
  404 if job_id not found
```

### 2.2 편집기 데이터 (`app/routers/editor_api.py`)

```
GET /api/slides/{job_id}
  Response 200: PresentationData JSON (전체 슬라이드 + 요소)
  403 if status not in [editing, complete]

PUT /api/slides/{job_id}
  Request: PresentationData JSON (편집기에서 수정된 전체 데이터)
  Validation: Pydantic으로 전체 검증
  Response 200: { saved: true }
  Side effect: output/{job_id}/slides.json 덮어쓰기

GET /api/slide-image/{job_id}/{slide_num}
  Response: image/png (해당 슬라이드 래스터 이미지)
  Cache-Control: max-age=3600 (이미지는 불변)

PUT /api/element/{job_id}/{slide_num}/{element_id}
  Request: SlideElement JSON (단일 요소 업데이트)
  Response 200: { updated: true }
  용도: 속성 패널에서 하나의 요소만 수정할 때 전체 PUT 대신 사용

DELETE /api/element/{job_id}/{slide_num}/{element_id}
  Response 200: { deleted: true }
```

### 2.3 수동 OCR (`app/routers/ocr.py`)

```
POST /api/ocr-region
  Request: {
    job_id: "abc12345",
    slide_num: 3,
    region: { x_pct: 10.5, y_pct: 20.0, width_pct: 30.0, height_pct: 15.0 }
  }
  Processing:
    1. output/{job_id}/images/slide_3.png 로드
    2. region을 픽셀로 변환, Pillow로 크롭
    3. 크롭 이미지를 Claude Vision에 전송 (텍스트 전용 프롬프트)
    4. 결과 파싱
  Response 200: {
    text: "추출된 텍스트 내용",
    paragraphs: [TextParagraph...],
    confidence: "high" | "medium" | "low"
  }
  Timeout: 15초 (단일 영역이므로 빠름)

POST /api/analyze-region
  Request: {
    job_id: "abc12345",
    slide_num: 3,
    region: { x_pct: 10.5, y_pct: 20.0, width_pct: 30.0, height_pct: 15.0 }
  }
  Processing: 크롭 이미지를 Claude Vision에 전송 (요소 전체 분석 프롬프트)
  Response 200: SlideElement (해당 영역에서 감지된 단일 요소)
  용도: 텍스트뿐 아니라 도형/아이콘 등도 분석하고 싶을 때
```

### 2.4 PPTX 생성 & 다운로드 (`app/routers/generate.py`)

```
POST /api/generate/{job_id}
  Processing:
    1. output/{job_id}/slides.json 읽기 (최신 편집 상태)
    2. pptx_builder.build_presentation() 호출
    3. output/{job_id}/result.pptx 저장
  Response 200: { ready: true, filename: "presentation.pptx" }

GET /api/download/{job_id}
  Response: application/vnd.openxmlformats-officedocument.presentationml.presentation
  Content-Disposition: attachment; filename="NotebookLM_Slides.pptx"
```

---

## 3. Claude Vision 프롬프트 상세 (`app/vision_analyzer.py`)

### 3.1 전체 슬라이드 분석 프롬프트

```python
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
      "image_placeholder_color": "#E0E0E0",
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
- image_placeholder_color에 이미지의 지배적 색상

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

FULL_ANALYSIS_USER_PROMPT = "Extract all visual elements from this presentation slide. Be thorough — do not miss any element, no matter how small."
```

### 3.2 수동 영역 OCR 프롬프트 (`app/region_ocr.py`)

```python
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
```

### 3.3 수동 영역 전체 분석 프롬프트

```python
REGION_ANALYSIS_SYSTEM_PROMPT = """You are a slide element analyzer. Given a cropped region of a presentation slide, identify what element is shown and describe its properties.

Return ONLY valid JSON matching the SlideElement schema:
{
  "element_type": "...",
  "paragraphs": [...],
  "shape_kind": "...",
  "fill_color_hex": "...",
  ...
}

Note: bbox will be set by the caller based on the crop region. Do not include bbox.
Only include fields relevant to the detected element type.
"""
```

### 3.4 API 호출 패턴

```python
async def analyze_slide(image_bytes: bytes, slide_number: int) -> SlideData:
    client = anthropic.AsyncAnthropic()  # API key from env
    
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    
    for attempt in range(3):  # 최대 3회 시도
        try:
            message = await client.messages.create(
                model=settings.claude_model,  # claude-sonnet-4-20250514
                max_tokens=8192,  # 복잡한 슬라이드는 토큰 많이 사용
                system=FULL_ANALYSIS_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64
                        }},
                        {"type": "text", "text": FULL_ANALYSIS_USER_PROMPT}
                    ]
                }]
            )
            
            raw = message.content[0].text
            # 마크다운 코드 펜스 제거
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]  # 첫 줄 제거
                raw = raw.rsplit("```", 1)[0]  # 마지막 ``` 제거
            
            parsed = json.loads(raw)
            slide = SlideData(
                slide_number=slide_number,
                background_color_hex=parsed.get("background_color_hex", "#FFFFFF"),
                elements=[SlideElement(**e) for e in parsed.get("elements", [])]
            )
            return slide
            
        except json.JSONDecodeError as e:
            if attempt == 2:
                # 3회 실패 → 빈 슬라이드 반환
                return SlideData(slide_number=slide_number, elements=[])
            # 재시도: Claude에게 오류 정보와 함께 수정 요청
            # (다음 시도에서 더 강한 지시)
            continue
            
        except anthropic.APIError as e:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)  # 1s, 2s 백오프
```

---

## 4. PPTX 빌더 상세 (`app/pptx_builder.py`)

### 4.1 좌표 변환

```python
from pptx.util import Inches, Pt, Emu

def pct_to_emu(pct: float, total_inches: float) -> int:
    """% 값을 EMU(English Metric Units)로 변환. 1 inch = 914400 EMU"""
    return int(Inches((pct / 100.0) * total_inches))

# 사용 예: x_emu = pct_to_emu(elem.bbox.x_pct, prs_data.slide_width_inches)
```

### 4.2 요소 타입별 빌드 함수

```python
def _build_text_box(slide, elem: SlideElement, w_in: float, h_in: float):
    left = pct_to_emu(elem.bbox.x_pct, w_in)
    top = pct_to_emu(elem.bbox.y_pct, h_in)
    width = pct_to_emu(elem.bbox.width_pct, w_in)
    height = pct_to_emu(elem.bbox.height_pct, h_in)
    
    txBox = slide.shapes.add_textbox(left, top, width, height)
    txBox.rotation = elem.rotation
    _apply_paragraphs(txBox.text_frame, elem.paragraphs)

def _build_shape(slide, elem: SlideElement, w_in: float, h_in: float):
    shape_type = SHAPE_KIND_TO_MSO[elem.shape_kind]  # 매핑 딕셔너리
    left = pct_to_emu(elem.bbox.x_pct, w_in)
    top = pct_to_emu(elem.bbox.y_pct, h_in)
    width = pct_to_emu(elem.bbox.width_pct, w_in)
    height = pct_to_emu(elem.bbox.height_pct, h_in)
    
    shape = slide.shapes.add_shape(shape_type, left, top, width, height)
    shape.rotation = elem.rotation
    
    # 채우기
    if elem.fill_color_hex:
        shape.fill.solid()
        shape.fill.fore_color.rgb = hex_to_rgb(elem.fill_color_hex)
        # 투명도 (0=불투명, 100000=투명 — python-pptx는 oxml 조작 필요)
        if elem.fill_opacity < 1.0:
            _set_fill_opacity(shape, elem.fill_opacity)
    else:
        shape.fill.background()  # 투명
    
    # 테두리
    if elem.border_color_hex:
        shape.line.color.rgb = hex_to_rgb(elem.border_color_hex)
        shape.line.width = Pt(elem.border_width_pt or 1.0)
    else:
        shape.line.fill.background()  # 테두리 없음
    
    # 내부 텍스트
    if elem.paragraphs:
        _apply_paragraphs(shape.text_frame, elem.paragraphs)

def _build_line(slide, elem: SlideElement, w_in: float, h_in: float):
    """선은 bbox의 좌상단→우하단 또는 좌하단→우상단으로 그린다."""
    x1 = pct_to_emu(elem.bbox.x_pct, w_in)
    y1 = pct_to_emu(elem.bbox.y_pct, h_in)
    x2 = pct_to_emu(elem.bbox.x_pct + elem.bbox.width_pct, w_in)
    y2 = pct_to_emu(elem.bbox.y_pct + elem.bbox.height_pct, h_in)
    
    connector = slide.shapes.add_connector(
        MSO_CONNECTOR_TYPE.STRAIGHT, x1, y1, x2, y2
    )
    connector.line.color.rgb = hex_to_rgb(elem.line_color_hex or "#000000")
    connector.line.width = Pt(elem.line_width_pt)
    
    # 화살표
    if elem.end_arrow != ArrowType.NONE:
        _set_arrow_end(connector, elem.end_arrow)
    if elem.start_arrow != ArrowType.NONE:
        _set_arrow_start(connector, elem.start_arrow)
    
    # 선 스타일 (점선, 대시)
    if elem.line_style != LineStyle.SOLID:
        _set_line_dash(connector, elem.line_style)

def _build_image_placeholder(slide, elem: SlideElement, w_in: float, h_in: float):
    """래스터 이미지 복원 불가 → 색상 배경 + 설명 텍스트의 사각형"""
    left = pct_to_emu(elem.bbox.x_pct, w_in)
    top = pct_to_emu(elem.bbox.y_pct, h_in)
    width = pct_to_emu(elem.bbox.width_pct, w_in)
    height = pct_to_emu(elem.bbox.height_pct, h_in)
    
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = hex_to_rgb(elem.image_placeholder_color)
    shape.line.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
    shape.line.width = Pt(0.5)
    
    # 설명 텍스트
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = f"[Image: {elem.image_description or 'unknown'}]"
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    run.font.italic = True

def _build_table(slide, elem: SlideElement, w_in: float, h_in: float):
    if not elem.table_rows or not elem.table_cols or not elem.table_data:
        return
    left = pct_to_emu(elem.bbox.x_pct, w_in)
    top = pct_to_emu(elem.bbox.y_pct, h_in)
    width = pct_to_emu(elem.bbox.width_pct, w_in)
    height = pct_to_emu(elem.bbox.height_pct, h_in)
    
    table_shape = slide.shapes.add_table(
        elem.table_rows, elem.table_cols, left, top, width, height
    )
    table = table_shape.table
    
    for r, row_data in enumerate(elem.table_data):
        for c, cell_text in enumerate(row_data):
            if r < elem.table_rows and c < elem.table_cols:
                table.cell(r, c).text = cell_text
```

### 4.3 텍스트 적용 함수

```python
def _apply_paragraphs(text_frame, paragraphs: list[TextParagraph]):
    text_frame.word_wrap = True
    
    for i, para_data in enumerate(paragraphs):
        if i == 0:
            para = text_frame.paragraphs[0]
        else:
            para = text_frame.add_paragraph()
        
        para.alignment = ALIGN_MAP.get(para_data.alignment, PP_ALIGN.LEFT)
        
        # 불릿
        if para_data.bullet:
            para.level = para_data.indent_level
            # python-pptx에서 불릿 설정은 XML 조작 필요
            _set_bullet(para, para_data.bullet_char)
        
        # 줄간격
        if para_data.line_spacing != 1.0:
            para.line_spacing = Pt(para_data.line_spacing * 12)  # 근사치
        
        for j, run_data in enumerate(para_data.runs):
            run = para.add_run()
            run.text = run_data.text
            
            font = run.font
            font.name = resolve_font(run_data.font.family)  # 폴백 적용
            font.size = Pt(run_data.font.size_pt)
            font.bold = run_data.font.bold
            font.italic = run_data.font.italic
            font.underline = run_data.font.underline
            font.color.rgb = hex_to_rgb(run_data.font.color_hex)
            
            # 하이퍼링크
            if run_data.hyperlink:
                run.hyperlink.address = run_data.hyperlink
```

### 4.4 MSO_SHAPE 매핑 딕셔너리

```python
from pptx.enum.shapes import MSO_SHAPE

SHAPE_KIND_TO_MSO = {
    ShapeKind.RECTANGLE: MSO_SHAPE.RECTANGLE,
    ShapeKind.ROUNDED_RECTANGLE: MSO_SHAPE.ROUNDED_RECTANGLE,
    ShapeKind.ELLIPSE: MSO_SHAPE.OVAL,
    ShapeKind.TRIANGLE: MSO_SHAPE.ISOSCELES_TRIANGLE,
    ShapeKind.RIGHT_ARROW: MSO_SHAPE.RIGHT_ARROW,
    ShapeKind.LEFT_ARROW: MSO_SHAPE.LEFT_ARROW,
    ShapeKind.UP_ARROW: MSO_SHAPE.UP_ARROW,
    ShapeKind.DOWN_ARROW: MSO_SHAPE.DOWN_ARROW,
    ShapeKind.DIAMOND: MSO_SHAPE.DIAMOND,
    ShapeKind.PENTAGON: MSO_SHAPE.PENTAGON,
    ShapeKind.HEXAGON: MSO_SHAPE.HEXAGON,
    ShapeKind.CLOUD: MSO_SHAPE.CLOUD,
    ShapeKind.STAR: MSO_SHAPE.STAR_5_POINT,
    ShapeKind.CHEVRON: MSO_SHAPE.CHEVRON,
    ShapeKind.CALLOUT: MSO_SHAPE.CALLOUT_1,
}
```

### 4.5 배경색 설정

```python
def _set_slide_background(slide, hex_color: str):
    background = slide.background
    fill = background.fill
    fill.solid()
    fill.fore_color.rgb = hex_to_rgb(hex_color)
```

### 4.6 메인 빌드 함수

```python
def build_presentation(data: PresentationData) -> bytes:
    prs = Presentation()
    prs.slide_width = Inches(data.slide_width_inches)
    prs.slide_height = Inches(data.slide_height_inches)
    
    blank_layout = prs.slide_layouts[6]  # blank
    
    BUILDER_MAP = {
        ElementType.TEXT_BOX: _build_text_box,
        ElementType.SHAPE: _build_shape,
        ElementType.LINE: _build_line,
        ElementType.CONNECTOR: _build_line,  # 같은 빌더 사용
        ElementType.IMAGE: _build_image_placeholder,
        ElementType.TABLE: _build_table,
    }
    
    for slide_data in data.slides:
        slide = prs.slides.add_slide(blank_layout)
        
        if slide_data.background_color_hex != "#FFFFFF":
            _set_slide_background(slide, slide_data.background_color_hex)
        
        # z-order 순으로 추가 (낮은 것부터 = 뒤에서부터)
        for elem in sorted(slide_data.elements, key=lambda e: e.z_order):
            builder = BUILDER_MAP.get(elem.element_type)
            if builder:
                try:
                    builder(slide, elem, data.slide_width_inches, data.slide_height_inches)
                except Exception as e:
                    # 개별 요소 실패 시 건너뛰고 계속
                    logger.warning(f"Slide {slide_data.slide_number}, element {elem.id}: {e}")
        
        # 발표자 노트
        if slide_data.notes:
            notes_slide = slide.notes_slide
            notes_slide.notes_text_frame.text = slide_data.notes
    
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
```

---

## 5. 인터랙티브 편집기 상세

### 5.1 전체 화면 레이아웃 (`editor.html`)

```
┌─────────────────────────────────────────────────────────────────┐
│ [Toolbar]  선택 | 텍스트 | 도형▾ | 선 | 삭제 | 재분석 | PPT생성  │
├──────────┬────────────────────────────────────┬──────────────────┤
│ Slide    │                                    │ Property Panel   │
│ Thumbs   │     Canvas Area                    │                  │
│          │     (슬라이드 이미지 + 오버레이)      │ [요소 타입]      │
│ [1] ■    │                                    │ [텍스트 편집]     │
│ [2] ■    │     ┌─────────┐                    │ [폰트 설정]      │
│ [3] ■◀   │     │ element │ ← 선택된 요소       │ [색상]           │
│ [4] ■    │     │  bbox   │   8개 핸들 표시      │ [크기/위치 %]    │
│ [5] ■    │     └─────────┘                    │ [z-order]        │
│          │                                    │ [삭제 버튼]       │
│          │                                    │                  │
├──────────┴────────────────────────────────────┴──────────────────┤
│ [Status Bar] 슬라이드 3/12 | 요소 8개 (자동 6, 수동 2) | 저장됨   │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 상태 관리 (`editor-app.js`)

```javascript
// 전역 앱 상태 (단일 진실의 원천)
const AppState = {
    jobId: null,
    presentation: null,        // PresentationData 전체
    currentSlideIndex: 0,      // 현재 보고 있는 슬라이드 (0-based)
    selectedElementId: null,   // 선택된 요소의 id
    currentTool: 'select',     // select | text | shape | line | delete
    shapeSubTool: 'rectangle', // 도형 도구 선택 시 하위 타입
    isDirty: false,            // 저장되지 않은 변경 있음
    isDrawing: false,          // 현재 그리기 중
    drawStart: null,           // { x_pct, y_pct }
    
    // 현재 슬라이드 데이터 getter
    get currentSlide() {
        return this.presentation?.slides[this.currentSlideIndex] ?? null;
    },
    
    // 선택된 요소 getter
    get selectedElement() {
        if (!this.selectedElementId || !this.currentSlide) return null;
        return this.currentSlide.elements.find(e => e.id === this.selectedElementId);
    }
};
```

### 5.3 Canvas 렌더링 (`canvas-renderer.js`)

```javascript
class CanvasRenderer {
    constructor(canvasElement, imageCanvasElement) {
        this.canvas = canvasElement;        // 전경 (요소 오버레이)
        this.imageCanvas = imageCanvasElement; // 배경 (슬라이드 이미지)
        this.ctx = canvasElement.getContext('2d');
        this.imgCtx = imageCanvasElement.getContext('2d');
    }
    
    // 슬라이드 이미지 로드 및 표시
    async loadSlideImage(jobId, slideNum) { ... }
    
    // 모든 요소의 바운딩 박스 오버레이 렌더링
    renderElements(elements, selectedId) {
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        
        for (const elem of elements) {
            const isSelected = elem.id === selectedId;
            const rect = this.pctToPixel(elem.bbox);
            
            // 요소 타입별 색상
            const color = ELEMENT_COLORS[elem.element_type];
            
            // 바운딩 박스
            this.ctx.strokeStyle = isSelected ? '#FF6B00' : color;
            this.ctx.lineWidth = isSelected ? 3 : 1.5;
            this.ctx.setLineDash(elem.source === 'manual' ? [6, 3] : []);
            this.ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
            
            // 반투명 배경
            this.ctx.fillStyle = color + (isSelected ? '30' : '15');
            this.ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
            
            // 요소 이름 라벨
            this._drawLabel(rect, elem.name || elem.element_type, color);
            
            // 선택된 요소 → 8개 리사이즈 핸들
            if (isSelected) {
                this._drawResizeHandles(rect);
            }
        }
    }
    
    // 리사이즈 핸들 (8개: 4모서리 + 4중점)
    _drawResizeHandles(rect) {
        const handles = this._getHandlePositions(rect);
        for (const h of handles) {
            this.ctx.fillStyle = '#FFFFFF';
            this.ctx.strokeStyle = '#FF6B00';
            this.ctx.lineWidth = 2;
            this.ctx.fillRect(h.x - 4, h.y - 4, 8, 8);
            this.ctx.strokeRect(h.x - 4, h.y - 4, 8, 8);
        }
    }
    
    _getHandlePositions(rect) {
        const { x, y, w, h } = rect;
        return [
            { x: x,         y: y,         cursor: 'nw-resize', type: 'nw' },
            { x: x + w/2,   y: y,         cursor: 'n-resize',  type: 'n'  },
            { x: x + w,     y: y,         cursor: 'ne-resize', type: 'ne' },
            { x: x + w,     y: y + h/2,   cursor: 'e-resize',  type: 'e'  },
            { x: x + w,     y: y + h,     cursor: 'se-resize', type: 'se' },
            { x: x + w/2,   y: y + h,     cursor: 's-resize',  type: 's'  },
            { x: x,         y: y + h,     cursor: 'sw-resize', type: 'sw' },
            { x: x,         y: y + h/2,   cursor: 'w-resize',  type: 'w'  },
        ];
    }
    
    // % ↔ 픽셀 변환
    pctToPixel(bbox) {
        return {
            x: (bbox.x_pct / 100) * this.canvas.width,
            y: (bbox.y_pct / 100) * this.canvas.height,
            w: (bbox.width_pct / 100) * this.canvas.width,
            h: (bbox.height_pct / 100) * this.canvas.height,
        };
    }
    
    pixelToPct(px, py) {
        return {
            x_pct: (px / this.canvas.width) * 100,
            y_pct: (py / this.canvas.height) * 100,
        };
    }
}

// 요소 타입별 오버레이 색상
const ELEMENT_COLORS = {
    text_box:   '#2196F3',  // 파란색
    shape:      '#4CAF50',  // 초록색
    line:       '#F44336',  // 빨간색
    connector:  '#FF9800',  // 주황색
    image:      '#9C27B0',  // 보라색
    table:      '#00BCD4',  // 청록색
    group:      '#795548',  // 갈색
};
```

### 5.4 도구 상호작용 (`element-tools.js`)

```javascript
class ElementTools {
    constructor(renderer, appState) {
        this.renderer = renderer;
        this.state = appState;
        this.dragMode = null;  // null | 'move' | 'resize' | 'draw'
        this.resizeHandle = null;  // 어느 핸들을 잡았는지
        this.dragOrigin = null;
        this.originalBbox = null;
    }
    
    // 마우스 이벤트 핸들러
    onMouseDown(e) {
        const pct = this.renderer.pixelToPct(e.offsetX, e.offsetY);
        
        switch (this.state.currentTool) {
            case 'select':
                this._handleSelectMouseDown(e, pct);
                break;
            case 'text':
            case 'shape':
            case 'line':
                this._startDrawing(pct);
                break;
            case 'delete':
                this._handleDeleteClick(pct);
                break;
        }
    }
    
    onMouseMove(e) {
        const pct = this.renderer.pixelToPct(e.offsetX, e.offsetY);
        
        if (this.dragMode === 'move') {
            this._handleMove(pct);
        } else if (this.dragMode === 'resize') {
            this._handleResize(pct);
        } else if (this.dragMode === 'draw') {
            this._handleDrawPreview(pct);
        } else {
            this._updateCursor(e, pct);
        }
    }
    
    onMouseUp(e) {
        const pct = this.renderer.pixelToPct(e.offsetX, e.offsetY);
        
        if (this.dragMode === 'draw') {
            this._finishDrawing(pct);
        }
        
        this.dragMode = null;
        this.resizeHandle = null;
        this.state.isDirty = true;
    }
    
    // 선택 모드: 클릭 → 요소 선택 또는 핸들 잡기
    _handleSelectMouseDown(e, pct) {
        // 1. 현재 선택된 요소의 핸들 위인지 확인
        if (this.state.selectedElement) {
            const handle = this._hitTestHandle(e.offsetX, e.offsetY);
            if (handle) {
                this.dragMode = 'resize';
                this.resizeHandle = handle;
                this.originalBbox = { ...this.state.selectedElement.bbox };
                this.dragOrigin = pct;
                return;
            }
        }
        
        // 2. 어떤 요소 위인지 확인 (z-order 역순 — 앞에 있는 것 우선)
        const elements = [...this.state.currentSlide.elements].reverse();
        const hit = elements.find(el => this._isInsideBbox(pct, el.bbox));
        
        if (hit) {
            this.state.selectedElementId = hit.id;
            this.dragMode = 'move';
            this.dragOrigin = pct;
            this.originalBbox = { ...hit.bbox };
        } else {
            this.state.selectedElementId = null;
        }
        
        this.renderer.renderElements(
            this.state.currentSlide.elements,
            this.state.selectedElementId
        );
    }
    
    // 이동
    _handleMove(pct) {
        const dx = pct.x_pct - this.dragOrigin.x_pct;
        const dy = pct.y_pct - this.dragOrigin.y_pct;
        
        const elem = this.state.selectedElement;
        elem.bbox.x_pct = Math.max(0, Math.min(100 - elem.bbox.width_pct,
            this.originalBbox.x_pct + dx));
        elem.bbox.y_pct = Math.max(0, Math.min(100 - elem.bbox.height_pct,
            this.originalBbox.y_pct + dy));
        
        this.renderer.renderElements(
            this.state.currentSlide.elements,
            this.state.selectedElementId
        );
    }
    
    // 리사이즈 (핸들 방향에 따라 8가지 동작)
    _handleResize(pct) {
        const dx = pct.x_pct - this.dragOrigin.x_pct;
        const dy = pct.y_pct - this.dragOrigin.y_pct;
        const orig = this.originalBbox;
        const elem = this.state.selectedElement;
        const h = this.resizeHandle;
        
        // 최소 크기
        const MIN = 2; // 2%
        
        if (h.includes('w') || h === 'w') {
            elem.bbox.x_pct = Math.min(orig.x_pct + dx, orig.x_pct + orig.width_pct - MIN);
            elem.bbox.width_pct = orig.width_pct - (elem.bbox.x_pct - orig.x_pct);
        }
        if (h.includes('e') || h === 'e') {
            elem.bbox.width_pct = Math.max(MIN, orig.width_pct + dx);
        }
        if (h.includes('n')) {
            elem.bbox.y_pct = Math.min(orig.y_pct + dy, orig.y_pct + orig.height_pct - MIN);
            elem.bbox.height_pct = orig.height_pct - (elem.bbox.y_pct - orig.y_pct);
        }
        if (h.includes('s')) {
            elem.bbox.height_pct = Math.max(MIN, orig.height_pct + dy);
        }
        
        this.renderer.renderElements(
            this.state.currentSlide.elements,
            this.state.selectedElementId
        );
    }
    
    // 새 요소 그리기 시작
    _startDrawing(pct) {
        this.dragMode = 'draw';
        this.dragOrigin = pct;
        this.state.isDrawing = true;
    }
    
    // 그리기 완료 → 요소 생성
    async _finishDrawing(pct) {
        this.state.isDrawing = false;
        
        const bbox = this._normalizeBbox(this.dragOrigin, pct);
        if (bbox.width_pct < 1 || bbox.height_pct < 1) return; // 너무 작으면 무시
        
        let newElement;
        
        switch (this.state.currentTool) {
            case 'text':
                // 텍스트 도구: 영역 그린 후 OCR 시도
                newElement = await this._createTextElement(bbox);
                break;
            case 'shape':
                newElement = this._createShapeElement(bbox);
                break;
            case 'line':
                newElement = this._createLineElement(bbox);
                break;
        }
        
        if (newElement) {
            this.state.currentSlide.elements.push(newElement);
            this.state.selectedElementId = newElement.id;
            this.state.isDirty = true;
            this.renderer.renderElements(
                this.state.currentSlide.elements,
                this.state.selectedElementId
            );
        }
    }
    
    // 텍스트 요소 생성 (OCR 호출)
    async _createTextElement(bbox) {
        // 1. 사용자에게 선택 — OCR 자동 추출 vs 수동 입력
        const choice = await showDialog({
            title: '텍스트 영역',
            message: '이 영역의 텍스트를 어떻게 입력하시겠습니까?',
            buttons: ['자동 추출 (OCR)', '직접 입력']
        });
        
        let paragraphs = [];
        
        if (choice === 0) {
            // Claude Vision OCR 호출
            const result = await ApiClient.ocrRegion(
                this.state.jobId,
                this.state.currentSlideIndex + 1,
                bbox
            );
            paragraphs = result.paragraphs;
        }
        // choice === 1이면 빈 paragraphs → 속성 패널에서 직접 입력
        
        return {
            id: generateId(),
            element_type: 'text_box',
            bbox: bbox,
            source: 'manual',
            paragraphs: paragraphs.length > 0 ? paragraphs : [{
                runs: [{ text: '', font: { family: 'Calibri', size_pt: 18, bold: false, italic: false, underline: false, color_hex: '#000000' } }],
                alignment: 'left',
                bullet: false,
                indent_level: 0,
                line_spacing: 1.2
            }],
            z_order: Math.max(...this.state.currentSlide.elements.map(e => e.z_order), 0) + 1,
            name: `manual_text_${Date.now()}`
        };
    }
    
    // 도형 요소 생성
    _createShapeElement(bbox) {
        return {
            id: generateId(),
            element_type: 'shape',
            bbox: bbox,
            source: 'manual',
            shape_kind: this.state.shapeSubTool,
            fill_color_hex: '#4CAF50',
            fill_opacity: 0.8,
            border_color_hex: '#2E7D32',
            border_width_pt: 1.5,
            paragraphs: [],
            z_order: Math.max(...this.state.currentSlide.elements.map(e => e.z_order), 0) + 1,
            name: `manual_${this.state.shapeSubTool}_${Date.now()}`
        };
    }
}
```

### 5.5 속성 패널 (`property-panel.js`)

```javascript
class PropertyPanel {
    constructor(panelElement, appState) {
        this.panel = panelElement;
        this.state = appState;
    }
    
    // 선택된 요소가 변경될 때 호출
    update(element) {
        if (!element) {
            this.panel.innerHTML = '<p class="no-selection">요소를 선택하세요</p>';
            return;
        }
        
        this.panel.innerHTML = this._buildPanelHTML(element);
        this._bindEvents(element);
    }
    
    _buildPanelHTML(elem) {
        return `
        <div class="panel-section">
            <h3>요소 정보</h3>
            <div class="field">
                <label>타입</label>
                <select id="prop-type">${this._typeOptions(elem.element_type)}</select>
            </div>
            <div class="field">
                <label>이름</label>
                <input id="prop-name" value="${elem.name || ''}">
            </div>
            <div class="field badge">
                <span class="source-badge ${elem.source}">${elem.source === 'auto' ? '자동' : '수동'}</span>
            </div>
        </div>
        
        <div class="panel-section">
            <h3>위치 & 크기</h3>
            <div class="field-row">
                <label>X%</label><input type="number" id="prop-x" value="${elem.bbox.x_pct.toFixed(1)}" step="0.5">
                <label>Y%</label><input type="number" id="prop-y" value="${elem.bbox.y_pct.toFixed(1)}" step="0.5">
            </div>
            <div class="field-row">
                <label>W%</label><input type="number" id="prop-w" value="${elem.bbox.width_pct.toFixed(1)}" step="0.5">
                <label>H%</label><input type="number" id="prop-h" value="${elem.bbox.height_pct.toFixed(1)}" step="0.5">
            </div>
            <div class="field-row">
                <label>회전</label><input type="number" id="prop-rotation" value="${elem.rotation}" step="5" min="0" max="359">
                <label>Z순서</label><input type="number" id="prop-z" value="${elem.z_order}" min="0">
            </div>
        </div>
        
        ${elem.element_type === 'text_box' || elem.paragraphs?.length > 0 ? this._textSection(elem) : ''}
        ${elem.element_type === 'shape' ? this._shapeSection(elem) : ''}
        ${elem.element_type === 'line' || elem.element_type === 'connector' ? this._lineSection(elem) : ''}
        
        <div class="panel-section">
            <button id="btn-delete-element" class="btn-danger">요소 삭제</button>
        </div>
        `;
    }
    
    _textSection(elem) {
        // 각 paragraph의 텍스트를 textarea로, 폰트 설정을 입력 필드로 표시
        return `
        <div class="panel-section">
            <h3>텍스트</h3>
            ${elem.paragraphs.map((p, i) => `
                <div class="para-editor" data-para-idx="${i}">
                    <textarea class="para-text" rows="2">${p.runs.map(r => r.text).join('')}</textarea>
                    <div class="font-controls">
                        <input class="font-family" value="${p.runs[0]?.font?.family || 'Calibri'}" placeholder="폰트">
                        <input type="number" class="font-size" value="${p.runs[0]?.font?.size_pt || 18}" step="1" min="6" max="120">pt
                        <button class="toggle-bold ${p.runs[0]?.font?.bold ? 'active' : ''}">B</button>
                        <button class="toggle-italic ${p.runs[0]?.font?.italic ? 'active' : ''}">I</button>
                        <input type="color" class="font-color" value="${p.runs[0]?.font?.color_hex || '#000000'}">
                        <select class="para-align">
                            <option value="left" ${p.alignment === 'left' ? 'selected' : ''}>좌</option>
                            <option value="center" ${p.alignment === 'center' ? 'selected' : ''}>중</option>
                            <option value="right" ${p.alignment === 'right' ? 'selected' : ''}>우</option>
                        </select>
                    </div>
                </div>
            `).join('')}
            <button id="btn-add-paragraph">+ 단락 추가</button>
        </div>`;
    }
    
    _shapeSection(elem) {
        return `
        <div class="panel-section">
            <h3>도형</h3>
            <div class="field">
                <label>종류</label>
                <select id="prop-shape-kind">${this._shapeKindOptions(elem.shape_kind)}</select>
            </div>
            <div class="field-row">
                <label>채우기</label><input type="color" id="prop-fill" value="${elem.fill_color_hex || '#FFFFFF'}">
                <label>투명도</label><input type="range" id="prop-fill-opacity" min="0" max="1" step="0.1" value="${elem.fill_opacity ?? 1}">
            </div>
            <div class="field-row">
                <label>테두리</label><input type="color" id="prop-border" value="${elem.border_color_hex || '#000000'}">
                <label>두께</label><input type="number" id="prop-border-width" value="${elem.border_width_pt || 1}" step="0.5" min="0">pt
            </div>
        </div>`;
    }
    
    _lineSection(elem) {
        return `
        <div class="panel-section">
            <h3>선</h3>
            <div class="field-row">
                <label>색상</label><input type="color" id="prop-line-color" value="${elem.line_color_hex || '#000000'}">
                <label>두께</label><input type="number" id="prop-line-width" value="${elem.line_width_pt || 1}" step="0.5">pt
            </div>
            <div class="field-row">
                <label>스타일</label>
                <select id="prop-line-style">
                    <option value="solid" ${elem.line_style === 'solid' ? 'selected' : ''}>실선</option>
                    <option value="dashed" ${elem.line_style === 'dashed' ? 'selected' : ''}>점선</option>
                    <option value="dotted" ${elem.line_style === 'dotted' ? 'selected' : ''}>점</option>
                </select>
            </div>
            <div class="field-row">
                <label>시작 화살표</label><select id="prop-start-arrow">${this._arrowOptions(elem.start_arrow)}</select>
                <label>끝 화살표</label><select id="prop-end-arrow">${this._arrowOptions(elem.end_arrow)}</select>
            </div>
        </div>`;
    }
}
```

### 5.6 슬라이드 내비게이션 (`slide-nav.js`)

```javascript
class SlideNavigator {
    constructor(navElement, appState) {
        this.nav = navElement;
        this.state = appState;
    }
    
    render() {
        // 좌측 썸네일 목록
        this.nav.innerHTML = this.state.presentation.slides.map((slide, i) => `
            <div class="thumb ${i === this.state.currentSlideIndex ? 'active' : ''}"
                 data-index="${i}" title="슬라이드 ${i + 1}">
                <span class="thumb-num">${i + 1}</span>
                <canvas class="thumb-canvas" width="120" height="68"></canvas>
                <span class="thumb-info">${slide.elements.length}개 요소</span>
            </div>
        `).join('');
        
        // 각 썸네일에 축소된 슬라이드 이미지 렌더링
        this._renderThumbnails();
    }
    
    // 슬라이드 전환
    goToSlide(index) {
        if (this.state.isDirty) {
            this._autoSave();  // 전환 전 자동 저장
        }
        this.state.currentSlideIndex = index;
        this.state.selectedElementId = null;
        // 이벤트 발행 → canvas-renderer, property-panel 갱신
        dispatchEvent(new CustomEvent('slideChanged', { detail: { index } }));
    }
}
```

### 5.7 키보드 단축키

```javascript
// editor-app.js에서 등록
document.addEventListener('keydown', (e) => {
    // Delete/Backspace → 선택된 요소 삭제
    if ((e.key === 'Delete' || e.key === 'Backspace') && AppState.selectedElementId) {
        if (!e.target.matches('input, textarea, select')) {
            deleteSelectedElement();
        }
    }
    
    // Escape → 선택 해제 / 그리기 취소
    if (e.key === 'Escape') {
        AppState.selectedElementId = null;
        AppState.currentTool = 'select';
        refresh();
    }
    
    // Ctrl+S → 저장
    if (e.ctrlKey && e.key === 's') {
        e.preventDefault();
        saveToServer();
    }
    
    // Ctrl+Z → 실행취소 (undo stack)
    if (e.ctrlKey && e.key === 'z') {
        e.preventDefault();
        undo();
    }
    
    // Ctrl+Shift+Z → 다시실행 (redo stack)
    if (e.ctrlKey && e.shiftKey && e.key === 'z') {
        e.preventDefault();
        redo();
    }
    
    // 좌/우 화살표 → 슬라이드 전환
    if (e.key === 'ArrowLeft' && !e.target.matches('input, textarea')) {
        slideNav.goToSlide(Math.max(0, AppState.currentSlideIndex - 1));
    }
    if (e.key === 'ArrowRight' && !e.target.matches('input, textarea')) {
        slideNav.goToSlide(Math.min(AppState.presentation.slides.length - 1, AppState.currentSlideIndex + 1));
    }
    
    // 1-5 숫자키 → 도구 전환
    if (e.key === '1') setTool('select');
    if (e.key === '2') setTool('text');
    if (e.key === '3') setTool('shape');
    if (e.key === '4') setTool('line');
    if (e.key === '5') setTool('delete');
});
```

### 5.8 Undo/Redo 시스템

```javascript
class UndoManager {
    constructor(maxHistory = 50) {
        this.undoStack = [];   // 이전 상태들
        this.redoStack = [];
        this.maxHistory = maxHistory;
    }
    
    // 변경 전에 호출 → 현재 상태 스냅샷 저장
    pushState(slideData) {
        this.undoStack.push(JSON.parse(JSON.stringify(slideData)));
        if (this.undoStack.length > this.maxHistory) {
            this.undoStack.shift();
        }
        this.redoStack = [];  // 새 변경이 생기면 redo 초기화
    }
    
    undo(currentSlideData) {
        if (this.undoStack.length === 0) return null;
        this.redoStack.push(JSON.parse(JSON.stringify(currentSlideData)));
        return this.undoStack.pop();
    }
    
    redo(currentSlideData) {
        if (this.redoStack.length === 0) return null;
        this.undoStack.push(JSON.parse(JSON.stringify(currentSlideData)));
        return this.redoStack.pop();
    }
}
```

---

## 6. 폰트 유틸리티 (`app/font_utils.py`)

```python
FONT_FALLBACK = {
    # Google 폰트 → Windows 기본 폰트
    "Google Sans": "Arial",
    "Google Sans Text": "Arial",
    "Product Sans": "Arial",
    "Roboto": "Arial",
    "Roboto Condensed": "Arial Narrow",
    "Roboto Slab": "Georgia",
    "Open Sans": "Calibri",
    "Lato": "Calibri",
    "Montserrat": "Arial",
    "Noto Sans": "Arial",
    "Noto Sans KR": "맑은 고딕",       # 한국어
    "Noto Sans JP": "Yu Gothic",       # 일본어
    "Source Sans Pro": "Calibri",
    "Poppins": "Arial",
    "Inter": "Arial",
    
    # macOS 폰트 → Windows 폰트
    "Helvetica Neue": "Arial",
    "Helvetica": "Arial",
    "SF Pro": "Segoe UI",
}

def resolve_font(detected_name: str) -> str:
    """감지된 폰트 이름을 시스템에서 사용 가능한 폰트로 매핑"""
    if not detected_name:
        return "Calibri"
    return FONT_FALLBACK.get(detected_name, detected_name)
```

---

## 7. 에러 처리 상세

| 단계 | 실패 시나리오 | 처리 |
|------|-------------|------|
| PDF 업로드 | 파일 아님, 깨진 PDF, 암호화 PDF | 400 + 구체적 에러 메시지 |
| PDF 업로드 | 50MB 초과 | 413 "파일이 너무 큽니다 (최대 50MB)" |
| PDF 업로드 | 50페이지 초과 | 400 "슬라이드가 너무 많습니다 (최대 50장)" |
| PDF→이미지 | 특정 페이지 렌더링 실패 | 해당 페이지 건너뛰기, 빈 이미지 placeholder |
| Claude API | 429 Rate Limit | 지수 백오프 재시도 (1s, 2s, 4s) 최대 3회 |
| Claude API | 500/503 서버 오류 | 동일 재시도 |
| Claude API | 인증 실패 (401) | 즉시 중단, "API 키를 확인하세요" |
| Claude 응답 | JSON 파싱 실패 | 2회 재시도, 최종 실패 → 빈 슬라이드 + 경고 |
| Claude 응답 | Pydantic 검증 실패 | 유효한 요소만 남기고 나머지 무시 (partial recovery) |
| 수동 OCR | 영역이 너무 작음 (< 1%) | 프론트엔드에서 차단 |
| 수동 OCR | Claude가 텍스트 못 읽음 | confidence: "low" 반환, UI에서 수동 입력 유도 |
| PPTX 빌더 | 개별 요소 생성 실패 | 해당 요소 건너뛰기 + 로그 + 경고 메시지 |
| PPTX 빌더 | 전체 실패 | 500 + 상세 에러 로그 |
| 편집기 저장 | Pydantic 검증 실패 | 400 + 어떤 필드가 잘못되었는지 상세 반환 |

---

## 8. 구현 순서 (7 Phase, 25 Step)

### Phase 1 — 프로젝트 스켈레톤 (Step 1-7)
1. `git init`, `.gitignore` 생성 (output/, .env, __pycache__, *.pyc)
2. `requirements.txt` 생성 + `pip install -r requirements.txt`
3. `.env.example` 생성
4. `app/__init__.py`, `app/config.py` — Settings 클래스
5. `app/models.py` — 전체 Pydantic 모델 (위 §1 전체)
6. `app/main.py` — FastAPI 앱, 라우터 등록, static 파일 마운트, CORS
7. `app/routers/` — 4개 라우터 파일 스텁 (빈 엔드포인트)
8. `run.py` — uvicorn 실행
9. `app/static/index.html` + `css/index.css` + `js/upload.js` — 업로드 UI
10. **검증**: `python run.py` → `localhost:8000` 접속 → 업로드 폼 표시 확인

### Phase 2 — PDF → 이미지 (Step 11)
11. `app/pdf_to_images.py` — PyMuPDF로 PDF → PNG 리스트, 200 DPI
12. **검증**: 테스트 PDF로 PNG 생성 확인

### Phase 3 — Claude Vision 연동 (Step 13-14)
13. `app/vision_analyzer.py` — 전체 프롬프트 + API 호출 + JSON 파싱 + 재시도
14. **검증**: 단일 슬라이드 이미지 → JSON 응답 품질 확인, 프롬프트 조정

### Phase 4 — PPTX 빌더 (Step 15-17)
15. `app/color_utils.py` — hex ↔ RGBColor, 색상 검증
16. `app/font_utils.py` — 폰트 매핑 딕셔너리
17. `app/pptx_builder.py` — 전체 빌드 함수 (§4 전체)
18. **검증**: fixture JSON → PPTX → PowerPoint에서 열기

### Phase 5 — 파이프라인 & 자동 추출 (Step 19-21)
19. `app/job_manager.py` — Job 상태 관리 (in-memory dict)
20. `app/pipeline.py` — extract_all_slides() 오케스트레이터 + 진행률 콜백
21. `app/routers/upload.py` — POST /api/convert (BackgroundTask), GET /api/status
22. `app/routers/generate.py` — POST /api/generate, GET /api/download
23. **검증**: PDF 업로드 → 폴링으로 진행률 확인 → PPTX 다운로드 (편집기 없이)

### Phase 6 — 인터랙티브 편집기 (Step 24-30)
24. `app/routers/editor_api.py` — GET/PUT /api/slides, GET /api/slide-image, PUT/DELETE 요소
25. `app/region_ocr.py` — 이미지 크롭 + Claude OCR 호출
26. `app/routers/ocr.py` — POST /api/ocr-region, POST /api/analyze-region
27. `app/static/editor.html` — 전체 레이아웃 (캔버스 + 패널 + 도구 모음 + 썸네일)
28. `app/static/css/editor.css` — 편집기 스타일
29. `app/static/js/api-client.js` — 모든 API fetch 래퍼
30. `app/static/js/canvas-renderer.js` — 이미지 로드 + 요소 오버레이 + 핸들 렌더링
31. `app/static/js/element-tools.js` — 선택/이동/리사이즈/그리기 도구
32. `app/static/js/property-panel.js` — 속성 패널 양방향 바인딩
33. `app/static/js/slide-nav.js` — 슬라이드 목록/전환
34. `app/static/js/editor-app.js` — 상태 관리, 이벤트 연결, undo/redo, 키보드 단축키
35. `js/upload.js` 수정 — 자동 추출 완료 시 editor.html로 리다이렉트
36. **검증**: PDF 업로드 → 자동 추출 → 편집기 진입 → 수동 편집 → PPT 생성

### Phase 7 — 마무리 (Step 37-39)
37. 전체 에러 핸들링 검토 (§7 테이블 기준)
38. 자동 저장 로직 (편집기에서 30초마다 또는 슬라이드 전환 시)
39. CLAUDE.md 업데이트 (빌드/실행 명령어, 아키텍처 요약)

---

## 9. 검증 체크리스트

### 자동 추출 검증
- [ ] PDF 업로드 → 진행률 "슬라이드 N/M 분석 중" 정상 표시
- [ ] 추출 완료 시 편집기 자동 전환
- [ ] 각 슬라이드의 자동 검출 요소가 이미지 위에 올바르게 오버레이됨
- [ ] 텍스트 내용이 원본과 일치 (오탈자 없음)
- [ ] 바운딩 박스 위치가 실제 요소 위치와 ±5% 이내

### 수동 편집 검증
- [ ] 요소 클릭 → 선택 (주황색 테두리 + 8개 핸들)
- [ ] 요소 드래그 → 이동 (슬라이드 영역 밖으로 나가지 않음)
- [ ] 핸들 드래그 → 리사이즈 (최소 크기 2% 제한)
- [ ] 텍스트 도구 → 영역 그리기 → OCR 팝업 → 텍스트 추출
- [ ] 텍스트 도구 → 영역 그리기 → "직접 입력" → 빈 텍스트박스 생성
- [ ] 도형 도구 → 사각형/원 등 그리기
- [ ] 선 도구 → 선 그리기
- [ ] 삭제 도구 or Delete 키 → 요소 삭제
- [ ] 속성 패널에서 텍스트/폰트/색상 수정 → Canvas에 즉시 반영
- [ ] 속성 패널에서 위치/크기 숫자 직접 수정 → Canvas 반영
- [ ] 슬라이드 전환 (썸네일 클릭 / 좌우 화살표)
- [ ] Ctrl+Z / Ctrl+Shift+Z → Undo/Redo 동작
- [ ] Ctrl+S → 서버 저장 → "저장됨" 상태 표시

### PPTX 생성 검증
- [ ] "PPT 생성" 버튼 → 다운로드
- [ ] PowerPoint/Google Slides에서 열기 성공
- [ ] 각 요소가 개별 클릭 가능 (그룹화되지 않음)
- [ ] 수동 추가한 요소가 정확한 위치에 존재
- [ ] 텍스트 편집 가능
- [ ] 도형 색상/테두리 올바름
- [ ] 배경색 올바름

---

## 10. 백엔드 모듈 세부 구현

### 10.1 `run.py` — 진입점

```python
import uvicorn
from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info",
    )
```

### 10.2 `app/config.py` — 전체 설정 필드

```python
from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # API
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    claude_max_tokens: int = 8192
    
    # 서버
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    
    # PDF 제한
    max_pdf_size_mb: int = 50
    max_slides: int = 50
    render_dpi: int = 200
    
    # Job 관리
    output_dir: Path = Path("./output")
    job_ttl_hours: int = 24          # 오래된 job 자동 삭제
    auto_save_interval_sec: int = 30
    
    # OCR
    min_region_pct: float = 1.0      # 수동 OCR 최소 영역 (가로/세로 모두)
    ocr_timeout_sec: int = 15
    
    # PPTX
    default_slide_width: float = 13.333
    default_slide_height: float = 7.5
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()
```

### 10.3 `app/main.py` — FastAPI 앱 팩토리 상세

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging, asyncio

from app.config import settings
from app.job_manager import cleanup_expired_jobs
from app.routers import upload, editor_api, ocr, generate

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작: output 디렉토리 생성
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    
    # 백그라운드: 만료 job 정리 태스크 (1시간 간격)
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    
    yield
    
    # 종료: 정리 태스크 취소
    cleanup_task.cancel()

async def _periodic_cleanup():
    while True:
        await asyncio.sleep(3600)
        cleanup_expired_jobs(settings.job_ttl_hours)

app = FastAPI(
    title="PDF to PPT Converter",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS (개발 중 프론트엔드 핫리로드 시 필요)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(upload.router, prefix="/api")
app.include_router(editor_api.router, prefix="/api")
app.include_router(ocr.router, prefix="/api")
app.include_router(generate.router, prefix="/api")

# 정적 파일
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# 루트 → index.html
@app.get("/")
async def root():
    from fastapi.responses import FileResponse
    return FileResponse("app/static/index.html")

# 편집기 페이지
@app.get("/editor")
async def editor_page():
    from fastapi.responses import FileResponse
    return FileResponse("app/static/editor.html")

# 전역 예외 핸들러
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "내부 서버 오류가 발생했습니다.", "detail": str(exc)}
    )
```

### 10.4 `app/job_manager.py` — Job 생명주기 관리

```python
import uuid, shutil, json, logging
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

from app.models import Job, JobStatus, PresentationData
from app.config import settings

logger = logging.getLogger(__name__)

# Thread-safe in-memory store
_jobs: dict[str, Job] = {}
_lock = Lock()

def create_job() -> Job:
    job_id = uuid.uuid4().hex[:12]
    output_dir = settings.output_dir / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "images").mkdir(exist_ok=True)
    
    job = Job(
        job_id=job_id,
        output_dir=str(output_dir),
        created_at=datetime.utcnow(),
    )
    with _lock:
        _jobs[job_id] = job
    return job

def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)

def update_job(job_id: str, **kwargs):
    """원자적 업데이트. kwargs에 변경할 필드만 전달."""
    with _lock:
        job = _jobs.get(job_id)
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)

def save_slides_json(job_id: str, data: PresentationData):
    """편집기 상태를 JSON 파일로 저장"""
    job = get_job(job_id)
    if not job:
        return
    path = Path(job.output_dir) / "slides.json"
    path.write_text(data.model_dump_json(indent=2), encoding="utf-8")

def load_slides_json(job_id: str) -> PresentationData | None:
    """저장된 JSON에서 PresentationData 로드"""
    job = get_job(job_id)
    if not job:
        return None
    path = Path(job.output_dir) / "slides.json"
    if not path.exists():
        return None
    return PresentationData.model_validate_json(path.read_text(encoding="utf-8"))

def save_slide_image(job_id: str, slide_num: int, png_bytes: bytes):
    """슬라이드 이미지 파일 저장"""
    job = get_job(job_id)
    if not job:
        return
    path = Path(job.output_dir) / "images" / f"slide_{slide_num}.png"
    path.write_bytes(png_bytes)

def get_slide_image_path(job_id: str, slide_num: int) -> Path | None:
    job = get_job(job_id)
    if not job:
        return None
    path = Path(job.output_dir) / "images" / f"slide_{slide_num}.png"
    return path if path.exists() else None

def cleanup_expired_jobs(ttl_hours: int):
    """TTL 초과 job 삭제 (파일 + 메모리)"""
    cutoff = datetime.utcnow() - timedelta(hours=ttl_hours)
    to_delete = []
    with _lock:
        for jid, job in _jobs.items():
            if job.created_at < cutoff:
                to_delete.append(jid)
    
    for jid in to_delete:
        job = _jobs.get(jid)
        if job:
            try:
                shutil.rmtree(job.output_dir, ignore_errors=True)
            except Exception:
                pass
            with _lock:
                _jobs.pop(jid, None)
    
    if to_delete:
        logger.info(f"Cleaned up {len(to_delete)} expired jobs")
```

### 10.5 `app/pdf_to_images.py` — 세부 구현

```python
import fitz  # PyMuPDF
import logging
from app.config import settings

logger = logging.getLogger(__name__)

class PDFProcessingError(Exception):
    """PDF 처리 중 발생하는 에러"""
    pass

def validate_pdf(pdf_bytes: bytes) -> int:
    """PDF 유효성 검증. 페이지 수 반환. 실패 시 PDFProcessingError."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise PDFProcessingError(f"PDF 파일을 열 수 없습니다: {e}")
    
    if doc.is_encrypted:
        doc.close()
        raise PDFProcessingError("암호화된 PDF는 지원하지 않습니다.")
    
    page_count = len(doc)
    doc.close()
    
    if page_count == 0:
        raise PDFProcessingError("PDF에 페이지가 없습니다.")
    if page_count > settings.max_slides:
        raise PDFProcessingError(
            f"슬라이드가 너무 많습니다 ({page_count}장). 최대 {settings.max_slides}장까지 지원합니다."
        )
    
    return page_count

def pdf_to_images(pdf_bytes: bytes, dpi: int | None = None) -> list[bytes]:
    """PDF의 각 페이지를 PNG 이미지로 변환.
    
    Returns:
        각 페이지의 PNG bytes 리스트 (1-indexed 슬라이드와 대응)
    """
    dpi = dpi or settings.render_dpi
    zoom = dpi / 72  # fitz 기본 해상도는 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    
    for page_idx in range(len(doc)):
        try:
            page = doc[page_idx]
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png_bytes = pix.tobytes("png")
            images.append(png_bytes)
            logger.debug(f"Page {page_idx + 1}: {pix.width}x{pix.height}px, {len(png_bytes)} bytes")
        except Exception as e:
            logger.warning(f"Page {page_idx + 1} 렌더링 실패: {e}")
            # 실패한 페이지는 1x1 투명 PNG placeholder
            images.append(_create_placeholder_png())
    
    doc.close()
    return images

def _create_placeholder_png() -> bytes:
    """렌더링 실패 시 사용할 빈 이미지"""
    # 최소한의 흰색 PNG (작은 크기)
    import struct, zlib
    # 1x1 흰색 PNG
    def create_chunk(chunk_type, data):
        chunk = chunk_type + data
        crc = struct.pack('>I', zlib.crc32(chunk) & 0xffffffff)
        return struct.pack('>I', len(data)) + chunk + crc
    
    signature = b'\x89PNG\r\n\x1a\n'
    ihdr = create_chunk(b'IHDR', struct.pack('>IIBBBBB', 100, 56, 8, 2, 0, 0, 0))
    raw_data = b'\x00' + b'\xff\xff\xff' * 100
    raw_all = b''
    for _ in range(56):
        raw_all += raw_data
    idat = create_chunk(b'IDAT', zlib.compress(raw_all))
    iend = create_chunk(b'IEND', b'')
    return signature + ihdr + idat + iend
```

### 10.6 `app/region_ocr.py` — 이미지 크롭 + OCR 구현

```python
import base64, json, logging
from PIL import Image
import io
import anthropic

from app.models import BoundingBox, TextParagraph
from app.config import settings

logger = logging.getLogger(__name__)

REGION_OCR_SYSTEM_PROMPT = """..."""  # §3.2에 정의된 전문

REGION_ANALYSIS_SYSTEM_PROMPT = """..."""  # §3.3에 정의된 전문

def crop_image(image_bytes: bytes, region: BoundingBox) -> bytes:
    """슬라이드 이미지에서 지정 영역을 크롭.
    
    region은 % 좌표. 이미지 픽셀로 변환 후 Pillow로 크롭.
    """
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    
    left = int((region.x_pct / 100) * w)
    top = int((region.y_pct / 100) * h)
    right = int(((region.x_pct + region.width_pct) / 100) * w)
    bottom = int(((region.y_pct + region.height_pct) / 100) * h)
    
    # 경계 클램핑
    left = max(0, min(left, w - 1))
    top = max(0, min(top, h - 1))
    right = max(left + 1, min(right, w))
    bottom = max(top + 1, min(bottom, h))
    
    cropped = img.crop((left, top, right, bottom))
    
    # PNG로 인코딩
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()

async def ocr_region(image_bytes: bytes, region: BoundingBox) -> dict:
    """지정 영역의 텍스트를 OCR로 추출.
    
    Returns:
        {"paragraphs": [...], "confidence": "high|medium|low"}
    """
    cropped = crop_image(image_bytes, region)
    
    # 크롭된 이미지가 너무 작으면 low confidence
    img = Image.open(io.BytesIO(cropped))
    if img.width < 20 or img.height < 10:
        return {"paragraphs": [], "confidence": "low"}
    
    client = anthropic.AsyncAnthropic()
    image_b64 = base64.standard_b64encode(cropped).decode()
    
    try:
        message = await client.messages.create(
            model=settings.claude_model,
            max_tokens=2048,
            system=REGION_OCR_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_b64
                    }},
                    {"type": "text", "text": "Read all text in this cropped image. Preserve formatting."}
                ]
            }]
        )
        
        raw = _strip_code_fences(message.content[0].text)
        parsed = json.loads(raw)
        return parsed
        
    except Exception as e:
        logger.warning(f"OCR failed: {e}")
        return {"paragraphs": [], "confidence": "low"}

async def analyze_region(image_bytes: bytes, region: BoundingBox) -> dict:
    """지정 영역의 요소를 전체 분석 (텍스트 + 도형 + 색상 등).
    
    Returns: SlideElement 속성 딕셔너리 (bbox 제외)
    """
    cropped = crop_image(image_bytes, region)
    client = anthropic.AsyncAnthropic()
    image_b64 = base64.standard_b64encode(cropped).decode()
    
    try:
        message = await client.messages.create(
            model=settings.claude_model,
            max_tokens=2048,
            system=REGION_ANALYSIS_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": image_b64
                    }},
                    {"type": "text", "text": "Analyze this element. What type is it and what are its properties?"}
                ]
            }]
        )
        
        raw = _strip_code_fences(message.content[0].text)
        return json.loads(raw)
        
    except Exception as e:
        logger.warning(f"Region analysis failed: {e}")
        return {"element_type": "text_box", "paragraphs": []}

def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()
```

### 10.7 `app/pipeline.py` — 오케스트레이터 상세

```python
import asyncio, logging, json
from pathlib import Path

from app.models import PresentationData, SlideData, JobStatus
from app.config import settings
from app.job_manager import (
    get_job, update_job, save_slides_json, save_slide_image
)
from app.pdf_to_images import pdf_to_images, validate_pdf
from app.vision_analyzer import analyze_slide

logger = logging.getLogger(__name__)

async def extract_all_slides(job_id: str, pdf_bytes: bytes):
    """PDF → 이미지 → Claude Vision 분석 전체 파이프라인.
    
    BackgroundTask에서 호출됨.
    상태 전이: UPLOADING → EXTRACTING → EDITING (or FAILED)
    """
    try:
        # Phase 1: PDF 검증 & 이미지 변환
        update_job(job_id, status=JobStatus.EXTRACTING, message="PDF를 이미지로 변환 중...")
        
        page_count = validate_pdf(pdf_bytes)
        update_job(job_id, total_slides=page_count)
        
        images = pdf_to_images(pdf_bytes)
        
        # 이미지 저장
        for i, img_bytes in enumerate(images, start=1):
            save_slide_image(job_id, i, img_bytes)
        
        # Phase 2: Claude Vision으로 각 슬라이드 분석
        slides: list[SlideData] = []
        
        for i, img_bytes in enumerate(images, start=1):
            update_job(
                job_id,
                current_slide=i,
                message=f"슬라이드 {i}/{page_count} 분석 중..."
            )
            
            try:
                slide_data = await analyze_slide(img_bytes, slide_number=i)
                slides.append(slide_data)
            except Exception as e:
                logger.error(f"Slide {i} analysis failed: {e}")
                # 실패한 슬라이드는 빈 슬라이드로 대체
                slides.append(SlideData(slide_number=i, elements=[]))
            
            # API rate limit 방지: 슬라이드 간 0.5초 대기
            if i < page_count:
                await asyncio.sleep(0.5)
        
        # Phase 3: 결과 저장
        presentation = PresentationData(
            slide_width_inches=settings.default_slide_width,
            slide_height_inches=settings.default_slide_height,
            slides=slides,
        )
        save_slides_json(job_id, presentation)
        
        # 상태 전이: EDITING
        total_elements = sum(len(s.elements) for s in slides)
        update_job(
            job_id,
            status=JobStatus.EDITING,
            current_slide=page_count,
            message=f"분석 완료! {page_count}장 슬라이드, {total_elements}개 요소 검출"
        )
        
    except Exception as e:
        logger.exception(f"Pipeline failed for job {job_id}: {e}")
        update_job(
            job_id,
            status=JobStatus.FAILED,
            error=str(e),
            message=f"오류 발생: {e}"
        )
```

### 10.8 라우터 세부 (`app/routers/upload.py` 예시)

```python
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from app.config import settings
from app.job_manager import create_job, get_job
from app.pdf_to_images import validate_pdf, PDFProcessingError
from app.pipeline import extract_all_slides

router = APIRouter()

@router.post("/convert")
async def convert_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    # 1. 파일명 검증
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 업로드 가능합니다.")
    
    # 2. 크기 검증
    contents = await file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > settings.max_pdf_size_mb:
        raise HTTPException(413, f"파일이 너무 큽니다 ({size_mb:.1f}MB). 최대 {settings.max_pdf_size_mb}MB")
    
    # 3. PDF 유효성 검증
    try:
        page_count = validate_pdf(contents)
    except PDFProcessingError as e:
        raise HTTPException(400, str(e))
    
    # 4. Job 생성
    job = create_job()
    
    # 5. 백그라운드 처리 시작
    background_tasks.add_task(extract_all_slides, job.job_id, contents)
    
    return {"job_id": job.job_id, "total_slides": page_count}

@router.get("/status/{job_id}")
async def get_status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job을 찾을 수 없습니다.")
    
    return {
        "status": job.status.value,
        "total_slides": job.total_slides,
        "current_slide": job.current_slide,
        "message": job.message,
        "error": job.error,
    }
```

### 10.9 `app/color_utils.py`

```python
from pptx.dml.color import RGBColor
import re

_HEX_PATTERN = re.compile(r'^#?([0-9a-fA-F]{6})$')

def hex_to_rgb(hex_color: str) -> RGBColor:
    """'#RRGGBB' 또는 'RRGGBB' → RGBColor. 잘못된 값이면 검정 반환."""
    match = _HEX_PATTERN.match(hex_color.strip())
    if not match:
        return RGBColor(0, 0, 0)
    hex_str = match.group(1)
    return RGBColor.from_string(hex_str)

def rgb_to_hex(rgb: RGBColor) -> str:
    """RGBColor → '#RRGGBB'"""
    return f"#{rgb.red:02X}{rgb.green:02X}{rgb.blue:02X}"

def validate_hex_color(value: str) -> bool:
    return bool(_HEX_PATTERN.match(value.strip()))
```

### 10.10 PPTX 빌더 — XML 조작 헬퍼 (oxml)

```python
from lxml import etree
from pptx.oxml.ns import qn, nsmap

def _set_fill_opacity(shape, opacity: float):
    """도형 채우기 투명도 설정 (0.0=투명, 1.0=불투명).
    python-pptx에 직접 API 없음 → XML 조작.
    """
    # alpha 값: 0=투명 ~ 100000=불투명
    alpha = int(opacity * 100000)
    solid_fill = shape.fill._fill  # oxml element
    srgb = solid_fill.find(qn('a:solidFill')).find(qn('a:srgbClr'))
    if srgb is not None:
        alpha_elem = srgb.find(qn('a:alpha'))
        if alpha_elem is None:
            alpha_elem = etree.SubElement(srgb, qn('a:alpha'))
        alpha_elem.set('val', str(alpha))

def _set_bullet(paragraph, bullet_char: str | None):
    """단락에 불릿 설정. python-pptx 직접 지원 안 함 → XML 조작."""
    pPr = paragraph._p.get_or_add_pPr()
    
    if bullet_char and len(bullet_char) == 1:
        # 문자 불릿
        buChar = etree.SubElement(pPr, qn('a:buChar'))
        buChar.set('char', bullet_char)
    else:
        # 기본 불릿
        buFont = etree.SubElement(pPr, qn('a:buFont'))
        buFont.set('typeface', 'Arial')
        buChar = etree.SubElement(pPr, qn('a:buChar'))
        buChar.set('char', '•')

def _set_line_dash(connector, line_style):
    """선 스타일 (점선/대시) 설정."""
    from app.models import LineStyle
    dash_map = {
        LineStyle.DASHED: 'dash',
        LineStyle.DOTTED: 'dot',
    }
    ln = connector.line._ln
    prstDash = etree.SubElement(ln, qn('a:prstDash'))
    prstDash.set('val', dash_map.get(line_style, 'solid'))

def _set_arrow_end(connector, arrow_type):
    """선 끝 화살표 설정."""
    from app.models import ArrowType
    arrow_map = {
        ArrowType.TRIANGLE: ('triangle', 'med', 'med'),
        ArrowType.STEALTH: ('stealth', 'med', 'med'),
        ArrowType.OPEN: ('arrow', 'med', 'med'),
    }
    if arrow_type not in arrow_map:
        return
    atype, w, l = arrow_map[arrow_type]
    ln = connector.line._ln
    tailEnd = etree.SubElement(ln, qn('a:tailEnd'))
    tailEnd.set('type', atype)
    tailEnd.set('w', w)
    tailEnd.set('len', l)

def _set_arrow_start(connector, arrow_type):
    """선 시작 화살표 설정."""
    from app.models import ArrowType
    arrow_map = {
        ArrowType.TRIANGLE: ('triangle', 'med', 'med'),
        ArrowType.STEALTH: ('stealth', 'med', 'med'),
        ArrowType.OPEN: ('arrow', 'med', 'med'),
    }
    if arrow_type not in arrow_map:
        return
    atype, w, l = arrow_map[arrow_type]
    ln = connector.line._ln
    headEnd = etree.SubElement(ln, qn('a:headEnd'))
    headEnd.set('type', atype)
    headEnd.set('w', w)
    headEnd.set('len', l)
```

---

## 11. 프론트엔드 세부 구현

### 11.1 업로드 페이지 UI 상세 (`index.html` + `upload.js`)

**드래그 앤 드롭 영역**:
```html
<div id="upload-area" class="upload-zone">
    <div class="upload-icon">📄</div>
    <h2>PDF 파일을 여기에 드래그하거나</h2>
    <button id="btn-browse" class="btn-primary">파일 선택</button>
    <input type="file" id="file-input" accept=".pdf" hidden>
    <p class="upload-hint">최대 50MB, 50페이지 이하의 PDF</p>
</div>

<div id="progress-area" class="hidden">
    <h2 id="progress-title">분석 중...</h2>
    <div class="progress-bar">
        <div id="progress-fill" class="progress-fill" style="width: 0%"></div>
    </div>
    <p id="progress-message">준비 중...</p>
    <p id="progress-detail" class="text-muted"></p>
</div>
```

**upload.js 상세**:
```javascript
class UploadManager {
    constructor() {
        this.uploadArea = document.getElementById('upload-area');
        this.progressArea = document.getElementById('progress-area');
        this.fileInput = document.getElementById('file-input');
        this.jobId = null;
        this.pollInterval = null;
        
        this._bindEvents();
    }
    
    _bindEvents() {
        // 드래그 앤 드롭
        this.uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            this.uploadArea.classList.add('drag-over');
        });
        this.uploadArea.addEventListener('dragleave', () => {
            this.uploadArea.classList.remove('drag-over');
        });
        this.uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            this.uploadArea.classList.remove('drag-over');
            const file = e.dataTransfer.files[0];
            if (file) this._handleFile(file);
        });
        
        // 파일 선택 버튼
        document.getElementById('btn-browse').addEventListener('click', () => {
            this.fileInput.click();
        });
        this.fileInput.addEventListener('change', (e) => {
            if (e.target.files[0]) this._handleFile(e.target.files[0]);
        });
    }
    
    async _handleFile(file) {
        // 클라이언트 사이드 검증
        if (!file.name.toLowerCase().endsWith('.pdf')) {
            this._showError('PDF 파일만 업로드 가능합니다.');
            return;
        }
        if (file.size > 50 * 1024 * 1024) {
            this._showError('파일이 너무 큽니다 (최대 50MB).');
            return;
        }
        
        // UI 전환
        this.uploadArea.classList.add('hidden');
        this.progressArea.classList.remove('hidden');
        
        // 업로드
        try {
            const formData = new FormData();
            formData.append('file', file);
            
            const res = await fetch('/api/convert', { method: 'POST', body: formData });
            
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || err.error || '업로드 실패');
            }
            
            const { job_id, total_slides } = await res.json();
            this.jobId = job_id;
            
            document.getElementById('progress-title').textContent = 
                `${total_slides}장 슬라이드 분석 중...`;
            
            // 폴링 시작
            this._startPolling();
            
        } catch (e) {
            this._showError(e.message);
        }
    }
    
    _startPolling() {
        this.pollInterval = setInterval(async () => {
            try {
                const res = await fetch(`/api/status/${this.jobId}`);
                const data = await res.json();
                
                // 진행률 업데이트
                if (data.total_slides > 0) {
                    const pct = (data.current_slide / data.total_slides) * 100;
                    document.getElementById('progress-fill').style.width = `${pct}%`;
                }
                document.getElementById('progress-message').textContent = data.message;
                
                // 완료 → 편집기로 이동
                if (data.status === 'editing') {
                    clearInterval(this.pollInterval);
                    window.location.href = `/editor?job_id=${this.jobId}`;
                }
                
                // 실패
                if (data.status === 'failed') {
                    clearInterval(this.pollInterval);
                    this._showError(data.error || '분석 중 오류가 발생했습니다.');
                }
                
            } catch (e) {
                // 네트워크 오류는 무시 (재시도)
            }
        }, 2000);
    }
    
    _showError(msg) {
        this.uploadArea.classList.remove('hidden');
        this.progressArea.classList.add('hidden');
        // 에러 토스트 표시
        const toast = document.createElement('div');
        toast.className = 'toast error';
        toast.textContent = msg;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 5000);
    }
}

// 초기화
new UploadManager();
```

### 11.2 CSS 디자인 시스템 (`css/common.css`)

```css
:root {
    /* 색상 팔레트 */
    --color-primary: #2196F3;
    --color-primary-dark: #1976D2;
    --color-primary-light: #BBDEFB;
    --color-danger: #F44336;
    --color-success: #4CAF50;
    --color-warning: #FF9800;
    
    --color-bg: #F5F5F5;
    --color-surface: #FFFFFF;
    --color-surface-hover: #FAFAFA;
    --color-border: #E0E0E0;
    --color-text: #212121;
    --color-text-secondary: #757575;
    --color-text-muted: #BDBDBD;
    
    /* 간격 */
    --spacing-xs: 4px;
    --spacing-sm: 8px;
    --spacing-md: 16px;
    --spacing-lg: 24px;
    --spacing-xl: 32px;
    
    /* 테두리 */
    --radius-sm: 4px;
    --radius-md: 8px;
    --radius-lg: 12px;
    
    /* 그림자 */
    --shadow-sm: 0 1px 3px rgba(0,0,0,0.12);
    --shadow-md: 0 4px 6px rgba(0,0,0,0.1);
    --shadow-lg: 0 10px 25px rgba(0,0,0,0.15);
    
    /* 타이포그래피 */
    --font-sans: 'Segoe UI', -apple-system, sans-serif;
    --font-mono: 'Cascadia Code', 'Consolas', monospace;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: var(--font-sans);
    background: var(--color-bg);
    color: var(--color-text);
    line-height: 1.5;
}

/* 버튼 */
.btn-primary {
    background: var(--color-primary);
    color: white;
    border: none;
    padding: var(--spacing-sm) var(--spacing-lg);
    border-radius: var(--radius-md);
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    transition: background 0.2s;
}
.btn-primary:hover { background: var(--color-primary-dark); }
.btn-danger { background: var(--color-danger); color: white; /* 동일 구조 */ }

/* 토스트 알림 */
.toast {
    position: fixed;
    bottom: var(--spacing-lg);
    right: var(--spacing-lg);
    padding: var(--spacing-md);
    border-radius: var(--radius-md);
    color: white;
    font-size: 14px;
    z-index: 1000;
    animation: slideIn 0.3s ease;
}
.toast.error { background: var(--color-danger); }
.toast.success { background: var(--color-success); }
```

### 11.3 편집기 CSS 레이아웃 (`css/editor.css`)

```css
.editor-layout {
    display: grid;
    grid-template-columns: 140px 1fr 280px;
    grid-template-rows: 48px 1fr 32px;
    grid-template-areas:
        "toolbar toolbar toolbar"
        "slides  canvas  panel"
        "status  status  status";
    height: 100vh;
    overflow: hidden;
}

/* 도구 모음 */
.toolbar {
    grid-area: toolbar;
    display: flex;
    align-items: center;
    gap: var(--spacing-sm);
    padding: 0 var(--spacing-md);
    background: var(--color-surface);
    border-bottom: 1px solid var(--color-border);
}
.toolbar .tool-btn {
    padding: 6px 12px;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: white;
    cursor: pointer;
    font-size: 13px;
    transition: all 0.15s;
}
.toolbar .tool-btn.active {
    background: var(--color-primary);
    color: white;
    border-color: var(--color-primary);
}
.toolbar .tool-btn:hover:not(.active) {
    background: var(--color-surface-hover);
}

/* 도형 하위 메뉴 (드롭다운) */
.shape-dropdown {
    position: absolute;
    top: 100%;
    left: 0;
    background: white;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow-md);
    padding: var(--spacing-xs);
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 2px;
    z-index: 100;
}
.shape-dropdown .shape-option {
    width: 36px;
    height: 36px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm);
    cursor: pointer;
}
.shape-dropdown .shape-option:hover {
    background: var(--color-primary-light);
}

/* 슬라이드 썸네일 */
.slide-nav {
    grid-area: slides;
    overflow-y: auto;
    padding: var(--spacing-sm);
    background: #EEEEEE;
    border-right: 1px solid var(--color-border);
}
.thumb {
    position: relative;
    margin-bottom: var(--spacing-sm);
    border: 2px solid transparent;
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: border-color 0.15s;
}
.thumb.active { border-color: var(--color-primary); }
.thumb:hover:not(.active) { border-color: var(--color-border); }
.thumb-num {
    position: absolute;
    top: 2px;
    left: 4px;
    font-size: 10px;
    color: var(--color-text-secondary);
}

/* 캔버스 영역 */
.canvas-area {
    grid-area: canvas;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #E0E0E0;
    overflow: hidden;
    position: relative;
}
.canvas-container {
    position: relative;
    box-shadow: var(--shadow-lg);
}
.canvas-container canvas {
    position: absolute;
    top: 0;
    left: 0;
}
#image-canvas { z-index: 1; }
#overlay-canvas { z-index: 2; }

/* 속성 패널 */
.property-panel {
    grid-area: panel;
    overflow-y: auto;
    padding: var(--spacing-md);
    background: var(--color-surface);
    border-left: 1px solid var(--color-border);
}
.panel-section {
    margin-bottom: var(--spacing-md);
    padding-bottom: var(--spacing-md);
    border-bottom: 1px solid var(--color-border);
}
.panel-section h3 {
    font-size: 12px;
    text-transform: uppercase;
    color: var(--color-text-secondary);
    margin-bottom: var(--spacing-sm);
    letter-spacing: 0.5px;
}
.field { margin-bottom: var(--spacing-sm); }
.field label {
    display: block;
    font-size: 11px;
    color: var(--color-text-secondary);
    margin-bottom: 2px;
}
.field input, .field select, .field textarea {
    width: 100%;
    padding: 4px 8px;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    font-size: 13px;
}
.field-row {
    display: flex;
    gap: var(--spacing-sm);
    align-items: center;
    margin-bottom: var(--spacing-sm);
}
.field-row label { font-size: 11px; min-width: 24px; }
.field-row input { width: 70px; }

/* 소스 뱃지 */
.source-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
}
.source-badge.auto { background: #E3F2FD; color: #1565C0; }
.source-badge.manual { background: #FFF3E0; color: #E65100; }

/* 상태 바 */
.status-bar {
    grid-area: status;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 var(--spacing-md);
    background: var(--color-surface);
    border-top: 1px solid var(--color-border);
    font-size: 12px;
    color: var(--color-text-secondary);
}
```

### 11.4 API 클라이언트 (`api-client.js`)

```javascript
class ApiClient {
    static async convertPdf(file) {
        const formData = new FormData();
        formData.append('file', file);
        return this._fetch('/api/convert', { method: 'POST', body: formData });
    }
    
    static async getStatus(jobId) {
        return this._fetch(`/api/status/${jobId}`);
    }
    
    static async getSlides(jobId) {
        return this._fetch(`/api/slides/${jobId}`);
    }
    
    static async saveSlides(jobId, presentationData) {
        return this._fetch(`/api/slides/${jobId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(presentationData),
        });
    }
    
    static async updateElement(jobId, slideNum, elementId, elementData) {
        return this._fetch(`/api/element/${jobId}/${slideNum}/${elementId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(elementData),
        });
    }
    
    static async deleteElement(jobId, slideNum, elementId) {
        return this._fetch(`/api/element/${jobId}/${slideNum}/${elementId}`, {
            method: 'DELETE',
        });
    }
    
    static async ocrRegion(jobId, slideNum, region) {
        return this._fetch('/api/ocr-region', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_id: jobId, slide_num: slideNum, region }),
        });
    }
    
    static async analyzeRegion(jobId, slideNum, region) {
        return this._fetch('/api/analyze-region', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_id: jobId, slide_num: slideNum, region }),
        });
    }
    
    static async generatePptx(jobId) {
        return this._fetch(`/api/generate/${jobId}`, { method: 'POST' });
    }
    
    static getSlideImageUrl(jobId, slideNum) {
        return `/api/slide-image/${jobId}/${slideNum}`;
    }
    
    static getDownloadUrl(jobId) {
        return `/api/download/${jobId}`;
    }
    
    static async _fetch(url, options = {}) {
        const res = await fetch(url, options);
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            throw new Error(body.detail || body.error || `HTTP ${res.status}`);
        }
        // 204 No Content 등 빈 응답 처리
        const text = await res.text();
        return text ? JSON.parse(text) : {};
    }
}
```

### 11.5 편집기 다이얼로그

```javascript
// 범용 모달 다이얼로그
function showDialog({ title, message, buttons }) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'dialog-overlay';
        overlay.innerHTML = `
            <div class="dialog">
                <h3 class="dialog-title">${title}</h3>
                <p class="dialog-message">${message}</p>
                <div class="dialog-buttons">
                    ${buttons.map((label, i) => `
                        <button class="btn-${i === 0 ? 'primary' : 'secondary'}" data-idx="${i}">
                            ${label}
                        </button>
                    `).join('')}
                </div>
            </div>
        `;
        
        overlay.querySelectorAll('button').forEach(btn => {
            btn.addEventListener('click', () => {
                overlay.remove();
                resolve(parseInt(btn.dataset.idx));
            });
        });
        
        document.body.appendChild(overlay);
    });
}

// OCR 결과 미리보기 다이얼로그
function showOcrResultDialog(paragraphs, confidence) {
    return new Promise((resolve) => {
        const text = paragraphs.map(p => 
            p.runs.map(r => r.text).join('')
        ).join('\n');
        
        const overlay = document.createElement('div');
        overlay.className = 'dialog-overlay';
        overlay.innerHTML = `
            <div class="dialog dialog-wide">
                <h3>OCR 결과 (신뢰도: ${confidence})</h3>
                <textarea id="ocr-result-text" rows="6" class="dialog-textarea">${text}</textarea>
                <p class="text-muted">텍스트를 수정할 수 있습니다.</p>
                <div class="dialog-buttons">
                    <button class="btn-primary" id="btn-ocr-accept">적용</button>
                    <button class="btn-secondary" id="btn-ocr-manual">직접 입력으로 전환</button>
                    <button class="btn-secondary" id="btn-ocr-cancel">취소</button>
                </div>
            </div>
        `;
        
        overlay.querySelector('#btn-ocr-accept').onclick = () => {
            // 수정된 텍스트로 paragraphs 재구성
            const editedText = overlay.querySelector('#ocr-result-text').value;
            const editedParagraphs = editedText.split('\n').map(line => ({
                runs: [{ text: line, font: paragraphs[0]?.runs[0]?.font || {} }],
                alignment: 'left', bullet: false, indent_level: 0, line_spacing: 1.2
            }));
            overlay.remove();
            resolve({ action: 'accept', paragraphs: editedParagraphs });
        };
        overlay.querySelector('#btn-ocr-manual').onclick = () => {
            overlay.remove();
            resolve({ action: 'manual' });
        };
        overlay.querySelector('#btn-ocr-cancel').onclick = () => {
            overlay.remove();
            resolve({ action: 'cancel' });
        };
        
        document.body.appendChild(overlay);
    });
}
```

### 11.6 편집기 자동 저장 & 브라우저 이탈 경고

```javascript
// 자동 저장 (30초 간격 + 슬라이드 전환 시)
class AutoSaver {
    constructor(appState, intervalMs = 30000) {
        this.state = appState;
        this.timer = null;
        this.saving = false;
        
        // 주기적 저장
        this.timer = setInterval(() => this.save(), intervalMs);
        
        // 브라우저 이탈 시 경고
        window.addEventListener('beforeunload', (e) => {
            if (this.state.isDirty) {
                e.preventDefault();
                e.returnValue = '';  // Chrome 요구사항
            }
        });
    }
    
    async save() {
        if (!this.state.isDirty || this.saving) return;
        
        this.saving = true;
        updateStatusBar('저장 중...');
        
        try {
            await ApiClient.saveSlides(this.state.jobId, this.state.presentation);
            this.state.isDirty = false;
            updateStatusBar('저장됨');
        } catch (e) {
            updateStatusBar(`저장 실패: ${e.message}`);
        } finally {
            this.saving = false;
        }
    }
    
    destroy() {
        if (this.timer) clearInterval(this.timer);
    }
}
```

### 11.7 편집기 초기화 흐름 (`editor-app.js`)

```javascript
// editor.html 로드 시 실행
async function initEditor() {
    // 1. URL에서 job_id 추출
    const params = new URLSearchParams(window.location.search);
    AppState.jobId = params.get('job_id');
    if (!AppState.jobId) {
        alert('job_id가 없습니다. 업로드 페이지로 돌아갑니다.');
        window.location.href = '/';
        return;
    }
    
    // 2. 슬라이드 데이터 로드
    try {
        AppState.presentation = await ApiClient.getSlides(AppState.jobId);
    } catch (e) {
        alert(`데이터 로드 실패: ${e.message}`);
        return;
    }
    
    // 3. 컴포넌트 초기화
    const imageCanvas = document.getElementById('image-canvas');
    const overlayCanvas = document.getElementById('overlay-canvas');
    
    const renderer = new CanvasRenderer(overlayCanvas, imageCanvas);
    const tools = new ElementTools(renderer, AppState);
    const panel = new PropertyPanel(document.getElementById('property-panel'), AppState);
    const slideNav = new SlideNavigator(document.getElementById('slide-nav'), AppState);
    const undoMgr = new UndoManager();
    const autoSaver = new AutoSaver(AppState);
    
    // 4. Canvas 크기 설정 (16:9 비율, 사용 가능한 공간에 맞춤)
    function resizeCanvas() {
        const area = document.querySelector('.canvas-area');
        const maxW = area.clientWidth - 40;
        const maxH = area.clientHeight - 40;
        const ratio = 16 / 9;
        
        let w, h;
        if (maxW / maxH > ratio) {
            h = maxH;
            w = h * ratio;
        } else {
            w = maxW;
            h = w / ratio;
        }
        
        [imageCanvas, overlayCanvas].forEach(c => {
            c.width = w;
            c.height = h;
            c.style.width = w + 'px';
            c.style.height = h + 'px';
        });
        
        document.querySelector('.canvas-container').style.width = w + 'px';
        document.querySelector('.canvas-container').style.height = h + 'px';
    }
    resizeCanvas();
    window.addEventListener('resize', () => {
        resizeCanvas();
        loadCurrentSlide();
    });
    
    // 5. 첫 슬라이드 로드
    async function loadCurrentSlide() {
        const slideNum = AppState.currentSlideIndex + 1;
        await renderer.loadSlideImage(AppState.jobId, slideNum);
        renderer.renderElements(
            AppState.currentSlide.elements,
            AppState.selectedElementId
        );
        panel.update(AppState.selectedElement);
        slideNav.render();
        updateStatusBar();
    }
    
    await loadCurrentSlide();
    
    // 6. 이벤트 바인딩
    overlayCanvas.addEventListener('mousedown', (e) => {
        undoMgr.pushState(AppState.currentSlide);
        tools.onMouseDown(e);
    });
    overlayCanvas.addEventListener('mousemove', (e) => tools.onMouseMove(e));
    overlayCanvas.addEventListener('mouseup', (e) => {
        tools.onMouseUp(e);
        panel.update(AppState.selectedElement);
    });
    
    // 슬라이드 전환 이벤트
    window.addEventListener('slideChanged', async () => {
        await loadCurrentSlide();
    });
    
    // 도구 모음 버튼
    document.querySelectorAll('.tool-btn[data-tool]').forEach(btn => {
        btn.addEventListener('click', () => {
            AppState.currentTool = btn.dataset.tool;
            document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });
    
    // PPT 생성 버튼
    document.getElementById('btn-generate').addEventListener('click', async () => {
        if (AppState.isDirty) await autoSaver.save();
        
        try {
            document.getElementById('btn-generate').disabled = true;
            document.getElementById('btn-generate').textContent = '생성 중...';
            
            await ApiClient.generatePptx(AppState.jobId);
            
            // 다운로드 트리거
            const a = document.createElement('a');
            a.href = ApiClient.getDownloadUrl(AppState.jobId);
            a.download = 'presentation.pptx';
            a.click();
            
        } catch (e) {
            alert(`PPT 생성 실패: ${e.message}`);
        } finally {
            document.getElementById('btn-generate').disabled = false;
            document.getElementById('btn-generate').textContent = 'PPT 생성';
        }
    });
    
    // 전체 재분석 버튼
    document.getElementById('btn-reanalyze').addEventListener('click', async () => {
        if (!confirm('현재 슬라이드를 다시 분석하면 수동 편집 내용이 사라집니다. 계속하시겠습니까?')) return;
        // 현재 슬라이드만 재분석
        const slideNum = AppState.currentSlideIndex + 1;
        try {
            const res = await fetch(`/api/reanalyze/${AppState.jobId}/${slideNum}`, { method: 'POST' });
            const newSlide = await res.json();
            AppState.presentation.slides[AppState.currentSlideIndex] = newSlide;
            AppState.isDirty = true;
            await loadCurrentSlide();
        } catch (e) {
            alert(`재분석 실패: ${e.message}`);
        }
    });
    
    // 키보드 단축키 (§5.7에 정의된 전체)
    document.addEventListener('keydown', handleKeyboard);
}

function updateStatusBar(customMsg) {
    const slide = AppState.currentSlide;
    const total = AppState.presentation?.slides?.length || 0;
    const idx = AppState.currentSlideIndex + 1;
    const autoCount = slide?.elements?.filter(e => e.source === 'auto').length || 0;
    const manualCount = slide?.elements?.filter(e => e.source === 'manual').length || 0;
    
    const left = `슬라이드 ${idx}/${total}`;
    const mid = `요소 ${autoCount + manualCount}개 (자동 ${autoCount}, 수동 ${manualCount})`;
    const right = customMsg || (AppState.isDirty ? '수정됨 (미저장)' : '저장됨');
    
    document.getElementById('status-left').textContent = left;
    document.getElementById('status-mid').textContent = mid;
    document.getElementById('status-right').textContent = right;
}

document.addEventListener('DOMContentLoaded', initEditor);
```

---

## 12. 보안 고려사항

| 영역 | 위협 | 대응 |
|------|------|------|
| 파일 업로드 | 악성 파일 (PDF 아닌 것) | magic bytes 검증 (`%PDF-` 시작), 확장자 + Content-Type 검증 |
| 파일 크기 | DoS (대용량 파일) | 50MB 제한, FastAPI의 `UploadFile` 스트리밍 |
| Path traversal | job_id 조작으로 임의 파일 접근 | job_id를 UUID hex로 생성 (영숫자만), `..` 포함 여부 검증 |
| API 키 노출 | .env가 커밋됨 | .gitignore에 .env 포함, .env.example은 값 없이 |
| XSS | 사용자 입력이 HTML에 삽입 | `textContent` 사용 (innerHTML에 사용자 데이터 직접 넣지 않음), property panel에서 입력값 이스케이프 |
| CORS | 무제한 origin | 개발 중만 `*`, 프로덕션에서는 특정 origin |
| Claude API 남용 | 과도한 OCR 요청 | 엔드포인트별 rate limit (IP당 분당 10회) |

---

## 13. 성능 최적화

| 영역 | 전략 |
|------|------|
| 슬라이드 이미지 | `Cache-Control: max-age=3600` — 이미지는 불변 |
| 썸네일 | 120x68 크기로 축소된 이미지를 별도 캐시. Canvas에 원본 로드 후 축소 렌더링 |
| Canvas 렌더링 | `requestAnimationFrame` 사용. 드래그 중에만 매 프레임 렌더, 그 외에는 이벤트 기반 |
| 대용량 PDF | 이미지 변환은 동기지만 빠름 (PyMuPDF C 바인딩). Vision API가 병목 |
| 자동 저장 | 30초 디바운스. `isDirty` 플래그로 불필요한 저장 방지 |
| 메모리 | 슬라이드 이미지는 Canvas 로드 후 Image 객체 캐시. 현재 슬라이드 ± 1만 메모리 유지 |

---

## 14. 구현 순서 세분화 (최종)

### Phase 1 — 프로젝트 초기화 (Step 1-4)
1. `git init` + `.gitignore` (output/, .env, __pycache__, *.pyc, node_modules/)
2. `requirements.txt` 작성 + 가상환경 생성 + 설치
3. `.env.example` 작성
4. 기본 디렉토리 구조 생성 (`app/`, `app/routers/`, `app/static/`, `app/static/css/`, `app/static/js/`, `tests/`, `tests/fixtures/`)

### Phase 2 — 백엔드 골격 (Step 5-12)
5. `app/config.py` — Settings 전체 (§10.2)
6. `app/models.py` — 모든 Pydantic 모델 (§1 전체)
7. `app/color_utils.py` (§10.9)
8. `app/font_utils.py` (§6)
9. `app/job_manager.py` — Job CRUD + 파일 I/O (§10.4)
10. `app/main.py` — FastAPI 앱 + lifespan + 예외 핸들러 (§10.3)
11. `app/routers/__init__.py` + 4개 라우터 파일 스텁
12. `run.py` (§10.1)
13. **검증**: `python run.py` → 서버 기동 확인

### Phase 3 — PDF → 이미지 (Step 14-15)
14. `app/pdf_to_images.py` — validate_pdf + pdf_to_images + placeholder (§10.5)
15. **검증**: Python REPL에서 테스트 PDF → PNG 리스트 생성

### Phase 4 — Claude Vision 연동 (Step 16-18)
16. `app/vision_analyzer.py` — 전체 프롬프트 + analyze_slide() + JSON 파싱 + 재시도 (§3.1, §3.4)
17. `app/region_ocr.py` — crop_image + ocr_region + analyze_region (§10.6)
18. **검증**: 단일 슬라이드 이미지 → JSON 출력 확인, 프롬프트 품질 반복

### Phase 5 — PPTX 빌더 (Step 19-21)
19. `app/pptx_builder.py` — 전체 빌드 함수 + 요소별 빌더 + XML 헬퍼 (§4 전체 + §10.10)
20. `tests/fixtures/sample_vision_response.json` — 수동 작성 테스트 데이터
21. **검증**: fixture JSON → build_presentation() → PPTX 파일 → PowerPoint에서 열기

### Phase 6 — 파이프라인 & API 연결 (Step 22-27)
22. `app/pipeline.py` — extract_all_slides() 전체 (§10.7)
23. `app/routers/upload.py` — POST /api/convert + GET /api/status (§10.8)
24. `app/routers/editor_api.py` — GET/PUT /api/slides + GET /api/slide-image + PUT/DELETE 요소
25. `app/routers/ocr.py` — POST /api/ocr-region + POST /api/analyze-region
26. `app/routers/generate.py` — POST /api/generate + GET /api/download
27. **검증**: curl/httpie로 전체 API 플로우 테스트 (업로드 → 상태 → 데이터 → 생성 → 다운로드)

### Phase 7 — 업로드 프론트엔드 (Step 28-31)
28. `app/static/css/common.css` — 디자인 시스템 (§11.2)
29. `app/static/css/index.css` — 업로드 페이지 스타일
30. `app/static/index.html` — 드래그 앤 드롭 + 진행률 UI (§11.1)
31. `app/static/js/upload.js` — UploadManager 전체 (§11.1)
32. **검증**: 브라우저에서 PDF 업로드 → 진행률 → 편집기 리다이렉트

### Phase 8 — 편집기 프론트엔드 (Step 33-43)
33. `app/static/css/editor.css` — 편집기 레이아웃 전체 (§11.3)
34. `app/static/editor.html` — HTML 골격 (toolbar + slide-nav + canvas-area + property-panel + status-bar)
35. `app/static/js/api-client.js` — ApiClient 전체 (§11.4)
36. `app/static/js/canvas-renderer.js` — CanvasRenderer 클래스 (§5.3 + 이미지 로드 + 그리기 프리뷰)
37. `app/static/js/element-tools.js` — 선택/이동/리사이즈 구현 (§5.4 전반부)
38. `app/static/js/element-tools.js` — 텍스트/도형/선 그리기 + OCR 연동 (§5.4 후반부)
39. `app/static/js/property-panel.js` — 속성 패널 전체 + 양방향 바인딩 (§5.5)
40. `app/static/js/slide-nav.js` — 슬라이드 내비게이션 (§5.6)
41. `app/static/js/editor-app.js` — 상태 관리 + 초기화 + 이벤트 연결 (§11.7)
42. 다이얼로그 (OCR 결과 미리보기, 삭제 확인) (§11.5)
43. Undo/Redo + 자동 저장 + 키보드 단축키 (§5.7, §5.8, §11.6)
44. **검증**: 전체 E2E — PDF 업로드 → 편집기 → 수동 편집 → PPT 생성

### Phase 9 — 마무리 (Step 45-47)
45. 에러 핸들링 전체 검토 (§7, §12 보안 테이블)
46. 파일 업로드 보안 검증 (magic bytes, path traversal)
47. CLAUDE.md 업데이트

---

## 15. 나머지 라우터 전체 구현

### 15.1 `app/routers/editor_api.py`

```python
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pathlib import Path

from app.models import PresentationData, SlideElement, JobStatus
from app.job_manager import (
    get_job, load_slides_json, save_slides_json, get_slide_image_path
)

router = APIRouter()

def _require_editing_state(job_id: str):
    """job이 편집 가능 상태인지 확인"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job을 찾을 수 없습니다.")
    if job.status not in (JobStatus.EDITING, JobStatus.COMPLETE):
        raise HTTPException(403, f"현재 상태({job.status.value})에서는 편집할 수 없습니다.")
    return job

@router.get("/slides/{job_id}")
async def get_slides(job_id: str):
    _require_editing_state(job_id)
    data = load_slides_json(job_id)
    if not data:
        raise HTTPException(404, "슬라이드 데이터가 없습니다.")
    return data.model_dump()

@router.put("/slides/{job_id}")
async def save_slides(job_id: str, data: PresentationData):
    _require_editing_state(job_id)
    save_slides_json(job_id, data)
    return {"saved": True, "slides_count": len(data.slides)}

@router.get("/slide-image/{job_id}/{slide_num}")
async def get_slide_image(job_id: str, slide_num: int):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job을 찾을 수 없습니다.")
    
    path = get_slide_image_path(job_id, slide_num)
    if not path:
        raise HTTPException(404, f"슬라이드 {slide_num} 이미지가 없습니다.")
    
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"}
    )

@router.put("/element/{job_id}/{slide_num}/{element_id}")
async def update_element(job_id: str, slide_num: int, element_id: str, element: SlideElement):
    """단일 요소 업데이트 (속성 패널에서 실시간 편집 시)"""
    _require_editing_state(job_id)
    data = load_slides_json(job_id)
    if not data:
        raise HTTPException(404, "슬라이드 데이터가 없습니다.")
    
    # 슬라이드 찾기 (slide_num은 1-based)
    slide = next((s for s in data.slides if s.slide_number == slide_num), None)
    if not slide:
        raise HTTPException(404, f"슬라이드 {slide_num}을 찾을 수 없습니다.")
    
    # 요소 찾기 및 교체
    for i, elem in enumerate(slide.elements):
        if elem.id == element_id:
            slide.elements[i] = element
            save_slides_json(job_id, data)
            return {"updated": True}
    
    raise HTTPException(404, f"요소 {element_id}를 찾을 수 없습니다.")

@router.delete("/element/{job_id}/{slide_num}/{element_id}")
async def delete_element(job_id: str, slide_num: int, element_id: str):
    _require_editing_state(job_id)
    data = load_slides_json(job_id)
    if not data:
        raise HTTPException(404)
    
    slide = next((s for s in data.slides if s.slide_number == slide_num), None)
    if not slide:
        raise HTTPException(404)
    
    original_len = len(slide.elements)
    slide.elements = [e for e in slide.elements if e.id != element_id]
    
    if len(slide.elements) == original_len:
        raise HTTPException(404, f"요소 {element_id}를 찾을 수 없습니다.")
    
    save_slides_json(job_id, data)
    return {"deleted": True, "remaining_elements": len(slide.elements)}
```

### 15.2 `app/routers/ocr.py`

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models import BoundingBox, JobStatus
from app.config import settings
from app.job_manager import get_job, get_slide_image_path
from app.region_ocr import ocr_region, analyze_region

router = APIRouter()

class OcrRegionRequest(BaseModel):
    job_id: str
    slide_num: int
    region: BoundingBox

@router.post("/ocr-region")
async def ocr_region_endpoint(req: OcrRegionRequest):
    # 검증
    job = get_job(req.job_id)
    if not job:
        raise HTTPException(404, "Job을 찾을 수 없습니다.")
    if job.status not in (JobStatus.EDITING, JobStatus.COMPLETE):
        raise HTTPException(403, "편집 상태가 아닙니다.")
    
    # 영역 크기 검증
    if req.region.width_pct < settings.min_region_pct or req.region.height_pct < settings.min_region_pct:
        raise HTTPException(400, f"영역이 너무 작습니다 (최소 {settings.min_region_pct}%)")
    
    # 이미지 로드
    img_path = get_slide_image_path(req.job_id, req.slide_num)
    if not img_path:
        raise HTTPException(404, f"슬라이드 {req.slide_num} 이미지가 없습니다.")
    
    image_bytes = img_path.read_bytes()
    
    # OCR 실행
    result = await ocr_region(image_bytes, req.region)
    return result

@router.post("/analyze-region")
async def analyze_region_endpoint(req: OcrRegionRequest):
    """영역의 요소를 전체 분석 (텍스트뿐 아니라 도형/색상 등)"""
    job = get_job(req.job_id)
    if not job:
        raise HTTPException(404, "Job을 찾을 수 없습니다.")
    if job.status not in (JobStatus.EDITING, JobStatus.COMPLETE):
        raise HTTPException(403)
    
    if req.region.width_pct < settings.min_region_pct or req.region.height_pct < settings.min_region_pct:
        raise HTTPException(400, f"영역이 너무 작습니다.")
    
    img_path = get_slide_image_path(req.job_id, req.slide_num)
    if not img_path:
        raise HTTPException(404)
    
    image_bytes = img_path.read_bytes()
    result = await analyze_region(image_bytes, req.region)
    
    # bbox는 요청의 region으로 설정
    result["bbox"] = req.region.model_dump()
    result["source"] = "manual"
    
    return result

@router.post("/reanalyze/{job_id}/{slide_num}")
async def reanalyze_slide(job_id: str, slide_num: int):
    """특정 슬라이드를 Claude Vision으로 다시 분석"""
    from app.vision_analyzer import analyze_slide
    from app.job_manager import load_slides_json, save_slides_json
    
    job = get_job(job_id)
    if not job:
        raise HTTPException(404)
    
    img_path = get_slide_image_path(job_id, slide_num)
    if not img_path:
        raise HTTPException(404)
    
    image_bytes = img_path.read_bytes()
    new_slide = await analyze_slide(image_bytes, slide_num)
    
    # 기존 데이터에서 해당 슬라이드만 교체
    data = load_slides_json(job_id)
    if data:
        for i, s in enumerate(data.slides):
            if s.slide_number == slide_num:
                data.slides[i] = new_slide
                break
        save_slides_json(job_id, data)
    
    return new_slide.model_dump()
```

### 15.3 `app/routers/generate.py`

```python
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

from app.models import JobStatus
from app.job_manager import get_job, update_job, load_slides_json
from app.pptx_builder import build_presentation

router = APIRouter()

@router.post("/generate/{job_id}")
async def generate_pptx(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job을 찾을 수 없습니다.")
    if job.status not in (JobStatus.EDITING, JobStatus.COMPLETE):
        raise HTTPException(403, "편집 상태가 아닙니다.")
    
    # 데이터 로드
    data = load_slides_json(job_id)
    if not data or not data.slides:
        raise HTTPException(400, "슬라이드 데이터가 비어있습니다.")
    
    # 상태 전이
    update_job(job_id, status=JobStatus.GENERATING, message="PPT 생성 중...")
    
    try:
        # PPTX 생성
        pptx_bytes = build_presentation(data)
        
        # 파일 저장
        output_path = Path(job.output_dir) / "result.pptx"
        output_path.write_bytes(pptx_bytes)
        
        update_job(job_id, status=JobStatus.COMPLETE, message="PPT 생성 완료!")
        
        return {
            "ready": True,
            "filename": "NotebookLM_Slides.pptx",
            "size_bytes": len(pptx_bytes),
            "slides_count": len(data.slides),
            "total_elements": sum(len(s.elements) for s in data.slides),
        }
        
    except Exception as e:
        update_job(job_id, status=JobStatus.EDITING, error=str(e), message=f"PPT 생성 실패: {e}")
        raise HTTPException(500, f"PPT 생성 중 오류: {e}")

@router.get("/download/{job_id}")
async def download_pptx(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job을 찾을 수 없습니다.")
    
    pptx_path = Path(job.output_dir) / "result.pptx"
    if not pptx_path.exists():
        raise HTTPException(404, "생성된 PPT 파일이 없습니다. 먼저 /api/generate를 호출하세요.")
    
    return FileResponse(
        pptx_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename="NotebookLM_Slides.pptx",
        headers={"Content-Disposition": "attachment; filename=NotebookLM_Slides.pptx"}
    )
```

---

## 16. Canvas 헬퍼 메서드 전체 구현

### 16.1 CanvasRenderer — 누락된 메서드들

```javascript
class CanvasRenderer {
    constructor(overlayCanvas, imageCanvas) {
        this.canvas = overlayCanvas;
        this.imageCanvas = imageCanvas;
        this.ctx = overlayCanvas.getContext('2d');
        this.imgCtx = imageCanvas.getContext('2d');
        this.slideImage = null;      // 현재 슬라이드의 Image 객체
        this.imageCache = {};        // { "jobId_slideNum": Image } 캐시
        this.drawPreviewRect = null; // 그리기 중 미리보기 사각형
    }
    
    async loadSlideImage(jobId, slideNum) {
        const cacheKey = `${jobId}_${slideNum}`;
        
        if (this.imageCache[cacheKey]) {
            this.slideImage = this.imageCache[cacheKey];
            this._drawImage();
            this._preloadAdjacentSlides(jobId, slideNum);
            return;
        }
        
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => {
                this.slideImage = img;
                this.imageCache[cacheKey] = img;
                this._drawImage();
                this._preloadAdjacentSlides(jobId, slideNum);
                resolve();
            };
            img.onerror = () => reject(new Error('이미지 로드 실패'));
            img.src = ApiClient.getSlideImageUrl(jobId, slideNum);
        });
    }
    
    _drawImage() {
        if (!this.slideImage) return;
        this.imgCtx.clearRect(0, 0, this.imageCanvas.width, this.imageCanvas.height);
        this.imgCtx.drawImage(
            this.slideImage,
            0, 0,
            this.imageCanvas.width,
            this.imageCanvas.height
        );
    }
    
    _preloadAdjacentSlides(jobId, slideNum) {
        // 현재 슬라이드의 앞뒤 슬라이드를 미리 로드
        for (const offset of [-1, 1]) {
            const adjNum = slideNum + offset;
            const key = `${jobId}_${adjNum}`;
            if (adjNum >= 1 && !this.imageCache[key]) {
                const img = new Image();
                img.onload = () => { this.imageCache[key] = img; };
                img.src = ApiClient.getSlideImageUrl(jobId, adjNum);
            }
        }
    }
    
    // 요소 이름 라벨 그리기
    _drawLabel(rect, label, color) {
        const fontSize = 10;
        this.ctx.font = `${fontSize}px sans-serif`;
        const textWidth = this.ctx.measureText(label).width;
        
        // 라벨 배경
        const labelH = fontSize + 4;
        const labelY = rect.y - labelH - 2;
        
        if (labelY >= 0) {
            this.ctx.fillStyle = color;
            this.ctx.fillRect(rect.x, labelY, textWidth + 8, labelH);
            this.ctx.fillStyle = '#FFFFFF';
            this.ctx.fillText(label, rect.x + 4, labelY + fontSize);
        }
    }
    
    // 그리기 미리보기 (rubber band)
    renderDrawPreview(startPct, currentPct, tool) {
        // 기존 요소 다시 그리기 (잔상 방지)
        // → 호출 측에서 renderElements 후 이 메서드 호출
        
        if (!startPct || !currentPct) return;
        
        const x1 = (Math.min(startPct.x_pct, currentPct.x_pct) / 100) * this.canvas.width;
        const y1 = (Math.min(startPct.y_pct, currentPct.y_pct) / 100) * this.canvas.height;
        const x2 = (Math.max(startPct.x_pct, currentPct.x_pct) / 100) * this.canvas.width;
        const y2 = (Math.max(startPct.y_pct, currentPct.y_pct) / 100) * this.canvas.height;
        
        const w = x2 - x1;
        const h = y2 - y1;
        
        this.ctx.save();
        this.ctx.setLineDash([4, 4]);
        this.ctx.lineWidth = 2;
        
        if (tool === 'line') {
            // 선 도구는 시작→끝 직선
            const sx = (startPct.x_pct / 100) * this.canvas.width;
            const sy = (startPct.y_pct / 100) * this.canvas.height;
            const ex = (currentPct.x_pct / 100) * this.canvas.width;
            const ey = (currentPct.y_pct / 100) * this.canvas.height;
            this.ctx.strokeStyle = '#F44336';
            this.ctx.beginPath();
            this.ctx.moveTo(sx, sy);
            this.ctx.lineTo(ex, ey);
            this.ctx.stroke();
        } else {
            // 텍스트/도형 도구는 사각형
            const color = tool === 'text' ? '#2196F3' : '#4CAF50';
            this.ctx.strokeStyle = color;
            this.ctx.strokeRect(x1, y1, w, h);
            this.ctx.fillStyle = color + '15';
            this.ctx.fillRect(x1, y1, w, h);
            
            // 크기 표시
            const wPct = Math.abs(currentPct.x_pct - startPct.x_pct).toFixed(1);
            const hPct = Math.abs(currentPct.y_pct - startPct.y_pct).toFixed(1);
            this.ctx.fillStyle = color;
            this.ctx.font = '11px sans-serif';
            this.ctx.fillText(`${wPct}% × ${hPct}%`, x1, y1 - 4);
        }
        
        this.ctx.restore();
    }
    
    // 줌 적용
    setZoom(level) {
        // level: 0.5 ~ 2.0
        const container = this.canvas.parentElement;
        container.style.transform = `scale(${level})`;
        container.style.transformOrigin = 'center center';
    }
}
```

### 16.2 ElementTools — 누락된 메서드들

```javascript
// ElementTools 클래스 내부 메서드 (§5.4에 추가)

    // hit-test: 마우스 좌표가 바운딩 박스 내부인지
    _isInsideBbox(pct, bbox) {
        return (
            pct.x_pct >= bbox.x_pct &&
            pct.x_pct <= bbox.x_pct + bbox.width_pct &&
            pct.y_pct >= bbox.y_pct &&
            pct.y_pct <= bbox.y_pct + bbox.height_pct
        );
    }
    
    // hit-test: 리사이즈 핸들 위인지 (반환: 핸들 타입 또는 null)
    _hitTestHandle(mouseX, mouseY) {
        if (!this.state.selectedElement) return null;
        
        const rect = this.renderer.pctToPixel(this.state.selectedElement.bbox);
        const handles = this.renderer._getHandlePositions(rect);
        const THRESHOLD = 8; // 핸들 클릭 허용 오차 (px)
        
        for (const h of handles) {
            if (Math.abs(mouseX - h.x) <= THRESHOLD && Math.abs(mouseY - h.y) <= THRESHOLD) {
                return h.type; // 'nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'
            }
        }
        return null;
    }
    
    // 두 점으로부터 정규화된 bbox 생성 (좌상단 기준)
    _normalizeBbox(startPct, endPct) {
        return {
            x_pct: Math.max(0, Math.min(startPct.x_pct, endPct.x_pct)),
            y_pct: Math.max(0, Math.min(startPct.y_pct, endPct.y_pct)),
            width_pct: Math.min(100, Math.abs(endPct.x_pct - startPct.x_pct)),
            height_pct: Math.min(100, Math.abs(endPct.y_pct - startPct.y_pct)),
        };
    }
    
    // 그리기 중 미리보기 업데이트
    _handleDrawPreview(pct) {
        this.renderer.renderElements(
            this.state.currentSlide.elements,
            this.state.selectedElementId
        );
        this.renderer.renderDrawPreview(
            this.dragOrigin,
            pct,
            this.state.currentTool
        );
    }
    
    // 삭제 도구 클릭 처리
    _handleDeleteClick(pct) {
        const elements = [...this.state.currentSlide.elements].reverse();
        const hit = elements.find(el => this._isInsideBbox(pct, el.bbox));
        
        if (hit) {
            const idx = this.state.currentSlide.elements.findIndex(e => e.id === hit.id);
            if (idx !== -1) {
                this.state.currentSlide.elements.splice(idx, 1);
                this.state.selectedElementId = null;
                this.state.isDirty = true;
                this.renderer.renderElements(
                    this.state.currentSlide.elements,
                    null
                );
            }
        }
    }
    
    // 마우스 커서 업데이트
    _updateCursor(e, pct) {
        const canvas = this.renderer.canvas;
        
        switch (this.state.currentTool) {
            case 'select':
                // 핸들 위: 리사이즈 커서
                if (this.state.selectedElement) {
                    const handle = this._hitTestHandle(e.offsetX, e.offsetY);
                    if (handle) {
                        const cursorMap = {
                            'nw': 'nw-resize', 'n': 'n-resize', 'ne': 'ne-resize',
                            'e': 'e-resize', 'se': 'se-resize', 's': 's-resize',
                            'sw': 'sw-resize', 'w': 'w-resize'
                        };
                        canvas.style.cursor = cursorMap[handle];
                        return;
                    }
                }
                // 요소 위: move 커서
                const elements = [...(this.state.currentSlide?.elements || [])].reverse();
                const hovered = elements.find(el => this._isInsideBbox(pct, el.bbox));
                canvas.style.cursor = hovered ? 'move' : 'default';
                break;
            
            case 'text':
                canvas.style.cursor = 'text';
                break;
            case 'shape':
            case 'line':
                canvas.style.cursor = 'crosshair';
                break;
            case 'delete':
                canvas.style.cursor = 'pointer';
                break;
            default:
                canvas.style.cursor = 'default';
        }
    }
    
    // 선 요소 생성
    _createLineElement(bbox) {
        return {
            id: generateId(),
            element_type: 'line',
            bbox: bbox,
            source: 'manual',
            line_color_hex: '#000000',
            line_width_pt: 2.0,
            line_style: 'solid',
            start_arrow: 'none',
            end_arrow: 'triangle',
            paragraphs: [],
            z_order: Math.max(...this.state.currentSlide.elements.map(e => e.z_order), 0) + 1,
            name: `manual_line_${Date.now()}`
        };
    }

// ID 생성 유틸
function generateId() {
    return Math.random().toString(36).substring(2, 10);
}
```

### 16.3 PropertyPanel._bindEvents 전체

```javascript
    _bindEvents(elem) {
        const panel = this.panel;
        
        // === 요소 정보 섹션 ===
        
        // 타입 변경
        panel.querySelector('#prop-type')?.addEventListener('change', (e) => {
            elem.element_type = e.target.value;
            this.state.isDirty = true;
            // 타입 변경 시 패널 전체 재렌더 (섹션이 바뀌므로)
            this.update(elem);
            refreshCanvas();
        });
        
        // 이름 변경
        panel.querySelector('#prop-name')?.addEventListener('input', (e) => {
            elem.name = e.target.value;
            this.state.isDirty = true;
            refreshCanvas(); // 라벨 업데이트
        });
        
        // === 위치/크기 섹션 ===
        
        const posInputs = {
            '#prop-x': 'x_pct',
            '#prop-y': 'y_pct',
            '#prop-w': 'width_pct',
            '#prop-h': 'height_pct',
        };
        
        for (const [selector, field] of Object.entries(posInputs)) {
            panel.querySelector(selector)?.addEventListener('input', (e) => {
                const val = parseFloat(e.target.value);
                if (!isNaN(val) && val >= 0 && val <= 100) {
                    elem.bbox[field] = val;
                    this.state.isDirty = true;
                    refreshCanvas();
                }
            });
        }
        
        panel.querySelector('#prop-rotation')?.addEventListener('input', (e) => {
            elem.rotation = parseFloat(e.target.value) || 0;
            this.state.isDirty = true;
        });
        
        panel.querySelector('#prop-z')?.addEventListener('input', (e) => {
            elem.z_order = parseInt(e.target.value) || 0;
            this.state.isDirty = true;
            refreshCanvas();
        });
        
        // === 텍스트 섹션 ===
        
        panel.querySelectorAll('.para-editor').forEach((paraEl) => {
            const idx = parseInt(paraEl.dataset.paraIdx);
            const para = elem.paragraphs[idx];
            if (!para) return;
            
            // 텍스트 내용
            paraEl.querySelector('.para-text')?.addEventListener('input', (e) => {
                // 단순화: 전체 텍스트를 하나의 run으로 (서식 유지)
                if (para.runs.length > 0) {
                    para.runs[0].text = e.target.value;
                }
                this.state.isDirty = true;
            });
            
            // 폰트 패밀리
            paraEl.querySelector('.font-family')?.addEventListener('change', (e) => {
                para.runs.forEach(r => { r.font.family = e.target.value; });
                this.state.isDirty = true;
            });
            
            // 폰트 크기
            paraEl.querySelector('.font-size')?.addEventListener('input', (e) => {
                const size = parseFloat(e.target.value);
                if (size > 0 && size <= 200) {
                    para.runs.forEach(r => { r.font.size_pt = size; });
                    this.state.isDirty = true;
                }
            });
            
            // 볼드 토글
            paraEl.querySelector('.toggle-bold')?.addEventListener('click', (e) => {
                const newBold = !para.runs[0]?.font?.bold;
                para.runs.forEach(r => { r.font.bold = newBold; });
                e.target.classList.toggle('active', newBold);
                this.state.isDirty = true;
            });
            
            // 이탤릭 토글
            paraEl.querySelector('.toggle-italic')?.addEventListener('click', (e) => {
                const newItalic = !para.runs[0]?.font?.italic;
                para.runs.forEach(r => { r.font.italic = newItalic; });
                e.target.classList.toggle('active', newItalic);
                this.state.isDirty = true;
            });
            
            // 폰트 색상
            paraEl.querySelector('.font-color')?.addEventListener('input', (e) => {
                para.runs.forEach(r => { r.font.color_hex = e.target.value; });
                this.state.isDirty = true;
            });
            
            // 정렬
            paraEl.querySelector('.para-align')?.addEventListener('change', (e) => {
                para.alignment = e.target.value;
                this.state.isDirty = true;
            });
        });
        
        // 단락 추가 버튼
        panel.querySelector('#btn-add-paragraph')?.addEventListener('click', () => {
            elem.paragraphs.push({
                runs: [{ text: '', font: { family: 'Calibri', size_pt: 18, bold: false, italic: false, underline: false, color_hex: '#000000' } }],
                alignment: 'left', bullet: false, indent_level: 0, line_spacing: 1.2
            });
            this.state.isDirty = true;
            this.update(elem); // 재렌더
        });
        
        // === 도형 섹션 ===
        
        panel.querySelector('#prop-shape-kind')?.addEventListener('change', (e) => {
            elem.shape_kind = e.target.value;
            this.state.isDirty = true;
        });
        
        panel.querySelector('#prop-fill')?.addEventListener('input', (e) => {
            elem.fill_color_hex = e.target.value;
            this.state.isDirty = true;
        });
        
        panel.querySelector('#prop-fill-opacity')?.addEventListener('input', (e) => {
            elem.fill_opacity = parseFloat(e.target.value);
            this.state.isDirty = true;
        });
        
        panel.querySelector('#prop-border')?.addEventListener('input', (e) => {
            elem.border_color_hex = e.target.value;
            this.state.isDirty = true;
        });
        
        panel.querySelector('#prop-border-width')?.addEventListener('input', (e) => {
            elem.border_width_pt = parseFloat(e.target.value) || 1;
            this.state.isDirty = true;
        });
        
        // === 선 섹션 ===
        
        panel.querySelector('#prop-line-color')?.addEventListener('input', (e) => {
            elem.line_color_hex = e.target.value;
            this.state.isDirty = true;
        });
        
        panel.querySelector('#prop-line-width')?.addEventListener('input', (e) => {
            elem.line_width_pt = parseFloat(e.target.value) || 1;
            this.state.isDirty = true;
        });
        
        panel.querySelector('#prop-line-style')?.addEventListener('change', (e) => {
            elem.line_style = e.target.value;
            this.state.isDirty = true;
        });
        
        panel.querySelector('#prop-start-arrow')?.addEventListener('change', (e) => {
            elem.start_arrow = e.target.value;
            this.state.isDirty = true;
        });
        
        panel.querySelector('#prop-end-arrow')?.addEventListener('change', (e) => {
            elem.end_arrow = e.target.value;
            this.state.isDirty = true;
        });
        
        // === 삭제 버튼 ===
        
        panel.querySelector('#btn-delete-element')?.addEventListener('click', () => {
            if (!confirm('이 요소를 삭제하시겠습니까?')) return;
            const idx = this.state.currentSlide.elements.findIndex(e => e.id === elem.id);
            if (idx !== -1) {
                this.state.currentSlide.elements.splice(idx, 1);
                this.state.selectedElementId = null;
                this.state.isDirty = true;
                this.update(null);
                refreshCanvas();
            }
        });
    }
    
    // select 옵션 생성 헬퍼들
    _typeOptions(current) {
        const types = ['text_box', 'shape', 'line', 'connector', 'image', 'table'];
        return types.map(t => `<option value="${t}" ${t === current ? 'selected' : ''}>${t}</option>`).join('');
    }
    
    _shapeKindOptions(current) {
        const kinds = ['rectangle','rounded_rectangle','ellipse','triangle','right_arrow','left_arrow',
                       'diamond','pentagon','hexagon','cloud','star','chevron','callout'];
        return kinds.map(k => `<option value="${k}" ${k === current ? 'selected' : ''}>${k}</option>`).join('');
    }
    
    _arrowOptions(current) {
        const arrows = ['none','triangle','stealth','open'];
        return arrows.map(a => `<option value="${a}" ${a === current ? 'selected' : ''}>${a}</option>`).join('');
    }
```

### 16.4 SlideNavigator._renderThumbnails

```javascript
    async _renderThumbnails() {
        const thumbCanvases = this.nav.querySelectorAll('.thumb-canvas');
        
        for (const canvas of thumbCanvases) {
            const index = parseInt(canvas.closest('.thumb').dataset.index);
            const slideNum = index + 1;
            const cacheKey = `${this.state.jobId}_${slideNum}`;
            
            const ctx = canvas.getContext('2d');
            
            // 이미지 캐시 확인
            const img = new Image();
            img.onload = () => {
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
            };
            img.src = ApiClient.getSlideImageUrl(this.state.jobId, slideNum);
        }
        
        // 클릭 이벤트
        this.nav.querySelectorAll('.thumb').forEach(thumb => {
            thumb.addEventListener('click', () => {
                const idx = parseInt(thumb.dataset.index);
                this.goToSlide(idx);
            });
        });
    }
    
    _autoSave() {
        // AutoSaver 인스턴스에 위임
        if (window._autoSaver) {
            window._autoSaver.save();
        }
    }
```

---

## 17. editor.html 전체 HTML

```html
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PDF → PPT 편집기</title>
    <link rel="stylesheet" href="/static/css/common.css">
    <link rel="stylesheet" href="/static/css/editor.css">
</head>
<body>
    <div class="editor-layout">
        <!-- 도구 모음 -->
        <div class="toolbar">
            <div class="tool-group">
                <button class="tool-btn active" data-tool="select" title="선택 (1)">
                    <span>↖</span> 선택
                </button>
                <button class="tool-btn" data-tool="text" title="텍스트 (2)">
                    <span>T</span> 텍스트
                </button>
                <div class="tool-btn-wrapper" style="position: relative;">
                    <button class="tool-btn" data-tool="shape" title="도형 (3)" id="btn-shape-tool">
                        <span>▢</span> 도형 ▾
                    </button>
                    <div class="shape-dropdown hidden" id="shape-dropdown">
                        <div class="shape-option" data-shape="rectangle" title="사각형">▢</div>
                        <div class="shape-option" data-shape="rounded_rectangle" title="둥근 사각형">▢</div>
                        <div class="shape-option" data-shape="ellipse" title="원">○</div>
                        <div class="shape-option" data-shape="triangle" title="삼각형">△</div>
                        <div class="shape-option" data-shape="diamond" title="마름모">◇</div>
                        <div class="shape-option" data-shape="right_arrow" title="화살표">→</div>
                        <div class="shape-option" data-shape="star" title="별">☆</div>
                        <div class="shape-option" data-shape="hexagon" title="육각형">⬡</div>
                    </div>
                </div>
                <button class="tool-btn" data-tool="line" title="선 (4)">
                    <span>╱</span> 선
                </button>
                <button class="tool-btn" data-tool="delete" title="삭제 (5)">
                    <span>✕</span> 삭제
                </button>
            </div>
            
            <div class="toolbar-divider"></div>
            
            <div class="tool-group">
                <button class="tool-btn" id="btn-bring-front" title="맨 앞으로">↑ 앞</button>
                <button class="tool-btn" id="btn-send-back" title="맨 뒤로">↓ 뒤</button>
            </div>
            
            <div class="toolbar-divider"></div>
            
            <div class="tool-group">
                <button class="tool-btn" id="btn-zoom-in" title="확대">🔍+</button>
                <span id="zoom-level" class="zoom-label">100%</span>
                <button class="tool-btn" id="btn-zoom-out" title="축소">🔍-</button>
                <button class="tool-btn" id="btn-zoom-fit" title="화면에 맞추기">맞춤</button>
            </div>
            
            <div class="toolbar-spacer"></div>
            
            <div class="tool-group">
                <button class="tool-btn" id="btn-reanalyze" title="현재 슬라이드 재분석">재분석</button>
                <button class="btn-primary" id="btn-generate">PPT 생성</button>
            </div>
        </div>
        
        <!-- 슬라이드 썸네일 목록 -->
        <div class="slide-nav" id="slide-nav">
            <!-- JS에서 동적 생성 -->
        </div>
        
        <!-- 캔버스 영역 -->
        <div class="canvas-area">
            <!-- 로딩 스피너 -->
            <div class="loading-overlay hidden" id="canvas-loading">
                <div class="spinner"></div>
                <p id="loading-message">로딩 중...</p>
            </div>
            
            <div class="canvas-container" id="canvas-container">
                <canvas id="image-canvas"></canvas>
                <canvas id="overlay-canvas"></canvas>
            </div>
        </div>
        
        <!-- 속성 패널 -->
        <div class="property-panel" id="property-panel">
            <p class="no-selection">요소를 선택하세요</p>
        </div>
        
        <!-- 상태 바 -->
        <div class="status-bar">
            <span id="status-left">슬라이드 -/-</span>
            <span id="status-mid">요소 0개</span>
            <span id="status-right">저장됨</span>
        </div>
    </div>
    
    <script src="/static/js/api-client.js"></script>
    <script src="/static/js/canvas-renderer.js"></script>
    <script src="/static/js/element-tools.js"></script>
    <script src="/static/js/property-panel.js"></script>
    <script src="/static/js/slide-nav.js"></script>
    <script src="/static/js/editor-app.js"></script>
</body>
</html>
```

---

## 18. index.html 전체 HTML

```html
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PDF → PPT Converter</title>
    <link rel="stylesheet" href="/static/css/common.css">
    <link rel="stylesheet" href="/static/css/index.css">
</head>
<body>
    <div class="page-container">
        <header class="page-header">
            <h1>PDF → PPT Converter</h1>
            <p class="subtitle">NotebookLM 슬라이드를 편집 가능한 PowerPoint로 변환</p>
        </header>
        
        <main>
            <div id="upload-area" class="upload-zone">
                <div class="upload-icon">
                    <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                        <line x1="12" y1="18" x2="12" y2="12"/>
                        <polyline points="9 15 12 12 15 15"/>
                    </svg>
                </div>
                <h2>PDF 파일을 여기에 드래그하거나</h2>
                <button id="btn-browse" class="btn-primary">파일 선택</button>
                <input type="file" id="file-input" accept=".pdf" hidden>
                <p class="upload-hint">최대 50MB, 50페이지 이하의 PDF</p>
            </div>
            
            <div id="progress-area" class="hidden">
                <div class="progress-card">
                    <h2 id="progress-title">분석 준비 중...</h2>
                    <div class="progress-bar">
                        <div id="progress-fill" class="progress-fill"></div>
                    </div>
                    <p id="progress-message">PDF를 업로드하는 중입니다...</p>
                    <p id="progress-detail" class="text-muted">Claude Vision AI가 각 슬라이드의 요소를 분석합니다</p>
                </div>
            </div>
            
            <div id="error-area" class="hidden">
                <div class="error-card">
                    <h3>오류 발생</h3>
                    <p id="error-message"></p>
                    <button id="btn-retry" class="btn-primary">다시 시도</button>
                </div>
            </div>
        </main>
        
        <footer class="page-footer">
            <p>Powered by Claude Vision API + python-pptx</p>
        </footer>
    </div>
    
    <script src="/static/js/upload.js"></script>
</body>
</html>
```

---

## 19. 추가 편집기 기능 상세

### 19.1 요소 복사/붙여넣기

```javascript
// editor-app.js에 추가

let clipboard = null; // 복사된 요소

function copySelectedElement() {
    if (!AppState.selectedElement) return;
    clipboard = JSON.parse(JSON.stringify(AppState.selectedElement));
    updateStatusBar('요소 복사됨');
}

function pasteElement() {
    if (!clipboard || !AppState.currentSlide) return;
    
    const newElem = JSON.parse(JSON.stringify(clipboard));
    newElem.id = generateId();
    newElem.name = newElem.name + '_copy';
    newElem.source = 'manual';
    // 약간 오프셋하여 붙여넣기 (겹치지 않도록)
    newElem.bbox.x_pct = Math.min(95, newElem.bbox.x_pct + 2);
    newElem.bbox.y_pct = Math.min(95, newElem.bbox.y_pct + 2);
    newElem.z_order = Math.max(...AppState.currentSlide.elements.map(e => e.z_order), 0) + 1;
    
    AppState.currentSlide.elements.push(newElem);
    AppState.selectedElementId = newElem.id;
    AppState.isDirty = true;
    refreshCanvas();
    updateStatusBar('요소 붙여넣기 완료');
}

// 키보드 단축키에 추가:
// Ctrl+C → copySelectedElement()
// Ctrl+V → pasteElement()
// Ctrl+D → 복제 (복사+붙여넣기 한번에)
```

### 19.2 요소 z-order 제어

```javascript
function bringToFront() {
    if (!AppState.selectedElement || !AppState.currentSlide) return;
    const maxZ = Math.max(...AppState.currentSlide.elements.map(e => e.z_order));
    AppState.selectedElement.z_order = maxZ + 1;
    AppState.isDirty = true;
    refreshCanvas();
}

function sendToBack() {
    if (!AppState.selectedElement || !AppState.currentSlide) return;
    const minZ = Math.min(...AppState.currentSlide.elements.map(e => e.z_order));
    AppState.selectedElement.z_order = Math.max(0, minZ - 1);
    AppState.isDirty = true;
    refreshCanvas();
}

function bringForward() {
    if (!AppState.selectedElement || !AppState.currentSlide) return;
    AppState.selectedElement.z_order += 1;
    AppState.isDirty = true;
    refreshCanvas();
}

function sendBackward() {
    if (!AppState.selectedElement || !AppState.currentSlide) return;
    AppState.selectedElement.z_order = Math.max(0, AppState.selectedElement.z_order - 1);
    AppState.isDirty = true;
    refreshCanvas();
}

// 도구 모음 버튼 바인딩
document.getElementById('btn-bring-front').addEventListener('click', bringToFront);
document.getElementById('btn-send-back').addEventListener('click', sendToBack);
```

### 19.3 Canvas 줌 제어

```javascript
let currentZoom = 1.0;
const ZOOM_STEP = 0.1;
const ZOOM_MIN = 0.3;
const ZOOM_MAX = 3.0;

function setZoom(level) {
    currentZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, level));
    const container = document.getElementById('canvas-container');
    container.style.transform = `scale(${currentZoom})`;
    document.getElementById('zoom-level').textContent = `${Math.round(currentZoom * 100)}%`;
}

function zoomIn() { setZoom(currentZoom + ZOOM_STEP); }
function zoomOut() { setZoom(currentZoom - ZOOM_STEP); }
function zoomFit() { setZoom(1.0); }

document.getElementById('btn-zoom-in').addEventListener('click', zoomIn);
document.getElementById('btn-zoom-out').addEventListener('click', zoomOut);
document.getElementById('btn-zoom-fit').addEventListener('click', zoomFit);

// 마우스 휠 줌 (Ctrl + 스크롤)
document.querySelector('.canvas-area').addEventListener('wheel', (e) => {
    if (e.ctrlKey) {
        e.preventDefault();
        if (e.deltaY < 0) zoomIn();
        else zoomOut();
    }
});
```

### 19.4 로딩 스피너 표시/숨기기

```javascript
function showCanvasLoading(message = '로딩 중...') {
    const overlay = document.getElementById('canvas-loading');
    overlay.classList.remove('hidden');
    document.getElementById('loading-message').textContent = message;
}

function hideCanvasLoading() {
    document.getElementById('canvas-loading').classList.add('hidden');
}

// 사용: OCR 호출 시
async function _createTextElementWithOcr(bbox) {
    showCanvasLoading('텍스트 추출 중...');
    try {
        const result = await ApiClient.ocrRegion(...);
        // ...
    } finally {
        hideCanvasLoading();
    }
}
```

### 19.5 도형 드롭다운 메뉴

```javascript
// 도형 도구 버튼 → 드롭다운 토글
document.getElementById('btn-shape-tool').addEventListener('click', (e) => {
    const dropdown = document.getElementById('shape-dropdown');
    dropdown.classList.toggle('hidden');
    
    // 도구도 활성화
    AppState.currentTool = 'shape';
    document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active'));
    e.currentTarget.classList.add('active');
});

// 도형 종류 선택
document.querySelectorAll('.shape-option').forEach(opt => {
    opt.addEventListener('click', () => {
        AppState.shapeSubTool = opt.dataset.shape;
        document.getElementById('shape-dropdown').classList.add('hidden');
    });
});

// 다른 곳 클릭 시 드롭다운 닫기
document.addEventListener('click', (e) => {
    if (!e.target.closest('.tool-btn-wrapper')) {
        document.getElementById('shape-dropdown').classList.add('hidden');
    }
});
```

---

## 20. 프로젝트 설정 파일들

### 20.1 `.gitignore`

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
*.egg
.venv/
venv/
env/

# 환경변수
.env

# 출력물
output/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db
desktop.ini

# 테스트
.pytest_cache/
htmlcov/
.coverage
```

### 20.2 `requirements.txt`

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
python-multipart==0.0.18
python-pptx==1.0.2
anthropic==0.42.0
PyMuPDF==1.25.3
Pillow==11.1.0
pydantic==2.10.4
pydantic-settings==2.7.1
python-dotenv==1.0.1
jinja2==3.1.5

# 테스트
pytest==8.3.4
pytest-asyncio==0.24.0
httpx==0.28.1
```

### 20.3 `.env.example`

```
# Anthropic API Key (필수)
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here

# Claude 모델 (선택, 기본값: claude-sonnet-4-20250514)
# CLAUDE_MODEL=claude-sonnet-4-20250514

# 서버 설정 (선택)
# HOST=0.0.0.0
# PORT=8000
# DEBUG=true

# 제한 설정 (선택)
# MAX_PDF_SIZE_MB=50
# MAX_SLIDES=50
# RENDER_DPI=200
```

---

## 21. 테스트 코드

### 21.1 `tests/fixtures/sample_vision_response.json`

```json
{
  "background_color_hex": "#FFFFFF",
  "elements": [
    {
      "element_type": "text_box",
      "bbox": { "x_pct": 5.0, "y_pct": 3.0, "width_pct": 90.0, "height_pct": 12.0 },
      "paragraphs": [
        {
          "runs": [
            { "text": "인공지능의 미래", "font": { "family": "Google Sans", "size_pt": 32.0, "bold": true, "italic": false, "underline": false, "color_hex": "#1A237E" } }
          ],
          "alignment": "center", "bullet": false, "bullet_char": null, "indent_level": 0, "line_spacing": 1.2
        }
      ],
      "shape_kind": null, "fill_color_hex": null, "fill_opacity": 1.0,
      "border_color_hex": null, "border_width_pt": null,
      "rotation": 0, "z_order": 2, "opacity": 1.0, "name": "slide_title"
    },
    {
      "element_type": "shape",
      "bbox": { "x_pct": 8.0, "y_pct": 20.0, "width_pct": 84.0, "height_pct": 55.0 },
      "paragraphs": [
        {
          "runs": [
            { "text": "AI 기술은 다양한 분야에서 혁신을 이끌고 있습니다.", "font": { "family": "Google Sans", "size_pt": 18.0, "bold": false, "italic": false, "underline": false, "color_hex": "#333333" } }
          ],
          "alignment": "left", "bullet": true, "bullet_char": "•", "indent_level": 0, "line_spacing": 1.5
        },
        {
          "runs": [
            { "text": "자연어 처리 (NLP)", "font": { "family": "Google Sans", "size_pt": 16.0, "bold": true, "italic": false, "underline": false, "color_hex": "#333333" } }
          ],
          "alignment": "left", "bullet": true, "bullet_char": "•", "indent_level": 1, "line_spacing": 1.5
        }
      ],
      "shape_kind": "rounded_rectangle",
      "fill_color_hex": "#E8EAF6", "fill_opacity": 0.9,
      "border_color_hex": "#3F51B5", "border_width_pt": 1.5,
      "corner_radius_pct": 8.0,
      "rotation": 0, "z_order": 1, "opacity": 1.0, "name": "content_box"
    },
    {
      "element_type": "line",
      "bbox": { "x_pct": 5.0, "y_pct": 17.0, "width_pct": 90.0, "height_pct": 0.5 },
      "paragraphs": [],
      "line_color_hex": "#3F51B5", "line_width_pt": 2.0,
      "line_style": "solid", "start_arrow": "none", "end_arrow": "none",
      "rotation": 0, "z_order": 1, "opacity": 1.0, "name": "divider_line"
    },
    {
      "element_type": "image",
      "bbox": { "x_pct": 70.0, "y_pct": 78.0, "width_pct": 25.0, "height_pct": 18.0 },
      "paragraphs": [],
      "image_description": "brain-shaped neural network illustration with blue nodes",
      "image_placeholder_color": "#BBDEFB",
      "rotation": 0, "z_order": 3, "opacity": 1.0, "name": "ai_illustration"
    }
  ]
}
```

### 21.2 `tests/test_models.py`

```python
import pytest
from app.models import (
    BoundingBox, SlideElement, SlideData, PresentationData,
    ElementType, ShapeKind, FontInfo, TextRun, TextParagraph
)

def test_bounding_box_valid():
    bb = BoundingBox(x_pct=10, y_pct=20, width_pct=30, height_pct=40)
    assert bb.x_pct == 10
    assert bb.width_pct == 30

def test_bounding_box_clamped():
    with pytest.raises(Exception):
        BoundingBox(x_pct=-1, y_pct=20, width_pct=30, height_pct=40)
    with pytest.raises(Exception):
        BoundingBox(x_pct=10, y_pct=20, width_pct=0, height_pct=40)

def test_slide_element_defaults():
    elem = SlideElement(
        element_type=ElementType.TEXT_BOX,
        bbox=BoundingBox(x_pct=0, y_pct=0, width_pct=50, height_pct=10),
    )
    assert elem.source == "auto"
    assert elem.z_order == 0
    assert elem.rotation == 0.0
    assert len(elem.id) == 8

def test_font_info_color_validation():
    font = FontInfo(color_hex="#FF0000")
    assert font.color_hex == "#FF0000"
    
    with pytest.raises(Exception):
        FontInfo(color_hex="not-a-color")

def test_slide_data_serialization():
    slide = SlideData(
        slide_number=1,
        background_color_hex="#FFFFFF",
        elements=[
            SlideElement(
                element_type=ElementType.TEXT_BOX,
                bbox=BoundingBox(x_pct=10, y_pct=10, width_pct=80, height_pct=10),
                paragraphs=[
                    TextParagraph(runs=[TextRun(text="Hello")])
                ],
            )
        ],
    )
    json_str = slide.model_dump_json()
    loaded = SlideData.model_validate_json(json_str)
    assert loaded.elements[0].paragraphs[0].runs[0].text == "Hello"

def test_presentation_data_defaults():
    prs = PresentationData()
    assert prs.slide_width_inches == 13.333
    assert prs.slide_height_inches == 7.5
    assert prs.slides == []
```

### 21.3 `tests/test_pptx_builder.py`

```python
import pytest, json, io
from pathlib import Path
from pptx import Presentation

from app.models import PresentationData
from app.pptx_builder import build_presentation

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_vision_response.json"

@pytest.fixture
def sample_presentation():
    with open(FIXTURE_PATH) as f:
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
    sample_presentation.slides.append(sample_presentation.slides[0].model_copy(update={"slide_number": 2}))
    sample_presentation.slides.append(sample_presentation.slides[0].model_copy(update={"slide_number": 3}))
    
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
```

### 21.4 `tests/test_pdf_to_images.py`

```python
import pytest
from app.pdf_to_images import pdf_to_images, validate_pdf, PDFProcessingError

# 최소한의 빈 PDF (1페이지)
MINIMAL_PDF = (
    b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
)

def test_validate_pdf_valid():
    count = validate_pdf(MINIMAL_PDF)
    assert count == 1

def test_validate_pdf_invalid():
    with pytest.raises(PDFProcessingError, match="열 수 없습니다"):
        validate_pdf(b"not a pdf")

def test_validate_pdf_empty():
    with pytest.raises(PDFProcessingError):
        validate_pdf(b"")

def test_pdf_to_images_returns_pngs():
    images = pdf_to_images(MINIMAL_PDF, dpi=72)
    assert len(images) == 1
    assert images[0][:4] == b'\x89PNG'  # PNG magic bytes
```

### 21.5 `tests/test_region_ocr.py`

```python
import pytest
from PIL import Image
import io

from app.models import BoundingBox
from app.region_ocr import crop_image

def _create_test_image(width=800, height=450):
    img = Image.new('RGB', (width, height), color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()

def test_crop_image_basic():
    img_bytes = _create_test_image(800, 450)
    region = BoundingBox(x_pct=10, y_pct=20, width_pct=30, height_pct=40)
    
    cropped = crop_image(img_bytes, region)
    
    # 결과가 유효한 PNG인지 확인
    assert cropped[:4] == b'\x89PNG'
    
    # 크기 확인
    result_img = Image.open(io.BytesIO(cropped))
    expected_w = int(0.30 * 800)  # 240
    expected_h = int(0.40 * 450)  # 180
    assert abs(result_img.width - expected_w) <= 1
    assert abs(result_img.height - expected_h) <= 1

def test_crop_image_boundary_clamping():
    img_bytes = _create_test_image(800, 450)
    # 경계를 넘는 영역
    region = BoundingBox(x_pct=80, y_pct=80, width_pct=30, height_pct=30)
    
    cropped = crop_image(img_bytes, region)
    result_img = Image.open(io.BytesIO(cropped))
    # 이미지 크기를 넘지 않아야 함
    assert result_img.width > 0
    assert result_img.height > 0

def test_crop_image_full_slide():
    img_bytes = _create_test_image(800, 450)
    region = BoundingBox(x_pct=0, y_pct=0, width_pct=100, height_pct=100)
    
    cropped = crop_image(img_bytes, region)
    result_img = Image.open(io.BytesIO(cropped))
    assert result_img.width == 800
    assert result_img.height == 450
```

---

## 22. Vision Analyzer — JSON 복구 로직

```python
# app/vision_analyzer.py 내부

import re

def _repair_json(raw_text: str) -> str | None:
    """Claude 응답에서 깨진 JSON을 최대한 복구 시도.
    
    흔한 문제:
    1. 마크다운 코드 펜스 감싸기
    2. 마지막 쉼표 (trailing comma)
    3. 주석 포함
    4. 불완전한 JSON (토큰 제한으로 잘림)
    """
    text = raw_text.strip()
    
    # 1. 코드 펜스 제거
    if text.startswith("```"):
        # ```json\n...\n``` 또는 ```\n...\n```
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    
    # 2. trailing comma 제거: ,} → } 또는 ,] → ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    
    # 3. 한줄 주석 제거: // ...
    text = re.sub(r'//[^\n]*', '', text)
    
    # 4. 잘린 JSON 복구 시도: 열린 브래킷 카운트
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')
    
    if open_braces > 0 or open_brackets > 0:
        # 마지막 완전한 요소까지 자르기
        # elements 배열 안에서 마지막 완전한 } 찾기
        last_complete = text.rfind('}')
        if last_complete > 0:
            text = text[:last_complete + 1]
            # 남은 열린 브래킷 닫기
            open_braces = text.count('{') - text.count('}')
            open_brackets = text.count('[') - text.count(']')
            text += ']' * max(0, open_brackets)
            text += '}' * max(0, open_braces)
    
    # 유효한 JSON인지 최종 확인
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        return None

async def analyze_slide(image_bytes: bytes, slide_number: int) -> SlideData:
    """§3.4의 확장 버전 — JSON 복구 로직 포함"""
    client = anthropic.AsyncAnthropic()
    image_b64 = base64.standard_b64encode(image_bytes).decode()
    
    last_error = None
    
    for attempt in range(3):
        try:
            message = await client.messages.create(
                model=settings.claude_model,
                max_tokens=settings.claude_max_tokens,
                system=FULL_ANALYSIS_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64
                        }},
                        {"type": "text", "text": FULL_ANALYSIS_USER_PROMPT}
                    ]
                }]
            )
            
            raw = message.content[0].text
            
            # JSON 파싱 시도
            try:
                parsed = json.loads(raw.strip())
            except json.JSONDecodeError:
                # 복구 시도
                repaired = _repair_json(raw)
                if repaired:
                    parsed = json.loads(repaired)
                    logger.info(f"Slide {slide_number}: JSON 복구 성공")
                else:
                    raise json.JSONDecodeError("복구 실패", raw, 0)
            
            # Pydantic 검증 — 개별 요소 레벨에서 부분 복구
            valid_elements = []
            for elem_dict in parsed.get("elements", []):
                try:
                    elem = SlideElement(**elem_dict)
                    valid_elements.append(elem)
                except Exception as e:
                    logger.warning(f"Slide {slide_number}: 요소 무시 — {e}")
            
            slide = SlideData(
                slide_number=slide_number,
                background_color_hex=parsed.get("background_color_hex", "#FFFFFF"),
                elements=valid_elements,
            )
            
            logger.info(f"Slide {slide_number}: {len(valid_elements)} elements extracted")
            return slide
            
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"Slide {slide_number}: JSON 파싱 실패 (시도 {attempt + 1}/3)")
            if attempt < 2:
                await asyncio.sleep(1)
            
        except anthropic.RateLimitError:
            logger.warning(f"Rate limit hit, waiting {2 ** attempt}s...")
            await asyncio.sleep(2 ** attempt)
            
        except anthropic.APIStatusError as e:
            if e.status_code == 401:
                raise  # 인증 실패는 즉시 중단
            last_error = e
            logger.warning(f"API error: {e}")
            await asyncio.sleep(2 ** attempt)
    
    # 3회 모두 실패
    logger.error(f"Slide {slide_number}: 모든 시도 실패 — {last_error}")
    return SlideData(slide_number=slide_number, elements=[])
```

---

## 23. 편집기 CSS — 추가 컴포넌트

```css
/* 로딩 스피너 */
.loading-overlay {
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(255,255,255,0.85);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    z-index: 100;
}
.spinner {
    width: 40px; height: 40px;
    border: 4px solid var(--color-border);
    border-top-color: var(--color-primary);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* 다이얼로그 */
.dialog-overlay {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    animation: fadeIn 0.15s ease;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

.dialog {
    background: white;
    border-radius: var(--radius-lg);
    padding: var(--spacing-xl);
    max-width: 400px;
    width: 90%;
    box-shadow: var(--shadow-lg);
}
.dialog-wide { max-width: 600px; }
.dialog-title { margin-bottom: var(--spacing-md); }
.dialog-message { margin-bottom: var(--spacing-lg); color: var(--color-text-secondary); }
.dialog-buttons { display: flex; gap: var(--spacing-sm); justify-content: flex-end; }
.dialog-textarea {
    width: 100%;
    padding: var(--spacing-sm);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    font-family: var(--font-mono);
    font-size: 13px;
    resize: vertical;
    margin-bottom: var(--spacing-sm);
}

/* 도구 모음 구분선 */
.toolbar-divider {
    width: 1px;
    height: 24px;
    background: var(--color-border);
    margin: 0 var(--spacing-sm);
}
.toolbar-spacer { flex: 1; }

/* 줌 레벨 표시 */
.zoom-label {
    font-size: 12px;
    color: var(--color-text-secondary);
    min-width: 40px;
    text-align: center;
}

/* 폰트 컨트롤 */
.font-controls {
    display: flex;
    align-items: center;
    gap: 4px;
    margin-top: 4px;
    flex-wrap: wrap;
}
.font-controls input[type="text"],
.font-controls .font-family {
    width: 80px;
    padding: 2px 4px;
    font-size: 12px;
}
.font-controls .font-size {
    width: 40px;
    padding: 2px 4px;
    font-size: 12px;
}
.font-controls input[type="color"] {
    width: 28px;
    height: 24px;
    padding: 0;
    border: 1px solid var(--color-border);
    cursor: pointer;
}
.toggle-bold, .toggle-italic {
    width: 28px;
    height: 28px;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    background: white;
    cursor: pointer;
    font-weight: normal;
    font-size: 13px;
}
.toggle-bold { font-weight: bold; }
.toggle-italic { font-style: italic; }
.toggle-bold.active, .toggle-italic.active {
    background: var(--color-primary);
    color: white;
    border-color: var(--color-primary);
}

/* 업로드 페이지 추가 스타일 (index.css) */
.page-container {
    max-width: 600px;
    margin: 0 auto;
    padding: var(--spacing-xl);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
}
.page-header { text-align: center; margin-bottom: var(--spacing-xl); }
.page-header h1 { font-size: 28px; margin-bottom: var(--spacing-sm); }
.subtitle { color: var(--color-text-secondary); }

.upload-zone {
    width: 100%;
    padding: var(--spacing-xl) * 2;
    border: 2px dashed var(--color-border);
    border-radius: var(--radius-lg);
    text-align: center;
    transition: all 0.2s;
    cursor: pointer;
}
.upload-zone:hover, .upload-zone.drag-over {
    border-color: var(--color-primary);
    background: var(--color-primary-light);
}
.upload-icon { color: var(--color-text-muted); margin-bottom: var(--spacing-md); }
.upload-hint { color: var(--color-text-muted); font-size: 13px; margin-top: var(--spacing-md); }

.progress-card, .error-card {
    width: 100%;
    padding: var(--spacing-xl);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-md);
    text-align: center;
}
.progress-bar {
    width: 100%;
    height: 8px;
    background: var(--color-border);
    border-radius: 4px;
    overflow: hidden;
    margin: var(--spacing-md) 0;
}
.progress-fill {
    height: 100%;
    background: var(--color-primary);
    border-radius: 4px;
    transition: width 0.3s ease;
    width: 0%;
}
.error-card { border-left: 4px solid var(--color-danger); text-align: left; }

.hidden { display: none !important; }
.text-muted { color: var(--color-text-muted); font-size: 13px; }

.page-footer {
    margin-top: var(--spacing-xl);
    color: var(--color-text-muted);
    font-size: 12px;
}
```

---

## 24. 완전한 키보드 단축키 표

| 단축키 | 동작 | 조건 |
|--------|------|------|
| `1` | 선택 도구 | 입력 필드 미포커스 |
| `2` | 텍스트 도구 | 〃 |
| `3` | 도형 도구 | 〃 |
| `4` | 선 도구 | 〃 |
| `5` | 삭제 도구 | 〃 |
| `Delete` / `Backspace` | 선택된 요소 삭제 | 요소 선택 상태 + 입력 필드 미포커스 |
| `Escape` | 선택 해제 / 도구 → 선택 | 항상 |
| `Ctrl+S` | 서버에 저장 | 항상 |
| `Ctrl+Z` | 실행취소 (Undo) | 항상 |
| `Ctrl+Shift+Z` | 다시실행 (Redo) | 항상 |
| `Ctrl+C` | 선택된 요소 복사 | 요소 선택 상태 |
| `Ctrl+V` | 붙여넣기 | 클립보드에 요소 있음 |
| `Ctrl+D` | 복제 (복사+붙여넣기) | 요소 선택 상태 |
| `←` | 이전 슬라이드 | 입력 필드 미포커스 |
| `→` | 다음 슬라이드 | 〃 |
| `Ctrl+Scroll ↑` | 캔버스 줌 인 | 캔버스 위 |
| `Ctrl+Scroll ↓` | 캔버스 줌 아웃 | 〃 |
| `[` | 맨 뒤로 보내기 | 요소 선택 상태 |
| `]` | 맨 앞으로 가져오기 | 〃 |

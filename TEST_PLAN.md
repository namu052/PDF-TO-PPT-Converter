# PDF-to-PPT Converter — Phase별 구현 & 테스트 계획

## Context
PLAN.md에 정의된 상세 설계를 기반으로, 각 Phase 끝에서 동작을 검증할 수 있도록 구현 순서를 재구성한다.
현재 상태: 프로젝트 디렉토리에 CLAUDE.md, PLAN.md만 존재. 코드 없음.

상세 설계 참조: `PLAN.md` (5,074줄, 24개 섹션)

---

## Phase 1 — 프로젝트 스켈레톤 + 서버 기동

### 목표
FastAPI 서버가 기동되고, 브라우저에서 업로드 페이지가 표시되는 것까지.

### 구현할 파일
1. `.gitignore` — output/, .env, __pycache__, *.pyc, .venv/
2. `requirements.txt` — fastapi, uvicorn, python-multipart, python-pptx, anthropic, PyMuPDF, Pillow, pydantic, pydantic-settings, python-dotenv
3. `.env.example` — ANTHROPIC_API_KEY placeholder
4. `app/__init__.py` — 빈 파일
5. `app/config.py` — Settings 클래스 (PLAN.md §10.2)
6. `app/models.py` — 전체 Pydantic 모델 (PLAN.md §1 전체)
7. `app/color_utils.py` — hex_to_rgb, rgb_to_hex (PLAN.md §10.9)
8. `app/font_utils.py` — FONT_FALLBACK + resolve_font (PLAN.md §6)
9. `app/job_manager.py` — Job CRUD + 파일 I/O (PLAN.md §10.4)
10. `app/routers/__init__.py` — 빈 파일
11. `app/routers/upload.py` — 스텁 (빈 엔드포인트)
12. `app/routers/editor_api.py` — 스텁
13. `app/routers/ocr.py` — 스텁
14. `app/routers/generate.py` — 스텁
15. `app/main.py` — FastAPI 앱 + 라우터 등록 + static 마운트 + CORS (PLAN.md §10.3)
16. `run.py` — uvicorn 진입점 (PLAN.md §10.1)
17. `app/static/css/common.css` — 디자인 시스템 (PLAN.md §11.2 + §23)
18. `app/static/css/index.css` — 업로드 페이지 스타일 (PLAN.md §23 업로드 섹션)
19. `app/static/index.html` — 업로드 페이지 HTML (PLAN.md §18)
20. `app/static/js/upload.js` — UploadManager (폴링까지, PLAN.md §11.1)

### 테스트 방법
```bash
# 1. 가상환경 + 의존성 설치
python -m venv .venv && .venv/Scripts/activate && pip install -r requirements.txt

# 2. 서버 기동
python run.py

# 3. 브라우저 검증
# → http://localhost:8000 접속
# → 업로드 페이지 (드래그앤드롭 영역, "파일 선택" 버튼) 표시 확인
# → 파일 선택 클릭 시 파일 다이얼로그 열림 확인
# → PDF 아닌 파일 드롭 시 에러 토스트 표시 확인

# 4. API 스텁 확인
# → http://localhost:8000/docs 에서 Swagger UI 표시 확인
# → 4개 라우터의 엔드포인트가 목록에 나열됨

# 5. Pydantic 모델 검증 (Python REPL)
python -c "from app.models import SlideElement, ElementType, BoundingBox; e = SlideElement(element_type=ElementType.TEXT_BOX, bbox=BoundingBox(x_pct=10, y_pct=20, width_pct=30, height_pct=40)); print(e.model_dump_json(indent=2))"
```

---

## Phase 2 — PDF → 이미지 변환

### 목표
PDF 파일을 받아서 각 페이지를 PNG 이미지로 변환하고 저장할 수 있는 것까지.

### 구현할 파일
1. `app/pdf_to_images.py` — validate_pdf + pdf_to_images + _create_placeholder_png (PLAN.md §10.5)
2. `tests/__init__.py` — 빈 파일
3. `tests/test_pdf_to_images.py` — 4개 테스트 (PLAN.md §21.4)
4. `tests/test_models.py` — 6개 테스트 (PLAN.md §21.2)

### 테스트 방법
```bash
# 1. pytest 실행
pytest tests/test_pdf_to_images.py tests/test_models.py -v

# 2. 수동 검증 — 실제 PDF 파일로 테스트
python -c "
from app.pdf_to_images import pdf_to_images, validate_pdf
pdf = open('테스트파일.pdf', 'rb').read()
count = validate_pdf(pdf)
print(f'페이지 수: {count}')
images = pdf_to_images(pdf)
print(f'이미지 수: {len(images)}')
for i, img in enumerate(images):
    open(f'test_slide_{i+1}.png', 'wb').write(img)
    print(f'slide_{i+1}.png: {len(img)} bytes')
"
# → 각 PNG 파일을 열어 슬라이드 이미지가 정상적으로 렌더링되었는지 확인
# → test_slide_*.png 파일 삭제
```

---

## Phase 3 — Claude Vision 연동 (자동 추출)

### 목표
슬라이드 이미지를 Claude Vision API에 보내 요소를 JSON으로 추출하는 것까지.

### 전제조건
- `.env` 파일에 `ANTHROPIC_API_KEY` 설정 필요

### 구현할 파일
1. `app/vision_analyzer.py` — 전체 프롬프트 + analyze_slide() + _repair_json() (PLAN.md §3.1, §3.4, §22)
2. `app/region_ocr.py` — crop_image + ocr_region + analyze_region (PLAN.md §10.6, §3.2, §3.3)
3. `tests/test_region_ocr.py` — crop_image 테스트 3개 (PLAN.md §21.5)

### 테스트 방법
```bash
# 1. crop_image 단위 테스트 (API 호출 없음)
pytest tests/test_region_ocr.py -v

# 2. Claude Vision 실제 호출 테스트 (API 키 필요)
python -c "
import asyncio
from app.vision_analyzer import analyze_slide
from app.pdf_to_images import pdf_to_images

pdf = open('테스트파일.pdf', 'rb').read()
images = pdf_to_images(pdf)

async def test():
    slide = await analyze_slide(images[0], slide_number=1)
    print(f'배경색: {slide.background_color_hex}')
    print(f'검출 요소: {len(slide.elements)}개')
    for e in slide.elements:
        text = ' '.join(r.text for p in e.paragraphs for r in p.runs)[:50]
        print(f'  [{e.element_type}] {e.name} — {text}')

asyncio.run(test())
"
# → 요소가 1개 이상 검출되는지 확인
# → 텍스트 내용이 원본 슬라이드와 일치하는지 확인
# → bbox 좌표가 합리적인 범위 (0~100%) 안에 있는지 확인
```

---

## Phase 4 — PPTX 빌더

### 목표
JSON 데이터(SlideElement 리스트)를 받아 실제 PowerPoint 파일을 생성하고 열 수 있는 것까지.

### 구현할 파일
1. `app/pptx_builder.py` — build_presentation + 요소별 빌더 6개 + _apply_paragraphs + XML 헬퍼 (PLAN.md §4 전체 + §10.10)
2. `tests/fixtures/sample_vision_response.json` — 테스트 fixture (PLAN.md §21.1)
3. `tests/test_pptx_builder.py` — 5개 테스트 (PLAN.md §21.3)

### 테스트 방법
```bash
# 1. pytest 실행
pytest tests/test_pptx_builder.py -v

# 2. fixture 기반 PPTX 생성 → 수동 확인
python -c "
import json
from app.models import PresentationData
from app.pptx_builder import build_presentation

with open('tests/fixtures/sample_vision_response.json') as f:
    data = json.load(f)

prs = PresentationData(slides=[{'slide_number': 1, **data}])
result = build_presentation(prs)
open('test_output.pptx', 'wb').write(result)
print(f'PPTX 생성 완료: {len(result)} bytes')
"
# → test_output.pptx를 PowerPoint 또는 Google Slides에서 열기
# → "인공지능의 미래" 제목 텍스트가 있는지 확인
# → 둥근 사각형 도형이 파란 테두리로 렌더링되는지 확인
# → 구분선(line)이 있는지 확인
# → 이미지 placeholder가 회색 사각형 + 설명 텍스트인지 확인
# → 각 요소가 개별 클릭 가능한지 확인 (그룹화 안 됨)
# → test_output.pptx 삭제

# 3. Phase 3 결과를 입력으로 E2E 테스트 (선택)
python -c "
import asyncio, json
from app.vision_analyzer import analyze_slide
from app.pdf_to_images import pdf_to_images
from app.models import PresentationData
from app.pptx_builder import build_presentation

pdf = open('테스트파일.pdf', 'rb').read()
images = pdf_to_images(pdf)

async def test():
    slides = []
    for i, img in enumerate(images[:2], 1):  # 처음 2장만
        slide = await analyze_slide(img, i)
        slides.append(slide)
    prs = PresentationData(slides=slides)
    result = build_presentation(prs)
    open('e2e_test.pptx', 'wb').write(result)
    print(f'E2E PPTX: {len(result)} bytes, {len(slides)} slides')

asyncio.run(test())
"
# → e2e_test.pptx를 PowerPoint에서 열어 원본 PDF와 비교
```

---

## Phase 5 — 파이프라인 + 업로드/상태/생성/다운로드 API

### 목표
PDF 업로드 → 자동 추출 → PPTX 다운로드까지의 전체 백엔드 흐름이 API로 동작하는 것까지. (편집기 없이, curl/브라우저로 테스트)

### 구현할 파일
1. `app/pipeline.py` — extract_all_slides 오케스트레이터 (PLAN.md §10.7)
2. `app/routers/upload.py` — POST /api/convert + GET /api/status (스텁 → 실제 구현, PLAN.md §10.8)
3. `app/routers/generate.py` — POST /api/generate + GET /api/download (PLAN.md §15.3)
4. `app/static/js/upload.js` 수정 — 자동 추출 완료 시 editor.html 리다이렉트 (임시로 alert)

### 테스트 방법
```bash
# 1. 서버 기동
python run.py

# 2. PDF 업로드 (curl 또는 브라우저)
curl -X POST http://localhost:8000/api/convert \
  -F "file=@테스트파일.pdf" 
# → {"job_id": "abc123...", "total_slides": N} 응답 확인

# 3. 진행률 폴링
curl http://localhost:8000/api/status/abc123...
# → status가 "extracting" → "editing"으로 변하는지 반복 확인
# → current_slide가 1, 2, ... N으로 증가하는지 확인
# → message가 "슬라이드 N/M 분석 중..." 형태인지 확인

# 4. PPTX 생성
curl -X POST http://localhost:8000/api/generate/abc123...
# → {"ready": true, "slides_count": N, ...} 응답 확인

# 5. 다운로드
curl -o result.pptx http://localhost:8000/api/download/abc123...
# → result.pptx를 PowerPoint에서 열기 성공 확인

# 6. 브라우저 E2E
# → http://localhost:8000 에서 PDF 업로드
# → 진행률 바가 증가하는지 확인
# → 완료 시 편집기 페이지로 리다이렉트 (아직 편집기 미구현이므로 alert 또는 빈 페이지)

# 7. 에러 케이스
curl -X POST http://localhost:8000/api/convert -F "file=@image.png"
# → 400 "PDF 파일만 업로드 가능합니다." 확인

curl http://localhost:8000/api/status/nonexistent
# → 404 확인
```

---

## Phase 6 — 편집기 API (백엔드)

### 목표
편집기 프론트엔드가 사용할 모든 백엔드 API가 동작하는 것까지.

### 구현할 파일
1. `app/routers/editor_api.py` — GET/PUT /api/slides, GET /api/slide-image, PUT/DELETE /api/element (스텁 → 실제 구현, PLAN.md §15.1)
2. `app/routers/ocr.py` — POST /api/ocr-region, POST /api/analyze-region, POST /api/reanalyze (스텁 → 실제 구현, PLAN.md §15.2)

### 테스트 방법 (Phase 5에서 생성된 job_id 사용)
```bash
# 1. 슬라이드 데이터 조회
curl http://localhost:8000/api/slides/{job_id} | python -m json.tool
# → PresentationData JSON 전체가 반환되는지 확인
# → slides 배열에 각 슬라이드의 elements가 포함되는지 확인

# 2. 슬라이드 이미지 조회
curl -o slide1.png http://localhost:8000/api/slide-image/{job_id}/1
# → PNG 이미지가 정상 다운로드되는지 확인

# 3. 슬라이드 데이터 수정 (PUT)
# 기존 데이터를 가져와서 요소 하나의 이름을 변경 후 저장
curl -X PUT http://localhost:8000/api/slides/{job_id} \
  -H "Content-Type: application/json" \
  -d @modified_slides.json
# → {"saved": true} 확인

# 4. 단일 요소 삭제
curl -X DELETE http://localhost:8000/api/element/{job_id}/1/{element_id}
# → {"deleted": true} 확인
# → GET /api/slides에서 해당 요소가 사라졌는지 확인

# 5. OCR 테스트 (API 키 필요)
curl -X POST http://localhost:8000/api/ocr-region \
  -H "Content-Type: application/json" \
  -d '{"job_id": "{job_id}", "slide_num": 1, "region": {"x_pct": 5, "y_pct": 3, "width_pct": 90, "height_pct": 12}}'
# → {"paragraphs": [...], "confidence": "high"} 형태 확인
# → 추출된 텍스트가 실제 슬라이드 내용과 일치하는지 확인
```

---

## Phase 7 — 편집기 프론트엔드 (기본 뷰)

### 목표
편집기 페이지에서 슬라이드 이미지 + 요소 오버레이가 표시되고, 슬라이드 간 전환이 가능한 것까지.

### 구현할 파일
1. `app/static/css/editor.css` — 편집기 레이아웃 전체 (PLAN.md §11.3 + §23)
2. `app/static/editor.html` — HTML 골격 (PLAN.md §17)
3. `app/static/js/api-client.js` — ApiClient 전체 (PLAN.md §11.4)
4. `app/static/js/canvas-renderer.js` — CanvasRenderer 클래스 (PLAN.md §5.3 + §16.1)
5. `app/static/js/slide-nav.js` — SlideNavigator (PLAN.md §5.6 + §16.4)
6. `app/static/js/property-panel.js` — PropertyPanel 기본 구조 (PLAN.md §5.5, 표시만 — 양방향 바인딩은 Phase 8)
7. `app/static/js/element-tools.js` — 스텁 (빈 클래스, Phase 8에서 구현)
8. `app/static/js/editor-app.js` — AppState + initEditor + loadCurrentSlide (PLAN.md §11.7, 도구 이벤트 제외)
9. `app/static/js/upload.js` 수정 — 완료 시 `/editor?job_id=...`로 리다이렉트

### 테스트 방법
```
# 1. 서버 기동 → http://localhost:8000 에서 PDF 업로드
# 2. 자동 추출 완료 후 편집기 페이지로 자동 이동 확인

# 편집기 화면 검증:
# → [레이아웃] 3열 그리드 (좌: 썸네일, 중: 캔버스, 우: 속성패널) 표시
# → [캔버스] 첫 슬라이드 이미지가 16:9 비율로 중앙에 표시
# → [오버레이] 자동 추출된 요소의 바운딩 박스가 반투명 색상으로 오버레이
#    - text_box: 파란색, shape: 초록색, line: 빨간색, image: 보라색
#    - 각 박스 위에 요소 이름 라벨 표시
# → [썸네일] 좌측에 슬라이드 목록이 번호와 함께 표시
#    - 현재 슬라이드에 파란 테두리
#    - 각 썸네일 아래 "N개 요소" 표시
# → [슬라이드 전환] 썸네일 클릭 시 해당 슬라이드로 전환
#    - 캔버스 이미지 + 오버레이가 갱신됨
#    - 좌/우 화살표 키로도 전환 가능
# → [상태바] 하단에 "슬라이드 1/N | 요소 M개 (자동 M, 수동 0) | 저장됨" 표시
# → [속성패널] 요소 미선택 시 "요소를 선택하세요" 표시
# → [도구모음] 상단에 버튼들 표시 (아직 동작하지 않아도 됨)
```

---

## Phase 8 — 편집기 인터랙션 (선택/이동/리사이즈/그리기)

### 목표
요소 선택, 이동, 리사이즈, 새 요소 그리기, 속성 편집이 가능한 완전한 편집기.

### 구현할 파일
1. `app/static/js/element-tools.js` — 전체 구현 (PLAN.md §5.4 + §16.2)
   - 선택 모드: 클릭으로 요소 선택, 핸들 hit-test
   - 이동: 드래그로 요소 이동 (경계 클램핑)
   - 리사이즈: 8방향 핸들 드래그 (최소 2%)
   - 텍스트 도구: 영역 그리기 → OCR 다이얼로그
   - 도형 도구: 영역 그리기 → 도형 생성
   - 선 도구: 시작~끝 선 생성
   - 삭제 도구: 클릭으로 요소 삭제
   - 커서 업데이트: 도구/위치에 따른 커서 변경
2. `app/static/js/property-panel.js` — 양방향 바인딩 완성 (PLAN.md §5.5 + §16.3)
   - 위치/크기 숫자 입력 → 요소 업데이트 → 캔버스 반영
   - 텍스트 편집 (textarea + 폰트 설정)
   - 도형 속성 (색상, 테두리)
   - 선 속성 (색상, 두께, 화살표)
   - 삭제 버튼
3. `app/static/js/editor-app.js` — 추가 기능 (PLAN.md §5.7, §5.8, §11.6, §19)
   - Undo/Redo (UndoManager 클래스)
   - 키보드 단축키 전체
   - 자동 저장 (AutoSaver, 30초)
   - 브라우저 이탈 경고
   - 복사/붙여넣기 (Ctrl+C/V/D)
   - z-order 제어 (앞/뒤 버튼)
   - 줌 제어 (Ctrl+스크롤, 버튼)
   - 도형 드롭다운 메뉴
   - OCR 결과 미리보기 다이얼로그
   - PPT 생성 버튼 → 다운로드

### 테스트 방법
```
# 서버 기동 → PDF 업로드 → 편집기 진입

# [선택/이동/리사이즈]
# → 요소 클릭 → 주황색 테두리 + 8개 흰색 핸들 표시
# → 속성 패널에 선택된 요소 정보 표시 (타입, 이름, 위치, 텍스트 등)
# → 요소 드래그 → 이동 (슬라이드 밖으로 나가지 않음)
# → 핸들 드래그 → 리사이즈 (너무 작아지지 않음, 최소 2%)
# → 빈 영역 클릭 → 선택 해제
# → Escape → 선택 해제

# [속성 패널 편집]
# → 텍스트 요소 선택 → textarea에서 텍스트 수정
# → 폰트 크기 변경 → (PPTX 생성 시 반영됨)
# → 볼드/이탤릭 토글
# → X%, Y%, W%, H% 숫자 변경 → 캔버스에서 즉시 이동/리사이즈
# → 삭제 버튼 → 확인 대화상자 → 요소 삭제

# [새 요소 그리기]
# → 텍스트 도구(2) 선택 → 캔버스에서 영역 드래그
#   → "자동 추출 / 직접 입력" 선택 대화상자
#   → "자동 추출" → OCR 결과 미리보기 → 적용/수정/취소
#   → "직접 입력" → 빈 텍스트박스 생성 → 속성 패널에서 입력
# → 도형 도구(3) → 드롭다운에서 모양 선택 → 영역 드래그 → 도형 생성
# → 선 도구(4) → 시작~끝 드래그 → 선 생성 (화살표 기본 포함)
# → 삭제 도구(5) → 요소 클릭 → 즉시 삭제

# [그리기 미리보기]
# → 텍스트/도형 도구로 드래그 중 → 점선 사각형 + 크기 % 표시
# → 선 도구로 드래그 중 → 점선 직선 표시

# [Undo/Redo]
# → 요소 이동 → Ctrl+Z → 원래 위치로 복원
# → Ctrl+Shift+Z → 다시 이동된 위치

# [자동 저장]
# → 요소 수정 후 30초 대기 → 상태바 "저장 중..." → "저장됨"
# → Ctrl+S → 즉시 저장

# [키보드 단축키]
# → 1~5: 도구 전환, Delete: 요소 삭제, ←/→: 슬라이드 전환

# [PPT 생성]
# → "PPT 생성" 버튼 클릭 → "생성 중..." → 자동 다운로드
# → 다운로드된 PPTX를 PowerPoint에서 열기
# → 수동 추가한 요소가 올바른 위치에 있는지 확인
# → 편집한 텍스트가 반영되었는지 확인

# [복사/붙여넣기]
# → 요소 선택 → Ctrl+C → Ctrl+V → 약간 오프셋된 복사본 생성
# → Ctrl+D → 복제

# [줌]
# → Ctrl+스크롤 → 캔버스 확대/축소
# → 줌 레벨 표시 (100%, 110%, ...)
# → "맞춤" 버튼 → 100%로 복원

# [z-order]
# → "↑ 앞" 버튼 → 선택된 요소가 맨 앞으로
# → "↓ 뒤" 버튼 → 선택된 요소가 맨 뒤로
```

---

## Phase 9 — 마무리 + CLAUDE.md 업데이트

### 목표
에러 핸들링 점검, 보안 검증, CLAUDE.md에 빌드/실행 명령어 문서화.

### 구현할 파일
1. 에러 핸들링 점검 — PLAN.md §7 테이블 기준으로 각 시나리오 확인/보강
   - PDF 업로드: 크기 초과, 페이지 초과, 암호화, 깨진 PDF
   - Claude API: 429 재시도, 401 즉시 중단, JSON 파싱 실패 복구
   - PPTX 빌더: 개별 요소 실패 시 skip + warning
2. 보안 검증 — PLAN.md §12 기준
   - path traversal 방지 (job_id에 `..` 포함 불가)
   - XSS 방지 (innerHTML에 사용자 데이터 직접 넣지 않음)
   - .env가 gitignored인지 확인
3. `CLAUDE.md` 업데이트 — 빌드/실행 명령어, 아키텍처 요약

### 테스트 방법
```bash
# 에러 케이스 수동 테스트:
# → 50MB 초과 PDF 업로드 → 413 에러
# → 비PDF 파일 업로드 → 400 에러
# → 존재하지 않는 job_id → 404 에러
# → 잘못된 Pydantic 데이터 PUT → 400 에러 + 상세 필드 정보

# 전체 E2E 흐름 최종 확인:
# 1. http://localhost:8000 접속
# 2. PDF 드래그앤드롭 업로드
# 3. 진행률 바 → 자동 추출 완료
# 4. 편집기 자동 진입
# 5. 요소 확인/수정/추가/삭제
# 6. PPT 생성 버튼 → 다운로드
# 7. PowerPoint에서 열어 결과 확인
```

---

## 구현 순서 요약

| Phase | 핵심 산출물 | 검증 기준 |
|-------|------------|-----------|
| 1 | 서버 + 업로드 UI | `localhost:8000` 접속, 업로드 폼 표시 |
| 2 | PDF → PNG | pytest 통과, PNG 파일 생성 |
| 3 | Claude Vision → JSON | 요소 JSON 추출 성공 |
| 4 | JSON → PPTX | PowerPoint에서 열기 성공 |
| 5 | 업로드→추출→다운로드 API | curl로 전체 흐름 동작 |
| 6 | 편집기 API | curl로 CRUD 동작 |
| 7 | 편집기 뷰 | 이미지+오버레이+썸네일 표시 |
| 8 | 편집기 인터랙션 | 선택/이동/그리기/OCR/PPT생성 |
| 9 | 마무리 | 에러 핸들링, 보안, 문서화 |

각 Phase는 독립적으로 테스트 가능하며, 이전 Phase가 정상 동작해야 다음으로 진행한다.

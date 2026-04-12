# Parallel Tracks Plan

## Goal
이 문서는 기존 작업 티켓 `T01-T10`을 병렬 작업 가능한 `백엔드`, `프런트`, `테스트` 3개 트랙으로 재편한 실행 계획이다. 목표 아키텍처는 `PDF -> 슬라이드 이미지 -> 웹 편집(가이드) -> Claude Vision 분석 -> 웹 편집(검토/수정) -> PPTX 생성`이며, 핵심은 `manual-first`와 `fidelity-first`다.

## Ownership Rules
- 백엔드 트랙 전용: `app/*.py`, `app/routers/*.py`, 신규 Python 모듈, 세션 저장 구조
- 프런트 트랙 전용: `app/static/**`
- 테스트 트랙 전용: `tests/**`
- 문서 갱신: 최종 통합 웨이브에서만 수행
- 병렬 충돌 방지: `app/models.py`, `app/config.py`, `app/main.py`는 백엔드만 수정한다

## Shared API Contract
프런트와 테스트는 아래 계약을 기준으로 선행 개발 가능하다.

| Endpoint | Purpose | Main Response |
|---|---|---|
| `POST /api/convert` | 업로드 후 rasterize-only 세션 생성 | `job_id`, `total_slides`, `status`, `next_url` |
| `GET /api/status/{job_id}` | 전체 세션 상태 | `status`, `current_slide`, `ready_slides`, `failed_reason` |
| `GET /api/session/{job_id}` | 세션 요약 | 슬라이드별 `guide/review/generate` 상태 |
| `GET /api/slide/{job_id}/{slide_num}` | 슬라이드 상세 | `source_image_url`, `guides`, `latest_revision`, `final_elements`, `locked_ids` |
| `PUT /api/guides/{job_id}/{slide_num}` | 가이드 전체 저장 | `saved`, `guides_count` |
| `POST /api/analyze/{job_id}/{slide_num}` | guided analysis 시작 | `revision_id`, `status=analyzing` |
| `GET /api/analysis/{job_id}/{slide_num}/{revision_id}` | 리비전 조회 | `candidates`, `summary`, `confidence` |
| `POST /api/review/{job_id}/{slide_num}/{revision_id}/apply` | 후보 일부/전체 채택 | `final_elements`, `locked_ids` |
| `POST /api/lock/{job_id}/{slide_num}` | 요소 잠금/해제 | `locked_ids` |
| `POST /api/generate/{job_id}` | 최종 PPT 생성 | `ready`, `filename`, `slides_count` |

## Backend Track

### B1. Session Foundation
- 매핑 티켓: `T01`
- 파일:
  - [app/models.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/models.py:1)
  - [app/config.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/config.py:1)
  - [app/job_manager.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/job_manager.py:1)
  - `app/session_store.py`
- TODO:
  - `JobSession`, `SlideSession`, `GuideAnnotation`, `AnalysisRevision`, `FinalElement`, `RenderAsset` 정의
  - 디스크 기반 `manifest.json` 저장 구조 설계
  - 기존 `job_manager`를 호환 래퍼로 축소
  - 상태 머신을 `editing_guidance`, `editing_review`, `ready_for_generate` 중심으로 재정의
- 선행조건: 없음
- 산출물: 세션/슬라이드 저장 포맷 확정

### B2. Rasterize-Only Upload and Read APIs
- 매핑 티켓: `T02`, `T03`
- 파일:
  - [app/pipeline.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/pipeline.py:1)
  - [app/routers/upload.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/routers/upload.py:1)
  - [app/routers/editor_api.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/routers/editor_api.py:1)
  - [app/main.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/main.py:1)
- TODO:
  - 업로드 후 Claude Vision 자동 실행 제거
  - PNG 렌더링까지만 수행하고 `editing_guidance`로 종료
  - `/api/session/{job_id}`, `/api/slide/{job_id}/{slide_num}`, `/api/source-image/...` 추가
  - 기존 `slides.json` 의존 응답 제거 또는 임시 호환화
- 선행조건: `B1`
- 산출물: 프런트가 새 세션 API만으로 로드 가능

### B3. Guide Persistence and Overlay Composition
- 매핑 티켓: `T05`, `T06` 일부
- 파일:
  - [app/routers/editor_api.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/routers/editor_api.py:1)
  - `app/annotation_composer.py`
  - `app/session_store.py`
- TODO:
  - guides CRUD API 추가
  - 원본 이미지 + guide annotation -> overlay/composite PNG 합성기 설계
  - `guides.json`, `guide_overlay.png` 저장 규칙 확정
- 선행조건: `B2`
- 산출물: 프런트 가이드 편집 저장과 분석 준비 입력 포맷 확정

### B4. Guided Analysis Revision Loop
- 매핑 티켓: `T06`, `T07`, `T08`
- 파일:
  - `app/analysis_pipeline.py`
  - `app/routers/analysis.py`
  - [app/vision_analyzer.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/vision_analyzer.py:1)
  - [app/region_ocr.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/region_ocr.py:1)
  - [app/routers/ocr.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/routers/ocr.py:1)
  - [app/main.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/main.py:1)
- TODO:
  - guided analysis 요청/저장/리비전 조회 API 추가
  - locked final elements를 입력 제약으로 포함
  - 후보 적용, 일부 채택, 잠금/해제 API 추가
  - 부분 OCR과 부분 재분석을 revision 체계에 편입
- 선행조건: `B3`
- 산출물: `guide -> analyze -> apply -> lock -> reanalyze` 루프 완성

### B5. Fidelity-First Generator
- 매핑 티켓: `T09`
- 파일:
  - `app/fidelity_assets.py`
  - [app/pptx_builder.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/pptx_builder.py:1)
  - [app/font_utils.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/font_utils.py:1)
  - [app/color_utils.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/color_utils.py:1)
  - [app/routers/generate.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/routers/generate.py:1)
- TODO:
  - `primitive`, `raster`, `hybrid` 렌더 전략 구현
  - 복잡한 선/도형/텍스트용 crop asset 추출 규칙 정의
  - 생성 시 `final_elements + asset registry`만 사용하게 변경
- 선행조건: `B4`
- 산출물: fidelity-first PPT 생성 경로

### B6. Final Integration and Docs
- 매핑 티켓: `T10` 백엔드 부분
- 파일:
  - [app/routers/upload.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/routers/upload.py:1)
  - [app/routers/editor_api.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/routers/editor_api.py:1)
  - `app/routers/analysis.py`
  - [app/routers/generate.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/routers/generate.py:1)
- TODO:
  - 상태 응답 포맷 통일
  - 생성 게이트 구현
  - legacy `slides.json` 경로 제거 준비
- 선행조건: `B5`
- 산출물: 통합 API 정리 완료

## Frontend Track

### F1. Session-Based Editor Shell
- 매핑 티켓: `T04` 일부
- 파일:
  - [app/static/editor.html](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/editor.html:1)
  - [app/static/js/api-client.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/api-client.js:1)
  - [app/static/js/editor-app.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/editor-app.js:1)
  - [app/static/js/canvas-renderer.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/canvas-renderer.js:1)
  - [app/static/js/slide-nav.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/slide-nav.js:1)
- TODO:
  - `source/guide/candidate/final` 레이어 구조 도입
  - 새 세션 API 기준 초기 로딩 흐름으로 전환
  - 슬라이드별 상태 배지 표시
- 선행조건: API 계약 문서만 있으면 목업으로 시작 가능, 실제 연결은 `B2` 이후
- 산출물: 새 세션 기반 편집기 셸

### F2. Guidance Overlay Tools
- 매핑 티켓: `T05`
- 파일:
  - [app/static/editor.html](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/editor.html:1)
  - [app/static/js/element-tools.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/element-tools.js:1)
  - [app/static/js/property-panel.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/property-panel.js:1)
  - [app/static/js/editor-app.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/editor-app.js:1)
- TODO:
  - guide 도구: 선, 화살표, 사각형, 타원, 메모 텍스트, 영역 박스, 하이라이트
  - `intent_tag`, `note`, 스타일 편집 UI 추가
  - autosave와 dirty state를 guide 기준으로 분리
- 선행조건: `F1`, 실제 저장 연결은 `B3`
- 산출물: 원본 이미지 위 가이드 작성 기능

### F3. Review and Lock UI
- 매핑 티켓: `T07`
- 파일:
  - [app/static/js/canvas-renderer.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/canvas-renderer.js:1)
  - [app/static/js/property-panel.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/property-panel.js:1)
  - [app/static/js/editor-app.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/editor-app.js:1)
- TODO:
  - candidate/final 시각 구분
  - accept/reject/lock/unlock 조작 UI
  - revision 전환과 비교 표시
- 선행조건: `F2`, 실제 연결은 `B4`
- 산출물: 피드백 루프용 검토 UI

### F4. Partial Analyze and OCR UX
- 매핑 티켓: `T08`
- 파일:
  - [app/static/js/element-tools.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/element-tools.js:1)
  - [app/static/js/api-client.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/api-client.js:1)
  - [app/static/js/editor-app.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/editor-app.js:1)
- TODO:
  - 선택영역 OCR
  - 선택영역만 analyze
  - 부분 재분석 후 기존 final 요소 보호
- 선행조건: `F3`, 실제 연결은 `B4`
- 산출물: 부분 분석 UX

### F5. Generate Readiness and Final UX
- 매핑 티켓: `T09`, `T10` 프런트 부분
- 파일:
  - [app/static/editor.html](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/editor.html:1)
  - [app/static/js/editor-app.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/editor-app.js:1)
  - [app/static/js/api-client.js](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/app/static/js/api-client.js:1)
- TODO:
  - 생성 준비 여부 표시
  - 미확정 슬라이드 경고
  - 최종 생성/다운로드 흐름 정리
- 선행조건: `F4`, 실제 연결은 `B5-B6`
- 산출물: end-to-end 사용자 흐름 마감

## Test Track

### Q1. Contract Fixtures and Baseline
- 매핑 티켓: `T01` 테스트 부분
- 파일:
  - [tests/test_models.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/tests/test_models.py:1)
  - `tests/fixtures/session_manifest.json`
  - `tests/fixtures/guide_annotations.json`
- TODO:
  - 새 세션/가이드/revision fixture 작성
  - API 응답 스키마 기준 fixture 확정
- 선행조건: 없음
- 산출물: 프런트와 테스트 공용 fixture

### Q2. Upload and Session API Tests
- 매핑 티켓: `T02`, `T03`
- 파일:
  - [tests/test_api_integration.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/tests/test_api_integration.py:1)
  - [tests/test_pdf_to_images.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/tests/test_pdf_to_images.py:1)
- TODO:
  - rasterize-only 업로드 검증
  - 세션/슬라이드 조회 API 검증
  - source image 저장 경로 검증
- 선행조건: `Q1`, 실제 구현 연결은 `B2`
- 산출물: 업로드/세션 회귀 보호망

### Q3. Guide and Revision Loop Tests
- 매핑 티켓: `T05`, `T06`, `T07`, `T08`
- 파일:
  - `tests/test_annotation_composer.py`
  - `tests/test_analysis_pipeline.py`
  - [tests/test_region_ocr.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/tests/test_region_ocr.py:1)
  - [tests/test_api_integration.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/tests/test_api_integration.py:1)
- TODO:
  - overlay 합성 정확도 검증
  - revision 저장, apply, lock, reanalyze 검증
  - 부분 OCR/부분 재분석 검증
- 선행조건: `Q2`, 실제 구현 연결은 `B3-B4`
- 산출물: 피드백 제어 루프 회귀 테스트

### Q4. Fidelity Builder Tests
- 매핑 티켓: `T09`
- 파일:
  - [tests/test_pptx_builder.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/tests/test_pptx_builder.py:1)
  - `tests/test_fidelity_builder.py`
- TODO:
  - `primitive/raster/hybrid` 경로 검증
  - 복잡 요소가 기본 도형으로 뭉개지지 않는지 검증
  - crop asset 기반 생성 검증
- 선행조건: `Q3`, 실제 구현 연결은 `B5`
- 산출물: 고충실도 생성 회귀 테스트

### Q5. E2E and Docs Sync
- 매핑 티켓: `T10`
- 파일:
  - [tests/test_api_integration.py](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/tests/test_api_integration.py:1)
  - [AGENTS.md](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/AGENTS.md:1)
  - [CLAUDE.md](/C:/Users/PC/vibecoding_master/PRD_ClaudeCode/PDF_TO_PPT_Converter/CLAUDE.md:1)
- TODO:
  - `upload -> guidance -> analyze -> review -> generate` 전체 플로우 검증
  - 문서와 실제 API/상태 머신 동기화 확인
- 선행조건: `Q4`, 실제 구현 연결은 `B6`, `F5`
- 산출물: 최종 통합 승인 기준

## Parallel Execution Waves

### Wave 1
- 백엔드: `B1`
- 프런트: `F1` 목업 상태로 시작
- 테스트: `Q1`

### Wave 2
- 백엔드: `B2`
- 프런트: `F1` 실제 API 연결
- 테스트: `Q2`

### Wave 3
- 백엔드: `B3`
- 프런트: `F2`
- 테스트: `Q3`의 overlay fixture와 CRUD 테스트 선행

### Wave 4
- 백엔드: `B4`
- 프런트: `F3`, `F4`
- 테스트: `Q3` 완료

### Wave 5
- 백엔드: `B5`
- 프런트: `F5`
- 테스트: `Q4`

### Wave 6
- 백엔드: `B6`
- 프런트: `F5` 마감
- 테스트: `Q5`

## Integration Gates
- Gate A: `B2 + F1 + Q2` 완료 시 업로드 후 원본 슬라이드 편집 진입 가능
- Gate B: `B4 + F4 + Q3` 완료 시 `guide -> analyze -> review -> reanalyze` 루프 가능
- Gate C: `B5 + F5 + Q4` 완료 시 fidelity-first PPT 생성 가능
- Gate D: `B6 + Q5` 완료 시 manual-first 전체 플로우 릴리스 가능

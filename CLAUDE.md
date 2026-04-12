# CLAUDE.md

This repository is now organized around a manual-first PDF to PPT workflow.

## Project

Flow:

`PDF -> slide images -> web guidance editing -> Claude Vision analysis -> review/edit -> PPTX generation`

Automatic Vision analysis does not run immediately after upload. The user adds overlays first, then explicitly requests analysis.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Set `ANTHROPIC_API_KEY` in `.env` for guided analysis and OCR features.

## Run

```bash
python run.py
```

Server default: `http://localhost:8000`

## Test

```bash
python -m pytest -q
python -m pytest tests/test_api_integration.py -q
python -m pytest --cov=app --cov-report=term-missing -q
```

Current local baseline: `112 passed`, overall coverage **87%** (global minimum 80%).

Sub-80% modules to watch: `annotation_composer.py` (65%), `job_manager.py` (69%), `pdf_to_images.py` (72%), `routers/analysis.py` (72%).

## Architecture

- `app/main.py`: FastAPI entrypoint, router registration, static file serving
- `app/models.py`: session, guide, revision, final-element, and PPT element models
- `app/session_store.py`: disk-backed session storage under `output/<job_id>/`
- `app/job_manager.py`: legacy-compatible wrapper over the session store
- `app/pipeline.py`: rasterize-only upload pipeline
- `app/annotation_composer.py`: source image + guide overlay composition
- `app/analysis_pipeline.py`: guided analysis orchestration and revision persistence
- `app/vision_analyzer.py`: full-slide and guided Claude Vision calls
- `app/region_ocr.py`: OCR and region-level analysis helpers
- `app/fidelity_assets.py`: crop asset creation for raster/hybrid fallback
- `app/pptx_builder.py`: fidelity-first PPTX builder with primitive/raster/hybrid rendering
- `app/routers/`: upload, editor, analysis, OCR, and generate APIs
- `app/static/`: manual-first UI

## Session Layout

Each job is stored on disk:

- `output/<job_id>/manifest.json`
- `output/<job_id>/slides/<n>/session.json`
- `output/<job_id>/slides/<n>/source.png`
- `output/<job_id>/slides/<n>/guide_overlay.png`
- `output/<job_id>/slides/<n>/revisions/<revision_id>/revision.json`
- `output/<job_id>/assets/*.png`

## Implementation Notes

- Treat guides, candidates, and accepted final elements as separate layers.
- Locked final elements must survive reanalysis.
- Prefer visual fidelity over editability when the two conflict.
- Use raster or hybrid fallback for complex text, shapes, icons, and chart-like content.

## Router Error Handling Convention

All routers follow the pattern established in `app/routers/analysis.py`:

- Validate path params (`job_id`, `revision_id`, `asset_id`, `slide_num`) against a safe-charset regex before touching the filesystem — blocks path traversal.
- Catch `anthropic.APIError` → HTTP 502 with Korean `Claude Vision 호출 실패: {message}` detail and reset the job to `EDITING_GUIDANCE`.
- Catch `ValueError` → HTTP 400.
- Catch generic `Exception` → HTTP 500 with Korean message and `logger.exception`.
- Never leak raw stack traces to clients; the global handler in `main.py` is a last-resort fallback only.

## Security Posture

- `.env` holds `ANTHROPIC_API_KEY`; `.gitignore` excludes it. Rotate the key if the dev machine is shared.
- CORS `allow_origins=*` and `host=0.0.0.0` are dev defaults — tighten before any production deployment.
- Upload enforces `settings.max_slides` (413 if exceeded) and `validate_pdf` via PyMuPDF regardless of declared content-type.
- `StaticFiles` is rooted at `app/static/` only; `output/` and `.env` are never served.

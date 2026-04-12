# Repository Guidelines

## Project Structure & Module Organization
`app/` contains the FastAPI backend. Core modules are `session_store.py` for disk-backed jobs, `pipeline.py` for PDF rasterization, `analysis_pipeline.py` for guide-driven Vision revisions, and `pptx_builder.py` for fidelity-first PPT output. `app/routers/` exposes upload, editor, analysis, OCR, and generate APIs. `app/static/` contains the manual-first UI (`index.html`, `editor.html`, `js/`, `css/`). `tests/` holds pytest coverage. Treat `output/` and generated `.pptx` files as runtime artifacts, not source.

## Build, Test, and Development Commands
Create and activate a virtual environment, then install dependencies:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Run the app locally with `python run.py` or `uvicorn app.main:app --reload`. Use `python -m pytest -q` for the full suite, or scope runs during development, for example `python -m pytest tests/test_api_integration.py -q`.

## Coding Style & Naming Conventions
Follow existing Python style: 4-space indentation, PEP 8 spacing, explicit imports, and type hints on new public functions. Use `snake_case` for modules, functions, and variables; use `PascalCase` for Pydantic models and enums in `app/models.py`. Keep API handlers thin and push reusable logic into `app/` modules instead of route files. For frontend assets under `app/static/`, match the current lowercase, hyphenated file naming pattern such as `canvas-renderer.js`.

## Testing Guidelines
This repo uses `pytest` with `asyncio_mode = strict`. Name files `test_*.py` and test functions `test_*`. Mock Anthropic or Vision calls in tests; do not rely on live API traffic or a real `ANTHROPIC_API_KEY`. Add or extend tests whenever you change session persistence, guide overlays, revision review flow, OCR, or PPTX fidelity fallback.

## Commit & Pull Request Guidelines
The current history uses Conventional Commit style (`docs: add project plan...`); continue with prefixes like `feat:`, `fix:`, `test:`, and `docs:`. Keep commits focused and imperative. PRs should summarize user-visible changes, note config or `.env` updates, list the test command(s) run, and include screenshots when `app/static/` UI behavior changes.

## Security & Configuration Tips
Copy `.env.example` to `.env` and keep secrets local. Never commit API keys, generated slide outputs, or job data from `output/`. Manual-first analysis depends on `ANTHROPIC_API_KEY`; the app should still run without it, but Vision/OCR endpoints will fail.

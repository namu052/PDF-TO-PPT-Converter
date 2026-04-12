from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.job_manager import cleanup_expired_jobs
from app.routers import analysis, diagnostics, editor_api, generate, ocr, upload

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    yield
    cleanup_task.cancel()


async def _periodic_cleanup():
    while True:
        await asyncio.sleep(3600)
        cleanup_expired_jobs(settings.job_ttl_hours)


app = FastAPI(
    title="PDF to PPT Converter",
    version="2.0.0",
    lifespan=lifespan,
)

_DEV_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEV_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api")
app.include_router(editor_api.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(diagnostics.router, prefix="/api")
app.include_router(ocr.router, prefix="/api")
app.include_router(generate.router, prefix="/api")

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def root():
    return FileResponse(
        "app/static/index.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/editor")
async def editor_page():
    return FileResponse(
        "app/static/editor.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "An internal server error occurred."},
    )

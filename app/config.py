from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = Field(default="", repr=False)
    claude_model: str = "claude-sonnet-4-20250514"
    claude_max_tokens: int = 8192

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    max_pdf_size_mb: int = 50
    max_slides: int = 50
    render_dpi: int = 200

    output_dir: Path = Path("./output")
    job_ttl_hours: int = 24
    auto_save_interval_sec: int = 30
    revision_retention: int = 10
    manifest_version: int = 2
    overlay_export_enabled: bool = True
    fidelity_threshold: float = 0.8

    min_region_pct: float = 1.0
    ocr_timeout_sec: int = 15

    default_slide_width: float = 13.333
    default_slide_height: float = 7.5

    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()

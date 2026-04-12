from __future__ import annotations

from pathlib import Path

from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


class Settings(BaseSettings):
    anthropic_api_key: str = Field(default="", repr=False)
    claude_model: str = "claude-haiku-4-5"
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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # This app is developed locally around a repo-scoped `.env`.
        # Prefer it over inherited shell/system variables so stale keys do not
        # silently override the project configuration.
        return init_settings, dotenv_settings, env_settings, file_secret_settings


settings = Settings()

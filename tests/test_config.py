from app.config import Settings


def test_settings_prefer_dotenv_over_environment(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ANTHROPIC_API_KEY=dotenv-key\nCLAUDE_MODEL=claude-haiku-4-5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

    settings = Settings(_env_file=env_file)

    assert settings.anthropic_api_key == "dotenv-key"

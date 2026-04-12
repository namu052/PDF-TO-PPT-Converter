from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app import vision_diagnostics
from app.config import settings
from app.models import VisionServiceStatus


class _FakeClient:
    def __init__(self, create_impl):
        self.messages = self
        self._create_impl = create_impl
        self.closed = False

    async def create(self, **kwargs):
        return await self._create_impl(**kwargs)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_probe_vision_status_reports_missing_api_key(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    snapshot = await vision_diagnostics.probe_vision_status()

    assert snapshot.configured is False
    assert snapshot.can_analyze is False
    assert snapshot.status == VisionServiceStatus.MISSING_API_KEY


@pytest.mark.asyncio
async def test_probe_vision_status_reports_ok(monkeypatch):
    async def _create(**kwargs):
        content = kwargs["messages"][0]["content"]
        assert content[0]["type"] == "image"
        assert content[1]["type"] == "text"
        return SimpleNamespace(content=[SimpleNamespace(text="OK")])

    client = _FakeClient(_create)
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(settings, "claude_model", "claude-haiku-4-5")
    monkeypatch.setattr(vision_diagnostics.anthropic, "AsyncAnthropic", lambda api_key: client)

    snapshot = await vision_diagnostics.probe_vision_status()

    assert snapshot.configured is True
    assert snapshot.can_analyze is True
    assert snapshot.status == VisionServiceStatus.OK
    assert client.closed is True


@pytest.mark.asyncio
async def test_probe_vision_status_reports_billing_block(monkeypatch):
    async def _create(**kwargs):
        raise anthropic.APIStatusError(
            "billing failed",
            response=httpx.Response(
                402,
                request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
            ),
            body={
                "error": {
                    "message": (
                        "Your credit balance is too low to access the Anthropic API. "
                        "Please go to Plans & Billing to upgrade or purchase credits."
                    )
                }
            },
        )

    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(
        vision_diagnostics.anthropic,
        "AsyncAnthropic",
        lambda api_key: _FakeClient(_create),
    )

    snapshot = await vision_diagnostics.probe_vision_status()

    assert snapshot.configured is True
    assert snapshot.can_analyze is False
    assert snapshot.status == VisionServiceStatus.BILLING_BLOCKED
    assert snapshot.status_code == 402
    assert "credit balance is too low" in (snapshot.detail or "")

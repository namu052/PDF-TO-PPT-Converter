from __future__ import annotations

import contextlib

import anthropic

from app.config import settings
from app.models import VisionServiceStatus, VisionStatusSnapshot

_PROBE_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAN0lEQVR4nO3RwQ0AMAjDwJT9d05HMB9+vgGCZF7bXJrT9XhgwR8gEyETIRMhEyETIRMhEyEThXzH8QM9OMM6fAAAAABJRU5ErkJggg=="
)
_PROBE_PROMPT = "Reply with OK."


def _extract_anthropic_message(exc: anthropic.APIError) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return str(exc)


def _status_message(status: VisionServiceStatus) -> str:
    messages = {
        VisionServiceStatus.OK: "Claude Vision is reachable.",
        VisionServiceStatus.MISSING_API_KEY: "ANTHROPIC_API_KEY is not configured.",
        VisionServiceStatus.AUTH_FAILED: "Anthropic rejected the API key.",
        VisionServiceStatus.BILLING_BLOCKED: "Anthropic billing is blocking Claude Vision requests.",
        VisionServiceStatus.RATE_LIMITED: "Anthropic rate limited the diagnostic request.",
        VisionServiceStatus.NETWORK_ERROR: "The server could not reach the Anthropic API.",
        VisionServiceStatus.UNKNOWN_ERROR: "Claude Vision diagnostic failed unexpectedly.",
    }
    return messages[status]


def _classify_status(status_code: int | None, detail: str) -> VisionServiceStatus:
    normalized = detail.lower()
    if status_code == 401:
        return VisionServiceStatus.AUTH_FAILED
    if status_code == 429:
        return VisionServiceStatus.RATE_LIMITED
    if (
        status_code == 402
        or "credit balance is too low" in normalized
        or "plans & billing" in normalized
        or "purchase credits" in normalized
        or "billing" in normalized
    ):
        return VisionServiceStatus.BILLING_BLOCKED
    return VisionServiceStatus.UNKNOWN_ERROR


async def probe_vision_status() -> VisionStatusSnapshot:
    base_snapshot = VisionStatusSnapshot(
        model=settings.claude_model,
        configured=bool(settings.anthropic_api_key),
    )
    if not settings.anthropic_api_key:
        return base_snapshot.model_copy(
            update={
                "status": VisionServiceStatus.MISSING_API_KEY,
                "message": _status_message(VisionServiceStatus.MISSING_API_KEY),
            }
        )

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        await client.messages.create(
            model=settings.claude_model,
            max_tokens=1,
            system="You are a diagnostics probe.",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": _PROBE_IMAGE_B64,
                            },
                        },
                        {"type": "text", "text": _PROBE_PROMPT},
                    ],
                }
            ],
        )
        return base_snapshot.model_copy(
            update={
                "can_analyze": True,
                "status": VisionServiceStatus.OK,
                "message": _status_message(VisionServiceStatus.OK),
            }
        )
    except anthropic.APIStatusError as exc:
        detail = _extract_anthropic_message(exc)
        status = _classify_status(getattr(exc, "status_code", None), detail)
        return base_snapshot.model_copy(
            update={
                "status": status,
                "status_code": getattr(exc, "status_code", None),
                "message": _status_message(status),
                "detail": detail,
            }
        )
    except anthropic.APIConnectionError as exc:
        return base_snapshot.model_copy(
            update={
                "status": VisionServiceStatus.NETWORK_ERROR,
                "message": _status_message(VisionServiceStatus.NETWORK_ERROR),
                "detail": str(exc),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return base_snapshot.model_copy(
            update={
                "status": VisionServiceStatus.UNKNOWN_ERROR,
                "message": _status_message(VisionServiceStatus.UNKNOWN_ERROR),
                "detail": str(exc),
            }
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                await close()

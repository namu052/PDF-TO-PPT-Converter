from __future__ import annotations

import io
import re

from PIL import Image

from app.models import FinalElement, RenderStrategy
from app.session_store import get_asset_path, get_source_image_path, save_asset, save_final_elements

# Defense in depth: asset_id ultimately becomes a filename under
# output/<job>/assets/<asset_id>.png. Reject anything that could escape.
_ASSET_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def ensure_assets_for_slide(job_id: str, slide_num: int, elements: list[FinalElement]) -> list[FinalElement]:
    source_path = get_source_image_path(job_id, slide_num)
    if not source_path:
        return elements

    source = Image.open(source_path).convert("RGBA")
    updated: list[FinalElement] = []
    for element in elements:
        if element.render_strategy == RenderStrategy.PRIMITIVE:
            updated.append(element)
            continue

        candidate_id = element.asset_id or f"{slide_num:03d}_{element.final_id}"
        asset_id = candidate_id if _ASSET_ID_SAFE_RE.match(candidate_id) else f"{slide_num:03d}_{element.final_id}"
        asset_path = get_asset_path(job_id, asset_id)
        if not asset_path.exists():
            cropped = _crop_bbox(source, element.source_region or element.bbox)
            buffer = io.BytesIO()
            cropped.save(buffer, format="PNG")
            save_asset(job_id, asset_id, buffer.getvalue())
        updated.append(element.model_copy(update={"asset_id": asset_id}))

    save_final_elements(job_id, slide_num, updated)
    return updated


def _crop_bbox(image: Image.Image, bbox) -> Image.Image:
    left = int((bbox.x_pct / 100.0) * image.width)
    top = int((bbox.y_pct / 100.0) * image.height)
    right = int(((bbox.x_pct + bbox.width_pct) / 100.0) * image.width)
    bottom = int(((bbox.y_pct + bbox.height_pct) / 100.0) * image.height)
    left = max(0, min(left, image.width - 1))
    top = max(0, min(top, image.height - 1))
    right = max(left + 1, min(right, image.width))
    bottom = max(top + 1, min(bottom, image.height))
    return image.crop((left, top, right, bottom))

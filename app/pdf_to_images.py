"""PDF to PNG image conversion utilities.

This module provides PDF validation and page-level rendering to PNG bytes
using PyMuPDF (fitz). It is consumed by the extraction pipeline to feed
slide images to the Vision analyzer.

Public API:
- ``PDFProcessingError``: custom exception for PDF handling failures.
- ``validate_pdf(pdf_bytes)``: validates a PDF and returns its page count.
- ``pdf_to_images(pdf_bytes, dpi=None)``: renders every page to PNG bytes.

Specification source: PLAN.md §10.5.
"""

from __future__ import annotations

import logging
import struct
import zlib

import fitz  # PyMuPDF

from app.config import settings

logger = logging.getLogger(__name__)


class PDFProcessingError(Exception):
    """Raised when a PDF cannot be opened, is invalid, or fails to render."""

    pass


def validate_pdf(pdf_bytes: bytes) -> int:
    """Validate a PDF byte stream and return the number of pages.

    Checks performed:
    - The bytes must form a PDF that PyMuPDF can open.
    - The document must not be encrypted.
    - The document must contain at least one page.
    - The page count must not exceed ``settings.max_slides``.

    Args:
        pdf_bytes: Raw bytes of the PDF file.

    Returns:
        The number of pages in the validated PDF.

    Raises:
        PDFProcessingError: If the PDF is missing, unreadable, encrypted,
            empty, or has too many pages.
    """
    if not pdf_bytes:
        raise PDFProcessingError("빈 PDF 파일입니다.")

    # Magic bytes check (PLAN §12): PDF는 반드시 "%PDF-"로 시작해야 한다.
    # 앞쪽 1KB 내에 BOM/whitespace 없이 선두에 오는 것이 일반적이지만,
    # 관용적으로 파일 앞부분에서 %PDF- 시그니처를 탐색한다.
    if b"%PDF-" not in pdf_bytes[:1024]:
        raise PDFProcessingError("유효한 PDF 파일이 아닙니다.")

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise PDFProcessingError(f"PDF 파일을 열 수 없습니다: {e}") from e

    try:
        if doc.is_encrypted:
            raise PDFProcessingError("암호화된 PDF는 지원하지 않습니다.")

        page_count = len(doc)
    finally:
        doc.close()

    if page_count == 0:
        raise PDFProcessingError("PDF에 페이지가 없습니다.")

    if page_count > settings.max_slides:
        raise PDFProcessingError(
            f"슬라이드가 너무 많습니다 ({page_count}장). "
            f"최대 {settings.max_slides}장까지 지원합니다."
        )

    return page_count


def pdf_to_images(pdf_bytes: bytes, dpi: int | None = None) -> list[bytes]:
    """Render every page of a PDF to a PNG byte string.

    The rendering resolution is controlled by ``dpi`` (falling back to
    ``settings.render_dpi``). Pages that fail to render individually are
    replaced with a placeholder PNG so that the returned list always has
    the same length as the PDF page count.

    Args:
        pdf_bytes: Raw bytes of the PDF file.
        dpi: Optional rendering DPI. Defaults to ``settings.render_dpi``.

    Returns:
        A list of PNG byte strings, one per page (1-indexed slide order).

    Raises:
        PDFProcessingError: If the PDF cannot be opened at all.
    """
    effective_dpi = dpi or settings.render_dpi
    zoom = effective_dpi / 72  # fitz default resolution is 72 DPI
    matrix = fitz.Matrix(zoom, zoom)

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise PDFProcessingError(f"PDF 파일을 열 수 없습니다: {e}") from e

    images: list[bytes] = []
    try:
        for page_idx in range(len(doc)):
            images.append(_render_page(doc, page_idx, matrix))
    finally:
        doc.close()

    return images


def _render_page(doc: fitz.Document, page_idx: int, matrix: fitz.Matrix) -> bytes:
    """Render a single PDF page to PNG bytes, using a placeholder on failure."""
    try:
        page = doc[page_idx]
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        png_bytes = pix.tobytes("png")
        logger.debug(
            "Page %d: %dx%dpx, %d bytes",
            page_idx + 1,
            pix.width,
            pix.height,
            len(png_bytes),
        )
        return png_bytes
    except Exception as e:
        logger.warning("Page %d 렌더링 실패: %s", page_idx + 1, e)
        return _create_placeholder_png()


def _create_placeholder_png() -> bytes:
    """Create a small white PNG to use when page rendering fails.

    Produces a minimal 100x56 white PNG without external dependencies so
    that the overall pipeline can continue even if a single page fails.
    """
    width, height = 100, 56

    def create_chunk(chunk_type: bytes, data: bytes) -> bytes:
        chunk = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + chunk + crc

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = create_chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
    )

    row = b"\x00" + b"\xff\xff\xff" * width
    raw_all = row * height
    idat = create_chunk(b"IDAT", zlib.compress(raw_all))
    iend = create_chunk(b"IEND", b"")

    return signature + ihdr + idat + iend

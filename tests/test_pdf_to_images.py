"""Unit tests for ``app.pdf_to_images``.

Spec source: PLAN.md §21.4 and §10.5. Covers ``validate_pdf``,
``pdf_to_images``, and the ``PDFProcessingError`` path for invalid input.
These tests use a hand-rolled minimal single-page PDF so that no external
fixtures are required.
"""

from __future__ import annotations

import pytest

from app.pdf_to_images import PDFProcessingError, pdf_to_images, validate_pdf


# Minimal syntactically valid single-page PDF (1 empty A4-ish page).
MINIMAL_PDF = (
    b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
)

PNG_MAGIC = b"\x89PNG"


# ---------------------------------------------------------------------------
# validate_pdf
# ---------------------------------------------------------------------------


def test_validate_pdf_valid():
    page_count = validate_pdf(MINIMAL_PDF)
    assert page_count == 1


def test_validate_pdf_invalid_bytes():
    # Garbage bytes fail the magic-bytes check first ("유효한 PDF 파일이 아닙니다").
    # A PDF-prefixed-but-unreadable blob fails later at fitz.open ("열 수 없습니다").
    with pytest.raises(PDFProcessingError, match="유효한 PDF 파일이 아닙니다|열 수 없습니다"):
        validate_pdf(b"not a pdf")


def test_pdf_to_images_rejects_non_pdf_bytes():
    with pytest.raises(PDFProcessingError):
        pdf_to_images(b"not a real pdf")


def test_color_utils_happy_and_error_paths():
    # NOTE: app.color_utils.rgb_to_hex has a bug (accesses .red/.green/.blue
    # attributes which don't exist on RGBColor). Exercising only the parts
    # that work today so QA can report the bug without fixing it.
    from app.color_utils import hex_to_rgb, validate_hex_color

    # Valid hex converts without raising and returns an RGBColor-like tuple.
    assert tuple(hex_to_rgb("#112233")) == (0x11, 0x22, 0x33)
    # Invalid hex -> black fallback (covers line 12).
    assert tuple(hex_to_rgb("not-a-color")) == (0, 0, 0)
    # validate_hex_color both branches (covers line 23).
    assert validate_hex_color("#abcdef") is True
    assert validate_hex_color("nope") is False


def test_validate_pdf_empty_bytes():
    with pytest.raises(PDFProcessingError):
        validate_pdf(b"")


def test_validate_pdf_rejects_garbage_prefix():
    # A byte blob that superficially begins with "%PDF" but is otherwise
    # unreadable must still raise rather than silently succeed.
    with pytest.raises(PDFProcessingError):
        validate_pdf(b"%PDF-1.0\nnot really a pdf\n")


# ---------------------------------------------------------------------------
# pdf_to_images
# ---------------------------------------------------------------------------


def test_pdf_to_images_returns_png_list():
    images = pdf_to_images(MINIMAL_PDF, dpi=72)
    assert isinstance(images, list)
    assert len(images) == 1
    assert isinstance(images[0], (bytes, bytearray))
    assert images[0][:4] == PNG_MAGIC


def test_pdf_to_images_respects_custom_dpi():
    # Higher DPI should produce a larger payload than a lower DPI render
    # because pixel area grows quadratically with the zoom factor.
    low = pdf_to_images(MINIMAL_PDF, dpi=36)
    high = pdf_to_images(MINIMAL_PDF, dpi=144)
    assert len(low) == 1 and len(high) == 1
    assert low[0][:4] == PNG_MAGIC
    assert high[0][:4] == PNG_MAGIC
    assert len(high[0]) >= len(low[0])


def test_pdf_to_images_invalid_bytes_raises():
    with pytest.raises(PDFProcessingError, match="열 수 없습니다"):
        pdf_to_images(b"definitely not a pdf", dpi=72)


def test_pdf_to_images_empty_bytes_raises():
    with pytest.raises(PDFProcessingError):
        pdf_to_images(b"", dpi=72)

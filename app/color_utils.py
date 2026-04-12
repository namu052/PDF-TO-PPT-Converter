import re

from pptx.dml.color import RGBColor

_HEX_PATTERN = re.compile(r'^#?([0-9a-fA-F]{6})$')


def hex_to_rgb(hex_color: str) -> RGBColor:
    """'#RRGGBB' 또는 'RRGGBB' -> RGBColor. 잘못된 값이면 검정 반환."""
    match = _HEX_PATTERN.match(hex_color.strip())
    if not match:
        return RGBColor(0, 0, 0)
    hex_str = match.group(1)
    return RGBColor.from_string(hex_str)


def rgb_to_hex(rgb: RGBColor) -> str:
    """RGBColor -> '#RRGGBB'"""
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def validate_hex_color(value: str) -> bool:
    return bool(_HEX_PATTERN.match(value.strip()))

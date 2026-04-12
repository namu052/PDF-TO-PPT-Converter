FONT_FALLBACK = {
    # Google 폰트 -> Windows 기본 폰트
    "Google Sans": "Arial",
    "Google Sans Text": "Arial",
    "Product Sans": "Arial",
    "Roboto": "Arial",
    "Roboto Condensed": "Arial Narrow",
    "Roboto Slab": "Georgia",
    "Open Sans": "Calibri",
    "Lato": "Calibri",
    "Montserrat": "Arial",
    "Noto Sans": "Arial",
    "Noto Sans KR": "맑은 고딕",
    "Noto Sans JP": "Yu Gothic",
    "Source Sans Pro": "Calibri",
    "Poppins": "Arial",
    "Inter": "Arial",

    # macOS 폰트 -> Windows 폰트
    "Helvetica Neue": "Arial",
    "Helvetica": "Arial",
    "SF Pro": "Segoe UI",
}


def resolve_font(detected_name: str) -> str:
    """감지된 폰트 이름을 시스템에서 사용 가능한 폰트로 매핑"""
    if not detected_name:
        return "Calibri"
    return FONT_FALLBACK.get(detected_name, detected_name)

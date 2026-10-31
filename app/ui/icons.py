"""선형 아이콘 세트 (웹 index.html 의 SVG 스프라이트와 동일한 도형).

icon("camera", "#e7eaf0") 처럼 색을 지정해 QIcon 으로 쓴다. 렌더 결과는 캐시된다.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "review": '<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/>',
    "aperture": '<circle cx="12" cy="12" r="9"/><path d="M14.3 3.3L9.7 11M20.4 8.5H11.2M19.4 16.2L14.8 8.2M9.7 20.7l4.6-7.9M3.6 15.5h9.2M4.6 7.8l4.6 8"/>',
    "folder": '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1111.2 3 7 7 0 0021 12.8z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5l-4.2-4.2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "edit": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/>',
    "videocam": '<path d="M22 7l-6 5 6 5z"/><rect x="2" y="5" width="14" height="14" rx="2.5"/>',
    "camera": '<path d="M3 8.5A2.5 2.5 0 015.5 6h2l1.6-2.2h5.8L16.5 6h2A2.5 2.5 0 0121 8.5v9a2.5 2.5 0 01-2.5 2.5h-13A2.5 2.5 0 013 17.5z"/><circle cx="12" cy="13" r="3.8"/>',
    "pause": '<rect x="6.5" y="5" width="3.6" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="13.9" y="5" width="3.6" height="14" rx="1" fill="currentColor" stroke="none"/>',
    "play": '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor" stroke="none"/>',
    "rec": '<circle cx="12" cy="12" r="6.5" fill="currentColor" stroke="none"/>',
    "freeze": '<path d="M12 2.5v19M3.8 7.2l16.4 9.6M3.8 16.8l16.4-9.6M9.5 4l2.5 2 2.5-2M9.5 20l2.5-2 2.5 2"/>',
    "quad": '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M12 3v18M3 12h18"/>',
    "save": '<path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z"/><path d="M17 21v-7H7v7M7 3v4h8"/>',
    "printer": '<path d="M6 9V3h12v6"/><path d="M6 18H4.5A2.5 2.5 0 012 15.5v-4A2.5 2.5 0 014.5 9h15a2.5 2.5 0 012.5 2.5v4a2.5 2.5 0 01-2.5 2.5H18"/><rect x="6" y="14" width="12" height="7" rx="1"/>',
    "chev-l": '<path d="M15 18l-6-6 6-6"/>',
    "chev-r": '<path d="M9 18l6-6-6-6"/>',
    "comment": '<path d="M21 14.5a2.5 2.5 0 01-2.5 2.5H8l-5 4V5.5A2.5 2.5 0 015.5 3h13A2.5 2.5 0 0121 5.5z"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a6 6 0 016-6h4a6 6 0 016 6v1"/>',
    "live": '<circle cx="12" cy="12" r="2.5"/><path d="M16.2 7.8a6 6 0 010 8.4M7.8 16.2a6 6 0 010-8.4M19 5a10 10 0 010 14M5 19A10 10 0 015 5"/>',
    "single": '<rect x="3" y="4" width="18" height="16" rx="2.5"/>',
    "compare": '<rect x="2.5" y="4" width="8.5" height="16" rx="2"/><rect x="13" y="4" width="8.5" height="16" rx="2"/>',
    "hand": '<path d="M18 11V6.5a1.75 1.75 0 00-3.5 0V11M14.5 10V4.5a1.75 1.75 0 00-3.5 0V10M11 10.5V6a1.75 1.75 0 00-3.5 0v8.5"/><path d="M18 9a1.75 1.75 0 013.5 0v5a8 8 0 01-8 8h-1.6a7.5 7.5 0 01-5.6-2.5L3 15.6a1.8 1.8 0 012.6-2.5L7.5 15"/>',
    "lens": '<circle cx="10.5" cy="10.5" r="7"/><path d="M21 21l-5.5-5.5"/><circle cx="10.5" cy="10.5" r="2.8"/>',
    "crop": '<path d="M6 2v14a2 2 0 002 2h14"/><path d="M18 22V8a2 2 0 00-2-2H2"/>',
    "type": '<path d="M4 7V4h16v3M9 20h6M12 4v16"/>',
    "arrow": '<path d="M6 18L18 6M8 6h10v10"/>',
    "circle": '<ellipse cx="12" cy="12" rx="9" ry="7.5"/>',
    "ruler": '<path d="M3 15.5L15.5 3 21 8.5 8.5 21z"/><path d="M7 11.5l2 2M10 8.5l2 2M13 5.5l2 2M5.5 14.5l1 1M8.5 11.5l1 1"/>',
    "angle": '<path d="M4 20h16M4 20L15 5"/><path d="M10 20a6 6 0 00-2.2-4.6"/>',
    "calib": '<circle cx="12" cy="12" r="7.5"/><path d="M12 2v4.5M12 17.5V22M2 12h4.5M17.5 12H22"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/>',
    "rot-l": '<path d="M3.5 12a8.5 8.5 0 108.5-8.5 9.2 9.2 0 00-6.4 2.6L3.5 8"/><path d="M3.5 3.5V8H8"/>',
    "rot-r": '<path d="M20.5 12A8.5 8.5 0 1112 3.5a9.2 9.2 0 016.4 2.6L20.5 8"/><path d="M20.5 3.5V8H16"/>',
    "flip-h": '<path d="M12 3v18" stroke-dasharray="2 2.5"/><path d="M9 7l-6 5 6 5zM15 7l6 5-6 5z"/>',
    "flip-v": '<path d="M3 12h18" stroke-dasharray="2 2.5"/><path d="M7 9l5-6 5 6zM7 15l5 6 5-6z"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 010 11H11"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 000 11H13"/>',
    "history": '<path d="M3 3v5h5"/><path d="M3.1 13A9 9 0 106 5.3L3 8"/><path d="M12 7.5V12l3 2"/>',
    "eraser": '<path d="M20 20H8.5l-5-5a2 2 0 010-2.8l9.2-9.2a2 2 0 012.8 0l5 5a2 2 0 010 2.8L12 20"/><path d="M6.5 10.5l7 7"/>',
    "fit": '<path d="M8 3H5a2 2 0 00-2 2v3M21 8V5a2 2 0 00-2-2h-3M3 16v3a2 2 0 002 2h3M16 21h3a2 2 0 002-2v-3"/>',
    "zin": '<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5l-4.2-4.2M11 8v6M8 11h6"/>',
    "zout": '<circle cx="11" cy="11" r="7"/><path d="M20.5 20.5l-4.2-4.2M8 11h6"/>',
    "bot": '<rect x="4" y="8" width="16" height="11" rx="3"/><circle cx="9" cy="13.5" r="1.3" fill="currentColor"/><circle cx="15" cy="13.5" r="1.3" fill="currentColor"/><path d="M12 8V5M12 5a1.5 1.5 0 10-.01 0M2 13h2M20 13h2"/>',
    "check": '<path d="M4 12.5l5 5L20 6.5"/>',
    "wand": '<path d="M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8L19 13M17.8 6.2L19 5M12.2 6.2L11 5"/><path d="M3 21l11-11"/>',
    "sliders": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1.5 14h5M9.5 8h5M17.5 16h5"/>',
    "x": '<path d="M18 6L6 18M6 6l12 12"/>',
    "info": '<circle cx="12" cy="12" r="9.5"/><path d="M12 11v5.5M12 7.6h.01"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="8.5" cy="8.5" r="1.6"/><path d="M21 15l-5-5L5 21"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13"/><circle cx="3.5" cy="6" r="1" fill="currentColor"/><circle cx="3.5" cy="12" r="1" fill="currentColor"/><circle cx="3.5" cy="18" r="1" fill="currentColor"/>',
    "expand": '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
    "shrink": '<path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7"/>',
    "trash": '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>',
    "keyboard": '<rect x="2" y="5" width="20" height="14" rx="2.5"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 12.5h.01M10 12.5h.01M14 12.5h.01M18 12.5h.01M8 16h8"/>',
    "user-plus": '<circle cx="10" cy="8" r="4"/><path d="M2.5 21v-1a6 6 0 016-6h3a6 6 0 013.5 1.1"/><path d="M19 14v6M16 17h6"/>',
    "user-edit": '<circle cx="10" cy="8" r="4"/><path d="M2.5 21v-1a6 6 0 016-6h3"/><path d="M20.2 12.3a1.6 1.6 0 012.3 2.3L16.5 20.6 13.5 21.3l.7-3z"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2.5"/><path d="M3 10h18M8 3v4M16 3v4"/><path d="M8 14h.01M12 14h.01M16 14h.01M8 17.5h.01M12 17.5h.01"/>',
    "home": '<path d="M3 11.5L12 4l9 7.5"/><path d="M5.5 10v10h13V10"/><path d="M10 20v-6h4v6"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>',
    "clipboard": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1M9 10h6M9 14h6M9 17.5h3.5"/>',
    "book": '<path d="M4 4.5A1.5 1.5 0 015.5 3H19v15H5.5A1.5 1.5 0 004 19.5z"/><path d="M4 19.5A1.5 1.5 0 015.5 18H19v3H5.5A1.5 1.5 0 014 19.5zM8 7h7"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16.5 9.5"/>',
    "file-pdf": '<path d="M14 3H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V9z"/><path d="M14 3v6h6M8.5 17.5v-5h2a1.5 1.5 0 010 3h-2"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z"/>',
    "chev-d": '<path d="M6 9l6 6 6-6"/>',
    "send": '<path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/>',
    "download": '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
    # 상단바 질환 아이콘 (app/ui/dx_bar.py) — 눈 모양 위에 소견 위치를 표시
    "dx-normal": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "dx-cataract": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3.3" fill="currentColor" fill-opacity=".45"/><path d="M12 9.4v5.2M9.4 12h5.2" opacity=".85"/>',
    "dx-pco": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="4.2"/><circle cx="12" cy="12" r="2.2" fill="currentColor" fill-opacity=".4"/><path d="M9.2 9.2l5.6 5.6M14.8 9.2l-5.6 5.6" opacity=".7"/>',
    "dx-pterygium": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><path d="M3 12l6.6-2.5v5z" fill="currentColor" fill-opacity=".55"/>',
    "dx-conjunctivitis": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><path d="M4.2 10.6l1.2.9-1.2.9 1.2.9M19.8 10.6l-1.2.9 1.2.9-1.2.9M6.4 13.6l1 .7M17.6 13.6l-1 .7M6.6 9.4l.9-.6M17.4 9.4l-.9-.6"/>',
    "dx-hemorrhage": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><path d="M15.4 9.2c1.8-.5 3.6.3 4.8 1.9-.9 1.6-2.4 2.7-4.3 3-.7-1.5-.9-3.2-.5-4.9z" fill="currentColor" stroke="none"/>',
    "dx-dryeye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><circle cx="9.3" cy="14.8" r=".9" fill="currentColor" stroke="none"/><circle cx="11.2" cy="16.2" r=".9" fill="currentColor" stroke="none"/><circle cx="13.4" cy="16.1" r=".9" fill="currentColor" stroke="none"/><circle cx="15" cy="14.6" r=".9" fill="currentColor" stroke="none"/><circle cx="12.2" cy="14.2" r=".8" fill="currentColor" stroke="none"/>',
    "dx-keratitis": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><circle cx="13.8" cy="10.4" r="1.8" fill="currentColor" stroke="none"/><path d="M4.4 10.8l1.2.9-1.2.9M19.6 10.8l-1.2.9 1.2.9"/>',
    "dx-abrasion": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><path d="M9.2 8.6l1.6 1.9-1 1.4 1.8 1.6-.8 1.5" stroke-width="1.5"/><circle cx="14.6" cy="13.6" r="1.1" fill="currentColor" stroke="none"/>',
    "dx-blepharitis": '<path d="M2 11.5s3.6-6.5 10-6.5 10 6.5 10 6.5"/><circle cx="12" cy="11.8" r="3"/><path d="M2.5 15c2.7 2.5 5.9 3.8 9.5 3.8s6.8-1.3 9.5-3.8"/><circle cx="6.2" cy="17.4" r=".95" fill="currentColor" stroke="none"/><circle cx="9.6" cy="18.6" r=".95" fill="currentColor" stroke="none"/><circle cx="14.4" cy="18.6" r=".95" fill="currentColor" stroke="none"/><circle cx="17.8" cy="17.4" r=".95" fill="currentColor" stroke="none"/>',
    "dx-chalazion": '<path d="M2 13c3-5 6.5-7.5 10-7.5S19 8 22 13"/><path d="M9 6.6a3 3 0 016 0" fill="currentColor" fill-opacity=".35"/><circle cx="12" cy="12.8" r="2.8"/><path d="M3 16.5c2.5 2.4 5.5 3.6 9 3.6s6.5-1.2 9-3.6"/>',
    "dx-uveitis": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3.4"/><path d="M12 7.2V6.2M12 16.8v1M7.2 12h-1M16.8 12h1M8.6 8.6l-.7-.7M15.4 15.4l.7.7M15.4 8.6l.7-.7M8.6 15.4l-.7.7"/><path d="M10.9 11.4l1.2-1 1.2.8-.2 1.5-1.3.6-1-.9z" fill="currentColor" stroke="none"/>',
    "dx-pinguecula": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><ellipse cx="7" cy="12.5" rx="1.6" ry="1.1" fill="currentColor" stroke="none"/>',
}

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{c}" '
        'stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round" color="{c}">{body}</svg>')


def names() -> list[str]:
    return list(_PATHS)


def svg(name: str, color: str = "#e7eaf0", width: float = 1.9) -> str:
    body = _PATHS[name].replace("currentColor", color)
    return _SVG.format(c=color, w=width, body=body)


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = "#e7eaf0", size: int = 18, width: float = 1.9) -> QPixmap:
    ratio = 2  # 고해상도 디스플레이 대비 2배로 렌더
    pm = QPixmap(size * ratio, size * ratio)
    pm.fill(Qt.GlobalColor.transparent)
    r = QSvgRenderer(QByteArray(svg(name, color, width).encode("utf-8")))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    r.render(p, QRectF(0, 0, size * ratio, size * ratio))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(name: str, color: str = "#e7eaf0", size: int = 18, disabled: str | None = None) -> QIcon:
    ic = QIcon(pixmap(name, color, size))
    if disabled:
        ic.addPixmap(pixmap(name, disabled, size), QIcon.Mode.Disabled)
    return ic


def gap(text: str) -> str:
    """아이콘과 글자 사이 간격 (Qt 버튼 기본 간격이 좁아 앞에 공백 하나)."""
    return f" {text}" if text else text

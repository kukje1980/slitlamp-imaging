"""앱 테마(QSS) + 윈도우 아이콘 — "Clinical Graphite".

안과 영상 프로그램(VK-2, EyeSuite, IMAGEnet 등)처럼 어두운 그래파이트 작업 공간을 기본으로 하고
웹 버전(app.css)과 같은 팔레트·형태를 쓴다. light / dark 지원.
Fusion 스타일 위에 QSS를 올려 OS와 무관하게 일관된 외형을 보장한다.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QPainter, QPen, QPixmap, QPolygonF

from app import brand as _brand

# 팔레트 (웹 app.css 토큰과 동일)
DARK = {
    "bg": "#0b0e13", "panel": "#12161d", "panel2": "#181d26", "raised": "#1d2330",
    "ink": "#e7eaf0", "ink_soft": "#a3acba", "ink_muted": "#6b7586",
    "rule": "#232a35", "rule_strong": "#303846",
    "accent": "#33c2b1", "accent_hover": "#6fdccf", "accent_ink": "#032321", "accent_soft": "#13302d",
    "record": "#f0524f", "record_hover": "#ff6b68", "record_soft": "#3a1a1c",
    "field": "#0f1319", "disabled": "#3a4352",
    "od": "#5aa9ff", "os": "#f5a524", "ou": "#b18cff",
}
LIGHT = {
    "bg": "#eef1f5", "panel": "#ffffff", "panel2": "#f4f6f9", "raised": "#ffffff",
    "ink": "#111827", "ink_soft": "#4b5563", "ink_muted": "#8b95a3",
    "rule": "#dfe4eb", "rule_strong": "#cbd3dd",
    "accent": "#0f8f84", "accent_hover": "#0b7268", "accent_ink": "#ffffff", "accent_soft": "#daf3ef",
    "record": "#dc2626", "record_hover": "#b91c1c", "record_soft": "#fde4e4",
    "field": "#ffffff", "disabled": "#c3cad4",
    "od": "#2563eb", "os": "#c2410c", "ou": "#7c3aed",
}

# 아이콘 색 (양쪽 테마에서 모두 읽히는 중간 톤) — 브랜드가 강조색을 바꿀 수 있음
_B = _brand.current()
ICON = "#98a2b3"
ICON_ACCENT = _B.get("icon_accent", "#2fb5a5")
ICON_RECORD = "#ef4f4c"
ICON_ON_ACCENT = _B.get("icon_on_accent", "#06231f")
EYE_COLORS = dict(_B.get("eye_colors") or {"OD": "#5aa9ff", "OS": "#f5a524", "OU": "#b18cff"})


def palette(mode: str) -> dict:
    base = DARK if mode == "dark" else LIGHT
    over = _brand.current().get("palette", {}).get("dark" if mode == "dark" else "light", {})
    return {**base, **over}


def _arrow_file(color: str, up: bool) -> str:
    """콤보·숫자칸의 ▼/▲ 화살표 그림 (QSS 는 image: url() 로만 그릴 수 있어 임시 폴더에 PNG 로 둔다)."""
    path = Path(tempfile.gettempdir()) / "slitlamp-ui" / f"arrow-{'up' if up else 'down'}-{color.lstrip('#')}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        img = QImage(20, 20, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        pts = [(3, 14), (17, 14), (10, 5)] if up else [(3, 6), (17, 6), (10, 15)]
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))
        p.end()
        img.save(str(path))
    return path.as_posix()


def build_qss(mode: str = "dark") -> str:
    c = palette(mode)
    arrow_down, arrow_up = _arrow_file(c["ink_soft"], False), _arrow_file(c["ink_soft"], True)
    return f"""
    QWidget {{
        background: {c['bg']};
        color: {c['ink']};
        font-family: "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", "맑은 고딕", "Segoe UI", sans-serif;
        font-size: 13px;
    }}
    QMainWindow, QDialog {{ background: {c['bg']}; }}
    QToolTip {{ background: {c['raised']}; color: {c['ink']}; border: 1px solid {c['rule_strong']}; padding: 5px 8px; border-radius: 6px; }}

    /* ---------- 상단바 ---------- */
    QFrame#TopBar {{ background: {c['panel']}; border: none; border-bottom: 1px solid {c['rule']}; }}
    QLabel#BrandTitle {{ background: transparent; font-size: 15px; font-weight: 700; }}
    QLabel#BrandSub {{ background: transparent; color: {c['ink_muted']}; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
    QFrame#ModeSwitch {{ background: {c['bg']}; border: 1px solid {c['rule']}; border-radius: 10px; }}
    QPushButton#ModeBtn {{
        background: transparent; border: none; border-radius: 7px; padding: 6px 14px;
        color: {c['ink_soft']}; font-weight: 600; min-height: 20px;
    }}
    QPushButton#ModeBtn:hover {{ color: {c['ink']}; }}
    QPushButton#ModeBtn:checked {{ background: {c['raised']}; color: {c['ink']}; border: 1px solid {c['rule_strong']}; }}
    QPushButton#IconBtn {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 6px 10px; color: {c['ink_soft']}; font-weight: 600; }}
    QPushButton#IconBtn:hover {{ background: {c['panel2']}; border-color: {c['rule']}; color: {c['ink']}; }}
    QFrame#NavSeg {{ background: {c['bg']}; border: 1px solid {c['rule']}; border-radius: 11px; }}
    QPushButton#NavBtn {{
        background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 0px; min-height: 0px;
    }}
    QPushButton#NavBtn:hover {{ background: {c['panel2']}; border-color: {c['rule']}; }}
    QPushButton#NavBtn:pressed {{ background: {c['raised']}; }}
    QPushButton#NavBtn:checked {{ background: {c['raised']}; border: 1px solid {c['rule_strong']}; }}

    /* 상단바 질환 아이콘 줄 (app/ui/dx_bar.py) */
    QFrame#DxBar {{ background: {c['bg']}; border: 1px solid {c['rule']}; border-radius: 11px; }}
    QPushButton#DxBtn {{ background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 0px; min-height: 0px; }}
    QPushButton#DxBtn:hover {{ background: {c['panel2']}; border-color: {c['rule']}; }}
    QPushButton#DxBtn:checked {{ background: {c['raised']}; border: 1px solid {c['accent']}; }}
    QPushButton#DxMenuBtn {{ background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 4px 8px; min-height: 0px; color: {c['ink_soft']}; font-weight: 600; }}
    QPushButton#DxMenuBtn:hover {{ background: {c['panel2']}; border-color: {c['rule']}; color: {c['ink']}; }}
    QPushButton#DxMenuBtn::menu-indicator {{ width: 0px; }}
    /* 멀티 화면 오른쪽 메뉴(보정 · 편집 · 분석) 접기 · 펴기 */
    QToolButton#ProSideToggle {{ background: {c['panel']}; border: 1px solid {c['rule']}; border-radius: 7px; padding: 0px; }}
    QToolButton#ProSideToggle:hover {{ background: {c['panel2']}; border-color: {c['rule_strong']}; }}
    QToolButton#ProSideToggle:checked {{ background: {c['raised']}; }}

    /* 아이콘 위 · 이름 아래 버튼(IconTextButton) 안쪽 라벨 */
    QPushButton[iconText="true"] QLabel {{ background: transparent; }}
    QLabel#BtnText {{ color: {c['ink_soft']}; font-size: 11px; font-weight: 600; }}
    QLabel#BtnText[on="true"] {{ color: {c['ink']}; }}
    QLabel#BtnText:disabled {{ color: {c['disabled']}; }}

    /* ---------- 패널 ---------- */
    QFrame#Panel, #ThumbnailPanel {{ background: {c['panel']}; border: 1px solid {c['rule']}; border-radius: 12px; }}
    QFrame#PatientPick {{ background: transparent; border: none; }}
    QFrame#Banner {{ background: {c['panel']}; border: none; border-bottom: 1px solid {c['rule']}; }}
    QFrame#BannerSep {{ background: {c['rule']}; }}
    QLabel#Avatar {{ background: {c['accent_soft']}; color: {c['accent_hover'] if mode == 'dark' else c['accent']};
        border-radius: 19px; font-size: 15px; font-weight: 700; }}
    QLabel#PatientName {{ background: transparent; font-size: 16px; font-weight: 700; }}
    QLabel#ChartNo {{ background: transparent; color: {c['ink_muted']}; font-family: Menlo, Consolas, "JetBrains Mono", monospace; font-size: 12px; }}
    QLabel#PatientMeta {{ background: transparent; color: {c['ink_soft']}; font-size: 12px; }}

    /* 라벨 */
    QLabel {{ background: transparent; color: {c['ink']}; }}
    QLabel#Muted {{ color: {c['ink_soft']}; }}
    QLabel#StatusLabel {{ color: {c['ink_soft']}; font-family: Menlo, Consolas, "JetBrains Mono", monospace; font-size: 12px; }}
    QLabel#PanelHeader {{ color: {c['ink_muted']}; font-weight: 700; font-size: 11px; padding: 8px 2px 2px; letter-spacing: 1px; }}
    QLabel#CountBadge {{ background: {c['accent_soft']}; color: {c['accent_hover'] if mode == 'dark' else c['accent']};
        border-radius: 9px; padding: 1px 8px; font-size: 11px; font-weight: 700; }}
    QLabel#ThumbTitle {{ font-weight: 700; font-size: 13px; }}
    QLabel#DayHeader {{ color: {c['ink_muted']}; font-size: 11px; font-weight: 700; padding: 6px 2px 0; }}
    QPushButton#DayDeleteBtn {{ background: transparent; border: 1px solid transparent; border-radius: 6px;
                               padding: 0; margin-top: 4px; }}
    QPushButton#DayDeleteBtn:hover {{ background: {c['record_soft']}; border-color: {c['record']}; }}

    /* 입력/콤보 */
    QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit, QDateEdit, QDoubleSpinBox {{
        background: {c['field']}; border: 1px solid {c['rule']}; border-radius: 8px;
        padding: 6px 10px; min-height: 20px;
        selection-background-color: {c['accent']}; selection-color: {c['accent_ink']};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QDateEdit:focus {{
        border: 1px solid {c['accent']};
    }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{ image: url("{arrow_down}"); width: 10px; height: 10px; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
    QDateEdit::up-button, QDateEdit::down-button {{ border: none; background: transparent; width: 18px; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateEdit::up-arrow {{ image: url("{arrow_up}"); width: 9px; height: 9px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateEdit::down-arrow {{ image: url("{arrow_down}"); width: 9px; height: 9px; }}
    QComboBox QAbstractItemView {{
        background: {c['panel']}; border: 1px solid {c['rule_strong']}; padding: 4px;
        selection-background-color: {c['accent_soft']}; selection-color: {c['ink']}; outline: none;
    }}
    QCheckBox {{ background: transparent; spacing: 8px; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid {c['rule_strong']}; background: {c['field']}; }}
    QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}

    /* 일반 버튼 */
    QPushButton {{
        background: {c['raised']}; border: 1px solid {c['rule_strong']}; border-radius: 8px;
        padding: 7px 14px; min-height: 18px; color: {c['ink']}; font-weight: 600;
    }}
    QPushButton:hover {{ background: {c['panel2']}; border-color: {c['ink_muted']}; }}
    QPushButton:pressed {{ background: {c['panel2']}; }}
    QPushButton:disabled {{ color: {c['disabled']}; border-color: {c['rule']}; background: transparent; }}

    QPushButton#Primary {{ background: {c['accent']}; color: {c['accent_ink']}; border: none; font-weight: 700; }}
    QPushButton#Primary:hover {{ background: {c['accent_hover']}; }}
    QPushButton#Primary:disabled {{ background: {c['rule']}; color: {c['disabled']}; }}

    /* 하단 촬영 도크 */
    QFrame#Dock {{ background: {c['panel']}; border: 1px solid {c['rule']}; border-radius: 12px; }}
    QFrame#Dock QLabel {{ background: transparent; }}
    QFrame#Dock QWidget#DockPart {{ background: transparent; }}
    QFrame#DockSep {{ background: {c['rule']}; }}
    QFrame#EyeSeg, QFrame#DockGroup {{ background: {c['bg']}; border: 1px solid {c['rule']}; border-radius: 10px; }}
    QPushButton#EyeBtn {{
        background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 2px 6px; min-width: 40px; min-height: 34px;
        color: {c['ink_soft']}; font-family: Menlo, Consolas, "JetBrains Mono", monospace; font-size: 13px; font-weight: 700;
    }}
    QPushButton#EyeBtn:hover {{ color: {c['ink']}; }}
    QPushButton#EyeBtn:checked {{ background: {c['raised']}; border: 1px solid {c['rule_strong']}; color: {c['ink']}; }}
    QPushButton#EyeBtn[eye="OD"]:checked {{ color: {c['od']}; border: 1px solid {c['od']}; }}
    QPushButton#EyeBtn[eye="OS"]:checked {{ color: {c['os']}; border: 1px solid {c['os']}; }}
    QPushButton#EyeBtn[eye="OU"]:checked {{ color: {c['ou']}; border: 1px solid {c['ou']}; }}
    QLabel#EyeStatus {{
        color: {c['ink_soft']}; background: {c['bg']}; border: 1px solid {c['rule']}; border-radius: 8px;
        padding: 2px 7px; min-height: 18px; font-size: 11px; font-weight: 700;
    }}
    QLabel#EyeStatus[level="device"] {{ color: #3ccf7a; border-color: #3ccf7a; }}
    QLabel#EyeStatus[level="unknown"] {{ color: #ffb454; border-color: #d99a2b; }}
    QLabel#EyeStatus[level="stale"] {{ color: #ffb454; border-color: #d99a2b; }}
    QLabel#EyeStatus[level="manual"] {{ color: {c['ink_soft']}; }}
    QPushButton#SmartBtn {{
        background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 2px 10px; min-height: 28px;
        color: {c['ink_soft']}; font-weight: 600;
    }}
    QPushButton#SmartBtn:hover {{ color: {c['ink']}; }}
    QPushButton#SmartBtn:checked {{ background: {c['accent_soft']}; border: 1px solid {c['accent']}; color: {c['accent_hover'] if mode == 'dark' else c['accent']}; }}
    QLabel#SmartTitle {{ color: {c['ink']}; font-weight: 700; padding-right: 4px; }}
    QLabel#SmartMeter {{ color: {c['ink_soft']}; font-family: Menlo, Consolas, "JetBrains Mono", monospace; font-size: 12px; padding: 0 3px; }}
    QLabel#SmartHint {{ color: {c['ink_soft']}; font-size: 12px; padding-left: 6px; }}
    QPushButton#ToolBtn, QPushButton#Pause, QPushButton#Freeze, QPushButton#Record {{
        background: transparent; border: 1px solid transparent; border-radius: 9px; color: {c['ink_soft']}; padding: 8px 12px;
    }}
    QPushButton#ToolBtn[iconText="true"], QPushButton#Pause[iconText="true"], QPushButton#Freeze[iconText="true"],
    QPushButton#Record[iconText="true"], QPushButton#Primary[iconText="true"] {{ padding: 0px; min-height: 0px; border-radius: 10px; }}
    QPushButton#ToolBtn:hover, QPushButton#Pause:hover, QPushButton#Freeze:hover {{ background: {c['panel2']}; border-color: {c['rule']}; color: {c['ink']}; }}
    QPushButton#Pause:checked {{ background: {c['accent_soft']}; border-color: {c['accent']}; color: {c['accent_hover'] if mode == 'dark' else c['accent']}; }}
    QPushButton#Pause QLabel#BtnText[on="true"] {{ color: {c['accent_hover'] if mode == 'dark' else c['accent']}; }}
    QPushButton#Freeze {{ color: {c['accent_hover'] if mode == 'dark' else c['accent']}; }}
    QPushButton#Freeze QLabel#BtnText {{ color: {c['accent_hover'] if mode == 'dark' else c['accent']}; }}
    QPushButton#ToolBtn:disabled, QPushButton#Pause:disabled, QPushButton#Freeze:disabled, QPushButton#Record:disabled {{
        color: {c['disabled']}; background: transparent; border-color: transparent; }}
    QPushButton#Shutter {{
        background: {c['accent']}; border: 3px solid {c['accent_soft']}; border-radius: 28px;
        min-width: 50px; max-width: 50px; min-height: 50px; max-height: 50px; padding: 0px;
    }}
    QPushButton#Shutter:hover {{ background: {c['accent_hover']}; border-color: {c['accent']}; }}
    QPushButton#Shutter:pressed {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
    QPushButton#Shutter:disabled {{ background: {c['rule']}; border-color: {c['rule']}; }}
    QPushButton#Record {{ color: {c['record']}; font-weight: 700; }}
    QPushButton#Record QLabel#BtnText {{ color: {c['record']}; font-weight: 700; }}
    QPushButton#Record:hover {{ background: {c['record_soft']}; }}
    QPushButton#Record:checked {{ background: {c['record']}; color: #ffffff; }}
    QPushButton#Record QLabel#BtnText[on="true"] {{ color: #ffffff; }}
    QPushButton#Record QLabel#BtnText:disabled, QPushButton#Freeze QLabel#BtnText:disabled {{ color: {c['disabled']}; }}
    QPushButton#Primary QLabel#BtnText {{ color: {c['accent_ink']}; font-weight: 700; }}
    QPushButton#DateBtn {{ background: {c['raised']}; border: 1px solid {c['rule_strong']}; border-radius: 8px; padding: 5px; }}
    QPushButton#DateBtn:hover {{ background: {c['panel2']}; border-color: {c['ink_muted']}; }}
    QLineEdit[invalid="true"] {{ border: 1px solid {c['record']}; }}

    /* 달력 팝업 */
    QFrame#DatePopup {{ background: {c['panel']}; border: 1px solid {c['rule_strong']}; border-radius: 10px; }}
    QCalendarWidget {{ background: {c['panel']}; }}
    QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {c['panel2']}; border-radius: 8px; }}
    QCalendarWidget QToolButton {{
        background: transparent; color: {c['ink']}; border: none; border-radius: 6px;
        padding: 5px 10px; font-weight: 700; font-size: 13px; icon-size: 16px;
    }}
    QCalendarWidget QToolButton:hover {{ background: {c['raised']}; }}
    QCalendarWidget QToolButton::menu-indicator {{ image: none; }}
    QCalendarWidget QSpinBox {{ padding: 2px 6px; min-height: 16px; }}
    QCalendarWidget QAbstractItemView {{
        background: {c['panel']}; color: {c['ink']}; outline: none; border: none;
        selection-background-color: {c['accent']}; selection-color: {c['accent_ink']};
        alternate-background-color: {c['panel']};
    }}
    QCalendarWidget QAbstractItemView:disabled {{ color: {c['ink_muted']}; }}

    /* 상태바 */
    QStatusBar {{ background: {c['panel']}; border-top: 1px solid {c['rule']}; color: {c['ink_soft']}; }}
    QStatusBar QLabel {{ background: transparent; }}

    /* 스크롤 영역/바 */
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea#HScroll QScrollBar:horizontal {{ height: 8px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c['rule_strong']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {c['ink_muted']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {c['rule_strong']}; border-radius: 4px; min-width: 30px; }}

    /* 썸네일 */
    QPushButton#FilterChip {{
        background: transparent; border: 1px solid {c['rule']}; border-radius: 6px; padding: 3px 4px;
        color: {c['ink_soft']}; font-size: 11px; font-weight: 600; min-height: 16px;
    }}
    QPushButton#FilterChip:hover {{ color: {c['ink']}; border-color: {c['rule_strong']}; }}
    QPushButton#FilterChip:checked {{ background: {c['raised']}; color: {c['ink']}; border: 1px solid {c['rule_strong']}; border-bottom: 2px solid {c['accent']}; }}
    QFrame#ThumbSep {{ background: {c['rule']}; }}

    /* ===== Pro 모드 ===== */
    QFrame#ProToolbar {{ background: {c['panel']}; border: 1px solid {c['rule']}; border-radius: 12px; }}
    QFrame#ProSep {{ background: {c['rule']}; margin: 6px 4px; }}
    QFrame#ProSeg {{ background: {c['bg']}; border: 1px solid {c['rule']}; border-radius: 9px; }}
    QPushButton#ProBtn, QPushButton#ProCapture, QPushButton#ProGhost, QPushButton#ProPrimary,
    QPushButton#ProSegBtn, QPushButton#ProDanger {{
        padding: 6px 11px; font-weight: 600; font-size: 12px; border-radius: 7px;
    }}
    QPushButton#ProDanger {{ background: transparent; border-color: transparent;
                             color: {c['record']}; }}
    QPushButton#ProDanger:hover {{ background: {c['record_soft']}; color: {c['record_hover']}; }}
    QPushButton#ProDanger:disabled {{ color: {c['disabled']}; }}
    QPushButton#ProSegBtn {{ background: transparent; border: 1px solid transparent; color: {c['ink_soft']}; }}
    QPushButton#ProSegBtn:hover {{ background: {c['panel2']}; color: {c['ink']}; }}
    QPushButton#ProSegBtn:checked, QPushButton#ProBtn:checked {{ background: {c['accent']}; color: {c['accent_ink']}; border-color: {c['accent']}; }}
    QPushButton#ProGhost {{ background: transparent; border-color: transparent; color: {c['ink_soft']}; }}
    QPushButton#ProGhost:hover {{ background: {c['panel2']}; color: {c['ink']}; }}
    QPushButton#ProPrimary {{ background: {c['accent']}; color: {c['accent_ink']}; border: none; }}
    QPushButton#ProPrimary:hover {{ background: {c['accent_hover']}; }}
    QPushButton#ProCapture {{ color: {c['accent_hover'] if mode == 'dark' else c['accent']}; border: 1px solid {c['accent']}; background: transparent; }}
    QPushButton#ProCapture:checked {{ background: {c['record']}; color: #ffffff; border-color: {c['record']}; }}
    QToolButton#ProTool {{
        background: {c['raised']}; border: 1px solid {c['rule_strong']}; border-radius: 8px;
        padding: 4px 2px; font-size: 11px; font-weight: 600; color: {c['ink']};
    }}
    QToolButton#ProTool:hover {{ background: {c['panel2']}; border-color: {c['ink_muted']}; }}
    QToolButton#ProTool:checked {{ background: {c['accent']}; color: {c['accent_ink']}; border-color: {c['accent']}; }}
    QToolButton#ProTool:disabled {{ color: {c['disabled']}; background: transparent; border-color: {c['rule']}; }}
    QStackedWidget#ProDisplay {{ background: #000000; border-radius: 12px; }}
    QFrame#ProSide {{ background: {c['panel']}; border: 1px solid {c['rule']}; border-radius: 12px; }}
    QFrame#ProCard {{ background: {c['panel2']}; border: 1px solid {c['rule']}; border-radius: 9px; }}
    QFrame#ProCard QLabel {{ background: transparent; }}
    QLabel#ProInfo {{
        background: {c['panel']}; border: 1px solid {c['rule']}; border-radius: 9px;
        padding: 7px 14px; color: {c['ink_soft']};
    }}
    QTabWidget#ProTabs::pane {{ border: none; }}
    QTabWidget#ProTabs QStackedWidget, QWidget#ProTabPage,
    QFrame#ProSide QScrollArea, QFrame#ProSide QWidget#qt_scrollarea_viewport {{ background: {c['panel']}; }}
    QTabWidget#ProTabs QTabBar {{ background: {c['bg']}; border: 1px solid {c['rule']}; border-radius: 9px; }}
    QTabWidget#ProTabs QTabBar::tab {{
        background: transparent; color: {c['ink_soft']}; padding: 6px 0; margin: 3px 2px; min-width: 76px;
        border: 1px solid transparent; border-radius: 6px; font-weight: 700;
    }}
    QTabWidget#ProTabs QTabBar::tab:selected {{ background: {c['raised']}; color: {c['ink']}; border: 1px solid {c['rule_strong']}; }}
    QTabWidget#ProTabs QTabBar::tab:hover {{ color: {c['ink']}; }}
    QFrame#ProTabGrid {{ background: {c['bg']}; border: 1px solid {c['rule']}; border-radius: 9px; }}
    QPushButton#ProTabBtn {{
        background: transparent; color: {c['ink_soft']}; padding: 5px 0; min-height: 22px;
        border: 1px solid transparent; border-radius: 6px; font-weight: 700;
    }}
    QPushButton#ProTabBtn:checked {{ background: {c['raised']}; color: {c['ink']}; border: 1px solid {c['rule_strong']}; }}
    QPushButton#ProTabBtn:hover {{ color: {c['ink']}; }}
    QLabel#Histogram {{ background: #070a0f; border: 1px solid {c['rule']}; border-radius: 7px; }}
    QFrame#MultiCell {{ background: #0a0d12; border: 1px solid #1d242f; border-radius: 6px; }}
    QFrame#MultiCell:hover {{ border: 2px solid {c['accent']}; }}
    QFrame#MultiCell[selected="true"] {{ border: 3px solid #facc15; }}
    QLabel#MultiCaption {{ background: transparent; color: #c7cdd8; font-size: 11px; }}
    QLabel#SelBadge {{
        background: #facc15; color: #111827; font-weight: 800; font-size: 11px;
        padding: 1px 8px; border-radius: 6px;
    }}
    QSlider {{ background: transparent; }}
    QSlider::groove:horizontal {{ height: 4px; background: {c['rule_strong']}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {c['accent']}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
        border: 2px solid {c['panel']};
    }}
    QSlider#SliderR::handle:horizontal, QSlider#SliderR::sub-page:horizontal {{ background: #f87171; }}
    QSlider#SliderG::handle:horizontal, QSlider#SliderG::sub-page:horizontal {{ background: #4ade80; }}
    QSlider#SliderB::handle:horizontal, QSlider#SliderB::sub-page:horizontal {{ background: #60a5fa; }}

    /* ===== 홈(시작) 화면 ===== */
    QWidget#Home {{ background: {c['bg']}; }}
    QLabel#HomeTitle {{ font-size: 36px; font-weight: 800; letter-spacing: -1px; }}
    QLabel#HomeSub {{ font-size: 15px; font-weight: 600; color: {c['ink_soft']}; }}
    QLabel#HomeGuide {{ font-size: 13px; color: {c['ink_muted']}; }}
    QFrame#HomeTiles {{ background: transparent; }}
    QPushButton#HomeTile {{
        background: {c['panel']}; border: 1px solid {c['rule']}; border-radius: 18px; padding: 0px;
    }}
    QPushButton#HomeTile QLabel#BtnText {{ color: {c['ink']}; font-size: 14px; font-weight: 700; }}
    QPushButton#HomeTile:hover {{ background: {c['panel2']}; border: 1px solid {c['accent']}; }}
    QPushButton#HomeTile:pressed {{ background: {c['accent_soft']}; }}
    QPushButton#HomeLink {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px 6px; min-height: 0px; color: {c['ink_soft']}; font-weight: 600; }}
    QPushButton#HomeLink:hover {{ background: {c['panel2']}; color: {c['ink']}; border-color: {c['rule']}; }}
    QPushButton#HomeLink:disabled {{ color: {c['disabled']}; background: transparent; }}

    /* ===== 검사 기록 화면 ===== */
    QListWidget#ExamList {{ background: transparent; border: none; outline: none; }}
    QListWidget#ExamList::item {{
        background: {c['panel2']}; border: 1px solid {c['rule']}; border-radius: 8px;
        padding: 4px 8px; color: {c['ink']};
    }}
    QListWidget#ExamList::item:hover {{ border-color: {c['rule_strong']}; }}
    QListWidget#ExamList::item:selected {{ background: {c['accent_soft']}; border: 1px solid {c['accent']}; color: {c['ink']}; }}
    QLabel#ExamPatient {{ font-size: 13px; }}
    QTableWidget#PatientTable {{
        background: {c['panel']}; alternate-background-color: {c['panel2']}; border: 1px solid {c['rule']};
        border-radius: 10px; gridline-color: {c['rule']}; outline: none;
        selection-background-color: {c['accent_soft']}; selection-color: {c['ink']};
    }}
    QTableWidget#PatientTable::item {{ padding: 0 10px; border: none; }}
    QHeaderView::section {{
        background: {c['panel2']}; color: {c['ink_soft']}; border: none; border-bottom: 1px solid {c['rule']};
        padding: 8px 10px; font-weight: 700; font-size: 12px;
    }}
    QWidget#ExamGrid, QWidget#ExamButtons {{ background: transparent; }}
    QWidget#MultiGrid {{ background: #000000; border-radius: 10px; }}
    QLabel#MultiEmpty {{ color: #8a94a6; background: transparent; }}
    QFrame#MultiCell {{ background: #000000; border: 2px solid transparent; border-radius: 6px; }}
    QFrame#MultiCell[active="true"] {{ border: 2px solid #facc15; }}
    QListWidget#RefStrip {{ background: transparent; border: none; }}
    QLabel#KeyProbe {{ border: 1px dashed {c['rule_strong']}; border-radius: 8px; padding: 8px; }}
    QLabel#KeyProbe:focus {{ border: 1px solid {c['accent']}; }}
    QListWidget#RefStrip::item {{ border: 2px solid transparent; border-radius: 6px; padding: 1px; margin: 1px; }}
    QListWidget#RefStrip::item:hover {{ border: 2px solid {c['rule_strong']}; }}
    QListWidget#RefStrip::item:selected {{ border: 2px solid #facc15; background: transparent; }}
    QPushButton#MultiBtn {{
        background: transparent; border: 1px solid {c['rule']}; border-radius: 7px; padding: 3px 10px;
        min-height: 0px; color: {c['ink_soft']}; font-weight: 700;
    }}
    QPushButton#MultiBtn:hover {{ color: {c['ink']}; border-color: {c['rule_strong']}; }}
    QPushButton#MultiBtn:checked {{ background: {c['accent_soft']}; border-color: {c['accent']}; color: {c['ink']}; }}
    QFrame#Panel QScrollArea, QFrame#Panel QWidget#qt_scrollarea_viewport {{ background: transparent; }}

    QMenuBar {{ background: {c['panel']}; }}
    QMenuBar::item:selected {{ background: {c['panel2']}; }}
    QMenu {{ background: {c['panel']}; border: 1px solid {c['rule_strong']}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['ink']}; }}
    QMenu::item:disabled {{ color: {c['disabled']}; }}
    QMenu::separator {{ height: 1px; background: {c['rule']}; margin: 4px 8px; }}

    /* ===== Explorer 판 (메뉴바 · 툴바 · 환자/검사 트리 · 썸네일 목록) ===== */
    QMenuBar {{ border-bottom: 1px solid {c['rule']}; padding: 2px 6px; }}
    QMenuBar::item {{ background: transparent; padding: 5px 11px; border-radius: 6px; }}
    QToolBar#ExplorerTools {{ background: {c['panel']}; border: none; border-bottom: 1px solid {c['rule']}; padding: 3px 6px; spacing: 2px; }}
    QToolBar#ExplorerTools::separator {{ background: {c['rule']}; width: 1px; margin: 6px 5px; }}
    QToolBar#ExplorerTools QToolButton {{
        background: transparent; border: 1px solid transparent; border-radius: 8px;
        padding: 4px 7px; color: {c['ink_soft']}; font-size: 11px; font-weight: 600; min-width: 46px;
    }}
    QToolBar#ExplorerTools QToolButton:hover {{ background: {c['panel2']}; border-color: {c['rule']}; color: {c['ink']}; }}
    QToolBar#ExplorerTools QToolButton:checked {{ background: {c['accent_soft']}; border-color: {c['accent']}; color: {c['ink']}; }}
    QToolBar#ExplorerTools QToolButton:disabled {{ color: {c['disabled']}; }}
    QDockWidget {{ color: {c['ink_muted']}; font-weight: 700; font-size: 11px; }}
    QDockWidget::title {{ background: {c['panel']}; padding: 7px 10px; border-bottom: 1px solid {c['rule']}; }}
    QFrame#ExplorerSide, QFrame#ExplorerThumbs {{ background: {c['panel']}; }}
    QTreeWidget, QListWidget, QTableWidget {{
        background: {c['field']}; border: 1px solid {c['rule']}; border-radius: 8px; outline: none;
        alternate-background-color: {c['panel2']};
    }}
    QTreeWidget::item, QListWidget::item {{ padding: 4px 2px; border-radius: 5px; }}
    QTreeWidget::item:hover, QListWidget::item:hover {{ background: {c['panel2']}; }}
    QTreeWidget::item:selected, QListWidget::item:selected {{ background: {c['accent_soft']}; color: {c['ink']}; }}
    QListWidget#ThumbList::item {{ padding: 3px; margin: 2px; border: 1px solid transparent; }}
    QListWidget#ThumbList::item:selected {{ border: 2px solid {c['accent']}; }}
    QHeaderView::section {{ background: {c['panel2']}; color: {c['ink_soft']}; border: none; padding: 5px 8px; font-weight: 700; }}
    QLabel#ExplorerInfo {{
        background: {c['panel2']}; border: 1px solid {c['rule']}; border-radius: 8px; padding: 8px 10px; color: {c['ink_soft']};
    }}
    QFrame#ExplorerBar {{ background: {c['panel']}; border: 1px solid {c['rule']}; border-radius: 10px; }}
    QStackedWidget#ExplorerView {{ background: #000000; border-radius: 10px; }}
    QGroupBox {{ border: 1px solid {c['rule']}; border-radius: 8px; margin-top: 14px; padding: 10px 8px 8px; font-weight: 700; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {c['ink_muted']}; }}
    """ + _brand_qss(mode)


def _brand_qss(mode: str = "dark") -> str:
    """브랜드 전용 덧칠 — 헤더(메뉴바·툴바)를 테마와 상관없이 브랜드 색으로, 셔터 링을 브랜드 포인트 색으로."""
    b = _brand.current()
    out = ""
    h = b.get("header")
    k = b.get("k_colors")
    stripe = bool(b.get("stripe") and k)
    if h:
        line = "none" if stripe else f"3px solid {h['line']}"      # 줄무늬가 있으면 줄무늬가 아래 선 역할
        out += f"""
    QMenuBar {{ background: {h['bg']}; color: {h['ink']}; border-bottom: 1px solid {h['rule']}; }}
    QMenuBar::item {{ background: transparent; color: {h['ink']}; }}
    QMenuBar::item:selected, QMenuBar::item:pressed {{ background: {h['hover']}; color: {h['ink']}; }}
    QToolBar#ExplorerTools {{ background: {h['bg']}; border: none; border-bottom: {line}; }}
    QToolBar#ExplorerTools::separator {{ background: {h['rule']}; }}
    QToolBar#ExplorerTools QToolButton {{ color: {h['ink_soft']}; background: transparent; }}
    QToolBar#ExplorerTools QToolButton:hover {{ background: {h['hover']}; border-color: {h['rule']}; color: {h['ink']}; }}
    QToolBar#ExplorerTools QToolButton:checked {{ background: {h['active']}; border-color: {h['accent']}; color: {h['ink']}; }}
    QToolBar#ExplorerTools QToolButton:disabled {{ color: {h['disabled']}; }}
    QToolBar#ExplorerTools QLabel#BrandLogo, QToolBar#ExplorerTools QWidget {{ background: transparent; }}

    QFrame#TopBar {{ background: {h['bg']}; border: none; border-bottom: {line}; }}
    QFrame#TopBar QLabel#BrandLogo, QFrame#TopBar QFrame#PatientPick {{ background: transparent; }}
    QFrame#TopBar QFrame#NavSeg {{ background: {h['seg']}; border: 1px solid {h['rule']}; }}
    QFrame#TopBar QFrame#DxBar {{ background: {h['seg']}; border: 1px solid {h['rule']}; }}
    QFrame#TopBar QPushButton#DxBtn:hover {{ background: {h['hover']}; border-color: {h['rule']}; }}
    QFrame#TopBar QPushButton#DxBtn:checked {{ background: {h['active']}; border: 1px solid {h['accent']}; }}
    QFrame#TopBar QPushButton#NavBtn {{ background: transparent; border-color: transparent; }}
    QFrame#TopBar QPushButton#NavBtn:hover {{ background: {h['hover']}; border-color: {h['rule']}; }}
    QFrame#TopBar QPushButton#NavBtn:pressed {{ background: {h['hover']}; }}
    QFrame#TopBar QPushButton#NavBtn:checked {{ background: {h['active']}; border: 1px solid {h['accent']}; }}
    QFrame#TopBar QLabel#BtnText {{ color: {h['ink_soft']}; }}
    QFrame#TopBar QLabel#BtnText[on="true"] {{ color: {h['ink']}; }}
    QFrame#TopBar QLabel#BtnText:disabled {{ color: {h['disabled']}; }}
    QFrame#TopBar QLabel#PatientName {{ color: {h['ink']}; background: transparent; }}
    QFrame#TopBar QLabel#ChartNo, QFrame#TopBar QLabel#PatientMeta {{ color: {h['ink_soft']}; background: transparent; }}
    """
    if stripe:
        sky, blue, navy, orange = k
        # 로고 K 처럼: 하늘 → 파랑(위 곡선) · 네이비(세로획) · 주황(아래 곡선)
        grad = (f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {sky}, stop:0.6 {blue}, "
                f"stop:0.601 {navy}, stop:0.62 {navy}, stop:0.621 {orange}, stop:1 #f4a462)")
        out += f"""
    QFrame#BrandStripe {{ border: none; min-height: 4px; max-height: 4px; background: {grad}; }}
    QToolBar#ExplorerTools {{ border-bottom: 4px solid; border-bottom-color: {grad}; }}
    QPushButton#HomeTile:hover {{ border: 1px solid {sky}; }}
    QPushButton#HomeTile[kc="0"] {{ border-bottom: 3px solid {sky}; }}
    QPushButton#HomeTile[kc="1"] {{ border-bottom: 3px solid {blue}; }}
    QPushButton#HomeTile[kc="2"] {{ border-bottom: 3px solid {orange}; }}
    QLabel#HomeKicker {{ color: {orange}; font-size: 12px; font-weight: 800; letter-spacing: 2px; }}
    QLabel#HomeSub {{ color: {sky if mode == 'dark' else blue}; }}
    QFrame#HomeRule {{ border: none; min-height: 3px; max-height: 3px; max-width: 72px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {sky}, stop:0.5 {blue}, stop:0.501 {orange}, stop:1 {orange}); }}
    """
    ring = b.get("shutter_ring")
    if ring:
        out += f"""
    QPushButton#Shutter {{ border: 3px solid {ring}; }}
    QPushButton#Shutter:hover {{ border-color: {ring}; }}
    """
    return out


def make_app_icon() -> QIcon:
    """앱 아이콘. 브랜드 아이콘 파일이 있으면 그것을, 없으면 눈(eye) 아이콘을 벡터로 그려 생성."""
    path = _brand.asset(_brand.current().get("icon"))
    if path:
        return QIcon(path)
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_render_icon(size))
    return icon


def _render_icon(size: int, bg: str | None = None) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = size

    # 둥근 사각 배경 (기본 티얼, 브랜드가 icon_bg 로 바꿀 수 있음)
    bg = bg or _brand.current().get("icon_bg", "#0d9488")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(bg)))
    r = s * 0.14
    p.drawRoundedRect(QRectF(0, 0, s, s), r, r)

    # 눈 외곽 (아몬드형) — 흰색 스트로크
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(max(1.5, s * 0.055))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    cx, cy = s / 2, s / 2
    half_w = s * 0.34
    half_h = s * 0.20
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    path.moveTo(cx - half_w, cy)
    path.quadTo(cx, cy - half_h * 2.0, cx + half_w, cy)
    path.quadTo(cx, cy + half_h * 2.0, cx - half_w, cy)
    p.drawPath(path)

    # 홍채
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#ffffff")))
    ir = s * 0.145
    p.drawEllipse(QRectF(cx - ir, cy - ir, ir * 2, ir * 2))
    # 동공 (배경색)
    p.setBrush(QBrush(QColor(bg)))
    pr = s * 0.07
    p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

    p.end()
    return pm

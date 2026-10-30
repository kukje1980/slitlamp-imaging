"""Pro 모드 작업공간 — 전문가용 영상 검토 화면 (VK-2 스타일 워크플로).

표시 모드
  LIVE     실시간 영상. CAPTURE(Space)로 정지/재개, SAVE로 환자 DB 저장
  1화면    단일 영상. PREV/NEXT(←/→)로 이동, 확대·이동, 보정, 텍스트/화살표 주석
  MULTI    16장(4×4) / 36장(6×6) 그리드. 클릭=비교 선택(최대 2), 더블클릭=1화면
  비교     선택한 2장 좌우 비교. 확대·스크롤 동기화, 보정 동일 적용
  grid     촬영 화면의 '멀티' (embedded=True) — 고른 사진 · 참조 사진 격자(app/ui/multi_view.py).
           위 도구줄은 숨기고 오른쪽 메뉴가 누른 칸에 적용된다. 메뉴는 세 개: 보정 · 편집(보기 → 도구 → 필터 → 회전·반전) ·
           분석(측정 종류 하나 고르고 실행 — multi_analysis.py). 실행 취소 · 다시 실행 · 원본 · 주석 지우기 · 편집본 저장은
           메뉴 아래 늘 보이는 줄에. 스마트(LIVE 전용)는 없다. 기본 도구는 돋보기, 참조 사진 칸은 편집본 저장 · 측정 기록 ·
           코멘트에서 빠진다.
스마트 캡처 (사이드 '스마트' 탭 · LIVE)
  초점 판정(중앙 ROI 선명도) · Best Shot(최근 1초 중 가장 선명한 프레임) · Smart Freeze / Auto Save
  포커스 피킹 · 실시간 화질 점검(초점·노출·안정·반사)  — app/capture/pro_smart_capture.py
분석 (사이드 '분석' 탭)
  조명 모드 태그 · 형광 염색 면적 · 충혈 지수 · 경과 비교(고스트 겹치기·위치 정렬·깜빡임 전환)
  모두 측정·기록 보조이며 진단을 하지 않는다 — app/capture/analysis.py
기타
  보정본 저장  보정·주석을 반영한 새 영상으로 저장(원본 보존, 코멘트 '보정본'/'주석')
  코멘트       영상별 코멘트 편집      환자정보  진단명·담당의 등 편집
  삭제         MULTI에서 선택한 영상(최대 2장) / 1화면의 현재 영상을 DB·디스크에서 삭제 (Delete 키)
  인쇄 / PDF   머리말·환자정보·진단명·꼬리말이 포함된 A4 보고서
  분석         영상 품질·형광염색·결막 충혈·마이봄샘·수정체 지수, 이전 영상 자동 정렬,
               가장 선명한 영상 찾기, TBUT(동영상), DICOM 내보내기 (analysis_panel.py)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import brand as app_brand
from app import sounds
from app.capture import analysis, cataract
from app.capture.frame_utils import bgr_to_qimage
from app.capture.image_process import Adjust, apply_adjust, auto_levels, histogram
from app.capture.pro_smart_capture import SmartCaptureEngine, SmartSettings, peaking_mask, roi_rect
from app.db.measurements_dao import Measurement
from app.ui import icons
from app.ui.fit import HScroll
from app.ui.multi_view import MultiView, is_reference
from app.ui.patient_dialog import PatientDialog
from app.ui.pro import print_util
from app.ui.pro.analysis_panel import AnalysisPanel
from app.ui.pro.image_canvas import ImageCanvas
from app.ui.pro.multi_analysis import MultiAnalysisPanel
from app.ui.pro.multi_grid import MultiGrid
from app.ui.pro.smart_panel import PEAKING_COLORS, AnalysisTab, SmartTab
from app.ui.theme import ICON, ICON_ACCENT, ICON_ON_ACCENT, ICON_RECORD

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

log = logging.getLogger(__name__)

MODES = ["live", "single", "multi", "compare", "grid"]
_SMART_MODES = ["off", "freeze", "snapshot"]
_EYE_KO = {"OD": "우안(OD)", "OS": "좌안(OS)", "OU": "양안(OU)"}
_CHANNELS = ["rgb", "green", "red", "blue", "gray"]
_DENOISE = ["none", "median", "average"]
# (도구, 버튼 텍스트, 설명, 허용 모드)
_TOOLS = [
    ("pan", "이동", "이동 / 휠 확대", ("single", "compare", "grid")),
    ("magnify", "돋보기", "누르고 있는 동안 확대 렌즈", ("single", "compare", "live", "grid")),
    ("crop", "자르기", "드래그한 영역만 남김", ("single", "grid")),
    ("text", "텍스트", "클릭한 위치에 텍스트 주석", ("single", "compare", "grid")),
    ("arrow", "화살표", "드래그해 화살표 주석", ("single", "compare", "grid")),
    ("circle", "원", "드래그해 원·타원 표시", ("single", "compare", "grid")),
    ("length", "길이", "드래그해 길이 측정 (눈금 보정 시 mm)", ("single", "compare", "grid")),
    ("angle", "각도", "세 점 클릭 — 두 번째 점이 꼭짓점", ("single", "compare", "grid")),
    ("calibrate", "눈금", "실제 길이를 아는 선을 그어 mm 단위 보정", ("single", "grid")),
]
_TOOL_ICONS = {"pan": "hand", "magnify": "lens", "crop": "crop", "text": "type", "arrow": "arrow",
               "circle": "circle", "length": "ruler", "angle": "angle", "calibrate": "calib"}


def _icon(name: str, color: str = ICON, on: str | None = None) -> QIcon:
    """선형 아이콘. on 을 주면 체크(선택) 상태용 색을 따로 둔다."""
    ic = icons.icon(name, color, 18, disabled="#4a5262")
    if on:
        ic.addPixmap(icons.pixmap(name, on, 18), QIcon.Mode.Normal, QIcon.State.On)
    return ic


def caption_of(cap, number=None, total=None) -> str:
    parts = [cap.captured_at or ""]
    eye = _EYE_KO.get(cap.eye or "", "")
    if eye:
        parts.append(eye)
    if cap.note:
        parts.append(cap.note)
    text = "  ·  ".join(p for p in parts if p)
    return f"[{number}/{total}]  {text}" if number else text


def _mask_qimage(mask, rgb, alpha: int) -> QImage:
    """bool 마스크 → 색을 입힌 반투명 ARGB 이미지 (표시 전용 레이어)."""
    h, w = mask.shape[:2]
    arr = np.zeros((h, w, 4), np.uint8)
    r, g, b = rgb
    arr[mask] = (b, g, r, alpha)                        # ARGB32 = 메모리상 BGRA
    return QImage(arr.data, w, h, 4 * w, QImage.Format.Format_ARGB32).copy()


def load_bgr(path: str | None):
    if not path or cv2 is None or not os.path.exists(path):
        return None
    data = np.fromfile(path, dtype=np.uint8)          # 한글 경로 대응
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


class ProWorkspace(QWidget):
    savedCapture = Signal()
    patientUpdated = Signal(object)
    smartModeChanged = Signal(str)          # LIVE 의 Smart 자동 촬영 모드 (off | freeze | snapshot)

    def __init__(self, db, service, config, get_eye, parent=None, embedded: bool = False):
        """embedded=True: 촬영 화면의 '멀티' — 위 도구줄 없이 사진 격자(grid 모드) + 오른쪽 메뉴만."""
        super().__init__(parent)
        self.setObjectName("ProWorkspace")
        self.db = db
        self.service = service
        self.config = config
        self.get_eye = get_eye
        self.embedded = bool(embedded)
        self._patient = None
        self._caps: list = []
        self._idx = -1
        self._mode = "grid" if self.embedded else "multi"
        self._live = None
        self._frozen = False
        self._frozen_frame = None
        self._pair = (None, None)
        self._adj = Adjust()
        self._tool = "magnify" if self.embedded else "pan"
        # 스마트 캡처 · 분석 상태
        self.engine = SmartCaptureEngine(SmartSettings(
            roi_frac=float(getattr(config, "smart_roi_frac", 0.5) or 0.5),
            auto_mode=getattr(config, "smart_auto_mode", "off") or "off"))
        self._smart_last = None
        self._peak_tick = 0
        self._analysis_mask = None           # 마지막 측정 마스크 (단일 화면 표시용)
        self._analysis_text = ""             # 마지막 측정 결과 (코멘트 기록용)
        self._tag = ""                       # 조명 모드 추정
        self._cat = None                     # 마지막 혼탁 지수 결과 (kind, dict)
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(500)
        self._blink_timer.timeout.connect(self._blink_tick)
        self._blink_on = False
        self._pending_best = False           # Best Shot 버튼·자동 정지로 들어온 정지 요청
        self._build()
        self._cmp_active = self.cmp_left
        for cv in self._canvases():
            cv.edited.connect(lambda cv=cv: self._on_canvas_edited(cv))
            cv.activated.connect(lambda cv=cv: self._on_canvas_activated(cv))
            cv.calibrated.connect(self.set_px_per_mm)
        self.grid_view.activeChanged.connect(lambda _cv: self._on_grid_active())
        self.grid_view.edited.connect(self._on_canvas_edited)
        self.grid_view.calibrated.connect(self.set_px_per_mm)
        self._shortcuts()
        self.set_px_per_mm(getattr(config, "px_per_mm", 0.0) or 0.0)
        self.set_mode(self._mode)
        self._update_info()

    # ================= UI =================
    def _btn(self, text, tip, checkable=False, obj="ProBtn", icon=None, color=ICON) -> QPushButton:
        b = QPushButton(icons.gap(text) if icon else text)
        b.setObjectName(obj)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setMinimumHeight(34)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if icon:
            b.setIcon(_icon(icon, color, on=ICON_ON_ACCENT if checkable else None))
            b.setIconSize(QSize(15, 15))
        return b

    @staticmethod
    def _sep() -> QFrame:
        f = QFrame()
        f.setObjectName("ProSep")
        f.setFixedWidth(1)
        return f

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        bar = QFrame()
        bar.setObjectName("ProToolbar")
        tl = QHBoxLayout(bar)
        tl.setContentsMargins(8, 6, 8, 6)
        tl.setSpacing(4)

        self.b_live = self._btn("LIVE", "실시간 영상", True, "ProSegBtn", "live")
        self.b_single = self._btn("1화면", "단일 영상 보기", True, "ProSegBtn", "single")
        self.b_multi = self._btn("MULTI", "다중 영상 그리드 (16/36)", True, "ProSegBtn", "review")
        self.b_cmp = self._btn("비교", "선택한 2장 나란히 비교", True, "ProSegBtn", "compare")
        self.mode_group = QButtonGroup(self)
        seg = QFrame()
        seg.setObjectName("ProSeg")
        sgl = QHBoxLayout(seg)
        sgl.setContentsMargins(2, 2, 2, 2)
        sgl.setSpacing(2)
        for b, m in ((self.b_live, "live"), (self.b_single, "single"),
                     (self.b_multi, "multi"), (self.b_cmp, "compare")):
            self.mode_group.addButton(b)
            b.setMinimumHeight(30)
            b.clicked.connect(lambda _=False, m=m: self.set_mode(m))
            sgl.addWidget(b)
        tl.addWidget(seg)
        tl.addWidget(self._sep())

        self.b_capture = self._btn("CAPTURE", "라이브 정지/재개 (Space)", True, "ProCapture", "freeze", ICON_ACCENT)
        self.b_save = self._btn("SAVE", "정지 영상을 환자 DB에 저장", icon="save")
        self.c_eye = QComboBox()
        self.c_eye.addItems(["안구 미지정", "OD 우안", "OS 좌안"])
        self.c_eye.setToolTip("저장할 영상의 안구 방향")
        self.c_smart = QComboBox()
        self.c_smart.addItems(["Smart 끄기", "Smart 자동 정지", "Smart 자동 저장"])
        self.c_smart.setToolTip("LIVE 에서 초점이 가장 잘 맞는 순간 자동 정지 / 자동 저장")
        tl.addWidget(self.c_eye)
        tl.addWidget(self.b_capture)
        tl.addWidget(self.b_save)
        tl.addWidget(self.c_smart)
        tl.addWidget(self._sep())

        self.b_prev = self._btn("PREV", "이전 영상 / 페이지 (←)", icon="chev-l")
        self.b_next = self._btn("NEXT", "다음 영상 / 페이지 (→)", icon="chev-r")
        self.b_next.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.c_per = QComboBox()
        self.c_per.addItems(["16장", "36장"])
        self.c_per.setToolTip("MULTI 표시 장수")
        tl.addWidget(self.b_prev)
        tl.addWidget(self.b_next)
        tl.addWidget(self.c_per)
        tl.addWidget(self._sep())

        # 편집 도구·실행 취소·편집본 저장은 사이드 패널 '편집' 탭에 배치 (툴바 폭 절약)
        self.tool_buttons = {}
        self.tool_group = QButtonGroup(self)
        for t, text, tip, _modes in _TOOLS:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setObjectName("ProTool")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setMinimumHeight(50)
            b.setIcon(_icon(_TOOL_ICONS[t], ICON, on=ICON_ON_ACCENT))
            b.setIconSize(QSize(18, 18))
            self.tool_group.addButton(b)
            b.clicked.connect(lambda _=False, t=t: self.set_tool(t))
            self.tool_buttons[t] = b
        self.b_pan, self.b_text, self.b_arrow = (self.tool_buttons[k] for k in ("pan", "text", "arrow"))
        self.b_pan.setChecked(True)
        self.b_rot_l = self._btn("", "반시계 방향 90° 회전", icon="rot-l")
        self.b_rot_r = self._btn("", "시계 방향 90° 회전", icon="rot-r")
        self.b_flip_h = self._btn("", "좌우 반전", icon="flip-h")
        self.b_flip_v = self._btn("", "상하 반전", icon="flip-v")
        self.b_undo = self._btn("실행 취소", "마지막 편집 취소 (Ctrl+Z)", icon="undo")
        self.b_redo = self._btn("다시 실행", "취소한 편집 다시 실행 (Ctrl+Y)", icon="redo")
        self.b_orig = self._btn("원본", "모든 보정·편집을 지우고 원본으로", icon="history")
        self.b_clear = self._btn("주석 지우기", "현재 화면의 주석·측정 모두 삭제", icon="eraser")
        self.b_edit_save = self._btn("편집본 저장", "보정·편집·주석·측정을 반영한 새 영상으로 저장 (원본 보존)",
                                     obj="ProPrimary", icon="save", color=ICON_ON_ACCENT)

        self.b_comment = self._btn("코멘트", "현재 영상 코멘트 편집", obj="ProGhost", icon="comment")
        self.b_patient = self._btn("환자정보", "진단명·담당의 등 환자 정보 편집", obj="ProGhost", icon="user")
        self.b_delete = self._btn("삭제",
                                  "선택한 영상 삭제 (Delete) — "
                                  "MULTI: 선택한 영상, 1화면: 현재 영상",
                                  obj="ProDanger", icon="trash", color=ICON_RECORD)
        for b in (self.b_comment, self.b_patient, self.b_delete):
            tl.addWidget(b)
        tl.addStretch(1)
        self.b_print = self._btn("인쇄", "현재 화면 영상 보고서 인쇄", obj="ProPrimary", icon="printer",
                                 color=ICON_ON_ACCENT)
        self.b_pdf = self._btn("PDF", "PDF 보고서로 저장", icon="download")
        tl.addWidget(self.b_print)
        tl.addWidget(self.b_pdf)
        self.toolbar_scroll = HScroll(bar)          # 좁은 창: 버튼이 겹치지 않고 옆으로 밀어 보기
        self.toolbar_scroll.setVisible(not self.embedded)
        root.addWidget(self.toolbar_scroll)

        body = QHBoxLayout()
        body.setSpacing(8)
        self.stack = QStackedWidget()
        self.stack.setObjectName("ProDisplay")
        self.live_canvas = ImageCanvas()
        self.live_canvas._placeholder = "라이브 영상 대기 중"
        self.single_canvas = ImageCanvas()
        self.multi = MultiGrid()
        cmp = QWidget()
        cl = QHBoxLayout(cmp)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        self.cmp_left = ImageCanvas()
        self.cmp_right = ImageCanvas()
        self.cmp_left.link(self.cmp_right)
        cl.addWidget(self.cmp_left)
        cl.addWidget(self.cmp_right)
        self.grid_view = MultiView()                # grid 모드 (촬영 화면의 멀티)
        for w in (self.live_canvas, self.single_canvas, self.multi, cmp, self.grid_view):
            self.stack.addWidget(w)
        if self.embedded:                           # 쓰지 않는 비교 화면(캔버스 2개)이 창 최소 폭을 키우지 않게
            self.stack.setMinimumSize(self.grid_view.minimumSize())
        body.addWidget(self.stack, 1)
        self.side = self._build_side()
        self.side_toggle = None
        if self.embedded:
            # 멀티: 오른쪽 메뉴(보정 · 편집 · 분석)는 가는 손잡이로 접고 편다 — 기본은 접힘 (config.multi_side_visible)
            self.side_toggle = QToolButton()
            self.side_toggle.setObjectName("ProSideToggle")
            self.side_toggle.setCheckable(True)
            self.side_toggle.setFixedWidth(22)
            self.side_toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
            self.side_toggle.setIconSize(QSize(14, 14))
            self.side_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.side_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            self.side_toggle.clicked.connect(lambda on: self.set_side_visible(on))
            body.addWidget(self.side_toggle)
        body.addWidget(self.side)
        root.addLayout(body, 1)
        if self.embedded:
            self.set_side_visible(bool(getattr(self.config, "multi_side_visible", False)), save=False)

        self.info = QLabel()
        self.info.setObjectName("ProInfo")
        self.info.setTextFormat(Qt.TextFormat.RichText)
        self.info.setVisible(False)          # 환자 요약은 상단 환자 배너에 표시
        root.addWidget(self.info)

        self.b_capture.toggled.connect(self._on_capture_toggled)
        self.c_smart.currentIndexChanged.connect(lambda i: self.smartModeChanged.emit(_SMART_MODES[i]))
        self.b_save.clicked.connect(self.save_live)
        self.b_prev.clicked.connect(lambda: self.step(-1))
        self.b_next.clicked.connect(lambda: self.step(1))
        self.c_per.currentIndexChanged.connect(lambda i: self.set_per_page(36 if i == 1 else 16))
        self.b_clear.clicked.connect(self.clear_annotations)
        self.b_edit_save.clicked.connect(self.save_edited)
        self.b_comment.clicked.connect(self.edit_comment)
        self.b_patient.clicked.connect(self.edit_patient)
        self.b_delete.clicked.connect(self.delete_selected)
        self.b_print.clicked.connect(self.print_current)
        self.b_pdf.clicked.connect(self._pdf_dialog)
        self.multi.openRequested.connect(self.open_index)
        self.multi.selectionChanged.connect(self._on_selection)

    def _build_side(self) -> QFrame:
        side = QFrame()
        side.setObjectName("ProSide")
        side.setFixedWidth(276)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)

        card = QFrame()
        card.setObjectName("ProCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 6, 10, 8)
        cl.setSpacing(2)
        h1 = QLabel("영상 정보")
        h1.setObjectName("PanelHeader")
        cl.addWidget(h1)
        self.lbl_img = QLabel("-")
        self.lbl_img.setWordWrap(True)
        self.lbl_img.setObjectName("Muted")
        cl.addWidget(self.lbl_img)
        self.info_card = card
        card.setVisible(not self.embedded)          # 촬영 화면의 멀티에서는 '영상 정보' 칸을 두지 않음 (메뉴를 바로 위에)
        lay.addWidget(card)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("ProTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._scroll(self._tab_adjust()), "보정")
        if not self.embedded:
            self.tabs.addTab(self._scroll(self._tab_filter()), "필터")
        self.tabs.addTab(self._scroll(self._tab_edit()), "편집")
        self.smart = SmartTab(self.config)
        self.smart.settingsChanged.connect(self._on_smart_settings)
        self.smart.bestShotClicked.connect(self.freeze_best_shot)
        if self.embedded:
            # 멀티: 필터는 편집 탭 안에, 스마트(LIVE 전용)는 없고, 분석 + 정밀 분석은 한 탭(측정 종류 하나 고르고 실행)
            self.smart.hide()
            self.analysis = self.advanced_analysis = MultiAnalysisPanel(self)
            self.tabs.addTab(self._scroll(self.analysis), "분석")
        else:
            self.analysis = AnalysisTab()
            self.tabs.addTab(self._scroll(self.smart), "스마트")
            self.tabs.addTab(self._scroll(self.analysis), "분석")
            self.analysis.fluoClicked.connect(self.measure_fluorescein)
            self.analysis.rednessClicked.connect(self.measure_redness)
            self.analysis.addNoteClicked.connect(self.record_measurement)
            self.analysis.clearClicked.connect(self.clear_analysis)
            self.analysis.ghostChanged.connect(self.set_ghost)
            self.analysis.alignClicked.connect(self.align_pair)
            self.analysis.blinkToggled.connect(self.set_blink)
            self.analysis.nuclearClicked.connect(self.measure_nuclear)
            self.analysis.corticalClicked.connect(self.measure_cortical)
            self.analysis.cataractRecordClicked.connect(self.record_cataract)
            self.analysis.guideToggled.connect(lambda _on: self._update_side())
            self.advanced_analysis = AnalysisPanel(self)
            self.tabs.addTab(self._scroll(self.advanced_analysis), "정밀 분석")
        self.analysis.set_red_sensitivity(int(getattr(self.config, "redness_sensitivity", 5) or 5))
        self.analysis.rednessSensitivityChanged.connect(self._on_red_sensitivity)
        self.analysis.set_enabled_for(self._mode)
        # 탭 6개가 사이드 폭(276px)에 한 줄로 들어가지 않아 스마트·분석·정밀 분석이 화살표 뒤로 숨던 문제 →
        # 기본 탭 줄은 숨기고 3열 버튼 격자로 모든 탭을 항상 보이게 한다 (탭 위젯·순서는 그대로, 멀티는 3개 한 줄)
        self.tabs.tabBar().hide()
        grid = QFrame()
        grid.setObjectName("ProTabGrid")
        gl = QGridLayout(grid)
        gl.setContentsMargins(3, 3, 3, 3)
        gl.setSpacing(2)
        self.tab_buttons = []
        tab_group = QButtonGroup(self)
        for i in range(self.tabs.count()):
            tb = QPushButton(self.tabs.tabText(i))
            tb.setObjectName("ProTabBtn")
            tb.setCheckable(True)
            tb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            tb.clicked.connect(lambda _=False, i=i: self.tabs.setCurrentIndex(i))
            tab_group.addButton(tb)
            gl.addWidget(tb, i // 3, i % 3)
            self.tab_buttons.append(tb)
        self.tabs.currentChanged.connect(lambda i: self.tab_buttons[i].setChecked(True) if 0 <= i < len(self.tab_buttons) else None)
        self.tab_buttons[self.tabs.currentIndex()].setChecked(True)
        lay.addWidget(grid)
        lay.addWidget(self.tabs, 1)

        self.edit_bar = None
        if self.embedded:
            # 멀티: 편집 이력은 어느 메뉴에서든 바로 쓰게 메뉴 아래 늘 보이는 줄에
            self.edit_bar = QFrame()
            self.edit_bar.setObjectName("ProCard")
            bl = QGridLayout(self.edit_bar)
            bl.setContentsMargins(6, 6, 6, 6)
            bl.setSpacing(4)
            bl.addWidget(self.b_undo, 0, 0)
            bl.addWidget(self.b_redo, 0, 1)
            bl.addWidget(self.b_orig, 1, 0)
            bl.addWidget(self.b_clear, 1, 1)
            bl.addWidget(self.b_edit_save, 2, 0, 1, 2)
            lay.addWidget(self.edit_bar)

        self.lbl_sel = QLabel("MULTI에서 영상 2장을 클릭해 선택한 뒤 '비교'를 누르세요.")
        self.lbl_sel.setWordWrap(True)
        self.lbl_sel.setObjectName("Muted")
        self.lbl_sel.setVisible(not self.embedded)
        lay.addWidget(self.lbl_sel)
        return side

    @staticmethod
    def _scroll(w: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(w)
        return sa

    def _slider_row(self, form: QFormLayout, label: str, lo: int, hi: int, val: int, fmt) -> QSlider:
        s = self._slider(lo, hi, val)
        v = QLabel()
        v.setObjectName("Muted")
        v.setFixedWidth(34)
        v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(s, 1)
        row.addWidget(v)
        form.addRow(label, row)
        self._value_labels.append((s, v, fmt))
        return s

    def _tab_adjust(self) -> QWidget:
        w = QWidget()
        w.setObjectName("ProTabPage")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 8, 4, 4)
        lay.setSpacing(6)
        self._value_labels = []
        pct = lambda v: f"{v / 100:.2f}"  # noqa: E731
        form = QFormLayout()
        form.setSpacing(5)
        self.s_bright = self._slider_row(form, "밝기", -100, 100, 0, str)
        self.s_contrast = self._slider_row(form, "대비", 20, 300, 100, pct)
        self.s_gamma = self._slider_row(form, "감마", 20, 300, 100, pct)
        self.s_sat = self._slider_row(form, "채도", 0, 200, 100, pct)
        lay.addLayout(form)

        lay.addWidget(self._sub("색균형 (RGB)"))
        form2 = QFormLayout()
        form2.setSpacing(5)
        self.s_r = self._slider_row(form2, "R", 50, 150, 100, pct)
        self.s_g = self._slider_row(form2, "G", 50, 150, 100, pct)
        self.s_b = self._slider_row(form2, "B", 50, 150, 100, pct)
        for sl, name in ((self.s_r, "SliderR"), (self.s_g, "SliderG"), (self.s_b, "SliderB")):
            sl.setObjectName(name)
        lay.addLayout(form2)

        lay.addWidget(self._sub("레벨 · 히스토그램"))
        self.hist = QLabel()
        self.hist.setFixedSize(236, 64)
        self.hist.setObjectName("Histogram")
        lay.addWidget(self.hist)
        form3 = QFormLayout()
        form3.setSpacing(5)
        self.s_black = self._slider_row(form3, "흑점", 0, 250, 0, str)
        self.s_white = self._slider_row(form3, "백점", 5, 255, 255, str)
        lay.addLayout(form3)

        form4 = QFormLayout()
        form4.setSpacing(5)
        self.c_channel = QComboBox()
        self.c_channel.addItems(["컬러", "Red-free (녹색)", "적색", "청색", "흑백"])
        form4.addRow("채널", self.c_channel)
        self.chk_neg = QCheckBox("네거티브 (NEGA)")
        form4.addRow("", self.chk_neg)
        lay.addLayout(form4)

        self.chk_adj_all = QCheckBox("모든 칸에 같이 적용")
        self.chk_adj_all.setToolTip("멀티 화면: 보정 · 필터를 누른 칸만이 아니라 모든 칸에 같이 적용")
        self.chk_adj_all.setVisible(self.embedded)
        lay.addWidget(self.chk_adj_all)

        r1 = QHBoxLayout()
        self.b_auto = self._btn("자동 대비", "", icon="wand")
        self.b_auto.setToolTip("히스토그램 양끝 0.5%를 잘라 흑·백점 자동 설정")
        self.b_reset = self._btn("초기화", "보정 초기화", icon="history")
        r1.addWidget(self.b_auto)
        r1.addWidget(self.b_reset)
        lay.addLayout(r1)
        lay.addStretch(1)

        for sl in (self.s_bright, self.s_contrast, self.s_gamma, self.s_sat, self.s_r, self.s_g, self.s_b,
                   self.s_black, self.s_white):
            sl.valueChanged.connect(self._on_adjust)
        self.c_channel.currentIndexChanged.connect(self._on_adjust)
        self.chk_neg.toggled.connect(self._on_adjust)
        self.chk_adj_all.toggled.connect(lambda on: self._on_adjust() if on else None)
        self.b_auto.clicked.connect(self.auto_contrast)
        self.b_reset.clicked.connect(self.reset_adjust)
        self._update_value_labels()
        return w

    def _filter_form(self) -> QFormLayout:
        """노이즈 제거 · 선명하게 · 국소 대비 향상 콤보 (Pro 검토는 '필터' 탭, 멀티는 편집 탭 안)."""
        form = QFormLayout()
        form.setSpacing(6)
        self.c_denoise = QComboBox()
        self.c_denoise.addItems(["없음", "Median (3×3)", "평균 (3×3)"])
        self.c_sharpen = QComboBox()
        self.c_sharpen.addItems(["없음", "약", "중", "강"])
        self.c_enhance = QComboBox()
        self.c_enhance.addItems(["없음", "약", "중", "강"])
        form.addRow("노이즈 제거", self.c_denoise)
        form.addRow("선명하게", self.c_sharpen)
        form.addRow("국소 대비 향상", self.c_enhance)
        for c in (self.c_denoise, self.c_sharpen, self.c_enhance):
            c.currentIndexChanged.connect(self._on_adjust)
        return form

    def _tab_filter(self) -> QWidget:
        w = QWidget()
        w.setObjectName("ProTabPage")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 8, 4, 4)
        lay.setSpacing(8)
        lay.addLayout(self._filter_form())
        tip = QLabel("· 노이즈 제거: 어두운 영상의 잡티 감소\n"
                     "· 선명하게: 경계(각막·수정체 단면)를 또렷하게\n"
                     "· 국소 대비 향상: 미세한 밝기 차이(혼탁·부종)를 강조\n"
                     "· 보정 탭의 Red-free는 혈관·출혈 관찰에 유용")
        tip.setWordWrap(True)
        tip.setObjectName("Muted")
        lay.addWidget(tip)
        lay.addStretch(1)
        return w

    def _view_row(self) -> QGridLayout:
        g0 = QGridLayout()
        g0.setSpacing(4)
        self.b_fit = self._btn("맞춤", "모든 칸을 창에 맞춤" if self.embedded else "화면 맞춤", icon="fit")
        self.b_zin = self._btn("확대", "확대", icon="zin")
        self.b_zout = self._btn("축소", "축소", icon="zout")
        g0.addWidget(self.b_fit, 0, 0)
        g0.addWidget(self.b_zin, 0, 1)
        g0.addWidget(self.b_zout, 0, 2)
        return g0

    def _rotate_row(self) -> QGridLayout:
        g1 = QGridLayout()
        g1.setSpacing(4)
        g1.addWidget(self.b_rot_l, 0, 0)
        g1.addWidget(self.b_rot_r, 0, 1)
        g1.addWidget(self.b_flip_h, 0, 2)
        g1.addWidget(self.b_flip_v, 0, 3)
        return g1

    def _tools_block(self) -> QVBoxLayout:
        """도구 3×3 + 눈금 줄."""
        block = QVBoxLayout()
        block.setSpacing(6)
        g2 = QGridLayout()
        g2.setSpacing(4)
        for k, (t, *_rest) in enumerate(_TOOLS):
            g2.addWidget(self.tool_buttons[t], k // 3, k % 3)
        block.addLayout(g2)
        cal = QHBoxLayout()
        self.lbl_calib = QLabel()
        self.lbl_calib.setObjectName("Muted")
        self.lbl_calib.setWordWrap(True)
        self.b_calib_clear = QPushButton("해제")
        self.b_calib_clear.setToolTip("눈금 보정 해제 (px 단위로 측정)")
        self.b_calib_clear.setFixedWidth(52)
        cal.addWidget(self.lbl_calib, 1)
        cal.addWidget(self.b_calib_clear)
        block.addLayout(cal)
        return block

    def _history_grid(self) -> QGridLayout:
        g3 = QGridLayout()
        g3.setSpacing(4)
        g3.addWidget(self.b_undo, 0, 0)
        g3.addWidget(self.b_redo, 0, 1)
        g3.addWidget(self.b_orig, 1, 0)
        g3.addWidget(self.b_clear, 1, 1)
        g3.addWidget(self.b_edit_save, 2, 0, 1, 2)
        return g3

    def _tab_edit(self) -> QWidget:
        w = QWidget()
        w.setObjectName("ProTabPage")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 8, 4, 4)
        lay.setSpacing(6)

        lay.addWidget(self._sub("보기"))
        lay.addLayout(self._view_row())
        if self.embedded:
            # 멀티: 보기 → 도구 → 필터 → 회전 · 반전 (편집 이력은 메뉴 아래 늘 보이는 줄에)
            lay.addWidget(self._sub("도구 (주석 · 측정)"))
            lay.addLayout(self._tools_block())
            lay.addWidget(self._sub("필터"))
            lay.addLayout(self._filter_form())
            tip = QLabel("누른 칸에 적용 — 보정 탭 '모든 칸에 같이 적용' 을 켜면 필터도 모든 칸에")
            tip.setWordWrap(True)
            tip.setObjectName("Muted")
            lay.addWidget(tip)
            lay.addWidget(self._sub("회전 · 반전"))
            lay.addLayout(self._rotate_row())
        else:
            lay.addWidget(self._sub("회전 · 반전"))
            lay.addLayout(self._rotate_row())
            lay.addWidget(self._sub("도구 (주석 · 측정)"))
            lay.addLayout(self._tools_block())
            lay.addWidget(self._sub("편집 이력"))
            lay.addLayout(self._history_grid())
        lay.addStretch(1)

        self.b_fit.clicked.connect(self._fit_all)
        self.b_zin.clicked.connect(lambda: self._zoom(1.25))
        self.b_zout.clicked.connect(lambda: self._zoom(0.8))
        self.b_rot_l.clicked.connect(lambda: self.rotate(-1))
        self.b_rot_r.clicked.connect(lambda: self.rotate(1))
        self.b_flip_h.clicked.connect(lambda: self.flip(True))
        self.b_flip_v.clicked.connect(lambda: self.flip(False))
        self.b_undo.clicked.connect(self.undo)
        self.b_redo.clicked.connect(self.redo)
        self.b_orig.clicked.connect(self.restore_original)
        self.b_calib_clear.clicked.connect(lambda: self.set_px_per_mm(0.0))
        return w

    @staticmethod
    def _sub(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("PanelHeader")
        return lbl

    @staticmethod
    def _slider(lo, hi, val) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        return s

    def _shortcuts(self) -> None:
        keys = [(QKeySequence.StandardKey.Undo, self.undo),
                (QKeySequence.StandardKey.Redo, self.redo),
                (QKeySequence("Ctrl+Y"), self.redo)]
        if not self.embedded:                    # 촬영 화면 안에서는 ←/→/Space 가 촬영 단축키, Esc 는 전체 화면 해제
            keys += [(QKeySequence(Qt.Key.Key_Escape), lambda: self.set_tool(self._default_tool())),
                     (QKeySequence(Qt.Key.Key_Left), lambda: self.step(-1)),
                     (QKeySequence(Qt.Key.Key_Right), lambda: self.step(1)),
                     (QKeySequence(Qt.Key.Key_Space), self._space),
                     (QKeySequence(Qt.Key.Key_Delete), self.delete_selected)]
        for key, fn in keys:
            sc = QShortcut(key, self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    def _space(self) -> None:
        if self._mode == "live":
            self.b_capture.toggle()

    def _default_tool(self) -> str:
        return "magnify" if self._mode == "grid" else "pan"

    # ================= 데이터 =================
    def set_patient(self, patient) -> None:
        self._patient = patient
        self.multi.clear_selection()
        self.reload()
        self._update_info()
        self._update_trend()

    def reload(self, select_last: bool = True) -> None:
        if self._patient is None:
            self._caps = []
        else:
            self._caps = [c for c in self.db.captures.list_by_patient(self._patient.id, order="asc")
                          if c.media_type == "photo"]
        if self.embedded:                        # 멀티 격자는 촬영 화면이 고른 사진으로 채운다
            self._update_info()
            self._update_side()
            return
        self.multi.set_captures(self._caps)
        if select_last or not (0 <= self._idx < len(self._caps)):
            self._idx = len(self._caps) - 1
        self._show_current()
        self._update_info()

    def captures(self) -> list:
        return list(self._caps)

    def current_index(self) -> int:
        return self._idx

    def mode(self) -> str:
        return self._mode

    def set_side_visible(self, on: bool, save: bool = True) -> None:
        """멀티(embedded) 오른쪽 메뉴 보이기 / 접기. 접어도 보정 값 · 편집은 그대로다."""
        if not self.embedded or self.side_toggle is None:
            return
        on = bool(on)
        self.side.setVisible(on)
        self.side_toggle.setChecked(on)
        self.side_toggle.setIcon(icons.icon("chev-r" if on else "chev-l", ICON, 14))
        self.side_toggle.setToolTip("보정 · 편집 · 분석 메뉴 접기" if on else "보정 · 편집 · 분석 메뉴 펴기")
        if save and getattr(self.config, "multi_side_visible", None) != on:
            self.config.multi_side_visible = on
            try:
                self.config.save()
            except Exception:  # noqa: BLE001
                log.warning("multi_side_visible save failed", exc_info=True)

    def side_visible(self) -> bool:
        return self.side.isVisibleTo(self) if self.embedded else True

    def current_capture(self):
        """지금 보고 있는 환자 사진 (1화면 · 멀티의 누른 칸). 참조 사진 · 그 밖의 화면은 None."""
        if self._mode == "single" and 0 <= self._idx < len(self._caps):
            return self._caps[self._idx]
        if self._mode == "grid":
            cap = self.grid_view.active_capture()
            return None if cap is None or is_reference(cap) else cap
        return None

    def set_grid(self, caps: list) -> None:
        """멀티 격자에 놓을 사진 (환자 사진 · RefCapture)."""
        self.grid_view.set_captures(caps)
        self._on_grid_active()

    def _update_info(self) -> None:
        p = self._patient
        if p is None:
            self.info.setText("환자를 선택하세요")
            return
        sex = {"M": "남", "F": "여", "O": "기타"}.get(p.sex or "", "")
        bits = [f"<b>{p.name}</b> ({p.chart_no or '-'})"]
        if sex:
            bits.append(sex)
        if p.birth_date:
            bits.append(p.birth_date)
        bits.append(f"진단명: <b>{p.diagnosis or '-'}</b>")
        bits.append(f"담당의: {p.doctor or '-'}")
        bits.append(f"영상 {len(self._caps)}장")
        self.info.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(bits))

    # ================= 모드 =================
    def set_mode(self, mode: str) -> None:
        if mode == "compare" and not self._prepare_compare():
            mode = self._mode if self._mode != "compare" else "multi"
        self._mode = mode
        self.stack.setCurrentIndex(MODES.index(mode))
        seg = {"live": self.b_live, "single": self.b_single, "multi": self.b_multi,
               "compare": self.b_cmp}.get(mode)
        if seg is not None:
            seg.setChecked(True)
        live = mode == "live"
        self.b_capture.setEnabled(live)
        self.b_save.setEnabled(live)
        self.c_smart.setEnabled(live)
        self.c_per.setEnabled(mode == "multi")
        ann = mode in ("single", "compare", "grid")
        for t, *_x, modes in _TOOLS:
            self.tool_buttons[t].setEnabled(mode in modes)
        for b in (self.b_clear, self.b_edit_save):
            b.setEnabled(ann)
        self.b_comment.setEnabled(mode == "single")
        self._refresh_delete_state()
        if mode == "compare":
            self._cmp_active = self.cmp_left
        if mode == "single":
            self._show_current()
        if live:
            self.live_canvas.set_adjust(self._adj)
            if self._live is not None and not self._frozen:
                self.live_canvas.set_image(self._live, keep_view=True)
        if getattr(self, "_tool", "pan") != "pan" or mode == "grid":
            self.set_tool(self._tool)            # 이 모드에서 못 쓰는 도구면 이동으로
        if not live:
            self.live_canvas.set_hud([], None)
            self.live_canvas.set_overlay(None)
        if mode != "compare":
            self.set_blink(False)
        self.analysis.set_enabled_for(mode)
        self._update_side()
        self._refresh_edit_state()
        self._analysis_view_changed()

    def _show_current(self) -> None:
        if not (0 <= self._idx < len(self._caps)):
            self.single_canvas.set_image(None)
            self._update_side()
            self._refresh_delete_state()
            return
        cap = self._caps[self._idx]
        self.single_canvas.set_image(load_bgr(cap.file_path))
        self.single_canvas.set_adjust(self._adj)
        self.single_canvas.set_caption(caption_of(cap, self._idx + 1, len(self._caps)))
        self._analysis_mask, self._analysis_text = None, ""
        self._last_measure = None
        self.analysis.lbl_result.setText("-")
        self._cat = None
        self.analysis.lbl_cat.setText("-")
        self._update_tag()
        self._update_trend()
        self._update_side()
        self._refresh_edit_state()
        self._refresh_delete_state()
        self._analysis_view_changed()

    def _refresh_delete_state(self) -> None:
        if hasattr(self, "b_delete"):
            self.b_delete.setEnabled(bool(self.delete_targets()))

    def _analysis_view_changed(self) -> None:
        if hasattr(self, "advanced_analysis"):
            self.advanced_analysis.on_view_changed()

    def _update_side(self) -> None:
        if self._mode == "single" and 0 <= self._idx < len(self._caps):
            c = self._caps[self._idx]
            self.lbl_img.setText(
                f"{self._idx + 1} / {len(self._caps)}\n촬영: {c.captured_at}\n"
                f"안구: {_EYE_KO.get(c.eye or '', '미지정')}\n코멘트: {c.note or '-'}")
        elif self._mode == "multi":
            self.lbl_img.setText(
                f"페이지 {self.multi.page() + 1} / {self.multi.page_count()}  ·  "
                f"{self.multi.per_page()}장 보기\n전체 {len(self._caps)}장  ·  선택 {len(self.multi.selection())}장")
        elif self._mode == "compare":
            rows = [f"{'좌' if i == 0 else '우'}: {c.captured_at} {_EYE_KO.get(c.eye or '', '')}"
                    for i, c in enumerate(self._pair) if c]
            self.lbl_img.setText("2장 비교 (확대·이동 동기화)\n" + "\n".join(rows))
        elif self._mode == "grid":
            self.lbl_img.setText(self._grid_info())
        elif self._mode == "live":
            auto = {"freeze": " · Smart Freeze", "save": " · Auto Save"}.get(self.engine.s.auto_mode, "")
            self.lbl_img.setText("LIVE — " + ("정지됨 · SAVE로 저장" if self._frozen else "실시간" + auto))
        else:
            self.lbl_img.setText("영상이 없습니다")

    def _grid_info(self) -> str:
        n = self.grid_view.count()
        cap = self.grid_view.active_capture()
        if cap is None:
            return "멀티 — 오른쪽 목록에서 사진을 고르거나 아래 '참조' 줄에서 참조 사진을 고르세요"
        head = f"멀티 {n}장  ·  {self.grid_view.index_of(self.grid_view.active_canvas()) + 1}번 칸 (노란 테두리)"
        if is_reference(cap):
            r = cap.ref
            kind = "샘플 사진" if getattr(r, "is_sample", False) else "등록한 참조 영상"
            return (f"{head}\n참조 · {kind}\n분류: {r.category or '-'}\n제목: {r.title or '-'}\n"
                    "(참조 사진은 편집본 저장 · 측정 기록에서 빠집니다)")
        return (f"{head}\n촬영: {cap.captured_at}\n안구: {_EYE_KO.get(cap.eye or '', '미지정')}\n"
                f"코멘트: {cap.note or '-'}")

    def _on_grid_active(self) -> None:
        """멀티에서 누른 칸이 바뀜 → 보정 값 · 분석 · 편집 상태를 그 칸 기준으로."""
        if self._mode != "grid":
            return
        cv = self.grid_view.active_canvas()
        if cv is not None:
            self._sync_controls(cv.adjust())
        self.clear_analysis()
        self._cat = None
        self.analysis.lbl_cat.setText("-")
        self.analysis.set_enabled_for("grid", can_record=self.current_capture() is not None)
        self._update_tag()
        self._update_trend()
        self._update_side()
        self._refresh_edit_state()
        self._analysis_view_changed()

    # ================= 탐색 =================
    def step(self, delta: int) -> None:
        if self._mode == "multi":
            self.multi.go_page(delta)
            self._update_side()
        elif self._mode == "single" and self._caps:
            self._idx = min(max(0, self._idx + delta), len(self._caps) - 1)
            self._show_current()

    def open_index(self, idx: int) -> None:
        if 0 <= idx < len(self._caps):
            self._idx = idx
            self.set_mode("single")

    def set_per_page(self, n: int) -> None:
        self.multi.set_per_page(n)
        blk = self.c_per.blockSignals(True)
        self.c_per.setCurrentIndex(1 if n == 36 else 0)
        self.c_per.blockSignals(blk)
        self._update_side()

    def select_for_compare(self, i: int, j: int) -> None:
        self.multi.set_selection([i, j])

    def _on_selection(self, sel: list) -> None:
        self.lbl_sel.setText(
            f"선택 {len(sel)}/2 — 2장이면 '비교', 지우려면 '삭제'(Delete)." if sel
            else "MULTI에서 영상 2장을 클릭해 선택한 뒤 '비교'를 누르세요.")
        self._update_side()
        self._refresh_delete_state()

    # ================= 삭제 =================
    def delete_targets(self) -> list:
        """지금 '삭제'를 누르면 지워질 영상 — MULTI: 선택한 영상, 1화면: 현재 영상."""
        if self._mode == "multi":
            n = len(self._caps)
            return [self._caps[i] for i in sorted(self.multi.selection()) if 0 <= i < n]
        if self._mode == "single" and 0 <= self._idx < len(self._caps):
            return [self._caps[self._idx]]
        return []

    def delete_selected(self, confirm: bool = True) -> int:
        """선택한 영상을 DB·디스크에서 삭제한다. 지워진 장수를 돌려준다."""
        targets = self.delete_targets()
        if not targets:
            if self.isVisible():
                QMessageBox.information(
                    self, "영상 삭제",
                    "삭제할 영상이 없습니다.\n"
                    "MULTI에서 영상을 클릭해 선택하거나, 1화면에서 영상을 열어 주세요.")
            return 0
        if confirm:
            n = len(targets)
            desc = caption_of(targets[0]) if n == 1 else f"{n}장"
            ret = QMessageBox.question(
                self, "영상 삭제",
                f"선택한 영상 {desc}을(를) 삭제하시겠습니까?\n\n"
                "DB 기록과 저장된 파일(썸네일 포함)이 함께 지워지며 되돌릴 수 없습니다.\n"
                "(지정 폴더에 복사된 사본은 남겨 둡니다)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return 0
        try:
            deleted = self.service.delete_captures(targets)
        except Exception as e:  # noqa: BLE001
            log.exception("Pro delete failed")
            QMessageBox.warning(self, "삭제 실패", f"영상을 삭제하지 못했습니다.\n{e}")
            deleted = 0
        self.multi.clear_selection()
        # 1화면: 같은 자리(다음 영상)를, 끝이었다면 마지막 영상을 표시
        self.reload(select_last=False)
        self.savedCapture.emit()                # 기본 화면 썸네일 갱신
        return deleted

    # ================= 비교 =================
    def _prepare_compare(self) -> bool:
        sel = self.multi.selection()
        if len(sel) == 2:
            i, j = sorted(sel)
        elif self._mode == "single" and self._idx > 0:
            i, j = self._idx - 1, self._idx
        else:
            if self.isVisible():
                QMessageBox.information(self, "2장 비교", "MULTI 화면에서 비교할 영상 2장을 클릭해 선택하세요.")
            return False
        a, b = self._caps[i], self._caps[j]
        self._pair = (a, b)
        for cv, cap, num in ((self.cmp_left, a, i + 1), (self.cmp_right, b, j + 1)):
            cv.set_image(load_bgr(cap.file_path))
            cv.set_adjust(self._adj)
            cv.set_caption(caption_of(cap, num, len(self._caps)))
        self.analysis.lbl_cmp.setText("")
        self.set_ghost(self.analysis.s_ghost.value())
        return True

    # ================= 보정 · 편집 대상 =================
    def _canvases(self) -> list:
        return [self.live_canvas, self.single_canvas, self.cmp_left, self.cmp_right] + self.grid_view.canvases()

    def _target_canvases(self) -> list:
        if self._mode == "grid":
            if self.chk_adj_all.isChecked():
                return self.grid_view.canvases()
            cv = self.grid_view.active_canvas()
            return [cv] if cv is not None else []
        return {"single": [self.single_canvas], "compare": [self.cmp_left, self.cmp_right],
                "live": [self.live_canvas]}.get(self._mode, [])

    def active_canvas(self):
        """편집 명령(회전·실행 취소·자동 대비 등)이 적용될 캔버스."""
        if self._mode == "single":
            return self.single_canvas
        if self._mode == "compare":
            return self._cmp_active
        if self._mode == "live":
            return self.live_canvas
        if self._mode == "grid":
            return self.grid_view.active_canvas()
        return None

    def _on_canvas_activated(self, cv) -> None:
        if self._mode == "compare" and cv in (self.cmp_left, self.cmp_right):
            self._cmp_active = cv
            self._refresh_edit_state()

    def _on_canvas_edited(self, cv) -> None:
        if cv is self.active_canvas():
            self._sync_controls(cv.adjust())
        self._refresh_edit_state()

    # ================= 보정 =================
    def _read_controls(self) -> Adjust:
        black = self.s_black.value()
        white = max(self.s_white.value(), black + 5)
        return Adjust(
            brightness=float(self.s_bright.value()),
            contrast=self.s_contrast.value() / 100.0,
            gamma=self.s_gamma.value() / 100.0,
            negative=self.chk_neg.isChecked(),
            channel=_CHANNELS[self.c_channel.currentIndex()],
            saturation=self.s_sat.value() / 100.0,
            r_gain=self.s_r.value() / 100.0,
            g_gain=self.s_g.value() / 100.0,
            b_gain=self.s_b.value() / 100.0,
            black=int(black),
            white=int(white),
            sharpen=self.c_sharpen.currentIndex(),
            denoise=_DENOISE[self.c_denoise.currentIndex()],
            enhance=self.c_enhance.currentIndex(),
        )

    def _control_widgets(self) -> tuple:
        return (self.s_bright, self.s_contrast, self.s_gamma, self.s_sat, self.s_r, self.s_g, self.s_b,
                self.s_black, self.s_white, self.c_channel, self.chk_neg, self.c_denoise, self.c_sharpen,
                self.c_enhance)

    def _sync_controls(self, adj: Adjust) -> None:
        """슬라이더·콤보를 adj 값으로 (시그널 없이) 맞춘다."""
        for w in self._control_widgets():
            w.blockSignals(True)
        self.s_bright.setValue(int(round(adj.brightness)))
        self.s_contrast.setValue(int(round(adj.contrast * 100)))
        self.s_gamma.setValue(int(round(adj.gamma * 100)))
        self.s_sat.setValue(int(round(adj.saturation * 100)))
        self.s_r.setValue(int(round(adj.r_gain * 100)))
        self.s_g.setValue(int(round(adj.g_gain * 100)))
        self.s_b.setValue(int(round(adj.b_gain * 100)))
        self.s_black.setValue(int(adj.black))
        self.s_white.setValue(int(adj.white))
        self.c_channel.setCurrentIndex(_CHANNELS.index(adj.channel))
        self.chk_neg.setChecked(adj.negative)
        self.c_denoise.setCurrentIndex(_DENOISE.index(adj.denoise))
        self.c_sharpen.setCurrentIndex(int(adj.sharpen))
        self.c_enhance.setCurrentIndex(int(adj.enhance))
        for w in self._control_widgets():
            w.blockSignals(False)
        self._adj = adj.copy()
        self._update_value_labels()

    def _update_value_labels(self) -> None:
        for sl, lbl, fmt in getattr(self, "_value_labels", []):
            lbl.setText(fmt(sl.value()))

    def _on_adjust(self, *_) -> None:
        self._adj = self._read_controls()
        self._update_value_labels()
        for cv in self._target_canvases():
            cv.set_adjust(self._adj, record=True)
        self._refresh_edit_state()

    def set_adjust(self, adj: Adjust) -> None:
        self._sync_controls(adj)
        self._on_adjust()

    def adjust(self) -> Adjust:
        return self._adj

    def reset_adjust(self) -> None:
        self.set_adjust(Adjust())

    def auto_contrast(self) -> None:
        """자동 대비: 현재 채널·노이즈 설정 기준 휘도 0.5%~99.5%를 흑·백점으로."""
        cv = self.active_canvas()
        img = cv.image() if cv is not None else None
        if img is None:
            return
        pre = apply_adjust(img, Adjust(channel=self._adj.channel, denoise=self._adj.denoise))
        lo, hi = auto_levels(pre)
        adj = self._adj.copy()
        adj.black, adj.white = lo, hi
        self.set_adjust(adj)

    def _fit_all(self) -> None:
        for cv in self._canvases():
            cv.reset_view()

    def _zoom(self, f: float) -> None:
        if self._mode == "compare":
            self.cmp_left.zoom_by(f)
        elif self._mode == "single":
            self.single_canvas.apply_zoom(f)
        elif self._mode == "live":
            self.live_canvas.apply_zoom(f)
        elif self._mode == "grid" and self.active_canvas() is not None:
            self.active_canvas().apply_zoom(f)

    # ================= 편집 (기하 · 이력) =================
    def rotate(self, k: int) -> None:
        cv = self.active_canvas()
        if self._mode in ("single", "grid") and cv is not None:
            cv.rotate(k)

    def flip(self, horizontal: bool) -> None:
        cv = self.active_canvas()
        if self._mode in ("single", "grid") and cv is not None:
            cv.flip(horizontal)

    def undo(self) -> bool:
        cv = self.active_canvas()
        return bool(cv and cv.undo())

    def redo(self) -> bool:
        cv = self.active_canvas()
        return bool(cv and cv.redo())

    def restore_original(self) -> bool:
        cv = self.active_canvas()
        return bool(cv and cv.restore_original())

    def set_px_per_mm(self, value: float) -> None:
        """눈금 보정값(px/mm) 적용·저장. 0이면 해제(px 단위)."""
        value = max(0.0, float(value or 0.0))
        self.config.px_per_mm = value
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            log.warning("px_per_mm 저장 실패", exc_info=True)
        for cv in self._canvases():
            cv.set_px_per_mm(value)
        self.lbl_calib.setText(f"눈금: 1 mm = {value:.1f} px" if value > 0 else "눈금: 미보정 (px 단위)")
        self.b_calib_clear.setEnabled(value > 0)

    def _refresh_edit_state(self) -> None:
        cv = self.active_canvas()
        has = cv is not None and cv.image() is not None
        edit_mode = self._mode in ("single", "compare", "grid")
        self.b_undo.setEnabled(bool(has and edit_mode and cv.can_undo()))
        self.b_redo.setEnabled(bool(has and edit_mode and cv.can_redo()))
        self.b_orig.setEnabled(bool(has and edit_mode and cv.is_modified()))
        for b in (self.b_rot_l, self.b_rot_r, self.b_flip_h, self.b_flip_v):
            b.setEnabled(bool(has and self._mode in ("single", "grid")))
        if self._mode == "grid":
            self.b_clear.setEnabled(bool(has and cv.has_annotations()))
            self.b_edit_save.setEnabled(any(not is_reference(c) for c, _cv in self.grid_view.modified()))
        self.b_auto.setEnabled(bool(has))
        self._update_hist(cv.image() if has else None)

    def _update_hist(self, img) -> None:
        W, H = self.hist.width(), self.hist.height()
        pm = QPixmap(W, H)
        pm.fill(QColor("#0b1220"))
        if img is not None:
            hh = histogram(img[::4, ::4])
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            peak = max(1, int(np.percentile(hh["lum"], 99.5)))
            path = QPainterPath(QPointF(0, H))
            for i, v in enumerate(hh["lum"]):
                path.lineTo(i * (W - 1) / 255, H - min(1.0, v / peak) * (H - 4))
            path.lineTo(W, H)
            p.fillPath(path, QColor(203, 213, 225, 150))
            for key, col in (("r", "#f87171"), ("g", "#4ade80"), ("b", "#60a5fa")):
                pts = [QPointF(i * (W - 1) / 255, H - min(1.0, v / peak) * (H - 4)) for i, v in enumerate(hh[key])]
                p.setPen(QPen(QColor(col), 1))
                p.drawPolyline(pts)
            p.setPen(QPen(QColor("#facc15"), 1, Qt.PenStyle.DashLine))
            for lv in (self._adj.black, self._adj.white):
                x = lv * (W - 1) / 255
                p.drawLine(QPointF(x, 0), QPointF(x, H))
            p.end()
        self.hist.setPixmap(pm)

    # ================= 주석 · 도구 =================
    def set_tool(self, tool: str) -> None:
        allowed = {t: modes for t, *_x, modes in _TOOLS}
        if tool not in allowed or (self._mode in ("single", "compare", "live", "grid")
                                   and self._mode not in allowed[tool] and tool != "pan"):
            tool = "pan"
        for cv in (self.single_canvas, self.cmp_left, self.cmp_right, self.live_canvas):
            cv.set_tool(tool if cv is not self.live_canvas or tool == "magnify" else "pan")
        self.grid_view.set_tool(tool)
        self.tool_buttons[tool].setChecked(True)
        self._tool = tool

    def clear_annotations(self) -> None:
        if self._mode == "grid":                 # 멀티: 누른 칸만 (다른 칸의 주석은 그대로)
            cv = self.grid_view.active_canvas()
            if cv is not None:
                cv.clear_annotations()
            self._refresh_edit_state()
            return
        for cv in (self.single_canvas, self.cmp_left, self.cmp_right):
            cv.clear_annotations()

    # ================= LIVE =================
    def set_live_frame(self, frame) -> None:
        self._live = frame
        if self._mode != "live" or frame is None:
            return
        res = None
        try:
            res = self.engine.push(frame, time.monotonic())
        except Exception:  # noqa: BLE001
            log.warning("smart capture 분석 실패", exc_info=True)
        self._smart_last = res
        if not self._frozen:
            self.live_canvas.set_image(frame, keep_view=True)
            if res is not None:
                self._show_smart(frame, res)
                if res.triggered and self.config.smart_mode == "off":
                    self._auto_trigger()
        if res is not None:
            self.smart.show_quality(res.quality)

    def _show_smart(self, frame, res) -> None:
        h, w = frame.shape[:2]
        x, y, rw, rh = roi_rect(w, h, self.engine.s.roi_frac)
        rel = res.rel_focus
        col = "#3ccf7a" if rel >= 85 else ("#ffd08a" if rel >= 60 else "#f0524f")
        lines = [(f"초점 {rel:.0f}", col)]
        mode = self.engine.s.auto_mode
        if mode == "freeze":
            lines.append(("AUTO FREEZE", ICON_ACCENT))
        elif mode == "save":
            lines.append(("AUTO SAVE", ICON_ACCENT))
        if res.metrics.blink:
            lines.append(("깜빡임", "#ffd08a"))
        if self.analysis.chk_guide.isChecked():
            lines.append(("백내장 모드 · 슬릿을 중앙 띠에, 45°", "#c9b3ff"))
            cond = self._last_conditions()
            if cond:
                lines.append((f"지난 검사: {cond}", "#c9b3ff"))
            gw = max(8, w // 40)
            self.live_canvas.set_hud(lines, QRectF(w / 2 - gw / 2, h * 0.08, gw, h * 0.84))
            return
        self.live_canvas.set_hud(lines, QRectF(x, y, rw, rh))
        if self.smart.peaking():
            self._peak_tick += 1
            if self._peak_tick % 2 == 0:             # 피킹은 2프레임마다 (영상 표시는 매 프레임)
                self.live_canvas.set_overlay(self._peaking_image(frame), 0.85)
        elif self.live_canvas.has_overlay():
            self.live_canvas.set_overlay(None)

    def _peaking_image(self, frame) -> QImage:
        mask = peaking_mask(frame, self.smart.sensitivity())
        r, g, b = PEAKING_COLORS.get(self.smart.color_key(), PEAKING_COLORS["red"])[1]
        return _mask_qimage(mask > 0, (r, g, b), 230)

    def _on_smart_settings(self) -> None:
        self.engine.s.auto_mode = self.smart.auto_mode()
        self.engine.s.roi_frac = self.smart.roi_frac()
        if not self.smart.peaking():
            self.live_canvas.set_overlay(None)
        self.smart.apply_to_config(self.config)
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            log.warning("smart capture 설정 저장 실패", exc_info=True)

    def _best_frame(self):
        """Best Shot 이 켜져 있으면 최근 1초 중 가장 선명한 프레임, 아니면 현재 프레임."""
        if self.smart.best_shot():
            f, m = self.engine.best()
            if f is not None:
                return f.copy(), m
        return (None if self._live is None else self._live.copy()), None

    def freeze_best_shot(self) -> None:
        if self._mode != "live" or self._live is None:
            return
        if self._frozen:
            self.b_capture.setChecked(False)
        self._pending_best = True
        self.b_capture.setChecked(True)

    def _auto_trigger(self) -> None:
        if self._mode != "live" or self._frozen:
            return
        if self.engine.s.auto_mode == "freeze":
            self._pending_best = True
            self.b_capture.setChecked(True)
            self.smart.log(f"자동 정지 {datetime.now():%H:%M:%S} — 확인 후 SAVE 또는 RESUME")
        elif self.engine.s.auto_mode == "save" and self._patient is not None:
            frame, m = self._best_frame()
            if frame is None:
                return
            try:
                self.service.save_photo(frame, self._patient, eye=self.current_eye(), note="자동 촬영")
            except Exception:  # noqa: BLE001
                log.exception("auto save failed")
                return
            self.reload()
            self.savedCapture.emit()
            self.smart.log(f"자동 저장 {datetime.now():%H:%M:%S} (영상 {len(self._caps)}장)")

    def set_smart_mode(self, mode: str) -> None:
        blk = self.c_smart.blockSignals(True)
        self.c_smart.setCurrentIndex(_SMART_MODES.index(mode) if mode in _SMART_MODES else 0)
        self.c_smart.blockSignals(blk)

    def freeze_with(self, frame) -> None:
        """LIVE 에서 주어진 프레임(Smart Capture 가 고른 가장 선명한 프레임)으로 정지."""
        if frame is None or self._mode != "live" or self._frozen:
            return
        self._live = frame
        self._external_freeze_frame = frame.copy()
        self.b_capture.setChecked(True)

    def is_live_running(self) -> bool:
        return self._mode == "live" and not self._frozen

    def _on_capture_toggled(self, on: bool) -> None:
        self._frozen = on
        if on:
            use_best = getattr(self, "_pending_best", False) or self.smart.best_shot()
            self._pending_best = False
            supplied = getattr(self, "_external_freeze_frame", None)
            self._external_freeze_frame = None
            if supplied is not None:
                self._frozen_frame = supplied
            elif use_best:
                self._frozen_frame, m = self._best_frame()
                if m is not None and self._smart_last is not None:
                    self.live_canvas.set_hud([("FREEZE · Best Shot", "#9ccaff")], None)
            else:
                self._frozen_frame = None if self._live is None else self._live.copy()
            self.live_canvas.set_overlay(None)
            if self._frozen_frame is not None:
                sounds.play("freeze")
                self.live_canvas.set_image(self._frozen_frame, keep_view=True)
            self.b_capture.setText(icons.gap("RESUME"))
            self.b_capture.setIcon(_icon("play", "#ffffff"))
        else:
            self._frozen_frame = None
            self.b_capture.setText(icons.gap("CAPTURE"))
            self.b_capture.setIcon(_icon("freeze", ICON_ACCENT))
        self._update_side()
        self._analysis_view_changed()

    def current_eye(self) -> str:
        if self.get_eye is not None:
            return self.get_eye()
        return ["NA", "OD", "OS"][self.c_eye.currentIndex()]

    def _require_patient(self) -> bool:
        if self._patient is None:
            QMessageBox.information(self, "확인", "환자를 먼저 선택하세요.")
            return False
        return True

    def save_live(self):
        if not self._require_patient():
            return None
        frame = self._frozen_frame if (self._frozen and self._frozen_frame is not None) else self._live
        if frame is None:
            QMessageBox.information(self, "확인", "저장할 영상이 없습니다.")
            return None
        sounds.play("shutter")
        try:
            cap = self.service.save_photo(frame, self._patient, eye=self.current_eye())
        except Exception as e:  # noqa: BLE001
            log.exception("Pro live save failed")
            QMessageBox.warning(self, "저장 실패", f"영상을 저장하지 못했습니다.\n{e}")
            return None
        self.reload()
        self.savedCapture.emit()
        if self._frozen:
            self.b_capture.setChecked(False)
        return cap

    # ================= 분석 (측정 보조) =================
    def _an_canvas(self):
        """분석 탭(측정 보조)이 다루는 캔버스: 1화면 · 멀티의 누른 칸."""
        if self._mode == "single":
            return self.single_canvas
        if self._mode == "grid":
            return self.grid_view.active_canvas()
        return None

    def _an_image(self):
        cv = self._an_canvas()
        return cv.image() if cv is not None else None

    def _update_tag(self) -> None:
        img = self._an_image() if self._mode == "grid" else self.single_canvas.image()
        if img is None:
            self._tag = ""
            self.analysis.lbl_tag.setText("1화면 · 멀티에서 영상을 열면 자동으로 추정합니다.")
            return
        try:
            self._tag = analysis.classify_illumination(img)
        except Exception:  # noqa: BLE001
            self._tag = ""
        name = analysis.ILLUMINATION_LABELS.get(self._tag, "-")
        self.analysis.lbl_tag.setText(f"추정 조명: {name}  (색 분포 기준 · 기록에 함께 저장)")

    def _measure_roi(self):
        """'원' 주석의 마지막 것을 측정 영역으로 (없으면 전체)."""
        cv = self._an_canvas()
        circles = [a for a in (cv.annotations() if cv is not None else []) if a.get("kind") == "circle"]
        if not circles:
            return None
        a = circles[-1]
        return (a["x1"], a["y1"], a["x2"], a["y2"])

    def measure_fluorescein(self) -> dict | None:
        img = self._an_image()
        if img is None:
            return None
        r = analysis.fluorescein_area(img, self._measure_roi(), self.config.px_per_mm or 0.0)
        mm = f", {r['area_mm2']:.2f} mm²" if r["area_mm2"] is not None else " (눈금 보정 시 mm²)"
        area = "측정 영역" if self._measure_roi() else "전체"
        self._analysis_text = f"형광 염색 면적 {r['ratio']:.1f}% ({area}){mm}"
        self._show_result(r["mask"], (52, 211, 153))
        self._last_measure = "fluo"
        return r

    def measure_redness(self) -> dict | None:
        img = self._an_image()
        if img is None:
            return None
        r = analysis.redness_index(img, self._measure_roi(), self.analysis.s_red_sens.value())
        area = "측정 영역" if self._measure_roi() else "전체"
        self._analysis_text = (f"충혈 지수 {r['index']:.0f}/100 · 혈관 화소 {r['vessel_pct']:.1f}% "
                               f"({area} · 민감도 {r['sensitivity']})")
        self._show_result(r["mask"], (255, 59, 48), alpha=115)   # 옅게 — 아래 조직이 보이게
        self._last_measure = "redness"
        if r["vessel_pct"] >= 40:
            self.analysis.lbl_result.setText(
                self.analysis.lbl_result.text() + "\n붉은 표시가 넓으면 민감도를 낮추거나 '원' 으로 결막만 둘러 측정하세요")
        return r

    def _on_red_sensitivity(self, v: int) -> None:
        """충혈 민감도: 저장하고, 방금 충혈 지수를 쟀으면 바로 다시 잰다."""
        self.config.redness_sensitivity = int(v)
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            log.warning("redness_sensitivity 저장 실패", exc_info=True)
        if getattr(self, "_last_measure", None) == "redness" and self._an_image() is not None:
            self.measure_redness()

    def _show_result(self, mask, rgb, alpha: int = 150) -> None:
        self._analysis_mask = mask
        cv = self._an_canvas()
        if cv is not None:
            cv.set_overlay(_mask_qimage(mask, rgb, alpha), 1.0)
        self.analysis.lbl_result.setText(self._analysis_text + "\n(표시 레이어는 저장본에 안 들어감)")

    def clear_analysis(self) -> None:
        self._analysis_mask, self._analysis_text = None, ""
        self._last_measure = None
        for cv in [self.single_canvas] + self.grid_view.canvases():
            cv.set_overlay(None)
        self.analysis.lbl_result.setText("-")

    def record_measurement(self) -> None:
        cap = self.current_capture()
        if cap is None or not self._analysis_text:
            return
        tag = analysis.ILLUMINATION_LABELS.get(self._tag)
        entry = f"[{tag}] {self._analysis_text}" if tag else self._analysis_text
        note = f"{cap.note} | {entry}" if cap.note else entry
        self.set_comment(note)
        self.analysis.lbl_result.setText(self._analysis_text + "\n코멘트에 기록했습니다.")

    # ---- 백내장: 수정체 혼탁 지수 ----
    def _last_conditions(self) -> str:
        if self._patient is None:
            return ""
        eye = self.current_eye()
        for kind in ("nuclear", "cortical"):
            m = self.db.measurements.latest(self._patient.id, kind, eye if eye != "NA" else None)
            if m and m.conditions:
                return m.conditions
        return ""

    def measure_nuclear(self) -> dict | None:
        img = self._an_image()
        if img is None:
            return None
        r = cataract.nuclear_opacity(img, self._measure_roi())
        self._cat = ("nuclear", r)
        if r["lens_band"] is None:
            self.analysis.lbl_cat.setText("슬릿 띠를 찾지 못했습니다 — 슬릿 단면(광학 절단면) 사진인지, "
                                          "'원'으로 측정 영역을 좁혀 보세요.")
            return r
        ref = "각막 기준" if r["has_cornea"] else "각막 띠 없음 · 절대 밝기 참고값"
        self._analysis_text = f"핵 혼탁 지수 {r['index']:.0f} (수정체/각막 밝기비 {r['ratio']:.2f}, {ref})"
        self._show_result(r["mask"], (201, 179, 255))
        self.analysis.lbl_cat.setText(self._analysis_text + self._trend_delta("nuclear", r["index"]))
        return r

    def measure_cortical(self) -> dict | None:
        img = self._an_image()
        if img is None:
            return None
        pupil = self._measure_roi()
        if pupil is None:
            self.analysis.lbl_cat.setText("편집 탭 '원' 도구로 동공을 먼저 표시하세요 (역조명 사진).")
            return None
        r = cataract.cortical_opacity(img, pupil, self.config.px_per_mm or 0.0)
        self._cat = ("cortical", r)
        d = (f"동공 {r['pupil_diameter_mm']:.1f} mm" if r["pupil_diameter_mm"] is not None
             else f"동공 {r['pupil_diameter_px']:.0f} px")
        warn = " · 산동 부족 → 참고용" if r["small_pupil"] else ""
        self._analysis_text = f"피질 혼탁 지수 {r['index']:.0f} (어두운 얼룩 {r['area_pct']:.1f}%, {d}{warn})"
        self._show_result(r["mask"], (255, 59, 48))
        self.analysis.lbl_cat.setText(self._analysis_text + self._trend_delta("cortical", r["index"]))
        return r

    def _trend_delta(self, kind: str, value: float) -> str:
        if self._patient is None:
            return ""
        cap = self.current_capture()
        eye = (cap.eye if cap and cap.eye and cap.eye != "NA" else None)
        prev = [m for m in self.db.measurements.list_by_patient(self._patient.id, kind, eye)
                if not (cap and m.capture_id == cap.id)]
        if not prev:
            return ""
        d = value - prev[-1].value
        return f"\n지난 검사({prev[-1].measured_at[:10]}) 대비 {d:+.0f}"

    def record_cataract(self):
        cap = self.current_capture()
        if self._cat is None or cap is None:
            QMessageBox.information(self, "측정 기록", "먼저 '핵 혼탁' 또는 '피질 혼탁'을 측정하세요."
                                    + ("\n(참조 사진은 기록하지 않습니다)" if self._cat is not None else ""))
            return None
        kind, r = self._cat
        detail = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()
                  if k != "mask" and v is not None and not isinstance(v, (tuple, list))}
        m = Measurement(patient_id=self._patient.id, capture_id=cap.id, eye=cap.eye or "NA", kind=kind,
                        value=float(r["index"]), detail=detail, locs=self.analysis.locs_values(),
                        conditions=self.analysis.e_cond.text().strip())
        self.db.measurements.insert(m)
        parts = [self._analysis_text]
        if m.conditions:
            parts.append(f"조건: {m.conditions}")
        if m.locs:
            parts.append("LOCS " + " ".join(f"{k}{v}" for k, v in m.locs.items()))
        entry = " · ".join(parts)
        tag = analysis.ILLUMINATION_LABELS.get(self._tag)
        entry = f"[{tag}] {entry}" if tag else entry
        self.set_comment(f"{cap.note} | {entry}" if cap.note else entry)
        self._update_trend()
        self.analysis.lbl_cat.setText(self._analysis_text + "\n측정 기록에 저장했습니다.")
        return m

    def _update_trend(self) -> None:
        """환자·안구별 혼탁 지수 경과 그래프 (핵 = 보라, 피질 = 빨강)."""
        lbl = self.analysis.trend
        W, H = lbl.width(), lbl.height()
        pm = QPixmap(W, H)
        pm.fill(QColor("#0b1220"))
        text = ""
        if self._patient is not None:
            cap = self.current_capture()
            eye = cap.eye if cap and cap.eye and cap.eye != "NA" else None
            series = {k: self.db.measurements.list_by_patient(self._patient.id, k, eye)
                      for k in ("nuclear", "cortical")}
            n = sum(len(v) for v in series.values())
            if n:
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setPen(QPen(QColor("#334155"), 1))
                for frac in (0.25, 0.5, 0.75):
                    p.drawLine(QPointF(0, H * frac), QPointF(W, H * frac))
                for kind, col in (("nuclear", "#c9b3ff"), ("cortical", "#f87171")):
                    ms = series[kind]
                    if not ms:
                        continue
                    k = max(1, len(ms) - 1)
                    pts = [QPointF(8 + i * (W - 16) / k, H - 6 - (H - 12) * min(100.0, m.value) / 100)
                           for i, m in enumerate(ms)]
                    p.setPen(QPen(QColor(col), 2))
                    if len(pts) > 1:
                        p.drawPolyline(pts)
                    p.setBrush(QColor(col))
                    for pt in pts:
                        p.drawEllipse(pt, 3, 3)
                p.end()
                last = {k: v[-1] for k, v in series.items() if v}
                text = "  ·  ".join(f"{'핵' if k == 'nuclear' else '피질'} {m.value:.0f} ({m.measured_at[:10]})"
                                   for k, m in last.items())
                text += f"  ·  기록 {n}건" + (f" · {eye}" if eye else "")
        lbl.setPixmap(pm)
        self.analysis.lbl_trend.setText(text or "측정 기록이 없습니다 (같은 안구 · 같은 촬영 조건끼리 비교)")

    # ---- 경과 비교 ----
    def set_ghost(self, percent: int) -> None:
        if self._mode != "compare" or self.cmp_left.image() is None or self.cmp_right.image() is None:
            return
        if percent <= 0 and not self._blink_timer.isActive():
            self.cmp_right.set_overlay(None)
            return
        if not self.cmp_right.has_overlay():
            self.cmp_right.set_overlay(bgr_to_qimage(self.cmp_left.processed()), percent / 100.0)
        else:
            self.cmp_right.set_overlay_opacity(percent / 100.0)

    def align_pair(self) -> bool:
        a, b = self.cmp_left.image(), self.cmp_right.image()
        if self._mode != "compare" or a is None or b is None:
            return False
        aligned, ok = analysis.align_to(a, b)
        self.cmp_left.set_image(aligned, keep_view=True)
        self.cmp_left.set_adjust(self._adj)
        self.cmp_left.set_caption(caption_of(self._pair[0]) + "  ·  정렬됨" if self._pair[0] else "정렬됨")
        self.analysis.lbl_cmp.setText("이전 영상을 현재 영상에 맞춰 정렬했습니다." if ok
                                      else "정렬 실패 — 두 영상의 겹치는 구조가 부족합니다 (크기만 맞춤).")
        self.cmp_right.set_overlay(None)
        self.set_ghost(self.analysis.s_ghost.value())
        return ok

    def set_blink(self, on: bool) -> None:
        if on and self._mode == "compare" and self.cmp_right.image() is not None:
            self._blink_on = False
            self._blink_timer.start()
            self._blink_tick()
        else:
            self._blink_timer.stop()
            if self.analysis.b_blink.isChecked():
                self.analysis.b_blink.setChecked(False)
            if self._mode == "compare":
                self.cmp_right.set_overlay(None)
                self.set_ghost(self.analysis.s_ghost.value())

    def _blink_tick(self) -> None:
        if self._mode != "compare" or self.cmp_left.processed() is None:
            self.set_blink(False)
            return
        self._blink_on = not self._blink_on
        if not self.cmp_right.has_overlay():
            self.cmp_right.set_overlay(bgr_to_qimage(self.cmp_left.processed()), 1.0)
        self.cmp_right.set_overlay_opacity(1.0 if self._blink_on else 0.0)

    # ================= 편집 저장 / 코멘트 / 환자 =================
    def save_edited(self) -> list:
        if not self._require_patient():
            return []
        if self._mode == "single" and 0 <= self._idx < len(self._caps):
            targets = [(self.single_canvas, self._caps[self._idx])]
        elif self._mode == "compare":
            targets = [(self.cmp_left, self._pair[0]), (self.cmp_right, self._pair[1])]
        elif self._mode == "grid":               # 멀티: 편집한 칸마다 (참조 사진은 환자 기록이 아니라 뺌)
            targets = [(cv, cap) for cap, cv in self.grid_view.modified() if not is_reference(cap)]
        else:
            targets = []
        saved = []
        for cv, src in targets:
            if cv.image() is None or src is None:
                continue
            if not cv.is_modified():
                continue
            kinds = cv.annotation_kinds()
            if any(k in ("length", "angle") for k in kinds):
                note = "측정"
            elif kinds:
                note = "주석"
            elif cv.geometry_changed():
                note = "편집본"
            else:
                note = "보정본"
            try:
                saved.append(self.service.save_photo(cv.render_to_bgr(), self._patient,
                                                     eye=src.eye or "NA", note=note))
            except Exception as e:  # noqa: BLE001
                log.exception("Pro edited save failed")
                QMessageBox.warning(self, "저장 실패", f"편집본을 저장하지 못했습니다.\n{e}")
                break
        if not saved:
            if not any(cv.is_modified() for cv, _ in targets if cv.image() is not None):
                QMessageBox.information(self, "편집본 저장", "보정·편집·주석이 없어 저장할 내용이 없습니다.")
            return []
        if self._mode == "grid":                 # 같은 편집을 두 번 저장하지 않게 저장한 칸만 원본으로
            self.grid_view.reset_edits([src.id for _cv, src in targets[:len(saved)]])
            cv = self.grid_view.active_canvas()
            self._sync_controls(cv.adjust() if cv is not None else Adjust())
            self._refresh_edit_state()
            self.reload(select_last=False)
            self.savedCapture.emit()
            return saved
        self.clear_annotations()
        self.reset_adjust()                       # 저장본엔 보정이 이미 반영됨 → 이중 적용 방지
        self.reload(select_last=(self._mode == "single"))
        self.savedCapture.emit()
        return saved

    def edit_comment(self) -> None:
        cap = self.current_capture()
        if cap is None:
            return
        text, ok = QInputDialog.getText(self, "코멘트", "이 영상의 코멘트:", text=cap.note or "")
        if ok:
            self.set_comment(text)

    def set_comment(self, text: str) -> None:
        cap = self.current_capture()
        if cap is None and self._mode != "grid" and 0 <= self._idx < len(self._caps):
            cap = self._caps[self._idx]
        if cap is None:
            return
        note = (text or "").strip() or None
        self.db.captures.update_note(cap.id, note)
        cap.note = note
        if self._mode == "grid":
            self._update_side()
        else:
            self.multi.set_captures(self._caps, keep_page=True)
            self._show_current()
        self.savedCapture.emit()

    def edit_patient(self) -> None:
        if not self._require_patient():
            return
        dlg = PatientDialog(self, patient=self._patient, db=self.db)
        if not dlg.exec():
            return
        p = dlg.get_patient()
        try:
            self.db.patients.update(p)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "오류", f"수정 실패: {e}")
            return
        self._patient = p
        self._update_info()
        self.patientUpdated.emit(p)

    # ================= 인쇄 =================
    def print_items(self) -> list:
        if self._mode == "single" and self.single_canvas.image() is not None:
            return [(self.single_canvas.render_to_bgr(), caption_of(self._caps[self._idx]))]
        if self._mode == "compare":
            return [(cv.render_to_bgr(), caption_of(c))
                    for cv, c in ((self.cmp_left, self._pair[0]), (self.cmp_right, self._pair[1]))
                    if cv.image() is not None and c is not None]
        if self._mode == "live" and self.live_canvas.image() is not None:
            return [(self.live_canvas.render_to_bgr(), "LIVE " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))]
        if self._mode == "multi":
            items = []
            for i in (self.multi.selection() or self.multi.page_indices()):
                img = load_bgr(self._caps[i].file_path)
                if img is not None:
                    items.append((apply_adjust(img, self._adj), caption_of(self._caps[i])))
            return items
        return []

    def export_pdf(self, path: str) -> int:
        items = self.print_items()
        if not items:
            return 0
        printer = print_util.make_pdf_printer(path)
        return print_util.render_report(printer, self._patient, items,
                                        clinic=getattr(self.config, "clinic_name", "") or "",
                                        title=app_brand.current()["report_title"],
                                        rule_color=app_brand.current().get("report_rule", "#0d9488"))

    def _default_pdf_path(self) -> str:
        base = getattr(self.config, "export_root", "") or os.path.expanduser("~")
        chart = (self._patient.chart_no if self._patient else None) or "report"
        return os.path.join(base, f"{chart}_{datetime.now():%Y%m%d_%H%M}.pdf")

    def _pdf_dialog(self) -> None:
        if not self.print_items():
            QMessageBox.information(self, "PDF", "출력할 영상이 없습니다.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "PDF 보고서 저장", self._default_pdf_path(), "PDF (*.pdf)")
        if path:
            n = self.export_pdf(path)
            QMessageBox.information(self, "PDF", f"{n}쪽 보고서를 저장했습니다.\n{path}")

    def print_current(self) -> None:
        items = self.print_items()
        if not items:
            QMessageBox.information(self, "인쇄", "출력할 영상이 없습니다.")
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if QPrintDialog(printer, self).exec():
            print_util.render_report(printer, self._patient, items,
                                     clinic=getattr(self.config, "clinic_name", "") or "",
                                     title=app_brand.current()["report_title"],
                                     rule_color=app_brand.current().get("report_rule", "#0d9488"))

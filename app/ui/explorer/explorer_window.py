"""Explorer 판 메인 창 — 메뉴바 · 툴바 · 환자 › 검사 트리 · 라이브/영상 보기 · 썸네일 목록.

화면 구성
  왼쪽   환자 › 검사 트리 (검색: 이름·차트번호·진단명·병력·소견) + 환자/검사 정보
  가운데 라이브 영상(HUD) · 분할 정지영상(2·4·6·8·16칸) · 고른 영상 크게 보기
         + 촬영 막대(안구 · 분할 · 정지영상 · 캡처 · 녹화)
  아래   썸네일 목록 (Ctrl/Shift 로 여러 장 선택) — 보기 메뉴에서 오른쪽 세로 칸으로 바꿀 수 있음
메뉴
  파일    환자·검사 추가/수정/삭제, 영상 가져오기/내보내기/삭제, DB 백업/복원, 설정, 단축키
  카메라  라이브, 사진 캡처, 녹화, 정지영상, 분할(칸 채우기·저장), 카메라 속성(노출·게인·WB), 재연결
  영상    보정·측정(Pro), 겹쳐보기, 비교, 참조 영상 등록, 참조 라이브러리, 슬라이드쇼, 보고서(Word/PDF)
  보기    테마, 전체 화면, 영상 목록 세로(오른쪽)
캡처한 영상은 트리에서 고른 검사에 들어간다 (환자만 골랐으면 오늘 검사가 자동으로 만들어짐).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app import brand as app_brand
from app import paths, shortcuts, sounds
from app.capture.camera_controller import CameraController
from app.services.backup_service import BackupManager
from app.services.capture_service import CaptureService
from app.ui import icons, split_menu
from app.ui.control_bar import tool_button
from app.ui.explorer.camera_props_dialog import CameraPropsDialog
from app.ui.explorer.compare_dialog import MAX_ITEMS, CompareDialog
from app.ui.explorer.dialogs import ExamDialog, ReferenceDialog
from app.ui.explorer.overlay_dialog import OverlayDialog
from app.ui.explorer.patient_tree import PatientTree
from app.ui.explorer.pro_dialog import ProDialog
from app.ui.explorer.reference_library import ReferenceLibraryDialog
from app.ui.explorer.report_dialog import ReportDialog
from app.ui.explorer.slideshow import SlideShow
from app.ui.explorer.thumb_list import ThumbList
from app.ui.image_viewer import open_in_default_player
from app.ui.patient_dialog import PatientDialog
from app.ui.pro.image_canvas import ImageCanvas
from app.ui.pro.pro_workspace import caption_of, load_bgr
from app.ui.quad_view import SPLIT_COUNTS, QuadView
from app.ui.backup_tab import BackupStatusButton
from app.ui.settings_dialog import SettingsDialog
from app.ui.shortcuts_dialog import ShortcutsDialog
from app.ui.theme import ICON, ICON_ACCENT, ICON_ON_ACCENT, ICON_RECORD, build_qss, make_app_icon
from app.ui.video_view import VideoView

log = logging.getLogger(__name__)

_EYES = [("OD", "OD", "우안"), ("OS", "OS", "좌안"), ("OU", "OU", "양안"), ("NA", "—", "미지정")]
_IMAGE_FILTER = "이미지 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)"


class ExplorerWindow(QMainWindow):
    def __init__(self, config, db):
        super().__init__()
        self.config = config
        self.db = db
        self.service = CaptureService(db, config)
        # 백업: 외장 하드 · 다른 드라이브로 저장 즉시 · 주기적 · 수동 (촬영 창과 같은 설정 · 같은 백업 폴더)
        self.backup = BackupManager(config, db, parent=self)
        self.service.on_saved.append(self.backup.notify_saved)
        self.brand = app_brand.current()
        self.setWindowTitle(self.brand["window_title"])
        self.setWindowIcon(make_app_icon())
        self.resize(1440, 900)

        self.patient = None
        self.exam = None
        self.eye = "NA"
        self._paused = False                     # 단일 화면 정지영상 (누르면 멈춤 · 다시 누르면 라이브)
        self._freeze = None
        self._split = False                      # 분할 정지영상 (2 · 4 · 6 · 8 · 16칸)
        self._fullscreen = False
        self._rec_patient = None
        self._rec_pending = None                   # 인코더가 아직 닫는 중인 녹화 (닫힌 뒤 DB·내보내기 기록)
        self._rec_exam_id = None
        self._rec_eye = "NA"
        self._rec_path = None
        self._rec_ts = None
        self._windows: list = []                 # 열어 둔 비모달 창 (가비지 컬렉션 방지)

        self.camera = CameraController(config, self)
        self.camera.frame.connect(self._on_frame)
        self.camera.opened.connect(lambda w, h, fps: self._status(f"카메라 {w}x{h} @ {fps:.0f}fps"))
        self.camera.error.connect(self._on_camera_error)
        self.camera.recorder_error.connect(self._on_recorder_error)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_central()
        self._build_docks()
        self.setStatusBar(QStatusBar())
        self.btn_backup = BackupStatusButton()
        self.btn_backup.clicked.connect(lambda: self.open_settings("백업"))
        self.statusBar().addPermanentWidget(self.btn_backup)
        self.backup.statusChanged.connect(self.btn_backup.set_status)
        self.btn_backup.set_status(self.backup.status())
        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(500)
        self._rec_timer.timeout.connect(self._update_rec_hud)

        self._install_shortcuts()
        self.tree.selectionChanged.connect(self._on_tree_selection)
        self._on_tree_selection(self.tree.current_patient(), self.tree.current_exam())
        self.set_view("live")
        sounds.set_enabled(getattr(config, "sounds_enabled", True))
        sounds.preload()
        self.camera.start()
        warn = self.service.low_space_warning()
        if warn:
            self._status(warn, 15000)
        self.backup.start()

    # ================= 동작(QAction) =================
    def _act(self, text, icon=None, slot=None, tip="", checkable=False, key=None) -> QAction:
        a = QAction(text, self)
        if icon:
            a.setIcon(icons.icon(icon, ICON, 20))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        if key:
            a.setShortcut(QKeySequence(key))
        if slot:
            (a.toggled if checkable else a.triggered).connect(slot)
        return a

    def _build_actions(self) -> None:
        A = self._act
        # 파일
        self.act_new_patient = A("새 환자", "plus", self.new_patient, "새 환자 등록")
        self.act_edit_patient = A("환자 정보 수정", "edit", self.edit_patient, "선택한 환자 정보 수정")
        self.act_del_patient = A("환자 삭제", "trash", self.delete_patient, "환자와 영상 기록 삭제")
        self.act_new_exam = A("새 검사", "folder", self.new_exam, "선택한 환자에게 새 검사(내원) 추가")
        self.act_edit_exam = A("검사 정보 수정", "edit", self.edit_exam, "검사일·검사명·소견·메모 수정")
        self.act_del_exam = A("검사 삭제", "trash", self.delete_exam, "검사와 그 영상 삭제")
        self.act_import = A("영상 가져오기…", "download", self.import_images, "다른 장비·폴더의 사진을 이 검사로 가져오기")
        self.act_export = A("선택 영상 내보내기…", "folder", self.export_selected, "선택한 영상을 폴더로 복사")
        self.act_samples = A("샘플 사진 가져오기…", "image", lambda: self.import_samples(),
                             "받아 둔 질환별 샘플 사진 폴더를 등록 — 참조 영상 라이브러리에 질환별로 보입니다")
        self.act_delete_images = A("선택 영상 삭제", "trash", self.delete_selected, "선택한 영상 삭제")
        self.act_backup_drive = A("외장 하드로 지금 백업", "save", self.backup_to_drive,
                                  "사진 · 동영상 · 환자 기록을 설정 → 백업 의 위치(외장 하드 · 다른 드라이브)에 백업")
        self.act_backup = A("DB 백업…", "save", self.backup_db, "환자·검사·영상 기록을 백업 파일로 저장")
        self.act_restore = A("DB 복원…", "history", self.restore_db, "백업 파일에서 기록 복원")
        self.act_settings = A("설정…", "sliders", self.open_settings, "카메라·저장 위치·형식 설정")
        self.act_shortcuts = A("단축키…", "keyboard", self.open_shortcuts, "단축키 설정")
        self.act_quit = A("종료", None, self.close, key="Ctrl+Q")
        # 카메라
        self.act_live = A("라이브", "live", lambda: self.set_view("live"), "실시간 영상 보기")
        self.act_capture = A("캡처", "camera", self.capture, "사진 캡처 (선택한 검사에 저장)")
        self.act_record = A("녹화", "rec", self._on_record_toggled, "동영상 녹화 시작 / 중지", checkable=True)
        self.act_freeze = A("정지영상", "freeze", self.freeze,
                            "누르면 화면 멈춤 · 다시 누르면 라이브 (분할에서는 칸 채우기)")
        self.act_split = A("분할", "quad", self._on_split_toggled,
                           "분할 정지영상 — 2 · 4 · 6 · 8 · 16칸 중 골라 칸마다 정지영상 채우기", checkable=True)
        self.act_split_save = A("분할 저장", "save", self.save_split, "분할 칸별 영상 + 합성 영상을 이 검사에 저장")
        self.act_split_count = A("분할 칸 수 바꾸기…", "quad", self.change_split_count,
                                 "2 · 4 · 6 · 8 · 16칸 중 다시 고르기 (고른 칸 수는 저장되어 분할을 누르면 바로 시작)")
        self.act_props = A("카메라 속성", "aperture", self.open_camera_props, "노출 · 게인 · 화이트밸런스")
        self.act_reconnect = A("카메라 재연결", "videocam", self.restart_camera, "캡처 장비 다시 연결")
        self.act_record.setIcon(icons.icon("rec", ICON_RECORD, 20))
        self.act_capture.setIcon(icons.icon("camera", ICON_ACCENT, 20))
        # 영상
        self.act_pro = A("보정·측정", "wand", self.open_pro, "밝기·대비·Red-free·필터·주석·길이/각도 측정 (더블클릭)")
        self.act_overlay = A("겹쳐보기", "compare", self.open_overlay, "선택한 2장을 겹쳐 경과 비교")
        self.act_compare = A("비교", "review", self.open_compare, "선택한 2~4장 나란히 비교")
        self.act_ref_add = A("참조 등록", "plus", self.add_reference, "현재 영상을 참조 라이브러리에 등록")
        self.act_ref_lib = A("참조 영상", "image", self.open_reference_library, "질환별 참조 영상과 비교")
        self.act_slideshow = A("슬라이드쇼", "play", self.start_slideshow, "선택(또는 전체) 영상을 전체 화면으로")
        self.act_report = A("보고서", "printer", self.open_report, "Word · PDF 보고서 만들기 / 인쇄", key="Ctrl+P")
        # 보기
        self.act_theme = A("밝은 / 어두운 테마", "sun", self.toggle_theme)
        self.act_fullscreen = A("전체 화면", "expand", lambda: self.set_fullscreen(not self._fullscreen))
        self.act_thumbs_vertical = A("영상 목록 세로 (오른쪽)", "list", self.set_thumbs_vertical,
                                     "영상 목록 칸을 오른쪽 세로 줄로 / 끄면 아래 가로 줄로", checkable=True)
        self.act_about = A("정보", "info", self.about)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("파일(&F)")
        for a in (self.act_new_patient, self.act_edit_patient, self.act_del_patient, None,
                  self.act_new_exam, self.act_edit_exam, self.act_del_exam, None,
                  self.act_import, self.act_export, self.act_delete_images, self.act_samples, None,
                  self.act_backup_drive, self.act_backup, self.act_restore, None, self.act_settings,
                  self.act_shortcuts, None,
                  self.act_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("카메라(&C)")
        for a in (self.act_live, self.act_capture, self.act_record, None,
                  self.act_freeze, self.act_split, self.act_split_save, self.act_split_count, None,
                  self.act_props, self.act_reconnect):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("영상(&I)")
        for a in (self.act_pro, self.act_overlay, self.act_compare, None, self.act_ref_add,
                  self.act_ref_lib, None, self.act_slideshow, self.act_report):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("보기(&V)")
        m.addAction(self.act_theme)
        m.addAction(self.act_fullscreen)
        m.addAction(self.act_thumbs_vertical)
        mb.addMenu("도움말(&H)").addAction(self.act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("도구")
        tb.setObjectName("ExplorerTools")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        for a in (self.act_new_patient, self.act_new_exam, None,
                  self.act_live, self.act_freeze, self.act_split, self.act_capture, self.act_record,
                  self.act_props, None,
                  self.act_pro, self.act_overlay, self.act_compare, self.act_ref_add, self.act_ref_lib,
                  self.act_slideshow, self.act_report, None, self.act_import, self.act_export):
            tb.addSeparator() if a is None else tb.addAction(a)
        # 브랜드 로고 (Keeler Explorer 판 등) — 툴바 오른쪽 끝, 테마에 맞춰 밝은/어두운 배경용으로
        self.brand_logo = None
        if app_brand.asset(self.brand.get("logo")):
            spacer = QWidget()
            spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            spacer.setStyleSheet("background: transparent;")
            tb.addWidget(spacer)
            self.brand_logo = QLabel()
            self.brand_logo.setObjectName("BrandLogo")
            self.brand_logo.setContentsMargins(8, 0, 10, 0)
            tb.addWidget(self.brand_logo)
            self._render_brand_logo()
        self.addToolBar(tb)
        self.toolbar = tb
        # Keeler: 도구줄 아래 선은 로고 K 의 4색 줄무늬 (theme._brand_qss 의 QToolBar#ExplorerTools 테두리)

    def _render_brand_logo(self) -> None:
        if self.brand_logo is None:
            return
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtSvg import QSvgRenderer
        key = "logo_dark" if self.config.theme == "dark" else "logo"
        # 브랜드 색 헤더(네이비)면 테마와 상관없이 헤더용 로고(흰 글자)
        path = (app_brand.asset(self.brand.get("header_logo")) or app_brand.asset(self.brand.get(key))
                or app_brand.asset(self.brand.get("logo")))
        r = QSvgRenderer(path)
        size = r.defaultSize()
        h = 30
        w = max(1, round(size.width() * h / max(1, size.height())))
        ratio = 2
        pm = QPixmap(w * ratio, h * ratio)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r.render(p, QRectF(0, 0, w * ratio, h * ratio))
        p.end()
        pm.setDevicePixelRatio(ratio)
        self.brand_logo.setPixmap(pm)
        self.brand_logo.setFixedSize(w + 18, h)

    def _build_central(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(8, 8, 8, 6)
        lay.setSpacing(6)
        self.view = QStackedWidget()
        self.view.setObjectName("ExplorerView")
        self.video = VideoView()
        self.preview = ImageCanvas()
        self.quad = QuadView(count=int(getattr(self.config, "split_count", 4) or 4))
        self.view.addWidget(self.video)      # 0 = 라이브
        self.view.addWidget(self.preview)    # 1 = 영상 보기
        self.view.addWidget(self.quad)       # 2 = 분할 정지영상
        self.quad.cellClicked.connect(self._on_split_cell_clicked)
        lay.addWidget(self.view, 1)

        bar = QFrame()
        bar.setObjectName("ExplorerBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 6, 10, 6)
        bl.setSpacing(6)
        seg = QFrame()
        seg.setObjectName("EyeSeg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.eye_group = QButtonGroup(self)
        self.eye_buttons = {}
        for code, text, ko in _EYES:
            b = QPushButton(text)
            b.setObjectName("EyeBtn")
            b.setProperty("eye", code)
            b.setCheckable(True)
            b.setToolTip(ko)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, c=code: self.set_eye(c))
            self.eye_group.addButton(b)
            self.eye_buttons[code] = b
            sl.addWidget(b)
        self.eye_buttons["NA"].setChecked(True)
        bl.addWidget(seg)
        self.lbl_target = QLabel()
        self.lbl_target.setObjectName("Muted")
        bl.addWidget(self.lbl_target, 1)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("StatusLabel")
        bl.addWidget(self.lbl_status)

        def btn(text, name, icon, color, action):
            b = tool_button(text, icon, name, checkable=action.isCheckable(), color=color,
                            tip=action.toolTip())
            if action.isCheckable():
                b.toggled.connect(action.setChecked)
                action.toggled.connect(b.setChecked)
            else:
                b.clicked.connect(action.trigger)
            bl.addWidget(b)
            return b

        self.btn_split = btn("분할", "Pause", "quad", ICON, self.act_split)
        self.btn_split.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_split.customContextMenuRequested.connect(lambda _pos: self.change_split_count())
        self._sync_split_tip()
        self.btn_split_save = btn("분할 저장", "ToolBtn", "save", ICON, self.act_split_save)
        self.btn_split_save.setVisible(False)
        self.btn_freeze = btn("정지영상", "Freeze", "freeze", ICON_ACCENT, self.act_freeze)
        self.btn_record = btn("녹화", "Record", "rec", ICON_RECORD, self.act_record)
        # 사진 캡처 = 원형 셔터 (기본판 촬영 도크와 같은 모양 · QPushButton 기반)
        self.btn_capture = QPushButton()
        self.btn_capture.setObjectName("Shutter")
        self.btn_capture.setIcon(icons.icon("camera", ICON_ON_ACCENT, 26))
        self.btn_capture.setIconSize(QSize(26, 26))
        self.btn_capture.setFixedSize(56, 56)
        self.btn_capture.setToolTip(self.act_capture.toolTip())
        self.btn_capture.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_capture.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_capture.clicked.connect(self.act_capture.trigger)
        bl.insertWidget(bl.indexOf(self.btn_record), self.btn_capture)
        lay.addWidget(bar)
        self.setCentralWidget(central)

    def _build_docks(self) -> None:
        self.tree = PatientTree(self.db)
        d = QDockWidget("환자 · 검사", self)
        d.setObjectName("DockPatients")
        d.setWidget(self.tree)
        d.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        d.setMinimumWidth(290)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, d)
        self.thumbs = ThumbList()
        t = QDockWidget("영상 목록", self)
        t.setObjectName("DockThumbs")
        t.setWidget(self.thumbs)
        t.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        t.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, t)
        self.docks = (d, t)
        self.thumb_dock = t
        self.set_thumbs_vertical(bool(getattr(self.config, "explorer_thumbs_vertical", False)), save=False)
        t.dockLocationChanged.connect(self._on_thumb_dock_moved)     # 칸을 끌어 옮겨도 배치를 맞춤
        self.thumbs.picked.connect(self.show_capture)
        self.thumbs.currentChanged.connect(self._on_thumb_current)
        self.thumbs.activated.connect(self._on_thumb_activated)
        self.thumbs.selectionChanged.connect(lambda *_: self._sync_actions())

    # ================= 단축키 =================
    def _install_shortcuts(self) -> None:
        """사용자 지정 단축키(app/shortcuts.py)를 메뉴 동작에 연결."""
        self._keys = shortcuts.load(self.config)
        k = self._keys
        bind = {"capture": self.act_capture, "record": self.act_record, "freeze": self.act_freeze,
                "fullscreen": self.act_fullscreen, "newPatient": self.act_new_patient}
        for aid, act in bind.items():
            act.setShortcut(QKeySequence(k.get(aid, "")))
        extra = getattr(self, "_extra_actions", [])
        for a in extra:
            self.removeAction(a)
        self._extra_actions = []

        def add(key, fn):
            if not key:
                return
            a = QAction(self)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            self.addAction(a)
            self._extra_actions.append(a)

        for code in ("OD", "OS", "OU", "NA"):
            add(k.get(f"eye{code}"), lambda _=False, c=code: self.set_eye(c))
        add(k.get("mode"), lambda: self.set_view("image" if self.view.currentIndex() != 1 else "live"))
        add(k.get("quad"), lambda: self.stop_split() if self._split else self.start_split())
        add(k.get("patients"), lambda: (self.tree.search.setFocus(), self.tree.search.selectAll()))
        add("Esc", lambda: self.set_fullscreen(False))
        for a in (self.act_capture, self.act_record, self.act_freeze, self.act_new_patient, self.act_fullscreen):
            a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)

    def open_shortcuts(self) -> None:
        dlg = ShortcutsDialog(self._keys, self)
        if dlg.exec():
            self.config.shortcuts = dlg.values()
            self._save_config()
            self._install_shortcuts()
            self._status("단축키를 저장했습니다")

    # ================= 상태 =================
    def _status(self, text: str, ms: int = 4000) -> None:
        self.lbl_status.setText(text)
        self.statusBar().showMessage(text, ms)

    def _save_config(self) -> None:
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            log.warning("config save failed", exc_info=True)

    def _sync_actions(self) -> None:
        has_p = self.patient is not None
        has_e = self.exam is not None
        cur = self.thumbs.current()
        photos = [c for c in self.thumbs.selected() if c.media_type == "photo"]
        is_photo = cur is not None and cur.media_type == "photo"
        for a in (self.act_edit_patient, self.act_del_patient, self.act_new_exam, self.act_import):
            a.setEnabled(has_p)
        for a in (self.act_edit_exam, self.act_del_exam):
            a.setEnabled(has_e)
        self.act_capture.setEnabled(has_p)
        self.act_record.setEnabled(has_p or self.camera.recording)
        self.btn_capture.setEnabled(has_p)
        self.act_split_save.setEnabled(has_p and self._split and self.quad.has_any())
        self.btn_split_save.setEnabled(self.act_split_save.isEnabled())
        self.btn_record.setEnabled(self.act_record.isEnabled())
        self.act_export.setEnabled(bool(self.thumbs.selected()))
        self.act_delete_images.setEnabled(bool(self.thumbs.selected()))
        self.act_pro.setEnabled(is_photo)
        self.act_ref_add.setEnabled(is_photo)
        self.act_overlay.setEnabled(len(photos) >= 2)
        self.act_compare.setEnabled(len(photos) >= 2)
        self.act_slideshow.setEnabled(any(c.media_type == "photo" for c in self.thumbs.captures()))
        self.act_report.setEnabled(has_p and any(c.media_type == "photo" for c in self.thumbs.captures()))
        target = "환자를 선택하세요"
        if has_p:
            target = (f"저장 대상: {self.patient.name} › {self.exam.exam_date} {self.exam.title or ''}" if has_e
                      else f"저장 대상: {self.patient.name} › 오늘 검사 (자동)")
        self.lbl_target.setText(target)

    def _hud_patient(self) -> str:
        p = self.patient
        if p is None:
            return "환자 미선택"
        return f"{p.name}  ·  {p.chart_no}" if p.chart_no else p.name

    def _update_rec_hud(self) -> None:
        if self.camera.recording and self.camera.rec_started:
            s = int(time.monotonic() - self.camera.rec_started)
            self.video.set_hud(state="rec", rec_text=f"{s // 60:02d}:{s % 60:02d}")

    def _refresh_hud(self) -> None:
        if self.camera.recording:
            self._update_rec_hud()
        else:
            self.video.set_hud(state="paused" if self._paused else "live")

    # ================= 환자 · 검사 =================
    def _on_tree_selection(self, patient, exam) -> None:
        self.patient, self.exam = patient, exam
        self.video.set_hud(patient=self._hud_patient())
        self.reload_thumbs()

    def reload_thumbs(self, keep: bool = False) -> None:
        if self.patient is None:
            self.thumbs.set_captures([], "영상")
        elif self.exam is not None:
            caps = self.db.captures.list_by_exam(self.exam.id, order="asc")
            self.thumbs.set_captures(caps, f"{self.exam.exam_date}  {self.exam.title or ''}", keep=keep)
        else:
            caps = self.db.captures.list_by_patient(self.patient.id, order="asc")
            self.thumbs.set_captures(caps, f"{self.patient.name} — 전체 영상", keep=keep)
        self._sync_actions()

    def _after_new_capture(self, cap) -> None:
        """새 영상이 들어간 검사를 트리에서 고르고 목록을 갱신 (뷰는 라이브 그대로)."""
        if self.exam is None or self.exam.id != cap.exam_id:
            self.tree.reload(self.patient.id, cap.exam_id)
        else:
            self.tree.refresh_exams()
            self.exam = self.tree.current_exam()
            self.reload_thumbs()

    def new_patient(self) -> None:
        dlg = PatientDialog(self, db=self.db)
        if not dlg.exec():
            return
        if dlg.existing is not None:                 # 같은 차트번호 → 기존 환자에 이어서
            pid = dlg.existing.id
        else:
            p = dlg.get_patient()
            try:
                pid = p.id = self.db.patients.insert(p)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "오류", f"저장 실패: {e}\n(차트번호 중복 여부를 확인하세요)")
                return
        self.tree.search.blockSignals(True)
        self.tree.search.clear()
        self.tree.search.blockSignals(False)
        self.tree.reload(pid)

    def edit_patient(self) -> None:
        if self.patient is None:
            return
        dlg = PatientDialog(self, patient=self.patient, db=self.db)
        if not dlg.exec():
            return
        try:
            self.db.patients.update(dlg.get_patient())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "오류", f"수정 실패: {e}")
            return
        self.tree.reload(self.patient.id, self.exam.id if self.exam else None)

    def delete_patient(self) -> None:
        p = self.patient
        if p is None:
            return
        if QMessageBox.question(self, "환자 삭제",
                                f"{p.name} ({p.chart_no or '-'}) 환자와 모든 검사·영상 기록을 지울까요?\n"
                                "(디스크의 영상 파일은 남겨 둡니다)") != QMessageBox.StandardButton.Yes:
            return
        self.db.patients.delete(p.id)
        self.tree.reload()

    def new_exam(self) -> None:
        if self.patient is None:
            return
        dlg = ExamDialog(self, patient_id=self.patient.id)
        if dlg.exec():
            eid = self.db.exams.insert(dlg.get_exam())
            self.tree.reload(self.patient.id, eid)

    def edit_exam(self) -> None:
        if self.exam is None:
            return
        dlg = ExamDialog(self, exam=self.exam)
        if dlg.exec():
            self.db.exams.update(dlg.get_exam())
            self.tree.reload(self.patient.id, self.exam.id)

    def delete_exam(self, confirm: bool = True) -> None:
        e = self.exam
        if e is None:
            return
        if confirm and QMessageBox.question(
                self, "검사 삭제", f"{e.exam_date} {e.title or ''} 검사와 영상 {e.n_captures}장을 지울까요?\n"
                                   "(지정 폴더에 복사된 사본은 남겨 둡니다)") != QMessageBox.StandardButton.Yes:
            return
        _remove_files(self.db.exams.delete(e.id))
        self.tree.reload(self.patient.id)

    # ================= 영상 보기 =================
    def set_view(self, mode: str) -> None:
        live = mode == "live"
        if not live and self.thumbs.current() is None:
            live = True
        self.view.setCurrentIndex((2 if self._split else 0) if live else 1)
        if not live:
            self.show_capture(self.thumbs.current())

    def show_capture(self, cap) -> None:
        if cap is None:
            return
        if cap.media_type == "video":
            self._status("동영상은 더블클릭하면 기본 플레이어로 재생합니다")
            return
        self.view.setCurrentIndex(1)
        self.preview.set_image(load_bgr(cap.file_path))
        caps = self.thumbs.captures()
        n = next((i for i, c in enumerate(caps) if c.id == cap.id), 0) + 1
        self.preview.set_caption(caption_of(cap, n, len(caps)))
        self._sync_actions()

    def _on_thumb_current(self, cap) -> None:
        if self.view.currentIndex() == 1 and cap is not None:
            self.show_capture(cap)
        self._sync_actions()

    def _on_thumb_activated(self, cap) -> None:
        if cap.media_type == "video":
            open_in_default_player(cap.file_path)
        else:
            self.open_pro()

    # ================= 카메라 =================
    def _on_frame(self, frame) -> None:
        if self._split:
            self.quad.set_live_frame(frame)
        elif not self._paused:
            self.video.set_frame(frame)

    def _on_camera_error(self, msg: str) -> None:
        self._status(f"카메라 오류: {msg}", 8000)
        if self.camera.recording:
            self.stop_recording()

    def restart_camera(self) -> None:
        if self.camera.recording:
            self.stop_recording()
        self.camera.restart()
        self.video.clear()

    def set_eye(self, code: str) -> None:
        self.eye = code
        self.eye_buttons[code].setChecked(True)
        self.video.set_hud(eye=code)
        self._status(f"안구: {'미지정' if code == 'NA' else code}", 2000)

    # ---- 정지영상 · 분할 정지영상 (기본판 촬영 도크와 같은 동작) ----
    def freeze(self) -> None:
        """단일: 누르면 멈춤 · 다시 누르면 라이브.  분할: 라이브 칸을 정지 → 다음 칸, 다 찼으면 '처음부터'."""
        if self._split:
            if self.quad.is_full():
                self.clear_split()
                return
            if self.camera.last_frame is None:
                return
            if self.quad.freeze_active(self.eye):
                sounds.play("freeze")
                n = self.quad.count()
                self._status(f"{n}칸 모두 채움 · '분할 저장' 하거나, 칸을 눌러 그 칸만 다시 · '처음부터'로 전체 다시"
                             if self.quad.is_full() else "정지영상 추가됨 (다음 칸으로 이동)")
            else:
                self._status("라이브 영상을 기다리는 중입니다")
            self._sync_freeze_button()
            return
        if not self._paused and self.camera.last_frame is None:
            return
        self.set_paused(not self._paused)

    def set_paused(self, on: bool) -> None:
        self._paused = on
        self._freeze = self.camera.last_frame if on else None    # 정지한 순간의 영상 (캡처 대상)
        if on:
            sounds.play("freeze")
            self.set_view("live")
            self.video.set_frame(self._freeze)
        self._status("정지영상 (캡처로 저장 · 다시 누르면 라이브)" if on else "라이브", 3000)
        self._refresh_hud()
        self._sync_freeze_button()

    def _sync_freeze_button(self) -> None:
        if self._split and self.quad.is_full():
            text, icon, tip = "처음부터", "rot-l", "모든 칸을 비우고 1번 칸부터 다시 촬영"
        elif self._paused and not self._split:
            text, icon, tip = "라이브", "play", "라이브 영상으로 돌아가기"
        else:
            text, icon, tip = "정지영상", "freeze", "누르면 화면 멈춤 · 다시 누르면 라이브 (분할에서는 칸 채우기)"
        self.btn_freeze.setText(text)
        self.btn_freeze.setIcon(icons.icon(icon, ICON_ACCENT, 22))
        self.btn_freeze.setToolTip(text if text != "정지영상" else tip)
        self.act_freeze.setText(text)
        self.act_freeze.setToolTip(tip)
        has = self._split and self.quad.has_any()
        self.act_split_save.setEnabled(has and self.patient is not None)
        self.btn_split_save.setEnabled(self.act_split_save.isEnabled())

    def _on_split_toggled(self, on: bool) -> None:
        if on and not self._split:
            if getattr(self.config, "split_count_saved", False):
                self.start_split(int(getattr(self.config, "split_count", 4) or 4))   # 저장한 칸 수로 바로
                return
            n = self._choose_split_count(self.btn_split)
            if n is None:                          # 메뉴를 닫으면 켜지 않음
                # 버튼 ↔ 메뉴 항목이 서로 체크를 맞추는 중이라, 이 신호 처리가 다 끝난 뒤에 끈다 (되켜짐 반복 방지)
                QTimer.singleShot(0, lambda: None if self._split else self._set_split_checked(False))
                return
            self.start_split(n)
        elif not on and self._split:
            self.stop_split()

    def _set_split_checked(self, on: bool) -> None:
        for w in (self.act_split, self.btn_split):
            blk = w.blockSignals(True)
            w.setChecked(on)
            w.blockSignals(blk)

    @staticmethod
    def _exec_menu(menu, pos):
        return menu.exec(pos)

    def _choose_split_count(self, anchor) -> int | None:
        """분할 칸 수(2 · 4 · 6 · 8 · 16)를 고른다. 고른 칸 수는 저장되어 다음부터 분할은 묻지 않고 바로 시작."""
        n = split_menu.choose(self.config, self, anchor, self._exec_menu)
        self._sync_split_tip()
        if n:
            self._status(f"{n}분할로 저장 — 다음부터 분할은 바로 {n}분할 (칸 수 바꾸기: 분할 버튼 오른쪽 클릭 · 카메라 메뉴)")
        return n

    def change_split_count(self) -> None:
        """칸 수 고르기 메뉴를 (저장 여부와 상관없이) 띄운다. 분할 중이면 고른 칸 수로 다시 시작."""
        n = self._choose_split_count(self.btn_split)
        if n and self._split:
            self.start_split(n)

    def _sync_split_tip(self) -> None:
        n = int(getattr(self.config, "split_count", 4) or 4)
        tip = (f"분할 정지영상 — 저장한 {n}분할로 바로 시작 (칸 수 바꾸기: 오른쪽 클릭 · 카메라 메뉴)"
               if getattr(self.config, "split_count_saved", False)
               else "분할 정지영상 — 누르면 2 · 4 · 6 · 8 · 16칸 중에서 고릅니다 (고른 칸 수는 저장)")
        self.btn_split.setToolTip(tip)

    def start_split(self, n: int | None = None) -> None:
        n = n if n in SPLIT_COUNTS else int(getattr(self.config, "split_count", 4) or 4)
        if n != getattr(self.config, "split_count", 4):
            self.config.split_count = n
            self._save_config()
        if self._paused:
            self._paused, self._freeze = False, None
            self._refresh_hud()
        self._split = True
        self._set_split_checked(True)
        self.quad.set_count(n)                     # 켤 때마다 모두 비우고 1번 칸부터
        self.view.setCurrentIndex(2)
        self.btn_split_save.setVisible(True)
        self._sync_freeze_button()
        self._status(f"{n}분할 · '정지영상'으로 각 칸을 채우세요 (칸을 누르면 그 칸만 다시)")

    def stop_split(self) -> None:
        self._split = False
        self._set_split_checked(False)
        self.quad.reset()
        if self.view.currentIndex() == 2:
            self.view.setCurrentIndex(0)
        self.btn_split_save.setVisible(False)
        self._sync_freeze_button()
        self._status("단일 화면")

    def clear_split(self) -> None:
        self.quad.reset()
        self._sync_freeze_button()
        self._status(f"{self.quad.count()}분할 처음부터")

    def _on_split_cell_clicked(self, idx: int) -> None:
        if self._split:
            self.quad.set_active(idx)                 # 누른 칸을 다시 라이브로 (그 칸만 다시 촬영)
            self._sync_freeze_button()
            self._status(f"{idx + 1}번 칸을 다시 촬영합니다 · '정지영상'으로 채우세요")

    def save_split(self) -> list:
        """칸별 영상 + 격자 합성 영상을 선택한 검사(없으면 오늘 검사)에 저장."""
        if not self._split or not self._require_patient():
            return []
        if not self.quad.has_any():
            QMessageBox.information(self, "확인", "저장할 정지영상이 없습니다.")
            return []
        n = self.quad.count()
        exam_id = self.exam.id if self.exam else None
        sounds.play("shutter")
        saved = []
        try:
            for frame, cell_eye in self.quad.images_for_save(self.eye):
                cap = self.service.save_photo(frame, self.patient, eye=cell_eye, note=f"{n}분할", exam_id=exam_id)
                exam_id = cap.exam_id                     # 모두 같은 검사로
                saved.append(cap)
            comp = self.quad.composite()
            if comp is not None:
                saved.append(self.service.save_photo(comp, self.patient, eye="NA", note=f"{n}분할 합성",
                                                     exam_id=exam_id))
        except Exception as e:  # noqa: BLE001
            log.exception("split save failed")
            QMessageBox.warning(self, "오류", f"분할 저장 실패: {e}")
        if saved:
            self._after_new_capture(saved[-1])
            self._status(f"{n}분할 저장됨 (칸별 + 합성 {len(saved)}장)")
        return saved

    def _require_patient(self) -> bool:
        if self.patient is None:
            QMessageBox.information(self, "확인", "환자를 먼저 선택하세요.")
            return False
        return True

    def capture(self):
        if not self._require_patient():
            return None
        frame = self._freeze if (self._paused and self._freeze is not None) else self.camera.last_frame
        if frame is None:
            QMessageBox.information(self, "확인", "표시할 영상이 없습니다.")
            return None
        sounds.play("shutter")                   # 저장(인코딩) 전에 바로 — 누른 순간 '찰칵'
        try:
            cap = self.service.save_photo(frame, self.patient, eye=self.eye,
                                          exam_id=self.exam.id if self.exam else None)
        except Exception as e:  # noqa: BLE001
            log.exception("photo save failed")
            QMessageBox.warning(self, "오류", f"저장 실패: {e}")
            return None
        self._after_new_capture(cap)
        self._status("사진 저장됨")
        return cap

    def _on_record_toggled(self, on: bool) -> None:
        if on and not self.camera.recording:
            self.start_recording()
        elif not on and self.camera.recording:
            self.stop_recording()

    def start_recording(self) -> None:
        if not self._require_patient():
            self._reset_record_button()
            return
        if self._rec_pending is not None:          # 앞 녹화 파일을 아직 닫는 중 — 새 녹화와 섞이지 않게
            self._status("앞 동영상 파일을 마무리하는 중입니다. 잠시 후 다시 누르세요.", 5000)
            self._reset_record_button()
            return
        path, ts = self.service.begin_video(self.patient, eye=self.eye)
        try:
            self.camera.start_recording(path)
            self.backup.set_busy([path])                  # 쓰는 중인 동영상은 백업 전체 확인에서 뺀다
        except Exception as e:  # noqa: BLE001
            log.exception("recorder start failed")
            QMessageBox.warning(self, "오류", f"녹화 시작 실패: {e}")
            self._reset_record_button()
            return
        self._rec_patient, self._rec_exam_id, self._rec_eye = self.patient, (self.exam.id if self.exam else None), self.eye
        self._rec_path, self._rec_ts = path, ts
        sounds.play("rec_start")
        self._rec_timer.start()
        self._update_rec_hud()
        self._status("녹화 중…")

    def stop_recording(self) -> None:
        if not self.camera.recording:
            return
        self._rec_timer.stop()
        first = self.camera.first_rec_frame
        count, duration = self.camera.stop_recording()
        self.backup.set_busy([])
        sounds.play("rec_stop")
        self._reset_record_button()
        self._refresh_hud()
        self._rec_pending = (self._rec_path, self._rec_patient, self._rec_eye, self._rec_ts, duration,
                             first, self._rec_exam_id)
        if self.camera.recorder.is_finalizing:
            # 인코더가 아직 파일을 닫는 중: 지금 복사·기록하면 잘린 mp4 가 내보내기에 남는다 → 닫힌 뒤 기록
            self._status("동영상 파일을 마무리하는 중…")
            self._rec_waited = 0.0
            QTimer.singleShot(200, self._poll_rec_finalized)
            return
        self._finish_recording()

    def _poll_rec_finalized(self) -> None:
        self._rec_waited += 0.2
        if self.camera.recorder.is_finalizing and self._rec_waited < 120:
            QTimer.singleShot(200, self._poll_rec_finalized)
            return
        self._finish_recording()

    def _finish_recording(self) -> None:
        pending, self._rec_pending = self._rec_pending, None
        if pending is None:
            return
        path, patient, eye, ts, duration, first, exam_id = pending
        count = self.camera.recorder.frame_count
        if not self.camera.recorder.is_finalizing:
            _, count, duration = self.camera.recorder.result()   # 파일을 다 닫은 뒤의 확정값
        if count == 0:
            try:
                os.remove(path)
            except OSError:
                pass
            QMessageBox.warning(self, "녹화 실패", "녹화된 영상이 없습니다 (카메라 또는 인코더 오류).")
            return
        try:
            cap = self.service.finish_video(path, patient, eye, ts, duration, thumb_frame=first, exam_id=exam_id)
        except Exception as e:  # noqa: BLE001
            log.exception("finish_video failed")
            QMessageBox.warning(self, "오류", f"동영상 저장 실패: {e}\n파일: {path}")
            return
        if self.patient is not None and self.patient.id == cap.patient_id:
            self._after_new_capture(cap)
        self._status(f"동영상 저장됨 ({duration // 1000}초)")

    def _reset_record_button(self) -> None:
        blk = self.act_record.blockSignals(True)
        self.act_record.setChecked(False)
        self.act_record.blockSignals(blk)
        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(False)
        self.btn_record.blockSignals(False)

    def _on_recorder_error(self, msg: str) -> None:
        log.error("recorder error: %s", msg)
        if self.camera.recording:
            self.stop_recording()
        self._status(f"녹화 오류로 녹화를 멈췄습니다: {msg}", 10000)

    def open_camera_props(self) -> None:
        self._show(CameraPropsDialog(self.camera, self.config, self))

    # ================= 영상 메뉴 =================
    def _show(self, w):
        """비모달 창을 띄우고 닫힐 때까지 참조를 쥔다."""
        self._windows.append(w)
        w.destroyed.connect(lambda *_: self._windows.remove(w) if w in self._windows else None)
        w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        w.show()
        return w

    def _photo_items(self, caps) -> list:
        out = []
        for c in caps:
            if c.media_type != "photo":
                continue
            img = load_bgr(c.file_path)
            if img is not None:
                out.append((img, caption_of(c)))
        return out

    def open_pro(self) -> None:
        cap = self.thumbs.current()
        if self.patient is None or cap is None or cap.media_type != "photo":
            return
        dlg = ProDialog(self.db, self.service, self.config, self.patient, cap, self)
        dlg.savedCapture.connect(self._refresh_after_edit)
        dlg.patientUpdated.connect(lambda p: self.tree.reload(p.id, self.exam.id if self.exam else None))
        self._show(dlg)

    def _refresh_after_edit(self) -> None:
        self.tree.refresh_exams()
        self.exam = self.tree.current_exam()
        self.reload_thumbs(keep=True)

    def open_overlay(self) -> None:
        caps = [c for c in self.thumbs.selected() if c.media_type == "photo"]
        if len(caps) < 2:
            QMessageBox.information(self, "겹쳐보기", "썸네일에서 Ctrl 을 누른 채 2장 이상 선택하세요.")
            return
        items = self._photo_items(caps)
        exam_id = caps[0].exam_id

        def save(img):
            cap = self.service.save_photo(img, self.patient, eye=caps[0].eye or "NA", note="겹쳐보기",
                                          exam_id=exam_id)
            self._refresh_after_edit()
            return f"저장했습니다 ({cap.captured_at})"

        return self._show(OverlayDialog(items, self, on_save=save if self.patient else None))

    def open_compare(self):
        caps = [c for c in self.thumbs.selected() if c.media_type == "photo"]
        if len(caps) < 2:
            QMessageBox.information(self, "비교", "썸네일에서 Ctrl 을 누른 채 2~4장을 선택하세요.")
            return None
        if len(caps) > MAX_ITEMS:
            self._status(f"앞의 {MAX_ITEMS}장만 비교합니다")
        exam_id = caps[0].exam_id

        def save(img):
            return self._save_screen(img, "비교", eye=caps[0].eye, exam_id=exam_id)

        return self._show(CompareDialog(self._photo_items(caps[:MAX_ITEMS]), self,
                                        on_save=save if self.patient else None))

    def _save_screen(self, img, note: str, eye: str | None = None, exam_id=None) -> str | None:
        """비교 화면 등을 한 장의 사진으로 선택한 검사(없으면 오늘 검사)에 저장."""
        if not self._require_patient():
            return None
        if exam_id is None and self.exam is not None:
            exam_id = self.exam.id
        try:
            cap = self.service.save_photo(img, self.patient, eye=eye if eye in ("OD", "OS", "OU") else "NA",
                                          note=note, exam_id=exam_id)
        except Exception as e:  # noqa: BLE001
            log.exception("screen save failed")
            QMessageBox.warning(self, "화면 저장", f"저장하지 못했습니다.\n{e}")
            return None
        sounds.play("shutter")
        self._refresh_after_edit()
        self._status(f"화면을 저장했습니다 ({note})")
        return f"저장했습니다 ({cap.captured_at})"

    def add_reference(self, values=None):
        cap = self.thumbs.current()
        if cap is None or cap.media_type != "photo":
            return None
        if values is None:
            dlg = ReferenceDialog(self, self.db.references.categories(),
                                  title=(self.exam.diagnosis if self.exam and self.exam.diagnosis
                                         else (self.patient.diagnosis or "") if self.patient else ""))
            if not dlg.exec():
                return None
            values = dlg.values()
        try:
            ref = self.service.add_reference(cap, *values)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "참조 영상", f"등록하지 못했습니다.\n{e}")
            return None
        self._status(f"참조 영상으로 등록했습니다 ({ref.category})")
        return ref

    def open_reference_library(self):
        cur = self.thumbs.current()
        current = None
        if cur is not None and cur.media_type == "photo":
            img = load_bgr(cur.file_path)
            if img is not None:
                who = self.patient.name if self.patient else ""
                current = (img, f"{who} · {caption_of(cur)}")
        eye = cur.eye if cur is not None else None
        exam_id = cur.exam_id if cur is not None else None
        return self._show(ReferenceLibraryDialog(
            self.db, self, current=current,
            on_import_samples=lambda parent: self.import_samples(parent=parent),
            on_register=lambda parent: self.add_reference() is not None,
            on_save_screen=lambda img, note: self._save_screen(img, note, eye=eye, exam_id=exam_id)))

    def import_samples(self, folder=None, parent=None) -> bool:
        """받아 둔 샘플 사진 폴더(질환별 하위 폴더 + metadata.json, zip 도 가능)를 질환별 샘플 환자로 등록.
        등록한 사진은 참조 영상 라이브러리의 '샘플 사진' 에 질환별로 보인다."""
        from app.services.sample_import import import_samples
        parent = parent if parent is not None and parent is not False else self
        if folder is None:
            start = Path.home() / "Downloads"           # 받아 둔 폴더는 보통 '다운로드'에 있다
            folder = QFileDialog.getExistingDirectory(
                parent, "샘플 사진 폴더 선택 (질환별 하위 폴더 + metadata.json)",
                str(start if start.is_dir() else Path.home()))
        if not folder:
            return False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = import_samples(self.db, self.config, folder)
        except (OSError, ValueError) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(parent, "샘플 사진", f"샘플 사진을 가져오지 못했습니다.\n{e}")
            return False
        QApplication.restoreOverrideCursor()
        self.tree.reload(self.patient.id if self.patient else None, self.exam.id if self.exam else None)
        self._status(result.summary().splitlines()[0])
        QMessageBox.information(
            parent, "샘플 사진",
            result.summary() + "\n\n참조 영상 라이브러리의 '샘플 사진' 에서 질환별로 보고 환자 영상과 비교할 수 있습니다.")
        return True

    def start_slideshow(self):
        sel = [c for c in self.thumbs.selected() if c.media_type == "photo"]
        caps = sel if len(sel) >= 2 else [c for c in self.thumbs.captures() if c.media_type == "photo"]
        if not caps:
            return None
        start = 0
        cur = self.thumbs.current()
        if cur is not None and cur in caps:
            start = caps.index(cur)
        show = SlideShow([(c.file_path, caption_of(c)) for c in caps], self.config.slideshow_seconds, self)
        show.go(start)
        self._show(show)
        show.showFullScreen()
        show.setFocus()
        return show

    def open_report(self):
        if self.patient is None:
            return None
        sel = self.thumbs.selected()
        caps = sel if sel else self.thumbs.captures()
        return self._show(ReportDialog(self.config, self.patient, caps, exam=self.exam, parent=self))

    # ================= 파일 메뉴: 가져오기 / 내보내기 / 삭제 / 백업 =================
    def import_images(self, files=None) -> int:
        if self.patient is None:
            return 0
        if files is None:
            files, _ = QFileDialog.getOpenFileNames(self, "영상 가져오기", os.path.expanduser("~"), _IMAGE_FILTER)
        n, last = 0, None
        for f in files:
            try:
                last = self.service.import_image(f, self.patient, eye=self.eye,
                                                 exam_id=self.exam.id if self.exam else None)
                n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("import failed: %s", f, exc_info=True)
                self._status(f"가져오지 못한 파일: {Path(f).name} ({e})", 8000)
        if last is not None:
            self._after_new_capture(last)
            self._status(f"{n}장을 가져왔습니다")
        return n

    def export_selected(self, folder=None) -> int:
        caps = self.thumbs.selected()
        if not caps:
            return 0
        if folder is None:
            folder = QFileDialog.getExistingDirectory(self, "내보낼 폴더", self.config.export_root or os.path.expanduser("~"))
        if not folder:
            return 0
        chart = paths.sanitize((self.patient.chart_no if self.patient else None) or "noChart")
        n = 0
        for c in caps:
            src = Path(c.file_path)
            stamp = (c.captured_at or "").replace("-", "").replace(":", "").replace(" ", "_")
            dst = Path(folder) / f"{chart}_{stamp}_{c.eye or 'NA'}_{c.id}{src.suffix}"
            try:
                shutil.copy2(src, dst)
                n += 1
            except OSError:
                log.warning("export failed: %s", src, exc_info=True)
        self._status(f"{n}장을 내보냈습니다 → {folder}")
        return n

    def delete_selected(self, confirm: bool = True) -> int:
        caps = self.thumbs.selected()
        if not caps:
            return 0
        if confirm and QMessageBox.question(
                self, "영상 삭제", f"선택한 영상 {len(caps)}장을 지울까요?\n(지정 폴더에 복사된 사본은 남겨 둡니다)") \
                != QMessageBox.StandardButton.Yes:
            return 0
        _remove_files([self.db.captures.delete(c.id) for c in caps])
        self.tree.refresh_exams()
        self.exam = self.tree.current_exam()
        self.reload_thumbs()
        self.set_view("live")
        return len(caps)

    def backup_to_drive(self) -> bool:
        """설정 → 백업 의 위치로 지금 백업 (위치를 아직 안 정했으면 설정 창의 백업 탭을 연다)."""
        if not self.backup.root():
            self.open_settings("백업")
            return False
        if self.backup.backup_now():
            self._status("백업을 시작했습니다 — 상태줄 오른쪽에 진행 상황이 보입니다")
            return True
        return False

    def backup_db(self, path=None):
        if path is None:
            default = Path(self.config.export_root or os.path.expanduser("~")) / \
                f"slitlamp_backup_{datetime.now():%Y%m%d_%H%M}.db"
            path, _ = QFileDialog.getSaveFileName(self, "DB 백업", str(default), "백업 (*.db)")
        if not path:
            return None
        try:
            out = self.db.backup_to(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "DB 백업", f"백업하지 못했습니다.\n{e}")
            return None
        self._status(f"백업했습니다 → {out}")
        return out

    def restore_db(self, path=None, confirm: bool = True) -> bool:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "DB 복원", self.config.export_root or os.path.expanduser("~"),
                                                  "백업 (*.db)")
        if not path:
            return False
        if confirm and QMessageBox.question(
                self, "DB 복원", "지금 기록을 백업 파일 내용으로 바꿉니다. 계속할까요?\n"
                                 "(먼저 'DB 백업' 으로 현재 상태를 저장해 두는 것을 권장합니다)") \
                != QMessageBox.StandardButton.Yes:
            return False
        try:
            self.db.restore_from(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "DB 복원", f"복원하지 못했습니다.\n{e}")
            return False
        self.tree.reload()
        self._status("백업에서 복원했습니다")
        return True

    # ================= 보기 · 설정 =================
    def open_settings(self, tab: str | None = None) -> None:
        dlg = SettingsDialog(self.config, self, actions={
            "camera": self.restart_camera,
            "shortcuts": lambda parent: self.open_shortcuts(),
            "samples": lambda parent: self.import_samples(parent=parent),
        }, standard=False)
        if tab:
            dlg.show_tab(tab)
        if dlg.exec():
            dlg.apply_to_config()
            self.backup.reconfigure()
            self.restart_camera()

    def toggle_theme(self) -> None:
        self.config.theme = "dark" if self.config.theme != "dark" else "light"
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(self.config.theme))
        self.act_theme.setIcon(icons.icon("sun" if self.config.theme == "dark" else "moon", ICON, 20))
        self._render_brand_logo()
        self._save_config()

    def set_thumbs_vertical(self, on: bool, save: bool = True) -> None:
        """영상 목록 칸: True = 오른쪽 세로 줄, False = 아래 가로 줄. 다음 실행에도 같은 배치."""
        on = bool(on)
        t = self.thumb_dock
        area = Qt.DockWidgetArea.RightDockWidgetArea if on else Qt.DockWidgetArea.BottomDockWidgetArea
        if self.dockWidgetArea(t) != area:
            blk = t.blockSignals(True)            # 직접 옮길 때는 dockLocationChanged 로 다시 들어오지 않게
            self.removeDockWidget(t)
            self.addDockWidget(area, t)
            t.show()
            t.blockSignals(blk)
        self.thumbs.set_vertical(on)
        t.setMinimumSize(212, 0) if on else t.setMinimumSize(0, 200)
        # 도크를 옮긴 직후에는 크기 지정이 무시될 수 있어 한 번 더 (이벤트 처리 뒤)
        size = ([t], [232], Qt.Orientation.Horizontal) if on else ([t], [230], Qt.Orientation.Vertical)
        self.resizeDocks(*size)
        QTimer.singleShot(0, lambda: self.resizeDocks(*size))
        blk = self.act_thumbs_vertical.blockSignals(True)
        self.act_thumbs_vertical.setChecked(on)
        self.act_thumbs_vertical.blockSignals(blk)
        if save and bool(getattr(self.config, "explorer_thumbs_vertical", False)) != on:
            self.config.explorer_thumbs_vertical = on
            self._save_config()

    def _on_thumb_dock_moved(self, area) -> None:
        """영상 목록 칸을 끌어서 오른쪽 / 아래로 옮기면 썸네일 배치도 맞춘다."""
        if area in (Qt.DockWidgetArea.RightDockWidgetArea, Qt.DockWidgetArea.BottomDockWidgetArea):
            self.set_thumbs_vertical(area == Qt.DockWidgetArea.RightDockWidgetArea)

    def set_fullscreen(self, on: bool) -> None:
        if on == self._fullscreen:
            return
        self._fullscreen = on
        for w in (self.menuBar(), self.toolbar, self.statusBar(), *self.docks):
            w.setVisible(not on)
        if on:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        else:
            self.showMaximized() if getattr(self, "_was_maximized", False) else self.showNormal()

    def about(self) -> None:
        QMessageBox.about(self, "정보", f"<b>{self.brand['app_name']}</b><br>"
                          "슬릿램프 영상 촬영 · 환자/검사 관리 · 겹쳐보기 · 비교 · 참조 영상 · 보고서<br><br>"
                          f"데이터 위치: {paths.get_appdata_dir()}")

    # ================= 종료 =================
    def closeEvent(self, event) -> None:  # noqa: N802
        for w in list(self._windows):
            try:
                w.close()
            except RuntimeError:
                pass
        try:
            if self.camera.recording:
                self.stop_recording()
            if self._rec_pending is not None:          # 닫는 중인 녹화 파일은 끝까지 기다려 기록
                self.camera.recorder.wait_finalized(60)
                self._finish_recording()
        except Exception:  # noqa: BLE001
            log.warning("stop recording on close failed", exc_info=True)
        self.camera.stop()
        self._save_config()
        try:
            self.backup.finish_on_exit(self)
        except Exception:  # noqa: BLE001
            log.warning("backup on exit failed", exc_info=True)
        self.backup.stop()
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            pass
        event.accept()


def _remove_files(records) -> None:
    """지운 캡처의 기본 저장 파일·썸네일 정리 (export 사본은 백업으로 남김)."""
    for r in records or []:
        if not r:
            continue
        for key in ("file_path", "thumbnail_path"):
            p = r.get(key)
            try:
                if p and os.path.isfile(p):
                    os.remove(p)
            except OSError:
                log.warning("remove failed: %s", p, exc_info=True)

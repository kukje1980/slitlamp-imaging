"""메인 윈도우: 레이아웃 조립 + 카메라/녹화 스레드 소유 + 신호 배선."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from collections import deque

from PySide6.QtCore import QEvent, QSize, Qt, QThread, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
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
    QVBoxLayout,
    QWidget,
)

from app import brand as app_brand
from app import edition as app_edition
from app import paths
from app import shortcuts
from app import sounds
from app import usb_buttons, usb_raw_input
from app.agent import dataset as agent_dataset
from app.agent.core import AgentSettings
from app.agent.events import EventLog
from app.agent.scene import default_model_path
from app.agent.worker import AgentWorker
from app.capture import presets as capture_presets
from app.capture.camera_worker import CameraWorker
from app.capture.frame_utils import device_names, find_sony_index
from app.capture.smart_capture import DEFAULT_ROI, SmartSettings, normalize_roi
from app.capture.smart_worker import SmartCaptureWorker
from app.capture.sources import DeviceSource, FileSource, SyntheticSource
from app.capture.video_recorder import VideoRecorder
from app.services import sample_import
from app.services.capture_service import CaptureService
from app.ui import icons
from app.ui.agent_bar import AgentBar
from app.ui.control_bar import ControlBar, tool_button
from app.ui.exam_screen import ExamScreen
from app.ui import split_menu
from app.ui.fit import HScroll
from app.ui.home_screen import HomeScreen
from app.ui.multi_view import is_reference, ref_capture
from app.ui.patient_list_screen import PatientListScreen
from app.ui.dx_bar import DxBar
from app.ui.image_viewer import ImageViewerDialog
from app.ui.patient_panel import PatientPanel
from app.ui.pro import print_util
from app.ui.pro.pro_workspace import ProWorkspace
from app.ui.quad_view import QuadView
from app.ui.settings_dialog import SettingsDialog
from app.ui.shortcuts_dialog import ShortcutsDialog
from app.ui.ref_bar import RefBar
from app.ui.smart_bar import SmartBar, mode_text
from app.ui import icons
from app.ui.theme import ICON, ICON_ACCENT, build_qss, make_app_icon
from app.ui.thumbnail_panel import ThumbnailPanel, day_label
from app.ui.video_view import VideoView

from app import __version__

log = logging.getLogger(__name__)


MODES = ["capture", "pro", "exam", "home", "patients"]       # mode_stack 순서


class MainWindow(QMainWindow):
    def __init__(self, config, db):
        super().__init__()
        self._mode = "capture"
        self.config = config
        self.db = db
        self.service = CaptureService(db, config)
        self.brand = app_brand.current()
        self.setWindowTitle(self.brand["window_title"] + app_edition.current()["title_suffix"])
        self.setWindowIcon(make_app_icon())
        self.resize(1280, 800)

        self._last_frame = None
        self._paused = False
        self._quad = False
        self._multi = False                  # 멀티: 선택한 사진 한 번에 보기
        self._single_frozen = False
        self._freeze_frame = None
        self._current_patient = None
        self._frame_times = deque(maxlen=60)
        self._frame_size = (config.capture_width, config.capture_height)
        self._recording = False
        self._video_path = None
        self._video_ts = None
        self._video_eye = "NA"
        self._first_video_frame = None
        self._video_patient = None
        self._rec_started = 0.0
        # 인코더가 파일을 닫는 동안 보류한 (path, patient, eye, ts, duration, thumb)
        self._pending_video = None
        self._finalize_timer = QTimer(self)
        self._finalize_timer.setInterval(200)
        self._finalize_timer.timeout.connect(self._poll_video_finalize)
        self._finalize_waited = 0.0
        self.cam_thread = None
        self.worker = None
        self._closing = False
        # 카메라 자동 재연결 (장비가 빠졌다 다시 꽂히면 버튼을 누르지 않아도 복구)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._auto_reconnect)
        self._reconnect_attempts = 0

        self.recorder = VideoRecorder(fallback_fourcc=config.video_fallback_fourcc)
        self.recorder.error.connect(self._on_recorder_error)

        sounds.set_enabled(config.sounds_enabled)
        # Smart Capture: 별도 스레드에서 프레임을 분석 (프리뷰가 끊기지 않게)
        self.smart = SmartCaptureWorker(self._smart_settings())
        self.smart.update(peaking=config.smart_peaking, peaking_color=config.smart_peaking_color)
        self.smart.analyzed.connect(self._on_smart_analyzed)
        self.smart.bestShot.connect(self._on_smart_best_shot)
        self.smart.start()
        # AI 에이전트: 장면 인식·자동 촬영·전후 클립·추천 프레임 + 사용 이벤트 로그 (익명)
        self.events = EventLog(paths.get_appdata_dir() / "agent", db=db,
                               enabled=config.agent_logging)
        self.agent = AgentWorker(self._agent_settings(), model_path=self._agent_model_path(),
                                 buffer_s=(config.agent_clip_before_s
                                           + config.agent_clip_after_s + 2.0))
        self.agent.sceneUpdated.connect(self._on_agent_scene)
        self.agent.decided.connect(self._on_agent_decisions)
        self.agent.candidatesReady.connect(self._on_agent_candidates)
        self.agent.clipReady.connect(self._on_agent_clip)
        self._agent_candidates = None          # (ClipResult, patient, eye)
        if config.agent_enabled:
            self.agent.start()
        self.events.log("session_start", edition=app_edition.current_key(),
                        agent=config.agent_enabled, version=__version__)

        self._build_ui()
        sounds.preload()
        self._pick_camera()
        self._start_camera()
        warn = self.service.low_space_warning()
        if warn:
            log.warning(warn)
            self.statusBar().showMessage(warn, 20000)

    # ---- UI 구성 ----
    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.patient_panel = PatientPanel(self.db)      # 환자 검색·선택 — 상단바 '정보 수정' 옆에 들어감
        self.topbar = self._build_topbar()
        outer.addWidget(self.topbar)
        self.brand_stripe = None
        if self.brand.get("stripe") and self.brand.get("k_colors"):
            # Keeler: 로고 K 의 4색(하늘·파랑·네이비·주황) 줄무늬를 상단바 아래에
            self.brand_stripe = QFrame()
            self.brand_stripe.setObjectName("BrandStripe")
            self.brand_stripe.setFixedHeight(4)
            outer.addWidget(self.brand_stripe)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(12, 12, 12, 10)
        simple = QWidget()                           # 기본(촬영) 화면
        root = QHBoxLayout(simple)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(10)
        self.video_view = VideoView()
        self.quad_view = QuadView()
        # 영상을 누르면 키보드 초점이 영상으로 (환자 검색칸에서 빠져나와 슬릿램프 USB 버튼 · 단축키가 바로 동작)
        for v in (self.video_view, self.quad_view):
            v.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.video_view)   # index 0 = 단일
        self.view_stack.addWidget(self.quad_view)     # index 1 = 분할 (2 · 4 · 6 · 8 · 16칸)
        # index 2 = 멀티 (고른 사진 + 참조 사진 모아 보기) — Pro 검토의 보정 · 필터 · 편집 · 스마트 · 분석 · 정밀 분석 메뉴와 함께
        self.multi_pro = ProWorkspace(self.db, self.service, self.config,
                                      get_eye=lambda: self.control_bar.current_eye(), embedded=True)
        self.multi_view = self.multi_pro.grid_view
        self.view_stack.addWidget(self.multi_pro)
        self.control_bar = ControlBar()
        self.ref_bar = RefBar(self.db)
        self.smart_bar = SmartBar()
        self.agent_bar = AgentBar()
        left.addWidget(self.view_stack, 1)
        left.addWidget(self.ref_bar)
        # 긴 줄은 좁은 창에서 눌려 겹치지 않게 옆으로 밀어 보는 칸에 (app/ui/fit.py)
        left.addWidget(HScroll(self.smart_bar, on_compact=self.smart_bar.set_compact))
        left.addWidget(HScroll(self.agent_bar))
        left.addWidget(HScroll(self.control_bar, on_compact=self.control_bar.set_compact))

        self.thumbnail_panel = ThumbnailPanel(self.db, newest_on_top=self.config.newest_on_top,
                                              split_eyes=bool(getattr(self.config, "thumbs_split_eyes", False)))

        root.addLayout(left, 1)
        root.addWidget(self.thumbnail_panel)

        # Pro(전문가 검토) 화면
        self.pro = ProWorkspace(self.db, self.service, self.config, get_eye=None)
        self.exam_screen = ExamScreen(self.db, self.config)
        self.home = HomeScreen(self.config)
        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(simple)            # 0 = 기본(촬영)
        self.mode_stack.addWidget(self.pro)          # 1 = Pro
        self.mode_stack.addWidget(self.exam_screen)  # 2 = 검사 기록
        self.mode_stack.addWidget(self.home)         # 3 = 홈
        self.patient_list = PatientListScreen(self.db)
        self.mode_stack.addWidget(self.patient_list)  # 4 = 환자 목록
        body_lay.addWidget(self.mode_stack, 1)
        outer.addWidget(body, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

        self.pro.savedCapture.connect(self._on_pro_saved)
        self.pro.patientUpdated.connect(self._on_pro_patient_updated)
        self.exam_screen.openCapture.connect(self._on_thumbnail_clicked)
        self.exam_screen.openInPro.connect(lambda: self.set_mode("pro"))
        self.exam_screen.editPatient.connect(lambda: self.patient_panel.edit_patient())
        self.exam_screen.deleteDay.connect(self._delete_day)
        self.home.action.connect(self._on_home_action)
        self.patient_list.openCapture.connect(lambda pid: self._open_patient(pid, "capture"))
        self.patient_list.openExam.connect(lambda pid: self._open_patient(pid, "exam"))
        self.patient_list.newPatient.connect(lambda: self._new_patient_from_list())
        self.patient_list.editPatient.connect(lambda pid: self._edit_patient_from_list(pid))

        self.patient_panel.patientChanged.connect(self._on_patient_changed)
        # 환자 검색칸에서 Enter = 검색 끝 → 초점을 영상으로 (USB 버튼 · 단축키가 바로 동작)
        self.patient_panel.search.returnPressed.connect(lambda: self._focus_capture_view() if self._mode == "capture" else None)
        self.control_bar.pauseToggled.connect(self._on_pause)
        self.control_bar.captureClicked.connect(self._on_capture)
        self.control_bar.recordToggled.connect(self._on_record_toggle)
        self.control_bar.split_count = int(getattr(self.config, "split_count", 4) or 4)
        self.control_bar.count_chooser = self._choose_split_count
        self.control_bar.count_saved = bool(getattr(self.config, "split_count_saved", False))
        self.control_bar.sync_quad_tip()
        self.control_bar.quadToggled.connect(self._on_quad_toggled)
        self.control_bar.freezeClicked.connect(self._on_freeze)
        self.control_bar.saveQuadClicked.connect(self._on_save_quad)
        self.control_bar.clearQuadClicked.connect(self._on_clear_quad)
        self.quad_view.cellClicked.connect(self._on_quad_cell_clicked)
        self.thumbnail_panel.thumbnailClicked.connect(self._on_thumbnail_clicked)   # 두 번 클릭: 큰 화면
        self.thumbnail_panel.selectionChanged.connect(self._on_selection_changed)   # 한 번 클릭: 선택
        # 오른쪽 클릭 = '멀티' (사진 목록의 사진: 그 사진도 골라서 · 라이브 영상: 고른 사진으로)
        self.thumbnail_panel.multiRequested.connect(self._safe("오른쪽 클릭 멀티", lambda: self._multi_by_right_click()))
        self.thumbnail_panel.dayDeleteRequested.connect(self._delete_day)      # 촬영일 제목 줄의 삭제: 그 날 모두
        self.video_view.rightClicked.connect(self._safe("오른쪽 클릭 멀티", lambda: self._multi_by_right_click()))
        self.control_bar.multiToggled.connect(self._on_multi_toggled)
        self.control_bar.saveMultiClicked.connect(self._safe("멀티 저장", lambda: self._on_save_multi()))
        self.control_bar.sendClicked.connect(self._safe("전송", lambda: self._on_send()))
        self.control_bar.presetClicked.connect(self._safe("촬영 모드", lambda: self._on_preset_menu()))
        self.control_bar.set_preset_name(self.config.active_preset or capture_presets.DEFAULT_NAME)
        self.multi_view.cellActivated.connect(self._on_multi_cell_activated)
        self.multi_pro.savedCapture.connect(self._on_pro_saved)          # 편집본 · 분석 결과 · 코멘트
        self.multi_pro.patientUpdated.connect(self._on_pro_patient_updated)
        self.ref_bar.selectionChanged.connect(self._safe("참조", lambda: self._on_refs_changed(self.ref_bar.selected_refs())))
        self.ref_bar.libraryRequested.connect(self._safe("참조 라이브러리", lambda: self.open_references()))
        self.ref_bar.hideRequested.connect(lambda: self._set_ref_bar_visible(False))
        self.control_bar.refsToggled.connect(lambda on: self._set_ref_bar_visible(on))
        # '멀티' 버튼은 오른쪽 촬영 이미지 칸(OD · OS 위) — 촬영 도크의 숨은 버튼과 상태를 함께 쓴다
        tb_multi = self.thumbnail_panel.btn_multi
        self.control_bar.multi_mirrors.append(tb_multi)
        tb_multi.clicked.connect(self._safe("멀티", lambda: self.control_bar.btn_multi.toggle()))
        self.control_bar.eyeChanged.connect(lambda _e: self._refresh_eye_status())   # HUD 눈 칩 + 자동 인식 상태
        self.control_bar.eyeChosen.connect(self._note_manual_eye)                   # OD · OS 를 직접 고름
        cfg = self.config
        self.smart_bar.set_state(cfg.smart_mode, cfg.smart_peaking, cfg.smart_peaking_sensitivity,
                                 cfg.smart_best_shot)
        self.smart_bar.modeChanged.connect(self._on_smart_mode)
        self.pro.set_smart_mode(cfg.smart_mode)
        self.pro.smartModeChanged.connect(lambda m: self.smart_bar.set_mode(m))
        self.smart_bar.peakingToggled.connect(self._on_smart_peaking)
        self.smart_bar.sensitivityChanged.connect(self._on_smart_sensitivity)
        self.smart_bar.roiSelectToggled.connect(self.video_view.set_roi_selecting)
        self.smart_bar.bestShotToggled.connect(self._on_smart_best_toggle)
        self.smart_bar.hideRequested.connect(lambda: self._set_smart_bar_visible(False))
        self.control_bar.smartToggled.connect(lambda on: self._set_smart_bar_visible(on))
        self.video_view.roiSelected.connect(self._on_smart_roi)
        self.agent_bar.set_state(cfg.agent_enabled, cfg.agent_auto_capture, cfg.agent_protocol,
                                 cfg.agent_logging)
        self.agent_bar.set_checklist(self.agent.core.checklist)
        self.agent_bar.setVisible(cfg.agent_enabled or app_edition.is_agent())
        self.agent_bar.enabledToggled.connect(self._on_agent_toggled)
        self.agent_bar.autoCaptureToggled.connect(self._on_agent_auto_toggled)
        self.agent_bar.protocolChanged.connect(self._on_agent_protocol)
        self.agent_bar.loggingToggled.connect(self._on_agent_logging)
        self.agent_bar.candidateChosen.connect(self._on_agent_candidate)
        self.agent_bar.candidatesDismissed.connect(self._on_agent_candidates_dismissed)
        self.agent_bar.resetChecklist.connect(self._on_agent_reset_checklist)
        self.agent_bar.exportDataset.connect(self._on_agent_export)
        self.agent_bar.openLogs.connect(self._on_agent_open_logs)
        self._sync_smart_view()
        self._rec_timer = QTimer(self)
        self._rec_timer.setInterval(500)
        self._rec_timer.timeout.connect(self._update_rec_hud)

        self._on_patient_changed(self.patient_panel.current_patient())
        self._fullscreen = False
        self._set_smart_bar_visible(bool(getattr(cfg, "smart_bar_visible", True)), save=False)
        self._set_ref_bar_visible(bool(getattr(cfg, "ref_bar_visible", False)), save=False)
        self._refresh_dx_bar()
        self._capture_viewer = None            # 촬영 직후 자동으로 뜨는 큰 보기 창
        self._install_shortcuts()
        # 슬릿램프 USB 버튼 · 좌우 신호(키 조합을 보내는 장치) — 앱 전체 키 이벤트에서 누름 · 뗌을 보고 촬영 동작을 부른다.
        # 좌우 신호(F6 → OD · F7 → OS)는 Windows Raw Input 으로 그 장치(VID 04D9 / PID 1203)에서 온 키인지 확인한다.
        self._eye_src = "unknown"              # 지금 검사눈이 정해진 경로: unknown(신호 없음) · device(슬릿램프) · manual(직접)
        self._eye_stale = False                # 창이 비활성이었거나 장치가 빠져 신호를 놓쳤을 수 있음 → '확인 필요'
        self._eye_pending = None               # 입력칸 · 다른 화면 · 다른 창에서 받은 좌우 신호 — 촬영 화면으로 오면 적용
        self._eye_signal_at = None             # 마지막 슬릿램프 좌우 신호 시각
        self._freeze_eye = None                # 정지 화면을 찍은 순간의 검사눈 (정지 중 좌우가 바뀌어도 그 사진은 이 눈)
        self._video_switches = []              # 녹화 중 좌우 전환 [(경과 초, 눈)] — 영상은 시작 눈으로 저장, 코멘트에 기록
        self.usb_raw = usb_raw_input.create(QApplication.instance(), on_device_change=self._on_usb_device_change)
        self.usb_raw.watch(usb_buttons.key_of(b["key"]) for b in usb_buttons.load(self.config).values())
        self.usb_filter = usb_buttons.UsbButtonFilter(
            usb_buttons.load(self.config), run=self._on_usb_button, context=self._usb_context,
            ignored=self._on_usb_ignored, parent=self, device_gate=self._usb_device_gate,
            activated=self._on_app_activated)
        QApplication.instance().installEventFilter(self.usb_filter)
        QApplication.instance().focusChanged.connect(lambda _old, _new: self._usb_eye_resume())
        self._refresh_eye_status()
        self.set_mode("capture" if getattr(self.config, "start_screen", "home") == "capture" else "home")

    def _build_topbar(self) -> QFrame:
        """브랜드 + 아이콘 메뉴: [홈 | 촬영 | Pro 검토 | 검사 기록 | 환자 목록] [신규 환자 | 정보 수정] 환자 검색 … [설정]."""
        bar = QFrame()
        bar.setObjectName("TopBar")
        # 아래 화면(멀티의 Pro 메뉴 등)이 커도 상단바 높이는 그대로 — 눌리면 메뉴 글자가 아이콘 위로 겹친다
        bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 6, 12, 6)
        lay.setSpacing(10)
        b = self.brand
        self.brand_logo = None
        if app_brand.asset(b.get("logo")):
            # 브랜드 로고 (테마에 맞춰 밝은/어두운 배경용을 바꿔 그림)
            self.brand_logo = QLabel()
            self.brand_logo.setObjectName("BrandLogo")
            lay.addWidget(self.brand_logo)
            self._render_brand_logo()
        else:
            logo = QLabel()
            logo.setPixmap(make_app_icon().pixmap(34, 34))
            logo.setFixedSize(34, 34)
            lay.addWidget(logo)
        if not (self.brand_logo and b.get("logo_only")):
            brand = QVBoxLayout()
            brand.setSpacing(0)
            t1 = QLabel("슬릿램프 영상 시스템")
            t1.setObjectName("BrandTitle")
            t2 = QLabel("SLIT LAMP IMAGING")
            t2.setObjectName("BrandSub")
            brand.addWidget(t1)
            brand.addWidget(t2)
            lay.addLayout(brand)
        lay.addSpacing(10)

        self.nav_buttons = []

        def nav(text, icon_name, tip, color=ICON, checkable=False):
            btn = tool_button(text, icon_name, "NavBtn", checkable=checkable, color=color, tip=tip,
                              icon_size=20, min_width=58)
            # 높이를 50 으로 고정하면 글꼴이 큰 PC(Windows 맑은 고딕·배율)에서 글자가 아이콘 위로 겹쳐
            # 보였다 → 최소 50 만 두고 글자 높이만큼 늘어나게
            btn.setMinimumHeight(50)
            btn.setToolTip(f"{text} — {tip}" if tip else text)
            self.nav_buttons.append(btn)
            return btn

        def seg():
            f = QFrame()
            f.setObjectName("NavSeg")
            sl = QHBoxLayout(f)
            sl.setContentsMargins(3, 3, 3, 3)
            sl.setSpacing(2)
            return f, sl

        # 촬영 / Pro 검토 전환 (act_pro 는 체크형 액션으로 유지)
        self.act_pro = QAction("Pro 검토", self)
        self.act_pro.setCheckable(True)
        self.act_pro.setToolTip("전문가용 영상 검토 화면 (MULTI·비교·보정·편집·인쇄)")
        self.act_pro.toggled.connect(self._on_pro_toggled)
        self.addAction(self.act_pro)
        sw, sl = seg()
        self.btn_mode_home = nav("홈", "home", "시작 화면 (아이콘 메뉴)", checkable=True)
        self.btn_mode_capture = nav("촬영", "camera", "실시간 촬영 화면", checkable=True)
        self.btn_mode_pro = nav("Pro 검토", "review", self.act_pro.toolTip(), checkable=True)
        self.btn_mode_exam = nav("검사 기록", "clipboard", "촬영일별 영상 모음과 검사 소견", checkable=True)
        self.btn_mode_patients = nav("환자 목록", "list", "등록된 환자 목록 — 차트번호 · 이름으로 찾아 촬영 · 검사 기록으로",
                                     checkable=True)
        self.mode_buttons = {"home": self.btn_mode_home, "capture": self.btn_mode_capture,
                             "pro": self.btn_mode_pro, "exam": self.btn_mode_exam,
                             "patients": self.btn_mode_patients}
        grp = QButtonGroup(self)
        for mode, name in (("home", "home"), ("capture", "camera"), ("pro", "review"), ("exam", "clipboard"),
                           ("patients", "list")):
            btn = self.mode_buttons[mode]
            ic = icons.icon(name, ICON, 20)
            ic.addPixmap(icons.pixmap(name, ICON_ACCENT, 20), ic.Mode.Normal, ic.State.On)
            btn.setIcon(ic)
            grp.addButton(btn)
            sl.addWidget(btn)
            btn.clicked.connect(self._safe(f"화면:{mode}", lambda m=mode: self.set_mode(m)))
        self.btn_mode_capture.setChecked(True)
        lay.addWidget(sw)

        # 환자: 신규 등록 · 정보 수정 (Pro 검토 바로 옆)
        pw, pl = seg()
        self.btn_new_patient = nav("신규 환자", "user-plus", "신규 환자 등록", color=ICON_ACCENT)
        self.btn_edit_patient = nav("정보 수정", "user-edit", "선택한 환자 정보 수정")
        self.btn_new_patient.clicked.connect(self._safe("신규 환자", lambda: self.patient_panel.new_patient()))
        self.btn_edit_patient.clicked.connect(self._safe("정보 수정", lambda: self.patient_panel.edit_patient()))
        pl.addWidget(self.btn_new_patient)
        pl.addWidget(self.btn_edit_patient)
        lay.addWidget(pw)
        lay.addWidget(self.patient_panel)               # 이름 · 차트번호 검색 + 환자 선택
        # 빈 공간: 질환별 작은 아이콘 — 누르면 아래 참조 줄에 그 질환 사진 + 멀티 화면 (app/ui/dx_bar.py)
        self.dx_bar = DxBar()
        self.dx_bar.diseaseClicked.connect(self._on_dx_clicked)
        lay.addWidget(self.dx_bar)
        lay.addStretch(1)

        # 카메라 재연결 · 단축키 · 샘플 · 테마는 모두 설정 창 안으로 (오른쪽에는 설정 하나)
        # 참조(질환별 참조 영상 · 샘플 사진)는 아래 촬영 도크의 '참조' 줄로 옮겼다
        tw, tl = seg()
        self.btn_settings = nav("설정", "gear", "설정 — 카메라 다시 연결 · 단축키 · 샘플 사진 · 화면 테마 · Smart 도구줄 · 저장 위치")
        self.btn_settings.clicked.connect(self._safe("설정", lambda: self._open_settings()))
        self.act_theme = QAction(self)
        self.act_theme.triggered.connect(self._safe("테마", lambda: self._toggle_theme()))
        tl.addWidget(self.btn_settings)
        lay.addWidget(tw)
        self._sync_theme_button()
        self._topbar_compact = False
        self._topbar_full_w = 0
        self._topbar_min_w = 0
        self._brand_texts = [w for w in bar.findChildren(QLabel) if w.objectName() in ("BrandTitle", "BrandSub")]
        bar.installEventFilter(self)
        return bar

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is getattr(self, "topbar", None) and ev.type() == QEvent.Type.Resize:
            self._fit_topbar()
        return super().eventFilter(obj, ev)

    def _fit_topbar(self) -> None:
        """창이 좁으면 상단 메뉴를 아이콘만 남겨(이름은 풍선 도움말) 글자와 아이콘이 서로 겹치지 않게 한다."""
        bar = getattr(self, "topbar", None)
        if bar is None:
            return
        if not self._topbar_compact:
            self._topbar_full_w = bar.layout().sizeHint().width()
        if not self._topbar_min_w:
            # 아이콘만 남겼을 때의 폭을 상단바 최소 폭으로 — 그보다 좁게는 눌리지 않는다
            self._set_topbar_compact(True)
            self._topbar_min_w = bar.layout().minimumSize().width()
            self._set_topbar_compact(False)
            bar.setMinimumWidth(self._topbar_min_w)
        compact = bar.width() < self._topbar_full_w
        if compact != self._topbar_compact:
            self._set_topbar_compact(compact)

    def _set_topbar_compact(self, compact: bool) -> None:
        self._topbar_compact = compact
        for b in self.nav_buttons:
            b.set_text_visible(not compact)
            b.setMinimumWidth(44 if compact else 58)
        for lb in self._brand_texts:
            lb.setVisible(not compact)
        self.dx_bar.set_compact(compact)
        # 묶음(NavSeg) 칸의 크기 기억을 지워야 바로 줄어든 폭으로 계산된다
        for seg in {b.parentWidget() for b in self.nav_buttons}:
            if seg is not None and seg.layout() is not None:
                seg.layout().invalidate()
                seg.updateGeometry()
        self.topbar.layout().invalidate()

    def _render_brand_logo(self) -> None:
        if self.brand_logo is None:
            return
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtSvg import QSvgRenderer
        key = "logo_dark" if self.config.theme == "dark" else "logo"
        # 브랜드 색 상단바(Keeler 네이비)면 테마와 상관없이 상단바용 로고(흰 글자)
        path = (app_brand.asset(self.brand.get("header_logo")) or app_brand.asset(self.brand.get(key))
                or app_brand.asset(self.brand.get("logo")))
        r = QSvgRenderer(path)
        size = r.defaultSize()
        h = 40
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
        self.brand_logo.setFixedSize(w, h)

    def _sync_theme_button(self) -> None:
        dark = self.config.theme == "dark"
        self.act_theme.setText("☀ 라이트 모드" if dark else "🌙 다크 모드")

    def _safe(self, name: str, fn):
        """버튼 동작 감싸기: 누른 기록을 app.log 에 남기고, 오류가 나면 조용히 사라지지 않게 안내창을 띄운다."""
        def run(*_args):
            log.info("버튼: %s", name)
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log.exception("버튼 '%s' 실행 실패", name)
                QMessageBox.warning(self, "오류", f"'{name}' 실행 중 오류가 났습니다.\n{e}\n\n"
                                    f"자세한 내용: {paths.get_appdata_dir() / 'logs' / 'app.log'}")
        return run

    def _update_rec_hud(self) -> None:
        if self._recording and self._rec_started:
            s = int(time.monotonic() - self._rec_started)
            self.video_view.set_hud(state="rec", rec_text=f"{s // 60:02d}:{s % 60:02d}")

    def _refresh_hud_state(self) -> None:
        if self._recording:
            self._update_rec_hud()
        elif self._single_frozen:
            self.video_view.set_hud(state="frozen")
        elif self._paused:
            self.video_view.set_hud(state="paused")
        else:
            self.video_view.set_hud(state="live")

    # ---- 단축키 (사용자 지정) ----
    def _install_shortcuts(self) -> None:
        from PySide6.QtGui import QKeySequence, QShortcut

        for sc in getattr(self, "_shortcut_objs", {}).values():
            sc.setEnabled(False)
            sc.deleteLater()
        self._keys = shortcuts.load(self.config)
        self._shortcut_objs = {}
        usb_keys = [b["key"] for b in usb_buttons.load(self.config).values()]
        for aid, key in self._keys.items():
            if not key:
                continue
            if any(usb_buttons.same_key(key, k) for k in usb_keys):
                # 같은 키를 USB 버튼이 쓰면 USB 버튼이 먼저 (한 번 눌러 두 동작이 겹치지 않게)
                log.warning("단축키 %s(%s) 는 USB 버튼 키와 같아 쓰지 않습니다", aid, key)
                continue
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.setAutoRepeat(False)
            sc.activated.connect(lambda aid=aid: self._run_action(aid))
            self._shortcut_objs[aid] = sc
        if not hasattr(self, "_esc_fullscreen"):
            self._esc_fullscreen = QShortcut(QKeySequence("Esc"), self)
            self._esc_fullscreen.activated.connect(lambda: self._set_fullscreen(False))
        self._sync_shortcut_scope()
        self._apply_key_hints()

    def _sync_shortcut_scope(self) -> None:
        """촬영 화면 전용 단축키는 Pro 화면에서 끈다 (Pro 의 Space 등과 겹치지 않게)."""
        capture = self._mode == "capture"
        for aid, sc in self._shortcut_objs.items():
            sc.setEnabled(capture or shortcuts.scope(aid) == "all")
        self._esc_fullscreen.setEnabled(self._fullscreen)

    def _apply_key_hints(self) -> None:
        cb = self.control_bar
        hints = {"capture": cb.btn_capture, "record": cb.btn_record,
                 "freeze": cb.btn_freeze, "quad": cb.btn_quad, "mode": self.btn_mode_pro,
                 "newPatient": self.btn_new_patient, "peaking": self.smart_bar.btn_peaking}
        for aid, w in hints.items():
            base = w.property("tipBase")
            if base is None:
                base = w.toolTip()
                w.setProperty("tipBase", base)
            key = self._keys.get(aid)
            w.setToolTip(f"{base} ({key})" if key else base)
        for code, b in cb.eye_buttons.items():
            hints[f"eye{code}"] = b
        for aid, w in hints.items():
            if aid.startswith("eye"):
                base = w.property("tipBase")
                if base is None:
                    base = w.toolTip()
                    w.setProperty("tipBase", base)
                key = self._keys.get(aid)
                w.setToolTip(f"{base} ({key})" if key else base)

    def _run_action(self, aid: str) -> None:
        cb = self.control_bar
        if aid.startswith("eye"):
            code = aid[3:]
            if self.mode_stack.currentIndex() == 1:
                self.pro.c_eye.setCurrentIndex(["NA", "OD", "OS"].index(code))
            else:
                cb.set_eye(code)
                self._note_manual_eye(code)
            self.statusBar().showMessage(f"안구: {'미지정' if code == 'NA' else code}", 2000)
        elif aid == "capture" and cb.btn_capture.isEnabled():
            self._on_capture()
        elif aid == "record" and cb.btn_record.isEnabled():
            cb.btn_record.toggle()
        elif aid == "pause" and cb.btn_pause.isVisible() and cb.btn_pause.isEnabled():
            cb.btn_pause.toggle()
        elif aid == "freeze" and cb.btn_freeze.isEnabled():
            self._on_freeze()
        elif aid == "quad":
            if cb.btn_quad.isChecked():
                cb.btn_quad.setChecked(False)
            else:
                cb.start_split(int(getattr(self.config, "split_count", 4) or 4))
        elif aid == "smart":
            self.smart_bar.cycle_mode()
        elif aid == "peaking":
            self.smart_bar.btn_peaking.toggle()
        elif aid == "fullscreen":
            self._set_fullscreen(not self._fullscreen)
        elif aid == "mode":
            self.act_pro.toggle()
        elif aid == "patients":
            self.set_mode("patients")
        elif aid == "newPatient":
            self.patient_panel.new_patient()

    # ---- 슬릿램프 USB 버튼 (app/usb_buttons.py) ----
    def _focus_capture_view(self) -> None:
        """촬영 화면을 열 때 키보드 초점을 영상으로 — 검색칸에 남아 USB 버튼이 무시되지 않게."""
        fw = QApplication.focusWidget()
        if fw is None or fw is self or not self.mode_stack.widget(0).isAncestorOf(fw) or fw is self.patient_panel.search:
            (self.quad_view if self._quad else self.video_view).setFocus(Qt.FocusReason.OtherFocusReason)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if not getattr(self, "_focused_once", False) and self._mode == "capture":
            self._focused_once = True
            QTimer.singleShot(0, self._focus_capture_view)

    def _usb_context(self) -> str | None:
        """USB 버튼을 지금 실행해도 되면 None, 아니면 그 이유 (window · screen · text)."""
        from PySide6.QtWidgets import (QAbstractSpinBox, QComboBox, QKeySequenceEdit, QLineEdit,
                                       QPlainTextEdit, QTextEdit)
        viewer = self._capture_viewer
        if viewer is not None and QApplication.activeWindow() is viewer:
            self._close_capture_viewer()          # 촬영 뒤 보기 창: USB 버튼이 오면 닫고 바로 다음 동작 (다시 촬영 등)
        elif QApplication.activeWindow() is not self or QApplication.activeModalWidget() is not None:
            return "window"                       # 설정 · 안내 창 · 다른 프로그램
        if self._mode != "capture" or self.mode_stack.currentIndex() != 0:
            return "screen"                       # 촬영 화면에서만
        fw = QApplication.focusWidget()
        if isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QKeySequenceEdit)) or (
                isinstance(fw, QComboBox) and fw.isEditable()):
            return "text"                         # 환자 검색 등 글자 입력 중
        return None

    def _on_usb_ignored(self, bid: str, reason: str) -> None:
        name = usb_buttons.button_label(bid)
        action = self.usb_filter.buttons.get(bid, {}).get("action", "none")
        if usb_buttons.is_eye_action(action):
            # 좌우 신호: 다른 키보드의 같은 키(device)는 버리고, 입력칸 · 다른 화면 · 다른 창에서 온 신호는 보류해 두었다가
            # 촬영 화면으로 돌아오면 적용 (신호를 놓친 채 예전 눈으로 찍지 않게)
            if reason == "device" or not self.config.usb_eye_auto:
                return
            code = usb_buttons.EYE_ACTIONS[action]
            self._eye_pending = code
            self._refresh_eye_status()
            where = {"text": "입력칸에 글자를 쓰는 중", "screen": "촬영 화면이 아니라서", "window": "다른 창이 앞에 있어"}.get(reason, "")
            self.statusBar().showMessage(f"슬릿램프 좌우 신호 {code} — {where} 보류했습니다 · 촬영 화면으로 돌아오면 적용", 5000)
            return
        if reason == "text":
            self.statusBar().showMessage(f"USB {name}: 입력칸에 글자를 쓰는 중이라 실행하지 않았습니다 — "
                                         "영상 화면을 한 번 누른 뒤 다시 누르세요", 5000)
        elif reason == "screen":
            self.statusBar().showMessage(f"USB {name}: 촬영 화면에서만 동작합니다", 3000)

    def _on_usb_button(self, bid: str, action: str) -> None:
        """USB 버튼 → 촬영 도크 버튼과 같은 동작 (녹화 시작/정지 · 스틸 정지/해제 · 사진 캡처), 좌우 신호 → 검사눈."""
        if usb_buttons.is_eye_action(action):
            code = usb_buttons.EYE_ACTIONS[action]
            if not self.config.usb_eye_auto:
                self.statusBar().showMessage(f"슬릿램프 좌우 신호 {code} 를 받았지만 자동 인식이 꺼져 있습니다 (설정 → USB 버튼)", 4000)
                return
            if self._usb_context() is not None:  # 누른 뒤 적용 전에 화면이 바뀐 경우 → 보류
                self._eye_pending = code
                self._refresh_eye_status()
                return
            self._apply_device_eye(code)
            return
        if self._usb_context() is not None:      # 누른 뒤 실행 전에 화면이 바뀐 경우
            return
        cb = self.control_bar
        name = usb_buttons.button_label(bid)
        btn = {"record": cb.btn_record, "freeze": cb.btn_freeze, "capture": cb.btn_capture}.get(action)
        if btn is None:
            return
        if not btn.isEnabled():
            why = "멀티 화면" if self._multi else ("동영상 파일 마무리 중" if self._pending_video else "환자 · 카메라 확인")
            self.statusBar().showMessage(f"USB {name}: 지금은 '{usb_buttons.action_label(action)}' 을 쓸 수 없습니다 ({why})", 4000)
            return
        self.events.log("usb_button", button=bid, action=action)
        self._run_action(action)
        if action == "record":
            text = "녹화 시작" if self._recording else "녹화 정지"
        elif action == "freeze":
            text = ("분할 칸 채움" if self._quad else
                    "스틸 화면 정지 (저장하지 않음)" if self._single_frozen else "라이브로 복귀")
        else:
            text = "사진 캡처"
        self.statusBar().showMessage(f"USB {name}: {text}", 3000)

    # ---- 슬릿램프 좌우 신호 → 검사눈 (F6 → OD · F7 → OS) ----
    def _usb_device_gate(self, qt_key: int):
        """좌우 신호가 슬릿램프 장치(Raw Input 확인)에서 왔는가 — True · False · None(확인 못 함 = 받음)."""
        if not getattr(self.config, "usb_device_check", True):
            return None
        return self.usb_raw.classify(qt_key)

    def _apply_device_eye(self, code: str) -> None:
        """슬릿램프 신호로 검사눈을 정한다 — 직접 고를 때와 같은 set_eye 를 거쳐 화면 표시 = 저장값."""
        self._eye_src = "device"
        self._eye_stale = False
        self._eye_pending = None
        self._eye_signal_at = time.time()
        self.control_bar.set_eye(code)                # 토글이 아니라 늘 그 눈으로 (OD 신호가 두 번 와도 OD)
        self.events.log("usb_eye", eye=code, recording=self._recording, frozen=self._single_frozen)
        msg = f"슬릿램프 좌우 신호: {code} ({'우안' if code == 'OD' else '좌안'})"
        if self._recording and self._video_eye and code != self._video_eye:
            s = int(time.monotonic() - self._rec_started)
            if not self._video_switches or self._video_switches[-1][1] != code:
                self._video_switches.append((s, code))
            msg += f" · 녹화 중 전환 — 영상은 시작 눈 {self._video_eye} 로 저장하고 전환 시각을 코멘트에 적습니다"
        if self._single_frozen and self._freeze_eye is not None and self._freeze_eye != code:
            msg += f" · 정지 화면은 찍은 순간의 눈 {self._freeze_eye} 로 저장됩니다"
        self.statusBar().showMessage(msg, 5000)
        self._refresh_eye_status()

    def _note_manual_eye(self, code: str) -> None:
        """OD · OS 를 직접 골랐을 때(버튼 · 단축키) — 정지 화면이면 그 사진의 눈도 고친 값으로 (사용자가 바로잡은 것)."""
        self._eye_src = "manual"
        self._eye_stale = False
        self._eye_pending = None
        if self._single_frozen:
            self._freeze_eye = code
        if self._recording and self._video_eye and code != self._video_eye:
            s = int(time.monotonic() - self._rec_started)
            if not self._video_switches or self._video_switches[-1][1] != code:
                self._video_switches.append((s, code))
        self._refresh_eye_status()

    def _on_app_activated(self) -> None:
        """다른 프로그램에서 돌아옴 — 그 사이 슬릿램프가 움직였어도 신호를 못 받았으므로 '확인 필요'."""
        if getattr(self, "_closing", False):
            return
        if self.config.usb_eye_auto and self._eye_src == "device" and self._eye_pending is None:
            self._eye_stale = True
            self._refresh_eye_status()
        QTimer.singleShot(0, self._usb_eye_resume)

    def changeEvent(self, e) -> None:  # noqa: N802
        super().changeEvent(e)
        if e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(0, self._usb_eye_resume)      # 설정 창 등을 닫고 돌아옴 → 보류한 좌우 신호 적용

    def _usb_eye_resume(self) -> None:
        """보류한 좌우 신호가 있고 지금 촬영 화면이면 적용한다 (입력칸 · 다른 화면 · 다른 창에서 돌아온 뒤)."""
        if getattr(self, "_closing", False) or getattr(self, "_eye_pending", None) is None:
            return
        if not self.config.usb_eye_auto:
            self._eye_pending = None
            self._refresh_eye_status()
            return
        if self._usb_context() is not None:
            return
        code = self._eye_pending
        self._apply_device_eye(code)
        self.statusBar().showMessage(f"보류했던 슬릿램프 좌우 신호 적용: {code}", 4000)

    def _on_usb_device_change(self, arrived: bool) -> None:
        """슬릿램프 장치가 꽂힘 · 빠짐 (Windows Raw Input) — 다시 꽂히면 위치를 알 수 없으니 신호로 정한 눈은 미확인으로."""
        if getattr(self, "_closing", False):
            return
        if arrived:
            if self._eye_src == "device":
                self.control_bar.set_eye("NA")
            self._eye_src = "unknown"
            self._eye_stale = False
            self._eye_pending = None
            self.statusBar().showMessage("슬릿램프 버튼 장치 연결됨 — 좌우 위치 미확인: 슬릿램프를 움직여 신호를 받거나 "
                                         "OD · OS 를 직접 고르세요", 8000)
        else:
            if self._eye_src == "device":
                self._eye_stale = True
            self.statusBar().showMessage("슬릿램프 버튼 장치가 빠졌습니다 — 좌우 자동 인식이 멈춥니다 (OD · OS 를 직접 고르세요)", 8000)
        self._refresh_eye_status()

    def _capture_eye(self) -> str:
        """사진에 적을 검사눈 — 정지 화면이면 찍은 순간의 눈, 아니면 지금 고른 눈."""
        if self._single_frozen and self._freeze_eye is not None:
            return self._freeze_eye
        return self.control_bar.current_eye()

    def _video_note(self) -> str:
        if not self._video_switches:
            return ""
        steps = " → ".join(f"{code} {s // 60}:{s % 60:02d}" for s, code in self._video_switches)
        return f"녹화 중 좌우 전환: {self._video_eye} 시작 → {steps} (영상 전체는 시작 눈 {self._video_eye} 로 저장)"

    def eye_status(self) -> dict:
        """좌우 자동 인식 상태 (시험 · 표시용)."""
        return {"eye": self.control_bar.current_eye(), "source": self._eye_src, "stale": self._eye_stale,
                "pending": self._eye_pending, "freeze_eye": self._freeze_eye}

    def _refresh_eye_status(self) -> None:
        """촬영 도크의 상태 칩과 영상 HUD 눈 칩 — 미확인 · 자동 · 수동 · 확인 필요 · 정지 화면."""
        if not hasattr(self, "_eye_src"):
            return
        cb = self.control_bar
        eye = cb.current_eye()
        auto = bool(getattr(self.config, "usb_eye_auto", True))
        if not auto:
            level, text, note = "off", "", ""
            tip = "슬릿램프 좌우 자동 인식 꺼짐 — OD · OS 를 직접 고릅니다 (설정 → USB 버튼)"
        elif self._eye_pending is not None:
            level, text, note = "stale", f"신호 {self._eye_pending} 보류", "확인 필요"
            tip = "입력칸 · 다른 화면 · 다른 창에서 받은 슬릿램프 좌우 신호 — 촬영 화면으로 돌아오면 적용합니다"
        elif self._eye_src == "unknown":
            level, text = "unknown", "미확인"
            note = "미확인" if eye == "NA" else "확인 필요"
            tip = ("슬릿램프 좌우 신호를 아직 받지 못했습니다 — 슬릿램프를 환자 눈 쪽으로 움직여 신호를 받거나 "
                   "OD · OS 를 직접 고르세요 (처음 위치를 OD 로 짐작하지 않습니다)")
        elif self._eye_src == "device" and self._eye_stale:
            level, text, note = "stale", "확인 필요", "확인 필요"
            tip = ("창이 비활성 상태였거나 장치가 빠져 슬릿램프 좌우 신호를 놓쳤을 수 있습니다 — 눈 위치를 확인하세요 "
                   "(다음 신호나 직접 고르면 풀립니다)")
        elif self._eye_src == "device":
            level, text, note = "device", "자동", "자동"
            at = time.strftime("%H:%M:%S", time.localtime(self._eye_signal_at)) if self._eye_signal_at else ""
            tip = f"슬릿램프 좌우 신호로 {eye} ({at})"
        else:
            level, text, note = "manual", "수동", ""
            tip = "OD · OS 를 직접 골랐습니다 — 다음 슬릿램프 좌우 신호가 오면 자동으로 바뀝니다"
        cb.set_eye_status(level, text, tip)
        hud_eye = eye
        if self._single_frozen and self._freeze_eye is not None and self._freeze_eye != eye:
            hud_eye, note = self._freeze_eye, "정지 화면"       # 찍은 순간의 눈 — 이 사진은 이 눈으로 저장
        self.video_view.set_hud(eye=hud_eye, eye_note=note)

    def _set_fullscreen(self, on: bool) -> None:
        """영상 전체 화면: 창을 화면 가득 채우고 상단바(환자 검색 포함)·썸네일·상태바를 숨김 (Esc 로 해제)."""
        if on == self._fullscreen:
            return
        self._fullscreen = on
        for w in (self.topbar, self.brand_stripe, self.thumbnail_panel, self.statusBar()):
            if w is not None:
                w.setVisible(not on)
        self.smart_bar.setVisible(not on and bool(self.config.smart_bar_visible))
        self.ref_bar.setVisible(not on and bool(self.config.ref_bar_visible))
        if on:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
            self.statusBar().showMessage("전체 화면 — Esc 로 나가기", 3000)
        else:
            self.showMaximized() if getattr(self, "_was_maximized", False) else self.showNormal()
        self._esc_fullscreen.setEnabled(on)

    def _open_shortcuts(self, parent=None) -> None:
        parent = parent if parent is not None and parent is not False else self
        taken = {b["key"]: f"슬릿램프 USB {usb_buttons.button_label(bid)}({usb_buttons.action_label(b['action'])})"
                 for bid, b in usb_buttons.load(self.config).items() if b["key"]}
        dlg = ShortcutsDialog(self._keys, parent, taken=taken)
        if dlg.exec():
            self.config.shortcuts = dlg.values()
            try:
                self.config.save()
            except Exception:  # noqa: BLE001
                log.warning("shortcut save failed", exc_info=True)
            self._install_shortcuts()
            self.statusBar().showMessage("단축키를 저장했습니다", 3000)

    # ---- 카메라 스레드 ----
    def _source_factory(self):
        cfg = self.config
        mode = cfg.source_mode

        def factory():
            if mode == "synthetic":
                return SyntheticSource(cfg.capture_width or 1280, cfg.capture_height or 720, 30.0)
            if mode == "file":
                return FileSource(cfg.test_file_path, loop=True)
            return DeviceSource(cfg.camera_index, cfg.camera_backend,
                                cfg.capture_width, cfg.capture_height)

        return factory

    def _pick_camera(self) -> None:
        """카메라 자동 선택: 저장된 이름으로 다시 찾고, 처음이면 소니(USB 스트리밍) 카메라를 우선."""
        cfg = self.config
        if cfg.source_mode != "device":
            return
        names = device_names()
        if not names:
            return
        idx = None
        if cfg.camera_name and cfg.camera_name in names:
            idx = names.index(cfg.camera_name)          # 장치 순서가 바뀌어도 같은 카메라
        elif cfg.camera_name:
            # 저장한 카메라가 안 보이면 같은 번호의 다른 장치(노트북 웹캠 등)로 몰래 바뀌지 않게 알린다
            cur = names[cfg.camera_index] if 0 <= cfg.camera_index < len(names) else "없음"
            msg = (f"설정한 카메라 '{cfg.camera_name}' 를 찾지 못했습니다.\n"
                   f"카메라 전원과 USB 연결을 확인하세요. 지금은 '{cur}' 로 연결합니다.\n\n"
                   f"연결된 카메라: {', '.join(names)}")
            log.warning(msg)
            self.statusBar().showMessage(msg.split("\n\n")[0].replace("\n", " "), 15000)
            box = QMessageBox(QMessageBox.Icon.Warning, "카메라", msg, QMessageBox.StandardButton.Ok, self)
            btn_pick = box.addButton("카메라 선택…", QMessageBox.ButtonRole.ActionRole)
            btn_pick.clicked.connect(lambda: QTimer.singleShot(0, self._open_settings))
            box.setModal(False)                 # 시작을 막지 않게 (창은 떠 있고 확인만 누르면 됨)
            box.show()
            self._camera_warning = box
            return
        elif not cfg.camera_name:
            idx = find_sony_index(names)
            if idx is not None:
                self.statusBar().showMessage(f"소니 카메라를 찾아 연결합니다: {names[idx]}", 8000)
        if idx is None or idx >= len(names):
            return
        if idx != cfg.camera_index or cfg.camera_name != names[idx]:
            cfg.camera_index, cfg.camera_name = idx, names[idx]
            try:
                cfg.save()
            except Exception:  # noqa: BLE001
                log.warning("camera selection save failed", exc_info=True)

    def _start_camera(self) -> None:
        self._camera_ok = False
        self.cam_thread = QThread(self)
        # 저장된 카메라 속성(노출 · 게인 · WB …)은 카메라를 열 때 다시 적용
        self.worker = CameraWorker(self._source_factory(),
                                   capture_presets.device_requests(self.config.camera_props or {})
                                   if self.config.camera_props else None)
        self.worker.moveToThread(self.cam_thread)
        self.cam_thread.started.connect(self.worker.run)
        self.worker.frame_ready.connect(self._on_frame_ready)
        self.worker.opened.connect(self._on_camera_opened)
        self.worker.error.connect(self._on_camera_error)
        self.worker.stopped.connect(self.cam_thread.quit)
        self.cam_thread.start()

    def _stop_camera(self) -> None:
        # run() 루프가 워커 스레드의 이벤트 루프를 점유하므로 QueuedConnection은
        # 처리되지 않는다. _running(bool)을 직접 내려 루프가 즉시 빠져나오게 한다.
        if self.worker is not None:
            self.worker.stop()
            # 늦게 멈추는 이전 워커의 프레임·오류가 새 카메라 화면에 섞이지 않게 연결을 끊는다
            for sig, slot in ((self.worker.frame_ready, self._on_frame_ready),
                              (self.worker.opened, self._on_camera_opened),
                              (self.worker.error, self._on_camera_error)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        if self.cam_thread is not None:
            self.cam_thread.quit()
            if not self.cam_thread.wait(3000):
                log.warning("camera thread did not stop within timeout")
        self.worker = None
        self.cam_thread = None

    def _restart_camera(self) -> None:
        self._reconnect_timer.stop()
        self._reconnect_attempts = 0
        if self._recording:                  # 카메라·해상도가 바뀌기 전에 지금 녹화를 마무리해 저장
            self._stop_recording()
        self._stop_camera()
        self.video_view.clear()
        self.smart.reset()                   # 다른 카메라·해상도의 선명도 기준과 섞이지 않게
        self._start_camera()

    _RECONNECT_DELAYS_MS = (2000, 3000, 5000, 8000, 12000)

    def _schedule_reconnect(self) -> None:
        """실제 장비 모드에서 카메라가 끊기면 점점 긴 간격으로 다시 연결을 시도한다
        (무한, 최대 12초 간격)."""
        if self._closing or self.config.source_mode != "device" or self._reconnect_timer.isActive():
            return
        delays = self._RECONNECT_DELAYS_MS
        delay = delays[min(self._reconnect_attempts, len(delays) - 1)]
        self._reconnect_attempts += 1
        self.control_bar.set_status(
            f"카메라 재연결 대기 중… ({delay // 1000}초 후, {self._reconnect_attempts}회)")
        self._reconnect_timer.start(delay)

    def _auto_reconnect(self) -> None:
        if self._closing or (self.worker is not None and self._camera_ok):
            return
        log.info("camera auto-reconnect attempt %s", self._reconnect_attempts)
        self._stop_camera()
        self._start_camera()

    # ---- 카메라 신호 처리 ----
    def _on_camera_opened(self, w: int, h: int, fps: float) -> None:
        self._frame_size = (w, h)
        self._camera_ok = True
        if self._reconnect_attempts:
            self.statusBar().showMessage("카메라가 다시 연결되었습니다", 5000)
        self._reconnect_attempts = 0
        self.control_bar.set_status(f"카메라 {w}x{h} @ {fps:.0f}fps")

    def _on_frame_ready(self, frame) -> None:
        self._last_frame = frame
        self._frame_times.append(time.monotonic())
        if self._recording:
            self.recorder.feed(frame)
            if self._first_video_frame is None:
                self._first_video_frame = frame
        if self.mode_stack.currentIndex() == 1:
            self.pro.set_live_frame(frame)
        if self._quad:
            self.quad_view.set_live_frame(frame)
        elif not self._paused and not self._single_frozen:
            self.video_view.set_frame(frame)
        if self._smart_wanted():
            self.smart.submit(frame)

    def _on_camera_error(self, msg: str) -> None:
        self._camera_ok = False
        self.control_bar.set_status(f"오류: {msg}")
        self.statusBar().showMessage(f"카메라 오류: {msg}", 8000)
        if self._recording:                  # 끊긴 시점까지 녹화된 부분은 저장한다
            self._stop_recording()
        self._schedule_reconnect()

    def _estimate_fps(self) -> float:
        if len(self._frame_times) < 2:
            return 30.0
        span = self._frame_times[-1] - self._frame_times[0]
        if span <= 0:
            return 30.0
        fps = (len(self._frame_times) - 1) / span
        return max(5.0, min(60.0, fps))

    # ---- 환자 ----
    def _on_patient_changed(self, patient) -> None:
        rec = self._video_patient
        if self._recording and (patient is None or rec is None or patient.id != rec.id):
            # 다른 환자로 바꾸면 녹화를 끝내 앞 환자에게 저장 — 새 환자의 눈이 앞 환자 영상에 섞이지 않게 (웹과 같음)
            self._stop_recording()
            self.statusBar().showMessage("환자를 바꿔 녹화를 끝내고 이전 환자에게 저장했습니다.", 8000)
        self._current_patient = patient
        self.control_bar.set_capture_enabled(patient is not None)
        self.btn_edit_patient.setEnabled(patient is not None)
        if self._recording:                  # 녹화 중에는 언제든 멈출 수 있어야 한다
            self.control_bar.btn_record.setEnabled(True)
        self.video_view.set_hud(patient=(f"{patient.name}  ·  {patient.chart_no}" if patient and patient.chart_no
                                         else (patient.name if patient else "환자 미선택")))
        self.thumbnail_panel.set_patient(patient.id if patient else None)
        self.pro.set_patient(patient)
        self.multi_pro.set_patient(patient)
        if self._mode == "exam":
            self.exam_screen.set_patient(patient)
        self.agent.new_session()
        self.agent_bar.set_checklist(self.agent.core.checklist)
        self.agent_bar.clear_candidates()
        self._agent_candidates = None
        self.events.log("patient_changed", has_patient=patient is not None)

    # ---- 상단 질환 아이콘 (app/ui/dx_bar.py) ----
    DX_MAX_REFS = 4                      # 한 질환에서 멀티 화면에 같이 놓는 사진 수 (참조 줄에서 더 고를 수 있음)

    def _refresh_dx_bar(self) -> None:
        """샘플 질환 · 등록한 참조 분류로 상단 아이콘 줄을 다시 만든다 (샘플 가져오기 · 참조 등록 뒤)."""
        try:
            self.dx_bar.set_categories(sample_import.sample_categories(self.db), self.db.references.categories())
        except Exception:  # noqa: BLE001
            log.warning("dx bar refresh failed", exc_info=True)

    def _on_dx_clicked(self, source: str, category: str) -> None:
        """질환 아이콘: 참조 줄을 열어 그 질환 사진을 보이고, 지금 환자 사진과 함께 멀티 화면으로.
        같은 아이콘을 다시 누르면 그 질환 사진을 뺀다."""
        log.info("버튼: 질환 참조 %s/%s", source, category)
        try:
            self._dx_select(source, category)
        except Exception as e:  # noqa: BLE001
            log.exception("질환 참조 실패")
            QMessageBox.warning(self, "질환 참조", f"'{category}' 참조 사진을 여는 중 문제가 생겼습니다.\n{e}")

    def _dx_select(self, source: str, category: str) -> None:
        key = (source, category)
        if self.dx_bar.active() == key:
            self.dx_bar.set_active(None)
            self.ref_bar.clear_selection()                 # → _on_refs_changed → 환자 사진만 남거나 멀티 닫힘
            self.statusBar().showMessage(f"'{category}' 참조 사진을 뺐습니다", 3000)
            return
        if self._mode != "capture":
            self.set_mode("capture")
        self._set_ref_bar_visible(True)
        if not self.ref_bar.show_category(source, category):
            self.ref_bar.refresh()
            if not self.ref_bar.show_category(source, category):
                self.statusBar().showMessage(f"'{category}' 참조 사진이 없습니다", 4000)
                return
        self.dx_bar.set_active(key)
        p = self._current_patient
        if p is not None and not self.thumbnail_panel.selected_captures():
            # 고른 환자 사진이 없으면 가장 최근 사진을 골라 같이 놓는다 (오른쪽 목록에서 더 고를 수 있음)
            latest = next((c for c in self.db.captures.list_by_patient(p.id, order="desc") if c.media_type == "photo"), None)
            if latest is not None:
                self.thumbnail_panel.toggle_selection(latest)
        refs = self.ref_bar.choose_only_current(self.DX_MAX_REFS)   # → _on_refs_changed → 멀티
        n_pt = len(self.thumbnail_panel.selected_captures())
        self.statusBar().showMessage(
            f"'{category}' 참조 사진 {len(refs)}장" + (f" + 환자 사진 {n_pt}장을 멀티 화면에 놓았습니다" if n_pt
                                                   else " — 오른쪽 목록에서 환자 사진을 누르면 같이 놓입니다")
            + "  ·  아래 참조 줄에서 사진을 더 고르거나 뺄 수 있습니다", 6000)

    # ---- 촬영 직후 큰 화면 보기 ----
    def _show_capture_viewer(self, cap) -> None:
        """사진을 찍은 직후 큰 화면(창 최대화)으로 보여 준다. '다시 촬영' · Esc · 촬영 키 · USB 버튼으로 촬영 화면 복귀."""
        old = self._capture_viewer
        if old is not None:
            self._capture_viewer = None
            old.close()
        keys = [k for k in (shortcuts.load(self.config).get("capture", ""),) if k]
        dlg = ImageViewerDialog(cap, self, after_capture=True, capture_keys=keys)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.retakeRequested.connect(lambda d=dlg: self._retake_from_viewer(d))
        dlg.finished.connect(lambda code, d=dlg, c=cap: self._on_capture_viewer_closed(d, c, code))
        self._capture_viewer = dlg
        dlg.showMaximized()
        dlg.raise_()
        dlg.activateWindow()

    def _retake_from_viewer(self, dlg) -> None:
        """보기 창에서 촬영 키 → 창을 닫고 바로 다시 찍는다."""
        dlg.accept()
        QTimer.singleShot(0, lambda: self._run_action("capture"))

    def _on_capture_viewer_closed(self, dlg, cap, code: int) -> None:
        if self._capture_viewer is dlg:
            self._capture_viewer = None
        if code == ImageViewerDialog.Deleted:
            self._delete_capture(cap)
        if self._mode == "capture":
            self._focus_capture_view()

    def _close_capture_viewer(self) -> bool:
        """촬영 뒤 보기 창이 떠 있으면 닫는다 (USB 버튼 · 단축키가 바로 다음 동작을 하게). 닫았으면 True."""
        dlg = self._capture_viewer
        if dlg is None:
            return False
        self._capture_viewer = None
        dlg.accept()
        return True

    # ---- Pro 모드 ----
    def _set_ref_bar_visible(self, on: bool, save: bool = True) -> None:
        """참조 줄 보이기/숨기기 — 숨겨도 고른 참조 사진은 멀티 화면에 그대로 있다."""
        self.config.ref_bar_visible = bool(on)
        self.ref_bar.setVisible(bool(on) and not self._fullscreen)
        self.control_bar.set_refs_visible(bool(on))
        if save:
            self._save_config_quietly()

    def _set_smart_bar_visible(self, on: bool, save: bool = True) -> None:
        """Smart 도구줄 보이기/숨기기 — 숨겨도 자동 촬영·피킹 설정은 그대로 동작한다."""
        self.config.smart_bar_visible = bool(on)
        self.smart_bar.setVisible(bool(on) and not self._fullscreen)
        self.control_bar.set_smart_visible(bool(on))
        if save:
            try:
                self.config.save()
            except Exception:  # noqa: BLE001
                log.warning("smart bar visibility save failed", exc_info=True)

    def _on_pro_toggled(self, on: bool) -> None:
        self.set_mode("pro" if on else "capture")

    def set_mode(self, name: str) -> None:
        """화면 전환: home(아이콘 메뉴) · capture(촬영) · pro(Pro 검토) · exam(검사 기록)."""
        if name not in MODES:
            return
        if self._mode == "exam" and name != "exam":
            self.exam_screen.flush()             # 적던 소견 저장
        self._mode = name
        self.mode_stack.setCurrentIndex(MODES.index(name))
        self.act_pro.blockSignals(True)
        self.act_pro.setChecked(name == "pro")
        self.act_pro.blockSignals(False)
        if hasattr(self, "_shortcut_objs"):
            self._sync_shortcut_scope()
        p = self._current_patient
        if name == "pro":
            self.pro.reload(select_last=False)
            if self._last_frame is not None:
                self.pro.set_live_frame(self._last_frame)
            self.pro.setFocus()
        elif name == "capture":
            self.thumbnail_panel.set_patient(p.id if p else None)
            self._focus_capture_view()
            QTimer.singleShot(0, self._usb_eye_resume)      # 다른 화면에서 받아 둔 좌우 신호 적용
        elif name == "exam":
            self.exam_screen.set_patient(p)
        elif name == "patients":
            self.patient_list.reload()
            self.patient_list.set_current(p.id if p else None)
            self.patient_list.search.setFocus()
        # 화면 버튼(홈 · 촬영 · Pro 검토 · 검사 기록 · 환자 목록) 중 지금 화면이 켜짐
        self.mode_buttons.get(name, self.btn_mode_home).setChecked(True)
        self.statusBar().showMessage({"home": "홈", "capture": "촬영", "pro": "Pro 모드", "exam": "검사 기록",
                                      "patients": "환자 목록"}[name], 3000)

    def mode(self) -> str:
        return self._mode

    # ---- 환자 목록 화면 ----
    def _open_patient(self, pid: int, mode: str) -> None:
        if self.patient_panel.select_patient(pid):
            self.set_mode(mode)

    def _new_patient_from_list(self) -> None:
        if self.patient_panel.new_patient():
            self.patient_list.reload()
            p = self._current_patient
            self.patient_list.set_current(p.id if p else None)

    def _edit_patient_from_list(self, pid: int) -> None:
        if self.patient_panel.select_patient(pid):
            self.patient_panel.edit_patient()
            self.patient_list.reload()
            self.patient_list.set_current(pid)

    def _on_home_action(self, aid: str) -> None:
        """홈 화면 아이콘 타일."""
        if aid == "newPatient":
            if self.patient_panel.new_patient():
                self.set_mode("capture")
        elif aid == "patients":
            self.set_mode("patients")
        elif aid in ("capture", "pro", "exam"):
            self.set_mode(aid)
        elif aid == "settings":
            self._open_settings()
        elif aid == "shortcuts":
            self._open_shortcuts()

    def _on_pro_saved(self) -> None:
        p = self._current_patient
        self.thumbnail_panel.set_patient(p.id if p else None)

    def _on_pro_patient_updated(self, patient) -> None:
        self._current_patient = patient
        self.patient_panel.refresh_current(patient)
        self.video_view.set_hud(patient=f"{patient.name}  ·  {patient.chart_no}" if patient.chart_no else patient.name)

    # ---- 컨트롤 ----
    def _on_pause(self, checked: bool) -> None:
        self._paused = checked
        self.events.log("pause", on=checked)
        self._refresh_hud_state()
        self._sync_smart_view()

    def _on_quad_toggled(self, on: bool) -> None:
        if on and self._multi:
            self._exit_multi(clear=False)
        self._quad = on
        self.quad_view.set_count(self.control_bar.split_count)   # 켜고 끌 때마다 모두 비우고 1번 칸부터
        self.control_bar.set_quad_full(False)
        self.view_stack.setCurrentIndex(1 if on else 0)
        # 모드 전환 시 단일-정지 상태/버튼 초기화
        self._single_frozen = False
        self._freeze_frame = None
        self._freeze_eye = None
        self.control_bar.set_freeze_state(False)
        self._refresh_hud_state()
        self._refresh_eye_status()
        self._sync_smart_view()
        self.control_bar.set_status(
            f"{self.quad_view.count()}분할 · '정지영상'으로 각 칸을 채우세요" if on else "단일 모드")

    def _on_quad_cell_clicked(self, idx: int) -> None:
        if self._quad:
            self.quad_view.set_active(idx)               # 누른 칸을 다시 라이브로 (그 칸만 다시 촬영)
            self.control_bar.set_quad_full(False)
            self.control_bar.set_status(f"{idx + 1}번 칸을 다시 촬영합니다 · '정지영상'으로 채우세요")

    def _on_freeze(self) -> None:
        if self._last_frame is None or self._multi:
            return
        if self._quad:
            if self.quad_view.is_full():                  # 칸이 다 찼을 때 누르면 '처음부터'
                self._on_clear_quad()
                return
            if self.quad_view.freeze_active(self.control_bar.current_eye()):
                sounds.play("freeze")
                if self.quad_view.is_full():
                    self.control_bar.set_quad_full(True)
                    self.control_bar.set_status(f"{self.quad_view.count()}칸 모두 채움 · '저장' 하거나, 칸을 눌러 그 칸만 다시 · '처음부터'로 전체 다시")
                else:
                    self.control_bar.set_status("정지영상 추가됨 (다음 칸으로 이동)")
            else:
                self.control_bar.set_status("라이브 영상을 기다리는 중입니다")
            return
        # 단일 모드: 현재 프레임을 정지영상으로 고정 / 재개 토글
        self._single_frozen = not self._single_frozen
        if self._single_frozen:
            sounds.play("freeze")
            self.events.log("freeze", scene_label=self.agent.scene().label,
                            quality=self.smart.last_quality())
            self._freeze_frame = self._last_frame
            self._freeze_eye = self.control_bar.current_eye()    # 찍은 순간의 눈 — 정지 중 슬릿램프가 움직여도 이 사진은 이 눈
            self.video_view.set_frame(self._freeze_frame)
            self.control_bar.set_freeze_state(True)
            self.control_bar.set_status("정지영상 (▶라이브로 복귀 · 사진 캡처로 저장)")
        else:
            self._freeze_frame = None
            self._freeze_eye = None
            self.control_bar.set_freeze_state(False)
            self.control_bar.set_status("라이브")
        self._refresh_hud_state()
        self._refresh_eye_status()
        self._sync_smart_view()

    def _on_save_quad(self) -> None:
        if self._current_patient is None:
            QMessageBox.information(self, "확인", "환자를 먼저 선택하세요.")
            return
        if not self.quad_view.has_any():
            QMessageBox.information(self, "확인", "저장할 정지영상이 없습니다.")
            return
        eye = self.control_bar.current_eye()
        n = self.quad_view.count()
        sounds.play("shutter")
        saved = 0
        try:
            for frame, cell_eye in self.quad_view.images_for_save(eye):
                cap = self.service.save_photo(frame, self._current_patient, eye=cell_eye, note=f"{n}분할")
                self.thumbnail_panel.add_capture(cap)
                saved += 1
            comp = self.quad_view.composite()
            if comp is not None:
                cap = self.service.save_photo(comp, self._current_patient, eye="NA", note=f"{n}분할 합성")
                self.thumbnail_panel.add_capture(cap)
                saved += 1
        except Exception as e:  # noqa: BLE001
            log.exception("quad save failed")
            QMessageBox.warning(self, "오류", f"분할 저장 실패: {e}")
            return
        self.control_bar.set_status(f"{n}분할 저장됨 (칸별+합성 {saved}장)")
        self.statusBar().showMessage(f"{n}분할 저장됨 (칸별 + 합성 {saved}장)", 4000)

    @staticmethod
    def _exec_menu(menu, pos):
        return menu.exec(pos)

    def _choose_split_count(self, anchor) -> int | None:
        """2 · 4 · 6 · 8 · 16칸 중에서 고른다. 고른 칸 수는 저장되어 다음부터 분할 버튼은 묻지 않고 바로 시작."""
        n = split_menu.choose(self.config, self, anchor, self._exec_menu)
        saved = bool(getattr(self.config, "split_count_saved", False))
        self.control_bar.count_saved = saved
        if n:
            self.control_bar.split_count = n
        self.control_bar.sync_quad_tip()
        if n:
            self.statusBar().showMessage(f"{n}분할로 저장 — 다음부터 분할 버튼은 바로 {n}분할 "
                                         "(칸 수 바꾸기: 분할 버튼 오른쪽 클릭 · 설정)", 6000)
        elif not saved:
            self.statusBar().showMessage("분할 버튼을 누를 때마다 칸 수를 묻습니다", 4000)
        return n

    # ---- 멀티 (고른 사진 + 참조 사진 한 번에 보기 · Pro 메뉴로 편집 · 분석) ----
    def _multi_items(self) -> list:
        """멀티 칸에 놓을 것: 오른쪽 목록에서 고른 사진(고른 순서) → 참조 줄에서 고른 참조 사진."""
        return self.thumbnail_panel.selected_captures() + [ref_capture(r) for r in self.ref_bar.selected_refs()]

    def _on_selection_changed(self, caps: list) -> None:
        if self._multi:
            items = self._multi_items()
            if items:
                self.multi_pro.set_grid(items)
            else:
                self._exit_multi(clear=False)
        if caps:
            self.statusBar().showMessage(f"{len(caps)}장 선택 · '멀티'를 누르면 한 화면에 모아 봅니다", 4000)

    def _on_refs_changed(self, refs: list) -> None:
        """참조 줄에서 고르면 바로 멀티 화면에 같이 (지금 환자 사진은 오른쪽 목록에서 고른 것)."""
        if not refs:
            self.dx_bar.set_active(None)          # 참조 사진을 다 빼면 상단 질환 아이콘도 꺼짐
        items = self._multi_items()
        if self._multi:
            if items:
                self.multi_pro.set_grid(items)
            else:
                self._exit_multi(clear=False)
        elif refs:
            self._enter_multi(items)
        if refs and not self.thumbnail_panel.selected_captures():
            self.statusBar().showMessage("오른쪽 목록에서 지금 환자 사진을 눌러 고르면 참조 사진과 나란히 봅니다", 5000)

    def _on_multi_toggled(self, on: bool) -> None:
        if not on:
            self._exit_multi(clear=True)              # 다시 누르면 선택까지 초기화
            self.statusBar().showMessage("멀티 초기화", 2500)
            return
        items = self._multi_items()
        if not items:
            self.control_bar.set_multi_mode(False)
            self.statusBar().showMessage("오른쪽 사진 목록에서 사진을 한 번씩 눌러 고르거나 아래 '참조' 줄에서 "
                                         "참조 사진을 고른 뒤 '멀티'를 누르세요", 5000)
            return
        self._enter_multi(items)

    def _multi_by_right_click(self) -> None:
        """오른쪽 클릭으로 멀티 화면 — '멀티' 버튼을 누른 것과 같음 (이미 멀티면 그대로)."""
        if self._multi:
            return
        items = self._multi_items()
        if not items:
            self.statusBar().showMessage("오른쪽 사진 목록에서 사진을 고르거나 사진 위에서 오른쪽 클릭하면 멀티 화면으로 봅니다", 5000)
            return
        self._enter_multi(items)
        self.statusBar().showMessage(f"멀티 화면 ({len(items)}장) — 사진 목록에서 오른쪽 클릭으로 더 넣기 · "
                                     "'멀티' 를 다시 누르면 닫기", 5000)

    def _enter_multi(self, items: list) -> None:
        if self._quad:
            self.control_bar.btn_quad.setChecked(False)
        self._multi = True
        self.multi_pro.set_px_per_mm(float(getattr(self.config, "px_per_mm", 0.0) or 0.0))
        self.multi_pro.set_grid(items)
        self.view_stack.setCurrentIndex(2)
        self.control_bar.set_multi_mode(True)

    def _exit_multi(self, clear: bool) -> None:
        self._multi = False
        self.view_stack.setCurrentIndex(1 if self._quad else 0)
        self.control_bar.set_multi_mode(False)
        self.multi_pro.set_grid([])
        if clear:
            self.thumbnail_panel.clear_selection()
            self.ref_bar.clear_selection()

    def _on_multi_cell_activated(self, cap) -> None:
        """멀티 칸을 두 번 누름 → 큰 화면으로 (참조 사진은 분류 · 제목을 창 제목에)."""
        if is_reference(cap):
            dlg = ImageViewerDialog(cap, self)
            dlg.setWindowTitle(f"참조 - {cap.label}")
            dlg.exec()
            return
        self._on_thumbnail_clicked(cap)

    def _on_save_multi(self) -> None:
        if self._current_patient is None:
            QMessageBox.information(self, "확인", "환자를 먼저 선택하세요.")
            return
        comp = self.multi_view.composite()
        if comp is None:
            QMessageBox.information(self, "확인", "저장할 사진이 없습니다.")
            return
        n = self.multi_view.count()
        cap = self.service.save_photo(comp, self._current_patient, eye="NA", note=f"멀티 {n}장")
        sounds.play("shutter")
        self.thumbnail_panel.add_capture(cap)
        self.statusBar().showMessage(f"멀티 화면({n}장)을 한 장의 사진으로 저장했습니다", 4000)

    # ---- 촬영 모드 (자주 쓰는 카메라 · 촬영 설정) ----
    def _on_preset_menu(self) -> None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        current = self.config.active_preset or capture_presets.DEFAULT_NAME
        acts = {}
        for p in capture_presets.all_presets(self.config):
            a = menu.addAction(p["name"])
            a.setCheckable(True)
            a.setChecked(p["name"] == current)
            acts[a] = p["name"]
        menu.addSeparator()
        a_save = menu.addAction("지금 설정을 새 모드로 저장…")
        a_edit = menu.addAction("모드 편집… (설정 → 촬영 모드)")
        anchor = self.control_bar.btn_preset
        pos = anchor.mapToGlobal(anchor.rect().topLeft())
        pos.setY(pos.y() - menu.sizeHint().height() - 4)
        chosen = self._exec_menu(menu, pos)
        if chosen in acts:
            self.apply_preset(acts[chosen])
        elif chosen is a_save:
            self._save_current_preset()
        elif chosen is a_edit:
            self._open_settings(tab="촬영 모드")

    def _save_current_preset(self, name: str | None = None) -> bool:
        if name is None:
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "촬영 모드 저장", "모드 이름 (예: 세극 조명 · 형광 · 마이봄)",
                                            text=self.config.active_preset or "")
            if not ok or not name.strip():
                return False
        preset = capture_presets.from_config(self.config, name)
        capture_presets.save(self.config, preset)
        self.config.active_preset = preset["name"]
        self.control_bar.set_preset_name(preset["name"])
        self._save_config_quietly()
        self.statusBar().showMessage(f"촬영 모드 '{preset['name']}' 를 저장했습니다", 4000)
        return True

    def apply_preset(self, name: str) -> bool:
        """촬영 모드 적용: 카메라 조정값 · 해상도 · Smart · 피킹 · 분할 칸 수."""
        preset = capture_presets.find(self.config, name)
        if preset is None:
            return False
        restart = capture_presets.apply_to_config(self.config, preset)
        cfg = self.config
        self.smart_bar.set_state(cfg.smart_mode, cfg.smart_peaking, cfg.smart_peaking_sensitivity, cfg.smart_best_shot)
        self.pro.set_smart_mode(cfg.smart_mode)
        self._apply_smart(save=False)
        if not self._quad:
            self.control_bar.split_count = int(cfg.split_count)
        self.control_bar.set_preset_name(preset["name"])
        self._save_config_quietly()
        if restart:
            self._restart_camera()                    # 해상도가 바뀌면 카메라를 다시 연다 (조정값은 열 때 적용)
        else:
            self._send_camera_props(cfg.camera_props)  # 촬영을 멈추지 않고 카메라 값만 바꾼다
        self.statusBar().showMessage(f"촬영 모드: {preset['name']}", 3000)
        return True

    def _send_camera_props(self, props: dict) -> None:
        if self.worker is None:
            return
        for key, value in capture_presets.device_requests(props or {}).items():
            self.worker.request_property(key, value)

    def _save_config_quietly(self) -> None:
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            log.warning("config save failed", exc_info=True)

    # ---- 전송 (폴더 · 클라우드 · PACS) ----
    def _on_send(self) -> None:
        from app.services import send_service as ss
        from app.ui.send_dialog import SendDialog
        p = self._current_patient
        if p is None:
            QMessageBox.information(self, "전송", "환자를 먼저 선택하세요.")
            return
        caps = self.thumbnail_panel.selected_captures()
        if not caps:
            QMessageBox.information(self, "전송", "오른쪽 사진 목록에서 보낼 사진을 한 번씩 눌러 고른 뒤 '전송'을 누르세요.")
            return
        dlg = SendDialog(self.config, len(caps), self)
        if not dlg.exec():
            return
        formats = dlg.selected_formats()
        note = self.db.exam_notes.get(p.id, datetime.now().strftime("%Y-%m-%d"))
        notes = "\n".join(x for x in ((note or {}).get("exam_type") and f"[{note['exam_type']}]",
                                         (note or {}).get("notes")) if x)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = ss.send(caps, p, self.config, formats=formats, notes=notes, pdf_writer=self._write_send_pdf)
        finally:
            QApplication.restoreOverrideCursor()
        self.events.log("send", method=self.config.send_method, count=len(caps), sent=len(res.sent))
        box = QMessageBox(QMessageBox.Icon.Information if res.ok else QMessageBox.Icon.Warning, "전송",
                          res.summary(), QMessageBox.StandardButton.Ok, self)
        folder = None
        if self.config.send_method in ("folder", "cloud") and res.sent:
            folder = os.path.dirname(res.sent[0])
            box.setInformativeText(folder)
            b_open = box.addButton("폴더 열기", QMessageBox.ButtonRole.ActionRole)
        else:
            b_open = None
        box.exec()
        if b_open is not None and box.clickedButton() is b_open and folder:
            from app.ui.image_viewer import open_in_default_player
            open_in_default_player(folder)

    def _write_send_pdf(self, path: str, patient, items: list, notes: str) -> None:
        from app.ui.pro.pro_workspace import caption_of
        printer = print_util.make_pdf_printer(path)
        b = app_brand.current()
        print_util.render_report(printer, patient, [(img, caption_of(cap)) for img, cap in items],
                                 clinic=getattr(self.config, "clinic_name", "") or "", title=b["report_title"],
                                 rule_color=b.get("report_rule", "#0d9488"), notes=notes)

    def _on_clear_quad(self) -> None:
        self.quad_view.reset()
        self.control_bar.set_quad_full(False)
        self.control_bar.set_status("4분할 초기화")

    def _on_capture(self) -> None:
        if self._current_patient is None:
            QMessageBox.information(self, "확인", "환자를 먼저 선택하세요.")
            return
        frame = self._freeze_frame if (self._single_frozen and self._freeze_frame is not None) else self._last_frame
        if frame is None:
            QMessageBox.information(self, "확인", "표시할 영상이 없습니다.")
            return
        note = ""
        if (self.config.smart_best_shot and not self._single_frozen and not self._paused
                and frame is self._last_frame):
            # Best Shot: 버튼을 누르는 사이 슬릿램프가 움직였을 수 있으므로 최근 0.6초 중 가장 선명한 원본 프레임
            best = self.smart.best_recent(0.6)
            if best is not None:
                frame, note = best.frame, f"Best Shot (초점 {best.quality.focus_pct:.0f})"
        eye = self._capture_eye()         # 찍은 순간의 눈을 여기서 고정 — 저장 중에 좌우가 바뀌어도 이 값
        sounds.play("shutter")            # 저장(인코딩) 전에 바로 — 누른 순간 '찰칵'
        try:
            cap = self.service.save_photo(frame, self._current_patient, eye=eye, note=note)
        except Exception as e:  # noqa: BLE001
            log.exception("photo save failed")
            QMessageBox.warning(self, "오류", f"저장 실패: {e}")
            return
        self.thumbnail_panel.add_capture(cap)
        self.control_bar.set_status("사진 저장됨" + (" · Best Shot" if note else ""))
        scene = self.agent.scene()
        self.events.log("capture", capture_id=cap.id, eye=eye, best_shot=bool(note),
                        scene_label=scene.label, scene=scene, quality=self.smart.last_quality())
        self._after_capture(cap, scene.label, eye, auto=False)
        if getattr(self.config, "view_after_capture", True) and not self._fullscreen:
            self._show_capture_viewer(cap)       # 찍은 사진을 큰 화면으로 — '다시 촬영' 으로 돌아온다

    # ---- AI 에이전트 ----
    def _agent_settings(self) -> AgentSettings:
        cfg = self.config
        return AgentSettings(auto_capture=cfg.agent_auto_capture,
                             protocol=cfg.agent_protocol if cfg.agent_protocol else "basic",
                             min_focus_pct=min(max(cfg.smart_ready_pct, 50.0), 99.0),
                             cooldown_s=max(cfg.smart_cooldown_s, 1.0))

    def _agent_model_path(self):
        p = self.config.agent_model_path.strip()
        if p:
            return p
        d = default_model_path()
        return str(d) if d else None

    def _apply_agent(self, save: bool = True) -> None:
        self.agent.update_settings(self._agent_settings())
        self.agent_bar.set_checklist(self.agent.core.checklist)
        self.events.set_enabled(self.config.agent_logging)
        if save:
            try:
                self.config.save()
            except Exception:  # noqa: BLE001
                log.warning("agent settings save failed", exc_info=True)

    def _on_agent_toggled(self, on: bool) -> None:
        self.config.agent_enabled = on
        if on:
            self.agent.start()
        else:
            self.agent.stop()
            self.agent_bar.set_scene(None)
        self.events.log("settings", agent_enabled=on)
        self._apply_agent()
        self.statusBar().showMessage("AI 에이전트 " + ("켜짐" if on else "꺼짐"), 3000)

    def _on_agent_auto_toggled(self, on: bool) -> None:
        self.config.agent_auto_capture = on
        self.events.log("settings", agent_auto_capture=on)
        self._apply_agent()

    def _on_agent_protocol(self, key: str) -> None:
        self.config.agent_protocol = key or "basic"
        self.events.log("settings", agent_protocol=self.config.agent_protocol)
        self._apply_agent()

    def _on_agent_logging(self, on: bool) -> None:
        self.config.agent_logging = on
        self._apply_agent()

    def _on_agent_reset_checklist(self) -> None:
        self.agent.reset_checklist()
        self.agent_bar.set_checklist(self.agent.core.checklist)
        self.events.log("checklist_reset")

    def _on_agent_scene(self, r) -> None:
        if self.config.agent_enabled:
            self.agent_bar.set_scene(r)

    def _on_agent_decisions(self, decisions) -> None:
        if not self.config.agent_enabled:
            return
        for d in decisions:
            if d.kind == "scene_changed":
                self.events.log("scene_changed", scene_label=d.scene.label if d.scene else "",
                                confidence=d.scene.confidence if d.scene else 0.0)
            elif d.kind == "hint":
                self.agent_bar.set_hint(d.reason)
            elif d.kind == "checklist_item":
                self.agent_bar.set_checklist(self.agent.core.checklist)
                self.events.log("checklist_item", item=d.payload.get("item", ""))
            elif d.kind == "checklist_done":
                self.agent_bar.set_checklist(self.agent.core.checklist)
                if not self._quad:
                    self.video_view.flash("프로토콜 완료 — 필요한 샷을 모두 찍었습니다")
                self.events.log("checklist_done")
            elif d.kind == "auto_capture":
                self._agent_auto_capture(d)

    def _agent_auto_capture(self, d) -> None:
        if (self.mode_stack.currentIndex() != 0 or self._quad or self._paused
                or self._single_frozen or self._recording):
            return
        if self._current_patient is None:
            self.control_bar.set_status("에이전트 자동 촬영: 환자를 먼저 선택하세요")
            return
        frame = self._last_frame
        best = self.smart.best_recent(0.6) if self.config.smart_best_shot else None
        if best is not None:
            frame = best.frame
        if frame is None:
            return
        scene = d.scene
        try:
            note = f"에이전트 자동 촬영 · {scene.label_ko} (초점 {d.score:.0f})"
            cap = self.service.save_photo(frame, self._current_patient, eye=d.eye, note=note)
        except Exception as e:  # noqa: BLE001
            log.exception("agent auto capture failed")
            self.statusBar().showMessage(f"자동 촬영 실패: {e}", 8000)
            return
        sounds.play("shutter")
        self.thumbnail_panel.add_capture(cap)
        self.video_view.flash(f"자동 촬영 · {scene.label_ko}")
        self.control_bar.set_status(f"에이전트 자동 촬영: {scene.label_ko} · {d.reason}")
        self.events.log("auto_capture", capture_id=cap.id, eye=d.eye, scene_label=scene.label,
                        scene=scene, quality=self.smart.last_quality(), reason=d.reason)
        self._after_capture(cap, scene.label, d.eye, auto=True)

    def _after_capture(self, cap, scene_label: str, eye: str, auto: bool) -> None:
        """촬영(수동·자동) 뒤: 체크리스트 갱신 + 전후 클립·추천 프레임 요청."""
        if not self.config.agent_enabled:
            return
        self._on_agent_decisions(self.agent.note_capture(scene_label, eye, auto=auto))
        cfg = self.config
        if not cfg.agent_save_clips and cfg.agent_candidates <= 0:
            return
        path, ts = None, None
        if cfg.agent_save_clips and self._current_patient is not None:
            try:
                path, ts = self.service.begin_video(self._current_patient, eye=eye)
            except Exception:  # noqa: BLE001
                log.warning("clip path failed", exc_info=True)
                path = None
        self.agent.request_clip(time.monotonic(), cfg.agent_clip_before_s, cfg.agent_clip_after_s,
                                path, k=max(0, min(6, cfg.agent_candidates)), tag="capture",
                                capture_id=cap.id, patient=self._current_patient, eye=eye, ts=ts,
                                scene_label=scene_label)

    def _on_agent_clip(self, res) -> None:
        """촬영 전후 클립 파일이 저장됨 → 환자 영상으로 등록."""
        req = res.request
        patient, eye = req.payload.get("patient"), req.payload.get("eye", "NA")
        if res.path is None or patient is None:
            return
        try:
            vcap = self.service.finish_video(res.path, patient, eye, req.payload.get("ts"),
                                             res.duration_ms, thumb_frame=res.thumb,
                                             note="촬영 전후 클립 (에이전트)")
        except Exception:  # noqa: BLE001
            log.warning("clip save failed", exc_info=True)
            return
        if self._current_patient is not None and patient.id == self._current_patient.id:
            self.thumbnail_panel.add_capture(vcap)
        self.events.log("clip_saved", capture_id=vcap.id, for_capture=req.payload.get("capture_id"),
                        frames=res.frames_written, duration_ms=res.duration_ms)

    def _on_agent_candidates(self, res) -> None:
        """촬영 전후 구간에서 고른 추천 프레임 → 도구줄에 표시 (의사가 클릭하면 저장)."""
        req = res.request
        patient, eye = req.payload.get("patient"), req.payload.get("eye", "NA")
        if not res.candidates or patient is None or self._current_patient is None \
                or patient.id != self._current_patient.id:
            return
        self._agent_candidates = (res, patient, eye)
        self.agent_bar.show_candidates(res.candidates)
        self.events.log("candidate_shown", for_capture=req.payload.get("capture_id"),
                        scores=[round(c.score, 1) for c in res.candidates])

    def _on_agent_candidate(self, idx: int) -> None:
        if self._agent_candidates is None:
            return
        res, patient, eye = self._agent_candidates
        if idx >= len(res.candidates):
            return
        c = res.candidates[idx]
        try:
            cap = self.service.save_photo(c.frame, patient, eye=eye,
                                          note=f"에이전트 추천 프레임 (초점 {c.score:.0f})")
        except Exception as e:  # noqa: BLE001
            log.exception("candidate save failed")
            QMessageBox.warning(self, "오류", f"저장 실패: {e}")
            return
        sounds.play("shutter")
        self.thumbnail_panel.add_capture(cap)
        self.agent_bar.mark_candidate_saved(idx)
        self.control_bar.set_status(f"추천 프레임 저장됨 (초점 {c.score:.0f})")
        payload = res.request.payload
        self.events.log("candidate_accepted", capture_id=cap.id,
                        for_capture=payload.get("capture_id"), rank=idx, score=c.score,
                        scene_label=c.scene or payload.get("scene_label", ""), eye=eye)

    def _on_agent_candidates_dismissed(self) -> None:
        if self._agent_candidates is not None:
            res = self._agent_candidates[0]
            self.events.log("candidate_rejected", for_capture=res.request.payload.get("capture_id"))
        self._agent_candidates = None
        self.agent_bar.clear_candidates()

    def _on_agent_export(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "학습 데이터를 내보낼 폴더",
                                             str(paths.get_appdata_dir()))
        if not d:
            return
        try:
            counts = agent_dataset.export_training_data(self.db, d)
        except Exception as e:  # noqa: BLE001
            log.exception("dataset export failed")
            QMessageBox.warning(self, "오류", f"내보내기 실패: {e}")
            return
        total = sum(counts.values())
        lines = "\n".join(f"  {k}: {v}장" for k, v in sorted(counts.items()))
        QMessageBox.information(self, "학습 데이터 내보내기",
                                f"{total}장을 내보냈습니다 (환자 정보 없음).\n{lines}\n\n"
                                f"학습: python tools/agent_train/train_scene.py --data \"{d}\"")
        self.events.log("dataset_export", total=total)

    def _on_agent_open_logs(self) -> None:
        d = self.events.events_dir()
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    # ---- Smart Capture ----
    def _smart_settings(self) -> SmartSettings:
        cfg = self.config
        return SmartSettings(mode=cfg.smart_mode if cfg.smart_mode in ("off", "freeze", "snapshot") else "off",
                             roi=normalize_roi(cfg.smart_roi),
                             ready_pct=min(max(cfg.smart_ready_pct, 50.0), 99.0),
                             cooldown_s=max(cfg.smart_cooldown_s, 0.5),
                             peaking_sensitivity=cfg.smart_peaking_sensitivity)

    def _apply_smart(self, save: bool = True) -> None:
        self.smart.update(settings=self._smart_settings(), peaking=self.config.smart_peaking,
                          peaking_color=self.config.smart_peaking_color)
        self._sync_smart_view()
        if save:
            try:
                self.config.save()
            except Exception:  # noqa: BLE001
                log.warning("smart capture settings save failed", exc_info=True)

    def _smart_wanted(self) -> bool:
        """분석이 필요한 때만 프레임을 넘긴다 (Pro 화면·일시정지 중에는 쉼)."""
        cfg = self.config
        if self.mode_stack.currentIndex() == 1:                 # Pro LIVE: 자동 정지·자동 저장
            return cfg.smart_mode != "off" and self.pro.is_live_running()
        if self._paused:
            return False
        return (cfg.smart_mode != "off" or cfg.smart_peaking or cfg.smart_best_shot
                or cfg.agent_enabled)

    def _smart_live(self) -> bool:
        """단일 화면에 라이브 영상이 보이는 중 (피킹·초점 표시를 그릴 수 있는 상태)."""
        return not self._quad and not self._paused and not self._single_frozen

    def _sync_smart_view(self) -> None:
        cfg = self.config
        live = self._smart_live()
        show = live and (cfg.smart_mode != "off" or cfg.smart_peaking)
        roi = normalize_roi(cfg.smart_roi) or DEFAULT_ROI
        self.video_view.set_roi(roi if show else None)
        if not (live and cfg.smart_peaking):
            self.video_view.set_overlay(None)
        if not show:
            self.video_view.set_focus_meter(None)
        if not self._smart_wanted():
            self.smart_bar.set_quality(None)

    def _on_smart_mode(self, mode: str) -> None:
        self.config.smart_mode = mode
        self.pro.set_smart_mode(mode)
        self._apply_smart()
        self.statusBar().showMessage(f"Smart 자동 촬영: {mode_text(mode)}", 3000)
        if mode == "snapshot" and self._current_patient is None:
            self.control_bar.set_status("자동 저장: 환자를 선택하면 저장됩니다")

    def _on_smart_peaking(self, on: bool) -> None:
        self.config.smart_peaking = on
        self._apply_smart()

    def _on_smart_sensitivity(self, v: int) -> None:
        self.config.smart_peaking_sensitivity = int(v)
        self._apply_smart(save=False)        # 슬라이더를 끄는 동안 매번 파일을 쓰지 않는다

    def _on_smart_best_toggle(self, on: bool) -> None:
        self.config.smart_best_shot = on
        self._apply_smart()

    def _on_smart_roi(self, roi) -> None:
        roi = normalize_roi(roi)
        self.config.smart_roi = list(roi) if roi else []
        self.smart_bar.set_roi_selecting(False)
        self.video_view.set_roi_selecting(False)
        self._apply_smart()
        self.statusBar().showMessage("초점 영역(ROI)을 지정했습니다" if roi else "초점 영역을 화면 중앙으로 되돌렸습니다", 3000)

    def _on_smart_analyzed(self, a) -> None:
        if not self._smart_wanted() or self.mode_stack.currentIndex() != 0:
            return
        q = a.quality
        self.smart_bar.set_quality(q)
        if self.config.agent_enabled and self._last_frame is not None:
            self.agent.live = self._smart_live() and not self._recording
            self.agent.submit(self._last_frame, time.monotonic(), q, self.control_bar.current_eye())
        if not self._smart_live():
            return
        cfg = self.config
        if cfg.smart_peaking:
            self.video_view.set_overlay(a.peaking)
        if cfg.smart_mode != "off" or cfg.smart_peaking:
            self.video_view.set_focus_meter(q.focus_pct if q.usable else 0.0, q.in_focus)

    def _pro_best_shot(self, shot, mode: str) -> None:
        """Pro LIVE 의 자동 정지(정지 후 SAVE 로 저장) / 자동 저장."""
        pro = self.pro
        if not pro.is_live_running():
            return
        pct = shot.quality.focus_pct
        if mode == "freeze":
            pro.freeze_with(shot.frame)
            self.statusBar().showMessage(f"Pro LIVE 자동 정지 (초점 {pct:.0f}) · SAVE 로 저장", 5000)
            return
        if self._current_patient is None:
            self.statusBar().showMessage("자동 저장: 환자를 먼저 선택하세요", 4000)
            return
        try:
            self.service.save_photo(shot.frame, self._current_patient, eye=pro.current_eye(),
                                    note=f"Smart 자동 저장 (초점 {pct:.0f})")
        except Exception as e:  # noqa: BLE001
            log.exception("pro smart snapshot failed")
            self.statusBar().showMessage(f"자동 저장 실패: {e}", 8000)
            return
        pro.reload(select_last=False)
        self._on_pro_saved()
        self.statusBar().showMessage(f"Pro LIVE 자동 저장 (초점 {pct:.0f})", 4000)

    def _on_smart_best_shot(self, shot) -> None:
        """자동 촬영 순간 — 버퍼에서 고른 가장 선명한 원본 프레임."""
        mode = self.config.smart_mode
        if mode == "off":
            return
        pct = shot.quality.focus_pct
        if self.mode_stack.currentIndex() == 1:
            self._pro_best_shot(shot, mode)
            return
        if self._paused:
            return
        eye = self.control_bar.current_eye()
        if mode == "snapshot":
            if self._current_patient is None:
                self.control_bar.set_status("자동 저장: 환자를 먼저 선택하세요")
                return
            try:
                cap = self.service.save_photo(shot.frame, self._current_patient, eye=eye,
                                              note=f"Smart 자동 저장 (초점 {pct:.0f})")
            except Exception as e:  # noqa: BLE001
                log.exception("smart snapshot save failed")
                self.statusBar().showMessage(f"자동 저장 실패: {e}", 8000)
                return
            self.thumbnail_panel.add_capture(cap)
            if not self._quad:
                self.video_view.flash("자동 저장")
            self.control_bar.set_status(f"자동 저장됨 (초점 {pct:.0f})")
            return
        # freeze: 의사가 확인한 뒤 '사진 캡처'로 저장하거나 라이브로 돌아간다
        if self._recording or self._single_frozen:
            return
        if self._quad:
            self.quad_view.set_live_frame(shot.frame)
            if self.quad_view.freeze_active(eye):
                self.control_bar.set_quad_full(self.quad_view.is_full())
                self.control_bar.set_status(f"자동 정지 → 4분할 칸 채움 (초점 {pct:.0f})")
            return
        self._single_frozen = True
        self._freeze_frame = shot.frame
        self.video_view.set_frame(shot.frame)
        self.video_view.flash("자동 정지 — 초점 최적")
        self.control_bar.set_freeze_state(True)
        self.control_bar.set_status(f"자동 정지 (초점 {pct:.0f}) · 사진 캡처로 저장 / ▶라이브로 복귀")
        self._refresh_hud_state()
        self._sync_smart_view()

    def _on_record_toggle(self, checked: bool) -> None:
        if checked:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        if self._current_patient is None:
            QMessageBox.information(self, "확인", "환자를 먼저 선택하세요.")
            self.control_bar.reset_record_button()
            return
        if self._pending_video is not None and not self.recorder.is_finalizing:
            self._finalize_video()                     # 앞 녹화를 먼저 기록 (새 녹화의 값과 섞이지 않게)
        eye = self.control_bar.current_eye()
        path, ts = self.service.begin_video(self._current_patient, eye=eye)
        size = self._frame_size
        if self._last_frame is not None:
            size = (self._last_frame.shape[1], self._last_frame.shape[0])
        try:
            self.recorder.start(path, self._estimate_fps(), size, quality=self.config.video_quality)
        except Exception as e:  # noqa: BLE001
            log.exception("recorder start failed")
            QMessageBox.warning(self, "오류", f"녹화 시작 실패: {e}")
            self.control_bar.reset_record_button()
            return
        self._recording = True
        sounds.play("rec_start")
        self.events.log("record_start", eye=eye, scene_label=self.agent.scene().label)
        self._video_path = path
        self._video_ts = ts
        self._video_eye = eye                          # 영상 전체는 시작한 눈으로 저장 (녹화 중 전환은 코멘트에 기록)
        self._video_switches = []
        self._video_patient = self._current_patient   # 시작한 환자에게 저장 (환자를 바꾸면 녹화를 끝냄)
        self._first_video_frame = None
        self._rec_started = time.monotonic()
        self._rec_timer.start()
        self._update_rec_hud()
        q = "고화질" if self.config.video_quality == "high" else "일반 화질"
        self.control_bar.set_status(f"녹화 중... ({q})")

    def _stop_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        sounds.play("rec_stop")
        self.events.log("record_stop", seconds=round(time.monotonic() - self._rec_started, 1))
        self._rec_timer.stop()
        self._refresh_hud_state()
        _, count, duration = self.recorder.stop()
        self._pending_video = (self._video_path, self._video_patient, self._video_eye,
                               self._video_ts, duration, self._first_video_frame, self._video_note())
        if self.recorder.is_finalizing:
            # 인코더가 아직 파일을 닫는 중: 지금 복사하면 잘린 mp4 가 export 에 남는다 → 닫힌 뒤 기록
            self._finalize_waited = 0.0
            self.control_bar.btn_record.setEnabled(False)
            self.control_bar.set_status("동영상 파일을 마무리하는 중…")
            self._finalize_timer.start()
            return
        self._finalize_video()

    def _poll_video_finalize(self) -> None:
        self._finalize_waited += self._finalize_timer.interval() / 1000
        if self.recorder.is_finalizing and self._finalize_waited < 120:
            return
        self._finalize_timer.stop()
        if self.recorder.is_finalizing:
            log.error("video encoder did not finish within 120s: %s",
                      self._pending_video and self._pending_video[0])
        self._finalize_video()

    def _finalize_video(self) -> None:
        pending, self._pending_video = self._pending_video, None
        self.control_bar.btn_record.setEnabled(self._current_patient is not None)
        if pending is None:
            return
        path, patient, eye, ts, duration, thumb, note = pending
        count = self.recorder.frame_count
        if not self.recorder.is_finalizing:
            _, count, duration = self.recorder.result()      # 파일을 다 닫은 뒤의 확정값
        if count == 0:
            # 프레임이 하나도 기록되지 않은 녹화(인코더 오류·카메라 끊김)는 DB 에 남기지 않는다
            # (프레임 수는 파일을 다 닫은 뒤에 판단 — 아직 쓰는 중인 파일을 지우지 않게)
            try:
                os.remove(path)
            except OSError:
                pass
            QMessageBox.warning(self, "녹화 실패", "녹화된 영상이 없습니다 (카메라 또는 인코더 오류).")
            self.control_bar.set_status("녹화 실패")
            self.control_bar.reset_record_button()
            return
        try:
            cap = self.service.finish_video(path, patient, eye, ts, duration, thumb_frame=thumb, note=note)
            self.thumbnail_panel.add_capture(cap)
            self.control_bar.set_status(f"동영상 저장됨 ({duration // 1000}초)")
        except Exception as e:  # noqa: BLE001
            log.exception("finish_video failed")
            QMessageBox.warning(self, "오류", f"동영상 저장 실패: {e}\n파일: {path}")
        self.control_bar.reset_record_button()

    def _on_recorder_error(self, msg: str) -> None:
        log.error("recorder error: %s", msg)
        if self._recording:                  # 녹화가 이어지는 것처럼 보이지 않게 멈추고 쓴 부분까지만 저장
            self._stop_recording()
        self.statusBar().showMessage(f"녹화 오류로 녹화를 멈췄습니다: {msg}", 10000)

    def _on_thumbnail_clicked(self, capture) -> None:
        if ImageViewerDialog(capture, self).exec() == ImageViewerDialog.Deleted:
            self._delete_capture(capture)

    def _delete_capture(self, capture) -> bool:
        """썸네일 보기에서 확인한 영상 한 건을 삭제하고 양쪽 화면 목록을 갱신한다."""
        try:
            ok = self.service.delete_capture(capture)
        except Exception as e:  # noqa: BLE001
            log.exception("capture delete failed")
            QMessageBox.warning(self, "오류", f"영상 삭제 실패: {e}")
            return False
        p = self._current_patient
        self.thumbnail_panel.set_patient(p.id if p else None)
        self.pro.reload(select_last=False)
        if hasattr(self, "exam_screen"):
            self.exam_screen.reload()
        self.control_bar.set_status("영상 삭제됨" if ok else "이미 삭제된 영상입니다")
        return ok

    def _delete_day(self, day: str, confirm: bool = True) -> int:
        """현재 환자가 그 날('YYYY-MM-DD') 촬영한 사진 · 동영상을 모두 삭제 (검사 소견은 남김)."""
        p = self._current_patient
        if p is None or not day:
            return 0
        caps = self.service.captures_on(p.id, day)
        if not caps:
            if self.isVisible():
                QMessageBox.information(self, "촬영일 삭제", f"{day_label(day)} 에 촬영한 영상이 없습니다.")
            return 0
        n_photo = sum(1 for c in caps if c.media_type != "video")
        n_video = len(caps) - n_photo
        what = " · ".join(x for x in (f"사진 {n_photo}장" if n_photo else "",
                                      f"동영상 {n_video}개" if n_video else "") if x)
        if confirm:
            ret = QMessageBox.question(
                self, "촬영일 삭제",
                f"{p.name} 환자의 {day_label(day)} 촬영 영상을 모두 삭제하시겠습니까?\n\n"
                f"{what} — DB 기록과 저장된 파일(썸네일 포함)이 지워지며 되돌릴 수 없습니다.\n"
                "(지정 폴더에 복사된 사본과 그 날의 검사 소견은 남겨 둡니다)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return 0
        try:
            n = self.service.delete_captures(caps)
        except Exception as e:  # noqa: BLE001
            log.exception("day delete failed")
            QMessageBox.warning(self, "오류", f"촬영일 삭제 실패: {e}")
            n = 0
        self.thumbnail_panel.set_patient(p.id)
        self.pro.reload(select_last=False)
        if hasattr(self, "exam_screen"):
            self.exam_screen.reload()
        self.control_bar.set_status(f"{day_label(day)} 촬영 영상 {n}장 삭제됨")
        return n

    def _toggle_theme(self) -> None:
        self.config.theme = "dark" if self.config.theme != "dark" else "light"
        self._apply_theme()

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(self.config.theme))
        self._sync_theme_button()
        self._render_brand_logo()
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            log.warning("theme save failed", exc_info=True)

    def _open_settings(self, tab: str | None = None) -> None:
        old_theme = self.config.theme
        old_props = dict(self.config.camera_props or {})
        old_active = capture_presets.find(self.config, self.config.active_preset or "")
        dlg = SettingsDialog(self.config, self, actions={
            "camera": lambda: self._restart_camera(),
            "shortcuts": lambda parent: self._open_shortcuts(parent),
            "samples": lambda parent: self._import_samples(parent),
            "camera_props": lambda props: self._send_camera_props(props),
        })
        if tab:
            dlg.show_tab(tab)
        accepted = dlg.exec()
        if not accepted:
            if getattr(dlg, "previewed", False):
                self._send_camera_props(old_props)    # 미리보기로 바꾼 카메라 값 되돌리기
            return
        if accepted:
            dlg.apply_to_config()
            new_active = capture_presets.find(self.config, self.config.active_preset or "")
            if new_active is not None and new_active != old_active:
                capture_presets.apply_to_config(self.config, new_active)    # 지금 모드를 고쳤으면 바로 반영
                cfg = self.config
                self.smart_bar.set_state(cfg.smart_mode, cfg.smart_peaking, cfg.smart_peaking_sensitivity,
                                         cfg.smart_best_shot)
                self._apply_smart(save=False)
            self.control_bar.set_preset_name(self.config.active_preset or capture_presets.DEFAULT_NAME)
            if new_active is not None and (old_active is None or new_active["name"] != old_active["name"]):
                self.statusBar().showMessage(f"촬영 모드: {new_active['name']}", 4000)
            if self.config.theme != old_theme:
                self._apply_theme()
            self._set_smart_bar_visible(self.config.smart_bar_visible, save=False)
            self.thumbnail_panel.set_split_eyes(bool(self.config.thumbs_split_eyes))
            if not self._quad:
                self.control_bar.split_count = int(self.config.split_count or 4)
            self.control_bar.count_saved = bool(getattr(self.config, "split_count_saved", False))
            self.control_bar.sync_quad_tip()
            self.home.chk_skip.blockSignals(True)
            self.home.chk_skip.setChecked(self.config.start_screen == "capture")
            self.home.chk_skip.blockSignals(False)
            sounds.set_enabled(self.config.sounds_enabled)
            self.thumbnail_panel.newest_on_top = self.config.newest_on_top
            cfg = self.config
            self.agent_bar.set_state(cfg.agent_enabled, cfg.agent_auto_capture, cfg.agent_protocol,
                                     cfg.agent_logging)
            self.agent_bar.setVisible(cfg.agent_enabled or app_edition.is_agent())
            if cfg.agent_enabled:
                self.agent.start()
            else:
                self.agent.stop()
            self._apply_agent(save=False)
            self.usb_filter.set_buttons(usb_buttons.load(self.config))
            self.usb_raw.watch(usb_buttons.key_of(b["key"]) for b in usb_buttons.load(self.config).values())
            self._refresh_eye_status()
            self._install_shortcuts()                  # USB 버튼 키와 겹치는 단축키는 쉬게
            self.events.log("settings", source="dialog")
            self._restart_camera()

    # ---- 참조 영상 (질환별 대표 영상 · 샘플 사진) ----
    def _reference_capture(self):
        """비교·등록할 지금 환자 사진: Pro 1화면 영상 → 멀티에서 누른 칸 → 사진 목록에서 고른 사진(마지막) → 가장 최근 사진."""
        if self.mode_stack.currentIndex() == MODES.index("pro") and self.pro.mode() == "single":
            caps, i = self.pro.captures(), self.pro.current_index()
            if 0 <= i < len(caps):
                return caps[i]
        if self._multi and self.multi_pro.current_capture() is not None:
            return self.multi_pro.current_capture()
        photos = [c for c in self.thumbnail_panel.selected_captures() if c.media_type == "photo"]
        if photos:
            return photos[-1]
        p = self._current_patient
        if p is None:
            return None
        return next((c for c in self.db.captures.list_by_patient(p.id) if c.media_type == "photo"), None)

    def open_references(self):
        """참조 영상 라이브러리 — 등록한 참조 영상 + 가져온 샘플 사진(질환별). 지금 환자 사진과 비교·등록."""
        from app.ui.explorer.reference_library import ReferenceLibraryDialog
        from app.ui.pro.pro_workspace import caption_of, load_bgr
        cap = self._reference_capture()
        current = None
        if cap is not None:
            img = load_bgr(cap.file_path)
            if img is not None:
                who = self._current_patient.name if self._current_patient else ""
                current = (img, f"{who} · {caption_of(cap)}")
        dlg = ReferenceLibraryDialog(self.db, self, current=current,
                                     on_import_samples=lambda parent: self._import_samples(parent),
                                     on_register=lambda parent: self._register_reference(cap, parent),
                                     on_save_screen=lambda img, note: self._save_reference_screen(img, note, cap))
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dlg.finished.connect(lambda *_: (self.ref_bar.refresh(), self._refresh_dx_bar()))   # 등록 · 삭제 · 샘플 → 참조 줄 · 질환 아이콘
        dlg.show()
        self._refs_dialog = dlg
        return dlg

    def _save_reference_screen(self, img, note: str, cap=None) -> str | None:
        """참조 비교 화면(환자 영상 ↔ 참조 영상)을 한 장의 사진으로 지금 환자 기록에 저장."""
        p = self._current_patient
        if p is None:
            QMessageBox.information(self, "확인", "환자를 먼저 선택하세요.")
            return None
        eye = cap.eye if cap is not None and cap.eye in ("OD", "OS", "OU") else "NA"
        try:
            saved = self.service.save_photo(img, p, eye=eye, note=note)
        except Exception as e:  # noqa: BLE001
            log.exception("reference screen save failed")
            QMessageBox.warning(self, "화면 저장", f"저장하지 못했습니다.\n{e}")
            return None
        sounds.play("shutter")
        self.thumbnail_panel.add_capture(saved)
        self.statusBar().showMessage(f"비교 화면을 {p.name} 님 사진으로 저장했습니다 ({note})", 4000)
        return f"{p.name} 님 사진으로 저장했습니다 ({saved.captured_at})"

    def _register_reference(self, cap, parent=None, values=None) -> bool:
        """지금 환자 사진을 참조 영상으로 등록 (분류·제목·설명, 라이브러리 폴더에 사본)."""
        from app.ui.explorer.dialogs import ReferenceDialog
        if cap is None:
            return False
        if values is None:
            p = self._current_patient
            dlg = ReferenceDialog(parent or self, self.db.references.categories(),
                                  title=(p.diagnosis or "") if p else "")
            if not dlg.exec():
                return False
            values = dlg.values()
        try:
            ref = self.service.add_reference(cap, *values)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(parent or self, "참조 영상", f"등록하지 못했습니다.\n{e}")
            return False
        self.ref_bar.refresh()
        self._refresh_dx_bar()
        self.statusBar().showMessage(f"참조 영상으로 등록했습니다 ({ref.category})", 4000)
        return True

    def _import_samples(self, parent=None, folder=None) -> bool:
        """받아 둔 샘플 사진 폴더(질환별 하위 폴더 + metadata.json)를 질환별 샘플 환자로 등록.
        등록한 사진은 참조 영상의 '샘플 사진' 에 질환별로도 보인다."""
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
        self.patient_panel._reload()
        self.ref_bar.refresh()
        self._refresh_dx_bar()
        QMessageBox.information(
            parent, "샘플 사진",
            result.summary() + "\n\n환자 목록에서 '샘플 · 질환명' 환자를 고르거나, 아래 촬영 도크의 '참조' 줄에서 "
            "질환별로 골라 멀티 화면에서 지금 환자 사진과 같이 볼 수 있습니다.")
        return True

    # ---- 종료 ----
    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        QApplication.instance().removeEventFilter(self.usb_filter)
        self.usb_raw.uninstall(QApplication.instance())
        self._reconnect_timer.stop()
        try:
            if self._recording:
                self._stop_recording()
            if self._pending_video is not None:          # 닫는 중인 녹화 파일은 끝까지 기다려 기록
                self._finalize_timer.stop()
                if not self.recorder.wait_finalized(60):
                    log.error("video encoder still running at close: %s", self._pending_video[0])
                self._finalize_video()
        except Exception:  # noqa: BLE001
            log.warning("stop recording on close failed", exc_info=True)
        self._stop_camera()
        self.smart.stop()
        self.agent.stop()
        self.events.log("session_end")
        try:
            self.db.close()
        except Exception:  # noqa: BLE001
            log.warning("db close failed", exc_info=True)
        event.accept()

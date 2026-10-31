"""설정 다이얼로그.

위쪽 '바로 실행' 줄: 카메라 다시 연결 · 단축키 설정 · 샘플 사진 가져오기 (main_window 가 actions 로 넘겨 줌).
그 아래: 화면 테마 · 시작 화면 · Smart 도구줄 · 소스 모드 · 카메라 · 해상도 · 사진 형식 · 저장 폴더 · 효과음 · AI 에이전트.
'USB 버튼' 탭: 슬릿램프 USB 버튼의 키 조합 · 기능 (app/ui/usb_buttons_tab.py).
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import system_specs
from app import sounds
from app.capture.frame_utils import device_names, enumerate_devices, is_sony
from app.ui import icons
from app.ui.theme import ICON_ACCENT
from app.ui.backup_tab import BackupTab
from app.ui.usb_buttons_tab import UsbButtonsTab


_THEMES = [("dark", "어두운 화면"), ("light", "밝은 화면")]


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None, actions: dict | None = None):
        """actions: {"camera": 함수, "shortcuts": 함수(parent), "samples": 함수(parent)} — 없으면 그 버튼은 꺼짐."""
        super().__init__(parent)
        self.config = config
        self._actions = actions or {}
        self.setWindowTitle("설정")
        self.resize(640, 560)

        # ---- 바로 실행 ----
        quick = QHBoxLayout()
        quick.setSpacing(8)
        self.btn_camera = self._quick("카메라 다시 연결", "videocam", "캡처 장비(카메라)를 끊었다가 다시 연결합니다", "camera")
        self.btn_shortcuts = self._quick("단축키 설정", "keyboard", "사진 캡처·녹화·안구 선택 등의 키를 바꿉니다", "shortcuts")
        self.btn_samples = self._quick("샘플 사진 가져오기", "image",
                                       "받아 둔 질환별 샘플 사진 폴더를 골라 샘플 환자로 등록합니다", "samples")
        for b in (self.btn_camera, self.btn_shortcuts, self.btn_samples):
            quick.addWidget(b)
        quick.addStretch(1)
        self.quick_status = QLabel("")
        self.quick_status.setObjectName("Muted")

        # ---- 화면 ----
        self.theme = QComboBox()
        for code, text in _THEMES:
            self.theme.addItem(text, code)
        self.theme.setCurrentIndex(1 if config.theme == "light" else 0)
        self.start_home = QCheckBox("프로그램을 켜면 홈(아이콘 메뉴) 화면에서 시작")
        self.start_home.setChecked(getattr(config, "start_screen", "home") != "capture")
        self.smart_visible = QCheckBox("촬영 화면에 Smart 도구줄 보이기")
        self.smart_visible.setChecked(bool(getattr(config, "smart_bar_visible", True)))
        self.right_click = QComboBox()
        self.right_click.addItem("메뉴 — 전송 · 삭제 · 멀티 중에서 고르기", "menu")
        self.right_click.addItem("멀티 화면 바로", "multi")
        self.right_click.setCurrentIndex(max(0, self.right_click.findData(getattr(config, "right_click_action", "menu"))))
        self.right_click.setToolTip("오른쪽 사진 목록에서 사진을 마우스 오른쪽으로 눌렀을 때")
        self.menu_items = {}
        items_row = QHBoxLayout()
        items_row.setContentsMargins(0, 0, 0, 0)
        want = list(getattr(config, "right_click_items", None) or ["send", "multi", "delete"])
        for key, text in (("send", "전송"), ("send_delete", "전송(삭제)"), ("multi", "멀티"), ("delete", "삭제")):
            cb = QCheckBox(text)
            cb.setChecked(key in want)
            if key == "send_delete":
                cb.setToolTip("보낸 뒤 이 PC 에서 삭제 — 모두 보냈을 때만, 확인 후")
            self.menu_items[key] = cb
            items_row.addWidget(cb)
        items_row.addStretch(1)
        self.menu_items_w = QWidget()
        self.menu_items_w.setLayout(items_row)
        self.right_click.currentIndexChanged.connect(
            lambda _i: self.menu_items_w.setEnabled(self.right_click.currentData() == "menu"))
        self.menu_items_w.setEnabled(self.right_click.currentData() == "menu")
        self.live_multi = QCheckBox("사진 목록에서 사진을 왼쪽 클릭으로 고르면 바로 가운데 멀티 화면에 놓기 (다 빼면 라이브 화면으로)")
        self.live_multi.setChecked(bool(getattr(config, "live_multi", False)))
        self.view_after = QCheckBox("사진을 찍으면 큰 화면으로 바로 보기 ('다시 촬영' · 촬영 키 · USB 버튼으로 돌아오기)")
        self.view_after.setChecked(bool(getattr(config, "view_after_capture", True)))
        self.split_count = QComboBox()
        for n in (2, 4, 6, 8, 16):
            self.split_count.addItem(f"{n}분할", n)
        i = self.split_count.findData(int(getattr(config, "split_count", 4) or 4))
        self.split_count.setCurrentIndex(i if i >= 0 else 1)
        self.split_ask = QCheckBox("분할 버튼을 누를 때마다 칸 수 묻기 (끄면 위 칸 수로 바로 시작)")
        self.split_ask.setChecked(not bool(getattr(config, "split_count_saved", False)))
        # 여기서 칸 수를 바꾸면 그 칸 수를 저장한 것으로 — 분할 버튼은 묻지 않고 바로 시작
        self.split_count.currentIndexChanged.connect(lambda _i: self.split_ask.setChecked(False))
        self.split_eyes = QCheckBox("오른쪽 사진 목록을 OD(우안) · OS(좌안)로 나눠 보기 (끄면 촬영일별)")
        self.split_eyes.setChecked(bool(getattr(config, "thumbs_split_eyes", False)))

        form = QFormLayout()
        self.source_mode = QComboBox()
        self.source_mode.addItems(["device", "file", "synthetic"])
        self.source_mode.setCurrentText(config.source_mode)

        self.camera_index = QComboBox()
        self.camera_index.setMinimumWidth(260)
        # 카메라를 직접 고르면 실제 장비 모드로 (합성/파일 모드에 남아 고른 카메라가 안 나오는 일 방지)
        self.camera_index.activated.connect(
            lambda _i: self.source_mode.setCurrentText("device"))
        self._names: list[str] = []
        btn_rescan = QPushButton("다시 찾기")
        btn_rescan.setToolTip("카메라를 연결하거나 USB 스트리밍으로 바꾼 뒤 눌러 목록을 새로 고칩니다")
        btn_rescan.clicked.connect(self._fill_cameras)
        cam_row = QHBoxLayout()
        cam_row.setContentsMargins(0, 0, 0, 0)
        cam_row.addWidget(self.camera_index, 1)
        cam_row.addWidget(btn_rescan)
        self.camera_hint = QLabel()
        self.camera_hint.setWordWrap(True)
        self.camera_hint.setStyleSheet("color: #f5a524;")
        # 줄바꿈 라벨은 폼 안에서 높이를 덜 잡아 잘리므로 3줄 높이를 미리 확보
        self.camera_hint.setMinimumHeight(self.camera_hint.fontMetrics().lineSpacing() * 3 + 4)
        self.camera_hint.setVisible(False)
        cam_col = QVBoxLayout()
        cam_col.setContentsMargins(0, 0, 0, 0)
        cam_col.addLayout(cam_row)
        cam_col.addWidget(self.camera_hint)
        cam_w = QWidget()
        cam_w.setLayout(cam_col)
        self._fill_cameras()
        # synthetic·file 에서 device 로 바꾸면 그때 카메라를 찾는다
        self.source_mode.currentTextChanged.connect(
            lambda m: self._fill_cameras() if m == "device" and not self._names else None)

        self.width = QSpinBox()
        self.width.setRange(320, 7680)
        self.width.setValue(config.capture_width)
        self.height = QSpinBox()
        self.height.setRange(240, 4320)
        self.height.setValue(config.capture_height)

        self.photo_format = QComboBox()
        self.photo_format.addItems(["png", "jpg"])
        self.photo_format.setCurrentText(config.photo_format)

        self.video_quality = QComboBox()
        self.video_quality.addItem("일반 — 가볍고 파일 작음 (기본)", "normal")
        self.video_quality.addItem("고화질 — 더 선명, 파일 약 2배", "high")
        self.video_quality.setCurrentIndex(max(0, self.video_quality.findData(config.video_quality)))
        self.video_quality.setToolTip("두 모드 모두 실시간(30fps)으로 녹화됩니다. 4K 는 보통 진료실 PC 에서도 끊기지 않게\n"
                                      "가장 빠른 인코더 설정을 쓰고, 화질(압축 정도)만 다릅니다.")

        self.export = QLineEdit(config.export_root)
        btn_browse = QPushButton("폴더 선택")
        btn_browse.clicked.connect(self._browse)
        exp_row = QHBoxLayout()
        exp_row.addWidget(self.export)
        exp_row.addWidget(btn_browse)
        exp_w = QWidget()
        exp_w.setLayout(exp_row)

        self.test_file = QLineEdit(config.test_file_path)

        self.sounds = QCheckBox("사진 캡처 찰칵 · 정지영상 · 녹화 시작/중지 소리")
        self.sounds.setChecked(bool(config.sounds_enabled))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setSingleStep(5)
        self.volume.setPageStep(10)
        self.volume.setValue(int(max(0, min(100, config.sound_volume))))
        self.volume.setToolTip("효과음 크기 (0 = 무음)")
        self.volume_label = QLabel(f"{self.volume.value()}%")
        self.volume_label.setMinimumWidth(40)
        self.btn_preview = QPushButton("들어보기")
        self.btn_preview.setToolTip("지금 슬라이더 크기로 '찰칵' 소리를 재생")
        vol_row = QHBoxLayout()
        vol_row.setContentsMargins(0, 0, 0, 0)
        vol_row.addWidget(self.volume, 1)
        vol_row.addWidget(self.volume_label)
        vol_row.addWidget(self.btn_preview)
        vol_w = QWidget()
        vol_w.setLayout(vol_row)
        self.volume.valueChanged.connect(lambda v: self.volume_label.setText(f"{v}%"))
        self.btn_preview.clicked.connect(self._preview_sound)
        self.sounds.toggled.connect(vol_w.setEnabled)
        vol_w.setEnabled(self.sounds.isChecked())
        self.agent = QCheckBox("AI 에이전트 (장면 인식 · 자동 촬영 · 전후 클립 · 추천 프레임)")
        self.agent.setChecked(bool(config.agent_enabled))
        self.agent_clips = QCheckBox("촬영 전후 클립(mp4) 도 환자 영상으로 저장")
        self.agent_clips.setChecked(bool(config.agent_save_clips))
        self.agent_logging = QCheckBox("사용 기록 수집 (익명 세션 · 환자 정보 없음 · 학습용)")
        self.agent_logging.setChecked(bool(config.agent_logging))

        # ---- 탭 1: 일반 ----
        general = QWidget()
        gl = QVBoxLayout(general)
        head = QLabel("바로 실행")
        head.setObjectName("ThumbTitle")
        gl.addWidget(head)
        gl.addLayout(quick)
        gl.addWidget(self.quick_status)
        gl.addWidget(self._rule())
        # 촬영 모드 (예전 촬영 도크의 '모드' 버튼): 고르면 확인 때 바로 바뀜 · 만들기 · 고치기는 '촬영 모드' 탭
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(200)
        self.preset_combo.setToolTip("자주 쓰는 카메라 · 촬영 설정 묶음 — 고르고 '확인' 을 누르면 바로 바뀝니다")
        self.preset_combo.currentIndexChanged.connect(self._on_preset_combo)
        self.btn_preset_edit = QPushButton("모드 만들기 · 편집…")
        self.btn_preset_edit.clicked.connect(lambda: self.show_tab("촬영 모드"))
        preset_row = QWidget()
        prl = QHBoxLayout(preset_row)
        prl.setContentsMargins(0, 0, 0, 0)
        prl.addWidget(self.preset_combo, 1)
        prl.addWidget(self.btn_preset_edit)

        gform = QFormLayout()
        gform.addRow("촬영 모드", preset_row)
        gform.addRow("화면 테마", self.theme)
        gform.addRow("시작 화면", self.start_home)
        gform.addRow("Smart 도구줄", self.smart_visible)
        gform.addRow("촬영 후 보기", self.view_after)
        gform.addRow("사진 목록", self.split_eyes)
        gform.addRow("사진 오른쪽 클릭", self.right_click)
        gform.addRow("메뉴 항목", self.menu_items_w)
        gform.addRow("실시간 멀티화면", self.live_multi)
        gform.addRow("분할 화면", self.split_count)
        gform.addRow("", self.split_ask)
        gform.addRow("효과음", self.sounds)
        gform.addRow("효과음 크기", vol_w)
        gform.addRow("AI 에이전트", self.agent)
        gform.addRow("", self.agent_clips)
        gform.addRow("", self.agent_logging)
        gl.addLayout(gform)
        gl.addStretch(1)

        # ---- 탭 2: 카메라 · 저장 ----
        camera = QWidget()
        cl = QVBoxLayout(camera)
        form.addRow("소스 모드", self.source_mode)
        form.addRow("카메라", cam_w)
        form.addRow("가로 해상도", self.width)
        form.addRow("세로 해상도", self.height)
        form.addRow("사진 형식", self.photo_format)
        form.addRow("동영상 화질", self.video_quality)
        form.addRow("저장 폴더(export)", exp_w)
        form.addRow("테스트 파일(file 모드)", self.test_file)
        cl.addLayout(form)
        note = QLabel("※ 확인 시 설정이 저장되고 카메라가 재시작됩니다.\n"
                      "※ 소니 ZV-E10 II · FX3: 카메라 MENU → 설정 → USB → USB 연결 모드 → 'USB 스트리밍' 후\n"
                      "   USB-C 로 연결하면 ★ 표시로 나타납니다 (4K 는 해상도 3840×2160).")
        note.setObjectName("Muted")
        cl.addWidget(note)
        cl.addStretch(1)

        # ---- 탭 3: 전송 ----
        send = self._build_send_tab()

        self.tabs = QTabWidget()
        self.tabs.addTab(general, "일반")
        self.tabs.addTab(camera, "카메라 · 저장")
        self.tabs.addTab(send, "전송")
        self.tabs.addTab(self._build_preset_tab(), "촬영 모드")
        self.usb_tab = UsbButtonsTab(config, raw_input=getattr(parent, "usb_raw", None))
        self.tabs.addTab(self.usb_tab, "USB 버튼")
        self.backup_tab = BackupTab(config, manager=getattr(parent, "backup", None))
        self.tabs.addTab(self.backup_tab, "백업")
        self.tabs.addTab(self._build_specs_tab(), "컴퓨터 사양")     # 안내 탭은 맨 끝

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(btns)
        self.adjustSize()

    def accept(self) -> None:
        bad = self.usb_tab.problem()
        if bad:
            self.show_tab("USB 버튼")
            QMessageBox.warning(self, "USB 버튼", bad)
            return
        bad = self.backup_tab.problem()
        if bad:
            self.show_tab("백업")
            QMessageBox.warning(self, "백업", bad)
            return
        super().accept()

    def _build_specs_tab(self) -> QWidget:
        """컴퓨터 사양 — 이 PC 확인 + 최소·권장 사양 + 동영상 화질 모드 설명 (app/system_specs.py)."""
        info = system_specs.detect(getattr(self.config, "media_root", None))
        self.specs_label = QLabel(system_specs.html(info))
        self.specs_label.setWordWrap(True)
        self.specs_label.setTextFormat(Qt.TextFormat.RichText)
        self.specs_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.specs_label.setContentsMargins(8, 4, 8, 8)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(self.specs_label)
        return area

    def show_tab(self, name: str) -> None:
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == name:
                self.tabs.setCurrentIndex(i)
                return

    # ---- 촬영 모드 탭 ----
    def _build_preset_tab(self) -> QWidget:
        from app.capture import camera_props, presets
        c = self.config
        self._presets = [dict(p, camera_props=dict(p["camera_props"])) for p in presets.all_presets(c)]
        self._active = c.active_preset or self._presets[0]["name"]
        self._loading = False
        w = QWidget()
        root = QHBoxLayout(w)
        # 왼쪽: 목록
        left = QVBoxLayout()
        intro = QLabel("자주 쓰는 촬영 설정을 모드로 저장해 두면 설정 → 일반의 '촬영 모드' 에서 바로 바꿉니다.  ★ = 지금 모드")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        left.addWidget(intro)
        self.preset_list = QListWidget()
        self.preset_list.setMinimumWidth(180)
        left.addWidget(self.preset_list, 1)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        self.btn_p_add = QPushButton("지금 설정으로 추가")
        self.btn_p_copy = QPushButton("복제")
        self.btn_p_del = QPushButton("삭제")
        self.btn_p_use = QPushButton("★ 지금 모드로")
        for b in (self.btn_p_add, self.btn_p_copy):
            row1.addWidget(b)
        for b in (self.btn_p_use, self.btn_p_del):
            row2.addWidget(b)
        left.addLayout(row1)
        left.addLayout(row2)
        root.addLayout(left, 2)
        # 오른쪽: 편집
        right = QVBoxLayout()
        form = QFormLayout()
        self.p_name = QLineEdit()
        form.addRow("이름", self.p_name)
        self.p_w = QSpinBox()
        self.p_w.setRange(320, 7680)
        self.p_h = QSpinBox()
        self.p_h.setRange(240, 4320)
        form.addRow("해상도", self._row(self.p_w, QLabel("×"), self.p_h))
        self.p_props: dict = {}
        for prop in camera_props.PROPS:
            chk = QCheckBox("지정")
            chk.setToolTip("끄면 이 항목은 카메라를 건드리지 않습니다 (자동 노출 · 자동 WB 는 자동)")
            if prop.toggle:
                val = QCheckBox("켜기")
                val.setChecked(bool(prop.default))
                val.toggled.connect(lambda _on: self._preset_edited())
            else:
                val = QSpinBox()
                val.setRange(int(prop.lo), int(prop.hi))
                val.setSingleStep(int(prop.step) or 1)
                val.setValue(int(prop.default))
                val.valueChanged.connect(lambda _v: self._preset_edited())
            val.setEnabled(False)
            chk.toggled.connect(val.setEnabled)
            chk.toggled.connect(lambda _on: self._preset_edited())
            self.p_props[prop.key] = (chk, val)
            form.addRow(prop.label, self._row(val, chk))
        self.p_smart = QComboBox()
        for code, text in (("off", "끄기"), ("freeze", "자동 정지"), ("snapshot", "자동 저장")):
            self.p_smart.addItem(text, code)
        self.p_peaking = QCheckBox("포커스 피킹 켜기")
        self.p_split = QComboBox()
        for n in (2, 4, 6, 8, 16):
            self.p_split.addItem(f"{n}분할", n)
        form.addRow("Smart 자동 촬영", self.p_smart)
        form.addRow("피킹", self.p_peaking)
        form.addRow("분할 칸 수", self.p_split)
        right.addLayout(form)
        self.btn_p_preview = QPushButton("카메라에 바로 적용 (미리보기)")
        self.btn_p_preview.setToolTip("체크한 카메라 값을 지금 화면에 바로 적용해 봅니다 (취소하면 되돌림)")
        self.btn_p_preview.setEnabled("camera_props" in self._actions)
        right.addWidget(self.btn_p_preview)
        hint = QLabel("'지정'을 끄면 그 항목은 카메라를 건드리지 않습니다(자동 노출 · 자동 WB 는 자동). "
                      "카메라마다 지원하는 항목 · 범위가 다릅니다.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        right.addWidget(hint)
        right.addStretch(1)
        root.addLayout(right, 3)

        self.preset_list.currentRowChanged.connect(lambda r: self._load_preset(r))
        self.p_name.textEdited.connect(lambda _t: self._preset_edited(rename=True))
        for wdg in (self.p_w, self.p_h):
            wdg.valueChanged.connect(lambda _v: self._preset_edited())
        self.p_smart.currentIndexChanged.connect(lambda _i: self._preset_edited())
        self.p_peaking.toggled.connect(lambda _on: self._preset_edited())
        self.p_split.currentIndexChanged.connect(lambda _i: self._preset_edited())
        self.btn_p_add.clicked.connect(lambda: self._add_preset(copy=False))
        self.btn_p_copy.clicked.connect(lambda: self._add_preset(copy=True))
        self.btn_p_del.clicked.connect(lambda: self._del_preset())
        self.btn_p_use.clicked.connect(lambda: self._use_preset())
        self.btn_p_preview.clicked.connect(lambda: self._preview())
        self._refresh_preset_list(select=next((i for i, p in enumerate(self._presets)
                                                if p["name"] == self._active), 0))
        return w

    def _sync_preset_combo(self) -> None:
        if not hasattr(self, "preset_combo"):
            return
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for p in self._presets:
            self.preset_combo.addItem(p["name"])
        names = [p["name"] for p in self._presets]
        self.preset_combo.setCurrentIndex(names.index(self._active) if self._active in names else 0)
        self.preset_combo.blockSignals(False)

    def _on_preset_combo(self, idx: int) -> None:
        if 0 <= idx < len(getattr(self, "_presets", [])):
            self._active = self._presets[idx]["name"]
            self._refresh_preset_list(select=idx)

    def _refresh_preset_list(self, select: int = 0) -> None:
        self._sync_preset_combo()
        self.preset_list.blockSignals(True)
        self.preset_list.clear()
        for p in self._presets:
            QListWidgetItem(("★ " if p["name"] == self._active else "   ") + p["name"], self.preset_list)
        self.preset_list.blockSignals(False)
        self.preset_list.setCurrentRow(max(0, min(select, len(self._presets) - 1)))
        self._load_preset(self.preset_list.currentRow())
        self.btn_p_del.setEnabled(len(self._presets) > 1)

    def _load_preset(self, row: int) -> None:
        if not (0 <= row < len(self._presets)):
            return
        p = self._presets[row]
        self._loading = True
        self.p_name.setText(p["name"])
        self.p_w.setValue(int(p["capture_width"]))
        self.p_h.setValue(int(p["capture_height"]))
        for key, (chk, val) in self.p_props.items():
            v = p["camera_props"].get(key)
            chk.setChecked(v is not None)
            if v is not None:
                if isinstance(val, QCheckBox):
                    val.setChecked(bool(v))
                else:
                    val.setValue(int(round(v)))
        self.p_smart.setCurrentIndex(max(0, self.p_smart.findData(p["smart_mode"])))
        self.p_peaking.setChecked(bool(p["smart_peaking"]))
        self.p_split.setCurrentIndex(max(0, self.p_split.findData(int(p["split_count"]))))
        self._loading = False

    def _editor_props(self) -> dict:
        out = {}
        for k, (chk, val) in self.p_props.items():
            if chk.isChecked():
                out[k] = (1.0 if val.isChecked() else 0.0) if isinstance(val, QCheckBox) else float(val.value())
        return out

    def _preview(self) -> None:
        self.previewed = True
        self._actions["camera_props"](self._editor_props())

    def _preset_edited(self, rename: bool = False) -> None:
        if self._loading:
            return
        row = self.preset_list.currentRow()
        if not (0 <= row < len(self._presets)):
            return
        p = self._presets[row]
        old_name = p["name"]
        new_name = self.p_name.text().strip() or old_name
        if rename and any(q["name"] == new_name for i, q in enumerate(self._presets) if i != row):
            return                                   # 이름이 겹치면 그대로 둔다
        p.update(name=new_name, capture_width=self.p_w.value(), capture_height=self.p_h.value(),
                 camera_props=self._editor_props(), smart_mode=self.p_smart.currentData() or "off",
                 smart_peaking=self.p_peaking.isChecked(), split_count=int(self.p_split.currentData() or 4))
        if self._active == old_name:
            self._active = new_name
        it = self.preset_list.item(row)
        if it is not None:
            it.setText(("★ " if new_name == self._active else "   ") + new_name)

    def _unique_name(self, base: str) -> str:
        names = {p["name"] for p in self._presets}
        if base not in names:
            return base
        for i in range(2, 100):
            if f"{base} {i}" not in names:
                return f"{base} {i}"
        return base + " *"

    def _add_preset(self, copy: bool) -> None:
        from app.capture import presets
        row = self.preset_list.currentRow()
        if copy and 0 <= row < len(self._presets):
            src = self._presets[row]
            p = dict(src, camera_props=dict(src["camera_props"]), name=self._unique_name(src["name"] + " 복사"))
        else:
            p = presets.from_config(self.config, self._unique_name("새 모드"))
        self._presets.append(p)
        self._refresh_preset_list(select=len(self._presets) - 1)
        self.p_name.setFocus()
        self.p_name.selectAll()

    def _del_preset(self) -> None:
        row = self.preset_list.currentRow()
        if len(self._presets) <= 1 or not (0 <= row < len(self._presets)):
            return
        gone = self._presets.pop(row)
        if gone["name"] == self._active:
            self._active = self._presets[0]["name"]
        self._refresh_preset_list(select=max(0, row - 1))

    def _use_preset(self) -> None:
        row = self.preset_list.currentRow()
        if 0 <= row < len(self._presets):
            self._active = self._presets[row]["name"]
            self._refresh_preset_list(select=row)

    # ---- 전송 탭 ----
    def _build_send_tab(self) -> QWidget:
        from app.services import send_service as ss
        c = self.config
        w = QWidget()
        lay = QVBoxLayout(w)
        intro = QLabel("촬영 화면 도크의 '전송' 버튼은 오른쪽 목록에서 고른 사진을 아래 방법 · 형식으로 보냅니다.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.send_method = QComboBox()
        for code, text in ss.METHODS:
            self.send_method.addItem(text, code)
        i = self.send_method.findData(getattr(c, "send_method", "folder"))
        self.send_method.setCurrentIndex(i if i >= 0 else 0)
        form.addRow("전송 방법", self.send_method)

        # 폴더
        self.send_folder = QLineEdit(getattr(c, "send_folder", "") or "")
        self.send_folder.setPlaceholderText("예) D:\\환자사진  ·  \\\\서버\\공유폴더")
        b = QPushButton("폴더 선택")
        b.clicked.connect(lambda: self._pick_dir(self.send_folder, "전송할 폴더 선택"))
        self.row_folder = self._row(self.send_folder, b)
        form.addRow("전송 폴더", self.row_folder)
        # 클라우드
        self.send_cloud = QComboBox()
        self.send_cloud.setEditable(True)
        for p in ss.cloud_candidates():
            self.send_cloud.addItem(str(p))
        self.send_cloud.setCurrentText(getattr(c, "send_cloud_folder", "") or
                                       (self.send_cloud.itemText(0) if self.send_cloud.count() else ""))
        self.send_cloud.lineEdit().setPlaceholderText("OneDrive · Google Drive · Dropbox · iCloud 폴더")
        b2 = QPushButton("폴더 선택")
        b2.clicked.connect(lambda: self._pick_dir(self.send_cloud.lineEdit(), "클라우드 동기화 폴더 선택"))
        self.row_cloud = self._row(self.send_cloud, b2)
        form.addRow("클라우드 폴더", self.row_cloud)
        self.cloud_hint = QLabel("PC 에 설치된 클라우드 앱(OneDrive · Google Drive · Dropbox · iCloud)의 폴더에 저장하면 "
                                 "그 앱이 인터넷으로 올려 줍니다." + ("" if self.send_cloud.count()
                                                               else "  (이 PC 에서 찾은 클라우드 폴더가 없습니다)"))
        self.cloud_hint.setObjectName("Muted")
        self.cloud_hint.setWordWrap(True)
        form.addRow("", self.cloud_hint)
        # PACS
        self.pacs_host = QLineEdit(getattr(c, "send_pacs_host", "") or "")
        self.pacs_host.setPlaceholderText("예) 192.168.0.10  ·  pacs.hospital.local")
        self.pacs_port = QSpinBox()
        self.pacs_port.setRange(1, 65535)
        self.pacs_port.setValue(int(getattr(c, "send_pacs_port", 104) or 104))
        self.pacs_ae = QLineEdit(getattr(c, "send_pacs_ae", "PACS") or "PACS")
        self.pacs_ae.setMaxLength(16)
        self.pacs_calling = QLineEdit(getattr(c, "send_pacs_calling_ae", "SLITLAMP") or "SLITLAMP")
        self.pacs_calling.setMaxLength(16)
        self.btn_pacs_test = QPushButton("연결 확인")
        self.btn_pacs_test.clicked.connect(lambda: self._test_pacs())
        self.pacs_status = QLabel("")
        self.pacs_status.setObjectName("Muted")
        self.row_pacs = []
        for label, widget in (("PACS 주소", self.pacs_host), ("포트", self.pacs_port),
                              ("PACS AE Title", self.pacs_ae), ("이 PC AE Title", self.pacs_calling),
                              ("", self._row(self.btn_pacs_test, self.pacs_status))):
            form.addRow(label, widget)
            self.row_pacs.append(widget)
        form.addRow(self._rule())

        # 형식
        fmt_box = QWidget()
        fl = QHBoxLayout(fmt_box)
        fl.setContentsMargins(0, 0, 0, 0)
        self.send_formats: dict = {}
        chosen = set(getattr(c, "send_formats", ["original"]) or [])
        for code, text in ss.FORMATS:
            cb = QCheckBox(text)
            cb.setChecked(code in chosen)
            self.send_formats[code] = cb
            fl.addWidget(cb)
        fl.addStretch(1)
        form.addRow("파일 형식", fmt_box)
        self.fmt_hint = QLabel("PDF 는 고른 사진을 환자 정보 · 소견란과 함께 한 부로 만듭니다. "
                               "PACS 로는 DICOM 으로만 보냅니다.")
        self.fmt_hint.setObjectName("Muted")
        self.fmt_hint.setWordWrap(True)
        form.addRow("", self.fmt_hint)
        lay.addLayout(form)
        lay.addStretch(1)
        self._form_send = form
        self.send_method.currentIndexChanged.connect(lambda _i: self._sync_send_rows())
        self._sync_send_rows()
        return w

    @staticmethod
    def _row(*widgets) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        for i, x in enumerate(widgets):
            h.addWidget(x, 1 if i == 0 else 0)
        return w

    def _pick_dir(self, edit, title: str) -> None:
        d = QFileDialog.getExistingDirectory(self, title, edit.text() or "")
        if d:
            edit.setText(d)

    def _sync_send_rows(self) -> None:
        m = self.send_method.currentData()
        form = self._form_send
        form.setRowVisible(self.row_folder, m == "folder")
        form.setRowVisible(self.row_cloud, m == "cloud")
        form.setRowVisible(self.cloud_hint, m == "cloud")
        for w in self.row_pacs:
            form.setRowVisible(w, m == "pacs")
        for code, cb in self.send_formats.items():
            cb.setEnabled(m != "pacs" or code == "dicom")

    def _send_values(self) -> dict:
        return {
            "send_method": self.send_method.currentData() or "folder",
            "send_folder": self.send_folder.text().strip(),
            "send_cloud_folder": self.send_cloud.currentText().strip(),
            "send_formats": [k for k, cb in self.send_formats.items() if cb.isChecked()],
            "send_pacs_host": self.pacs_host.text().strip(),
            "send_pacs_port": int(self.pacs_port.value()),
            "send_pacs_ae": self.pacs_ae.text().strip() or "PACS",
            "send_pacs_calling_ae": self.pacs_calling.text().strip() or "SLITLAMP",
        }

    def _test_pacs(self) -> None:
        from types import SimpleNamespace
        from app.services import send_service as ss
        self.pacs_status.setText("확인 중…")
        self.repaint()
        ok, msg = ss.test_pacs(SimpleNamespace(**self._send_values()))
        self.pacs_status.setText(("✔ " if ok else "✖ ") + msg)

    @staticmethod
    def _rule() -> QFrame:
        f = QFrame()
        f.setObjectName("ThumbSep")
        f.setFixedHeight(1)
        return f

    def _quick(self, text: str, icon_name: str, tip: str, key: str) -> QPushButton:
        b = QPushButton(icons.gap(text))
        b.setIcon(icons.icon(icon_name, ICON_ACCENT, 18))
        b.setIconSize(QSize(18, 18))
        b.setToolTip(tip)
        b.setMinimumHeight(40)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setEnabled(key in self._actions)
        b.clicked.connect(lambda _=False, k=key: self.run_action(k))
        return b

    def run_action(self, key: str) -> None:
        fn = self._actions.get(key)
        if fn is None:
            return
        if key == "camera":
            fn()
            self.quick_status.setText("카메라를 다시 연결했습니다.")
        else:
            fn(self)                          # 단축키 창 · 폴더 선택 창은 이 설정 창 위에 뜬다

    def _fill_cameras(self) -> None:
        """연결된 카메라를 이름으로 나열한다 (소니 ★). 저장한 카메라가 안 보이면 안내를 띄운다."""
        cfg = self.config
        current = self.camera_index.currentData()
        if current is None:
            current = cfg.camera_index
        names = device_names()
        probe = self.source_mode.currentText() == "device"      # 번호로 열어 보기는 실제 장비 모드에서만
        devs = list(range(len(names))) if names else (
            enumerate_devices(backend=cfg.camera_backend) if probe else [])
        self._names = names
        self.camera_index.clear()
        # 이름을 알면 목록에서 고르기만, 모르면 번호를 직접 칠 수 있게
        self.camera_index.setEditable(not names)
        for d in devs:
            label = f"{d}: {'★ ' if is_sony(names[d]) else ''}{names[d]}" if d < len(names) else str(d)
            self.camera_index.addItem(label, d)
        if not devs:
            self.camera_index.addItem(str(current), current)
        if cfg.camera_name and cfg.camera_name in names:
            current = names.index(cfg.camera_name)
        i = self.camera_index.findData(current)
        if i >= 0:
            self.camera_index.setCurrentIndex(i)
        if cfg.camera_name and names and cfg.camera_name not in names:
            self.camera_hint.setText(
                f"저장된 카메라 '{cfg.camera_name}' 가 지금 연결되어 있지 않습니다. "
                "카메라를 켜고 USB 를 연결한 뒤 '다시 찾기' 를 누르거나, 위 목록에서 다른 카메라를 고르세요.")
            self.camera_hint.setVisible(True)
        elif not devs and probe:
            self.camera_hint.setText("연결된 카메라를 찾지 못했습니다. 카메라를 연결한 뒤 '다시 찾기' 를 누르세요.")
            self.camera_hint.setVisible(True)
        else:
            self.camera_hint.setVisible(False)

    def _preview_sound(self) -> None:
        was = sounds.is_enabled()
        sounds.set_enabled(True)
        try:
            sounds.play("shutter", volume=self.volume.value())
        finally:
            sounds.set_enabled(was)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.export.text() or "")
        if d:
            self.export.setText(d)

    def apply_to_config(self):
        c = self.config
        c.source_mode = self.source_mode.currentText()
        data = self.camera_index.currentData()
        if data is None:
            try:
                data = int(self.camera_index.currentText() or 0)
            except ValueError:
                data = 0
        c.camera_index = int(data)
        c.camera_name = self._names[c.camera_index] if c.camera_index < len(self._names) else ""
        c.capture_width = self.width.value()
        c.capture_height = self.height.value()
        c.photo_format = self.photo_format.currentText()
        c.video_quality = self.video_quality.currentData() or "normal"
        c.export_root = self.export.text().strip()
        c.test_file_path = self.test_file.text().strip()
        c.sounds_enabled = self.sounds.isChecked()
        c.sound_volume = int(self.volume.value())
        c.agent_enabled = self.agent.isChecked()
        c.agent_save_clips = self.agent_clips.isChecked()
        c.agent_logging = self.agent_logging.isChecked()
        c.theme = self.theme.currentData() or "dark"
        c.start_screen = "home" if self.start_home.isChecked() else "capture"
        c.smart_bar_visible = self.smart_visible.isChecked()
        c.view_after_capture = self.view_after.isChecked()
        c.live_multi = self.live_multi.isChecked()
        c.right_click_action = self.right_click.currentData() or "menu"
        c.right_click_items = [k for k, cb in self.menu_items.items() if cb.isChecked()] or ["send", "multi", "delete"]
        c.thumbs_split_eyes = self.split_eyes.isChecked()
        c.split_count = int(self.split_count.currentData() or 4)
        c.split_count_saved = not self.split_ask.isChecked()
        for k, v in self._send_values().items():
            setattr(c, k, v)
        c.usb_buttons = self.usb_tab.values()
        c.usb_eye_auto = self.usb_tab.eye_auto()
        c.usb_device_check = self.usb_tab.device_check()
        for k, v in self.backup_tab.values().items():
            setattr(c, k, v)
        c.capture_presets = [dict(p, camera_props=dict(p["camera_props"])) for p in self._presets]
        if any(p["name"] == self._active for p in self._presets):
            c.active_preset = self._active
        c.save()
        return c

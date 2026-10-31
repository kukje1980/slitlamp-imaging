"""하단 촬영 도크 — 아이콘 메뉴: OD/OS 안구 스위치 + Smart · 참조(줄 열고 닫기) + 분할(2·4·6·8·16) · 정지영상 · 셔터(사진) · 녹화.

버튼 폭은 고정이라 글자가 바뀌어도(정지영상↔라이브, 녹화↔녹화 중지) 버튼 위치가 움직이지 않는다."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from app.ui import icons
from app.ui.icon_button import IconTextButton
from app.ui.theme import ICON, ICON_ACCENT, ICON_ON_ACCENT, ICON_RECORD

_EYE_CODES = ["NA", "OD", "OS"]
_EYE_BUTTONS = [("OS", "OS", "좌안"), ("OD", "OD", "우안"), ("NA", "—", "미지정")]   # OS 왼쪽 · OD 오른쪽
_BTN_W = 76            # 도크 버튼 고정 폭 (가장 긴 이름 '녹화 중지'·'정지영상' 기준)
_BTN_W_COMPACT = 44    # 좁은 창(아이콘만)


def tool_button(text: str, icon_name: str, obj: str, checkable: bool = False, color: str = ICON,
                tip: str = "", icon_size: int = 22, min_width: int = 60, on_color: str | None = None) -> IconTextButton:
    """아이콘 위 · 짧은 이름 아래 형태의 메뉴 버튼 (상단바·촬영 도크·검사 기록 공용)."""
    ic = icons.icon(icon_name, color, icon_size)
    if on_color:
        ic.addPixmap(icons.pixmap(icon_name, on_color, icon_size), ic.Mode.Normal, ic.State.On)
    b = IconTextButton(text, ic, icon_size)
    b.setObjectName(obj)
    b.setCheckable(checkable)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setMinimumWidth(min_width)
    if tip:
        b.setToolTip(tip)
    return b


class ControlBar(QFrame):
    pauseToggled = Signal(bool)
    captureClicked = Signal()
    recordToggled = Signal(bool)
    freezeClicked = Signal()
    quadToggled = Signal(bool)
    saveQuadClicked = Signal()
    clearQuadClicked = Signal()
    eyeChanged = Signal(str)             # 검사눈이 바뀜 (직접 고름 · 단축키 · 슬릿램프 신호 모두)
    eyeChosen = Signal(str)              # OD · OS · — 버튼을 직접 눌러 고름 (슬릿램프 좌우 신호와 구분)
    smartToggled = Signal(bool)          # Smart 도구줄 보이기 / 숨기기
    refsToggled = Signal(bool)           # 참조 줄 보이기 / 숨기기
    multiToggled = Signal(bool)          # 멀티: 선택한 사진 한 번에 보기 (다시 누르면 초기화)
    saveMultiClicked = Signal()          # 멀티 화면 저장
    sendClicked = Signal()               # 전송: 고른 사진을 폴더 · 클라우드 · PACS 로
    presetClicked = Signal()             # 촬영 모드 (자주 쓰는 카메라 · 촬영 설정) 고르기

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Dock")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 12, 6)
        lay.setSpacing(4)

        # 안구 선택: 세그먼트 버튼 (eye_combo 는 기존 API 호환용으로 숨겨 둔다)
        self.eye_combo = QComboBox()
        self.eye_combo.addItems(["미지정", "OD(우안)", "OS(좌안)"])
        self.eye_combo.setVisible(False)
        seg = QFrame()
        seg.setObjectName("EyeSeg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.eye_group = QButtonGroup(self)
        self.eye_buttons = {}
        for code, text, ko in _EYE_BUTTONS:
            b = QPushButton(text)
            b.setObjectName("EyeBtn")
            b.setProperty("eye", code)
            b.setCheckable(True)
            b.setToolTip(ko)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.eye_group.addButton(b)
            self.eye_buttons[code] = b
            sl.addWidget(b)
            b.clicked.connect(lambda _=False, c=code: self._choose_eye(c))
        self.eye_buttons["NA"].setChecked(True)
        # 좌우 자동 인식 상태 — 미확인 · 자동 · 수동 · 확인 필요 (메인 창이 set_eye_status 로 정함)
        self.eye_status = QLabel("")
        self.eye_status.setObjectName("EyeStatus")
        self.eye_status.setProperty("level", "off")
        self.eye_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.eye_status.setVisible(False)
        # 도크는 왼쪽 · 가운데 · 오른쪽 세 칸: 가운데(정지영상 · 셔터 · 녹화)는 어떤 모드에서도 제자리
        self._left = QWidget()
        ll = QHBoxLayout(self._left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        ll.addWidget(seg)
        ll.addSpacing(4)
        ll.addWidget(self.eye_status)
        ll.addSpacing(6)
        ll.addWidget(self._sep())
        ll.addSpacing(6)

        self.btn_quad = tool_button("분할", "quad", "Pause", checkable=True,
                                    tip="분할 정지영상 — 누르면 2 · 4 · 6 · 8 · 16칸 중에서 고릅니다 (고른 칸 수는 저장)")
        self.split_count = 4                 # 지금 / 다음 분할 칸 수
        self.count_chooser = None            # 호출형(anchor) → 칸 수 | None — 없으면 split_count 그대로
        self.count_saved = False             # True = 칸 수를 한 번 골라 저장함 → 누르면 묻지 않고 그 칸 수로
        self._quad_by_mouse = False          # 마우스로 누를 때만 칸 수를 묻는다 (코드로 켤 때는 묻지 않음)
        # 오른쪽 클릭: 언제든 칸 수 바꾸기 (분할 중이면 새 칸 수로 다시 시작)
        self.btn_quad.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_quad.customContextMenuRequested.connect(lambda _pos: self.change_split_count())
        self.btn_pause = tool_button("일시정지", "pause", "Pause", checkable=True, tip="일시정지")
        self.btn_freeze = tool_button("정지영상", "freeze", "Freeze", color=ICON_ACCENT,
                                      tip="누르면 화면 멈춤 · 다시 누르면 라이브 (4분할에서는 칸 채우기)")
        self.btn_capture = QPushButton()
        self.btn_capture.setObjectName("Shutter")
        self.btn_capture.setIcon(icons.icon("camera", ICON_ON_ACCENT, 26))
        self.btn_capture.setIconSize(QSize(26, 26))
        self.btn_capture.setFixedSize(56, 56)
        self.btn_capture.setToolTip("사진 캡처")
        self.btn_capture.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_capture.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_record = tool_button("녹화", "rec", "Record", checkable=True, color=ICON_RECORD,
                                      tip="녹화 시작/중지")
        self.btn_save_quad = tool_button("저장", "save", "Primary", color=ICON_ON_ACCENT, tip="분할 화면 저장 (칸별 + 합성)")
        self.btn_clear_quad = tool_button("비우기", "eraser", "ToolBtn", tip="분할 칸 모두 비우기")
        self.btn_close_multi = tool_button("초기화", "history", "ToolBtn",
                                           tip="멀티 화면 닫기 — 고른 사진 · 참조를 비우고 라이브 촬영 화면으로 ('멀티' 를 다시 누른 것과 같음)")

        self.btn_smart = tool_button("Smart", "aperture", "Pause", checkable=True, on_color=ICON_ACCENT,
                                     tip="Smart 도구줄 보이기 / 숨기기 (자동 촬영 설정은 그대로 유지)")
        self.btn_smart.setChecked(True)
        self.btn_refs = tool_button("참조", "image", "Pause", checkable=True, on_color=ICON_ACCENT,
                                    tip="참조 줄 보이기 / 숨기기 — 질환별 참조 영상 · 샘플 사진을 골라 "
                                        "멀티 화면에서 지금 환자 사진과 같이 보기")
        self.btn_multi = tool_button("멀티", "review", "Pause", checkable=True, on_color=ICON_ACCENT,
                                     tip="오른쪽 목록에서 한 번 눌러 고른 사진들을 한 화면에 모아 보기 · 다시 누르면 선택 초기화")
        self.btn_preset = tool_button("모드", "sliders", "ToolBtn",
                                      tip="촬영 모드 — 자주 쓰는 카메라 · 촬영 설정을 골라 바로 바꾸기 / 지금 설정 저장")
        self.btn_send = tool_button("전송", "send", "ToolBtn",
                                    tip="오른쪽 목록에서 고른 사진을 폴더 · 클라우드 · PACS(DICOM) 로 보내기 (방법 · 형식은 설정 → 전송)")
        self.btn_save_multi = tool_button("저장", "save", "Primary", color=ICON_ON_ACCENT,
                                          tip="지금 보이는 멀티 화면을 한 장의 사진으로 저장")

        # 일시정지는 정지영상(누르면 멈춤 · 다시 누르면 라이브)과 같은 기능이라 화면에 두지 않는다
        self.btn_pause.setVisible(False)
        ll.addWidget(self.btn_smart)
        ll.addWidget(self.btn_refs)
        # '멀티' 는 오른쪽 '촬영 이미지' 칸(OD · OS 위)의 버튼, '모드'(촬영 모드)는 설정 창으로 옮겼다 —
        # 여기 버튼은 상태 · 신호만 들고 화면엔 안 보임
        for hidden in (self.btn_multi, self.btn_preset):
            hidden.setParent(self)
            hidden.hide()
        self.multi_mirrors: list = []        # 같은 멀티 상태를 보여 줄 다른 버튼들 (촬영 이미지 칸의 '멀티')
        ll.addStretch(1)
        # 가운데: [분할 · 저장 · 정지영상] (한 묶음) [셔터] [녹화] [전송] [비우기]
        # 저장 · 비우기 칸은 버튼이 숨거나 꺼져도 자리를 지켜 셔터가 항상 같은 곳에 있다
        center = QWidget()
        cl = QHBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self._save_slot = self._slot(self.btn_save_quad, self.btn_save_multi)
        self._clear_slot = self._slot(self.btn_clear_quad, self.btn_close_multi)   # 분할 = 비우기 · 멀티 = 초기화 (같은 자리)
        self.still_group = QFrame()
        self.still_group.setObjectName("DockGroup")      # 분할 · 저장 · 정지영상 묶음
        gl = QHBoxLayout(self.still_group)
        gl.setContentsMargins(3, 3, 3, 3)
        gl.setSpacing(2)
        gl.addWidget(self.btn_quad)
        gl.addWidget(self._save_slot)
        gl.addWidget(self.btn_freeze)
        cl.addWidget(self.still_group)
        cl.addWidget(self.btn_capture)
        cl.addWidget(self.btn_record)
        cl.addWidget(self.btn_send)
        cl.addWidget(self._clear_slot)
        self._right = QWidget()
        rl = QHBoxLayout(self._right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        rl.addStretch(1)
        for side in (self._left, self._right):
            side.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        for part in (self._left, center, self._right, self._save_slot, self._clear_slot):
            part.setObjectName("DockPart")          # 도크 바탕색이 그대로 보이게 (투명)
        lay.addWidget(self._left, 1)
        lay.addWidget(center, 0)
        lay.addWidget(self._right, 1)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(8, 8)
        self.status_dot.setStyleSheet("background:#3ccf7a;border-radius:4px;")
        self.status = QLabel("준비")
        self.status.setObjectName("StatusLabel")
        # 녹화 오른쪽 실시간 안내(정지영상 · 사진 저장됨 …)는 화면에 띄우지 않는다 — 글자는 기록용으로만 유지
        self.status_dot.setVisible(False)
        self.status.setVisible(False)
        self._compact = False
        self._sized_buttons = (self.btn_preset, self.btn_quad, self.btn_pause, self.btn_smart, self.btn_refs,
                               self.btn_multi,
                               self.btn_send, self.btn_freeze, self.btn_record, self.btn_save_quad,
                               self.btn_clear_quad, self.btn_save_multi, self.btn_close_multi)
        self._apply_widths()
        lay.addWidget(self.status_dot)
        lay.addWidget(self.status)

        self.btn_quad.pressed.connect(lambda: setattr(self, "_quad_by_mouse", True))
        self.btn_quad.released.connect(lambda: setattr(self, "_quad_by_mouse", False))
        self.btn_quad.toggled.connect(self._on_quad)
        self.btn_pause.toggled.connect(self._on_pause)
        self.btn_freeze.clicked.connect(self.freezeClicked.emit)
        self.btn_capture.clicked.connect(self.captureClicked.emit)
        self.btn_record.toggled.connect(self._on_record)
        self.btn_save_quad.clicked.connect(self.saveQuadClicked.emit)
        self.btn_clear_quad.clicked.connect(self.clearQuadClicked.emit)
        self.btn_smart.toggled.connect(self.smartToggled.emit)
        self.btn_refs.toggled.connect(self.refsToggled.emit)
        self.btn_multi.toggled.connect(self.multiToggled.emit)
        self.btn_save_multi.clicked.connect(lambda: self.saveMultiClicked.emit())
        self.btn_close_multi.clicked.connect(lambda: self.btn_multi.setChecked(False))   # → multiToggled(False)
        self.btn_send.clicked.connect(lambda: self.sendClicked.emit())
        self.btn_preset.clicked.connect(lambda: self.presetClicked.emit())
        self.eye_combo.currentIndexChanged.connect(self._sync_eye_buttons)

        self.set_capture_enabled(False)
        self.set_quad_mode(False)
        self.set_multi_mode(False)

    @staticmethod
    def _slot(*buttons) -> QWidget:
        w = QWidget()
        sl = QHBoxLayout(w)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)
        for b in buttons:
            sl.addWidget(b)
        return w

    def _apply_widths(self) -> None:
        bw = _BTN_W_COMPACT if self._compact else _BTN_W
        for b in self._sized_buttons:
            b.setFixedWidth(bw)
        for slot in (self._save_slot, self._clear_slot):
            slot.setMinimumWidth(bw)

    def is_compact(self) -> bool:
        return self._compact

    def set_compact(self, on: bool) -> None:
        """좁은 창: 도크 버튼을 아이콘만 남겨(이름은 풍선 도움말) 셔터까지 한눈에 보이게."""
        on = bool(on)
        if on == self._compact:
            return
        self._compact = on
        for b in self._sized_buttons:
            b.set_text_visible(not on)
        for b in self.eye_buttons.values():
            if on:
                b.setFixedWidth(_BTN_W_COMPACT)
            else:
                b.setMinimumWidth(0)
                b.setMaximumWidth(16777215)
        self._apply_widths()
        for w in (self._left, self.still_group, self._save_slot, self._clear_slot, self._right, self):
            if w.layout() is not None:
                w.layout().invalidate()
            w.updateGeometry()

    @staticmethod
    def _sep() -> QFrame:
        f = QFrame()
        f.setObjectName("DockSep")
        f.setFixedSize(1, 28)
        return f

    def _on_quad(self, checked: bool) -> None:
        by_mouse, self._quad_by_mouse = self._quad_by_mouse, False
        if checked and by_mouse and self.count_chooser is not None and not self.count_saved:
            n = self.count_chooser(self.btn_quad)       # 2 · 4 · 6 · 8 · 16 고르기 (취소하면 None)
            if not n:
                self.btn_quad.blockSignals(True)
                self.btn_quad.setChecked(False)
                self.btn_quad.blockSignals(False)
                return
            self.split_count = int(n)
            self.sync_quad_tip()
        self._apply_split_label(checked)
        self.set_quad_mode(checked)
        self.quadToggled.emit(checked)

    def change_split_count(self) -> None:
        """칸 수 고르기 메뉴를 (저장 여부와 상관없이) 띄운다. 분할 중이면 고른 칸 수로 다시 시작."""
        if self.count_chooser is None:
            return
        n = self.count_chooser(self.btn_quad)
        if not n:
            return
        self.split_count = int(n)
        if self.btn_quad.isChecked():
            self._apply_split_label(True)
            self.quadToggled.emit(True)          # 새 칸 수로 모두 비우고 1번 칸부터
        self.sync_quad_tip()

    def sync_quad_tip(self) -> None:
        if self.count_saved:
            self.btn_quad.setToolTip(f"분할 정지영상 — 저장한 {self.split_count}분할로 바로 시작합니다 "
                                     "(칸 수 바꾸기: 오른쪽 클릭 · 설정)")
        else:
            self.btn_quad.setToolTip("분할 정지영상 — 누르면 2 · 4 · 6 · 8 · 16칸 중에서 고릅니다 (고른 칸 수는 저장)")

    def start_split(self, n: int | None = None) -> None:
        """칸 수를 묻지 않고 바로 분할을 켠다 (단축키 Q · 저장된 칸 수)."""
        if n:
            self.split_count = int(n)
        self.btn_quad.blockSignals(True)
        self.btn_quad.setChecked(True)
        self.btn_quad.blockSignals(False)
        self._apply_split_label(True)
        self.set_quad_mode(True)
        self.quadToggled.emit(True)

    def _apply_split_label(self, on: bool) -> None:
        self.btn_quad.setText(f"{self.split_count}분할" if on else "분할")

    def _on_pause(self, checked: bool) -> None:
        self.btn_pause.setText("재개" if checked else "일시정지")
        self.btn_pause.setIcon(icons.icon("play" if checked else "pause", ICON_ACCENT if checked else ICON, 22))
        self.pauseToggled.emit(checked)

    def _on_record(self, checked: bool) -> None:
        self.btn_record.setText("녹화 중지" if checked else "녹화")
        self.btn_record.setIcon(icons.icon("stop" if checked else "rec", "#ffffff" if checked else ICON_RECORD, 22))
        self.recordToggled.emit(checked)

    def set_freeze_state(self, frozen: bool) -> None:
        """단일 모드 정지영상 상태에 맞춰 버튼 표시 (정지영상 ↔ 라이브)."""
        self.btn_freeze.setText("라이브" if frozen else "정지영상")
        self.btn_freeze.setIcon(icons.icon("play" if frozen else "freeze", ICON_ACCENT, 22))

    def set_quad_full(self, full: bool) -> None:
        """분할 칸이 다 차면 정지영상 버튼이 '처음부터'(전체 비우고 1번 칸부터 다시)로 바뀐다."""
        self.btn_freeze.setText("처음부터" if full else "정지영상")
        self.btn_freeze.setIcon(icons.icon("rot-l" if full else "freeze", ICON_ACCENT, 22))
        self.btn_freeze.setToolTip("모든 칸을 비우고 1번 칸부터 다시 촬영" if full
                                   else "누르면 화면 멈춤 · 다시 누르면 라이브 (분할에서는 칸 채우기)")

    def set_multi_mode(self, on: bool) -> None:
        """멀티 화면일 때: 저장 버튼 표시, 정지영상은 쓸 수 없음 (라이브가 아님)."""
        self.btn_multi.blockSignals(True)
        self.btn_multi.setChecked(on)
        self.btn_multi.blockSignals(False)
        for b in self.multi_mirrors:
            b.blockSignals(True)
            b.setChecked(on)
            b.blockSignals(False)
        self._multi_on = bool(on)
        self._sync_save_slot()
        self.btn_close_multi.setVisible(bool(on))
        self.btn_freeze.setEnabled(not on and self.btn_capture.isEnabled())   # 숨기지 않고 끔 (자리 유지)

    def set_preset_name(self, name: str) -> None:
        """도크의 '모드' 버튼 아래 글자에 지금 모드 이름 (길면 줄임)."""
        short = (name or "모드").split(" (")[0]
        self.btn_preset.setText(short if len(short) <= 5 else short[:4] + "…")
        self.btn_preset.setToolTip(f"촬영 모드: {name or '없음'} — 눌러서 다른 모드로 바꾸거나 지금 설정을 저장")

    def set_smart_visible(self, on: bool) -> None:
        self.btn_smart.blockSignals(True)
        self.btn_smart.setChecked(on)
        self.btn_smart.blockSignals(False)

    def set_refs_visible(self, on: bool) -> None:
        self.btn_refs.blockSignals(True)
        self.btn_refs.setChecked(on)
        self.btn_refs.blockSignals(False)

    def current_eye(self) -> str:
        return _EYE_CODES[self.eye_combo.currentIndex()]

    def set_eye(self, code: str) -> None:
        """검사눈을 정한다 — 직접 고를 때 · 단축키 · 슬릿램프 좌우 신호 모두 이 하나를 거친다 (화면 표시 = 저장값)."""
        if code in _EYE_CODES:
            self.eye_combo.setCurrentIndex(_EYE_CODES.index(code))
            self._sync_eye_buttons()

    def _choose_eye(self, code: str) -> None:
        self.set_eye(code)
        self.eyeChosen.emit(code)

    def set_eye_status(self, level: str, text: str, tip: str = "") -> None:
        """좌우 자동 인식 상태 칩: level = off(숨김) · unknown · device · manual(직접 · 시작 기본값) · stale."""
        lbl = self.eye_status
        lbl.setText(text)
        lbl.setToolTip(tip)
        lbl.setVisible(level != "off" and bool(text))
        if lbl.property("level") != level:
            lbl.setProperty("level", level)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def _sync_eye_buttons(self, *_):
        code = self.current_eye()
        self.eye_buttons[code].setChecked(True)
        self.eyeChanged.emit(code)

    def set_capture_enabled(self, on: bool) -> None:
        self._capture_on = bool(on)
        self.btn_capture.setEnabled(on)
        self.btn_record.setEnabled(on)
        self.btn_freeze.setEnabled(on)
        self._sync_save_slot()

    def set_quad_mode(self, on: bool) -> None:
        # 분할에서만 쓰는 '비우기' 는 그때만 보이고, '저장' 은 묶음 안에 늘 보이되 분할 · 멀티일 때만 눌린다
        self._quad_on = bool(on)
        self.btn_clear_quad.setVisible(on)
        self._sync_save_slot()

    def _sync_save_slot(self) -> None:
        multi = getattr(self, "_multi_on", False)
        self.btn_save_multi.setVisible(multi)
        self.btn_save_quad.setVisible(not multi)
        self.btn_save_quad.setEnabled(getattr(self, "_quad_on", False) and getattr(self, "_capture_on", False))

    def set_status(self, text: str) -> None:
        self.status.setText(text)
        bad = text.startswith("오류")
        rec = text.startswith("녹화 중")
        self.status_dot.setStyleSheet(
            f"background:{'#f0524f' if (bad or rec) else '#3ccf7a'};border-radius:4px;")

    def reset_record_button(self) -> None:
        self.btn_record.blockSignals(True)
        self.btn_record.setChecked(False)
        self.btn_record.setText("녹화")
        self.btn_record.setIcon(icons.icon("rec", ICON_RECORD, 22))
        self.btn_record.blockSignals(False)

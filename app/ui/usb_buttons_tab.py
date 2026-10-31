"""설정 → 'USB 버튼' 탭: 슬릿램프 USB 버튼 · 좌우 신호의 키 조합 · 기능을 보고 바꾸고, 신호를 직접 눌러 확인한다.

- 버튼 · 신호마다 키 조합(칸을 누르고 USB 버튼을 누르면 그 신호가 들어감) · 기능(녹화 · 스틸 · 사진 캡처 · 검사눈 OD / OS · 사용 안 함).
- 좌우 자동 인식 켜기 / 끄기 · 장치 확인(Windows Raw Input 으로 VID 04D9 / PID 1203 의 신호만) 설정.
- '신호 확인' 칸: 눌러 둔 뒤 USB 버튼을 누르면 받은 키 조합과 어느 버튼 · 기능인지, 뗌까지 왔는지, 슬릿램프 장치에서
  온 신호인지(Raw Input 이 될 때) 보여 준다 (설정 창에서는 녹화 · 스틸 · 눈 바꾸기를 실제로 실행하지 않는다).
- 저장은 설정 창 '확인' 때 (config.usb_buttons · usb_eye_auto · usb_device_check). 같은 키를 두 버튼 · 단축키에 함께 쓰면
  확인을 막고 알려 준다.
"""
from __future__ import annotations

from PySide6.QtCore import QKeyCombination, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import shortcuts, usb_buttons

_MOD_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr}


class KeyProbe(QLabel):
    """눌러 둔 뒤 USB 버튼을 누르면 받은 신호를 보여 주는 칸 (동작은 실행하지 않음)."""

    IDLE = "여기를 한 번 누른 뒤 슬릿램프 USB 버튼을 누르거나 슬릿램프를 좌우로 움직여 보세요"

    def __init__(self, tab: "UsbButtonsTab"):
        super().__init__(self.IDLE)
        self.tab = tab
        self.setObjectName("KeyProbe")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWordWrap(True)
        self.setMinimumHeight(52)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.presses = 0
        self.releases = 0
        self._text = ""

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.setFocus()
        super().mousePressEvent(e)

    def focusInEvent(self, e) -> None:  # noqa: N802
        if not self._text:
            self.setText("USB 버튼을 누르거나 슬릿램프를 움직여 보세요 …")
        super().focusInEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if Qt.Key(e.key()) in _MOD_KEYS:
            return
        if e.isAutoRepeat():
            return
        self.presses += 1
        combo = QKeySequence(QKeyCombination(e.modifiers(), Qt.Key(e.key())))
        key = combo.toString(QKeySequence.SequenceFormat.PortableText)
        bid = self.tab.button_for(e.key(), e.modifiers())
        who = (f"{usb_buttons.button_label(bid)} · {usb_buttons.action_label(self.tab.action_of(bid))}"
               if bid else "등록된 버튼이 아닙니다")
        self._text = f"받은 신호: {usb_buttons.display(key)}  →  {who}  ·  {self.tab.device_text(e.key())}"
        self.setText(self._text + "  (누름)")
        e.accept()

    def keyReleaseEvent(self, e) -> None:  # noqa: N802
        if Qt.Key(e.key()) in _MOD_KEYS or e.isAutoRepeat() or not self._text:
            return
        self.releases += 1
        self.setText(self._text + "  (누름 · 뗌 확인)")
        e.accept()


class UsbButtonsTab(QWidget):
    def __init__(self, config, parent=None, raw_input=None):
        super().__init__(parent)
        self.config = config
        self.raw_input = raw_input           # app/usb_raw_input.py 객체 (메인 창이 만든 것, 없으면 None)
        lay = QVBoxLayout(self)
        intro = QLabel("슬릿램프 USB 버튼 · 좌우 신호(키보드 신호를 보내는 장치)를 촬영 화면 동작에 연결합니다. 촬영 화면에서만 동작하고, "
                       "글자 입력칸 · 설정 창 · 다른 프로그램에서는 실행하지 않습니다. 길게 눌러도 한 번만, 뗀 뒤 다시 눌러야 다음 동작입니다.\n"
                       "확인한 장치: VID 04D9 / PID 1203 (MI_00 키보드). 드라이버 · 보드 설정은 바꾸지 않습니다.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        for col, text in enumerate(("", "키 조합 (칸을 누르고 버튼을 누름)", "", "기능")):
            h = QLabel(text)
            h.setObjectName("Muted")
            grid.addWidget(h, 0, col)
        self.key_edits: dict[str, QKeySequenceEdit] = {}
        self.action_combos: dict[str, QComboBox] = {}
        cur = usb_buttons.load(config)
        for row, (bid, name) in enumerate(usb_buttons.BUTTONS, start=1):
            ed = QKeySequenceEdit(QKeySequence(cur[bid]["key"]))
            if hasattr(ed, "setMaximumSequenceLength"):
                ed.setMaximumSequenceLength(1)
            b_def = QPushButton("기본값")
            b_def.setToolTip(f"기본: {usb_buttons.display(usb_buttons.DEFAULTS[bid]['key'])} · "
                             f"{usb_buttons.action_label(usb_buttons.DEFAULTS[bid]['action'])}")
            b_def.clicked.connect(lambda _=False, bid=bid: self.reset(bid))
            combo = QComboBox()
            for aid, label in usb_buttons.ACTIONS:
                combo.addItem(label, aid)
            combo.setCurrentIndex(max(0, combo.findData(cur[bid]["action"])))
            grid.addWidget(QLabel(name), row, 0)
            grid.addWidget(ed, row, 1)
            grid.addWidget(b_def, row, 2)
            grid.addWidget(combo, row, 3)
            self.key_edits[bid] = ed
            self.action_combos[bid] = combo
        lay.addLayout(grid)

        note = QLabel("· 녹화 시작 / 정지: 촬영 도크 '녹화' 버튼과 같습니다 (환자를 먼저 선택).\n"
                      "· 스틸 화면 정지 / 해제: 촬영 도크 '정지영상' 과 같고 사진 파일은 저장하지 않습니다 "
                      "(분할 화면에서는 칸 채우기, 녹화 중에 눌러도 녹화는 계속됩니다).\n"
                      "· 사진 캡처: 촬영 도크 셔터와 같습니다 — 정지 화면이면 그 화면을, 아니면 라이브 영상을 찍은 순간의 눈으로 저장.\n"
                      "· 좌우 신호 4 · 5: 슬릿램프가 환자 오른눈(OD) · 왼눈(OS) 쪽으로 갔을 때 보내는 신호 — 토글이 아니라 "
                      "늘 그 눈으로 정합니다. OD · OS 를 직접 골라도 되고, 다음 신호가 오면 다시 자동으로 바뀝니다.\n"
                      "· 처음 켰을 때 · 장치를 다시 꽂았을 때는 눈 위치를 짐작하지 않고 '미확인' 으로 두며, 다른 프로그램에서 "
                      "돌아오면 신호를 놓쳤을 수 있어 '확인 필요' 로 표시합니다.\n"
                      "· 사진은 찍은 순간의 눈으로 저장됩니다(정지 화면은 정지한 순간, 녹화는 시작한 순간 · 녹화 중 전환은 코멘트에 기록). "
                      "이미 저장된 사진의 좌우는 바뀌지 않습니다.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.chk_eye_auto = QCheckBox("슬릿램프 좌우 신호(4 · 5)로 검사눈 자동 인식 (끄면 OD · OS 를 직접 고름)")
        self.chk_eye_auto.setChecked(bool(getattr(config, "usb_eye_auto", True)))
        lay.addWidget(self.chk_eye_auto)
        self.chk_device_check = QCheckBox("장치 확인 — Windows Raw Input 으로 VID 04D9 / PID 1203 (MI_00) 장치의 신호만 좌우 신호로 받기")
        self.chk_device_check.setChecked(bool(getattr(config, "usb_device_check", True)))
        self.chk_device_check.setToolTip("다른 키보드에서 같은 키 조합을 눌러도 좌우가 바뀌지 않게 합니다. 장치를 다시 꽂으면 경로를 다시 "
                                         "확인합니다. 글자 입력은 수집 · 기록하지 않습니다. Raw Input 을 못 쓰는 환경(맥 · 등록 실패)에서는 "
                                         "모든 키보드 신호를 받습니다. 녹화 · 스틸 · 캡처 버튼(1 · 2 · 3)은 장치 확인 없이 받습니다.")
        lay.addWidget(self.chk_device_check)
        self.lbl_device = QLabel(self.raw_input.status_text() if self.raw_input is not None else
                                 "장치 확인(Raw Input): 촬영 창이 있을 때만 알 수 있습니다")
        self.lbl_device.setObjectName("Muted")
        self.lbl_device.setWordWrap(True)
        lay.addWidget(self.lbl_device)

        head = QLabel("신호 확인")
        head.setObjectName("ThumbTitle")
        lay.addWidget(head)
        self.probe = KeyProbe(self)
        lay.addWidget(self.probe)
        lay.addStretch(1)

    # ---- 값 ----
    def reset(self, bid: str) -> None:
        d = usb_buttons.DEFAULTS[bid]
        self.key_edits[bid].setKeySequence(QKeySequence(d["key"]))
        self.action_combos[bid].setCurrentIndex(max(0, self.action_combos[bid].findData(d["action"])))

    def values(self) -> dict:
        return {bid: {"key": self.key_edits[bid].keySequence().toString(QKeySequence.SequenceFormat.PortableText),
                      "action": self.action_combos[bid].currentData() or "none"}
                for bid, _name in usb_buttons.BUTTONS}

    def eye_auto(self) -> bool:
        return self.chk_eye_auto.isChecked()

    def device_check(self) -> bool:
        return self.chk_device_check.isChecked()

    def action_of(self, bid: str) -> str:
        return self.action_combos[bid].currentData() or "none"

    def button_for(self, key, modifiers) -> str | None:
        for bid, v in self.values().items():
            if v["key"] and usb_buttons.matches(v["key"], key, modifiers):
                return bid
        return None

    def device_text(self, qt_key) -> str:
        """'신호 확인' 칸에 덧붙일 장치 판정 (Raw Input 이 될 때만 의미 있음)."""
        if self.raw_input is None or not self.raw_input.available():
            return "장치 확인 없음"
        hit = self.raw_input.classify(int(qt_key))
        if hit is None:
            return "장치: 알 수 없음"
        return "장치: 슬릿램프 확인" if hit else "장치: 다른 키보드"

    def problem(self) -> str | None:
        """확인을 막을 문제 (같은 키를 두 곳에) — 없으면 None."""
        vals = self.values()
        keys = [(bid, v["key"]) for bid, v in vals.items() if v["key"]]
        for i, (a, ka) in enumerate(keys):
            for b, kb in keys[i + 1:]:
                if usb_buttons.same_key(ka, kb):
                    return (f"USB {usb_buttons.button_label(a)} 와 {usb_buttons.button_label(b)} 에 같은 키 "
                            f"'{usb_buttons.display(ka)}' 가 지정되어 있습니다.")
        for bid, k in keys:
            if any(usb_buttons.same_key(k, r) for r in shortcuts.RESERVED):
                return f"'{usb_buttons.display(k)}' 는 화면에서 이미 쓰는 키라 USB 버튼에 쓸 수 없습니다."
            for aid, sk in shortcuts.load(self.config).items():
                if aid in shortcuts.HIDDEN_IN_STANDARD:
                    continue                    # 촬영 앱에서 쓰지 않는 단축키(촬영 ↔ Pro 전환 F2)와는 겹쳐도 됨
                if usb_buttons.same_key(k, sk):
                    return (f"'{usb_buttons.display(k)}' 는 단축키 '{shortcuts.label(aid)}' 에도 지정되어 있습니다 — "
                            "설정 → 단축키 설정에서 그 단축키를 바꾸거나 USB 버튼에 다른 키를 고르세요.")
        return None

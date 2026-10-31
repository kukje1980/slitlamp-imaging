"""썸네일 · 멀티 칸을 두 번 누르면 뜨는 큰 보기. 동영상은 OS 기본 플레이어로 재생.

사진은 Pro 검토와 같은 캔버스(ImageCanvas): 기본 도구는 돋보기 — 사진을 누르고 있는 동안 확대 렌즈,
마우스 휠 = 확대 · 축소, '이동' 으로 바꾸면 끌어서 옮기기, '맞춤' = 창에 맞춤.

하단 '삭제' 버튼(Delete)을 누르고 확인하면 대화상자가 ``ImageViewerDialog.Deleted`` 코드로 닫히고,
실제 삭제(DB·파일)는 호출한 쪽(메인 창)이 수행한다.

after_capture=True: 사진을 찍은 직후 자동으로 뜨는 큰 화면. '다시 촬영'(Esc · Enter)을 누르면 닫히고 촬영 화면으로
돌아간다. capture_keys 로 준 촬영 단축키를 누르면 retakeRequested 가 나가 메인 창이 창을 닫고 바로 다시 찍는다.
"""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.ui import icons
from app.ui.pro.image_canvas import ImageCanvas
from app.ui.theme import ICON, ICON_ON_ACCENT

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def open_in_default_player(path: str) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:  # noqa: BLE001
        pass


def _load_bgr(path: str):
    if not path or cv2 is None or not os.path.exists(path):
        return None
    data = np.fromfile(path, dtype=np.uint8)          # 한글 경로 대응
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


class ImageViewerDialog(QDialog):
    Deleted = 2  # exec() 반환값: 사용자가 '삭제'를 확인함
    retakeRequested = Signal()   # 촬영 뒤 보기 창에서 촬영 단축키를 누름 → 닫고 바로 다시 찍기

    def __init__(self, capture, parent=None, allow_delete: bool = True, after_capture: bool = False,
                 capture_keys=()):
        super().__init__(parent)
        self.capture = capture
        self.after_capture = bool(after_capture)
        self.b_retake = None
        self.setWindowTitle((f"촬영한 사진 - {capture.captured_at}" if after_capture else f"보기 - {capture.captured_at}"))
        self.resize(920, 720)
        lay = QVBoxLayout(self)
        self.canvas = None
        for k in capture_keys or ():
            seq = QKeySequence(k)
            if not seq.isEmpty():
                QShortcut(seq, self, activated=lambda: self.retakeRequested.emit())

        if capture.media_type == "video":
            open_in_default_player(capture.file_path)
            lay.addWidget(QLabel("동영상을 기본 플레이어로 재생합니다.\n\n" + capture.file_path))
            lay.addLayout(self._footer(allow_delete))
            return

        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.tool_buttons = {}
        group = QButtonGroup(self)
        for tool, text, icon, tip in (("magnify", "돋보기", "lens", "사진을 누르고 있는 동안 확대 렌즈 (휠: 확대 · 축소)"),
                                      ("pan", "이동", "hand", "끌어서 옮기기 (휠: 확대 · 축소)")):
            b = QPushButton(icons.gap(text))
            b.setCheckable(True)
            b.setToolTip(tip)
            ic = icons.icon(icon, ICON, 16)
            ic.addPixmap(icons.pixmap(icon, ICON_ON_ACCENT, 16), ic.Mode.Normal, ic.State.On)
            b.setIcon(ic)
            b.setIconSize(QSize(16, 16))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, t=tool: self.set_tool(t))
            group.addButton(b)
            bar.addWidget(b)
            self.tool_buttons[tool] = b
        self.b_fit = QPushButton(icons.gap("맞춤"))
        self.b_fit.setIcon(icons.icon("fit", ICON, 16))
        self.b_fit.setIconSize(QSize(16, 16))
        self.b_fit.setToolTip("창에 맞춤")
        self.b_fit.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bar.addWidget(self.b_fit)
        hint = QLabel("누르고 있으면 확대 렌즈 · 휠로 확대 · 축소"
                      + ("   ·   '다시 촬영' 또는 촬영 키 · USB 버튼으로 촬영 화면으로" if self.after_capture else ""))
        hint.setObjectName("Muted")
        bar.addWidget(hint)
        bar.addStretch(1)
        lay.addLayout(bar)

        self.canvas = ImageCanvas()
        self.canvas._placeholder = "이미지를 불러올 수 없습니다."
        self.canvas.set_image(_load_bgr(capture.file_path))
        lay.addWidget(self.canvas, 1)
        self.b_fit.clicked.connect(self.canvas.reset_view)
        self.set_tool("magnify")
        lay.addLayout(self._footer(allow_delete))

    def set_tool(self, tool: str) -> None:
        if self.canvas is None:
            return
        self.canvas.set_tool(tool)
        self.tool_buttons[tool].setChecked(True)

    # ---- 하단: 삭제 · 닫기 ----
    def _footer(self, allow_delete: bool) -> QHBoxLayout:
        foot = QHBoxLayout()
        self.b_delete = QPushButton(icons.gap("삭제"))
        self.b_delete.setObjectName("ProDanger")
        self.b_delete.setIcon(icons.icon("trash", "#ef4f4c", 16))
        self.b_delete.setIconSize(QSize(16, 16))
        self.b_delete.setToolTip("이 영상을 DB와 디스크에서 삭제 (Delete)")
        self.b_delete.setShortcut(Qt.Key.Key_Delete)
        self.b_delete.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.b_delete.setVisible(allow_delete)
        self.b_delete.clicked.connect(self._confirm_delete)
        b_close = QPushButton("닫기")
        b_close.clicked.connect(self.accept)
        foot.addWidget(self.b_delete)
        foot.addStretch(1)
        if self.after_capture:
            # 촬영 직후 화면: 큰 버튼 하나로 촬영 화면 복귀 (Enter · Esc 도 같음)
            self.b_retake = QPushButton(icons.gap("다시 촬영"))
            self.b_retake.setObjectName("Primary")
            self.b_retake.setIcon(icons.icon("camera", ICON_ON_ACCENT, 18))
            self.b_retake.setIconSize(QSize(18, 18))
            self.b_retake.setMinimumHeight(40)
            self.b_retake.setToolTip("촬영 화면으로 돌아가기 (Enter · Esc) — 촬영 단축키 · USB 버튼을 누르면 바로 다시 찍습니다")
            self.b_retake.setDefault(True)
            self.b_retake.clicked.connect(self.accept)
            foot.addWidget(self.b_retake)
        else:
            b_close.setDefault(True)
        foot.addWidget(b_close)
        return foot

    def _confirm_delete(self) -> None:
        kind = "동영상" if self.capture.media_type == "video" else "사진"
        ret = QMessageBox.question(
            self, f"{kind} 삭제",
            f"이 {kind}({self.capture.captured_at})을(를) 삭제하시겠습니까?\n\n"
            "DB 기록과 저장된 파일(썸네일 포함)이 함께 지워지며 되돌릴 수 없습니다.\n"
            "(지정 폴더에 복사된 사본은 남겨 둡니다)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            self.done(self.Deleted)

"""썸네일 · 멀티 칸을 두 번 누르면 뜨는 큰 보기. 동영상은 OS 기본 플레이어로 재생.

사진은 Pro 검토와 같은 캔버스(ImageCanvas) 한 장. 돋보기 · 이동 · 맞춤 도구 줄은 없다 — 사진을 누르고 있는 동안
확대 렌즈, 마우스 휠 = 확대 · 축소, 사진을 두 번 누르면 창에 맞춤.
사진 왼쪽 위에는 멀티 칸과 같은 눈 · 촬영 시각 표시('OD  10-30 10:07').
captures 로 사진 목록을 주면 사진 양옆 ‹ › 버튼(←/→ 키)으로 앞뒤 사진을 넘겨 본다 — 촬영 순서(왼쪽 = 이전,
오른쪽 = 다음), 끝에서는 멈춤, 동영상은 건너뜀. 넘기면 ``capture`` 가 지금 보이는 사진으로 바뀌고 삭제 · 전송도 그 사진.

하단 '삭제' 버튼(Delete)을 누르고 확인하면 대화상자가 ``ImageViewerDialog.Deleted`` 코드로 닫히고,
실제 삭제(DB·파일)는 호출한 쪽(메인 창)이 수행한다.

after_capture=True: 사진을 찍은 직후 자동으로 뜨는 큰 화면. '다시 촬영'(Esc · Enter)을 누르면 닫히고 촬영 화면으로
돌아간다. capture_keys 로 준 촬영 단축키를 누르면 retakeRequested 가 나가 메인 창이 창을 닫고 초기화(기본 —
라이브 촬영 화면) 또는 바로 다시 찍는다 (설정 → 일반 '보기 · 멀티 화면에서 캡처', config.capture_in_view).
아래쪽 '전송' · '전송(삭제)' 를 누르면 sendRequested · sendDeleteRequested 가 나가 메인 창이 이 사진 1장을 보낸다
(전송(삭제)는 모두 보냈을 때만 확인 후 이 PC 에서 지우고 창을 닫음).
"""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from app.ui import icons
from app.ui.multi_view import eye_time
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
    retakeRequested = Signal()   # 촬영 뒤 보기 창에서 촬영 단축키를 누름 → 닫고 초기화 (설정에 따라 바로 다시 찍기)
    sendRequested = Signal()     # 촬영 뒤 보기 창의 '전송' — 이 사진 1장
    sendDeleteRequested = Signal()  # '전송(삭제)' — 보낸 뒤 이 PC 에서 삭제 (모두 보냈을 때만, 확인 후)
    dxClicked = Signal(str, str)  # 질환 아이콘 (source, 분류) — 메인 창이 이 사진과 그 질환 샘플을 멀티 화면에

    def __init__(self, capture, parent=None, allow_delete: bool = True, after_capture: bool = False,
                 capture_keys=(), dx_categories=None, dx_preview=None, captures=None):
        super().__init__(parent)
        self.capture = capture
        self.after_capture = bool(after_capture)
        self.b_retake = None
        self.b_send = None
        self.b_send_delete = None
        self.b_prev = None
        self.b_next = None
        self.lbl_pos = None
        self.lbl_sent = None
        self.dx_bar = None
        self._viewport = None
        self._sent: set = set()              # 이 창에서 보낸 사진 id ('✓ 보냄' 표시 — 넘겨 봐도 사진마다)
        # ‹ › 로 넘길 사진 (사진만 — 동영상은 외부 플레이어라 건너뜀). 지금 사진이 목록에 없으면 넘기기 없음
        photos = [c for c in (captures or ()) if getattr(c, "media_type", "photo") == "photo"]
        idx = next((i for i, c in enumerate(photos) if c.id == capture.id), -1)
        if idx >= 0 and capture.media_type == "photo":
            photos[idx] = capture            # 받은 기록 그대로 (같은 사진의 다른 사본이 아니라)
            self._seq, self._index = photos, idx
        else:
            self._seq, self._index = [capture], 0
        self._update_title()
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

        if dx_categories is not None:
            # 상단 메뉴와 같은 질환별 샘플 아이콘 (이름 · 마우스를 올리면 사진) — 누르면 이 사진과 그 질환 샘플을 멀티 화면에
            from app.ui.dx_bar import DxBar
            samples, library = dx_categories
            self.dx_bar = DxBar(self)
            self.dx_bar.preview_provider = dx_preview
            self.dx_bar.set_categories(samples, library)
            self.dx_bar.diseaseClicked.connect(self.dxClicked.emit)
            if self.dx_bar.entries():
                row = QHBoxLayout()
                row.setSpacing(6)
                lbl = QLabel("질환별 샘플과 비교")
                lbl.setObjectName("Muted")
                row.addWidget(lbl)
                row.addWidget(self.dx_bar)
                row.addStretch(1)
                lay.addLayout(row)

        # 사진 한 장 — 돋보기 · 이동 · 맞춤 도구 줄 없이: 누르고 있으면 확대 렌즈, 휠 = 확대 · 축소, 두 번 누르면 창에 맞춤
        self.canvas = ImageCanvas()
        self.canvas._placeholder = "이미지를 불러올 수 없습니다."
        self.canvas.set_tool("magnify")
        self._viewport = self.canvas.viewport()
        self._viewport.installEventFilter(self)
        # 양옆 ‹ › = 이전 · 다음 사진 (←/→ 키도 같음). 넘길 사진이 없으면 숨김
        self.b_prev = self._nav_button("chev-l", "이전 사진 (←)", -1)
        self.b_next = self._nav_button("chev-r", "다음 사진 (→)", 1)
        mid = QHBoxLayout()
        mid.setSpacing(6)
        mid.addWidget(self.b_prev)
        mid.addWidget(self.canvas, 1)
        mid.addWidget(self.b_next)
        lay.addLayout(mid, 1)
        for key, delta in ((Qt.Key.Key_Left, -1), (Qt.Key.Key_Right, 1)):
            QShortcut(QKeySequence(key), self, activated=lambda d=delta: self.step(d))
        lay.addLayout(self._footer(allow_delete))
        self._show_current()

    # ---- 사진 넘기기 ----
    def _nav_button(self, icon: str, tip: str, delta: int) -> QPushButton:
        b = QPushButton()
        b.setObjectName("ViewerNav")
        ic = icons.icon(icon, ICON, 34)
        blank = QPixmap(68, 68)
        blank.fill(Qt.GlobalColor.transparent)
        ic.addPixmap(blank, QIcon.Mode.Disabled)          # 처음 · 마지막 사진에서는 화살표를 지운다 (자리는 그대로)
        b.setIcon(ic)
        b.setIconSize(QSize(34, 34))
        b.setFixedWidth(56)
        b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)   # 사진 높이 전체 — 누르기 쉽게
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(lambda: self.step(delta))
        return b

    def photo_count(self) -> int:
        return len(self._seq)

    def current_index(self) -> int:
        return self._index

    def step(self, delta: int) -> bool:
        """앞(-1) · 뒤(+1) 사진으로. 끝이면 그대로 (False)."""
        i = self._index + delta
        if self.canvas is None or not 0 <= i < len(self._seq):
            return False
        self._index = i
        self.capture = self._seq[i]
        self._show_current()
        return True

    def _show_current(self) -> None:
        cap = self.capture
        self.canvas.set_image(_load_bgr(cap.file_path))
        self.canvas.reset_view()
        self.canvas.set_caption(eye_time(cap))           # 멀티 칸과 같은 'OD  10-30 10:07'
        self._update_title()
        n = len(self._seq)
        for b, ok in ((self.b_prev, self._index > 0), (self.b_next, self._index < n - 1)):
            b.setVisible(n > 1)
            b.setEnabled(ok)
        if self.lbl_pos is not None:
            self.lbl_pos.setVisible(n > 1)
            self.lbl_pos.setText(f"{self._index + 1} / {n}")
        if self.lbl_sent is not None:
            self.lbl_sent.setText("✓ 보냄" if cap.id in self._sent else "")

    def _update_title(self) -> None:
        when = self.capture.captured_at
        self.setWindowTitle(f"촬영한 사진 - {when}" if self.after_capture else f"보기 - {when}")

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() == QEvent.Type.MouseButtonDblClick and obj is self._viewport and self.canvas is not None:
            self.canvas.reset_view()                     # 두 번 누르면 창에 맞춤 ('맞춤' 버튼 대신)
            return True
        return super().eventFilter(obj, ev)

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
        self.lbl_pos = QLabel("")                    # 넘겨 보는 사진의 순서 '3 / 12' (넘길 사진이 있을 때만)
        self.lbl_pos.setObjectName("Muted")
        self.lbl_pos.setVisible(False)
        foot.addWidget(self.lbl_pos)
        foot.addStretch(1)
        if self.after_capture and self.capture.media_type != "video":
            # 촬영 직후 화면에서 바로 전송 (이 사진 1장 — 방법 · 형식은 설정 → 전송)
            self.b_send = QPushButton(icons.gap("전송"))
            self.b_send.setIcon(icons.icon("send", ICON, 18))
            self.b_send.setIconSize(QSize(18, 18))
            self.b_send.setMinimumHeight(40)
            self.b_send.setToolTip("이 사진을 폴더 · 클라우드 · PACS(DICOM) 로 보내기 (방법 · 형식은 설정 → 전송)")
            self.b_send.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.b_send.clicked.connect(lambda: self.sendRequested.emit())
            self.b_send_delete = QPushButton(icons.gap("전송(삭제)"))
            self.b_send_delete.setIcon(icons.icon("send", "#ef4f4c", 18))
            self.b_send_delete.setIconSize(QSize(18, 18))
            self.b_send_delete.setMinimumHeight(40)
            self.b_send_delete.setToolTip("이 사진을 보낸 뒤 이 PC 에서 삭제 — 모두 보냈을 때만, 확인 후 (창이 닫힙니다)")
            self.b_send_delete.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.b_send_delete.clicked.connect(lambda: self.sendDeleteRequested.emit())
            self.lbl_sent = QLabel("")
            self.lbl_sent.setObjectName("Muted")
            foot.addWidget(self.lbl_sent)
            foot.addWidget(self.b_send)
            foot.addWidget(self.b_send_delete)
            foot.addSpacing(12)
        if self.after_capture:
            # 촬영 직후 화면: 큰 버튼 하나로 촬영 화면 복귀 (Enter · Esc 도 같음)
            self.b_retake = QPushButton(icons.gap("다시 촬영"))
            self.b_retake.setObjectName("Primary")
            self.b_retake.setIcon(icons.icon("camera", ICON_ON_ACCENT, 18))
            self.b_retake.setIconSize(QSize(18, 18))
            self.b_retake.setMinimumHeight(40)
            self.b_retake.setToolTip("촬영 화면으로 돌아가기 (Enter · Esc) — 촬영 단축키 · USB 사진 버튼을 누르면 초기화 "
                                     "(설정 → 일반 '보기 · 멀티 화면에서 캡처' 에서 '바로 촬영' 으로 바꿀 수 있음)")
            self.b_retake.setDefault(True)
            self.b_retake.clicked.connect(self.accept)
            foot.addWidget(self.b_retake)
        else:
            b_close.setDefault(True)
        foot.addWidget(b_close)
        return foot

    def mark_sent(self) -> None:
        """지금 보이는 사진을 보냈다는 표시 (같은 사진을 두 번 보내지 않게 눈으로 확인 — 넘겨 봐도 사진마다 기억)."""
        self._sent.add(self.capture.id)
        if self.lbl_sent is not None:
            self.lbl_sent.setText("✓ 보냄")

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

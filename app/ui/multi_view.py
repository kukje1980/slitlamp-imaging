"""멀티 보기의 사진 격자 — 오른쪽 목록에서 고른 사진(+ 상단 질환 아이콘으로 고른 참조 사진)을 한 화면에 모은다.

1장=전체, 2장=좌우, 3~4장=2×2, 5~6장=3×2, 7~9장=3×3 … (고른 순서대로, 참조 사진은 뒤에).
칸마다 Pro 검토와 같은 편집 캔버스(ImageCanvas)이고, 누른 칸(노란 테두리)이 편집 대상이다.
보정 · 편집 · 분석 메뉴는 이 격자를 품은 Pro 작업공간(ProWorkspace 의 'grid' 모드)이 맡는다.
- 기본 도구는 돋보기: 사진을 누르고 있는 동안 확대 렌즈. 휠 = 확대 · 축소.
  휠로 확대해 사진이 칸보다 커지면 왼쪽 버튼으로 끌어 사진을 옮긴다 (렌즈는 맞춤 크기일 때).
- 칸을 두 번 누르면(돋보기 · 이동 도구일 때) 그 사진을 큰 화면으로 본다 (cellActivated).
composite() 는 편집 · 주석까지 반영해 한 장의 BGR 이미지로 만든다. 고른 사진이 바뀌어도 남아 있는 사진의 편집은 유지된다.
참조 사진(RefCapture)은 환자 기록의 사진이 아니라서 편집본 저장 · 측정 기록 · 코멘트에서는 빠진다.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from app.capture.image_process import Adjust
from app.ui.pro.image_canvas import ImageCanvas

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


@dataclass
class RefCapture:
    """참조 사진(등록한 참조 영상 · 샘플 사진)을 멀티 칸에 넣는 꼴 — Capture 와 같은 이름의 값만 가진다."""
    ref: object
    id: str
    file_path: str
    thumbnail_path: str | None = None
    media_type: str = "photo"
    eye: str = "NA"
    captured_at: str = ""
    note: str = ""
    is_reference: bool = field(default=True, init=False)

    @property
    def label(self) -> str:
        r = self.ref
        return " · ".join(x for x in (getattr(r, "category", None), getattr(r, "title", None)) if x) or "참조"


def ref_key(ref) -> str:
    """참조 사진 하나를 가리키는 글자 (등록한 참조 = DB 번호, 샘플 = 원본 사진 번호)."""
    num = ref.id if ref.id is not None else ref.source_capture_id
    return f"ref:{ref.source}:{num}:{ref.file_path}"


def ref_capture(ref) -> RefCapture:
    return RefCapture(ref=ref, id=ref_key(ref), file_path=ref.file_path, thumbnail_path=ref.thumbnail_path)


def is_reference(cap) -> bool:
    return bool(getattr(cap, "is_reference", False))


def grid_shape(n: int) -> tuple[int, int]:
    """사진 수 → (열, 행)."""
    if n <= 1:
        return 1, 1
    if n == 2:
        return 2, 1
    cols = math.ceil(math.sqrt(n))
    return cols, math.ceil(n / cols)


def _load_bgr(cap):
    path = cap.file_path if cap.media_type == "photo" else (cap.thumbnail_path or "")
    if not path or cv2 is None or not os.path.exists(path):
        return None
    data = np.fromfile(path, dtype=np.uint8)          # 한글 경로 대응
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _short(text: str, n: int = 30) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _tag(i: int, cap) -> str:
    if is_reference(cap):
        return f"{i + 1}  참조 · {_short(cap.label)}"
    eye = cap.eye if cap.eye in ("OD", "OS", "OU") else ""
    return f"{i + 1}" + (f"  {eye}" if eye else "") + (f"  {cap.captured_at[5:16]}" if cap.captured_at else "")


class _Cell(QFrame):
    """사진 한 칸 — 편집 캔버스 + 지금 편집 대상이면 노란 테두리."""

    def __init__(self, cap, img, parent=None):
        super().__init__(parent)
        self.setObjectName("MultiCell")
        self.cap = cap
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        self.canvas = ImageCanvas()
        self.canvas.setMinimumSize(60, 45)             # 칸이 많아도 창 최소 크기가 커지지 않게
        self.canvas._placeholder = "사진을 불러오지 못했습니다"
        self.canvas.set_image(img)
        lay.addWidget(self.canvas)
        self.set_active(False)

    def set_active(self, on: bool) -> None:
        if self.property("active") != on:
            self.setProperty("active", on)
            self.style().unpolish(self)
            self.style().polish(self)


class MultiView(QWidget):
    cellActivated = Signal(object)        # Capture | RefCapture — 칸을 두 번 누름 (큰 화면으로)
    activeChanged = Signal(object)        # ImageCanvas | None — 편집 대상 칸이 바뀜
    edited = Signal(object)               # ImageCanvas — 그 칸의 편집 상태가 바뀜
    calibrated = Signal(float)            # 칸에서 눈금 보정 (px/mm)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MultiView")
        self.setMinimumSize(320, 240)
        self._cells: list[_Cell] = []
        self._active: _Cell | None = None
        self._tool = "magnify"
        self.px_per_mm = 0.0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("MultiGrid")
        self.grid_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        lay.addWidget(self.grid_host, 1)
        self.lbl_empty = QLabel("오른쪽 목록에서 사진을 한 번씩 눌러 고르거나 위 질환 아이콘으로 참조 사진을 고르세요")
        self.lbl_empty.setObjectName("MultiEmpty")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setWordWrap(True)
        self.grid.addWidget(self.lbl_empty, 0, 0)

    # ---------------- 사진 ----------------
    def set_captures(self, caps: list) -> None:
        """고른 사진으로 칸을 다시 놓는다. 이미 있던 사진의 칸(편집 · 확대 상태)은 그대로 쓴다."""
        old = {c.cap.id: c for c in self._cells}
        cells = []
        for cap in caps:
            cell = old.pop(cap.id, None)
            if cell is None:
                cell = _Cell(cap, _load_bgr(cap))
                cv = cell.canvas
                cv.set_tool(self._tool)
                cv.set_px_per_mm(self.px_per_mm)
                cv.activated.connect(lambda c=cell: self.set_active(c))
                cv.edited.connect(lambda cv=cv: self.edited.emit(cv))
                cv.calibrated.connect(self.calibrated.emit)
                cv.viewport().installEventFilter(self)
            else:
                cell.cap = cap                         # 코멘트 등이 바뀐 새 기록으로
            cells.append(cell)
        for cell in old.values():                     # 빠진 사진
            self.grid.removeWidget(cell)
            cell.hide()
            cell.setParent(None)
            cell.deleteLater()
        for cell in self._cells:
            self.grid.removeWidget(cell)
        self._cells = cells
        cols, _rows = grid_shape(len(cells))
        self.lbl_empty.setVisible(not cells)
        for i, cell in enumerate(cells):
            cell.canvas.set_caption(_tag(i, cell.cap))
            self.grid.addWidget(cell, i // cols, i % cols)
            cell.show()
        rows = math.ceil(len(cells) / cols) if cells else 1
        for c in range(max(cols, self.grid.columnCount())):
            self.grid.setColumnStretch(c, 1 if c < cols else 0)
        for r in range(max(rows, self.grid.rowCount())):
            self.grid.setRowStretch(r, 1 if r < rows else 0)
        active = self._active if self._active in cells else (cells[0] if cells else None)
        self.set_active(active, force=True)

    def clear(self) -> None:
        self.set_captures([])

    def captures(self) -> list:
        return [c.cap for c in self._cells]

    def count(self) -> int:
        return len(self._cells)

    def canvases(self) -> list:
        return [c.canvas for c in self._cells]

    def canvas_for(self, cap_id):
        return next((c.canvas for c in self._cells if c.cap.id == cap_id), None)

    def index_of(self, canvas) -> int:
        return next((i for i, c in enumerate(self._cells) if c.canvas is canvas), -1)

    # ---------------- 편집 대상 · 도구 ----------------
    def set_active(self, cell, force: bool = False) -> None:
        if isinstance(cell, ImageCanvas):
            cell = next((c for c in self._cells if c.canvas is cell), None)
        changed = cell is not self._active
        self._active = cell
        for c in self._cells:
            c.set_active(c is cell and len(self._cells) > 1)
        if changed or force:
            self.activeChanged.emit(cell.canvas if cell is not None else None)

    def active_canvas(self):
        return self._active.canvas if self._active is not None else None

    def active_capture(self):
        return self._active.cap if self._active is not None else None

    def tool(self) -> str:
        return self._tool

    def set_tool(self, tool: str) -> None:
        self._tool = tool or "magnify"
        for c in self._cells:
            c.canvas.set_tool(self._tool)

    def set_px_per_mm(self, value: float) -> None:
        self.px_per_mm = float(value or 0.0)
        for c in self._cells:
            c.canvas.set_px_per_mm(self.px_per_mm)

    def fit_all(self) -> None:
        for c in self._cells:
            c.canvas.reset_view()

    def modified(self) -> list:
        """편집 · 보정 · 주석이 있는 칸: [(Capture | RefCapture, ImageCanvas)]."""
        return [(c.cap, c.canvas) for c in self._cells if c.canvas.image() is not None and c.canvas.is_modified()]

    def reset_edits(self, cap_ids=None) -> None:
        """편집을 지우고 원본으로 (편집본을 저장한 뒤 — 같은 편집을 두 번 저장하지 않게)."""
        for c in self._cells:
            if cap_ids is None or c.cap.id in cap_ids:
                c.canvas.set_image(c.canvas.original())
                c.canvas.set_adjust(Adjust())
                self.edited.emit(c.canvas)

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() == QEvent.Type.MouseButtonDblClick and self._tool in ("magnify", "pan"):
            cell = next((c for c in self._cells if c.canvas.viewport() is obj), None)
            if cell is not None:
                self.cellActivated.emit(cell.cap)
                return True
        return super().eventFilter(obj, ev)

    # ---------------- 저장용 합성 ----------------
    def composite(self):
        """보이는 격자를 편집 · 보정 · 주석까지 반영해 한 장의 BGR 이미지로."""
        imgs = [c.canvas.render_to_bgr() for c in self._cells]
        real = [im for im in imgs if im is not None]
        if not real or cv2 is None:
            return None
        cols, rows = grid_shape(len(imgs))
        ref_h = max(im.shape[0] for im in real)
        ref_w = max(im.shape[1] for im in real)
        scale = min(1.0, 1600 / max(ref_w, 1))        # 칸 하나 최대 가로 1600px
        cw, ch = max(1, int(ref_w * scale)), max(1, int(ref_h * scale))
        gap = 8
        canvas = np.zeros((rows * ch + (rows - 1) * gap, cols * cw + (cols - 1) * gap, 3), dtype=np.uint8)
        for i, im in enumerate(imgs):
            if im is None:
                continue
            r, c = divmod(i, cols)
            s = min(cw / im.shape[1], ch / im.shape[0])
            w, h = max(1, int(im.shape[1] * s)), max(1, int(im.shape[0] * s))
            x0 = c * (cw + gap) + (cw - w) // 2
            y0 = r * (ch + gap) + (ch - h) // 2
            canvas[y0:y0 + h, x0:x0 + w] = cv2.resize(im, (w, h), interpolation=cv2.INTER_AREA)
            cap = self._cells[i].cap
            if is_reference(cap):
                label = f"{i + 1} REF"                 # 글꼴(Hershey)이 한글을 못 써서 영문 표시
            else:
                eye = cap.eye if cap.eye in ("OD", "OS", "OU") else ""
                label = f"{i + 1}" + (f" {eye}" if eye else "")
            fs = max(0.8, ch / 500)
            cv2.putText(canvas, label, (c * (cw + gap) + 14, r * (ch + gap) + int(36 * fs)),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), int(5 * fs), cv2.LINE_AA)
            cv2.putText(canvas, label, (c * (cw + gap) + 14, r * (ch + gap) + int(36 * fs)),
                        cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), max(1, int(2 * fs)), cv2.LINE_AA)
        return canvas

"""Pro 모드 이미지 캔버스 (보기 + 편집).

- 휠 확대/축소, 드래그 이동(Pan), 화면 맞춤(Fit), 돋보기(누르는 동안 확대 렌즈)
  돋보기 도구에서도 사진이 창보다 크게 확대돼 있으면 왼쪽 버튼으로 끌어 사진을 옮긴다 (손 모양 커서) —
  창에 맞는 크기(맞춤 · 축소)일 때만 누르는 동안 확대 렌즈
- 이미지 보정(Adjust) 적용 — 원본(_orig)은 보존, 편집 결과는 _base(기하) + _adj(보정) + _ann(주석)
- 기하 편집: 90° 회전, 좌우/상하 반전, 자르기 — 주석·측정 좌표도 함께 변환
- 주석: 텍스트, 화살표, 원 / 측정: 길이(px 또는 보정된 mm), 각도(3점)
- 눈금 보정: 실제 길이를 아는 선을 그어 px/mm 산출 (calibrated 시그널)
- 실행 취소 / 다시 실행(최대 20단계), 원본 복원
- render_to_bgr(): 보정 + 주석·측정을 편집 이미지 해상도로 합성 (편집본 저장·인쇄용)
- link(other): 2장 비교 시 확대·스크롤 동기화
- set_overlay(): 표시 전용 겹침 레이어 (포커스 피킹·이전 영상 고스트·분석 마스크) — 저장본에는 안 들어감
- set_hud(): 영상 위 상태 칩·관심 영역(ROI) 테두리 (스마트 캡처)
"""
from __future__ import annotations

import copy
import math
import time

import numpy as np
from PySide6.QtCore import QLineF, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QInputDialog,
)

from app.capture import image_process as ip
from app.capture.frame_utils import bgr_to_qimage
from app.capture.image_process import Adjust, apply_adjust

ANN_COLOR = QColor("#facc15")    # 주석: 노랑
MEAS_COLOR = QColor("#22d3ee")   # 측정: 시안
HISTORY_MAX = 20
MEASURE_KINDS = ("length", "angle")
_DRAG_TOOLS = {"arrow": "arrow", "circle": "circle", "length": "length", "crop": "crop", "calibrate": "length"}


def qimage_to_bgr(qimg: QImage) -> np.ndarray:
    qimg = qimg.convertToFormat(QImage.Format.Format_RGB888)
    w, h, bpl = qimg.width(), qimg.height(), qimg.bytesPerLine()
    buf = np.frombuffer(qimg.constBits(), dtype=np.uint8, count=bpl * h)
    rgb = buf.reshape(h, bpl)[:, : w * 3].reshape(h, w, 3)
    return rgb[..., ::-1].copy()


def length_px(a: dict) -> float:
    return math.hypot(a["x2"] - a["x1"], a["y2"] - a["y1"])


def angle_deg(a: dict) -> float:
    """(x2,y2)가 꼭짓점인 각도(0~180°)."""
    v1 = (a["x1"] - a["x2"], a["y1"] - a["y2"])
    v2 = (a["x3"] - a["x2"], a["y3"] - a["y2"])
    n = math.hypot(*v1) * math.hypot(*v2)
    if n == 0:
        return 0.0
    c = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / n))
    return math.degrees(math.acos(c))


def _map_points(ann: list, f) -> list:
    out = []
    for a in ann:
        b = dict(a)
        for kx, ky in (("x", "y"), ("x1", "y1"), ("x2", "y2"), ("x3", "y3")):
            if kx in b:
                b[kx], b[ky] = f(b[kx], b[ky])
        out.append(b)
    return out


class ImageCanvas(QGraphicsView):
    zoomed = Signal(float)
    edited = Signal()            # 보정·기하·주석 변경 → 실행 취소 상태·히스토그램 갱신
    activated = Signal()         # 사용자가 이 캔버스를 조작 (비교 모드 편집 대상)
    calibrated = Signal(float)   # 눈금 보정 결과 (px / mm)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pix = QGraphicsPixmapItem()
        self._pix.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._pix.setZValue(-2)
        self._scene.addItem(self._pix)
        self._overlay = QGraphicsPixmapItem()          # 주석보다 아래, 영상보다 위
        self._overlay.setZValue(-1)
        self._overlay.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._overlay.setVisible(False)
        self._scene.addItem(self._overlay)
        self._ov_shape: tuple = ()
        self._hud: list = []                           # [(text, color)]
        self._hud_roi: QRectF | None = None
        self._ana = QGraphicsPixmapItem()        # 분석 결과 오버레이 (영상 위, 주석 아래)
        self._ana.setZValue(1)
        self._scene.addItem(self._ana)
        self._ana_shape = None
        self._orig: np.ndarray | None = None
        self._base: np.ndarray | None = None
        self._proc: np.ndarray | None = None
        self._adj = Adjust()
        self._ann: list = []
        self._ov: list = []          # 주석 그래픽 아이템
        self._temp: list = []        # 그리는 중 미리보기 아이템
        self._undo: list = []
        self._redo: list = []
        self._last_adj_push = 0.0
        self._tool = "pan"
        self._drag: QPointF | None = None
        self._angle_pts: list = []
        self._lens: QPointF | None = None
        self._pan_from: QPoint | None = None   # 돋보기 도구에서 확대된 사진을 끌어 옮기는 중 (마지막 마우스 위치)
        self.lens_zoom = 2.5
        self.lens_radius = 90
        self.px_per_mm = 0.0
        self._auto_fit = True
        self._caption = ""
        self._placeholder = "영상 없음"

        self.setBackgroundBrush(QColor("#000000"))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setMinimumSize(320, 240)
        # 확대 · 축소 · 창 크기 · 사진이 바뀌어 옮길 수 있는지가 달라지면 커서(손 ↔ 십자)를 바꾼다
        self.horizontalScrollBar().rangeChanged.connect(self._update_cursor)
        self.verticalScrollBar().rangeChanged.connect(self._update_cursor)

    # ================= 이미지 / 상태 =================
    def set_image(self, frame: np.ndarray | None, keep_view: bool = False,
                  keep_annotations: bool = False) -> None:
        self.set_analysis_overlay(None)
        first = self._base is None
        self._orig = frame
        self._base = frame
        if not keep_annotations:
            self._ann = []
            self._undo.clear()
            self._redo.clear()
        panning = self._pan_from if keep_view and frame is not None else None
        self._cancel_temp()
        self._pan_from = panning                       # 라이브처럼 같은 보기로 새 프레임만 바뀌면 끌던 이동은 이어서
        self._refresh()
        self._update_cursor()
        if frame is not None and (not keep_view or first):
            self._auto_fit = True
            self.fit()

    def image(self) -> np.ndarray | None:
        """현재 편집 이미지(회전·자르기 반영, 보정 전)."""
        return self._base

    def original(self) -> np.ndarray | None:
        return self._orig

    def processed(self) -> np.ndarray | None:
        """보정까지 반영된 표시 이미지(주석 제외)."""
        return self._proc

    def adjust(self) -> Adjust:
        return self._adj.copy()

    def annotations(self) -> list:
        return copy.deepcopy(self._ann)

    def geometry_changed(self) -> bool:
        return self._base is not self._orig

    def is_modified(self) -> bool:
        return self.geometry_changed() or not self._adj.is_identity() or bool(self._ann)

    def _refresh(self) -> None:
        if self._base is None:
            self._proc = None
            self._pix.setPixmap(QPixmap())
            self._scene.setSceneRect(QRectF(0, 0, 1, 1))
        else:
            self._proc = apply_adjust(self._base, self._adj)
            self._pix.setPixmap(QPixmap.fromImage(bgr_to_qimage(self._proc)))
            h, w = self._base.shape[:2]
            self._scene.setSceneRect(QRectF(0, 0, w, h))
        if self._base is None or self._base.shape[:2] != self._ov_shape:
            self.set_overlay(None)                     # 다른 크기의 영상이면 겹침 레이어는 맞지 않음
        self._rebuild_overlay()
        self.viewport().update()

    # ================= 실행 취소 =================
    def _snap(self):
        return (self._base, self._adj.copy(), copy.deepcopy(self._ann))

    def _push(self) -> None:
        self._undo.append(self._snap())
        if len(self._undo) > HISTORY_MAX:
            self._undo.pop(0)
        self._redo.clear()

    def _restore(self, snap) -> None:
        self.set_analysis_overlay(None)
        geom_changed = snap[0] is not self._base
        self._base, adj, ann = snap
        self._adj = adj.copy()
        self._ann = ann
        self._refresh()
        if geom_changed:
            self.reset_view()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snap())
        self._restore(self._undo.pop())
        self._last_adj_push = 0.0
        self.edited.emit()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snap())
        self._restore(self._redo.pop())
        self._last_adj_push = 0.0
        self.edited.emit()
        return True

    def restore_original(self) -> bool:
        self.set_analysis_overlay(None)
        if self._base is None or not self.is_modified():
            return False
        self._push()
        geom = self.geometry_changed()
        self._base, self._adj, self._ann = self._orig, Adjust(), []
        self._refresh()
        if geom:
            self.reset_view()
        self.edited.emit()
        return True

    # ================= 보정 =================
    def set_adjust(self, adj: Adjust, record: bool = False) -> None:
        if record and self._base is not None and adj != self._adj:
            now = time.monotonic()
            if now - self._last_adj_push > 1.0:      # 슬라이더 드래그는 한 단계로 묶음
                self._push()
            self._last_adj_push = now
        changed = adj != self._adj
        self._adj = adj.copy()
        if changed or self._proc is None:
            self._refresh()
        if record and changed:
            self.edited.emit()

    # ================= 기하 편집 =================
    def rotate(self, k: int) -> None:
        """k=1 시계 방향 90°, k=-1 반시계 90°."""
        self.set_analysis_overlay(None)
        if self._base is None:
            return
        h, w = self._base.shape[:2]
        self._push()
        self._base = ip.rotate90(self._base, k)
        self.set_overlay(None)
        if k == 1:
            self._ann = _map_points(self._ann, lambda x, y: (h - y, x))
        else:
            self._ann = _map_points(self._ann, lambda x, y: (y, w - x))
        self._refresh()
        self.reset_view()
        self.edited.emit()

    def flip(self, horizontal: bool) -> None:
        self.set_analysis_overlay(None)
        if self._base is None:
            return
        h, w = self._base.shape[:2]
        self._push()
        self._base = ip.flip(self._base, horizontal)
        self.set_overlay(None)
        f = (lambda x, y: (w - x, y)) if horizontal else (lambda x, y: (x, h - y))
        self._ann = _map_points(self._ann, f)
        self._refresh()
        self.edited.emit()

    def crop_to(self, x0: float, y0: float, x1: float, y1: float) -> bool:
        self.set_analysis_overlay(None)
        if self._base is None:
            return False
        h, w = self._base.shape[:2]
        xa, xb = sorted((max(0, min(w, round(x0))), max(0, min(w, round(x1)))))
        ya, yb = sorted((max(0, min(h, round(y0))), max(0, min(h, round(y1)))))
        if xb - xa < 8 or yb - ya < 8:
            return False
        self._push()
        self._base = ip.crop(self._base, xa, ya, xb, yb)
        self.set_overlay(None)
        self._ann = _map_points(self._ann, lambda x, y: (x - xa, y - ya))
        self._refresh()
        self.reset_view()
        self.edited.emit()
        return True

    # ================= 보기 =================
    def fit(self) -> None:
        if self._base is not None:
            self.fitInView(self._pix, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._auto_fit:
            self.fit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._base is None:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self._auto_fit = False
        self.scale(factor, factor)
        self.zoomed.emit(factor)

    def apply_zoom(self, factor: float) -> None:
        self._auto_fit = False
        self.scale(factor, factor)

    def zoom_by(self, factor: float) -> None:
        self.apply_zoom(factor)
        self.zoomed.emit(factor)

    def reset_view(self) -> None:
        self._auto_fit = True
        self.resetTransform()
        self.fit()

    def link(self, other: "ImageCanvas") -> None:
        """두 캔버스의 확대·스크롤 동기화 (2장 비교)."""
        self.zoomed.connect(other.apply_zoom)
        other.zoomed.connect(self.apply_zoom)
        self.horizontalScrollBar().valueChanged.connect(other.horizontalScrollBar().setValue)
        other.horizontalScrollBar().valueChanged.connect(self.horizontalScrollBar().setValue)
        self.verticalScrollBar().valueChanged.connect(other.verticalScrollBar().setValue)
        other.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)

    # ================= 겹침 레이어 · HUD =================
    def set_overlay(self, img: QImage | None, opacity: float = 1.0) -> None:
        """영상 위 표시 전용 레이어. 크기가 달라도 영상 크기에 맞춰 늘려 그린다 (None = 끄기)."""
        if img is None or img.isNull() or self._base is None:
            self._ov_shape = ()
            self._overlay.setVisible(False)
            self._overlay.setPixmap(QPixmap())
            return
        h, w = self._base.shape[:2]
        self._ov_shape = (h, w)
        self._overlay.setPixmap(QPixmap.fromImage(img))
        self._overlay.setTransform(QTransform.fromScale(w / max(1, img.width()), h / max(1, img.height())))
        self._overlay.setOpacity(max(0.0, min(1.0, opacity)))
        self._overlay.setVisible(True)

    def set_overlay_opacity(self, opacity: float) -> None:
        self._overlay.setOpacity(max(0.0, min(1.0, opacity)))

    def has_overlay(self) -> bool:
        return self._overlay.isVisible()

    def set_hud(self, lines: list | None = None, roi: QRectF | None = None) -> None:
        """우측 상단 상태 칩 [(text, color)] 과 ROI 테두리 (이미지 좌표)."""
        self._hud = list(lines or [])
        self._hud_roi = roi
        self.viewport().update()

    def set_caption(self, text: str) -> None:
        self._caption = text
        self.viewport().update()

    def set_px_per_mm(self, value: float) -> None:
        self.px_per_mm = max(0.0, float(value or 0.0))
        self._rebuild_overlay()

    # ================= 주석 · 측정 =================
    def _scale(self) -> float:
        if self._base is None:
            return 1.0
        return max(1.0, max(self._base.shape[:2]) / 900.0)

    def length_label(self, a: dict) -> str:
        L = length_px(a)
        return f"{L / self.px_per_mm:.2f} mm" if self.px_per_mm > 0 else f"{L:.0f} px"

    def measurements(self) -> list:
        """[(종류, 값 텍스트), ...]"""
        out = []
        for a in self._ann:
            if a["kind"] == "length":
                out.append(("length", self.length_label(a)))
            elif a["kind"] == "angle":
                out.append(("angle", f"{angle_deg(a):.1f}°"))
        return out

    def _label(self, text: str, x: float, y: float, color: QColor, px: float) -> QGraphicsSimpleTextItem:
        item = QGraphicsSimpleTextItem(text)
        f = QFont()
        f.setPixelSize(int(px))
        f.setBold(True)
        item.setFont(f)
        item.setBrush(QBrush(color))
        outline = QPen(QColor("#000000"))
        outline.setWidthF(1.2 * self._scale())
        item.setPen(outline)
        item.setPos(x, y)
        self._scene.addItem(item)
        return item

    def _boxed_label(self, text: str, x: float, y: float, color: QColor, px: float) -> list:
        """측정값 라벨 — 반투명 어두운 상자 위에 표시해 어떤 배경에서도 읽히게."""
        item = self._label(text, x, y, color, px)
        pad = 4 * self._scale()
        r = item.boundingRect().adjusted(-pad, -pad / 2, pad, pad / 2).translated(x, y)
        box = self._scene.addRect(r, QPen(Qt.PenStyle.NoPen), QBrush(QColor(0, 0, 0, 165)))
        box.setZValue(1)                       # 영상(z=0) 위, 글자(z=2) 아래
        item.setZValue(2)
        return [box, item]

    def _make_items(self, a: dict, temp: bool = False) -> list:
        s = self._scale()
        kind = a["kind"]
        color = MEAS_COLOR if kind in MEASURE_KINDS or kind == "poly" else ANN_COLOR
        pen = QPen(color)
        pen.setWidthF((2.5 if color == MEAS_COLOR else 3.0) * s)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if temp:
            pen.setStyle(Qt.PenStyle.DashLine)
        items = []
        if kind == "text":
            items.append(self._label(a["text"], a["x"], a["y"], ANN_COLOR, 26 * s))
        elif kind == "arrow":
            p1, p2 = QPointF(a["x1"], a["y1"]), QPointF(a["x2"], a["y2"])
            items.append(self._scene.addLine(QLineF(p1, p2), pen))
            ang = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
            L = 18 * s
            h1 = QPointF(p2.x() - L * math.cos(ang - math.pi / 7), p2.y() - L * math.sin(ang - math.pi / 7))
            h2 = QPointF(p2.x() - L * math.cos(ang + math.pi / 7), p2.y() - L * math.sin(ang + math.pi / 7))
            solid = QPen(pen)
            solid.setStyle(Qt.PenStyle.SolidLine)
            items.append(self._scene.addPolygon(QPolygonF([p2, h1, h2]), solid, QBrush(color)))
        elif kind == "circle":
            r = QRectF(QPointF(min(a["x1"], a["x2"]), min(a["y1"], a["y2"])),
                       QPointF(max(a["x1"], a["x2"]), max(a["y1"], a["y2"])))
            items.append(self._scene.addEllipse(r, pen))
        elif kind == "length":
            p1, p2 = QPointF(a["x1"], a["y1"]), QPointF(a["x2"], a["y2"])
            items.append(self._scene.addLine(QLineF(p1, p2), pen))
            ang = math.atan2(p2.y() - p1.y(), p2.x() - p1.x()) + math.pi / 2
            t = 8 * s
            for p in (p1, p2):
                d = QPointF(t * math.cos(ang), t * math.sin(ang))
                items.append(self._scene.addLine(QLineF(p - d, p + d), pen))
            if not temp or length_px(a) > 4:
                mx, my = (p1.x() + p2.x()) / 2, (p1.y() + p2.y()) / 2
                items.extend(self._boxed_label(self.length_label(a), mx + 8 * s, my + 6 * s, MEAS_COLOR, 20 * s))
        elif kind == "angle":
            v = QPointF(a["x2"], a["y2"])
            items.append(self._scene.addLine(QLineF(QPointF(a["x1"], a["y1"]), v), pen))
            items.append(self._scene.addLine(QLineF(v, QPointF(a["x3"], a["y3"])), pen))
            # 라벨은 두 변의 이등분선 방향(각의 안쪽)에 배치
            u1 = QPointF(a["x1"] - v.x(), a["y1"] - v.y())
            u2 = QPointF(a["x3"] - v.x(), a["y3"] - v.y())
            n1, n2 = math.hypot(u1.x(), u1.y()) or 1, math.hypot(u2.x(), u2.y()) or 1
            bx, by = u1.x() / n1 + u2.x() / n2, u1.y() / n1 + u2.y() / n2
            nb = math.hypot(bx, by) or 1
            d = 34 * s
            items.extend(self._boxed_label(f"{angle_deg(a):.1f}°", v.x() + bx / nb * d - 18 * s,
                                           v.y() + by / nb * d - 10 * s, MEAS_COLOR, 20 * s))
        elif kind == "poly":
            pts = a["pts"]
            for p, q in zip(pts, pts[1:]):
                items.append(self._scene.addLine(QLineF(QPointF(*p), QPointF(*q)), pen))
        elif kind == "crop":
            cp = QPen(QColor("#ffffff"))
            cp.setWidthF(2 * s)
            cp.setStyle(Qt.PenStyle.DashLine)
            r = QRectF(QPointF(min(a["x1"], a["x2"]), min(a["y1"], a["y2"])),
                       QPointF(max(a["x1"], a["x2"]), max(a["y1"], a["y2"])))
            items.append(self._scene.addRect(r, cp, QBrush(QColor(255, 255, 255, 30))))
        return items

    def _remove(self, items: list) -> None:
        for it in items:
            if it.scene() is not None:
                self._scene.removeItem(it)

    def _rebuild_overlay(self) -> None:
        self._remove(self._ov)
        self._ov = []
        if self._base is None:
            return
        for a in self._ann:
            self._ov.extend(self._make_items(a))
        for it in self._ov:
            it.setZValue(2)

    def _add_ann(self, a: dict) -> None:
        self._push()
        self._ann.append(a)
        self._rebuild_overlay()
        self.edited.emit()

    # ================= 분석 오버레이 =================
    def set_analysis_overlay(self, bgra: np.ndarray | None) -> None:
        """분석 결과(BGRA, 편집 이미지와 같은 크기)를 영상 위에 겹쳐 표시. None = 지움.

        편집본 저장·인쇄(render_to_bgr)에도 함께 그려진다.
        """
        if bgra is None or self._base is None or bgra.shape[:2] != self._base.shape[:2]:
            self._ana.setPixmap(QPixmap())
            self._ana_shape = None
            return
        h, w = bgra.shape[:2]
        buf = np.ascontiguousarray(bgra)
        self._ana.setPixmap(QPixmap.fromImage(QImage(buf.data, w, h, 4 * w, QImage.Format.Format_ARGB32).copy()))
        self._ana_shape = (h, w)
        self.viewport().update()

    def has_analysis_overlay(self) -> bool:
        return self._ana_shape is not None

    def add_text(self, x: float, y: float, text: str) -> None:
        self._add_ann({"kind": "text", "x": x, "y": y, "text": text})

    def add_arrow(self, p1: QPointF, p2: QPointF) -> None:
        self._add_ann({"kind": "arrow", "x1": p1.x(), "y1": p1.y(), "x2": p2.x(), "y2": p2.y()})

    def add_circle(self, p1: QPointF, p2: QPointF) -> None:
        self._add_ann({"kind": "circle", "x1": p1.x(), "y1": p1.y(), "x2": p2.x(), "y2": p2.y()})

    def add_length(self, p1: QPointF, p2: QPointF) -> None:
        self._add_ann({"kind": "length", "x1": p1.x(), "y1": p1.y(), "x2": p2.x(), "y2": p2.y()})

    def add_angle(self, a: QPointF, vertex: QPointF, c: QPointF) -> None:
        self._add_ann({"kind": "angle", "x1": a.x(), "y1": a.y(), "x2": vertex.x(), "y2": vertex.y(),
                       "x3": c.x(), "y3": c.y()})

    def clear_annotations(self) -> None:
        if not self._ann:
            return
        self._push()
        self._ann = []
        self._rebuild_overlay()
        self.edited.emit()

    def annotation_kinds(self) -> list:
        return [a["kind"] for a in self._ann]

    def has_annotations(self) -> bool:
        return bool(self._ann)

    # ================= 도구 · 마우스 =================
    def tool(self) -> str:
        return self._tool

    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self._cancel_temp()
        if tool == "pan":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self._update_cursor()

    def can_pan(self) -> bool:
        """사진이 창보다 크게 확대돼 있어 끌어서 옮길 수 있는지 (스크롤할 곳이 있음)."""
        if self._base is None:
            return False
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        return h.maximum() > h.minimum() or v.maximum() > v.minimum()

    def _update_cursor(self, *_args) -> None:
        """돋보기 도구: 확대돼 옮길 수 있으면 손(끌어서 이동), 아니면 십자(누르면 확대 렌즈)."""
        if self._tool == "pan":
            return
        if self._pan_from is not None:
            shape = Qt.CursorShape.ClosedHandCursor
        elif self._tool == "magnify" and self.can_pan():
            shape = Qt.CursorShape.OpenHandCursor
        else:
            shape = Qt.CursorShape.CrossCursor
        self.viewport().setCursor(shape)

    def _cancel_temp(self) -> None:
        self._remove(self._temp)
        self._temp = []
        self._drag = None
        self._angle_pts = []
        self._lens = None
        if self._pan_from is not None:
            self._pan_from = None
            self._update_cursor()

    def _show_temp(self, a: dict) -> None:
        self._remove(self._temp)
        self._temp = self._make_items(a, temp=True)
        for it in self._temp:
            it.setZValue(2)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._base is None:
            super().mousePressEvent(event)
            return
        self.activated.emit()
        if event.button() != Qt.MouseButton.LeftButton or self._tool == "pan":
            super().mousePressEvent(event)
            return
        pos = self.mapToScene(event.position().toPoint())
        if self._tool == "text":
            text, ok = QInputDialog.getText(self, "텍스트 주석", "내용:")
            if ok and text.strip():
                self.add_text(pos.x(), pos.y(), text.strip())
        elif self._tool in _DRAG_TOOLS:
            self._drag = pos
        elif self._tool == "angle":
            self._angle_pts.append(pos)
            if len(self._angle_pts) == 3:
                a, v, c = self._angle_pts
                self._cancel_temp()
                self.add_angle(a, v, c)
        elif self._tool == "magnify":
            if self.can_pan():                         # 확대된 사진: 왼쪽 버튼으로 끌어 옮기기
                self._pan_from = event.position().toPoint()
                self._update_cursor()
            else:                                      # 맞춤 크기: 누르는 동안 확대 렌즈
                self._lens = event.position()
                self.viewport().update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pan_from is not None and not event.buttons() & Qt.MouseButton.LeftButton:
            self._pan_from = None                      # 놓는 순간을 못 받았으면 (창 밖에서 놓음 등) 이동 끝
            self._update_cursor()
        if self._pan_from is not None:
            pos = event.position().toPoint()
            delta = pos - self._pan_from
            self._pan_from = pos
            h, v = self.horizontalScrollBar(), self.verticalScrollBar()
            h.setValue(h.value() + (delta.x() if self.isRightToLeft() else -delta.x()))
            v.setValue(v.value() - delta.y())
            return
        if self._tool == "magnify" and self._lens is not None:
            self._lens = event.position()
            self.viewport().update()
            return
        pos = self.mapToScene(event.position().toPoint())
        if self._drag is not None and self._tool in _DRAG_TOOLS:
            self._show_temp({"kind": _DRAG_TOOLS[self._tool], "x1": self._drag.x(), "y1": self._drag.y(),
                             "x2": pos.x(), "y2": pos.y()})
            return
        if self._tool == "angle" and self._angle_pts:
            pts = [(p.x(), p.y()) for p in self._angle_pts] + [(pos.x(), pos.y())]
            if len(pts) == 3:
                self._show_temp({"kind": "angle", "x1": pts[0][0], "y1": pts[0][1], "x2": pts[1][0],
                                 "y2": pts[1][1], "x3": pts[2][0], "y3": pts[2][1]})
            else:
                self._show_temp({"kind": "poly", "pts": pts})
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._pan_from is not None and event.button() == Qt.MouseButton.LeftButton:
            self._pan_from = None
            self._update_cursor()
            return
        if self._tool == "magnify":
            self._lens = None
            self.viewport().update()
            return
        if self._drag is not None and self._tool in _DRAG_TOOLS:
            start, end = self._drag, self.mapToScene(event.position().toPoint())
            self._drag = None
            self._remove(self._temp)
            self._temp = []
            if QLineF(start, end).length() < 4:
                return
            if self._tool == "arrow":
                self.add_arrow(start, end)
            elif self._tool == "circle":
                self.add_circle(start, end)
            elif self._tool == "length":
                self.add_length(start, end)
            elif self._tool == "crop":
                self.crop_to(start.x(), start.y(), end.x(), end.y())
            elif self._tool == "calibrate":
                px = QLineF(start, end).length()
                mm, ok = QInputDialog.getDouble(self, "눈금 보정", "방금 그은 선의 실제 길이(mm):", 1.0, 0.01, 200.0, 2)
                if ok and mm > 0:
                    self.calibrated.emit(px / mm)
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and (self._temp or self._angle_pts or self._drag is not None):
            self._cancel_temp()
            return
        super().keyPressEvent(event)

    # ================= 오버레이 (캡션 · 돋보기) =================
    def drawForeground(self, painter: QPainter, rect) -> None:  # noqa: N802
        painter.save()
        painter.resetTransform()
        if self._base is None:
            painter.setPen(QColor("#64748b"))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
        else:
            if self._caption:
                f = QFont(self.font())
                f.setPointSize(10)
                painter.setFont(f)
                fm = painter.fontMetrics()
                w = fm.horizontalAdvance(self._caption) + 16
                painter.fillRect(8, 8, w, fm.height() + 8, QColor(0, 0, 0, 160))
                painter.setPen(QColor("#e2e8f0"))
                painter.drawText(16, 8 + 4 + fm.ascent(), self._caption)
            if self._hud_roi is not None:
                poly = self.mapFromScene(self._hud_roi)
                pen = QPen(QColor(255, 255, 255, 150), 1.4, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolygon(poly)
            if self._hud:
                self._draw_hud(painter)
            if self._lens is not None and not self._pix.pixmap().isNull():
                self._draw_lens(painter)
        painter.restore()

    def _draw_hud(self, painter: QPainter) -> None:
        f = QFont(self.font())
        f.setPointSize(10)
        f.setBold(True)
        painter.setFont(f)
        fm = painter.fontMetrics()
        y = 8
        right = self.viewport().width() - 8
        for text, color in self._hud:
            w = fm.horizontalAdvance(text) + 18
            r = QRectF(right - w, y, w, fm.height() + 10)
            painter.setPen(QPen(QColor(color), 1.2))
            painter.setBrush(QColor(8, 11, 16, 175))
            painter.drawRoundedRect(r, 6, 6)
            painter.setPen(QColor(color))
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
            y += fm.height() + 14

    def _draw_lens(self, painter: QPainter) -> None:
        vp = self._lens
        r = float(self.lens_radius)
        sp = self.mapToScene(vp.toPoint())
        scale = max(1e-6, self.transform().m11())
        half = r / (scale * self.lens_zoom)
        src = QRectF(sp.x() - half, sp.y() - half, 2 * half, 2 * half)
        dst = QRectF(vp.x() - r, vp.y() - r, 2 * r, 2 * r)
        path = QPainterPath()
        path.addEllipse(dst)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setClipPath(path)
        painter.fillRect(dst, QColor("#000000"))
        painter.drawPixmap(dst, self._pix.pixmap(), src)
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.drawEllipse(dst)
        painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
        painter.drawLine(QPointF(vp.x() - 8, vp.y()), QPointF(vp.x() + 8, vp.y()))
        painter.drawLine(QPointF(vp.x(), vp.y() - 8), QPointF(vp.x(), vp.y() + 8))

    # ================= 합성 렌더 =================
    def render_to_bgr(self) -> np.ndarray | None:
        """보정 + 주석·측정을 편집 이미지 해상도 BGR로 합성 (미리보기 선 제외)."""
        if self._base is None:
            return None
        temp, self._temp = self._temp, []
        for it in temp:
            it.setVisible(False)
        ov = self._overlay.isVisible()
        self._overlay.setVisible(False)              # 피킹·고스트·분석 마스크는 저장본에 넣지 않음
        h, w = self._base.shape[:2]
        qimg = QImage(w, h, QImage.Format.Format_RGB888)
        qimg.fill(QColor("#000000"))
        p = QPainter(qimg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self._scene.render(p, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
        p.end()
        for it in temp:
            it.setVisible(True)
        self._overlay.setVisible(ov)
        self._temp = temp
        return qimage_to_bgr(qimg)

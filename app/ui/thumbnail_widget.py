"""썸네일 패널의 단일 항목 (클릭 가능): 4:3 영상 + 안구 배지 + 시각·코멘트 오버레이."""
from __future__ import annotations

import logging
import os
from collections import OrderedDict

from PySide6.QtCore import QObject, QRectF, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.models.capture import Capture
from app.ui import icons
from app.ui.theme import EYE_COLORS
from app.ui.video_view import scaled_font

_cache: "OrderedDict[str, QPixmap]" = OrderedDict()
EYE_BADGE_H = 28       # 눈 표시(OD · OS) 높이 — 멀리서도 읽히게 크게
CACHE_MAX = 250        # 480px 썸네일 한 장 약 0.7MB — 최근 본 것만 (한 화면은 120장씩 그림)
SHARP_W = 480          # 카드(약 230px) × 레티나 2배 — 이보다 작은 썸네일(예전 120px)은 원본 사진에서 다시 읽는다


log = logging.getLogger(__name__)


def _read_scaled_image(path: str, width: int) -> QImage:
    """원본을 width 폭으로 줄여 읽는다 (JPEG 은 디코딩 단계에서 줄여 빠름, PNG 는 다 읽고 줄임). 작업 스레드에서도 안전."""
    r = QImageReader(path)
    r.setAutoTransform(True)
    size = r.size()
    if size.isValid() and size.width() > width:
        r.setScaledSize(size.scaled(width, 10 ** 6, Qt.AspectRatioMode.KeepAspectRatio))
    img = r.read()
    if not img.isNull() and img.width() > width:
        img = img.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    return img


def _read_scaled(path: str, width: int) -> QPixmap:
    img = _read_scaled_image(path, width)
    return QPixmap.fromImage(img) if not img.isNull() else QPixmap()


class _Upgrader(QObject):
    """예전 작은 썸네일(120px)을 원본에서 SHARP_W 로 다시 만든다 — 작업 스레드에서 읽고(PNG 1장 약 50ms),
    썸네일 파일을 한 번 새로 저장해 다음부터는 바로 선명하게. 끝나면 done(썸네일 경로, 이미지)."""
    done = Signal(str, QImage)

    def __init__(self):
        super().__init__()
        self.pending: set[str] = set()
        self.done.connect(self._finished)

    def request(self, src: str, thumb: str) -> None:
        if thumb in self.pending:
            return
        self.pending.add(thumb)
        QThreadPool.globalInstance().start(_UpgradeJob(self, src, thumb))

    def _finished(self, thumb: str, img: QImage) -> None:
        self.pending.discard(thumb)
        if not img.isNull():
            _cache[thumb] = QPixmap.fromImage(img)
            _cache.move_to_end(thumb)
            while len(_cache) > CACHE_MAX:
                _cache.popitem(last=False)


class _UpgradeJob(QRunnable):
    def __init__(self, up: _Upgrader, src: str, thumb: str):
        super().__init__()
        self.up, self.src, self.thumb = up, src, thumb

    def run(self) -> None:
        img = QImage()
        try:
            img = _read_scaled_image(self.src, SHARP_W)
            if not img.isNull():
                tmp = self.thumb + ".sharp.jpg"
                if img.save(tmp, "JPG", 90):
                    os.replace(tmp, self.thumb)          # 다음 실행부터는 이 파일이 바로 선명
        except Exception:  # noqa: BLE001 — 썸네일은 다시 만들 수 있는 캐시: 실패해도 작은 것을 그대로 쓴다
            log.warning("thumbnail upgrade failed: %s", self.thumb, exc_info=True)
        try:
            self.up.done.emit(self.thumb, img)
        except RuntimeError:                              # 프로그램이 닫히는 중
            pass


_upgrader: _Upgrader | None = None


def upgrader() -> _Upgrader:
    global _upgrader
    if _upgrader is None:
        _upgrader = _Upgrader()
    return _upgrader


def _needs_upgrade(capture: Capture, path: str, pm: QPixmap) -> bool:
    return (not pm.isNull() and pm.width() < SHARP_W and capture.media_type == "photo"
            and bool(capture.file_path) and capture.file_path != path and os.path.isfile(capture.file_path))


def _load_pixmap(capture: Capture) -> QPixmap | None:
    path = capture.thumbnail_path
    if not path and capture.media_type == "photo":
        path = capture.file_path
    if not path:
        return None
    pm = _cache.get(path)
    if pm is not None:
        _cache.move_to_end(path)
    else:
        pm = QPixmap(path)
        if _needs_upgrade(capture, path, pm):
            upgrader().request(capture.file_path, path)   # 작은 것 먼저 보이고, 선명한 것은 뒤에서
            return pm                                        # (캐시에는 선명한 것이 들어간다)
        if pm.isNull():
            return None
        if pm.width() > SHARP_W:
            pm = pm.scaledToWidth(SHARP_W, Qt.TransformationMode.SmoothTransformation)
        _cache[path] = pm
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return pm


class ThumbnailWidget(QWidget):
    clicked = Signal(object)        # Capture — 한 번 클릭 (촬영 화면: 선택)
    doubleClicked = Signal(object)  # Capture — 두 번 클릭 (큰 화면으로 보기)
    rightClicked = Signal(object)   # Capture — 오른쪽 클릭 (촬영 화면: 멀티로 모아 보기)

    def __init__(self, capture: Capture, parent=None):
        super().__init__(parent)
        self.capture = capture
        self.setObjectName("ThumbCard")
        self._pm = _load_pixmap(capture)
        self._scaled = None             # (폭, 높이, 배율, 줄인 사진) — 그릴 때마다 줄이지 않게
        self._thumb_key = capture.thumbnail_path or ""
        if self._thumb_key and self._thumb_key in upgrader().pending:
            upgrader().done.connect(self._on_upgraded)        # 선명한 썸네일이 준비되면 바꿔 그림
        self._hover = False
        self._rank = 0                  # 선택 순서 (0 = 선택 안 됨)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        eye = capture.eye if capture.eye and capture.eye != "NA" else ""
        self.setToolTip(" · ".join(x for x in (capture.captured_at, eye, capture.note or "") if x))

    def _on_upgraded(self, thumb: str, img: QImage) -> None:
        if thumb != self._thumb_key or img.isNull():
            return
        try:
            upgrader().done.disconnect(self._on_upgraded)
        except (RuntimeError, TypeError):
            pass
        self._pm = QPixmap.fromImage(img)
        self._scaled = None
        self.update()

    def _sharp_pixmap(self) -> QPixmap:
        """카드 크기 × 화면 배율(레티나 2배)로 줄인 사진 — 논리 크기로 줄였다가 늘리면 흐려진다."""
        dpr = max(1.0, float(self.devicePixelRatioF()))
        key = (self.width(), self.height(), dpr)
        if self._scaled is None or self._scaled[0] != key:
            s = self._pm.scaled(int(self.width() * dpr), int(self.height() * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            s.setDevicePixelRatio(dpr)
            self._scaled = (key, s)
        return self._scaled[1]

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return int(w * 3 / 4)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def resizeEvent(self, e) -> None:  # noqa: N802
        self.setFixedHeight(self.heightForWidth(self.width()))
        super().resizeEvent(e)

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        c = self.capture
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#000000"))
        p.drawRoundedRect(r, 9, 9)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(r, 9, 9)
        p.setClipPath(clip)
        if self._pm is not None:
            s = self._sharp_pixmap()
            sw, sh = s.width() / s.devicePixelRatio(), s.height() / s.devicePixelRatio()
            p.drawPixmap(QRectF((self.width() - sw) / 2, (self.height() - sh) / 2, sw, sh), s, QRectF(s.rect()))
        else:
            p.drawPixmap(self.width() // 2 - 15, self.height() // 2 - 15,
                         icons.pixmap("play" if c.media_type == "video" else "image", "#6b7586", 30))
        # 하단 그라데이션 + 시각·코멘트
        gh = 44
        g = QLinearGradient(0, self.height() - gh, 0, self.height())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, 215))
        p.fillRect(QRectF(0, self.height() - gh, self.width(), gh), g)
        f = scaled_font(self.font(), 0.85, True)
        p.setFont(f)
        when = c.captured_at[-8:] if c.captured_at else ""
        note = c.note or ""
        y = self.height() - (32 if note else 20)
        p.setPen(QColor("#e7eaf0"))
        p.drawText(QRectF(8, y, self.width() - 16, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, when)
        if note:
            p.setFont(scaled_font(self.font(), 0.85))
            p.setPen(QColor("#a3acba"))
            el = p.fontMetrics().elidedText(note, Qt.TextElideMode.ElideRight, self.width() - 16)
            p.drawText(QRectF(8, y + 15, self.width() - 16, 15), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, el)
        # 배지 — 눈: OD(우안)는 오른쪽 위 · OS(좌안)·OU 는 왼쪽 위, 크게 (동영상 길이는 왼쪽)
        lx, rx = 6.0, self.width() - 6.0
        if c.eye and c.eye != "NA":
            fe = scaled_font(self.font(), 1.35, True)
            p.setFont(fe)
            col = EYE_COLORS.get(c.eye, "#a3acba")
            w = p.fontMetrics().horizontalAdvance(c.eye) + 16
            if c.eye == "OD":
                br = QRectF(rx - w, 5, w, EYE_BADGE_H)
                rx -= w + 4
            else:
                br = QRectF(lx, 5, w, EYE_BADGE_H)
                lx += w + 4
            p.setPen(QPen(QColor(col), 1.5))
            p.setBrush(QColor(8, 11, 16, 205))
            p.drawRoundedRect(br, 6, 6)
            p.setPen(QColor(col))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, c.eye)
        if c.media_type == "video":
            p.setFont(f)
            text, col = f"▶ {round((c.duration_ms or 0) / 1000)}s", "#ff8a87"
            w = p.fontMetrics().horizontalAdvance(text) + 12
            br = QRectF(lx, 9, w, 20)
            p.setPen(QPen(QColor(col), 1))
            p.setBrush(QColor(8, 11, 16, 190))
            p.drawRoundedRect(br, 5, 5)
            p.setPen(QColor(col))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, text)
            lx += w + 4
        p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._rank:
            p.setPen(QPen(QColor("#facc15"), 3))
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), 9, 9)
            # 오른쪽 위 선택 번호 (OD 표시가 있으면 그 왼쪽)
            p.setFont(scaled_font(self.font(), 0.95, True))
            br = QRectF(rx - 26, 7, 26, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#facc15"))
            p.drawRoundedRect(br, 6, 6)
            p.setPen(QColor("#111827"))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, str(self._rank))
        else:
            p.setPen(QPen(QColor("#33c2b1"), 2) if self._hover else QPen(QColor(255, 255, 255, 18), 1))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)

    def set_rank(self, rank: int) -> None:
        if rank != self._rank:
            self._rank = rank
            self.update()

    def rank(self) -> int:
        return self._rank

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.capture)
        elif event.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit(self.capture)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self.capture)

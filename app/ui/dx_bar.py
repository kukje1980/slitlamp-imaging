"""상단바(환자 검색 오른쪽 빈 공간)의 질환별 작은 아이콘 줄.

아이콘을 누르면 그 질환의 샘플 · 등록 참조 사진(최대 4장)이 지금 환자 사진과 함께 멀티 화면에 놓인다.
같은 아이콘을 다시 누르면 그 질환 사진을 뺀다. (v1.20.3 부터 화면 아래 참조 줄은 없고, 고른 참조 사진은
메인 창의 숨긴 선택 모델 RefBar 가 들고 있다.)
질환 목록은 DB 의 샘플 환자(질환별, app/services/sample_import.py)와 등록한 참조 분류에서 만든다.
아이콘 아래에 짧은 질환 이름(정상 · 건성안 · 백내장 …)이 보이고, 마우스를 올리면 아이콘 아래에 그 질환의
사진이 작게(최대 PREVIEW_MAX 장) 뜬다 (DxPreview — 사진 목록은 메인 창이 preview_provider 로 준다).
창이 좁을 때(상단 메뉴가 아이콘만 남는 compact)는 한 개의 '질환 ▾' 버튼(메뉴)으로 줄어든다.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout

from app.ui import icons
from app.ui.theme import ICON, ICON_ACCENT

# 질환 이름에 들어 있는 말 → 아이콘 (앞에서부터 먼저 맞는 것; '후낭' 은 '백내장' 보다 먼저)
_ICON_BY_KEYWORD = [
    ("정상", "dx-normal"), ("건성", "dx-dryeye"), ("안검염", "dx-blepharitis"), ("마이봄", "dx-blepharitis"),
    ("결막하출혈", "dx-hemorrhage"), ("출혈", "dx-hemorrhage"), ("결막염", "dx-conjunctivitis"),
    ("후낭", "dx-pco"), ("백내장", "dx-cataract"), ("수정체", "dx-cataract"),
    ("다래끼", "dx-chalazion"), ("산립종", "dx-chalazion"), ("맥립종", "dx-chalazion"),
    ("익상편", "dx-pterygium"), ("검열반", "dx-pinguecula"),
    ("찰과상", "dx-abrasion"), ("미란", "dx-abrasion"), ("이물", "dx-abrasion"), ("상피", "dx-abrasion"),
    ("각막염", "dx-keratitis"), ("궤양", "dx-keratitis"), ("각막", "dx-keratitis"),
    ("포도막", "dx-uveitis"), ("홍채", "dx-uveitis"),
]
MAX_BUTTONS = 14                      # 그 이상은 '질환 ▾' 메뉴로
# 아이콘 아래에 쓰는 짧은 이름 (샘플 질환 폴더 이름은 '익상편_검열반' · '백내장수술후_후낭혼탁' 처럼 길다)
_SHORT_BY_ICON = {
    "dx-normal": "정상", "dx-dryeye": "건성안", "dx-blepharitis": "안검염", "dx-hemorrhage": "결막출혈",
    "dx-conjunctivitis": "결막염", "dx-pco": "후낭혼탁", "dx-cataract": "백내장", "dx-chalazion": "다래끼",
    "dx-pterygium": "익상편", "dx-pinguecula": "검열반", "dx-abrasion": "찰과상", "dx-keratitis": "각막염",
    "dx-uveitis": "포도막염",
}
LABEL_MAX = 4                         # 아이콘 아래 이름은 이 글자 수까지 (버튼 폭 46px · 10px 글자 — 넘으면 짧은 이름 · …)
BTN_LABELED = QSize(46, 42)           # 아이콘 + 아래 이름
BTN_ICON = QSize(30, 30)              # 창이 조금 좁을 때: 아이콘만 (이름은 마우스를 올리면 미리보기에)
PREVIEW_MAX = 4                       # 마우스를 올렸을 때 보여 줄 사진 수
PREVIEW_THUMB = QSize(120, 90)        # 미리보기 사진 한 장 크기
PREVIEW_DELAY_MS = 200                # 지나가기만 할 때 깜빡이지 않게 잠깐 머물면 띄운다


def short_label(name: str, source: str = "sample") -> str:
    """아이콘 아래 짧은 질환 이름: 앞부분('·' 앞)이 LABEL_MAX 자 이하면 그대로, 길면 샘플은 질환별 정해진 이름 ·
    등록한 분류는 앞 글자 + '…'."""
    text = (name or "").replace("_", " ").split("·")[0].split("/")[0].strip() or (name or "")
    if len(text) <= LABEL_MAX:
        return text
    ic = icon_for(name)
    if source == "sample" and ic in _SHORT_BY_ICON:
        return _SHORT_BY_ICON[ic]              # '백내장 수술 후 후낭혼탁' → '후낭혼탁'
    return text[:LABEL_MAX - 1] + "…"


def icon_for(name: str) -> str:
    n = name or ""
    for kw, ic in _ICON_BY_KEYWORD:
        if kw in n:
            return ic
    return "image"


class DxPreview(QFrame):
    """질환 아이콘 아래에 뜨는 작은 사진 미리보기 (툴팁처럼 떠 있는 창 — 누르거나 마우스를 떼면 사라짐)."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("DxPreview")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        lay.setSpacing(5)
        self.title = QLabel()
        self.title.setObjectName("DxPreviewTitle")
        lay.addWidget(self.title)
        self._row = QHBoxLayout()
        self._row.setSpacing(5)
        lay.addLayout(self._row)
        self.hint = QLabel("누르면 멀티 화면에 지금 환자 사진과 같이 (다시 누르면 빼기)")
        self.hint.setObjectName("Muted")
        lay.addWidget(self.hint)
        self.photos: list[QLabel] = []
        self._cache: dict[str, QPixmap] = {}

    def _pix(self, path: str) -> QPixmap:
        pm = self._cache.get(path)
        if pm is None:
            pm = QPixmap(path)
            if not pm.isNull():
                pm = pm.scaled(PREVIEW_THUMB, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            self._cache[path] = pm
        return pm

    def show_for(self, button, title: str, paths: list) -> None:
        for lb in self.photos:
            self._row.removeWidget(lb)
            lb.deleteLater()
        self.photos = []
        self.title.setText(title)
        shown = 0
        for path in paths:
            pm = self._pix(path) if path else QPixmap()
            if pm.isNull():
                continue
            lb = QLabel()
            lb.setObjectName("DxPreviewPhoto")
            lb.setFixedSize(PREVIEW_THUMB)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setPixmap(pm)
            self._row.addWidget(lb)
            self.photos.append(lb)
            shown += 1
            if shown >= PREVIEW_MAX:
                break
        if not self.photos:
            lb = QLabel("사진 없음")
            lb.setObjectName("Muted")
            self._row.addWidget(lb)
            self.photos.append(lb)
        self.adjustSize()
        pos = button.mapToGlobal(QPoint(0, button.height() + 4))   # 아이콘 바로 아래
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            pos.setX(max(g.left(), min(pos.x(), g.right() - self.width())))
            pos.setY(min(pos.y(), g.bottom() - self.height()))
        self.move(pos)
        self.show()
        self.raise_()


class _HoverWatch(QObject):
    """질환 버튼에 마우스가 들어옴 · 나감 → DxBar 에 알린다."""

    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar

    def eventFilter(self, obj, ev):  # noqa: N802
        t = ev.type()
        if t == QEvent.Type.Enter:
            self.bar._hover_enter(obj)
        elif t in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Hide):
            self.bar._hover_leave()
        return False


class DxBar(QFrame):
    diseaseClicked = Signal(str, str)     # (source: 'sample' | 'library', 분류 이름)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DxBar")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(3, 3, 3, 3)
        self._lay.setSpacing(2)
        self.buttons: dict[tuple[str, str], QToolButton] = {}
        self._entries: list[tuple[str, str, int]] = []      # (source, category, 사진 수)
        self._active: tuple[str, str] | None = None
        self._compact = False
        self._labels = True                   # 아이콘 아래 질환 이름 (창 폭이 모자라면 메인 창이 끈다)
        self.menu_btn = QPushButton(icons.gap("질환"))
        self.menu_btn.setObjectName("DxMenuBtn")
        self.menu_btn.setIcon(icons.icon("dx-normal", ICON, 18))
        self.menu_btn.setIconSize(QSize(18, 18))
        self.menu_btn.setToolTip("질환별 참조 사진 — 고르면 멀티 화면에 지금 환자 사진과 같이 (다시 고르면 빼기)")
        self.menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu = QMenu(self.menu_btn)
        self.menu_btn.setMenu(self.menu)
        self._lay.addWidget(self.menu_btn)
        self.menu_btn.hide()
        self.preview_provider = None          # (source, 분류) → [사진 파일 경로] — 메인 창이 정한다
        self.preview = DxPreview(self)
        self._hover_btn = None
        self._watch = _HoverWatch(self)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(PREVIEW_DELAY_MS)
        self._hover_timer.timeout.connect(self.show_preview)
        self.hide()

    # ---- 목록 ----
    def set_categories(self, samples: list, library: list) -> None:
        """samples: [(질환 이름, 사진 수)], library: [등록한 분류 이름]. 버튼을 다시 만든다 (활성 질환은 유지)."""
        self._hover_leave()
        for b in self.buttons.values():
            self._lay.removeWidget(b)
            b.deleteLater()
        self.buttons = {}
        self.menu.clear()
        self._entries = [("sample", str(name), int(n)) for name, n in samples] + \
                        [("library", str(c), 0) for c in library if c]
        for i, (source, cat, n) in enumerate(self._entries):
            key = (source, cat)
            b = QToolButton()
            b.setObjectName("DxBtn")
            b.setCheckable(True)
            b.setProperty("dx_label", short_label(cat, source))
            self._style_button(b)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            name = icon_for(cat) if source == "sample" else ("image" if icon_for(cat) == "image" else icon_for(cat))
            ic = icons.icon(name, ICON, 18)
            ic.addPixmap(icons.pixmap(name, ICON_ACCENT, 18), ic.Mode.Normal, ic.State.On)
            b.setIcon(ic)
            b.setIconSize(QSize(18, 18))
            what = f"샘플 사진 {n}장" if source == "sample" else "등록한 참조 영상"
            b.setProperty("dx_key", key)
            b.setProperty("dx_title", f"{cat} · {what}")
            b.setAccessibleName(cat)
            b.installEventFilter(self._watch)        # 마우스를 올리면 사진 미리보기 (툴팁 대신)
            b.clicked.connect(lambda _=False, k=key: self.diseaseClicked.emit(*k))
            self.buttons[key] = b
            self._lay.insertWidget(i, b)
            act = QAction(icons.icon(name, ICON, 16), f"{cat}  ({what})", self.menu)
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, k=key: self.diseaseClicked.emit(*k))
            self.menu.addAction(act)
        if self._active not in self.buttons:
            self._active = None
        self.set_active(self._active)
        self.setVisible(bool(self._entries))
        self._apply_compact()

    def entries(self) -> list:
        return list(self._entries)

    # ---- 마우스를 올리면 사진 미리보기 ----
    def _hover_enter(self, button) -> None:
        self._hover_btn = button
        self._hover_timer.start()

    def _hover_leave(self) -> None:
        self._hover_btn = None
        self._hover_timer.stop()
        self.preview.hide()

    def show_preview(self, button=None) -> bool:
        """button(없으면 지금 마우스가 올라간 버튼)의 질환 사진을 아이콘 아래에 띄운다."""
        b = button or self._hover_btn
        if b is None or not b.isVisible() or self.preview_provider is None:
            return False
        key = tuple(b.property("dx_key") or ())
        if len(key) != 2:
            return False
        try:
            paths = list(self.preview_provider(*key) or [])
        except Exception:  # noqa: BLE001 — 미리보기 실패가 버튼을 막지 않게
            paths = []
        self.preview.show_for(b, str(b.property("dx_title") or key[1]), paths)
        return True

    def hideEvent(self, e) -> None:  # noqa: N802
        self._hover_leave()
        super().hideEvent(e)

    # ---- 상태 ----
    def set_active(self, key) -> None:
        self._active = tuple(key) if key else None
        for k, b in self.buttons.items():
            b.setChecked(k == self._active)
        for act, (source, cat, _n) in zip(self.menu.actions(), self._entries):
            act.setChecked((source, cat) == self._active)
        if self._active is not None:
            b = self.buttons.get(self._active)
            if b is not None:
                self.menu_btn.setIcon(b.icon())
        else:
            self.menu_btn.setIcon(icons.icon("dx-normal", ICON, 18))

    def active(self):
        return self._active

    # ---- 아이콘 아래 이름 ----
    def _style_button(self, b) -> None:
        if self._labels:
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setText(str(b.property("dx_label") or ""))
            b.setFixedSize(BTN_LABELED)
        else:
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            b.setText("")
            b.setFixedSize(BTN_ICON)

    def set_labels(self, on: bool) -> None:
        """아이콘 아래 질환 이름 보이기 / 숨기기 (창이 조금 좁을 때 아이콘만)."""
        if bool(on) == self._labels:
            return
        self._labels = bool(on)
        for b in self.buttons.values():
            self._style_button(b)
        self.layout().invalidate()
        self.updateGeometry()

    def labels_visible(self) -> bool:
        return self._labels

    def label_extra_width(self) -> int:
        """이름을 보일 때 아이콘만일 때보다 더 드는 폭."""
        if self._compact or len(self._entries) > MAX_BUTTONS:
            return 0
        return len(self.buttons) * (BTN_LABELED.width() - BTN_ICON.width())

    def set_compact(self, on: bool) -> None:
        """좁은 창: 아이콘 줄 대신 '질환 ▾' 메뉴 버튼 하나."""
        self._compact = bool(on)
        self._apply_compact()

    def _apply_compact(self) -> None:
        compact = self._compact or len(self._entries) > MAX_BUTTONS
        for b in self.buttons.values():
            b.setVisible(not compact)
        self.menu_btn.setVisible(compact and bool(self._entries))
        self.layout().invalidate()
        self.updateGeometry()

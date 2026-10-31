"""상단바(환자 검색 오른쪽 빈 공간)의 질환별 작은 아이콘 줄.

아이콘을 누르면 아래 참조 줄(RefBar)에 그 질환의 샘플 · 등록 참조 사진이 작은 사진으로 나오고,
그 사진들이 지금 환자 사진과 함께 멀티 화면에 놓인다. 같은 아이콘을 다시 누르면 그 질환 사진을 뺀다.
질환 목록은 DB 의 샘플 환자(질환별, app/services/sample_import.py)와 등록한 참조 분류에서 만든다.
창이 좁을 때(상단 메뉴가 아이콘만 남는 compact)는 한 개의 '질환 ▾' 버튼(메뉴)으로 줄어든다.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QPushButton

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


def icon_for(name: str) -> str:
    n = name or ""
    for kw, ic in _ICON_BY_KEYWORD:
        if kw in n:
            return ic
    return "image"


class DxBar(QFrame):
    diseaseClicked = Signal(str, str)     # (source: 'sample' | 'library', 분류 이름)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DxBar")
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(3, 3, 3, 3)
        self._lay.setSpacing(2)
        self.buttons: dict[tuple[str, str], QPushButton] = {}
        self._entries: list[tuple[str, str, int]] = []      # (source, category, 사진 수)
        self._active: tuple[str, str] | None = None
        self._compact = False
        self.menu_btn = QPushButton(icons.gap("질환"))
        self.menu_btn.setObjectName("DxMenuBtn")
        self.menu_btn.setIcon(icons.icon("dx-normal", ICON, 18))
        self.menu_btn.setIconSize(QSize(18, 18))
        self.menu_btn.setToolTip("질환별 참조 사진 — 고르면 아래 참조 줄과 멀티 화면에 지금 환자 사진과 같이")
        self.menu_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu = QMenu(self.menu_btn)
        self.menu_btn.setMenu(self.menu)
        self._lay.addWidget(self.menu_btn)
        self.menu_btn.hide()
        self.hide()

    # ---- 목록 ----
    def set_categories(self, samples: list, library: list) -> None:
        """samples: [(질환 이름, 사진 수)], library: [등록한 분류 이름]. 버튼을 다시 만든다 (활성 질환은 유지)."""
        for b in self.buttons.values():
            self._lay.removeWidget(b)
            b.deleteLater()
        self.buttons = {}
        self.menu.clear()
        self._entries = [("sample", str(name), int(n)) for name, n in samples] + \
                        [("library", str(c), 0) for c in library if c]
        for i, (source, cat, n) in enumerate(self._entries):
            key = (source, cat)
            b = QPushButton()
            b.setObjectName("DxBtn")
            b.setCheckable(True)
            b.setFixedSize(30, 30)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            name = icon_for(cat) if source == "sample" else ("image" if icon_for(cat) == "image" else icon_for(cat))
            ic = icons.icon(name, ICON, 18)
            ic.addPixmap(icons.pixmap(name, ICON_ACCENT, 18), ic.Mode.Normal, ic.State.On)
            b.setIcon(ic)
            b.setIconSize(QSize(18, 18))
            what = f"샘플 사진 {n}장" if source == "sample" else "등록한 참조 영상"
            b.setToolTip(f"<b>{cat}</b> · {what}<br>누르면 아래 참조 줄에 사진이 나오고 멀티 화면에 지금 환자 사진과 같이 "
                         "놓입니다 (다시 누르면 빼기)")
            b.setAccessibleName(cat)
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

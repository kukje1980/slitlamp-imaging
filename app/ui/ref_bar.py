"""참조 줄 — 촬영 화면 아래(Smart 줄처럼 열고 닫음)에서 등록한 참조 영상 · 샘플 사진을 작은 사진으로 보여 준다.

사진을 한 번 누르면 고름(다시 누르면 뺌, 여러 장) → 고른 참조 사진은 멀티 화면에 지금 환자 사진과 같이 놓인다.
분류(전체 · 등록한 분류 · 샘플 질환)를 바꿔도 고른 사진은 그대로 남는다. '라이브러리…' = 참조 영상 라이브러리 창
(등록 · 정보 수정 · 샘플 사진 가져오기), 창을 닫으면 목록을 다시 읽는다.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)

from app.services import sample_import
from app.ui import icons
from app.ui.multi_view import ref_key
from app.ui.theme import ICON, ICON_ACCENT

ALL = ("all", None)
_THUMB = QSize(64, 48)                   # 줄 높이를 낮게 — 열어도 창 최소 높이가 크게 늘지 않게


class RefBar(QFrame):
    selectionChanged = Signal(list)       # [RefImage] — 고른 순서대로
    libraryRequested = Signal()           # '라이브러리…'
    hideRequested = Signal()              # 참조 줄 숨기기 (아래 촬영 도크의 '참조' 로 다시 보임)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setObjectName("Dock")
        self.db = db
        self._refs: list = []                 # 지금 분류에서 보이는 참조 사진
        self._chosen: dict = {}               # ref_key → RefImage (고른 순서)
        self._loaded = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 2, 12, 2)
        lay.setSpacing(6)

        title = QLabel()
        title.setPixmap(icons.pixmap("image", ICON_ACCENT, 18))
        lay.addWidget(title)
        name = QLabel("참조")
        name.setObjectName("SmartTitle")
        lay.addWidget(name)

        self.c_cat = QComboBox()
        self.c_cat.setToolTip("분류 — 등록한 참조 영상 · 샘플 사진(질환별)")
        self.c_cat.setMinimumWidth(110)
        lay.addWidget(self.c_cat)

        self.list = QListWidget()
        self.list.setObjectName("RefStrip")
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setFlow(QListWidget.Flow.LeftToRight)
        self.list.setWrapping(False)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setIconSize(_THUMB)
        self.list.setGridSize(QSize(_THUMB.width() + 8, _THUMB.height() + 8))
        self.list.setSpacing(2)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.list.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setFixedHeight(_THUMB.height() + 18)
        self.list.setMinimumWidth(120)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setToolTip("누르면 멀티 화면에 같이 보기 · 다시 누르면 빼기")
        lay.addWidget(self.list, 1)
        self.lbl_empty = QLabel("참조 영상이 없습니다 — '라이브러리…' 에서 등록하거나 샘플 사진을 가져오세요")
        self.lbl_empty.setObjectName("SmartHint")
        self.lbl_empty.setWordWrap(True)
        lay.addWidget(self.lbl_empty, 1)

        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("SmartHint")
        lay.addWidget(self.lbl_count)
        self.b_clear = self._button("빼기", "eraser", "고른 참조 사진을 모두 멀티 화면에서 빼기")
        self.b_lib = self._button("라이브러리…", "image", "참조 영상 라이브러리 — 등록 · 정보 수정 · 샘플 사진 가져오기 · "
                                                     "지금 환자 사진과 나란히 비교")
        lay.addWidget(self.b_clear)
        lay.addWidget(self.b_lib)
        self.btn_hide = QPushButton()
        self.btn_hide.setObjectName("ToolBtn")
        self.btn_hide.setIcon(icons.icon("chev-d", ICON, 16))
        self.btn_hide.setIconSize(QSize(16, 16))
        self.btn_hide.setToolTip("참조 줄 숨기기 — 아래 촬영 도크의 '참조' 버튼으로 다시 보입니다 (고른 사진은 그대로)")
        self.btn_hide.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_hide.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self.btn_hide)

        self.c_cat.currentIndexChanged.connect(lambda *_: self._load())
        self.list.itemSelectionChanged.connect(self._on_list_selection)
        self.b_clear.clicked.connect(self.clear_selection)
        self.b_lib.clicked.connect(lambda: self.libraryRequested.emit())
        self.btn_hide.clicked.connect(lambda: self.hideRequested.emit())
        self._sync_labels()

    @staticmethod
    def _button(text, icon, tip) -> QPushButton:
        b = QPushButton(icons.gap(text))
        b.setObjectName("ToolBtn")
        b.setIcon(icons.icon(icon, ICON, 16))
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    # ---- 목록 ----
    def showEvent(self, e) -> None:  # noqa: N802
        if not self._loaded:                       # 처음 보일 때 읽는다 (숨겨 두면 사진을 읽지 않음)
            self.reload()
        super().showEvent(e)

    def reload(self) -> None:
        """분류 · 참조 사진을 다시 읽는다 (라이브러리 창에서 등록 · 삭제 · 샘플 가져오기 뒤)."""
        self._loaded = True
        keep = self.c_cat.currentData()
        self.c_cat.blockSignals(True)
        self.c_cat.clear()
        lib_cats = self.db.references.categories()
        sample_cats = [c for c, _n in sample_import.sample_categories(self.db)]
        self.c_cat.addItem("전체", ALL)
        for c in lib_cats:
            self.c_cat.addItem(c, ("library", c))
        for c in sample_cats:
            self.c_cat.addItem(f"샘플 · {c}", ("sample", c))
        i = self.c_cat.findData(keep) if keep else 0
        self.c_cat.setCurrentIndex(max(0, i))
        self.c_cat.blockSignals(False)
        alive = {ref_key(r) for r in self._all_refs()}
        gone = [k for k in self._chosen if k not in alive]      # 라이브러리에서 지운 참조
        for k in gone:
            del self._chosen[k]
        self._load()
        if gone:
            self.selectionChanged.emit(self.selected_refs())

    def refresh(self) -> None:
        """라이브러리가 바뀌었을 때 — 한 번이라도 보인 줄만 다시 읽는다."""
        if self._loaded:
            self.reload()

    def _all_refs(self) -> list:
        return self.db.references.list() + sample_import.sample_references(self.db)

    def _load(self) -> None:
        source, category = tuple(self.c_cat.currentData() or ALL)
        refs = []
        if source in ("all", "library"):
            refs += self.db.references.list(category=category)
        if source in ("all", "sample"):
            refs += sample_import.sample_references(self.db, category=category)
        self._refs = refs
        self.list.blockSignals(True)
        self.list.clear()
        for r in refs:
            pm = QPixmap(r.thumbnail_path or r.file_path)
            if not pm.isNull():
                pm = pm.scaled(_THUMB, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            ic = QIcon(pm)
            ic.addPixmap(pm, QIcon.Mode.Selected)   # 고른 사진도 색이 덮이지 않게 (노란 테두리로 표시)
            it = QListWidgetItem(ic, "")
            label = " · ".join(x for x in (r.category, r.title) if x) or "참조"
            it.setToolTip(("샘플 사진 · " if r.is_sample else "") + label)
            self.list.addItem(it)
            it.setSelected(ref_key(r) in self._chosen)
        self.list.blockSignals(False)
        self._sync_labels()

    def _on_list_selection(self) -> None:
        for i, r in enumerate(self._refs):
            key, on = ref_key(r), self.list.item(i).isSelected()
            if on and key not in self._chosen:
                self._chosen[key] = r
            elif not on and key in self._chosen:
                del self._chosen[key]
        self._sync_labels()
        self.selectionChanged.emit(self.selected_refs())

    def _sync_labels(self) -> None:
        empty = self._loaded and not self._refs and self.c_cat.currentIndex() <= 0
        self.list.setVisible(not empty)
        self.lbl_empty.setVisible(empty)
        n = len(self._chosen)
        self.lbl_count.setText(f"{n}장 고름" if n else "")
        self.b_clear.setEnabled(bool(n))

    # ---- 상단 질환 아이콘(DxBar)에서 ----
    def category_key(self) -> tuple:
        return tuple(self.c_cat.currentData() or ALL)

    def show_category(self, source: str, category) -> bool:
        """분류를 (source, category) 로 바꿔 그 사진들을 보인다. 없는 분류면 False."""
        if not self._loaded:
            self.reload()
        want = (source, category)
        # findData 는 파이썬 튜플을 QVariant 로 비교하지 못할 수 있어 직접 찾는다
        i = next((k for k in range(self.c_cat.count()) if tuple(self.c_cat.itemData(k) or ()) == want), -1)
        if i < 0:
            return False
        if self.c_cat.currentIndex() == i:
            self._load()
        else:
            self.c_cat.setCurrentIndex(i)          # → _load
        return True

    def choose_only_current(self, limit: int | None = None) -> list:
        """지금 보이는 분류의 사진만 고른다 (다른 분류에서 고른 것은 뺌). limit 장까지. 고른 목록을 돌려준다."""
        return self.choose(self._refs[:limit] if limit else list(self._refs))

    def choose(self, refs: list) -> list:
        """고른 참조 사진을 refs 로 바꾼다 (이 순서대로 · 나머지는 뺌). 고른 목록을 돌려준다."""
        self._chosen = {ref_key(r): r for r in refs}
        self.list.blockSignals(True)
        for i, r in enumerate(self._refs):
            it = self.list.item(i)
            if it is not None:
                it.setSelected(ref_key(r) in self._chosen)
        self.list.blockSignals(False)
        self._sync_labels()
        self.selectionChanged.emit(self.selected_refs())
        return self.selected_refs()

    # ---- 고른 참조 사진 ----
    def refs(self) -> list:
        """지금 분류에서 보이는 참조 사진."""
        return list(self._refs)

    def selected_refs(self) -> list:
        return list(self._chosen.values())

    def toggle(self, index: int) -> None:
        """index 번째 사진 고르기 / 빼기 (누른 것과 같음)."""
        it = self.list.item(index)
        if it is not None:
            it.setSelected(not it.isSelected())

    def clear_selection(self) -> None:
        if not self._chosen:
            return
        self._chosen = {}
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.blockSignals(False)
        self._sync_labels()
        self.selectionChanged.emit([])

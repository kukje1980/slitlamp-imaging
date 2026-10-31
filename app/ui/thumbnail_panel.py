"""우측 썸네일 패널: 헤더(개수) + 필터(전체/OD/OS/동영상) + 촬영일별 묶음 목록.

한 번 클릭 = 선택(번호 표시, 다시 누르면 해제) · 두 번 클릭 = 큰 화면으로 보기(thumbnailClicked).
오른쪽 클릭 = 전송 · 삭제 · 멀티 메뉴 또는 (설정 '사진 오른쪽 클릭' = 멀티) 멀티 화면 바로 (그 사진을 안 골랐으면 골라서, 고른 사진 전체에 적용 —
sendRequested · deleteRequested · multiRequested, 실제 동작은 메인 창).
촬영일 제목 줄 오른쪽의 쓰레기통 = 그 날 촬영한 사진 모두 삭제 요청(dayDeleteRequested, 실제 삭제는 메인 창)."""
from __future__ import annotations

import time
from datetime import date

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ui import icons
from app.ui.theme import ICON_ACCENT, ICON_RECORD
from app.ui.thumbnail_widget import ThumbnailWidget

_DOW = "월화수목금토일"
_FILTERS = [("all", "전체"), ("OS", "OS"), ("OD", "OD")]   # OS(좌안)는 왼쪽 · OD(우안)는 오른쪽
PAGE = 120           # 한 번에 그리는 썸네일 수. 장기 추적 환자(수천 장)를 골라도 화면이 멈추지 않게 나눠 그린다


def day_label(day: str) -> str:
    """'2026-10-23' → '2026.10.23 (금)'."""
    try:
        y, m, d = (int(x) for x in day.split("-"))
        return f"{y:04d}.{m:02d}.{d:02d} ({_DOW[date(y, m, d).weekday()]})"
    except (ValueError, TypeError):
        return day or "-"


class ThumbnailPanel(QFrame):
    thumbnailClicked = Signal(object)  # Capture — 두 번 클릭: 큰 화면으로 보기
    selectionChanged = Signal(list)    # 선택한 Capture 목록 (선택 순서)
    multiRequested = Signal(object)    # Capture — 오른쪽 클릭 메뉴 '멀티': 고른 사진을 멀티로 모아 보기
    sendRequested = Signal(list)       # 오른쪽 클릭 메뉴 '전송': 고른 사진 (선택 순서)
    sendDeleteRequested = Signal(list) # 오른쪽 클릭 메뉴 '전송(삭제)': 보낸 뒤 이 PC 에서 삭제 (확인은 메인 창)
    deleteRequested = Signal(list)     # 오른쪽 클릭 메뉴 '삭제': 고른 사진 (확인은 메인 창)
    dayDeleteRequested = Signal(str)   # 'YYYY-MM-DD' — 촬영일 제목 줄의 삭제 버튼: 그 날 영상 모두 삭제

    def __init__(self, db, newest_on_top: bool = True, parent=None, split_eyes: bool = False):
        super().__init__(parent)
        self.db = db
        self.newest_on_top = newest_on_top
        self.split_eyes = split_eyes        # True: 촬영일 대신 OD(우안) · OS(좌안) · 미지정 으로 나눠 보기
        self._patient_id = None
        self._filter = "all"
        self._items: list = []
        self._limit = PAGE
        self._selected: list[int] = []     # 선택한 capture id (선택 순서)
        self._last_press: tuple | None = None   # (capture id, 시각) — 두 번 클릭 판별
        self._pre_click: tuple | None = None    # (capture id, 두 번 클릭 전 선택 목록)
        self._widgets: dict[int, ThumbnailWidget] = {}
        self.day_delete_buttons: dict[str, QPushButton] = {}   # 촬영일 → 삭제 버튼 (테스트 · 자동화용)
        self._menu_target = None
        self.right_click_action = "menu"     # menu: 전송 · 삭제 · 멀티 메뉴 | multi: 멀티 화면 바로 (설정 '사진 오른쪽 클릭')
        self.context_menu = QMenu(self)      # 사진 오른쪽 클릭: 전송 · 삭제 · 멀티
        self.context_menu.setObjectName("ThumbMenu")
        self._menu_title = self.context_menu.addAction("")
        self._menu_title.setEnabled(False)
        self.context_menu.addSeparator()
        self.menu_actions = {
            "send": self.context_menu.addAction(icons.icon("send", ICON_ACCENT, 18), "전송"),
            "send_delete": self.context_menu.addAction(icons.icon("send", ICON_RECORD, 18), "전송(삭제)"),
            "multi": self.context_menu.addAction(icons.icon("review", ICON_ACCENT, 18), "멀티"),
            "delete": self.context_menu.addAction(icons.icon("trash", ICON_RECORD, 18), "삭제"),
        }
        self.menu_actions["send"].setToolTip("고른 사진을 설정한 전송 방법(폴더 · 클라우드 · PACS)으로 보내기")
        self.menu_actions["send_delete"].setToolTip("보낸 뒤 이 PC 에서 삭제 (모두 보냈을 때만 · 확인 후)")
        self.menu_actions["send_delete"].triggered.connect(lambda: self._menu_do("send_delete"))
        self.set_menu_items(["send", "multi", "delete"])
        self.menu_actions["delete"].setToolTip("고른 사진 삭제 (확인 후)")
        self.menu_actions["multi"].setToolTip("고른 사진을 한 화면에 모아 보고 편집 (닫기: 촬영 도크 '초기화')")
        self.menu_actions["send"].triggered.connect(lambda: self._menu_do("send"))
        self.menu_actions["delete"].triggered.connect(lambda: self._menu_do("delete"))
        self.menu_actions["multi"].triggered.connect(lambda: self._menu_do("multi"))

        self.setObjectName("ThumbnailPanel")
        self.setFixedWidth(248)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 6, 8)
        outer.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 6, 0)
        title = QLabel("촬영 이미지")
        title.setObjectName("ThumbTitle")
        self._badge = QLabel("0")
        self._badge.setObjectName("CountBadge")
        head.addWidget(title)
        head.addWidget(self._badge)
        head.addStretch(1)
        outer.addLayout(head)

        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 6, 0)
        chips.setSpacing(4)
        self._chip_group = QButtonGroup(self)
        self.filter_buttons = {}
        for key, text in _FILTERS:
            b = QPushButton(text)
            b.setObjectName("FilterChip")
            b.setCheckable(True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._chip_group.addButton(b)
            self.filter_buttons[key] = b
            b.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            chips.addWidget(b)
        self.filter_buttons["all"].setChecked(True)
        outer.addLayout(chips)
        sep = QFrame()
        sep.setObjectName("ThumbSep")
        sep.setFixedHeight(1)
        outer.addWidget(sep)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 세로 스크롤바는 항상 표시: 필요할 때만 보이게 하면 목록 높이가 경계값일 때
        # 스크롤바 생김→너비 감소→카드 높이 감소→스크롤바 사라짐→… 무한 재배치로 앱이 죽을 수 있다
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._container = QWidget()
        self._container.setObjectName("ThumbList")
        self._lay = QVBoxLayout(self._container)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._lay.setContentsMargins(0, 0, 6, 6)
        self._lay.setSpacing(8)
        self._scroll.setWidget(self._container)
        outer.addWidget(self._scroll, 1)
        self._empty = QLabel()
        self._empty.setObjectName("Muted")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)

    # ---- 공개 API ----
    def set_patient(self, patient_id) -> None:
        if patient_id != self._patient_id and self._selected:
            self._selected = []
            self.selectionChanged.emit([])
        self._patient_id = patient_id
        self._limit = PAGE
        order = "desc" if self.newest_on_top else "asc"
        self._items = self.db.captures.list_by_patient(patient_id, order=order) if patient_id is not None else []
        self._rebuild()

    def add_capture(self, capture) -> None:
        if capture.patient_id != self._patient_id:
            return
        if self.newest_on_top:
            self._items.insert(0, capture)
        else:
            self._items.append(capture)
        self._rebuild()

    def set_filter(self, key: str) -> None:
        self._filter = key if key in self.filter_buttons else "all"
        self.filter_buttons[self._filter].setChecked(True)
        self._limit = PAGE
        self._rebuild()

    def show_more(self) -> None:
        """'더 보기': 다음 PAGE 장을 추가로 그린다."""
        self._limit += PAGE
        self._rebuild()

    def shown_count(self) -> int:
        return min(self._limit, len(self.visible_captures()))

    def count(self) -> int:
        return len(self._items)

    # ---- 선택 ----
    def selected_captures(self) -> list:
        by_id = {c.id: c for c in self._items}
        return [by_id[i] for i in self._selected if i in by_id]

    def toggle_selection(self, capture) -> None:
        if capture.id in self._selected:
            self._selected.remove(capture.id)
        else:
            self._selected.append(capture.id)
        self._apply_ranks()
        self.selectionChanged.emit(self.selected_captures())

    def clear_selection(self) -> None:
        if self._selected:
            self._selected = []
            self._apply_ranks()
            self.selectionChanged.emit([])

    def _apply_ranks(self) -> None:
        for cid, w in self._widgets.items():
            w.set_rank(self._selected.index(cid) + 1 if cid in self._selected else 0)

    def set_split_eyes(self, on: bool) -> None:
        if bool(on) != self.split_eyes:
            self.split_eyes = bool(on)
            self._rebuild()

    def _add_card(self, c) -> None:
        w = ThumbnailWidget(c)
        w.clicked.connect(self._on_press)
        w.doubleClicked.connect(self._on_double)
        w.rightClicked.connect(self._on_right)
        self._widgets[c.id] = w
        self._lay.addWidget(w)

    def _build_by_eye(self, shown: list) -> None:
        groups = [("OD", "OD · 우안", [c for c in shown if c.eye == "OD"]),
                  ("OS", "OS · 좌안", [c for c in shown if c.eye == "OS"]),
                  ("NA", "안구 미지정", [c for c in shown if c.eye not in ("OD", "OS")])]
        for code, title, caps in groups:
            if not caps:
                continue
            h = QLabel(f"{title}   ·   {len(caps)}장")
            h.setObjectName("DayHeader")
            h.setProperty("eye", code)
            self._lay.addWidget(h)
            for c in caps:
                self._add_card(c)

    def _on_press(self, capture) -> None:
        """한 번 클릭 = 선택/해제. 두 번 클릭의 시작이면 그 전 선택 상태를 기억해 둔다."""
        now = time.monotonic()
        gap = QApplication.doubleClickInterval() / 1000.0
        last = self._last_press
        if not (last and last[0] == capture.id and now - last[1] <= gap):
            self._pre_click = (capture.id, list(self._selected))
        self._last_press = (capture.id, now)
        self.toggle_selection(capture)

    def _on_right(self, capture) -> None:
        """오른쪽 클릭 = 전송 · 삭제 · 멀티 메뉴. 그 사진을 아직 안 골랐으면 고르고(고른 순서 맨 뒤),
        메뉴의 동작은 고른 사진 전체에 적용된다."""
        if capture.id not in self._selected:
            self.toggle_selection(capture)
        self._menu_target = capture
        if self.right_click_action == "multi":
            self.multiRequested.emit(capture)        # 설정: 오른쪽 클릭 = 멀티 화면 바로
            return
        n = len(self.selected_captures())
        self._menu_title.setText(f"{n}장 선택" if n > 1 else "사진 1장")
        self.context_menu.popup(QCursor.pos())

    def set_menu_items(self, items) -> None:
        """오른쪽 클릭 메뉴에 보일 항목 (설정 '메뉴 항목': send · send_delete · multi · delete). 비면 기본."""
        keep = [k for k in (items or []) if k in self.menu_actions] or ["send", "multi", "delete"]
        for k, act in self.menu_actions.items():
            act.setVisible(k in keep)

    def menu_items(self) -> list:
        return [k for k, a in self.menu_actions.items() if a.isVisible()]

    def _menu_do(self, what: str) -> None:
        self.context_menu.hide()
        caps = self.selected_captures()
        if not caps:
            return
        if what == "send":
            self.sendRequested.emit(caps)
        elif what == "send_delete":
            self.sendDeleteRequested.emit(caps)
        elif what == "delete":
            self.deleteRequested.emit(caps)
        else:
            self.multiRequested.emit(self._menu_target or caps[-1])

    def _on_double(self, capture) -> None:
        # 두 번 클릭 = 큰 화면으로 보기. 클릭으로 바뀐 선택은 두 번 클릭 전 상태로 되돌린다
        pre, last = self._pre_click, self._last_press
        recent = bool(last and last[0] == capture.id
                      and time.monotonic() - last[1] <= QApplication.doubleClickInterval() / 1000.0 + 0.1)
        if recent and pre and pre[0] == capture.id and pre[1] != self._selected:
            self._selected = list(pre[1])
            self._apply_ranks()
            self.selectionChanged.emit(self.selected_captures())
        self._last_press = None
        self.thumbnailClicked.emit(capture)

    def visible_captures(self) -> list:
        f = self._filter
        return [c for c in self._items
                if f == "all" or (c.media_type == "video" if f == "video" else c.eye == f)]

    # ---- 내부 ----
    def _day_header(self, day: str, count: int) -> QWidget:
        """촬영일 제목 줄: 날짜 · (필터에 보이는) 장수 + 오른쪽 끝 '그 날 모두 삭제' 버튼.

        삭제는 필터와 무관하게 그 날 전체가 대상이라 풍선 도움말에는 전체 장수를 쓴다."""
        row = QWidget()
        hl = QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        h = QLabel(f"{day_label(day)}   ·   {count}장")
        h.setObjectName("DayHeader")
        hl.addWidget(h, 1)
        total = sum(1 for c in self._items if (c.captured_at or "")[:10] == day)
        b = QPushButton()
        b.setObjectName("DayDeleteBtn")
        b.setIcon(icons.icon("trash", ICON_RECORD, 14))
        b.setIconSize(QSize(13, 13))
        b.setFixedSize(22, 22)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(f"{day_label(day)} 촬영 {total}장 모두 삭제 (필터와 무관하게 그 날 전체)")
        b.clicked.connect(lambda _=False, d=day: self.dayDeleteRequested.emit(d))
        self.day_delete_buttons[day] = b
        hl.addWidget(b)
        return row

    def _rebuild(self) -> None:
        self._widgets = {}
        self.day_delete_buttons = {}
        while self._lay.count():
            w = self._lay.takeAt(0).widget()
            if w is not None and w is not self._empty:
                w.hide()                     # 지워지기 전까지 옛 자리에 그려지지 않게
                w.setParent(None)
                w.deleteLater()
        self._badge.setText(str(len(self._items)))
        shown = self.visible_captures()
        if not shown:
            self._empty.setText("환자를 선택하세요" if self._patient_id is None
                                else ("조건에 맞는 영상이 없습니다" if self._items else "촬영 이미지가 없습니다"))
            self._lay.addWidget(self._empty)
            self._empty.show()
            return
        self._empty.hide()
        full = shown
        total, shown = len(full), full[:self._limit]
        if self.split_eyes:
            self._build_by_eye(shown)
            self._add_more_button(total, len(shown))
            self._apply_ranks()
            return
        per_day: dict[str, int] = {}
        for c in full:                       # 제목 줄의 장수는 그날 전체 (아직 안 그린 것 포함)
            d = (c.captured_at or "")[:10]
            per_day[d] = per_day.get(d, 0) + 1
        day = None
        for c in shown:
            d = (c.captured_at or "")[:10]
            if d != day:
                day = d
                self._lay.addWidget(self._day_header(d, per_day[d]))
            self._add_card(c)
        self._add_more_button(total, len(shown))
        self._apply_ranks()

    def _add_more_button(self, total: int, drawn: int) -> None:
        if total <= drawn:
            return
        more = QPushButton(f"이전 {min(PAGE, total - drawn)}장 더 보기  ({drawn} / {total})")
        more.setObjectName("MoreBtn")
        more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        more.clicked.connect(self.show_more)
        self._lay.addWidget(more)

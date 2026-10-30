"""검사 기록 화면 — 촬영일별 검사 목록(왼쪽) · 그날의 영상 모음과 소견(가운데) · 환자 정보(오른쪽).

참고한 장비 프로그램의 환자 상세(Examinations · Description) 페이지를 따른다.
소견은 환자·촬영일마다 한 건(db.exam_notes)이며, 입력 칸을 벗어나거나 '저장'을 누르면 저장된다.

영상은 한 번 누르면 선택(노란 테두리 + 고른 순서), 다시 누르면 해제, 두 번 누르면 크게 본다.
'선택 저장' 은 고른 사진·동영상 원본만 고른 폴더에 복사하고, 인쇄 · PDF 도 고른 사진이 있으면 그 사진만 넣는다.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app import brand as app_brand
from app.services import send_service
from app.ui import icons
from app.ui.control_bar import tool_button
from app.ui.fit import HScroll
from app.ui.patient_panel import age_of
from app.ui.pro import print_util
from app.ui.theme import ICON, ICON_ACCENT, ICON_ON_ACCENT, ICON_RECORD
from app.ui.thumbnail_panel import day_label
from app.ui.thumbnail_widget import ThumbnailWidget

_SEX = {"M": "남", "F": "여", "O": "기타"}
THUMB_W, THUMB_H = 196, 147                  # 검사 기록 사진 칸 (4:3)

# 검사 종류 서식: 고르면 소견란이 비어 있을 때 틀을 채워 준다
EXAM_TYPES = [
    ("", ""),
    ("세극등 검사", "전안부: \n각막: \n전방: \n홍채/동공: \n수정체: \n"),
    ("각막", "상피: \n실질: \n내피: \n형광 염색: \n"),
    ("결막/공막", "충혈: \n결막 부종: \n분비물: \n"),
    ("안검/마이봄샘", "안검연: \n마이봄샘 개구부: \n분비물 성상: \n탈락 등급: \n"),
    ("건성안", "TBUT: \nSchirmer: \n각막 염색: \n"),
    ("수정체/백내장", "혼탁 위치: \n등급(LOCS): \n"),
    ("경과 관찰", "이전 대비: \n"),
    ("기타", ""),
]


def _esc(v) -> str:
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class ExamScreen(QWidget):
    openCapture = Signal(object)          # Capture 클릭 → 보기
    openInPro = Signal()                  # 'Pro 검토에서 열기'
    editPatient = Signal()
    deleteDay = Signal(str)               # 'YYYY-MM-DD' — '날짜 삭제': 이 날 영상 모두 삭제 요청 (실제 삭제는 메인 창)

    def __init__(self, db, config, parent=None):
        super().__init__(parent)
        self.db = db
        self.config = config
        self._patient = None
        self._caps: list = []
        self._days: list[str] = []
        self._day: str | None = None
        self._notes_dirty = False
        self._loading = False
        self._selected: list[int] = []        # 고른 영상 id (고른 순서)
        self._thumbs: dict[int, ThumbnailWidget] = {}
        self._last_click = None               # (영상 id, 시각) — 두 번 누름 판단용
        self._click_snapshot: list[int] | None = None   # 두 번 누르기 직전의 선택 (되돌리기용)
        self._build()

    # ---------------- 구성 ----------------
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        # ---- 왼쪽: 검사 기록 목록 ----
        left = QFrame()
        left.setObjectName("Panel")
        left.setFixedWidth(250)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 10, 12, 10)
        ll.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel("검사 기록")
        t.setObjectName("ThumbTitle")
        self.badge_days = QLabel("0")
        self.badge_days.setObjectName("CountBadge")
        head.addWidget(t)
        head.addWidget(self.badge_days)
        head.addStretch(1)
        self.btn_refresh = QPushButton()
        self.btn_refresh.setObjectName("HomeLink")
        self.btn_refresh.setIcon(icons.icon("history", ICON, 16))
        self.btn_refresh.setToolTip("다시 읽기")
        self.btn_refresh.clicked.connect(lambda: self.reload())
        head.addWidget(self.btn_refresh)
        ll.addLayout(head)
        self.day_list = QListWidget()
        self.day_list.setObjectName("ExamList")
        self.day_list.setSpacing(2)
        self.day_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.day_list.currentRowChanged.connect(self._on_day_row)
        ll.addWidget(self.day_list, 1)
        self.lbl_empty_days = QLabel("촬영 기록이 없습니다")
        self.lbl_empty_days.setObjectName("Muted")
        self.lbl_empty_days.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(self.lbl_empty_days)
        root.addWidget(left)

        # ---- 가운데: 영상 모음 + 소견 ----
        center = QVBoxLayout()
        center.setSpacing(12)
        gal = QFrame()
        gal.setObjectName("Panel")
        gl = QVBoxLayout(gal)
        gl.setContentsMargins(12, 10, 12, 8)
        gl.setSpacing(8)
        gh = QHBoxLayout()
        self.lbl_day = QLabel("촬영일을 선택하세요")
        self.lbl_day.setObjectName("ThumbTitle")
        self.lbl_day_meta = QLabel("")
        self.lbl_day_meta.setObjectName("Muted")
        # 장 수 · 시간 · 선택 수 글자는 좁은 창에서 줄어들어도 되게 (버튼이 눌려 겹치지 않도록)
        self.lbl_day_meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        gh.addWidget(self.lbl_day)
        gh.addSpacing(8)
        gh.addWidget(self.lbl_day_meta, 1)       # 남는 폭을 차지 (좁으면 줄어듦)
        self.btn_select_all = tool_button("전체 선택", "check-circle", "ToolBtn",
                                          tip="이 날의 영상을 모두 고르기 · 다시 누르면 선택 해제", icon_size=20, min_width=60)
        self.btn_save_sel = tool_button("선택 저장", "save", "ToolBtn",
                                        tip="고른 사진·동영상만 원하는 폴더에 저장 (사진을 한 번 눌러 고르세요)",
                                        icon_size=20, min_width=60, color=ICON_ACCENT)
        self.btn_pro = tool_button("Pro 검토", "review", "ToolBtn", tip="이 환자의 영상을 Pro 검토 화면에서 열기", icon_size=20, min_width=56)
        self.btn_print = tool_button("인쇄", "printer", "ToolBtn",
                                     tip="이 날의 영상(고른 사진이 있으면 그 사진만) + 소견을 보고서로 인쇄", icon_size=20, min_width=56)
        self.btn_pdf = tool_button("PDF", "file-pdf", "ToolBtn",
                                   tip="이 날의 영상(고른 사진이 있으면 그 사진만) + 소견을 PDF 보고서로 저장", icon_size=20, min_width=56)
        self.btn_delete_day = tool_button("날짜 삭제", "trash", "ToolBtn",
                                          tip="이 날 촬영한 사진 · 동영상을 모두 삭제 (소견은 남김)", icon_size=20, min_width=60,
                                          color=ICON_RECORD)
        self.btn_select_all.clicked.connect(lambda: self.toggle_select_all())
        self.btn_save_sel.clicked.connect(lambda: self.save_selected())
        self.btn_pro.clicked.connect(lambda: self.openInPro.emit())
        self.btn_print.clicked.connect(lambda: self.print_day())
        self.btn_pdf.clicked.connect(lambda: self.export_pdf_dialog())
        self.btn_delete_day.clicked.connect(self._request_delete_day)
        # 버튼 묶음: 좁은 창에서는 아이콘만 (이름은 풍선 도움말), 그래도 좁으면 옆으로 밀어 보기 — 글자 · 아이콘 겹침 방지
        self._head_buttons = (self.btn_select_all, self.btn_save_sel, self.btn_pro, self.btn_print, self.btn_pdf,
                              self.btn_delete_day)
        row = QWidget()
        row.setObjectName("ExamButtons")
        rl_ = QHBoxLayout(row)
        rl_.setContentsMargins(0, 0, 0, 0)
        rl_.setSpacing(6)
        for b in self._head_buttons:
            b.setProperty("full_min_w", b.minimumWidth())
            rl_.addWidget(b)
        self.head_buttons = HScroll(row, on_compact=self._compact_head_buttons)
        self.head_buttons.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        gh.addWidget(self.head_buttons)
        gl.addLayout(gh)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("ExamGrid")
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 6, 0)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.grid_host)
        self.scroll.viewport().installEventFilter(self)   # 폭이 바뀌면 한 줄 사진 수를 다시 계산
        gl.addWidget(self.scroll, 1)
        self.lbl_hint = QLabel("사진을 한 번 누르면 선택 · 다시 누르면 해제 · 두 번 누르면 크게 보기")
        self.lbl_hint.setObjectName("Muted")
        gl.addWidget(self.lbl_hint)
        self.lbl_empty = QLabel("이 날의 촬영 영상이 없습니다")
        self.lbl_empty.setObjectName("Muted")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gl.addWidget(self.lbl_empty)
        center.addWidget(gal, 3)

        desc = QFrame()
        desc.setObjectName("Panel")
        dl = QVBoxLayout(desc)
        dl.setContentsMargins(12, 10, 12, 10)
        dl.setSpacing(8)
        dh = QHBoxLayout()
        dt = QLabel("검사 소견")
        dt.setObjectName("ThumbTitle")
        dh.addWidget(dt)
        dh.addSpacing(12)
        dh.addWidget(QLabel("검사 종류"))
        self.cmb_type = QComboBox()
        for name, _tpl in EXAM_TYPES:
            self.cmb_type.addItem(name or "선택 안 함")
        self.cmb_type.setMinimumWidth(170)
        self.cmb_type.currentIndexChanged.connect(self._on_type_changed)
        dh.addWidget(self.cmb_type)
        dh.addStretch(1)
        self.lbl_saved = QLabel("")
        self.lbl_saved.setObjectName("Muted")
        dh.addWidget(self.lbl_saved)
        self.btn_save = QPushButton(icons.gap("저장"))
        self.btn_save.setObjectName("Primary")
        self.btn_save.setIcon(icons.icon("save", ICON_ON_ACCENT, 16))
        self.btn_save.setIconSize(QSize(15, 15))
        self.btn_save.clicked.connect(lambda: self.save_notes())
        dh.addWidget(self.btn_save)
        dl.addLayout(dh)
        self.txt_notes = QPlainTextEdit()
        self.txt_notes.setPlaceholderText("검사 소견·진단·계획을 적으세요. 칸을 벗어나면 자동 저장됩니다.")
        self.txt_notes.setMinimumHeight(96)
        self.txt_notes.textChanged.connect(self._on_notes_changed)
        self.txt_notes.installEventFilter(self)
        dl.addWidget(self.txt_notes, 1)
        center.addWidget(desc, 2)
        root.addLayout(center, 1)

        # ---- 오른쪽: 환자 정보 ----
        right = QFrame()
        right.setObjectName("Panel")
        right.setFixedWidth(250)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 10, 12, 10)
        rl.setSpacing(8)
        rh = QHBoxLayout()
        rt = QLabel("환자 정보")
        rt.setObjectName("ThumbTitle")
        rh.addWidget(rt)
        rh.addStretch(1)
        self.btn_edit = QPushButton()
        self.btn_edit.setObjectName("HomeLink")
        self.btn_edit.setIcon(icons.icon("user-edit", ICON, 16))
        self.btn_edit.setToolTip("정보 수정")
        self.btn_edit.clicked.connect(lambda: self.editPatient.emit())
        rh.addWidget(self.btn_edit)
        rl.addLayout(rh)
        self.lbl_patient = QLabel()
        self.lbl_patient.setObjectName("ExamPatient")
        self.lbl_patient.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_patient.setWordWrap(True)
        self.lbl_patient.setAlignment(Qt.AlignmentFlag.AlignTop)
        rl.addWidget(self.lbl_patient, 1)
        root.addWidget(right)

        self._render_patient()
        self._set_notes_enabled(False)

    # ---------------- 공개 API ----------------
    def set_patient(self, patient) -> None:
        self.flush()
        if patient is None or self._patient is None or patient.id != self._patient.id:
            self._selected = []
        self._patient = patient
        self._render_patient()
        self.reload()

    def reload(self, keep_day: bool = True) -> None:
        """DB 에서 다시 읽어 촬영일 목록과 현재 날짜의 영상을 갱신."""
        prev = self._day if keep_day else None
        self._caps = (self.db.captures.list_by_patient(self._patient.id, order="desc")
                      if self._patient else [])
        days: list[str] = []
        for c in self._caps:
            d = (c.captured_at or "")[:10]
            if d and d not in days:
                days.append(d)
        self._days = days
        notes = self.db.exam_notes.list_by_patient(self._patient.id) if self._patient else {}
        self.day_list.blockSignals(True)
        self.day_list.clear()
        for d in days:
            caps = [c for c in self._caps if (c.captured_at or "")[:10] == d]
            n_od = sum(1 for c in caps if c.eye == "OD")
            n_os = sum(1 for c in caps if c.eye == "OS")
            n_vid = sum(1 for c in caps if c.media_type == "video")
            bits = [f"{len(caps)}장"]
            if n_od:
                bits.append(f"OD {n_od}")
            if n_os:
                bits.append(f"OS {n_os}")
            if n_vid:
                bits.append(f"동영상 {n_vid}")
            note = notes.get(d)
            tag = f"  ·  {note['exam_type']}" if note and note.get("exam_type") else ""
            it = QListWidgetItem(f"{day_label(d)}{tag}\n" + " · ".join(bits))
            it.setData(Qt.ItemDataRole.UserRole, d)
            it.setIcon(icons.icon("check-circle" if note and (note.get("notes") or note.get("exam_type")) else "clipboard",
                                  ICON_ACCENT if note else ICON, 18))
            it.setSizeHint(QSize(0, 48))
            self.day_list.addItem(it)
        self.day_list.blockSignals(False)
        self.badge_days.setText(str(len(days)))
        self.lbl_empty_days.setVisible(not days)
        if days:
            row = days.index(prev) if prev in days else 0
            self.day_list.setCurrentRow(row)
            self._show_day(days[row])
        else:
            self._show_day(None)

    def current_day(self) -> str | None:
        return self._day

    def day_captures(self) -> list:
        return [c for c in self._caps if self._day and (c.captured_at or "")[:10] == self._day]

    def _request_delete_day(self) -> None:
        if self._day and self.day_captures():
            self.deleteDay.emit(self._day)

    def flush(self) -> None:
        """바뀐 소견이 있으면 저장 (화면·환자·날짜 전환 전에 부른다)."""
        if self._notes_dirty and self._patient and self._day:
            self.save_notes()

    def save_notes(self) -> None:
        if not (self._patient and self._day):
            return
        idx = self.cmb_type.currentIndex()
        exam_type = EXAM_TYPES[idx][0] if 0 <= idx < len(EXAM_TYPES) else ""
        self.db.exam_notes.upsert(self._patient.id, self._day, exam_type, self.txt_notes.toPlainText())
        self._notes_dirty = False
        self.lbl_saved.setText(f"저장됨 {datetime.now():%H:%M}")
        self._refresh_day_item()

    # ---------------- 내부 ----------------
    def _on_day_row(self, row: int) -> None:
        if 0 <= row < len(self._days):
            self.flush()
            self._show_day(self._days[row])

    def grid_columns(self) -> int:
        """한 줄에 놓을 사진 수 — 영상 칸 폭에 맞춰 (최대 6)."""
        avail = self.scroll.viewport().width() - self.grid.contentsMargins().right()
        return max(1, min(6, (avail + self.grid.spacing()) // (THUMB_W + self.grid.spacing())))

    def _layout_grid(self) -> None:
        cols = self.grid_columns()
        widgets = list(self._thumbs.values())
        if getattr(self, "_grid_cols", None) == cols and self.grid.count() == len(widgets):
            return
        self._grid_cols = cols
        for w in widgets:
            self.grid.removeWidget(w)
        for i, w in enumerate(widgets):
            self.grid.addWidget(w, i // cols, i % cols)

    def _compact_head_buttons(self, on: bool) -> None:
        for b in self._head_buttons:
            b.set_text_visible(not on)
            b.setMinimumWidth(40 if on else int(b.property("full_min_w") or 56))
        row = self._head_buttons[0].parentWidget()
        if row is not None and row.layout() is not None:
            row.layout().invalidate()
            row.updateGeometry()

    # ---------------- 영상 선택 · 선택 저장 ----------------
    def selected_captures(self) -> list:
        """고른 영상 (고른 순서)."""
        by_id = {c.id: c for c in self.day_captures()}
        return [by_id[i] for i in self._selected if i in by_id]

    def toggle_select(self, cap) -> None:
        if cap.id in self._selected:
            self._selected.remove(cap.id)
        else:
            self._selected.append(cap.id)
        self._sync_selection()

    def toggle_select_all(self) -> None:
        caps = self.day_captures()
        if caps and len(self._selected) == len(caps):
            self._selected = []
        else:
            self._selected = [c.id for c in reversed(caps)]   # 촬영 순서대로
        self._sync_selection()

    def clear_selection(self) -> None:
        self._selected = []
        self._sync_selection()

    def _on_thumb_click(self, cap) -> None:
        now = time.monotonic()
        last = self._last_click
        if not (last and last[0] == cap.id and now - last[1] < QApplication.doubleClickInterval() / 1000):
            self._click_snapshot = list(self._selected)     # 새로 누르기 시작 — 지금 선택을 기억
        self._last_click = (cap.id, now)
        self.toggle_select(cap)

    def _on_thumb_double(self, cap) -> None:
        # 두 번 누르기의 첫 누름이 바꾼 선택은 되돌리고 크게 보기 (같은 사진을 방금 누른 경우만)
        last = self._last_click
        recent = last and last[0] == cap.id and time.monotonic() - last[1] < QApplication.doubleClickInterval() / 1000
        if recent and self._click_snapshot is not None:
            self._selected = list(self._click_snapshot)
            self._sync_selection()
        self._last_click = None
        self.openCapture.emit(cap)

    def _sync_selection(self) -> None:
        for cid, w in self._thumbs.items():
            w.set_rank(self._selected.index(cid) + 1 if cid in self._selected else 0)
        n = len(self._selected)
        caps = self.day_captures()
        self.btn_save_sel.setText(f"저장 ({n})" if n else "선택 저장")
        self.btn_save_sel.setEnabled(n > 0)
        self.btn_select_all.setText("선택 해제" if caps and n == len(caps) else "전체 선택")
        self.btn_select_all.setEnabled(bool(caps))
        self._update_day_meta()

    def save_selected(self, folder: str | None = None) -> list:
        """고른 사진·동영상 원본만 폴더에 복사. 저장한 파일 경로 목록."""
        caps = self.selected_captures()
        if not caps:
            QMessageBox.information(self, "선택 저장", "저장할 사진을 먼저 한 번씩 눌러 고르세요.")
            return []
        if folder is None:
            start = getattr(self.config, "export_root", "") or os.path.expanduser("~")
            folder = QFileDialog.getExistingDirectory(self, f"고른 {len(caps)}장을 저장할 폴더", start)
            if not folder:
                return []
        try:
            saved = send_service.save_to_folder(caps, self._patient, folder)
        except OSError as e:
            QMessageBox.warning(self, "선택 저장", f"저장하지 못했습니다.\n{e}")
            return []
        if len(saved) < len(caps):
            QMessageBox.warning(self, "선택 저장", f"{len(caps)}장 중 {len(saved)}장을 저장했습니다 "
                                f"(원본 파일이 없는 영상은 건너뜀).\n{folder}")
        else:
            QMessageBox.information(self, "선택 저장", f"고른 {len(saved)}장을 저장했습니다.\n{folder}")
        return saved

    def _update_day_meta(self) -> None:
        caps = self.day_captures()
        if self._day is None or not caps:
            return
        first = caps[-1].captured_at[11:16] if caps[-1].captured_at else ""
        last = caps[0].captured_at[11:16] if caps[0].captured_at else ""
        span = f"{first} ~ {last}" if first and last and first != last else first
        n = len(self.selected_captures())
        self.lbl_day_meta.setText(f"{len(caps)}장" + (f"  ·  {span}" if span else "") + (f"  ·  선택 {n}장" if n else ""))

    def _show_day(self, day: str | None) -> None:
        if day != self._day:
            self._selected = []              # 다른 날로 옮기면 선택 초기화
        self._day = day
        caps = self.day_captures()
        ids = {c.id for c in caps}
        self._selected = [i for i in self._selected if i in ids]   # 지워진 영상 · 다른 환자 영상은 빼기
        self._thumbs = {}
        # 영상 격자
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for c in caps:
            w = ThumbnailWidget(c)
            w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            w.setFixedSize(THUMB_W, THUMB_H)         # 4:3 고정 (resizeEvent 의 높이 재계산이 되돌이표가 되지 않게)
            w.clicked.connect(self._on_thumb_click)  # 한 번 누르면 선택 · 해제
            w.doubleClicked.connect(self._on_thumb_double)
            self._thumbs[c.id] = w
        self._layout_grid()
        self.lbl_empty.setVisible(day is not None and not caps)
        self.scroll.setVisible(bool(caps))
        self.lbl_hint.setVisible(bool(caps))
        if day is None:
            self.lbl_day.setText("촬영일을 선택하세요" if self._patient else "환자를 선택하세요")
            self.lbl_day_meta.setText("")
        else:
            self.lbl_day.setText(day_label(day))
            self.lbl_day_meta.setText("")            # 장 수 · 시간 · 선택 수는 _sync_selection 에서
        for b in (self.btn_print, self.btn_pdf):
            b.setEnabled(any(c.media_type == "photo" for c in caps))
        self.btn_pro.setEnabled(bool(self._patient))
        self._sync_selection()
        # 소견
        self._loading = True
        note = self.db.exam_notes.get(self._patient.id, day) if (self._patient and day) else None
        names = [n for n, _ in EXAM_TYPES]
        self.cmb_type.setCurrentIndex(names.index(note["exam_type"]) if note and note.get("exam_type") in names else 0)
        self.txt_notes.setPlainText((note or {}).get("notes") or "")
        self.lbl_saved.setText(f"저장됨 {note['updated_at'][5:16]}" if note and note.get("updated_at") else "")
        self._loading = False
        self._notes_dirty = False
        self._set_notes_enabled(day is not None)

    def _set_notes_enabled(self, on: bool) -> None:
        for w in (self.cmb_type, self.txt_notes, self.btn_save):
            w.setEnabled(on)

    def _on_type_changed(self, idx: int) -> None:
        if self._loading:
            return
        tpl = EXAM_TYPES[idx][1] if 0 <= idx < len(EXAM_TYPES) else ""
        if tpl and not self.txt_notes.toPlainText().strip():
            self.txt_notes.setPlainText(tpl)
        self._notes_dirty = True
        self.lbl_saved.setText("수정 중…")

    def _on_notes_changed(self) -> None:
        if self._loading:
            return
        self._notes_dirty = True
        self.lbl_saved.setText("수정 중…")

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        t = event.type()
        if t == event.Type.FocusOut and obj is getattr(self, "txt_notes", None):
            self.flush()
        elif t == event.Type.Resize and hasattr(self, "scroll") and obj is self.scroll.viewport():
            self._layout_grid()
        return super().eventFilter(obj, event)

    def _refresh_day_item(self) -> None:
        row = self.day_list.currentRow()
        if row < 0 or self._day is None:
            return
        note = self.db.exam_notes.get(self._patient.id, self._day)
        it = self.day_list.item(row)
        if it is None:
            return
        text = it.text().split("\n", 1)
        tag = f"  ·  {note['exam_type']}" if note and note.get("exam_type") else ""
        it.setText(f"{day_label(self._day)}{tag}\n{text[1] if len(text) > 1 else ''}")
        it.setIcon(icons.icon("check-circle" if note and (note.get("notes") or note.get("exam_type")) else "clipboard",
                              ICON_ACCENT if note else ICON, 18))

    def _render_patient(self) -> None:
        p = self._patient
        self.btn_edit.setEnabled(p is not None)
        if p is None:
            self.lbl_patient.setText("<span style='color:#8b95a3'>환자를 선택하세요</span>")
            return
        age = age_of(p.birth_date)
        rows = [
            ("이름", f"<b>{_esc(p.name)}</b>"),
            ("차트번호", _esc(p.chart_no or "-")),
            ("환자ID", _esc(p.patient_id or "-")),
            ("성별 · 나이", " · ".join(x for x in (_SEX.get(p.sex or "", ""), f"{age}세" if age is not None else "") if x) or "-"),
            ("생년월일", _esc(p.birth_date or "-")),
            ("연락처", _esc(p.phone or "-")),
            ("진단명", f"<b>{_esc(p.diagnosis or '-')}</b>"),
            ("담당의", _esc(p.doctor or "-")),
            ("증상", _esc(p.symptoms or "-").replace("\n", "<br>")),
            ("메모", _esc(p.memo or "-").replace("\n", "<br>")),
        ]
        html = "".join(f"<div style='margin:0 0 9px'><span style='color:#8b95a3;font-size:11px'>{k}</span><br>{v}</div>"
                       for k, v in rows)
        self.lbl_patient.setText(html)

    # ---------------- 보고서 ----------------
    def report_items(self) -> list:
        from app.ui.pro.pro_workspace import caption_of, load_bgr
        items = []
        chosen = self.selected_captures()                 # 고른 사진이 있으면 그 사진만 (고른 순서)
        for c in chosen or reversed(self.day_captures()):  # 없으면 이 날 전부 (촬영 순서대로)
            if c.media_type != "photo":
                continue
            img = load_bgr(c.file_path)
            if img is not None:
                items.append((img, caption_of(c)))
        return items

    def _report_kwargs(self) -> dict:
        b = app_brand.current()
        return dict(clinic=getattr(self.config, "clinic_name", "") or "", title=b["report_title"],
                    rule_color=b.get("report_rule", "#0d9488"), notes=self._notes_for_report())

    def _notes_for_report(self) -> str:
        idx = self.cmb_type.currentIndex()
        t = EXAM_TYPES[idx][0] if 0 <= idx < len(EXAM_TYPES) else ""
        body = self.txt_notes.toPlainText().strip()
        return (f"[{t}]\n" if t else "") + body

    def export_pdf(self, path: str) -> int:
        items = self.report_items()
        if not items:
            return 0
        printer = print_util.make_pdf_printer(path)
        return print_util.render_report(printer, self._patient, items, **self._report_kwargs())

    def export_pdf_dialog(self) -> None:
        if not self.report_items():
            QMessageBox.information(self, "PDF", "출력할 사진이 없습니다.")
            return
        base = getattr(self.config, "export_root", "") or os.path.expanduser("~")
        chart = (self._patient.chart_no if self._patient else None) or "report"
        default = os.path.join(base, f"{chart}_{(self._day or '').replace('-', '')}_검사기록.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "PDF 보고서 저장", default, "PDF (*.pdf)")
        if path:
            n = self.export_pdf(path)
            QMessageBox.information(self, "PDF", f"{n}쪽 보고서를 저장했습니다.\n{path}")

    def print_day(self) -> None:
        items = self.report_items()
        if not items:
            QMessageBox.information(self, "인쇄", "출력할 사진이 없습니다.")
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if QPrintDialog(printer, self).exec():
            print_util.render_report(printer, self._patient, items, **self._report_kwargs())

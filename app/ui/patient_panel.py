"""환자 검색 · 선택 칸 (상단바의 '정보 수정' 바로 옆에 들어간다).

환자 요약(이름·차트번호·나이 등)은 실시간 화면 위에 이미 보이므로 화면에 따로 띄우지 않는다
(요약 라벨은 다른 화면·테스트에서 쓰도록 만들어만 두고 숨김).
신규 등록·정보 수정 버튼은 상단바에 있고 new_patient()/edit_patient() 를 부른다."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QModelIndex, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from app.ui import icons
from app.ui.patient_dialog import PatientDialog
from app.ui.theme import ICON

_SEX = {"M": "남", "F": "여", "O": "기타"}


def _esc(v) -> str:
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def age_of(birth: str | None, today: date | None = None) -> int | None:
    """'YYYY-MM-DD' → 만 나이. 형식이 다르면 None."""
    try:
        y, m, d = (int(x) for x in (birth or "")[:10].split("-"))
        b = date(y, m, d)
    except (ValueError, TypeError):
        return None
    t = today or date.today()
    a = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    return a if 0 <= a < 150 else None


class PatientPanel(QFrame):
    patientChanged = Signal(object)  # Patient | None

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._patients = []
        self.setObjectName("PatientPick")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.summary = QFrame()                       # 숨김: 환자 요약 (실시간 화면 HUD 에 표시됨)
        sum_lay = QHBoxLayout(self.summary)
        sum_lay.setContentsMargins(0, 0, 0, 0)
        self.summary.setVisible(False)

        self.avatar = QLabel()
        self.avatar.setObjectName("Avatar")
        self.avatar.setFixedSize(38, 38)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sum_lay.addWidget(self.avatar)
        box = QVBoxLayout()
        box.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.lbl_name = QLabel()
        self.lbl_name.setObjectName("PatientName")
        self.lbl_chart = QLabel()
        self.lbl_chart.setObjectName("ChartNo")
        row.addWidget(self.lbl_name)
        row.addWidget(self.lbl_chart)
        row.addStretch(1)
        self.lbl_meta = QLabel()
        self.lbl_meta.setObjectName("PatientMeta")
        self.lbl_meta.setTextFormat(Qt.TextFormat.RichText)
        box.addLayout(row)
        box.addWidget(self.lbl_meta)
        sum_lay.addLayout(box, 1)
        lay.addWidget(self.summary)

        self.search = QLineEdit()
        self.search.setPlaceholderText("환자 검색")
        self.search.setToolTip("이름 · 차트번호 · 연락처로 검색")
        self.search.setFixedWidth(170)
        self.search.addAction(icons.icon("search", ICON, 16), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        # 환자 선택 목록(combo)은 화면에 두지 않는다 — 검색 칸에 치면 바로 아래에 결과가 뜨고 누르거나 Enter 로 고른다.
        # (combo 는 '지금 환자' 를 담는 내부 목록으로 남겨 둠 — 다른 화면 · 시험이 같은 방식으로 쓴다)
        self.combo = QComboBox(self)
        self.combo.setToolTip("환자 선택")
        self.combo.hide()
        self._matches: list = []
        self._results = QStringListModel(self)
        self.completer = QCompleter(self._results, self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setMaxVisibleItems(12)
        self.completer.setWidget(self.search)
        self.completer.activated[QModelIndex].connect(lambda ix: self._pick_match(ix.row()))
        lay.addWidget(self.search)

        self.search.textChanged.connect(self._reload)
        self.search.returnPressed.connect(self._pick_first)
        self.combo.currentIndexChanged.connect(self._on_select)

        self._reload()

    def _render_banner(self, p) -> None:
        if p is None:
            self.avatar.setPixmap(icons.pixmap("user", ICON, 18))
            self.lbl_name.setText("환자를 선택하세요")
            self.lbl_chart.setText("")
            self.lbl_meta.setText("오른쪽에서 검색하거나 상단의 '신규 환자' 를 누르세요")
            return
        self.avatar.setPixmap(QPixmap())
        self.avatar.setText((p.name or "?").strip()[:1])
        self.lbl_name.setText(p.name)
        self.lbl_chart.setText(f"#{p.chart_no}" if p.chart_no else "")
        mute = "color:#8b95a3"
        bits = []
        age = age_of(getattr(p, "birth_date", None))
        sa = " · ".join(x for x in (_SEX.get(p.sex or "", ""), f"{age}세" if age is not None else "") if x)
        if sa:
            bits.append(sa)
        if getattr(p, "birth_date", None):
            bits.append(f"<span style='{mute}'>생년월일</span> {_esc(p.birth_date)}")
        bits.append(f"<span style='{mute}'>진단명</span> <b>{_esc(getattr(p, 'diagnosis', None) or '-')}</b>")
        bits.append(f"<span style='{mute}'>담당의</span> {_esc(getattr(p, 'doctor', None) or '-')}")
        self.lbl_meta.setText("&nbsp;&nbsp;&nbsp;".join(bits))

    def select_patient(self, pid: int) -> bool:
        """환자 목록 화면 등에서 고른 환자로 바꾼다 (검색어는 비움)."""
        self.search.blockSignals(True)
        self.search.setText("")
        self.search.blockSignals(False)
        self._reload()
        for i, p in enumerate(self._patients):
            if p.id == pid:
                if self.combo.currentIndex() != i:
                    self.combo.setCurrentIndex(i)
                return True
        return False

    def current_patient(self):
        idx = self.combo.currentIndex()
        if 0 <= idx < len(self._patients):
            return self._patients[idx]
        return None

    def refresh_current(self, patient) -> None:
        """현재 선택된 환자 정보만 갱신 (선택 유지)."""
        idx = self.combo.currentIndex()
        if 0 <= idx < len(self._patients) and self._patients[idx].id == patient.id:
            self._patients[idx] = patient
            self.combo.setItemText(idx, f"{patient.name} ({patient.chart_no or '-'})")
            self._render_banner(patient)

    def _pick_match(self, row: int) -> None:
        """검색 결과 목록에서 고름 → 그 환자로 바꾸고 검색 칸을 비운다."""
        if 0 <= row < len(self._matches):
            pid = self._matches[row].id
            QTimer.singleShot(0, lambda: self.select_patient(pid))   # 자동 완성이 칸에 글자를 넣은 뒤 비운다

    def _pick_first(self) -> None:
        """Enter: 결과 목록에서 고른 줄(없으면 첫 결과)로."""
        if not self.search.text().strip() or not self._matches:
            return
        popup = self.completer.popup()
        row = popup.currentIndex().row() if popup.isVisible() and popup.currentIndex().isValid() else 0
        self.completer.popup().hide()
        self.select_patient(self._matches[max(0, row)].id)

    def _show_matches(self, q: str) -> None:
        self._results.setStringList([f"{p.name} ({p.chart_no or '-'})" for p in self._matches])
        if q and self._matches and self.search.hasFocus():
            self.completer.complete()
        else:
            self.completer.popup().hide()

    def _update_placeholder(self, p) -> None:
        self.search.setPlaceholderText(f"환자: {p.name} ({p.chart_no or '-'})" if p is not None else "환자 검색")
        self.search.setToolTip(("지금 환자: " + f"{p.name} ({p.chart_no or '-'})\n" if p is not None else "")
                               + "이름 · 차트번호 · 연락처로 검색 — 결과를 누르거나 Enter 로 고르기")

    def _reload(self, *_):
        q = self.search.text().strip()
        prev = self.current_patient() if hasattr(self, "_patients") else None
        self._patients = list(self.db.patients.search(q) if q else self.db.patients.list_all())
        self._matches = list(self._patients) if q else []
        # 검색어를 쳐도 지금 환자 선택은 유지 (검색 결과에 없으면 맨 앞에 남겨 둔다) — 다른 환자로 촬영되는 일 방지
        if prev is not None and q and not any(p.id == prev.id for p in self._patients):
            self._patients.insert(0, prev)
        self.combo.blockSignals(True)
        self.combo.clear()
        for p in self._patients:
            self.combo.addItem(f"{p.name} ({p.chart_no or '-'})")
        sel = next((i for i, p in enumerate(self._patients) if prev is not None and p.id == prev.id), 0)
        if self._patients:
            self.combo.setCurrentIndex(sel)
        self.combo.blockSignals(False)
        self._show_matches(q)
        self._on_select()

    def _on_select(self, *_):
        p = self.current_patient()
        self._render_banner(p)
        self._update_placeholder(p)
        self.patientChanged.emit(p)

    def new_patient(self) -> bool:
        """신규 환자 등록 (상단바 버튼·단축키 N). 저장되어 선택되면 True."""
        dlg = PatientDialog(self.window(), db=self.db)
        if not dlg.exec():
            return False
        if dlg.existing is not None:                 # 같은 차트번호 → 기존 환자에 이어서
            pid = dlg.existing.id
        else:
            p = dlg.get_patient()
            try:
                pid = p.id = self.db.patients.insert(p)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "오류", f"저장 실패: {e}\n(차트번호 중복 여부를 확인하세요)")
                return False
        self.search.blockSignals(True)
        self.search.setText("")
        self.search.blockSignals(False)
        self._reload()
        for i, pp in enumerate(self._patients):
            if pp.id == pid:
                self.combo.setCurrentIndex(i)
                break
        return True

    def edit_patient(self):
        """현재 환자 정보 수정 (상단바 버튼)."""
        p = self.current_patient()
        if not p:
            QMessageBox.information(self, "확인", "수정할 환자를 선택하세요.")
            return
        dlg = PatientDialog(self.window(), patient=p, db=self.db)
        if not dlg.exec():
            return
        try:
            self.db.patients.update(dlg.get_patient())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "오류", f"수정 실패: {e}")
            return
        self._reload()

    # 이전 이름 호환
    _new_patient = new_patient
    _edit_patient = edit_patient

"""설정 → '백업' 탭 · 상태줄 백업 버튼 (app/services/backup_service.py 의 BackupManager 를 쓴다).

- 백업 위치(외장 하드 · 다른 드라이브의 폴더) 고르기 · 연결 상태 · 여유 공간 · 사진 저장 드라이브와 같은 드라이브면 경고.
- 자동 백업(저장 즉시 복사 + 켤 때 · 주기적으로 빠진 파일 확인) / 끄면 수동, 확인 주기, 닫을 때 마무리.
- 지금 백업 · 중지 · 새 백업 폴더 시작 · 백업 폴더 열기, 진행 막대, 이 위치의 백업 폴더 목록(시작 ~ 마지막 날짜).
값 저장은 설정 창 '확인' 때 (config.backup_*). '지금 백업' · '새 백업 폴더 시작' 은 칸에 적힌 위치로 바로 한다.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.services import backup_service as bs

_LEVEL_COLOR = {"ok": "#3ccf7a", "warn": "#ffb454", "missing": "#ffb454", "error": "#f0524f", "unset": ""}


class BackupStatusButton(QToolButton):
    """상태줄 오른쪽 '백업 ✓ 14:32' · '백업 드라이브 없음' … — 누르면 설정 → 백업."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("BackupStatus")
        self.setAutoRaise(True)
        self.setProperty("level", "unset")

    def set_status(self, st: dict) -> None:
        self.setText(st.get("text", ""))
        self.setToolTip(st.get("tip", "") + "\n(누르면 설정 → 백업)")
        level = st.get("level", "unset")
        if self.property("level") != level:
            self.setProperty("level", level)
            self.style().unpolish(self)
            self.style().polish(self)


class BackupTab(QWidget):
    def __init__(self, config, manager=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.manager = manager
        lay = QVBoxLayout(self)
        intro = QLabel("사진 · 동영상 · 환자 기록(DB) · 참조 영상 · 설정을 외장 하드나 다른 드라이브에 백업합니다. 백업 폴더 이름은 "
                       "백업을 시작한 날짜와 마지막으로 백업한 날짜입니다 (YYMMDDYYMMDD) — 예: 2021년 9월 8일에 시작해 "
                       "2026년 10월 28일까지 백업했으면 '210908261028'. 날짜가 바뀐 뒤 처음 백업할 때 뒤의 날짜가 바뀝니다.")
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("백업 위치"), 0, 0)
        self.e_root = QLineEdit(getattr(config, "backup_root", "") or "")
        self.e_root.setPlaceholderText(f"예: {bs.EXAMPLE_ROOT}  (외장 하드 · 다른 드라이브)")
        grid.addWidget(self.e_root, 0, 1)
        self.b_browse = QPushButton("폴더 선택")
        grid.addWidget(self.b_browse, 0, 2)
        self.lbl_drive = QLabel()
        self.lbl_drive.setWordWrap(True)
        grid.addWidget(self.lbl_drive, 1, 1, 1, 2)
        lay.addLayout(grid)

        self.chk_auto = QCheckBox("자동 백업 — 사진 · 동영상을 저장하는 즉시 백업 위치에 복사 (실시간), 켤 때 · 아래 주기로 빠진 "
                                  "파일 확인 (끄면 '지금 백업' 으로 수동)")
        self.chk_auto.setChecked(bool(getattr(config, "backup_auto", True)))
        lay.addWidget(self.chk_auto)
        row = QHBoxLayout()
        row.addWidget(QLabel("빠진 파일 확인"))
        self.c_interval = QComboBox()
        for mins, text in bs.INTERVALS:
            self.c_interval.addItem(text, mins)
        i = self.c_interval.findData(int(getattr(config, "backup_interval_min", 60) or 60))
        self.c_interval.setCurrentIndex(i if i >= 0 else 1)
        row.addWidget(self.c_interval)
        row.addStretch(1)
        lay.addLayout(row)
        self.chk_exit = QCheckBox("프로그램을 닫을 때 남은 백업 마무리 (환자 기록 DB · 못 한 파일)")
        self.chk_exit.setChecked(bool(getattr(config, "backup_on_exit", True)))
        lay.addWidget(self.chk_exit)

        btns = QHBoxLayout()
        self.b_now = QPushButton("지금 백업")
        self.b_now.setToolTip("새 파일 · 바뀐 파일만 복사합니다 (처음에는 모두)")
        self.b_stop = QPushButton("중지")
        self.b_new = QPushButton("새 백업 폴더 시작")
        self.b_new.setToolTip("지금 백업 폴더는 그대로 두고, 오늘 날짜로 새 폴더를 만들어 처음부터 다시 백업합니다")
        self.b_open = QPushButton("백업 폴더 열기")
        for b in (self.b_now, self.b_stop, self.b_new, self.b_open):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        lay.addWidget(self.bar)
        self.lbl_state = QLabel("")
        self.lbl_state.setObjectName("Muted")
        self.lbl_state.setWordWrap(True)
        lay.addWidget(self.lbl_state)

        head = QLabel("이 위치의 백업 폴더")
        head.setObjectName("ThumbTitle")
        lay.addWidget(head)
        self.list_sets = QListWidget()
        self.list_sets.setMinimumHeight(90)
        lay.addWidget(self.list_sets, 1)
        note = QLabel(f"· 프로그램에서 지운 사진도 백업에는 남깁니다. 날짜별 환자 기록은 각 폴더의 db{os.sep}history 에 있습니다.\n"
                      "· 외장 하드를 여러 개 번갈아 써도 하드마다 자기 폴더가 이어집니다. 하드가 빠져 있던 동안 저장한 사진은 "
                      "다시 꽂으면 이어서 복사합니다.\n"
                      "· 복원 방법은 각 백업 폴더의 '복원 방법.txt' 에 있습니다.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.b_browse.clicked.connect(self._browse)
        self.e_root.textChanged.connect(self._on_root_edited)
        self.b_now.clicked.connect(self._backup_now)
        self.b_stop.clicked.connect(self._stop)
        self.b_new.clicked.connect(self._new_set)
        self.b_open.clicked.connect(self._open)
        if manager is not None:                      # 메서드로 연결 → 설정 창이 닫혀 지워지면 연결도 끊긴다
            manager.progress.connect(self._on_progress)
            manager.statusChanged.connect(self._on_status)
            manager.finished.connect(self._on_finished)
        self.refresh()

    # ---- 값 ----
    def root(self) -> str:
        return self.e_root.text().strip()

    def values(self) -> dict:
        return {"backup_root": self.root(), "backup_auto": self.chk_auto.isChecked(),
                "backup_interval_min": int(self.c_interval.currentData() or 60),
                "backup_on_exit": self.chk_exit.isChecked()}

    def problem(self) -> str | None:
        """확인 · 지금 백업을 막을 문제 (사진 폴더 안 · 백업 폴더 자체를 고름). 연결 안 됨 · 같은 드라이브는 막지 않는다."""
        level, text = bs.describe_target(self.root(), self.config.media_root)
        return text if level == "error" else None

    # ---- 화면 ----
    def refresh(self) -> None:
        root = self.root()
        level, text = bs.describe_target(root, self.config.media_root)
        col = _LEVEL_COLOR.get(level, "")
        self.lbl_drive.setText(text)
        self.lbl_drive.setStyleSheet(f"color: {col};" if col else "")
        mgr = self.manager
        running = bool(mgr and mgr.is_running())
        usable = bool(root) and level in ("ok", "warn") and mgr is not None
        self.b_now.setEnabled(usable and not running)
        self.b_new.setEnabled(usable and not running)
        self.b_stop.setEnabled(running)
        self.b_open.setEnabled(bool(root) and Path(root).is_dir())
        sets = bs.list_sets(root) if level in ("ok", "warn") else []
        mine = getattr(self.config, "backup_set_id", "")
        self.list_sets.clear()
        for s in reversed(sets):
            who = "  ← 이 PC 의 지금 백업 폴더" if s.set_id == mine else ""
            last = s.last_at.replace("T", " ")[:16] if s.last_at else s.last.isoformat()
            self.list_sets.addItem(f"{s.name}    {s.start:%Y-%m-%d} ~ {s.last:%Y-%m-%d}  (마지막 {last}){who}")
        if mgr is not None:
            st = mgr.status()
            msg = st.get("text", "")
            if st.get("message"):
                msg += " — " + st["message"]
            if not running and st.get("state") in ("ok", "idle") and not st.get("message"):
                cur = next((s for s in sets if s.set_id == mine), None)
                if cur is not None:
                    msg = f"지금 백업 폴더: {cur.name} ({cur.start:%Y-%m-%d} 시작) · {st.get('text', '')}"
            self.lbl_state.setText(msg)
            if not running:
                self.bar.setRange(0, 1)
                self.bar.setValue(1 if st.get("state") == "ok" else 0)
        elif not mgr:
            self.lbl_state.setText("백업은 촬영 창에서 합니다")

    def _on_root_edited(self, _text: str) -> None:
        self.refresh()

    # 설정 창은 닫혀도 지워지지 않고 남는다(부모 = 창) → 보이지 않을 때는 디스크를 읽는 refresh 를 하지 않는다
    def _on_status(self, _st: dict) -> None:
        if self.isVisible():
            self.refresh()

    def _on_finished(self, _res: dict) -> None:
        if self.isVisible():
            self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _on_progress(self, done: float, total: float, text: str) -> None:
        if not self.isVisible():
            return
        if total <= 0:
            self.bar.setRange(0, 0)                  # 확인 중 (움직이는 막대)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * done / total))
        self.lbl_state.setText(text)
        self.b_stop.setEnabled(True)
        self.b_now.setEnabled(False)
        self.b_new.setEnabled(False)

    # ---- 버튼 ----
    def _stop(self) -> None:
        if self.manager is not None:
            self.manager.cancel()
            self.lbl_state.setText("멈추는 중 …")

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "백업 위치 (외장 하드 · 다른 드라이브의 폴더)", self.root() or "")
        if d:
            self.e_root.setText(d)

    def _backup_now(self) -> None:
        bad = self.problem()
        if bad:
            QMessageBox.warning(self, "백업", bad)
            return
        if self.manager is not None and self.manager.backup_now(root=self.root()):
            self.lbl_state.setText("백업을 시작합니다 …")
            self.b_now.setEnabled(False)
            self.b_stop.setEnabled(True)

    def _new_set(self) -> None:
        bad = self.problem()
        if bad:
            QMessageBox.warning(self, "백업", bad)
            return
        cur = bs.find_set(self.root(), getattr(self.config, "backup_set_id", ""))
        keep = f"지금 백업 폴더 {cur.name} 은 그대로 두고, " if cur is not None else ""
        if QMessageBox.question(self, "새 백업 폴더",
                                keep + "오늘 날짜로 새 백업 폴더를 만들어 처음부터 다시 백업합니다 (모든 사진을 다시 복사).\n"
                                "계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        if self.manager is not None and self.manager.new_set(root=self.root()):
            self.lbl_state.setText("새 백업 폴더로 백업을 시작합니다 …")

    def _open(self) -> None:
        root = self.root()
        cur = bs.find_set(root, getattr(self.config, "backup_set_id", ""))
        target = cur.path if cur is not None else Path(root)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

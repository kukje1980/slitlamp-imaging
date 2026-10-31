"""데스크탑 단축키 — 키를 눌러 캡처·안구·4분할·모드 전환, 사용자 지정 키 저장."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import shortcuts
from app.models.patient import Patient


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


@pytest.fixture()
def win(qapp, app_env):
    config, db = app_env
    config.source_mode = "synthetic"
    config.start_screen = "capture"
    config.save = lambda: None
    db.patients.insert(Patient(chart_no="K1", name="단축키"))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.show()
    assert _wait(qapp, lambda: w._last_frame is not None)
    yield w
    w.close()


def test_defaults_and_load(app_env):
    config, _ = app_env
    d = shortcuts.defaults()
    assert d["capture"] == "C" and d["eyeOD"] == "1" and d["mode"] == "F2"
    config.shortcuts = {"capture": "F9", "bogus": "Z"}
    k = shortcuts.load(config)
    assert k["capture"] == "F9" and "bogus" not in k


def test_keys_drive_actions(qapp, win):
    QTest.keyClick(win, Qt.Key.Key_2)
    assert win.control_bar.current_eye() == "OS"
    n0 = win.thumbnail_panel.count()
    QTest.keyClick(win, Qt.Key.Key_C)
    assert _wait(qapp, lambda: win.thumbnail_panel.count() == n0 + 1)
    QTest.keyClick(win, Qt.Key.Key_Q)
    assert win.control_bar.btn_quad.isChecked()
    QTest.keyClick(win, Qt.Key.Key_Q)
    QTest.keyClick(win, Qt.Key.Key_F2)                        # v1.20.3: F2(촬영 ↔ Pro 검토)는 촬영 앱에서 없앰
    assert win.mode_stack.currentIndex() == 0 and "mode" not in win._shortcut_objs
    win.set_mode("exam")                                      # 다른 화면 → 촬영 전용 키는 꺼짐 · 안구 키는 그대로
    assert not win._shortcut_objs["capture"].isEnabled() and win._shortcut_objs["eyeOD"].isEnabled()
    QTest.keyClick(win, Qt.Key.Key_1)
    assert win.control_bar.current_eye() == "OD"
    win.set_mode("capture")
    assert win.mode_stack.currentIndex() == 0


def test_custom_key(qapp, win):
    win.config.shortcuts = {"capture": "F9", "record": ""}
    win._install_shortcuts()
    assert "record" not in win._shortcut_objs
    n0 = win.thumbnail_panel.count()
    QTest.keyClick(win, Qt.Key.Key_F9)
    assert _wait(qapp, lambda: win.thumbnail_panel.count() == n0 + 1)
    QTest.keyClick(win, Qt.Key.Key_C)                         # 더 이상 캡처 아님
    qapp.processEvents()
    assert win.thumbnail_panel.count() == n0 + 1
    assert "(F9)" in win.control_bar.btn_capture.toolTip()


def test_dialog_rejects_duplicates(qapp, monkeypatch):
    from PySide6.QtGui import QKeySequence
    from app.ui import shortcuts_dialog
    from app.ui.shortcuts_dialog import ShortcutsDialog
    warned = []
    monkeypatch.setattr(shortcuts_dialog.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    dlg = ShortcutsDialog(shortcuts.defaults())
    dlg.edits["record"].setKeySequence(QKeySequence("C"))     # 캡처와 겹침
    vals = dlg.values()
    assert vals["record"] == "C" and vals["capture"] == "C"
    dlg._accept()
    assert dlg.result() == 0 and warned                       # 겹치면 안내하고 닫히지 않음

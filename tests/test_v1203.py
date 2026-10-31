"""v1.20.3 — Pro 검토 입구 없앰 · 아래 참조 줄 없앰(설정 → 참조 영상 라이브러리) ·
촬영 직후 큰 화면 · 멀티 화면에서 캡처 = 초기화(설정으로 '바로 촬영') · 촬영 직후 큰 화면에서 바로 전송 · 전송(삭제)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app import shortcuts
from app.models.patient import Patient
from app.services import sample_import
from app.services.send_service import SendResult


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


def _pump(qapp, n=10):
    for _ in range(n):
        qapp.processEvents()


def _samples(root: Path) -> Path:
    meta = []
    files = {"01_건성안/건성안_1.jpg": {"title": "건성안 1"}, "04_백내장/핵백내장_1.jpg": {"title": "핵백내장"}}
    for i, (rel, info) in enumerate(files.items()):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = cv2.imencode(".jpg", np.full((90, 120, 3), 40 + i * 60, np.uint8))[1].tobytes()
        path.write_bytes(data)
        meta.append({"file": rel, "sha256": hashlib.sha256(data).hexdigest(), **info})
    (root / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return root


@pytest.fixture()
def win(qapp, app_env, monkeypatch, tmp_path):
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 320, 240
    config.view_after_capture = True
    config.save = lambda: None
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    sample_import.import_samples(db, config, _samples(tmp_path / "samples"))
    db.patients.insert(Patient(chart_no="V1", name="전송"))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.resize(1500, 900)
    w.show()
    assert _wait(qapp, lambda: w._last_frame is not None)
    i = next(k for k in range(w.patient_panel.combo.count()) if "V1" in w.patient_panel.combo.itemText(k))
    w.patient_panel.combo.setCurrentIndex(i)
    qapp.processEvents()
    yield w
    w._close_capture_viewer()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _count(win):
    return len(win.db.captures.list_by_patient(win._current_patient.id))


# ---------------- 1. Pro 검토 입구 없앰 ----------------
def test_no_pro_review_entry(qapp, win):
    assert not hasattr(win, "act_pro") and not hasattr(win, "btn_mode_pro")
    assert [b.text() for b in win.mode_buttons.values()] == ["홈", "촬영", "검사 기록", "환자 목록"]
    assert all("Pro" not in b.text() for b in win.nav_buttons)
    assert "pro" not in win.home.tiles and not hasattr(win.exam_screen, "btn_pro")
    assert "mode" not in win._shortcut_objs                       # F2 를 걸지 않음
    QTest.keyClick(win, Qt.Key.Key_F2)
    _pump(qapp)
    assert win.mode() == "capture"
    assert win.multi_pro is not win.pro and win.mode_stack.indexOf(win.pro) == 1   # Pro 기능은 멀티 안에 그대로


def test_shortcuts_dialog_hides_mode_and_keeps_saved_key(qapp, win, monkeypatch):
    from app.ui.shortcuts_dialog import ShortcutsDialog
    dlg = ShortcutsDialog(shortcuts.load(win.config), win, hidden=shortcuts.HIDDEN_IN_STANDARD)
    assert "mode" not in dlg.edits and "mode" not in dlg.values() and "capture" in dlg.edits
    dlg._reset()                                                  # 기본값으로: 숨긴 항목이 없어도 오류 없음
    assert dlg.values()["capture"] == "C"
    dlg.close()
    full = ShortcutsDialog(shortcuts.load(win.config), win)        # Explorer 판은 그대로 모두
    assert "mode" in full.edits
    full.close()
    win.config.shortcuts = {"mode": "F3", "capture": "C"}
    monkeypatch.setattr(ShortcutsDialog, "exec", lambda self: 1)
    win._open_shortcuts()
    assert win.config.shortcuts["mode"] == "F3" and win.config.shortcuts["capture"] == "C"
    assert "mode" not in win._shortcut_objs


def test_usb_key_may_reuse_hidden_shortcut(qapp, app_env):
    from app.ui.usb_buttons_tab import UsbButtonsTab
    config, _db = app_env
    config.shortcuts = {"mode": "F2"}
    tab = UsbButtonsTab(config)
    vals = tab.values()
    vals["1"]["key"] = "F2"
    tab.values = lambda: vals
    assert tab.problem() is None                                  # 촬영 앱에서 쓰지 않는 F2 와는 겹쳐도 됨


# ---------------- 2. 아래 참조 줄 없앰 · 설정 → 참조 영상 라이브러리 ----------------
def test_dx_icon_works_with_hidden_ref_model(qapp, win):
    rb = win.ref_bar
    assert not rb._loaded and not rb.isVisible()
    win.dx_bar.buttons[("sample", "건성안")].click()             # 처음 고를 때 읽는다 (숨긴 모델은 showEvent 가 없음)
    _pump(qapp)
    assert rb._loaded and not rb.isVisible() and win._multi and rb.selected_refs()
    assert not rb.isWindow() and not any(w is rb for w in QApplication.topLevelWidgets())
    win.dx_bar.buttons[("sample", "건성안")].click()             # 다시 누르면 뺌
    _pump(qapp)
    assert rb.selected_refs() == [] and win.dx_bar.active() is None


def test_settings_reference_library_button(qapp, win, monkeypatch):
    from app.ui.settings_dialog import SettingsDialog
    opened = []
    monkeypatch.setattr(win, "open_references", lambda: opened.append(QApplication.activeModalWidget()))
    dlg = SettingsDialog(win.config, win, actions={"references": lambda: win.open_references()})
    assert dlg.btn_references.isEnabled() and "참조 영상 라이브러리" in dlg.btn_references.text()
    dlg.show()
    _pump(qapp)
    dlg.btn_references.click()
    assert not dlg.isVisible() and opened == []                   # 설정 창을 먼저 닫고
    _pump(qapp)
    assert len(opened) == 1 and opened[0] is None                 # 그다음에 (모달 창 없이) 라이브러리
    assert not SettingsDialog(win.config, win).btn_references.isEnabled()


# ---------------- 3. 보기 · 멀티 화면에서 캡처 = 초기화 ----------------
def test_capture_in_multi_resets(qapp, win):
    win._on_capture()
    _pump(qapp)
    win._close_capture_viewer()
    n = _count(win)
    win.dx_bar.buttons[("sample", "백내장")].click()
    _pump(qapp)
    assert win._multi and win.dx_bar.active() and win.thumbnail_panel.selected_captures()
    QTest.mouseClick(win.control_bar.btn_capture, Qt.MouseButton.LeftButton)   # 촬영 도크 버튼
    _pump(qapp)
    assert not win._multi and win.view_stack.currentIndex() == 0 and _count(win) == n
    assert win.thumbnail_panel.selected_captures() == [] and win.ref_bar.selected_refs() == []
    assert win.dx_bar.active() is None and win._capture_viewer is None
    QTest.mouseClick(win.control_bar.btn_capture, Qt.MouseButton.LeftButton)   # 라이브에서는 그대로 촬영
    _pump(qapp)
    assert _count(win) == n + 1
    win._close_capture_viewer()
    # 촬영 단축키(C)도 같음
    win.dx_bar.buttons[("sample", "백내장")].click()
    _pump(qapp)
    assert win._multi
    win.activateWindow()                                          # 보기 창이 닫힌 뒤 메인 창이 앞으로
    _pump(qapp)
    assert QApplication.activeWindow() is win
    QTest.keyClick(win, Qt.Key.Key_C)
    _pump(qapp)
    assert not win._multi and _count(win) == n + 1
    # 설정 '바로 촬영': 멀티에서 그대로 찍는다 (예전)
    win.config.capture_in_view = "capture"
    win.dx_bar.buttons[("sample", "백내장")].click()
    _pump(qapp)
    win._run_action("capture")
    _pump(qapp)
    assert _count(win) == n + 2 and win._multi


def test_capture_resets_frozen_view_to_live(qapp, win):
    win._on_freeze()
    assert win._single_frozen
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    QTest.keyClick(v, Qt.Key.Key_C)
    _pump(qapp)
    assert win._capture_viewer is None and not win._single_frozen


def test_usb_capture_in_viewer_and_multi_resets(qapp, win, monkeypatch):
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    n = _count(win)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: v))
    assert win._usb_context() is None and win._capture_viewer is None   # USB 누름: 보기 창 닫힘
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: win))
    win._on_usb_button("3", "capture")                           # 사진 버튼 = 초기화 (찍지 않음)
    _pump(qapp)
    assert _count(win) == n and "초기화" in win.statusBar().currentMessage()
    win._on_usb_button("3", "capture")                           # 한 번 더 = 촬영
    _pump(qapp)
    assert _count(win) == n + 1
    win._close_capture_viewer()
    win.dx_bar.buttons[("sample", "건성안")].click()
    _pump(qapp)
    assert win._multi
    win._on_usb_button("3", "capture")                           # 멀티에서 사진 버튼 = 초기화
    _pump(qapp)
    assert not win._multi and _count(win) == n + 1
    # 다른 USB 버튼(스틸)은 보기 창을 닫고 그 동작을 그대로
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: v))
    assert win._usb_context() is None
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: win))
    win._on_usb_button("2", "freeze")
    _pump(qapp)
    assert win._single_frozen


def test_capture_in_view_setting_roundtrip(qapp, app_env, tmp_path):
    from app.config import AppConfig
    from app.ui.settings_dialog import SettingsDialog
    config, _db = app_env
    assert AppConfig().capture_in_view == "reset"
    dlg = SettingsDialog(config)
    assert dlg.capture_in_view.currentData() == "reset"
    dlg.capture_in_view.setCurrentIndex(dlg.capture_in_view.findData("capture"))
    dlg.apply_to_config()
    assert config.capture_in_view == "capture"
    config.save()
    assert AppConfig.load().capture_in_view == "capture"
    config.capture_in_view = "아무거나"
    config.save()
    assert AppConfig.load().capture_in_view == "reset"            # 쓸 수 없는 값은 기본값


# ---------------- 4. 촬영 직후 큰 화면에서 바로 전송 ----------------
def test_viewer_send_one_photo(qapp, win, monkeypatch):
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    cap = v.capture
    assert v.b_send.isVisible() and v.b_send_delete.isVisible()
    calls = []

    def fake_send(caps, parent=None):
        calls.append(([c.id for c in caps], parent))
        return SendResult(sent=["/tmp/x.jpg"])
    monkeypatch.setattr(win, "_send_captures", fake_send)
    QTest.mouseClick(v.b_send, Qt.MouseButton.LeftButton)
    _pump(qapp)
    assert calls == [([cap.id], v)] and v.lbl_sent.text() == "✓ 보냄"
    assert win._capture_viewer is v and _count(win) == 1          # 전송만: 창 · 사진 그대로


def test_viewer_send_delete(qapp, win, monkeypatch):
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[1]) or QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(win, "_send_captures", lambda caps, parent=None: SendResult(sent=["a"], errors=["PACS 오류"]))
    v.b_send_delete.click()                                       # 다 보내지 못함 → 지우지 않음
    _pump(qapp)
    assert _count(win) == 1 and win._capture_viewer is v and asked == []
    monkeypatch.setattr(win, "_send_captures", lambda caps, parent=None: None)
    v.b_send_delete.click()                                       # 전송 창 취소 → 그대로
    _pump(qapp)
    assert _count(win) == 1 and win._capture_viewer is v
    monkeypatch.setattr(win, "_send_captures", lambda caps, parent=None: SendResult(sent=["a"]))
    v.b_send_delete.click()                                       # 모두 보냄 → 확인 후 이 PC 에서 삭제 · 창 닫힘
    _pump(qapp)
    assert asked and asked[-1] == "전송(삭제)"
    assert _count(win) == 0 and win._capture_viewer is None and win.thumbnail_panel.count() == 0


def test_send_captures_real_folder(qapp, win, monkeypatch, tmp_path):
    """_send_captures 가 실제로 폴더로 보낸다 (오른쪽 목록 선택 없이 사진 1장)."""
    from app.ui import send_dialog
    win.config.send_method = "folder"
    win.config.send_folder = str(tmp_path / "out")
    monkeypatch.setattr(send_dialog.SendDialog, "exec", lambda self: 1)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    assert win.thumbnail_panel.selected_captures() == []
    res = win._send_captures([v.capture], parent=v)
    assert res is not None and res.ok and len(res.sent) >= 1 and Path(res.sent[0]).exists()


def test_multi_shows_send_button(qapp, win):
    win._on_capture()
    _pump(qapp)
    win._close_capture_viewer()
    win.dx_bar.buttons[("sample", "건성안")].click()
    _pump(qapp)
    assert win._multi and win.control_bar.btn_send.isVisible() and win.control_bar.btn_send.isEnabled()


# ---------------- 점검에서 고친 것 ----------------
def test_usb_reset_even_before_main_window_is_active(qapp, win, monkeypatch):
    """보기 창을 닫은 직후 메인 창이 아직 활성화되지 않았어도(맥 · 윈도우) USB 사진 버튼 = 초기화 (찍지도, 버리지도 않음)."""
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    win.thumbnail_panel.toggle_selection(v.capture)
    n = _count(win)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: v))
    assert win._usb_context() is None and win._capture_viewer is None
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: None))   # 아직 아무 창도 활성 아님
    win._on_usb_button("3", "capture")
    _pump(qapp)
    assert _count(win) == n and win.thumbnail_panel.selected_captures() == []


def test_usb_eye_signal_closing_viewer_does_not_turn_next_photo_into_reset(qapp, win, monkeypatch):
    win.config.usb_eye_auto = True
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    n = _count(win)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: v))
    assert win._usb_context() is None                            # 좌우 신호가 보기 창을 닫음
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: win))
    win._on_usb_button("5", "eye_os")
    _pump(qapp)
    win._on_usb_button("3", "capture")                           # 라이브에서 사진 버튼 = 촬영
    _pump(qapp)
    assert _count(win) == n + 1


def test_viewer_send_uses_photo_patient(qapp, win, monkeypatch):
    """보기 창이 떠 있는 동안 다른 환자로 바꿔도 그 사진의 환자로 보낸다."""
    from app.services import send_service
    from app.ui import send_dialog
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    owner = win._current_patient
    other = win.db.patients.insert(Patient(chart_no="V2", name="다른"))
    win._current_patient = win.db.patients.get(other)
    got = []
    monkeypatch.setattr(send_dialog.SendDialog, "exec", lambda self: 1)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(send_service, "send", lambda caps, p, *a, **k: got.append(p.id) or SendResult(sent=["x"]))
    win._send_captures([v.capture], parent=v)
    assert got == [owner.id]


def test_explorer_settings_hide_standard_only_items(qapp, app_env):
    from app.ui.settings_dialog import SettingsDialog
    config, _db = app_env
    d = SettingsDialog(config, standard=False)
    d.show()
    _pump(qapp)
    assert not d.btn_references.isVisible() and not d.capture_in_view.isVisible()
    d.close()

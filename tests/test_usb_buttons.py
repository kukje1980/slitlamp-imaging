"""슬릿램프 USB 버튼 (Ctrl+Shift+Alt+F9 녹화 · F10 스틸) · 좌우 신호 (F6 → OD · F7 → OS) — 키 이벤트를 흉내 낸 검사.

실제 USB 장치(VID 04D9 / PID 1203)로는 시험하지 않았다: 장치가 보내는 것과 같은 키 조합의 누름 · 뗌 · 자동 반복
이벤트를 Qt 에 직접 넣어 확인한다. Windows Raw Input(장치 확인)은 이름 판정 · 시각 창 · 키 변환만 검사한다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from app import usb_buttons
from app.models.patient import Patient

M = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier
F6, F7, F8, F9, F10 = Qt.Key.Key_F6, Qt.Key.Key_F7, Qt.Key.Key_F8, Qt.Key.Key_F9, Qt.Key.Key_F10


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(qapp, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


def _pump(qapp, t=0.1):
    end = time.time() + t
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def _key(target, key, press=True, mods=M, repeat=False):
    t = QEvent.Type.KeyPress if press else QEvent.Type.KeyRelease
    QApplication.sendEvent(target, QKeyEvent(t, key, mods, "", repeat))


def _click(target, key, mods=M):
    _key(target, key, True, mods)
    _key(target, key, False, mods)


# ---------------- 키 조합 · 설정값 ----------------
def test_defaults_and_bad_saved_values():
    class Cfg:
        usb_buttons = {}
    b = usb_buttons.load(Cfg())
    assert b == {"1": {"key": "Ctrl+Shift+Alt+F9", "action": "record"},
                 "2": {"key": "Ctrl+Shift+Alt+F10", "action": "freeze"},
                 "3": {"key": "Ctrl+Shift+Alt+F8", "action": "capture"},
                 "4": {"key": "Ctrl+Shift+Alt+F6", "action": "eye_od"},
                 "5": {"key": "Ctrl+Shift+Alt+F7", "action": "eye_os"}}
    Cfg.usb_buttons = {"1": {"key": "F8", "action": "없는동작"}, "2": "망가진 값", "9": {"key": "X"}}
    b = usb_buttons.load(Cfg())
    assert b["1"] == {"key": "F8", "action": "record"} and b["2"]["key"] == "Ctrl+Shift+Alt+F10"
    assert "9" not in b and b["3"]["action"] == "capture" and b["4"]["action"] == "eye_od"   # 예전 설정(1 · 2 만)에도 기본값으로
    assert usb_buttons.EYE_ACTIONS == {"eye_od": "OD", "eye_os": "OS"} and usb_buttons.is_eye_action("eye_os")
    assert not usb_buttons.is_eye_action("capture")


def test_key_matching_any_modifier_order():
    k = "Ctrl+Shift+Alt+F9"
    assert usb_buttons.same_key(k, "ctrl+alt+shift+f9") and not usb_buttons.same_key(k, "Ctrl+Shift+Alt+F10")
    assert usb_buttons.matches(k, F9, M)
    assert not usb_buttons.matches(k, F9, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert not usb_buttons.matches(k, F9, Qt.KeyboardModifier.NoModifier)          # F9 만 = 다른 키
    assert not usb_buttons.matches(k, F10, M)
    assert usb_buttons.matches(k, F9, M | Qt.KeyboardModifier.KeypadModifier)     # 키패드 표시는 무시
    assert usb_buttons.key_of(k) == int(F9) and usb_buttons.key_of("") is None


# ---------------- 누름 · 뗌 · 자동 반복 (필터 단독) ----------------
class _Target:
    pass


def _filter(qapp, context=lambda: None, clock=None):
    runs, ignored = [], []
    now = [100.0]
    f = usb_buttons.UsbButtonFilter(usb_buttons.load(_Target()), run=lambda b, a: runs.append((b, a)),
                                    context=context, ignored=lambda b, r: ignored.append((b, r)),
                                    clock=clock or (lambda: now[0]))
    return f, runs, ignored, now


def _ev(f, key, press=True, mods=M, repeat=False):
    t = QEvent.Type.KeyPress if press else QEvent.Type.KeyRelease
    return f.eventFilter(None, QKeyEvent(t, key, mods, "", repeat))


def test_filter_runs_once_per_press_and_needs_release(qapp):
    f, runs, _ign, now = _filter(qapp)
    assert _ev(f, F9) is True                                  # 버튼 1 누름 → 가로채고 실행
    _pump(qapp)
    assert runs == [("1", "record")]
    for _ in range(5):                                         # 길게 누름: 자동 반복
        assert _ev(f, F9, repeat=True) is True
    assert _ev(f, F9) is True                                  # 뗌 없이 다시 온 누름 (중복 신호)
    _pump(qapp)
    assert runs == [("1", "record")] and f.held() == {"1"}
    now[0] += 1.0
    _ev(f, F9, press=False, mods=Qt.KeyboardModifier.NoModifier)   # 조합키를 먼저 놓고 F9 를 떼도 뗌으로
    assert f.held() == set()
    _ev(f, F9)
    _pump(qapp)
    assert runs == [("1", "record"), ("1", "record")]          # 뗀 뒤 다시 누르면 다음 동작
    _ev(f, F9, press=False)
    now[0] += 1.0
    _ev(f, F10)
    _pump(qapp)
    assert runs[-1] == ("2", "freeze")                         # 버튼 2 는 따로


def test_filter_debounce_and_deactivate(qapp):
    f, runs, _ign, now = _filter(qapp)
    _ev(f, F10)
    _ev(f, F10, press=False)
    now[0] += 0.05                                             # 접점 튐: 0.05초 만에 다시 누름
    assert _ev(f, F10) is True
    _pump(qapp)
    assert runs == [("2", "freeze")]
    _ev(f, F10, press=False)
    now[0] += 1.0
    _ev(f, F9)                                                 # 누른 채 다른 프로그램으로 → 뗌을 못 받음
    f.eventFilter(None, QEvent(QEvent.Type.ApplicationDeactivate))
    assert f.held() == set()
    now[0] += 1.0
    _ev(f, F9)
    _pump(qapp)
    assert runs.count(("1", "record")) == 2
    assert _ev(f, Qt.Key.Key_R, mods=Qt.KeyboardModifier.NoModifier) is False   # 다른 키는 그대로 흘려보냄


def test_filter_ignored_context_does_not_run_or_swallow(qapp):
    reason = ["text"]
    f, runs, ignored, now = _filter(qapp, context=lambda: reason[0])
    assert _ev(f, F9) is False                                 # 입력칸 · 다른 화면: 가로채지 않음
    _pump(qapp)
    assert runs == [] and ignored == [("1", "text")]
    _ev(f, F9, press=False)
    reason[0] = None
    now[0] += 1.0
    assert _ev(f, F9) is True
    _pump(qapp)
    assert runs == [("1", "record")]
    f.set_buttons({"1": {"key": "Ctrl+Shift+Alt+F9", "action": "none"}})
    now[0] += 1.0
    _ev(f, F9, press=False)
    _ev(f, F9)
    _pump(qapp)
    assert runs == [("1", "record")]                           # '사용 안 함' 이면 아무것도 안 함


def test_filter_eye_signals_explicit_and_device_gate(qapp):
    """좌우 신호: 길게 눌러도 한 번, OD 신호가 두 번 와도 OD(토글 아님), 다른 키보드의 같은 키는 장치 확인으로 무시."""
    gate = {"v": None}
    f, runs, ignored, now = _filter(qapp)
    f.set_device_gate(lambda vk: gate["v"])
    assert _ev(f, F6) is True
    _pump(qapp)
    assert runs == [("4", "eye_od")]
    for _ in range(3):
        assert _ev(f, F6, repeat=True) is True                 # 길게 누름 → 그대로
    _ev(f, F6, press=False)
    now[0] += 1.0
    assert _ev(f, F6) is True
    _pump(qapp)
    assert runs == [("4", "eye_od"), ("4", "eye_od")]          # OD 신호 두 번 = 여전히 OD 동작 (토글이 아님)
    _ev(f, F6, press=False)
    now[0] += 1.0
    _ev(f, F7)
    _pump(qapp)
    assert runs[-1] == ("5", "eye_os")
    _ev(f, F7, press=False)
    now[0] += 1.0
    gate["v"] = False                                          # Raw Input: 다른 키보드가 보낸 같은 키 조합
    assert _ev(f, F6) is False                                 # 무시 · 가로채지도 않음
    _pump(qapp)
    assert ignored[-1] == ("4", "device") and runs[-1] == ("5", "eye_os")
    _ev(f, F6, press=False)
    now[0] += 1.0
    assert _ev(f, F9) is True                                  # 녹화 · 스틸 · 캡처 버튼은 예전처럼 장치 확인 없이
    _pump(qapp)
    assert runs[-1] == ("1", "record")
    _ev(f, F9, press=False)
    now[0] += 1.0
    assert _ev(f, F8) is True                                  # 버튼 3 → 사진 캡처
    _pump(qapp)
    assert runs[-1] == ("3", "capture")
    _ev(f, F8, press=False)
    now[0] += 1.0
    gate["v"] = True
    assert _ev(f, F6) is True
    _pump(qapp)
    assert runs[-1] == ("4", "eye_od")


def test_raw_input_device_name_key_log_and_fallback():
    from app import usb_raw_input as ri
    assert ri.is_slitlamp_device(r"\\?\HID#VID_04D9&PID_1203&MI_00#7&2d9f0b6&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}")
    assert ri.is_slitlamp_device(r"\\?\hid#vid_04d9&pid_1203&mi_00#8&1a2b3c#{...}")       # 대소문자 무관
    assert not ri.is_slitlamp_device(r"\\?\HID#VID_04D9&PID_1203&MI_01#7&2d9f0b6&0&0000#{...}")   # 다른 인터페이스
    assert not ri.is_slitlamp_device(r"\\?\HID#VID_046D&PID_C31C&MI_00#8&...")           # 다른 키보드
    assert not ri.is_slitlamp_device("") and not ri.is_slitlamp_device(None)
    assert (ri.qt_key_to_vk(F6), ri.qt_key_to_vk(F7), ri.qt_key_to_vk(F9), ri.qt_key_to_vk(F10)) == (0x75, 0x76, 0x78, 0x79)
    assert ri.qt_key_to_vk(Qt.Key.Key_A) == 0x41 and ri.qt_key_to_vk(Qt.Key.Key_1) == 0x31
    assert ri.qt_key_to_vk(Qt.Key.Key_Escape) is None
    now = [10.0]
    keylog = ri.RawKeyLog(clock=lambda: now[0], window=0.25)
    keylog.watch([0x75, 0x76])
    assert keylog.classify(0x75) is None and keylog.classify(None) is None       # 기록 없음 → 모름 (받음)
    keylog.note(0x75, True)
    assert keylog.classify(0x75) is True and keylog.last_device is True
    keylog.note(0x41, False)                                                     # 감시 키가 아니면 기록하지 않음 (글자 비수집)
    assert keylog.classify(0x41) is None and keylog.last_device is True and not keylog.watching(0x41)
    now[0] += 0.1
    keylog.note(0x76, False)
    assert keylog.classify(0x76) is False
    now[0] += 0.3
    assert keylog.classify(0x75) is None                                         # 너무 오래된 기록은 쓰지 않음
    null = ri.create(None)                                                       # 리눅스 · 맥: 장치 확인 없음 → 모두 받음
    assert isinstance(null, ri.NullRawInput) and not null.available()
    assert null.classify(int(F6)) is None and "사용 안 함" in null.status_text()


# ---------------- 메인 창 ----------------
@pytest.fixture()
def win(qapp, app_env, monkeypatch):
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 160, 120
    config.save = lambda: None
    db.patients.insert(Patient(chart_no="U1", name="버튼"))
    monkeypatch.setattr(usb_buttons, "MIN_INTERVAL", 0.0)      # 시험에서는 연달아 눌러도 되게 (간격 검사는 위에서)
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.resize(1400, 820)
    w.show()
    w.activateWindow()
    assert _wait(qapp, lambda: w._last_frame is not None)
    _wait(qapp, lambda: QApplication.activeWindow() is w, 3)
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _target():
    return QApplication.focusWidget() or QApplication.activeWindow()


def test_capture_screen_focus_and_still_button_saves_nothing(qapp, win):
    assert QApplication.focusWidget() is win.video_view            # 시작하면 초점은 영상 (검색칸 아님)
    assert win._usb_context() is None
    n0 = len(win.db.captures.list_by_patient(win._current_patient.id))
    _key(_target(), F10)                                           # 버튼 2 누름 → 스틸 정지
    _pump(qapp)
    assert win._single_frozen
    for _ in range(4):                                             # 누르고 있는 동안 자동 반복 → 그대로
        _key(_target(), F10, repeat=True)
    _pump(qapp)
    assert win._single_frozen
    _key(_target(), F10, press=False)
    assert not win._smart_live()                                   # 정지 중에는 Smart 자동 저장 · 자동 정지가 쉼
    _click(_target(), F10)                                         # 다시 누름 → 라이브로
    _pump(qapp)
    assert not win._single_frozen
    assert len(win.db.captures.list_by_patient(win._current_patient.id)) == n0   # 사진 파일 저장 안 함
    assert "USB 버튼 2" in win.statusBar().currentMessage()


def test_record_button_and_still_during_recording_keeps_video(qapp, win):
    db = win.db
    _click(_target(), F9)                                          # 버튼 1 → 녹화 시작
    _pump(qapp)
    if not win._recording:
        pytest.skip("인코더 없음")
    assert win.control_bar.btn_record.isChecked()
    assert _wait(qapp, lambda: win.recorder.frame_count >= 3)
    _click(_target(), F10)                                         # 녹화 중 버튼 2 → 스틸 정지
    _pump(qapp)
    assert win._single_frozen and win._recording                    # 녹화는 끊기지 않음
    n = win.recorder.frame_count
    assert _wait(qapp, lambda: win.recorder.frame_count >= n + 5)  # 정지 화면 중에도 카메라 프레임은 계속 녹화
    _click(_target(), F10)                                         # 스틸 해제
    _pump(qapp)
    assert not win._single_frozen and win._recording
    _key(_target(), F9)                                            # 버튼 1 길게: 자동 반복이 와도 한 번만 정지
    for _ in range(3):
        _key(_target(), F9, repeat=True)
    _pump(qapp)
    assert not win._recording
    _key(_target(), F9, press=False)
    assert _wait(qapp, lambda: win._pending_video is None and not win.recorder.is_finalizing, 20)
    caps = [c for c in db.captures.list_by_patient(win._current_patient.id) if c.media_type == "video"]
    assert len(caps) == 1 and Path(caps[0].file_path).stat().st_size > 0
    import cv2
    vc = cv2.VideoCapture(caps[0].file_path)                       # 파일이 열리고 프레임이 읽힘 (손상 없음)
    ok, _frame = vc.read()
    frames = int(vc.get(cv2.CAP_PROP_FRAME_COUNT))
    vc.release()
    assert ok and frames >= 8
    assert not [c for c in db.captures.list_by_patient(win._current_patient.id) if c.media_type == "photo"]


def test_not_in_text_input_other_screens_or_dialogs(qapp, win):
    search = win.patient_panel.search
    search.setFocus()
    _pump(qapp)
    assert isinstance(QApplication.focusWidget(), QLineEdit) and win._usb_context() == "text"
    _click(search, F10)
    _pump(qapp)
    assert not win._single_frozen and "입력칸" in win.statusBar().currentMessage()
    QTest.keyClick(search, Qt.Key.Key_Return)                      # 검색 끝(Enter) → 초점이 영상으로
    _pump(qapp)
    assert QApplication.focusWidget() is win.video_view
    _click(_target(), F10)
    _pump(qapp)
    assert win._single_frozen
    _click(_target(), F10)
    _pump(qapp)
    assert not win._single_frozen
    win.set_mode("exam")                                           # 촬영 화면이 아니면 실행 안 함
    _pump(qapp)
    assert win._usb_context() == "screen"
    rec0 = win._recording
    _click(_target(), F9)
    _pump(qapp)
    assert win._recording == rec0 and "촬영 화면에서만" in win.statusBar().currentMessage()
    win.set_mode("capture")
    _pump(qapp)
    assert QApplication.focusWidget() is win.video_view and win._usb_context() is None
    from app.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(win.config, win)                         # 설정 창이 앞에 있으면 실행 안 함
    dlg.show()
    dlg.activateWindow()
    assert _wait(qapp, lambda: QApplication.activeWindow() is dlg, 3)
    assert win._usb_context() == "window"
    probe = dlg.usb_tab.probe                                      # 설정 창의 '신호 확인' 칸은 받은 신호를 보여 줌
    probe.setFocus()
    _click(probe, F10)
    _pump(qapp)
    assert not win._single_frozen
    assert "버튼 2" in probe.text() and "뗌 확인" in probe.text() and probe.presses == 1
    dlg.close()


def test_settings_tab_change_actions_and_conflicts(qapp, win, monkeypatch):
    from app.ui import main_window as mw
    from app.ui.settings_dialog import SettingsDialog
    from app.ui.shortcuts_dialog import ShortcutsDialog
    warned = []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    from app.ui import settings_dialog as sd
    from app.ui import shortcuts_dialog as scd
    monkeypatch.setattr(sd.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(scd.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))

    dlg = SettingsDialog(win.config, win)
    tab = dlg.usb_tab
    assert dlg.tabs.tabText(dlg.tabs.indexOf(tab)) == "USB 버튼"
    assert tab.values() == {"1": {"key": "Ctrl+Alt+Shift+F9", "action": "record"},
                            "2": {"key": "Ctrl+Alt+Shift+F10", "action": "freeze"},
                            "3": {"key": "Ctrl+Alt+Shift+F8", "action": "capture"},
                            "4": {"key": "Ctrl+Alt+Shift+F6", "action": "eye_od"},
                            "5": {"key": "Ctrl+Alt+Shift+F7", "action": "eye_os"}}
    assert tab.chk_eye_auto.isChecked() and tab.chk_device_check.isChecked()
    # Windows 는 Raw Input 이 등록돼 '사용 중', 리눅스 · 맥은 '사용 안 함' (모든 키보드 신호를 받음)
    assert ("사용 중" if sys.platform == "win32" else "사용 안 함") in tab.lbl_device.text()
    tab.key_edits["2"].setKeySequence(tab.key_edits["1"].keySequence())     # 두 버튼에 같은 키 → 확인 막음
    dlg.accept()
    assert dlg.result() == 0 and warned and "같은 키" in warned[-1]
    tab.reset("2")
    win.config.shortcuts = {"record": "Ctrl+Shift+Alt+F9"}                    # 단축키와 같은 키 → 막음
    assert "단축키" in (tab.problem() or "")
    win.config.shortcuts = {}
    assert tab.problem() is None

    def fake_exec(self):                                                      # 설정 창: 버튼 2 를 '사용 안 함', 자동 인식 끔
        self.usb_tab.action_combos["2"].setCurrentIndex(self.usb_tab.action_combos["2"].findData("none"))
        self.usb_tab.chk_eye_auto.setChecked(False)
        return 1
    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    other = dict(win.config.shortcuts or {}), win.config.theme
    win._open_settings()
    assert win.config.usb_buttons["2"]["action"] == "none"
    assert win.config.usb_buttons["1"] == {"key": "Ctrl+Alt+Shift+F9", "action": "record"}
    assert win.config.usb_buttons["3"]["action"] == "capture" and win.config.usb_buttons["4"]["action"] == "eye_od"
    assert win.config.usb_eye_auto is False and win.config.usb_device_check is True
    assert not win.control_bar.eye_status.isVisible()                         # 자동 인식을 끄면 상태 칩도 없음
    win.config.usb_eye_auto = True
    win._refresh_eye_status()
    assert (dict(win.config.shortcuts or {}), win.config.theme) == other      # 다른 설정은 그대로
    assert win.usb_filter.buttons["2"]["action"] == "none"
    assert _wait(qapp, lambda: win._last_frame is not None)
    _wait(qapp, lambda: QApplication.activeWindow() is win, 3)
    win._focus_capture_view()
    _click(_target(), F10)
    _pump(qapp)
    assert not win._single_frozen                                             # 바꾼 기능이 바로 적용

    # 단축키 창: USB 버튼 키는 다른 동작에 지정할 수 없음
    taken = {"Ctrl+Shift+Alt+F9": "슬릿램프 USB 버튼 1"}
    sdlg = ShortcutsDialog({"record": "R"}, win, taken=taken)
    from PySide6.QtGui import QKeySequence
    sdlg.edits["capture"].setKeySequence(QKeySequence("Ctrl+Alt+Shift+F9"))
    sdlg._accept()
    assert sdlg.result() == 0 and "USB 버튼 1" in warned[-1]
    # 예전에 같은 키를 단축키로 저장해 둔 경우: USB 버튼이 먼저, 그 단축키는 쉼
    win.config.shortcuts = {"capture": "Ctrl+Shift+Alt+F9"}
    win._install_shortcuts()
    assert "capture" not in win._shortcut_objs and "record" in win._shortcut_objs


# ---------------- 좌우 신호 (F6 → OD · F7 → OS) · 캡처 버튼 (F8) ----------------
def _photo(win):
    """지금 화면을 사진으로 저장하고 그 기록을 돌려준다."""
    win._on_capture()
    return win.db.captures.list_by_patient(win._current_patient.id)[0]


def test_capture_button_f8_saves_one_photo_with_displayed_eye(qapp, win):
    pid = win._current_patient.id
    n0 = len(win.db.captures.list_by_patient(pid))
    _click(_target(), F6)                                                           # OD
    _pump(qapp)
    _key(_target(), F8)                                                             # 버튼 3 누름 → 사진 한 장
    for _ in range(4):
        _key(_target(), F8, repeat=True)                                            # 길게 눌러도 한 장만
    _pump(qapp)
    _key(_target(), F8, press=False)
    caps = win.db.captures.list_by_patient(pid)
    assert len(caps) == n0 + 1 and caps[0].media_type == "photo" and caps[0].eye == "OD"
    assert "USB 버튼 3" in win.statusBar().currentMessage() and "사진 캡처" in win.statusBar().currentMessage()
    _click(_target(), F10)                                                          # 정지 화면 (OD 로 찍힘)
    _pump(qapp)
    _click(_target(), F7)                                                           # 정지 중 왼눈 신호
    _pump(qapp)
    _click(_target(), F8)                                                           # 정지 화면은 찍은 순간의 눈(OD)으로
    _pump(qapp)
    caps = win.db.captures.list_by_patient(pid)
    assert len(caps) == n0 + 2 and caps[0].eye == "OD"
    _click(_target(), F10)                                                          # 라이브로
    _pump(qapp)
    _click(_target(), F8)                                                           # 지금 눈(OS)으로
    _pump(qapp)
    caps = win.db.captures.list_by_patient(pid)
    assert len(caps) == n0 + 3 and caps[0].eye == "OS"
    win.patient_panel.search.setFocus()                                             # 입력 중에는 찍지 않음
    _pump(qapp)
    _click(win.patient_panel.search, F8)
    _pump(qapp)
    assert len(win.db.captures.list_by_patient(pid)) == n0 + 3 and "입력칸" in win.statusBar().currentMessage()


def test_eye_signals_explicit_od_os_and_pinned_at_capture(qapp, win):
    cb, hud = win.control_bar, win.video_view
    assert cb.current_eye() == "OD" and win.eye_status()["source"] == "default"     # 켜자마자 OD 가 골라져 있음
    assert cb.eye_buttons["OD"].isChecked() and cb.eye_status.text() == "기본 OD"
    assert hud._eye == "OD" and hud._eye_note == ""
    _click(_target(), F6)                                                           # 신호 3 → OD
    _pump(qapp)
    assert cb.current_eye() == "OD" and cb.eye_buttons["OD"].isChecked()
    assert win.eye_status()["source"] == "device" and cb.eye_status.text() == "자동"
    assert hud._eye == "OD" and hud._eye_note == "자동" and "OD" in win.statusBar().currentMessage()
    first = _photo(win)
    assert first.eye == "OD"                                                        # 화면에 보이는 눈 = 저장된 눈
    _key(_target(), F6)                                                             # 길게 누름(자동 반복) → 한 번만, 그대로 OD
    for _ in range(4):
        _key(_target(), F6, repeat=True)
    _pump(qapp)
    _key(_target(), F6, press=False)
    _click(_target(), F6)                                                           # OD 신호가 또 와도 OD (토글 아님)
    _pump(qapp)
    assert cb.current_eye() == "OD"
    _click(_target(), F7)                                                           # 신호 4 → OS
    _pump(qapp)
    assert cb.current_eye() == "OS" and hud._eye == "OS"
    second = _photo(win)
    assert second.eye == "OS" and win.db.captures.get(first.id).eye == "OD"         # 먼저 저장한 사진은 그대로
    cb.eye_buttons["OD"].click()                                                    # 직접 고르면 '수동'
    _pump(qapp)
    assert cb.current_eye() == "OD" and win.eye_status()["source"] == "manual" and cb.eye_status.text() == "수동"
    assert hud._eye_note == ""
    _click(_target(), F7)                                                           # 다음 신호가 오면 다시 자동
    _pump(qapp)
    assert cb.current_eye() == "OS" and win.eye_status()["source"] == "device"


def test_eye_signal_held_in_text_input_other_screen_and_dialog(qapp, win):
    cb = win.control_bar
    _click(_target(), F6)
    _pump(qapp)
    assert cb.current_eye() == "OD"
    search = win.patient_panel.search
    search.setFocus()
    _pump(qapp)
    _click(search, F7)                                                              # 입력 중: 바꾸지 않고 보류
    _pump(qapp)
    assert cb.current_eye() == "OD" and win.eye_status()["pending"] == "OS"
    assert "보류" in cb.eye_status.text() and win.video_view._eye_note == "확인 필요"
    assert "보류" in win.statusBar().currentMessage()
    QTest.keyClick(search, Qt.Key.Key_Return)                                       # 초점이 영상으로 → 보류 신호 적용
    _pump(qapp)
    assert QApplication.focusWidget() is win.video_view
    assert cb.current_eye() == "OS" and win.eye_status()["pending"] is None and cb.eye_status.text() == "자동"
    win.set_mode("exam")                                                            # 다른 화면: 보류
    _pump(qapp)
    _click(_target(), F6)
    _pump(qapp)
    assert cb.current_eye() == "OS" and win.eye_status()["pending"] == "OD"
    win.set_mode("capture")
    _pump(qapp)
    assert cb.current_eye() == "OD" and win.eye_status()["pending"] is None
    from app.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(win.config, win)                                           # 설정 창(모달): 보류
    dlg.show()
    dlg.activateWindow()
    assert _wait(qapp, lambda: QApplication.activeWindow() is dlg, 3)
    probe = dlg.usb_tab.probe
    probe.setFocus()
    _click(probe, F7)
    _pump(qapp)
    assert cb.current_eye() == "OD" and win.eye_status()["pending"] == "OS"
    # 흉내 낸 키 이벤트에는 WM_INPUT 이 없어 Windows 는 '장치: 알 수 없음', 리눅스 · 맥은 '장치 확인 없음'
    device = "장치: 알 수 없음" if sys.platform == "win32" else "장치 확인 없음"
    assert "좌우 신호 5" in probe.text() and device in probe.text() and "뗌 확인" in probe.text()
    dlg.close()
    win.activateWindow()                                                            # (오프스크린은 창을 스스로 되살리지 않음)
    assert _wait(qapp, lambda: QApplication.activeWindow() is win, 3)
    win._focus_capture_view()
    _pump(qapp)
    assert cb.current_eye() == "OS" and win.eye_status()["pending"] is None          # 돌아오면 적용


def test_eye_stale_after_inactive_window_and_device_reconnect(qapp, win):
    cb, hud = win.control_bar, win.video_view
    _click(_target(), F6)
    _pump(qapp)
    assert cb.eye_status.text() == "자동"
    f = win.usb_filter
    f.eventFilter(None, QEvent(QEvent.Type.ApplicationDeactivate))                   # 다른 프로그램으로 갔다가
    f.eventFilter(None, QEvent(QEvent.Type.ApplicationActivate))                     # 돌아옴 → 신호를 놓쳤을 수 있음
    _pump(qapp)
    assert cb.current_eye() == "OD" and win.eye_status()["stale"] and cb.eye_status.text() == "확인 필요"
    assert hud._eye == "OD" and hud._eye_note == "확인 필요"
    assert _photo(win).eye == "OD"                                                   # 보이는 눈 그대로 (몰래 바꾸지 않음)
    _click(_target(), F7)                                                            # 다음 신호가 오면 풀림
    _pump(qapp)
    assert cb.current_eye() == "OS" and not win.eye_status()["stale"] and cb.eye_status.text() == "자동"
    win._on_usb_device_change(False)                                                 # 장치 빠짐 → 값은 두고 확인 필요
    _pump(qapp)
    assert cb.current_eye() == "OS" and win.eye_status()["stale"] and "빠졌" in win.statusBar().currentMessage()
    win._on_usb_device_change(True)                                                  # 다시 꽂힘 → 위치 미확인 (OD 로 짐작 안 함)
    _pump(qapp)
    assert cb.current_eye() == "NA" and win.eye_status()["source"] == "unknown" and cb.eye_status.text() == "미확인"
    cb.eye_buttons["OS"].click()
    _pump(qapp)
    win._on_usb_device_change(True)                                                  # 직접 고른 눈은 다시 꽂혀도 유지
    _pump(qapp)
    assert cb.current_eye() == "OS" and win.eye_status()["source"] == "unknown" and hud._eye_note == "확인 필요"


def test_eye_pinned_for_frozen_still_and_recording_start(qapp, win):
    cb, hud = win.control_bar, win.video_view
    _click(_target(), F6)
    _pump(qapp)
    _click(_target(), F10)                                                           # 스틸 정지 (OD 로 찍힘)
    _pump(qapp)
    assert win._single_frozen and win.eye_status()["freeze_eye"] == "OD"
    _click(_target(), F7)                                                            # 정지 중 슬릿램프가 왼눈으로
    _pump(qapp)
    assert cb.current_eye() == "OS" and hud._eye == "OD" and hud._eye_note == "정지 화면"
    assert "정지 화면은 찍은 순간의 눈 OD" in win.statusBar().currentMessage()
    assert _photo(win).eye == "OD"                                                   # 정지 화면은 찍은 순간의 눈으로 저장
    cb.eye_buttons["OS"].click()                                                     # 직접 고치면 그 사진도 고친 눈
    _pump(qapp)
    assert win.eye_status()["freeze_eye"] == "OS" and hud._eye == "OS" and _photo(win).eye == "OS"
    _click(_target(), F10)                                                           # 라이브로
    _pump(qapp)
    assert not win._single_frozen and win.eye_status()["freeze_eye"] is None
    _click(_target(), F6)
    _pump(qapp)
    _click(_target(), F9)                                                            # 녹화 시작 (OD)
    _pump(qapp)
    if not win._recording:
        pytest.skip("인코더 없음")
    assert _wait(qapp, lambda: win.recorder.frame_count >= 3)
    _click(_target(), F7)                                                            # 녹화 중 전환
    _pump(qapp)
    assert cb.current_eye() == "OS" and win._recording and win._video_eye == "OD"
    assert "녹화 중 전환" in win.statusBar().currentMessage()
    _click(_target(), F9)
    _pump(qapp)
    assert _wait(qapp, lambda: win._pending_video is None and not win.recorder.is_finalizing, 20)
    vids = [c for c in win.db.captures.list_by_patient(win._current_patient.id) if c.media_type == "video"]
    assert vids and vids[0].eye == "OD"                                              # 영상 전체는 시작 눈 — 덮어쓰지 않음
    assert "녹화 중 좌우 전환" in (vids[0].note or "") and "OS 0:0" in vids[0].note and "시작 눈 OD" in vids[0].note


def test_eye_auto_off_and_no_shortcut_conflict(qapp, win):
    cb = win.control_bar
    win.config.usb_eye_auto = False
    win._refresh_eye_status()
    assert not cb.eye_status.isVisible() and win.video_view._eye_note == ""
    _click(_target(), F7)                                                            # 꺼져 있으면 OS 신호를 받아도 그대로 (시작 OD)
    _pump(qapp)
    assert cb.current_eye() == "OD" and "꺼져" in win.statusBar().currentMessage()
    cb.eye_buttons["NA"].click()
    assert cb.current_eye() == "NA"
    cb.eye_buttons["OD"].click()
    assert cb.current_eye() == "OD"                                                  # 직접 고르기는 그대로
    win.config.usb_eye_auto = True
    win._refresh_eye_status()
    QTest.keyClick(win, Qt.Key.Key_2)                                                # 단축키 2 = OS (좌우 신호와 겹치지 않음)
    assert cb.current_eye() == "OS" and win.eye_status()["source"] == "manual"
    taken = [b["key"] for b in usb_buttons.load(win.config).values()]
    assert any(usb_buttons.same_key(k, "Ctrl+Shift+Alt+F6") for k in taken)
    win.config.shortcuts = {"eyeOD": "Ctrl+Shift+Alt+F6"}                            # 같은 키를 단축키에 두면 USB 가 먼저
    win._install_shortcuts()
    assert "eyeOD" not in win._shortcut_objs and "eyeOS" in win._shortcut_objs
    win.config.shortcuts = {}
    win._install_shortcuts()


@pytest.mark.parametrize("start, eye, source, chip", [("OS", "OS", "default", "기본 OS"), ("NA", "NA", "unknown", "미확인")])
def test_start_eye_setting(qapp, app_env, start, eye, source, chip):
    """설정 start_eye: 켰을 때 미리 골라 둘 눈 (NA 면 예전처럼 미확인) — 좌우 신호가 오면 그 눈으로."""
    from app.models.patient import Patient
    from app.ui.main_window import MainWindow
    config, db = app_env
    config.source_mode = "synthetic"
    config.save = lambda: None
    config.capture_width, config.capture_height = 160, 120
    config.start_eye = start
    db.patients.insert(Patient(chart_no="SE1", name="시작눈"))
    w = MainWindow(config, db)
    try:
        assert w.control_bar.current_eye() == eye and w.eye_status()["source"] == source
        assert w.control_bar.eye_status.text() == chip
        w._apply_device_eye("OD")
        assert w.control_bar.current_eye() == "OD" and w.eye_status()["source"] == "device"
    finally:
        w.close()
        w.deleteLater()

"""보기 창(한 장 화면) — 사진 왼쪽 위 OD · OS 표시(멀티 칸과 같게), 양옆 ‹ › · ←/→ 로 사진 넘기기,
돋보기 · 이동 · 맞춤 도구 줄 없앰. 넘긴 뒤의 삭제 · 전송 · 질환 비교는 지금 보이는 사진으로."""
from __future__ import annotations

import time

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from app.models.patient import Patient
from app.services.capture_service import CaptureService
from app.services.send_service import SendResult
from app.ui.image_viewer import ImageViewerDialog
from app.ui.multi_view import _tag, eye_time


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


@pytest.fixture()
def win(qapp, app_env, monkeypatch):
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 320, 240
    config.save = lambda: None
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    db.patients.insert(Patient(chart_no="N1", name="넘기기"))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.resize(1400, 860)
    w.show()
    assert _wait(qapp, lambda: w._last_frame is not None)
    i = next(k for k in range(w.patient_panel.combo.count()) if "N1" in w.patient_panel.combo.itemText(k))
    w.patient_panel.combo.setCurrentIndex(i)
    qapp.processEvents()
    yield w
    w._close_capture_viewer()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _photos(win, n, eyes=("OD", "OS")):
    """밝기가 다른 사진 n 장 (촬영 순서 = id 순서)."""
    svc = CaptureService(win.db, win.config)
    p = win._current_patient
    caps = [svc.save_photo(np.full((120, 160, 3), 30 + i * 40, np.uint8), p, eye=eyes[i % len(eyes)])
            for i in range(n)]
    win.thumbnail_panel.set_patient(p.id)
    _pump(QApplication.instance(), 5)
    return caps


def _shown_level(dlg) -> int:
    return int(dlg.canvas.image()[0, 0, 0])


# ---------------- 1. 사진 왼쪽 위 OD · OS 표시 ----------------
def test_caption_matches_multi_cell(qapp, win):
    caps = _photos(win, 2)
    dlg = ImageViewerDialog(caps[1], win)
    dlg.show()
    _pump(qapp)
    cap = caps[1]
    assert cap.eye == "OS"
    assert dlg.canvas._caption == f"OS  {cap.captured_at[5:16]}" == eye_time(cap)
    assert _tag(1, cap) == f"2  {dlg.canvas._caption}"          # 멀티 칸 = 번호 + 같은 표시
    dlg.close()


def test_caption_without_eye_and_reference():
    from types import SimpleNamespace

    from app.ui.multi_view import RefCapture
    na = SimpleNamespace(eye="NA", captured_at="2026-10-30 10:07:24", media_type="photo")
    assert eye_time(na) == "10-30 10:07" and _tag(0, na) == "1  10-30 10:07"
    ref = RefCapture(ref=SimpleNamespace(category="백내장", title="핵백내장"), id="ref:x", file_path="")
    assert eye_time(ref) == "참조 · 백내장 · 핵백내장" and _tag(2, ref) == "3  참조 · 백내장 · 핵백내장"


# ---------------- 2. 돋보기 · 이동 · 맞춤 도구 줄 없음 ----------------
def test_no_magnifier_toolbar(qapp, win):
    caps = _photos(win, 1)
    for after in (False, True):
        dlg = ImageViewerDialog(caps[0], win, after_capture=after)
        texts = [b.text().strip() for b in dlg.findChildren(QPushButton)]
        assert not any(t in ("돋보기", "이동", "맞춤") for t in texts), texts
        assert dlg.canvas.tool() == "magnify"                     # 누르고 있으면 확대 렌즈는 그대로
        dlg.close()


# ---------------- 3. ‹ › · ←/→ 로 넘기기 ----------------
def test_prev_next_buttons_and_keys(qapp, win):
    caps = _photos(win, 4)
    seq = win._viewer_photos(caps[2])
    assert [c.id for c in seq] == [c.id for c in caps]           # 촬영 순서 (목록은 최근이 위여도)
    dlg = ImageViewerDialog(caps[2], win, captures=seq)
    dlg.show()
    _pump(qapp)
    assert dlg.photo_count() == 4 and dlg.current_index() == 2 and dlg.capture is caps[2]
    assert dlg.b_prev.isVisible() and dlg.b_next.isVisible() and dlg.lbl_pos.text() == "3 / 4"
    assert _shown_level(dlg) == 30 + 2 * 40
    QTest.mouseClick(dlg.b_next, Qt.MouseButton.LeftButton)       # 다음 (마지막)
    assert dlg.capture.id == caps[3].id and dlg.lbl_pos.text() == "4 / 4"
    assert _shown_level(dlg) == 30 + 3 * 40
    assert not dlg.b_next.isEnabled() and dlg.b_prev.isEnabled()
    assert dlg.canvas._caption == eye_time(caps[3]) and caps[3].captured_at in dlg.windowTitle()
    assert dlg.step(1) is False and dlg.capture.id == caps[3].id   # 끝에서는 멈춤
    for _ in range(3):
        QTest.keyClick(dlg, Qt.Key.Key_Left)                      # ← 키
    assert dlg.capture.id == caps[0].id and dlg.lbl_pos.text() == "1 / 4"
    assert not dlg.b_prev.isEnabled() and dlg.b_next.isEnabled()
    QTest.keyClick(dlg, Qt.Key.Key_Left)
    assert dlg.capture.id == caps[0].id
    dlg.canvas.setFocus()                                         # 사진에 초점이 있어도 ←/→ 는 넘기기
    QTest.keyClick(dlg.canvas, Qt.Key.Key_Right)
    assert dlg.capture.id == caps[1].id and dlg.canvas._caption == eye_time(caps[1])
    dlg.close()


def test_nav_hidden_for_single_photo_and_video_skipped(qapp, win):
    caps = _photos(win, 1)
    dlg = ImageViewerDialog(caps[0], win, captures=win._viewer_photos(caps[0]))
    dlg.show()
    _pump(qapp)
    assert dlg.photo_count() == 1 and not dlg.b_prev.isVisible() and not dlg.b_next.isVisible()
    assert not dlg.lbl_pos.isVisible()
    dlg.close()
    # 동영상은 넘길 목록에서 빠진다 (외부 플레이어로 열리는 것)
    from dataclasses import replace
    video = replace(caps[0], id=-1, media_type="video")
    more = _photos(win, 2)
    seq = [caps[0], video] + more
    dlg = ImageViewerDialog(caps[0], win, captures=seq)
    assert dlg.photo_count() == 3
    assert dlg.step(1) and dlg.capture.id == more[0].id
    dlg.close()
    assert len(win._viewer_photos(caps[0], pool=seq)) == 3


def test_viewer_follows_thumbnail_filter(qapp, win):
    caps = _photos(win, 4)                                         # OD, OS, OD, OS
    win.thumbnail_panel.set_filter("OD")
    seq = win._viewer_photos(caps[2])
    assert [c.id for c in seq] == [caps[0].id, caps[2].id]       # 목록에 보이는 사진만
    win.thumbnail_panel.set_filter("all")


# ---------------- 4. 넘긴 뒤 삭제 = 지금 보이는 사진 ----------------
def test_delete_after_navigating_deletes_shown_photo(qapp, win, monkeypatch):
    caps = _photos(win, 3)

    def fake_exec(self):
        assert self.photo_count() == 3
        self.step(-1)                                              # 3번째 → 2번째 사진으로 넘긴 뒤 삭제
        return ImageViewerDialog.Deleted
    monkeypatch.setattr(ImageViewerDialog, "exec", fake_exec)
    win._on_thumbnail_clicked(caps[2])
    left = [c.id for c in win.db.captures.list_by_patient(win._current_patient.id, order="asc")]
    assert left == [caps[0].id, caps[2].id]


def test_exam_screen_viewer_steps_through_that_day(qapp, win, monkeypatch):
    caps = _photos(win, 3)
    seen = []

    def fake_exec(self):
        seen.append([c.id for c in self._seq])
        return 0
    monkeypatch.setattr(ImageViewerDialog, "exec", fake_exec)
    scr = win.exam_screen
    scr.set_patient(win._current_patient)
    _pump(qapp)
    day = (caps[0].captured_at or "")[:10]
    scr._show_day(day)
    scr.openCapture.emit(caps[1])
    assert seen and seen[-1] == [c.id for c in sorted(scr.day_captures(), key=lambda c: (c.captured_at, c.id))]
    assert caps[1].id in seen[-1]


# ---------------- 5. 촬영 직후 큰 화면에서도 넘기기 · 전송은 보이는 사진 ----------------
def test_after_capture_viewer_navigates_and_sends_shown_photo(qapp, win, monkeypatch):
    win.config.view_after_capture = True
    old = _photos(win, 2)
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    assert v is not None and v.after_capture and v.photo_count() == 3 and v.current_index() == 2
    new = v.capture
    assert v.windowTitle().startswith("촬영한 사진 - ") and v.lbl_pos.text() == "3 / 3"
    sent = []
    monkeypatch.setattr(win, "_send_captures",
                        lambda caps, parent=None: sent.append([c.id for c in caps]) or SendResult(sent=["x"]))
    QTest.mouseClick(v.b_send, Qt.MouseButton.LeftButton)
    _pump(qapp)
    assert sent[-1] == [new.id] and v.lbl_sent.text() == "✓ 보냄"
    QTest.keyClick(v, Qt.Key.Key_Left)                             # 앞 사진으로 — 아직 안 보낸 사진
    assert v.capture.id == old[1].id and v.lbl_sent.text() == ""
    assert v.windowTitle() == f"촬영한 사진 - {old[1].captured_at}"
    QTest.mouseClick(v.b_send, Qt.MouseButton.LeftButton)
    _pump(qapp)
    assert sent[-1] == [old[1].id]
    QTest.mouseClick(v.b_next, Qt.MouseButton.LeftButton)          # 다시 방금 찍은 사진 — 보낸 표시가 남아 있음
    assert v.capture is new and v.lbl_sent.text() == "✓ 보냄"
    # 넘긴 뒤 삭제 → 그때 보이던 사진이 지워진다
    QTest.keyClick(v, Qt.Key.Key_Left)
    QTest.keyClick(v, Qt.Key.Key_Left)
    assert v.capture.id == old[0].id
    v.done(ImageViewerDialog.Deleted)
    _pump(qapp)
    left = {c.id for c in win.db.captures.list_by_patient(win._current_patient.id)}
    assert left == {old[1].id, new.id} and win._capture_viewer is None


def test_after_capture_viewer_dx_uses_shown_photo(qapp, win, monkeypatch):
    win.config.view_after_capture = True
    old = _photos(win, 1)
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    calls = []
    monkeypatch.setattr(win, "_dx_from_viewer", lambda d, cap, s, c: calls.append(cap.id))
    v.step(-1)
    v.dxClicked.emit("sample", "백내장")
    assert calls == [old[0].id]
    win._close_capture_viewer()

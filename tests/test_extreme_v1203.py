"""극한 상황 테스트 3 — v1.20.2 · v1.20.3 에서 들어온 촬영 화면 기능.

오른쪽 클릭 메뉴(전송 · 전송(삭제) · 삭제) · 촬영 직후 큰 화면의 전송(삭제) · 뒤에서 선명한 썸네일 다시 만들기 ·
환자 검색으로 고르기 · 검사 소견 · 새 설정 값 · 캡처 = 초기화 · 실시간 멀티화면.
어떤 경우에도 보내지 않은 사진이 지워지거나, 다른 날 · 다른 환자의 소견이 보고서에 붙거나, 지운 사진의 파일이 되살아나거나,
적던 소견이 사라지거나, 치기만 한 검색어로 환자가 바뀌지 않아야 한다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.models.capture import Capture
from app.models.patient import Patient
from app.services import send_service as ss


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(qapp, cond, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


def _pump(qapp, n=10):
    for _ in range(n):
        qapp.processEvents()


def _photo(path: Path, v: int = 90, w: int = 640, h: int = 480) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = np.full((h, w, 3), v, np.uint8)
    cv2.circle(img, (w // 2, h // 2), h // 4, (255 - v, v, 30), -1)
    path.write_bytes(cv2.imencode(path.suffix or ".png", img)[1].tobytes())
    return path


def _insert(db, pid, path, media="photo", day="2025-01-05 09:30:00", eye="OD", thumb=None) -> Capture:
    c = Capture(patient_id=pid, media_type=media, file_path=str(path), thumbnail_path=(str(thumb) if thumb else None),
                captured_at=day, eye=eye)
    c.id = db.captures.insert(c)
    return db.captures.get(c.id)


# =====================================================================================
# 1. 전송 — '모두 보냈다' 는 판정이 정확해야 (전송(삭제)가 이 판정으로 지운다)
# =====================================================================================
def test_send_pdf_only_marks_video_and_unreadable_photo_as_skipped(app_env, tmp_path):
    """형식 'PDF 만' — 동영상 · 읽지 못한 사진은 PDF 에 들어가지 않는다. 건너뜀으로 적어야 '모두 보냄' 이 아니게 된다
    (안 적으면 전송(삭제)가 보내지도 않은 동영상을 지운다)."""
    config, db = app_env
    p = Patient(chart_no="S1", name="전송")
    p.id = db.patients.insert(p)
    good = _insert(db, p.id, _photo(tmp_path / "m" / "a.png"))
    video = _insert(db, p.id, tmp_path / "m" / "v.mp4", media="video")
    Path(video.file_path).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
    broken = _insert(db, p.id, tmp_path / "m" / "broken.png")
    Path(broken.file_path).write_bytes(b"not a png")
    config.send_method, config.send_folder = "folder", str(tmp_path / "out")
    written = []
    res = ss.send([good, video, broken], p, config, formats=["pdf"],
                  pdf_writer=lambda path, pt, items, notes: (written.append(len(items)), Path(path).write_bytes(b"%PDF")))
    assert written == [1]
    names = " ".join(n + w for n, w in res.skipped)
    assert len(res.skipped) == 2, res.skipped
    assert "동영상" in names and "읽지 못함" in names
    assert not (res.ok and not res.skipped), "보내지 않은 동영상이 있는데 '모두 보냄' 으로 판정"


# =====================================================================================
# 2. 촬영 창 — 전송(삭제) · 오른쪽 클릭 삭제 · 검사 소견 · 환자 검색 · 초기화 · 실시간 멀티
# =====================================================================================
@pytest.fixture()
def win(qapp, app_env, monkeypatch, tmp_path):
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 320, 240
    config.view_after_capture = False
    config.save = lambda: None
    config.send_method, config.send_folder = "folder", str(tmp_path / "sent")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    for chart, name in (("WA", "가환자"), ("WB", "나환자"), ("WC", "다환자")):
        db.patients.insert(Patient(chart_no=chart, name=name))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.resize(1500, 900)
    w.show()
    assert _wait(qapp, lambda: w._last_frame is not None, 10)
    w.patient_panel.select_patient(_pid(w, "WA"))
    _pump(qapp)
    yield w
    w._close_capture_viewer()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _pid(win, chart):
    return next(p.id for p in win.db.patients.list_all() if p.chart_no == chart)


def _caps(win, chart="WA"):
    return win.db.captures.list_by_patient(_pid(win, chart))


def _fake_send_dialog(monkeypatch, formats):
    from app.ui import send_dialog
    monkeypatch.setattr(send_dialog.SendDialog, "exec", lambda self: 1)
    monkeypatch.setattr(send_dialog.SendDialog, "selected_formats", lambda self: list(formats))


def test_send_delete_pdf_only_never_deletes_unsent_video(qapp, win, monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(win, "_write_send_pdf", lambda path, pt, items, notes: Path(path).write_bytes(b"%PDF"))
    _fake_send_dialog(monkeypatch, ["pdf"])
    win._on_capture()
    _pump(qapp)
    v = tmp_path / "rec.mp4"
    v.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
    _insert(win.db, _pid(win, "WA"), v, media="video", day=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    win.thumbnail_panel.set_patient(_pid(win, "WA"))
    for c in win.thumbnail_panel._items:
        win.thumbnail_panel.toggle_selection(c)
    assert len(win.thumbnail_panel.selected_captures()) == 2
    win._send_and_delete()
    _pump(qapp)
    assert len(_caps(win)) == 2 and v.is_file(), "PDF 에 들어가지 않은 동영상을 지웠다"
    assert asked == []                                                     # 삭제 확인까지 가지도 않음


def test_send_pdf_notes_use_the_photos_exam_day_not_today(qapp, win, monkeypatch, tmp_path):
    """보고서(PDF)의 소견은 그 사진을 찍은 날의 검사 소견이어야 한다 — 예전 사진을 오늘 보내면 오늘(다른 내원)의 소견이 붙었다."""
    pid = _pid(win, "WA")
    old = _insert(win.db, pid, _photo(tmp_path / "m" / "old.png"), day="2025-01-05 09:30:00")
    today = datetime.now().strftime("%Y-%m-%d")
    win.db.exam_notes.upsert(pid, "2025-01-05", "", "옛 내원: 각막 미란")
    win.db.exam_notes.upsert(pid, today, "", "오늘 내원: 결막염")
    got = []
    monkeypatch.setattr(win, "_write_send_pdf",
                        lambda path, pt, items, notes: (got.append(notes), Path(path).write_bytes(b"%PDF")))
    _fake_send_dialog(monkeypatch, ["pdf"])
    res = win._send_captures([old])
    assert res is not None and res.ok and got
    assert "각막 미란" in got[-1] and "결막염" not in got[-1], got[-1]
    win._on_capture()
    _pump(qapp)
    now_cap = next(c for c in _caps(win) if c.captured_at.startswith(today))
    win._send_captures([old, now_cap])
    both = got[-1]
    assert "2025-01-05" in both and today in both and "각막 미란" in both and "결막염" in both, both   # 여러 날이면 날짜별로


def test_send_delete_deletes_exactly_what_was_sent(qapp, win, monkeypatch):
    """보낸 뒤 확인 창이 떠 있는 동안 고른 사진이 바뀌어도(자동 촬영 · 목록 갱신) 지우는 것은 보낸 그 사진들뿐."""
    for _ in range(3):
        win._on_capture()
    _pump(qapp)
    caps = list(reversed(_caps(win)))
    win.thumbnail_panel.toggle_selection(caps[0])
    sent_ids = []

    def fake_send(cs, parent=None):
        sent_ids.extend(c.id for c in cs)
        win.thumbnail_panel.toggle_selection(caps[1])                     # 그 사이 선택이 늘어남
        win.thumbnail_panel.toggle_selection(caps[2])
        return ss.SendResult(sent=["x"])
    monkeypatch.setattr(win, "_send_captures", fake_send)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win._send_and_delete()
    _pump(qapp)
    left = {c.id for c in _caps(win)}
    assert sent_ids == [caps[0].id]
    assert left == {caps[1].id, caps[2].id}, "보내지 않은 사진까지 지웠다"


def test_right_click_delete_warns_about_selected_photos_hidden_by_filter(qapp, win, monkeypatch):
    win.control_bar.set_eye("OD")
    win._on_capture()
    win._on_capture()
    win.control_bar.set_eye("OS")
    win._on_capture()
    _pump(qapp)
    tp = win.thumbnail_panel
    od = [c for c in tp._items if c.eye == "OD"]
    os_ = [c for c in tp._items if c.eye == "OS"]
    assert len(od) == 2 and len(os_) == 1
    for c in od:
        tp.toggle_selection(c)
    tp.set_filter("OS")                                                    # OD 2장은 가려졌지만 고른 상태
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
    tp._on_right(os_[0])
    tp.context_menu.hide()
    tp._menu_do("delete")
    _pump(qapp)
    assert asked, "확인 창이 뜨지 않았다"
    assert "3" in asked[0] and "보이지 않는" in asked[0] and "2" in asked[0], asked[0]
    assert len(_caps(win)) == 3                                            # 아니요 → 그대로


def test_exam_notes_typed_then_photo_deleted_are_not_lost(qapp, win):
    win._on_capture()
    win._on_capture()
    _pump(qapp)
    caps = _caps(win)
    day = caps[0].captured_at[:10]
    win.set_mode("exam")
    _pump(qapp)
    es = win.exam_screen
    assert es.current_day() == day
    es.txt_notes.setPlainText("각막 중심부 상피 결손, 형광 염색 양성")          # 적는 중 (아직 저장 안 됨)
    assert es._notes_dirty
    assert win._delete_capture(caps[0])                                    # 보기 창에서 사진 한 장 삭제 → 목록 다시 읽기
    _pump(qapp)
    saved = win.db.exam_notes.get(_pid(win, "WA"), day)
    assert es.txt_notes.toPlainText() == "각막 중심부 상피 결손, 형광 염색 양성", "적던 소견이 지워졌다"
    assert saved and "상피 결손" in (saved.get("notes") or "")


def test_exam_notes_go_to_the_patient_they_were_written_for(qapp, win):
    win._on_capture()
    _pump(qapp)
    day = _caps(win)[0].captured_at[:10]
    win.set_mode("exam")
    _pump(qapp)
    win.exam_screen.txt_notes.setPlainText("가환자 소견")
    win.patient_panel.select_patient(_pid(win, "WB"))                     # 저장하지 않고 다른 환자로
    _pump(qapp)
    a = win.db.exam_notes.get(_pid(win, "WA"), day)
    b = win.db.exam_notes.get(_pid(win, "WB"), day)
    assert a and a["notes"] == "가환자 소견"
    assert not b or "가환자" not in (b.get("notes") or "")


def test_patient_search_typing_never_switches_patient(qapp, win):
    pp = win.patient_panel
    a = _pid(win, "WA")
    assert win._current_patient.id == a
    for q in ("나", "WB", "다환자", "w", "zzz-없음", "%", "_", "' OR 1=1 --"):
        pp.search.setText(q)
        _pump(qapp, 3)
        assert win._current_patient.id == a, f"검색어 {q!r} 를 치기만 했는데 환자가 바뀌었다"
    pp.search.setText("zzz-없음")
    pp._pick_first()                                                       # 결과 없음 + Enter → 그대로
    assert win._current_patient.id == a
    pp.search.setText("나환자")
    pp._pick_first()                                                       # Enter → 첫 결과
    _pump(qapp)
    assert win._current_patient.id == _pid(win, "WB") and pp.search.text() == ""


def test_patient_search_keyboard_choice_picks_highlighted_row(qapp, win):
    pp = win.patient_panel
    win.activateWindow()
    pp.search.setFocus()
    _pump(qapp)
    QTest.keyClicks(pp.search, "W")         # 차트번호 WA · WB · WC 모두 (QTest 키 입력은 영문만 — 한글을 주면 Qt 시험 도구가 중단)
    _pump(qapp)
    assert len(pp._matches) == 3
    if not pp.completer.popup().isVisible():
        pytest.skip("오프스크린에서 결과 목록이 뜨지 않음")
    popup = pp.completer.popup()
    QTest.keyClick(popup, Qt.Key.Key_Down)
    QTest.keyClick(popup, Qt.Key.Key_Down)
    _pump(qapp)
    row = popup.currentIndex().row()
    want = pp._matches[row].id if 0 <= row < len(pp._matches) else None
    assert len(pp._matches) == 3, "결과를 고르는 중에 목록이 바뀌었다"
    QTest.keyClick(popup, Qt.Key.Key_Return)
    _wait(qapp, lambda: win._current_patient is not None and win._current_patient.id == want, 2)
    assert want is not None and win._current_patient.id == want


def test_capture_reset_in_multi_never_saves_or_deletes(qapp, win):
    win.config.capture_in_view = "reset"
    win._on_capture()
    win._on_capture()
    _pump(qapp)
    for c in win.thumbnail_panel._items:
        win.thumbnail_panel.toggle_selection(c)
    win._multi_by_right_click()
    _pump(qapp)
    assert win._multi
    win._on_freeze()                                                       # 정지 화면까지
    n = len(_caps(win))
    files = sorted(c.file_path for c in _caps(win))
    win._on_capture()                                                      # 멀티에서 캡처 = 초기화
    _pump(qapp)
    assert not win._multi and len(_caps(win)) == n
    assert sorted(c.file_path for c in _caps(win)) == files and all(os.path.isfile(f) for f in files)
    win._on_capture()                                                      # 한 번 더 = 촬영
    _pump(qapp)
    assert len(_caps(win)) == n + 1


def test_live_multi_delete_selected_leaves_no_deleted_photo_on_screen(qapp, win, monkeypatch):
    win.config.live_multi = True
    for _ in range(3):
        win._on_capture()
    _pump(qapp)
    tp = win.thumbnail_panel
    caps = list(tp._items)
    tp.toggle_selection(caps[0])
    tp.toggle_selection(caps[1])
    _pump(qapp)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert win._delete_selected() == 2
    _pump(qapp)
    gone = {caps[0].id, caps[1].id}
    shown = {getattr(c, "id", None) for c in win._multi_items()} if win._multi else set()
    assert not (shown & gone)
    assert {c.id for c in _caps(win)} == {caps[2].id}


def test_viewer_send_delete_after_patient_switch_deletes_only_that_photo(qapp, win, monkeypatch):
    win.config.view_after_capture = True
    win._on_capture()
    _pump(qapp)
    v = win._capture_viewer
    cap = v.capture
    win.patient_panel.select_patient(_pid(win, "WB"))                     # 큰 화면이 떠 있는 동안 다른 환자로
    win.config.view_after_capture = False
    win._on_capture()                                                      # 나환자 사진 1장
    _pump(qapp)
    monkeypatch.setattr(win, "_send_captures", lambda cs, parent=None: ss.SendResult(sent=["x"]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win._send_from_viewer(v, cap, delete=True)
    _pump(qapp)
    assert win.db.captures.get(cap.id) is None
    assert len(_caps(win, "WB")) == 1 and all(os.path.isfile(c.file_path) for c in _caps(win, "WB"))


# =====================================================================================
# 3. 뒤에서 선명한 썸네일 다시 만들기 — 지운 사진의 썸네일을 되살리면 안 되고, 임시 파일을 남기면 안 된다
# =====================================================================================
def _small_thumb_capture(db, tmp_path, name="a"):
    p = Patient(chart_no=f"T{name}", name="썸네일")
    p.id = db.patients.insert(p)
    src = _photo(tmp_path / "media" / f"{name}.png", w=1280, h=960)
    thumb = tmp_path / "media" / ".thumbs" / f"{name}.jpg"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(cv2.imencode(".jpg", np.full((90, 120, 3), 50, np.uint8))[1].tobytes())   # 예전 120px 썸네일
    return _insert(db, p.id, src, thumb=thumb)


def test_thumbnail_upgrade_does_not_resurrect_deleted_thumbnail(qapp, app_env, tmp_path, monkeypatch):
    from app.services.capture_service import CaptureService
    from app.ui import thumbnail_widget as tw
    config, db = app_env
    cap = _small_thumb_capture(db, tmp_path)
    real = tw._read_scaled_image
    gate = {"go": False}

    def slow(path, width):
        img = real(path, width)                                           # 원본은 읽었고 …
        end = time.time() + 5
        while not gate["go"] and time.time() < end:
            time.sleep(0.01)                                               # … 그 사이 사진이 지워진다
        return img
    monkeypatch.setattr(tw, "_read_scaled_image", slow)
    tw._cache.clear()
    w = tw.ThumbnailWidget(cap)
    assert cap.thumbnail_path in tw.upgrader().pending
    assert CaptureService(db, config).delete_capture(cap)
    assert not os.path.exists(cap.thumbnail_path) and not os.path.exists(cap.file_path)
    gate["go"] = True
    _wait(qapp, lambda: cap.thumbnail_path not in tw.upgrader().pending, 6)
    assert not os.path.exists(cap.thumbnail_path), "지운 사진의 썸네일이 되살아났다"
    assert not list(Path(cap.thumbnail_path).parent.glob("*.sharp.jpg"))
    w.deleteLater()


def test_thumbnail_upgrade_failed_save_leaves_no_temp_file(qapp, app_env, tmp_path, monkeypatch):
    from app.ui import thumbnail_widget as tw
    config, db = app_env
    cap = _small_thumb_capture(db, tmp_path, "b")
    before = Path(cap.thumbnail_path).read_bytes()

    class _Half(QImage):
        def save(self, path, fmt=None, quality=-1):                       # 디스크 가득 참: 반쯤 쓰고 실패
            Path(path).write_bytes(b"\xff\xd8\xff" + b"0" * 10)
            return False
    real = tw._read_scaled_image
    monkeypatch.setattr(tw, "_read_scaled_image", lambda p, w: _Half(real(p, w)))
    tw._cache.clear()
    w = tw.ThumbnailWidget(cap)
    _wait(qapp, lambda: cap.thumbnail_path not in tw.upgrader().pending, 6)
    assert Path(cap.thumbnail_path).read_bytes() == before                # 원래 썸네일 그대로
    assert not list(Path(cap.thumbnail_path).parent.glob("*.sharp.jpg")), "반쯤 쓴 임시 파일이 남았다"
    w.deleteLater()


def test_backup_skips_thumbnail_upgrade_temp_files():
    from app.services import backup_service as bs
    assert bs._skip_name("011453_694_OD.jpg.sharp.jpg")
    assert not bs._skip_name("011453_694_OD.jpg")


# =====================================================================================
# 4. 새 설정 값 — 손으로 고친 설정 파일의 이상한 값
# =====================================================================================
def test_new_config_values_garbage_fall_back_safely(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from app import sounds
    from app.config import AppConfig
    p = AppConfig.config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "right_click_items": ["send", "rm -rf", "send"], "right_click_action": "explode", "live_multi": "yes",
        "capture_in_view": 3, "start_eye": "LEFT", "sound_volume": 9999, "thumbnail_max_side": 50,
        "backup_interval_min": -10, "backup_root": 123,
    }), encoding="utf-8")
    c = AppConfig.load()
    assert c.right_click_items == ["send", "multi", "delete"] and c.right_click_action == "menu"
    assert c.live_multi is False and c.capture_in_view == "reset"
    assert c.thumbnail_max_side >= 480 and c.backup_root == ""
    assert c.start_eye in ("OD", "OS", "NA")
    assert 0 <= c.sound_volume <= 100
    sounds.set_volume(c.sound_volume)
    assert 0 <= sounds._volume <= 100


def test_send_folder_with_very_long_korean_name_and_chart(app_env, tmp_path):
    """아주 긴 한글 이름 · 차트번호 — 폴더 · 파일 이름 한 칸이 255바이트(맥 · 리눅스)를 넘어 전송이 전부 실패하면 안 된다."""
    config, db = app_env
    p = Patient(chart_no="차트" * 60, name="김" * 120)
    p.id = db.patients.insert(p)
    cap = _insert(db, p.id, _photo(tmp_path / "m" / "a.png"))
    config.send_method, config.send_folder = "folder", str(tmp_path / "out")
    res = ss.send([cap], p, config, formats=["original", "jpg"])
    assert res.ok and not res.skipped and len(res.sent) == 2, res.summary()
    for f in res.sent:
        assert all(len(part.encode("utf-8")) <= 255 for part in Path(f).parts), f
        assert Path(f).is_file()

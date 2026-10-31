"""검사 기록 화면·소견 DAO·홈 화면 테스트."""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from app.models.capture import Capture
from app.models.patient import Patient
from app.services.capture_service import CaptureService
from app.ui.exam_screen import EXAM_TYPES, ExamScreen
from app.ui.home_screen import TILES, HomeScreen


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _patient_with_days(config, db):
    pid = db.patients.insert(Patient(chart_no="E1", name="검사", sex="F", birth_date="1980-03-15", diagnosis="건성안"))
    p = db.patients.get(pid)
    svc = CaptureService(db, config)
    img = np.zeros((60, 80, 3), np.uint8)
    c1 = svc.save_photo(img, p, eye="OD")
    c2 = svc.save_photo(img, p, eye="OS")
    # 다른 날짜의 기록 하나 (captured_at 만 바꿔 넣는다)
    db.captures.insert(Capture(patient_id=pid, media_type="photo", file_path=c1.file_path,
                               thumbnail_path=c1.thumbnail_path, captured_at="2025-01-05 09:30:00", eye="OD"))
    return p, (c1, c2)


def test_exam_notes_dao_upsert_and_cascade(app_env):
    _config, db = app_env
    pid = db.patients.insert(Patient(chart_no="N1", name="소견"))
    assert db.exam_notes.get(pid, "2026-01-01") is None
    row = db.exam_notes.upsert(pid, "2026-01-01", "세극등 검사", "각막 정상")
    assert row["exam_type"] == "세극등 검사" and row["notes"] == "각막 정상"
    row = db.exam_notes.upsert(pid, "2026-01-01", "", "  각막 혼탁  ")        # 같은 날 → 덮어쓰기
    assert row["exam_type"] is None and row["notes"] == "각막 혼탁"
    assert list(db.exam_notes.list_by_patient(pid)) == ["2026-01-01"]
    db.patients.delete(pid)
    assert db.exam_notes.get(pid, "2026-01-01") is None                       # 환자 삭제 시 함께 삭제


def test_exam_screen_groups_days_and_saves_notes(qapp, app_env):
    config, db = app_env
    p, (c1, c2) = _patient_with_days(config, db)
    scr = ExamScreen(db, config)
    scr.set_patient(p)
    assert scr.day_list.count() == 2 and scr.badge_days.text() == "2"
    assert scr.current_day() == c1.captured_at[:10]                           # 최신 날짜가 먼저 선택
    assert {c.id for c in scr.day_captures()} == {c1.id, c2.id}
    assert scr.grid.count() == 2 and scr.txt_notes.isEnabled()
    # 검사 종류를 고르면 빈 소견란에 서식이 채워진다
    idx = [n for n, _ in EXAM_TYPES].index("건성안")
    scr.cmb_type.setCurrentIndex(idx)
    assert "TBUT" in scr.txt_notes.toPlainText()
    scr.txt_notes.setPlainText("TBUT: 5초\n각막 염색: 하방 점상")
    scr.save_notes()
    saved = db.exam_notes.get(p.id, scr.current_day())
    assert saved["exam_type"] == "건성안" and "5초" in saved["notes"]
    assert scr.lbl_saved.text().startswith("저장됨")
    # 다른 날짜로 가면 (적던 내용은 flush) 그 날의 소견은 비어 있다
    scr.txt_notes.setPlainText("TBUT: 5초\n각막 염색: 하방 점상\n추가")
    scr.day_list.setCurrentRow(1)
    assert scr.current_day() == "2025-01-05" and scr.txt_notes.toPlainText() == ""
    assert "추가" in db.exam_notes.get(p.id, c1.captured_at[:10])["notes"]
    assert len(scr.day_captures()) == 1
    # 보고서 항목은 사진만, 소견 글 포함
    scr.day_list.setCurrentRow(0)
    assert len(scr.report_items()) == 2
    assert "[건성안]" in scr._notes_for_report()
    # 환자 없음
    scr.set_patient(None)
    assert scr.day_list.count() == 0 and not scr.txt_notes.isEnabled()
    scr.deleteLater()
    qapp.processEvents()


def test_exam_pdf_with_notes(qapp, app_env, tmp_path):
    config, db = app_env
    p, _ = _patient_with_days(config, db)
    scr = ExamScreen(db, config)
    scr.set_patient(p)
    scr.txt_notes.setPlainText("소견 테스트")
    out = tmp_path / "exam.pdf"
    assert scr.export_pdf(str(out)) == 1 and out.stat().st_size > 1000
    scr.deleteLater()
    qapp.processEvents()


def test_home_screen_tiles_and_start_option(qapp, app_env):
    config, _db = app_env
    config.save = lambda: None
    home = HomeScreen(config)
    got = []
    home.action.connect(got.append)
    assert set(home.tiles) == {t[0] for t in TILES}
    home.tiles["exam"].click()
    home.btn_keys.click()
    assert got == ["exam", "shortcuts"]
    home.chk_skip.setChecked(True)
    assert config.start_screen == "capture"
    from app import __version__
    assert home.lbl_version.text() == f"Version {__version__}"


def test_main_window_modes(qapp, app_env):
    config, db = app_env
    config.source_mode = "synthetic"
    config.start_screen = "home"
    config.save = lambda: None
    p, _ = _patient_with_days(config, db)
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    try:
        assert w.mode() == "home" and w.btn_mode_home.isChecked()
        assert w.patient_panel.parentWidget().objectName() == "TopBar"         # 검색·선택은 상단바에
        assert not w.patient_panel.summary.isVisibleTo(w)                        # 환자 요약 줄 없음
        assert not w._shortcut_objs["capture"].isEnabled()                   # 홈에서는 촬영 키 꺼짐
        w.home.tiles["capture"].click()
        assert w.mode() == "capture" and w.patient_panel.isVisibleTo(w) and w._shortcut_objs["capture"].isEnabled()
        w.set_mode("exam")
        assert w.mode_stack.currentIndex() == 2 and w.exam_screen.day_list.count() == 2
        # v1.20.3: Pro 검토로 들어가는 길(상단 버튼 · F2 · 홈 타일 · 검사 기록의 'Pro 검토')을 모두 없앰
        assert not hasattr(w, "act_pro") and not hasattr(w, "btn_mode_pro")
        assert not hasattr(w.exam_screen, "btn_pro") and not hasattr(w.exam_screen, "openInPro")
        assert "pro" not in w.home.tiles
        w._run_action("mode")                                                 # 예전 F2 동작 id: 아무 일 없음
        assert w.mode() == "exam"
        w._on_home_action("pro")
        assert w.mode() == "exam"
        w.set_mode("home")
        assert w.mode_stack.currentIndex() == 3
    finally:
        w.close()
        w.deleteLater()
        qapp.processEvents()


def test_start_screen_capture_skips_home(qapp, app_env):
    config, db = app_env
    config.source_mode = "synthetic"
    config.start_screen = "capture"
    config.save = lambda: None
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    try:
        assert w.mode() == "capture" and w.btn_mode_capture.isChecked()
    finally:
        w.close()
        w.deleteLater()
        qapp.processEvents()


def test_delete_day_from_thumbnail_panel_and_exam_screen(qapp, app_env):
    """촬영일 제목 줄의 삭제 버튼(썸네일) · '날짜 삭제'(검사 기록) → 메인 창이 그 날 영상만 모두 지운다."""
    config, db = app_env
    config.source_mode = "synthetic"
    config.start_screen = "capture"
    config.save = lambda: None
    p, (c1, c2) = _patient_with_days(config, db)
    today = c1.captured_at[:10]
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    try:
        w._on_patient_changed(p)
        panel = w.thumbnail_panel
        assert set(panel.day_delete_buttons) == {today, "2025-01-05"}         # 촬영일마다 삭제 버튼
        assert "2장 모두 삭제" in panel.day_delete_buttons[today].toolTip()

        # 검사 기록 화면: 날짜 선택 → '날짜 삭제' 는 그 날짜로 요청
        w.set_mode("exam")
        got = []
        w.exam_screen.deleteDay.disconnect(w._delete_day)                        # 메인 창의 확인 창 대신 신호만 받는다
        w.exam_screen.deleteDay.connect(got.append)
        w.exam_screen.day_list.setCurrentRow(1)                                # 2025-01-05
        w.exam_screen.btn_delete_day.click()
        assert got == ["2025-01-05"]

        # 실제 삭제 (확인 창 없이): 오늘 2장만 지워지고 다른 날은 남는다
        assert w._delete_day(today, confirm=False) == 2
        ids = {c.id for c in db.captures.list_by_patient(p.id)}
        assert c1.id not in ids and c2.id not in ids and len(ids) == 1
        assert set(panel.day_delete_buttons) == {"2025-01-05"}
        assert w.exam_screen.day_list.count() == 1 and w.exam_screen.current_day() == "2025-01-05"
        assert [c.id for c in w.pro.captures()] == list(ids)                    # Pro 목록도 갱신
        assert w._delete_day(today, confirm=False) == 0                         # 이미 없음 → 안내만
    finally:
        w.close()
        w.deleteLater()
        qapp.processEvents()

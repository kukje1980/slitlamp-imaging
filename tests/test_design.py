"""디자인 개편(Clinical Graphite) UI 구성 요소 테스트."""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.models.patient import Patient
from app.services.capture_service import CaptureService
from app.ui import icons
from app.ui.control_bar import ControlBar
from app.ui.date_field import DateField, normalize_date
from app.ui.patient_panel import PatientPanel, age_of
from app.ui.theme import build_qss
from app.ui.thumbnail_panel import ThumbnailPanel, day_label


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_icons_render_all(qapp):
    assert len(icons.names()) >= 40
    for n in icons.names():
        assert not icons.pixmap(n, "#ffffff", 16).isNull(), n
    assert icons.gap("저장") == " 저장" and icons.gap("") == ""


def test_qss_both_themes(qapp):
    for mode in ("dark", "light"):
        qss = build_qss(mode)
        assert "QPushButton#Shutter" in qss and "QPushButton#EyeBtn" in qss and "QPushButton#NavBtn" in qss
        qapp.setStyleSheet(qss)                               # 파싱 오류 없이 적용


def test_age_and_day_label():
    assert age_of("1968-03-15", date(2026, 3, 14)) == 57
    assert age_of("1968-03-15", date(2026, 3, 15)) == 58
    assert age_of("bad") is None and age_of(None) is None
    assert day_label("2026-10-23") == "2026.10.23 (금)"


def test_eye_segment_syncs_combo(qapp):
    bar = ControlBar()
    seen = []
    bar.eyeChanged.connect(seen.append)
    bar.eye_buttons["OS"].click()
    assert bar.current_eye() == "OS" and seen[-1] == "OS"
    bar.set_eye("OD")
    assert bar.eye_buttons["OD"].isChecked()
    bar.set_freeze_state(True)
    assert "라이브" in bar.btn_freeze.text()


def test_banner_shows_patient_summary(qapp, app_env):
    _config, db = app_env
    db.patients.insert(Patient(chart_no="C9", name="홍길동", sex="M", birth_date="1970-01-01",
                               diagnosis="각막염", doctor="이의사"))
    panel = PatientPanel(db)
    assert panel.lbl_name.text() == "홍길동"
    assert panel.lbl_chart.text() == "#C9"
    meta = panel.lbl_meta.text()
    assert "각막염" in meta and "이의사" in meta and "남" in meta
    assert panel.avatar.text() == "홍"


def test_thumbnail_filter_and_day_groups(qapp, app_env):
    config, db = app_env
    pid = db.patients.insert(Patient(chart_no="T1", name="썸네일"))
    p = db.patients.get(pid)
    svc = CaptureService(db, config)
    img = np.zeros((60, 80, 3), np.uint8)
    for e in ("OD", "OS", "OD"):
        svc.save_photo(img, p, eye=e)
    panel = ThumbnailPanel(db)
    panel.set_patient(pid)
    assert panel.count() == 3 and len(panel.visible_captures()) == 3
    panel.set_filter("OD")
    assert [c.eye for c in panel.visible_captures()] == ["OD", "OD"]
    assert set(panel.filter_buttons) == {"all", "OD", "OS"}            # 동영상 분류는 없앰
    panel.set_split_eyes(True)                                        # OD · OS 나눠 보기
    heads = [panel._lay.itemAt(i).widget().text() for i in range(panel._lay.count())
             if type(panel._lay.itemAt(i).widget()).__name__ == "QLabel"]
    assert heads == ["OD · 우안   ·   2장"]
    panel.set_split_eyes(False)
    panel.set_filter("all")
    cap = svc.save_photo(img, p, eye="OS")
    panel.add_capture(cap)
    assert panel.count() == 4 and panel.visible_captures()[0].id == cap.id   # 최신이 위


def test_normalize_date_accepts_easy_inputs():
    today = date(2026, 10, 26)
    assert normalize_date("19800315", today) == "1980-03-15"
    assert normalize_date("800315", today) == "1980-03-15"          # 주민번호 앞자리처럼
    assert normalize_date("150315", today) == "2015-03-15"          # 올해 두 자리보다 작으면 20xx
    assert normalize_date("1980.3.15", today) == "1980-03-15"
    assert normalize_date("1980/03/15", today) == "1980-03-15"
    assert normalize_date("  ", today) == ""
    assert normalize_date("19801315", today) is None                # 13월
    assert normalize_date("20991231", today) is None                # 미래
    assert normalize_date("abc", today) is None


def test_date_field_normalizes_and_flags(qapp):
    f = DateField("800315")
    assert f.value() == "1980-03-15" and f.edit.text() == "1980-03-15" and f.is_valid()
    f.edit.setText("1980-13-40")
    f.normalize()
    assert not f.is_valid() and f.edit.property("invalid") is True and f.value() == ""
    f.edit.setText("")
    f.normalize()
    assert f.is_valid() and f.value() == ""


def test_patient_dialog_uses_date_field(qapp):
    from app.models.patient import Patient
    from app.ui.patient_dialog import PatientDialog
    dlg = PatientDialog(patient=Patient(chart_no="C1", name="홍길동", birth_date="1970-01-01", sex="F"))
    assert isinstance(dlg.birth, DateField) and dlg.birth.value() == "1970-01-01"
    dlg.birth.edit.setText("19800315")
    p = dlg.get_patient()
    assert p.birth_date == "1980-03-15" and p.sex == "F"


def test_topbar_has_patient_icon_buttons(qapp, app_env):
    config, db = app_env
    config.source_mode = "synthetic"
    config.save = lambda: None
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    try:
        assert w.btn_new_patient.text() == "신규 환자" and w.btn_edit_patient.text() == "정보 수정"
        assert not w.btn_edit_patient.isEnabled()                   # 환자 없음 → 수정 불가
        from app.models.patient import Patient
        pid = db.patients.insert(Patient(chart_no="T1", name="테스트"))
        w.patient_panel._reload()
        assert w.btn_edit_patient.isEnabled() and w._current_patient.id == pid
        # 촬영 도크·상단 메뉴는 아이콘 위·글자 아래 버튼 (QPushButton 기반)
        from app.ui.icon_button import IconTextButton
        assert isinstance(w.control_bar.btn_record, IconTextButton) and isinstance(w.btn_mode_exam, IconTextButton)
    finally:
        w.close()
        w.deleteLater()
        qapp.processEvents()


def test_thumbnail_list_is_paged(qapp, app_env):
    """촬영본이 수천 장인 환자를 골라도 처음엔 PAGE 장만 그리고 '더 보기'로 이어 본다."""
    from app.models.capture import Capture
    from app.ui import thumbnail_panel as tp
    config, db = app_env
    pid = db.patients.insert(Patient(chart_no="P1", name="페이징"))
    for i in range(tp.PAGE * 2 + 10):
        db.captures.insert(Capture(patient_id=pid, media_type="photo", file_path="/none.png",
                                   captured_at=f"2026-01-{i // 100 + 1:02d} 10:{i % 60:02d}:00", eye="OD"))
    panel = tp.ThumbnailPanel(db)
    panel.set_patient(pid)
    assert panel.count() == tp.PAGE * 2 + 10 and panel.shown_count() == tp.PAGE
    btn = panel.findChild(QPushButton, "MoreBtn")
    assert btn is not None and "더 보기" in btn.text()
    btn.click()
    assert panel.shown_count() == tp.PAGE * 2
    panel.set_filter("OS")                    # 조건에 맞는 영상이 없으면 '더 보기' 도 없음
    assert panel.findChild(QPushButton, "MoreBtn") is None

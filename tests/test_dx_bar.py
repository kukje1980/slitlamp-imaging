"""상단바 질환 아이콘(DxBar) → 멀티 화면 (숨긴 참조 선택 모델), 멀티 오른쪽 메뉴 접기, 촬영 직후 큰 화면 보기('다시 촬영')."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.models.patient import Patient
from app.services import sample_import
from app.ui import icons
from app.ui.dx_bar import PREVIEW_MAX, DxBar, icon_for, short_label
from app.ui.image_viewer import ImageViewerDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


def _samples(root: Path) -> Path:
    meta = []
    files = {"01_건성안/건성안_1.jpg": {"title": "건성안 1"}, "01_건성안/건성안_2.jpg": {"title": "건성안 2"},
             "04_백내장/핵백내장_1.jpg": {"title": "핵백내장"}}
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
    config.view_after_capture = True              # 이 파일은 v1.20.0 기본값으로 확인
    config.multi_side_visible = False
    config.save = lambda: None
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    sample_import.import_samples(db, config, _samples(tmp_path / "samples"))
    db.patients.insert(Patient(chart_no="D1", name="홍길동", diagnosis="백내장"))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.resize(1500, 900)
    w.show()
    assert _wait(qapp, lambda: w._last_frame is not None)
    # 샘플 환자가 아니라 실제 환자를 고른다
    i = next(k for k in range(w.patient_panel.combo.count()) if "D1" in w.patient_panel.combo.itemText(k))
    w.patient_panel.combo.setCurrentIndex(i)
    qapp.processEvents()
    yield w
    w._close_capture_viewer()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _capture(win, qapp):
    """사진 한 장 찍고 자동으로 뜨는 보기 창은 닫는다."""
    win._on_capture()
    qapp.processEvents()
    win._close_capture_viewer()
    qapp.processEvents()


def test_icons_for_bundled_diseases(qapp):
    """설치판 샘플 질환 이름마다 눈 모양 아이콘이 있고 모두 그려진다."""
    for name in sample_import.KNOWN_GROUPS.values():
        ic = icon_for(name)
        assert ic.startswith("dx-"), name
        assert not icons.pixmap(ic, "#ffffff", 18).isNull(), ic
    assert icon_for("후낭혼탁") == "dx-pco" and icon_for("백내장") == "dx-cataract"
    assert icon_for("아무거나") == "image"


def test_new_defaults():
    from app.config import AppConfig
    c = AppConfig()
    assert c.view_after_capture is True and c.multi_side_visible is False


def test_dx_bar_lists_sample_and_library_categories(qapp):
    bar = DxBar()
    assert bar.isHidden() and not bar.entries()
    bar.set_categories([("건성안", 2), ("백내장", 1)], ["각막"])
    assert [e[:2] for e in bar.entries()] == [("sample", "건성안"), ("sample", "백내장"), ("library", "각막")]
    hits = []
    bar.diseaseClicked.connect(lambda s, c: hits.append((s, c)))
    bar.buttons[("sample", "백내장")].click()
    assert hits == [("sample", "백내장")]
    bar.set_active(("sample", "백내장"))
    assert bar.active() == ("sample", "백내장") and bar.buttons[("sample", "백내장")].isChecked()
    bar.set_compact(True)
    assert bar.menu_btn.isVisibleTo(bar) and not bar.buttons[("sample", "백내장")].isVisibleTo(bar)
    assert [a.isChecked() for a in bar.menu.actions()] == [False, True, False]
    bar.set_compact(False)
    assert bar.buttons[("sample", "백내장")].isVisibleTo(bar)


def test_dx_click_shows_refs_and_multi_with_patient_photo(qapp, win):
    assert win.dx_bar.isVisible() and [e[1] for e in win.dx_bar.entries()] == ["건성안", "백내장"]
    for (source, cat), b in win.dx_bar.buttons.items():            # 아이콘 아래에 질환 이름
        assert b.text() == short_label(cat, source) and b.text()
    b = win.dx_bar.buttons[("sample", "건성안")]
    assert win.dx_bar.show_preview(b)                              # 마우스를 올리면 그 질환 사진이 아래에 작게
    pv = win.dx_bar.preview
    assert pv.isVisible() and "건성안" in pv.title.text()
    assert 1 <= len(pv.photos) <= PREVIEW_MAX and pv.photos[0].pixmap() is not None
    assert pv.pos().y() >= b.mapToGlobal(b.rect().bottomLeft()).y()   # 아이콘 아래
    win.dx_bar._hover_leave()
    assert _wait(qapp, lambda: not pv.isVisible(), 2)               # 미리보기 창으로 오지 않으면 잠시 뒤 닫힘
    _capture(win, qapp)                                         # 환자 사진 1장
    assert not win.ref_bar.isVisible() and not win._multi
    win.dx_bar.buttons[("sample", "건성안")].click()
    qapp.processEvents()
    assert not win.ref_bar.isVisible() and win.ref_bar.category_key() == ("sample", "건성안")   # 참조 줄은 숨긴 채 (v1.20.3)
    assert [r.title for r in win.ref_bar.selected_refs()] == ["건성안 1 (저해상도 120×90)", "건성안 2 (저해상도 120×90)"]
    assert len(win.thumbnail_panel.selected_captures()) == 1   # 최근 환자 사진을 자동으로 골라 같이
    assert win._multi and win.multi_view.count() == 3 and win.view_stack.currentIndex() == 2
    assert win.dx_bar.active() == ("sample", "건성안")
    assert not win.multi_pro.side_visible()                      # 보정 · 편집 · 분석 메뉴는 기본 접힘
    # 다른 질환으로 바꾸면 그 질환 사진으로 교체
    win.dx_bar.buttons[("sample", "백내장")].click()
    qapp.processEvents()
    assert [r.category for r in win.ref_bar.selected_refs()] == ["백내장"] and win.multi_view.count() == 2
    # 같은 아이콘을 다시 누르면 그 질환 사진을 빼고 환자 사진만 남는다
    win.dx_bar.buttons[("sample", "백내장")].click()
    qapp.processEvents()
    assert win.dx_bar.active() is None and win.ref_bar.selected_refs() == []
    assert win._multi and win.multi_view.count() == 1
    # 참조 선택을 다 비워도(초기화 등) 아이콘이 꺼진다
    win.dx_bar.buttons[("sample", "건성안")].click()
    qapp.processEvents()
    assert win.dx_bar.active() is not None
    win.ref_bar.clear_selection()
    assert win.dx_bar.active() is None


def test_dx_click_without_patient_photo(qapp, win):
    """환자 사진이 없어도 참조 사진만으로 멀티 화면이 열린다."""
    win.dx_bar.buttons[("sample", "백내장")].click()
    qapp.processEvents()
    assert win._multi and win.multi_view.count() == 1 and win.thumbnail_panel.selected_captures() == []


def test_multi_side_menu_toggle_persists(qapp, win, app_env):
    config, _db = app_env
    win.dx_bar.buttons[("sample", "건성안")].click()
    qapp.processEvents()
    pro = win.multi_pro
    assert not pro.side_visible() and pro.side_toggle.isVisible() and not pro.side_toggle.isChecked()
    QTest.mouseClick(pro.side_toggle, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert pro.side_visible() and config.multi_side_visible is True
    QTest.mouseClick(pro.side_toggle, Qt.MouseButton.LeftButton)
    assert not pro.side_visible() and config.multi_side_visible is False


def test_multi_side_menu_opens_when_saved_visible(qapp, app_env, monkeypatch):
    config, db = app_env
    config.multi_side_visible = True
    config.save = lambda: None
    from app.ui.pro.pro_workspace import ProWorkspace
    from app.services.capture_service import CaptureService
    pro = ProWorkspace(db, CaptureService(db, config), config, get_eye=lambda: "NA", embedded=True)
    pro.show()
    qapp.processEvents()
    assert pro.side_visible() and pro.side_toggle.isChecked()
    normal = ProWorkspace(db, CaptureService(db, config), config, get_eye=None)
    assert normal.side_toggle is None and normal.side_visible()       # Pro 검토 화면은 그대로


def test_capture_opens_big_viewer_with_retake(qapp, win, app_env):
    config, db = app_env
    win._on_capture()
    qapp.processEvents()
    v = win._capture_viewer
    assert v is not None and v.isVisible() and v.after_capture and v.b_retake is not None
    assert v.windowTitle().startswith("촬영한 사진") and v.isMaximized() or v.isVisible()
    # USB 버튼이 오면 보기 창을 닫고 바로 다음 동작
    v.activateWindow()
    assert win._usb_context() is None and win._capture_viewer is None
    qapp.processEvents()
    # '다시 촬영' 으로 닫으면 촬영 화면 그대로
    win._on_capture()
    qapp.processEvents()
    v = win._capture_viewer
    QTest.mouseClick(v.b_retake, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert win._capture_viewer is None and win._mode == "capture"
    # 보기 창에서 촬영 키(C)를 누르면 닫고 초기화 — 사진은 찍지 않는다 (v1.20.3 기본)
    p = win._current_patient
    n = len(db.captures.list_by_patient(p.id))
    win._on_capture()
    qapp.processEvents()
    v = win._capture_viewer
    assert config.capture_in_view == "reset"
    QTest.keyClick(v, Qt.Key.Key_C)
    for _ in range(10):
        qapp.processEvents()
    assert len(db.captures.list_by_patient(p.id)) == n + 1
    assert win._capture_viewer is None and win._mode == "capture" and not win._multi
    # 설정 '바로 촬영' 이면 예전처럼 닫고 바로 다시 찍는다
    config.capture_in_view = "capture"
    win._on_capture()
    qapp.processEvents()
    v = win._capture_viewer
    QTest.keyClick(v, Qt.Key.Key_C)
    assert _wait(qapp, lambda: len(db.captures.list_by_patient(p.id)) == n + 3)
    assert win._capture_viewer is not None and win._capture_viewer is not v
    win._close_capture_viewer()
    config.capture_in_view = "reset"
    # 설정에서 끄면 보기 창이 뜨지 않는다
    config.view_after_capture = False
    win._on_capture()
    qapp.processEvents()
    assert win._capture_viewer is None


def test_viewer_deleted_from_after_capture(qapp, win, app_env):
    _config, db = app_env
    win._on_capture()
    qapp.processEvents()
    v = win._capture_viewer
    p = win._current_patient
    assert len(db.captures.list_by_patient(p.id)) == 1
    v.done(ImageViewerDialog.Deleted)
    qapp.processEvents()
    assert db.captures.list_by_patient(p.id) == [] and win._capture_viewer is None


def test_settings_has_view_after_capture(qapp, app_env):
    config, _db = app_env
    config.view_after_capture = True
    from app.ui.settings_dialog import SettingsDialog
    dlg = SettingsDialog(config)
    assert dlg.view_after.isChecked()
    dlg.view_after.setChecked(False)
    config.save = lambda: None
    dlg.apply_to_config()
    assert config.view_after_capture is False


def test_dx_short_labels_and_hover_preview(qapp, tmp_path):
    assert short_label("정상대조") == "정상대조" and short_label("안검염_MGD") == "안검염"
    assert short_label("백내장수술후_후낭혼탁") == "후낭혼탁" and short_label("결막하출혈") == "결막출혈"
    assert short_label("각막찰과상·상피결손·각막이물") == "찰과상" and short_label("감염각막염") == "각막염"
    assert short_label("내가 만든 아주 긴 분류", "library") == "내가 …"
    assert all(len(short_label(n)) <= 4 for n in ("정상 대조", "다래끼_콩다래끼", "익상편_검열반", "결막염"))
    img = tmp_path / "a.png"
    cv2.imwrite(str(img), np.full((60, 80, 3), 120, np.uint8))
    bar = DxBar()
    bar.set_categories([("건성안", 6)], [])
    asked = []
    bar.preview_provider = lambda s, c: asked.append((s, c)) or [str(img)] * 6 + [str(tmp_path / "없음.png")]
    bar.show()
    b = bar.buttons[("sample", "건성안")]
    assert b.text() == "건성안" and b.toolTip() == ""                    # 툴팁 대신 미리보기
    QApplication.sendEvent(b, QEvent(QEvent.Type.Enter))
    assert _wait(qapp, lambda: bar.preview.isVisible(), 2)
    assert asked == [("sample", "건성안")] and len(bar.preview.photos) == min(6, PREVIEW_MAX)
    QApplication.sendEvent(b, QEvent(QEvent.Type.Leave))
    assert bar.preview.isVisible()                                        # 미리보기 창으로 옮겨 갈 동안은 띄워 둠
    assert _wait(qapp, lambda: not bar.preview.isVisible(), 2)            # 그 창으로 오지 않으면 잠시 뒤 닫힘
    bar.preview_provider = lambda s, c: []                                # 사진이 없으면 '사진 없음'
    assert bar.show_preview(b) and bar.preview.photos[0].text() == "사진 없음"
    bar.close()


def test_after_capture_viewer_has_dx_bar_and_compares(qapp, win):
    """촬영 직후 큰 화면에도 상단과 같은 질환 아이콘 — 누르면 창을 닫고 방금 찍은 사진 + 그 질환 샘플을 멀티 화면에."""
    win._on_capture()
    qapp.processEvents()
    v = win._capture_viewer
    assert v is not None and v.dx_bar is not None
    assert [e[1] for e in v.dx_bar.entries()] == ["건성안", "백내장"]
    b = v.dx_bar.buttons[("sample", "백내장")]
    assert b.text() == "백내장" and v.dx_bar.show_preview(b) and v.dx_bar.preview.photos
    cap = win.db.captures.list_by_patient(win._current_patient.id)[0]
    v.dx_bar.buttons[("sample", "백내장")].click()
    assert _wait(qapp, lambda: win._multi, 3)
    assert win._capture_viewer is None
    assert [c.id for c in win.thumbnail_panel.selected_captures()] == [cap.id]
    assert win.dx_bar.active() == ("sample", "백내장") and win.ref_bar.selected_refs()



def test_dx_preview_stays_open_and_photos_are_clickable(qapp, tmp_path):
    """미리보기 창으로 마우스를 옮겨도 닫히지 않고, 사진을 누르면 photoClicked(질환, 원래 순번) · ✓ 표시."""
    img = tmp_path / "a.png"
    cv2.imwrite(str(img), np.full((60, 80, 3), 120, np.uint8))
    bar = DxBar()
    bar.set_categories([("건성안", 3)], [])
    chosen = set()
    bar.preview_provider = lambda s, c: [str(tmp_path / "없음.png"), str(img), str(img)]   # 첫 장은 못 읽음
    bar.chosen_provider = lambda s, c: sorted(chosen)
    hits = []

    def on_photo(s, c, i):
        hits.append((s, c, i))
        chosen.symmetric_difference_update({i})
    bar.photoClicked.connect(on_photo)
    bar.show()
    b = bar.buttons[("sample", "건성안")]
    QApplication.sendEvent(b, QEvent(QEvent.Type.Enter))
    pv = bar.preview
    assert _wait(qapp, lambda: pv.isVisible(), 2)
    assert [lb.index for lb in pv.photos] == [1, 2] and pv.chosen() == []   # 순번은 원래 목록 기준
    assert pv.hint.isVisible() and "누르면" in pv.hint.text()
    # 아이콘 → 미리보기 창으로 옮김: 닫히지 않음
    QApplication.sendEvent(b, QEvent(QEvent.Type.Leave))
    QApplication.sendEvent(pv, QEvent(QEvent.Type.Enter))
    time.sleep(0.5)
    qapp.processEvents()
    assert pv.isVisible()
    QTest.mouseClick(pv.photos[1], Qt.MouseButton.LeftButton)            # 두 번째(원래 순번 2) 사진 고르기
    assert hits == [("sample", "건성안", 2)] and pv.chosen() == [2] and pv.photos[1].property("chosen") == "true"
    assert pv.isVisible() and pv.photos[1].grab() is not None             # ✓ 그리기 (오류 없이)
    QTest.mouseClick(pv.photos[1], Qt.MouseButton.LeftButton)            # 다시 누르면 빼기
    assert hits[-1] == ("sample", "건성안", 2) and pv.chosen() == []
    # 미리보기 창에서도 나가면 잠시 뒤 닫힘 · 아이콘을 누르면 바로 닫힘
    QApplication.sendEvent(pv, QEvent(QEvent.Type.Leave))
    assert _wait(qapp, lambda: not pv.isVisible(), 2)
    assert bar.show_preview(b) and pv.isVisible()
    QTest.mouseClick(b, Qt.MouseButton.LeftButton)
    assert not pv.isVisible()
    bar.close()


def test_dx_preview_photo_toggles_multi(qapp, win):
    """상단 질환 아이콘 미리보기에서 사진을 골라 멀티 화면에 — 한 장씩 넣고 빼기, 다른 질환을 고르면 바꿔 넣기."""
    _capture(win, qapp)                                                    # 환자 사진 1장
    bar = win.dx_bar
    b = bar.buttons[("sample", "건성안")]
    assert bar.show_preview(b)
    pv = bar.preview
    assert len(pv.photos) == 2 and pv.chosen() == []
    QTest.mouseClick(pv.photos[1], Qt.MouseButton.LeftButton)             # 건성안 두 번째 사진만
    qapp.processEvents()
    assert [r.title for r in win.ref_bar.selected_refs()] == ["건성안 2 (저해상도 120×90)"]
    assert len(win.thumbnail_panel.selected_captures()) == 1               # 최근 환자 사진을 자동으로 같이
    assert win._multi and win.multi_view.count() == 2 and bar.active() == ("sample", "건성안")
    assert pv.isVisible() and pv.chosen() == [1]                           # 미리보기는 열린 채 ✓
    QTest.mouseClick(pv.photos[0], Qt.MouseButton.LeftButton)             # 첫 번째도 넣기
    qapp.processEvents()
    assert win.multi_view.count() == 3 and pv.chosen() == [0, 1]
    assert win._dx_preview_chosen("sample", "건성안") == [0, 1]
    QTest.mouseClick(pv.photos[1], Qt.MouseButton.LeftButton)             # 두 번째 빼기
    qapp.processEvents()
    assert [r.title for r in win.ref_bar.selected_refs()] == ["건성안 1 (저해상도 120×90)"]
    assert win.multi_view.count() == 2 and pv.chosen() == [0]
    # 다른 질환 사진을 고르면 앞 질환 사진은 빠지고 그 질환으로
    assert bar.show_preview(bar.buttons[("sample", "백내장")])
    QTest.mouseClick(pv.photos[0], Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert [r.title for r in win.ref_bar.selected_refs()] == ["핵백내장 (저해상도 120×90)"]
    assert bar.active() == ("sample", "백내장") and win.multi_view.count() == 2
    QTest.mouseClick(pv.photos[0], Qt.MouseButton.LeftButton)             # 마지막 참조 사진을 빼면 아이콘도 꺼짐
    qapp.processEvents()
    assert win.ref_bar.selected_refs() == [] and bar.active() is None
    bar.hide_preview()


def test_viewer_dx_preview_photo_compares_with_new_capture(qapp, win):
    """촬영 직후 큰 화면의 질환 아이콘 미리보기에서 사진을 누르면 창을 닫고 방금 찍은 사진 + 그 사진만 멀티 화면에."""
    win._on_capture()
    qapp.processEvents()
    v = win._capture_viewer
    b = v.dx_bar.buttons[("sample", "건성안")]
    assert v.dx_bar.show_preview(b) and "방금 찍은" in v.dx_bar.preview.hint.text()
    QTest.mouseClick(v.dx_bar.preview.photos[1], Qt.MouseButton.LeftButton)
    assert _wait(qapp, lambda: win._multi, 3)
    cap = win.db.captures.list_by_patient(win._current_patient.id)[0]
    assert [c.id for c in win.thumbnail_panel.selected_captures()] == [cap.id]
    assert [r.title for r in win.ref_bar.selected_refs()] == ["건성안 2 (저해상도 120×90)"]
    assert win.multi_view.count() == 2 and win._capture_viewer is None

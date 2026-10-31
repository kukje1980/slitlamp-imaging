"""멀티 화면 · 촬영 직후 큰 화면의 삭제 · 전송 · 편집/분석 아이콘과 마우스 오른쪽 메뉴."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMessageBox

from app.models.patient import Patient
from app.services import sample_import
from app.ui.multi_view import is_reference


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
    files = {"01_건성안/건성안_1.jpg": {"title": "건성안 1"}, "01_건성안/건성안_2.jpg": {"title": "건성안 2"}}
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
    config.save = lambda: None
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    sample_import.import_samples(db, config, _samples(tmp_path / "samples"))
    db.patients.insert(Patient(chart_no="M1", name="멀티"))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.resize(1500, 900)
    w.show()
    assert _wait(qapp, lambda: w._last_frame is not None)
    i = next(k for k in range(w.patient_panel.combo.count()) if "M1" in w.patient_panel.combo.itemText(k))
    w.patient_panel.combo.setCurrentIndex(i)
    qapp.processEvents()
    yield w
    w._close_capture_viewer()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _photos(win, qapp, n):
    pid = win._current_patient.id
    for _ in range(n):
        win._on_capture()
        qapp.processEvents()
        win._close_capture_viewer()
    assert _wait(qapp, lambda: len(win.db.captures.list_by_patient(pid)) >= n)
    win.thumbnail_panel.set_patient(pid)
    return win.db.captures.list_by_patient(pid, order="asc")


def _pick(label):
    """메뉴에서 label 로 시작하는 항목을 고른 것처럼."""
    def run(menu, _pos):
        names = [a.text() for a in menu.actions()]
        run.seen.append(names)
        return next((a for a in menu.actions() if a.text().startswith(label)), None)
    run.seen = []
    return run


def _right_click(view, i):
    cv = view.canvases()[i]
    cv.customContextMenuRequested.emit(QPoint(10, 10))


# ---------------- 멀티 화면 ----------------
def test_multi_cell_bar_send_delete_edit(qapp, win, monkeypatch):
    caps = _photos(win, qapp, 2)
    for c in caps:
        win.thumbnail_panel.toggle_selection(c)
    win._dx_toggle_photo("sample", "건성안", 0)                       # 참조 사진 1장도 같이
    qapp.processEvents()
    mp, view = win.multi_pro, win.multi_view
    assert win._multi and view.count() == 3 and mp.cell_bar.isVisible()
    assert [mp.b_cell_send.text().strip(), mp.b_cell_delete.text().strip(), mp.b_cell_edit.text().strip()] == \
        ["전송", "삭제", "편집 · 분석"]
    # 1번 칸(환자 사진) — 전송
    view.set_active(view.canvases()[0])
    assert mp.b_cell_send.isEnabled() and mp.b_cell_delete.isEnabled() and "1번 칸" in mp.lbl_cell.text()
    sent = []
    monkeypatch.setattr(win, "_send_captures", lambda cs, parent=None: sent.append([c.id for c in cs]))
    mp.b_cell_send.click()
    assert sent == [[caps[0].id]]
    # 편집 · 분석 = 오른쪽 메뉴 펴기 / 접기 (손잡이와 같은 상태)
    mp.set_side_visible(False, save=False)
    mp.b_cell_edit.click()
    assert mp.side_visible() and mp.side_toggle.isChecked() and mp.b_cell_edit.isChecked()
    mp.b_cell_edit.click()
    assert not mp.side_visible() and not mp.b_cell_edit.isChecked()
    # 참조 사진 칸은 전송 · 삭제 없음
    view.set_active(view.canvases()[2])
    assert is_reference(view.active_capture())
    assert not mp.b_cell_send.isEnabled() and not mp.b_cell_delete.isEnabled() and "참조 사진" in mp.lbl_cell.text()
    # 2번 칸 — 삭제 (확인 후): DB · 파일에서 지우고 멀티 칸에서도 뺀다
    view.set_active(view.canvases()[1])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    mp.b_cell_delete.click()
    qapp.processEvents()
    assert win.db.captures.get(caps[1].id) is None and not Path(caps[1].file_path).exists()
    assert view.count() == 2 and caps[1].id not in [c.id for c in view.captures()]
    assert [c.id for c in win.thumbnail_panel.selected_captures()] == [caps[0].id]


def test_multi_right_click_menu(qapp, win, monkeypatch):
    caps = _photos(win, qapp, 2)
    for c in caps:
        win.thumbnail_panel.toggle_selection(c)
    win._dx_toggle_photo("sample", "건성안", 1)
    qapp.processEvents()
    mp, view = win.multi_pro, win.multi_view
    assert view.count() == 3
    # 2번 칸에서 오른쪽 클릭 → 그 칸이 편집 대상이 되고 메뉴: 전송 · 삭제 · 편집/분석 · 빼기
    sent = []
    monkeypatch.setattr(win, "_send_captures", lambda cs, parent=None: sent.append([c.id for c in cs]))
    pick = _pick("전송")
    monkeypatch.setattr(mp, "_exec_menu", pick)
    _right_click(view, 1)
    assert view.active_capture().id == caps[1].id and sent == [[caps[1].id]]
    assert pick.seen[0] == ["전송", "삭제 (이 PC 에서)", mp.side_visible() and "편집 · 분석 메뉴 접기"
                            or "편집 · 분석 메뉴 열기", "", "멀티 화면에서 빼기"]
    # 편집 · 분석 메뉴 열기
    mp.set_side_visible(False, save=False)
    monkeypatch.setattr(mp, "_exec_menu", _pick("편집 · 분석"))
    _right_click(view, 0)
    assert mp.side_visible()
    # 참조 칸: 전송 · 삭제는 꺼져 있고 '빼기' 는 참조 사진만 뺀다 (지우지 않음)
    menu_acts = {}

    def capture_menu(menu, _pos):
        menu_acts.update({a.text(): a.isEnabled() for a in menu.actions() if a.text()})
        return next(a for a in menu.actions() if a.text() == "멀티 화면에서 빼기")
    monkeypatch.setattr(mp, "_exec_menu", capture_menu)
    _right_click(view, 2)
    assert menu_acts["전송"] is False and menu_acts["삭제 (이 PC 에서)"] is False
    assert win.ref_bar.selected_refs() == [] and view.count() == 2 and win.dx_bar.active() is None
    assert len(sample_import.sample_references(win.db, category="건성안")) == 2
    # 환자 사진 칸 '빼기' — 사진은 그대로, 선택만 풀림
    monkeypatch.setattr(mp, "_exec_menu", _pick("멀티 화면에서 빼기"))
    _right_click(view, 0)
    assert [c.id for c in win.thumbnail_panel.selected_captures()] == [caps[1].id]
    assert win.db.captures.get(caps[0].id) is not None and view.count() == 1
    # 삭제 — '아니오' 면 그대로
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(mp, "_exec_menu", _pick("삭제"))
    _right_click(view, 0)
    assert win.db.captures.get(caps[1].id) is not None and view.count() == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    _right_click(view, 0)                                              # 마지막 칸을 지우면 라이브로
    qapp.processEvents()
    assert win.db.captures.get(caps[1].id) is None and not win._multi


# ---------------- 촬영 직후 큰 화면 ----------------
def test_viewer_edit_button_opens_multi_with_side_menu(qapp, win):
    win.config.view_after_capture = True
    win.multi_pro.set_side_visible(False, save=False)
    win._on_capture()
    qapp.processEvents()
    v = win._capture_viewer
    assert v is not None and v.b_edit is not None and v.b_edit.text().strip() == "편집 · 분석"
    assert v.b_send.isVisible() and v.b_delete.isVisible() and v.b_edit.isVisible()
    cap = v.capture
    v.b_edit.click()
    assert _wait(qapp, lambda: win._multi, 3)
    assert win._capture_viewer is None and [c.id for c in win.multi_view.captures()] == [cap.id]
    assert win.multi_pro.side_visible()


def test_viewer_right_click_menu(qapp, win, monkeypatch):
    win.config.view_after_capture = True
    win._on_capture()
    qapp.processEvents()
    v = win._capture_viewer
    cap = v.capture
    sent = []
    monkeypatch.setattr(win, "_send_captures", lambda cs, parent=None: sent.append([c.id for c in cs]))
    pick = _pick("전송")
    monkeypatch.setattr(v, "_exec_menu", pick)
    v.canvas.customContextMenuRequested.emit(QPoint(20, 20))
    assert sent == [[cap.id]]
    assert pick.seen[0] == ["전송", "전송(삭제)", "편집 · 분석", "", "삭제"]
    # 삭제 — 확인 후 창이 닫히고 사진이 지워진다
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(v, "_exec_menu", _pick("삭제"))
    v.canvas.customContextMenuRequested.emit(QPoint(20, 20))
    assert _wait(qapp, lambda: win.db.captures.get(cap.id) is None, 3)
    assert win._capture_viewer is None


def test_plain_viewer_right_click_has_delete_only(qapp, app_env, tmp_path):
    from app.models.capture import Capture
    from app.ui.image_viewer import ImageViewerDialog
    img = tmp_path / "a.png"
    cv2.imwrite(str(img), np.full((60, 80, 3), 90, np.uint8))
    cap = Capture(patient_id=1, media_type="photo", file_path=str(img), captured_at="2026-10-31 10:00:00")
    v = ImageViewerDialog(cap)
    pick = _pick("없음")
    v._exec_menu = pick
    assert v.context_menu(QPoint(0, 0)) is None and pick.seen == [["삭제"]]
    v2 = ImageViewerDialog(cap, allow_delete=False)
    assert v2.context_menu(QPoint(0, 0)) is None                       # 고를 것이 없으면 메뉴를 띄우지 않음

"""설정 창 다시 분류 (왼쪽 메뉴 · 설정 안내 · 항목 설명) · 프로그램을 끌 때 촬영 사진 지우기."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from app.config import AppConfig
from app.models.patient import Patient
from app.services import sample_import
from app.services.capture_service import CaptureService


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _wait(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


def _frame(v=90):
    return np.full((60, 80, 3), v, np.uint8)


def _sample_folder(root: Path) -> Path:
    import hashlib
    import json

    import cv2
    path = root / "01_건성안" / "건성안_1.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = cv2.imencode(".jpg", _frame(150))[1].tobytes()
    path.write_bytes(data)
    meta = [{"file": "01_건성안/건성안_1.jpg", "sha256": hashlib.sha256(data).hexdigest(), "title": "건성안"}]
    (root / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return root


# ---------------- 1. 설정 창: 왼쪽 메뉴 · 설정 안내 ----------------
def test_settings_pages_nav_and_guide(qapp, app_env):
    from app.ui.settings_dialog import PAGES, SettingsDialog
    config, _db = app_env
    dlg = SettingsDialog(config)
    names = dlg.page_names()
    assert names == [name for _g, pages in PAGES for name, _i, _t in pages]
    assert names[0] == "설정 안내" and dlg.tabs.currentIndex() == 0 and dlg.tabs.tabBar().isHidden()
    # 왼쪽 메뉴: 분류 제목은 고를 수 없고, 화면을 고르면 그 화면으로
    heads = [dlg.nav.item(r) for r in range(dlg.nav.count()) if dlg.nav.item(r).data(Qt.ItemDataRole.UserRole) is None]
    assert [h.text() for h in heads] == [g for g, _p in PAGES] and all(h.flags() == Qt.ItemFlag.NoItemFlags for h in heads)
    row = next(r for r in range(dlg.nav.count()) if dlg.nav.item(r).text() == "저장 · 삭제")
    dlg.nav.setCurrentRow(row)
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "저장 · 삭제"
    # show_tab 은 왼쪽 메뉴도 따라 바꾸고, 예전 탭 이름도 받는다
    dlg.show_tab("USB 버튼")
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "단축키 · USB 버튼"
    assert dlg.nav.currentItem().text() == "단축키 · USB 버튼"
    dlg.show_tab("일반")
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "화면 · 소리"
    # 설정 안내 '이럴 땐 여기' 를 누르면 그 화면으로
    b = dlg.faq_buttons["프로그램을 끌 때 찍은 사진을 이 PC 에서 지우고 싶어요"]
    b.click()
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "저장 · 삭제"
    # 항목이 제 화면에 있고, 항목마다 설명이 붙어 있다
    for widget, page in ((dlg.theme, "화면 · 소리"), (dlg.view_after, "촬영"), (dlg.right_click, "사진 목록 · 멀티"),
                         (dlg.camera_index, "카메라"), (dlg.btn_camera, "카메라"), (dlg.preset_list, "촬영 모드"),
                         (dlg.btn_shortcuts, "단축키 · USB 버튼"), (dlg.delete_on_exit, "저장 · 삭제"),
                         (dlg.export, "저장 · 삭제"), (dlg.send_method, "전송"), (dlg.backup_tab, "백업"),
                         (dlg.btn_samples, "참조 · 샘플"), (dlg.btn_references, "참조 · 샘플"),
                         (dlg.agent, "AI 에이전트"), (dlg.specs_label, "컴퓨터 사양")):
        assert dlg.page_of(widget) == page, (widget, page)
    descs = dlg.findChildren(QLabel, "SettingsDesc")
    assert len(descs) >= 30 and all(d.wordWrap() for d in descs)
    dlg.close()


def test_settings_human_labels_roundtrip(qapp, app_env):
    from app.ui.settings_dialog import SettingsDialog
    config, _db = app_env
    config.save = lambda: None
    config.source_mode, config.photo_format = "synthetic", "png"
    dlg = SettingsDialog(config)
    assert dlg.source_mode.currentData() == "synthetic" and "시험" in dlg.source_mode.currentText()
    assert dlg.row_test_file.isHidden()                                  # 파일 재생일 때만 보임
    dlg.source_mode.setCurrentIndex(dlg.source_mode.findData("file"))
    assert not dlg.row_test_file.isHidden()
    dlg.photo_format.setCurrentIndex(dlg.photo_format.findData("jpg"))
    dlg.apply_to_config()
    assert config.source_mode == "file" and config.photo_format == "jpg"
    config.photo_format = "jpeg"                                         # 예전 값도 그대로 둔다
    assert SettingsDialog(config).photo_format.currentData() == "jpeg"


def test_delete_on_exit_setting_roundtrip(qapp, app_env):
    from app.ui.settings_dialog import SettingsDialog
    config, _db = app_env
    assert AppConfig().delete_on_exit is False and AppConfig().delete_on_exit_scope == "all"
    dlg = SettingsDialog(config)
    assert not dlg.delete_on_exit.isChecked() and not dlg.delete_scope.isEnabled()
    dlg.delete_on_exit.setChecked(True)
    assert dlg.delete_scope.isEnabled()
    dlg.delete_scope.setCurrentIndex(dlg.delete_scope.findData("session"))
    dlg.apply_to_config()
    assert config.delete_on_exit and config.delete_on_exit_scope == "session"
    loaded = AppConfig.load()
    assert loaded.delete_on_exit and loaded.delete_on_exit_scope == "session"
    config.delete_on_exit_scope = "아무거나"
    config.save()
    assert AppConfig.load().delete_on_exit_scope == "all"              # 쓸 수 없는 값은 기본값
    # Explorer 판은 촬영 프로그램이 아니라 이 설정을 보이지 않고 건드리지 않는다
    config.delete_on_exit = True
    d = SettingsDialog(config, standard=False)
    d.show()
    d.show_tab("저장 · 삭제")
    qapp.processEvents()
    assert not d.delete_on_exit.isVisible() and "프로그램을 끌 때 찍은 사진을 이 PC 에서 지우고 싶어요" not in d.faq_buttons
    d.delete_on_exit.setChecked(False)
    d.apply_to_config()
    assert config.delete_on_exit is True
    d.close()


# ---------------- 2. 지울 대상 · 지우기 (서비스) ----------------
def test_exit_cleanup_targets_scopes_and_delete(qapp, app_env, tmp_path):
    config, db = app_env
    sample_import.import_samples(db, config, _sample_folder(tmp_path / "samples"))
    svc = CaptureService(db, config)
    p = Patient(chart_no="EX1", name="끌때")
    p.id = db.patients.insert(p)
    old = svc.save_photo(_frame(10), p, eye="OD")
    with db._lock, db._conn:                                             # 어제 찍은 사진으로
        db._conn.execute("UPDATE captures SET captured_at='2000-01-02 09:00:00' WHERE id=?", (old.id,))
    since = db.captures.max_id()
    new1 = svc.save_photo(_frame(20), p, eye="OD")
    new2 = svc.save_photo(_frame(30), p, eye="OS")
    sample_ids = {c.id for sp in sample_import.sample_patients(db) for c in db.captures.list_by_patient(sp.id)}
    assert sample_ids                                                    # 샘플 사진은 어느 범위에도 안 들어감
    ids = lambda scope: {c.id for c in svc.exit_cleanup_targets(scope, since_id=since)}  # noqa: E731
    assert ids("all") == {old.id, new1.id, new2.id}
    assert ids("today") == {new1.id, new2.id}
    assert ids("session") == {new1.id, new2.id}

    seen = []
    n = svc.delete_for_exit(svc.exit_cleanup_targets("all"), on_progress=lambda d, t: seen.append((d, t)))
    assert n == 3 and seen[-1] == (3, 3)
    assert db.captures.list_by_patient(p.id) == []
    for c in (old, new1, new2):
        assert not Path(c.file_path).exists() and not Path(c.thumbnail_path).exists()
        assert c.export_path and Path(c.export_path).exists()            # export 사본은 남김
    # 비게 된 환자 · 날짜 폴더(.thumbs 포함)는 치우고, 샘플 사진 폴더 · 저장 루트는 그대로
    media = Path(config.media_root)
    assert media.is_dir() and not any(Path(c.file_path).parent.exists() for c in (old, new1, new2))
    assert all(Path(c.file_path).exists() for sp in sample_import.sample_patients(db)
               for c in db.captures.list_by_patient(sp.id))
    assert db.patients.get(p.id) is not None                             # 환자 정보는 남김


def test_delete_for_exit_stop_keeps_rest(qapp, app_env):
    config, db = app_env
    svc = CaptureService(db, config)
    p = Patient(chart_no="EX2", name="멈춤")
    p.id = db.patients.insert(p)
    caps = [svc.save_photo(_frame(40 + i), p) for i in range(4)]
    n = svc.delete_for_exit(caps, on_progress=lambda d, t: d < 2)        # 2장 지운 뒤 '멈추기'
    assert n == 2 and len(db.captures.list_by_patient(p.id)) == 2
    assert Path(caps[3].file_path).exists() and Path(caps[3].file_path).parent.is_dir()


# ---------------- 3. 프로그램을 끌 때 안내 창 ----------------
@pytest.fixture()
def make_win(qapp, app_env, monkeypatch):
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 160, 120
    config.save = lambda: None
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    made = []

    def make():
        from app.db.database import Database
        from app.ui.main_window import MainWindow
        w = MainWindow(config, Database(config.db_path).init())     # 창은 닫을 때 자기 DB 를 닫는다
        w.resize(1300, 800)
        w.show()
        assert _wait(qapp, lambda: w._last_frame is not None)
        made.append(w)
        return w
    yield make
    for w in made:
        if not w._closing:
            w.config.delete_on_exit = False
            w.close()


def _patient(db, chart):
    p = Patient(chart_no=chart, name="환자" + chart)
    p.id = db.patients.insert(p)
    return p


def test_close_asks_and_deletes(qapp, app_env, make_win, monkeypatch):
    config, db = app_env
    p = _patient(db, "C1")
    before = CaptureService(db, config).save_photo(_frame(5), p)          # 켜기 전에 찍은 사진
    config.delete_on_exit, config.delete_on_exit_scope = True, "session"
    w = make_win()
    new = [w.service.save_photo(_frame(60 + i), p) for i in range(2)]
    asked = []
    monkeypatch.setattr(w, "_exit_delete_question", lambda text, info: asked.append((text, info)) or "delete")
    assert w.close()
    assert len(asked) == 1 and "사진 2장" in asked[0][0] and "이번에 프로그램을 켠 뒤" in asked[0][0]
    assert "되돌릴 수 없습니다" in asked[0][1] and "export" in asked[0][1]
    assert w._closing
    assert {c.id for c in _left(config, p.id)} == {before.id}                                           # 이번에 켠 뒤 찍은 것만 지움
    assert Path(before.file_path).exists() and not any(Path(c.file_path).exists() for c in new)


def _left(config, pid):
    """창이 닫으며 DB 를 닫았으므로 새로 열어 남은 영상을 본다."""
    from app.db.database import Database
    db = Database(config.db_path).init()
    try:
        return db.captures.list_by_patient(pid)
    finally:
        db.close()


def test_close_keep_and_cancel(qapp, app_env, make_win, monkeypatch):
    config, db = app_env
    p = _patient(db, "C2")
    config.delete_on_exit, config.delete_on_exit_scope = True, "all"
    w = make_win()
    cap = w.service.save_photo(_frame(70), p)
    answers = iter(["cancel", "keep"])
    asked = []
    monkeypatch.setattr(w, "_exit_delete_question", lambda text, info: asked.append(text) or next(answers))
    assert not w.close()                                                  # 취소: 끄지 않고 그대로
    qapp.processEvents()
    assert w.isVisible() and not w._closing and w.worker is not None
    assert "모든 환자의" in asked[0] and "사진 1장" in asked[0]
    assert w.close()                                                      # 남기고 종료: 지우지 않음
    assert len(asked) == 2 and Path(cap.file_path).exists()
    assert [c.id for c in _left(config, p.id)] == [cap.id]


def test_close_without_option_or_photos_does_not_ask(qapp, app_env, make_win, monkeypatch):
    config, db = app_env
    p = _patient(db, "C3")
    config.delete_on_exit = True
    config.delete_on_exit_scope = "session"
    w = make_win()
    asked = []
    monkeypatch.setattr(w, "_exit_delete_question", lambda *a: asked.append(a) or "delete")
    assert w.close() and asked == []                                      # 이번에 찍은 사진이 없으면 묻지 않음
    config.delete_on_exit = False
    w2 = make_win()
    w2.service.save_photo(_frame(80), p)
    monkeypatch.setattr(w2, "_exit_delete_question", lambda *a: asked.append(a) or "delete")
    assert w2.close() and asked == []                                     # 설정이 꺼져 있으면 묻지 않음
    assert len(_left(config, p.id)) == 1


def test_exit_question_buttons(qapp, app_env, make_win, monkeypatch):
    """실제 안내 창: '삭제하고 종료' · '남기고 종료'(기본 버튼) · '취소'(Esc)."""
    config, db = app_env
    w = make_win()
    shown = []

    def fake_exec(box):
        shown.append(box)
        texts = [b.text() for b in box.buttons()]
        assert {"삭제하고 종료", "남기고 종료", "취소 (끄지 않음)"} <= set(texts)
        assert box.defaultButton().text() == "남기고 종료" and box.escapeButton().text() == "취소 (끄지 않음)"
        target = next(b for b in box.buttons() if b.text() == want[0])
        target.click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    for label, result in (("삭제하고 종료", "delete"), ("남기고 종료", "keep"), ("취소 (끄지 않음)", "cancel")):
        want = [label]
        assert w._exit_delete_question("질문", "설명") == result
    assert len(shown) == 3 and shown[0].windowTitle().startswith("프로그램 종료")

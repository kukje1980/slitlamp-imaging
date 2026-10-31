"""상단 메뉴 · 촬영 도크 버튼이 실제 마우스 클릭을 받는지, 설정 창 바로 실행, Smart 줄 숨기기, 4분할 초기화."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from app.models.patient import Patient
from app.ui.icon_button import IconTextButton


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
def win(qapp, app_env, monkeypatch):
    config, db = app_env
    config.source_mode = "synthetic"
    config.save = lambda: None
    db.patients.insert(Patient(chart_no="B1", name="버튼"))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.control_bar.count_chooser = lambda anchor: 4      # 테스트: 칸 수 고르기 창 대신 4칸
    w.resize(1400, 820)
    w.show()
    assert _wait(qapp, lambda: w._last_frame is not None)
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


def test_icon_button_api_and_click(qapp):
    from app.ui import icons
    b = IconTextButton("녹화", icons.icon("rec", "#ff0000", 22), 22)
    b.setCheckable(True)
    hits = []
    b.clicked.connect(lambda *_: hits.append(1))
    b.resize(80, 56)
    b.show()
    qapp.processEvents()
    # 글자·아이콘 라벨 위를 눌러도 버튼이 받는다
    for lb in (b._ic, b._tx):
        QTest.mouseClick(b, Qt.MouseButton.LeftButton, pos=lb.geometry().center())
    assert len(hits) == 2 and b.isChecked() is False            # 두 번 눌러 켜졌다 꺼짐
    b.setText("녹화 중지")
    assert b.text() == "녹화 중지" and not b.icon().isNull()
    b.setEnabled(False)
    QTest.mouseClick(b, Qt.MouseButton.LeftButton)
    assert len(hits) == 2
    b.close()


def test_top_menu_clicks_switch_screens(qapp, win):
    assert not hasattr(win, "btn_mode_pro") and not hasattr(win, "act_pro")   # v1.20.3: Pro 검토 입구 없앰
    assert "pro" not in win.mode_buttons
    for name, mode in (("btn_mode_exam", "exam"), ("btn_mode_home", "home"),
                       ("btn_mode_patients", "patients"), ("btn_mode_capture", "capture")):
        QTest.mouseClick(getattr(win, name), Qt.MouseButton.LeftButton)
        qapp.processEvents()
        assert win.mode() == mode, name
        assert getattr(win, name).isChecked(), name                      # 지금 화면 버튼이 켜짐
    assert win.btn_mode_patients.text() == "환자 목록" and win.btn_mode_patients.isVisible()
    win.set_mode("patients")                                             # 홈 타일 · 단축키 L 로 가도 위 버튼이 켜짐
    assert win.btn_mode_patients.isChecked() and not win.btn_mode_home.isChecked()
    win.set_mode("capture")
    for gone in ("btn_camera", "btn_keys", "btn_samples", "btn_theme"):   # 설정 창 안으로 옮김
        assert not hasattr(win, gone)


def test_settings_holds_camera_shortcuts_samples_theme(qapp, win, monkeypatch):
    from app.ui import main_window as mw
    calls = []
    monkeypatch.setattr(win, "_restart_camera", lambda: calls.append("camera"))
    monkeypatch.setattr(win, "_open_shortcuts", lambda parent=None: calls.append(("shortcuts", type(parent).__name__)))
    monkeypatch.setattr(win, "_import_samples", lambda parent=None: calls.append(("samples", type(parent).__name__)))
    seen = {}

    def fake_exec(dlg):
        seen["dlg"] = dlg
        for b in (dlg.btn_camera, dlg.btn_shortcuts, dlg.btn_samples):
            assert b.isEnabled()
            QTest.mouseClick(b, Qt.MouseButton.LeftButton)
        dlg.theme.setCurrentIndex(dlg.theme.findData("light"))
        dlg.smart_visible.setChecked(False)
        dlg.start_home.setChecked(False)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(mw.SettingsDialog, "exec", fake_exec)
    QTest.mouseClick(win.btn_settings, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert calls[0] == "camera"
    assert ("shortcuts", "SettingsDialog") in calls and ("samples", "SettingsDialog") in calls
    assert "카메라를 다시 연결" in seen["dlg"].quick_status.text()
    cfg = win.config
    assert cfg.theme == "light" and cfg.smart_bar_visible is False and cfg.start_screen == "capture"
    assert not win.smart_bar.isVisibleTo(win) and not win.control_bar.btn_smart.isChecked()
    assert win.home.chk_skip.isChecked()


def test_smart_bar_hide_and_show(qapp, win):
    assert win.smart_bar.isVisibleTo(win) and win.control_bar.btn_smart.isChecked()
    QTest.mouseClick(win.smart_bar.btn_hide, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert not win.smart_bar.isVisibleTo(win) and not win.control_bar.btn_smart.isChecked()
    assert win.config.smart_bar_visible is False
    QTest.mouseClick(win.control_bar.btn_smart, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert win.smart_bar.isVisibleTo(win) and win.config.smart_bar_visible is True
    win._set_fullscreen(True)                       # 전체 화면에서는 숨고, 나오면 설정대로
    assert not win.smart_bar.isVisibleTo(win)
    win._set_fullscreen(False)
    assert win.smart_bar.isVisibleTo(win)


def test_quad_full_then_start_over_and_reset_on_toggle(qapp, win):
    cb, qv = win.control_bar, win.quad_view
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)
    assert win._quad and cb.btn_clear_quad.isVisible()
    for _ in range(4):
        assert _wait(qapp, lambda: qv._active is not None and qv._frames[qv._active] is not None)
        QTest.mouseClick(cb.btn_freeze, Qt.MouseButton.LeftButton)
    assert qv.is_full() and cb.btn_freeze.text() == "처음부터"
    QTest.mouseClick(cb.btn_freeze, Qt.MouseButton.LeftButton)          # 다 찬 뒤 누르면 전체 초기화
    assert not qv.has_any() or not any(qv._frozen)
    assert cb.btn_freeze.text() == "정지영상" and qv._active == 0
    # 한 칸 채운 뒤 비우기
    assert _wait(qapp, lambda: qv._frames[0] is not None)
    QTest.mouseClick(cb.btn_freeze, Qt.MouseButton.LeftButton)
    assert qv._frozen[0]
    QTest.mouseClick(cb.btn_clear_quad, Qt.MouseButton.LeftButton)
    assert not any(qv._frozen)
    # 채운 채로 4분할을 껐다 켜면 초기화
    assert _wait(qapp, lambda: qv._frames[0] is not None)
    QTest.mouseClick(cb.btn_freeze, Qt.MouseButton.LeftButton)
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)
    assert not win._quad
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)
    assert win._quad and not any(qv._frozen) and qv._active == 0
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)


def _menu(qapp, panel, cap, what):
    """사진 오른쪽 클릭 → 메뉴(전송 · 삭제 · 멀티)에서 하나를 누른다."""
    QTest.mouseClick(panel._widgets[cap.id], Qt.MouseButton.RightButton)
    qapp.processEvents()
    assert panel.context_menu.isVisible()
    panel.menu_actions[what].trigger()
    for _ in range(5):
        qapp.processEvents()


def _photos(win, n, eyes=("OD", "OS")):
    import numpy as np
    from app.services.capture_service import CaptureService
    svc = CaptureService(win.db, win.config)
    p = win._current_patient
    caps = []
    for i in range(n):
        img = np.full((120, 160, 3), 40 + i * 30, np.uint8)
        caps.append(svc.save_photo(img, p, eye=eyes[i % len(eyes)]))
    win.thumbnail_panel.set_patient(p.id)
    qapp = QApplication.instance()
    for _ in range(5):
        qapp.processEvents()
    return caps


def test_thumbnail_click_selects_double_click_opens(qapp, win, monkeypatch):
    caps = _photos(win, 3)
    opened = []
    monkeypatch.setattr(win, "_on_thumbnail_clicked", lambda c: opened.append(c.id))
    win.thumbnail_panel.thumbnailClicked.disconnect()
    win.thumbnail_panel.thumbnailClicked.connect(win._on_thumbnail_clicked)
    w0 = win.thumbnail_panel._widgets[caps[0].id]
    w2 = win.thumbnail_panel._widgets[caps[2].id]
    QTest.mouseClick(w0, Qt.MouseButton.LeftButton)
    QTest.mouseClick(w2, Qt.MouseButton.LeftButton)
    assert [c.id for c in win.thumbnail_panel.selected_captures()] == [caps[0].id, caps[2].id]
    assert w0.rank() == 1 and w2.rank() == 2 and opened == []
    QTest.mouseClick(w0, Qt.MouseButton.LeftButton)                     # 다시 누르면 선택 해제
    assert w0.rank() == 0 and w2.rank() == 1
    QTest.qWait(QApplication.doubleClickInterval() + 400)               # 앞 클릭과 이어지지 않게
    QTest.mouseDClick(w0, Qt.MouseButton.LeftButton)                    # 두 번 클릭 = 크게 보기, 선택은 그대로
    assert opened == [caps[0].id] and w0.rank() == 0


def test_multi_view_select_save_and_reset(qapp, win):
    caps = _photos(win, 3)
    cb, panel = win.control_bar, win.thumbnail_panel
    win._multi_by_right_click()                                          # 고른 사진이 없으면 켜지지 않음
    assert not win._multi and not cb.btn_multi.isChecked()
    for c in caps:
        QTest.mouseClick(panel._widgets[c.id], Qt.MouseButton.LeftButton)
    _menu(qapp, panel, caps[-1], "multi")                               # 고른 사진 위 오른쪽 클릭 → 멀티
    assert win._multi and win.view_stack.currentIndex() == 2 and win.multi_view.count() == 3
    assert cb.btn_save_multi.isVisible() and cb.btn_freeze.isVisible() and not cb.btn_freeze.isEnabled()
    comp = win.multi_view.composite()
    assert comp is not None and comp.shape[1] > comp.shape[0]          # 3장 → 2×2 격자
    n0 = panel.count()
    QTest.mouseClick(cb.btn_save_multi, Qt.MouseButton.LeftButton)      # 멀티 화면 저장
    assert panel.count() == n0 + 1
    saved = win.db.captures.list_by_patient(win._current_patient.id)[0]
    assert saved.note == "멀티 3장"
    assert [c.id for c in panel.selected_captures()] == [c.id for c in caps]   # 저장 후에도 선택 유지
    QTest.mouseClick(cb.btn_close_multi, Qt.MouseButton.LeftButton)     # 촬영 도크 '초기화' 로 닫기
    assert not win._multi and win.view_stack.currentIndex() == 0
    assert panel.selected_captures() == [] and win.multi_view.count() == 0


def test_dock_buttons_do_not_move_when_labels_change(qapp, win):
    cb = win.control_bar
    pos = {n: getattr(cb, n).geometry().topLeft() for n in ("btn_freeze", "btn_capture", "btn_record")}
    widths = {n: getattr(cb, n).width() for n in pos}
    QTest.mouseClick(cb.btn_freeze, Qt.MouseButton.LeftButton)          # 정지영상 → 라이브
    qapp.processEvents()
    assert cb.btn_freeze.text() == "라이브"
    for n in pos:
        assert getattr(cb, n).geometry().topLeft() == pos[n] and getattr(cb, n).width() == widths[n], n
    QTest.mouseClick(cb.btn_freeze, Qt.MouseButton.LeftButton)
    assert not cb.status.isVisible()                                     # 녹화 오른쪽 안내 글자 없음


def test_ou_removed_from_choices(qapp, win):
    from app import shortcuts
    assert set(win.control_bar.eye_buttons) == {"OD", "OS", "NA"}
    assert "eyeOU" not in shortcuts.defaults()
    assert [win.pro.c_eye.itemText(i) for i in range(win.pro.c_eye.count())] == ["안구 미지정", "OD 우안", "OS 좌안"]
    win.control_bar.set_eye("OU")                                        # 예전 값이 와도 무시
    assert win.control_bar.current_eye() in ("NA", "OD", "OS")


def test_patient_search_in_top_bar_and_list_screen(qapp, win):
    from app.models.patient import Patient
    db = win.db
    pid2 = db.patients.insert(Patient(chart_no="B2", name="두번째", sex="F", birth_date="1980-03-15", diagnosis="건성안"))
    win.patient_panel._reload()
    assert win.patient_panel.parentWidget().objectName() == "TopBar"
    assert not win.patient_panel.summary.isVisibleTo(win)
    # 홈 → 환자 목록 타일
    win.set_mode("home")
    QTest.mouseClick(win.home.tiles["patients"], Qt.MouseButton.LeftButton)
    qapp.processEvents()
    pl = win.patient_list
    assert win.mode() == "patients" and win.mode_stack.currentWidget() is pl
    assert pl.table.rowCount() == 2 and pl.badge.text() == "2"
    pl.search.setText("두번")
    assert pl.table.rowCount() == 1 and pl.table.item(0, 4).text() == "건성안"
    pl.table.selectRow(0)
    QTest.mouseClick(pl.btn_go, Qt.MouseButton.LeftButton)              # 촬영 시작
    assert win.mode() == "capture" and win._current_patient.id == pid2
    # 단축키 L 도 환자 목록 화면
    win._run_action("patients")
    assert win.mode() == "patients" and pl.selected_id() == pid2


def test_real_double_click_sequence_keeps_selection(qapp, win):
    """실제 OS 순서(누름 → 뗌 → 두 번 클릭 → 뗌)에서도 선택이 바뀌지 않는다."""
    caps = _photos(win, 2)
    panel = win.thumbnail_panel
    opened = []
    panel.thumbnailClicked.disconnect()
    panel.thumbnailClicked.connect(lambda c: opened.append(c.id))
    w = panel._widgets[caps[0].id]
    QTest.mousePress(w, Qt.MouseButton.LeftButton)
    QTest.mouseRelease(w, Qt.MouseButton.LeftButton)
    QTest.mouseDClick(w, Qt.MouseButton.LeftButton)
    assert opened == [caps[0].id] and panel.selected_captures() == []


def test_split_counts_and_saved_choice(qapp, win):
    cb, qv = win.control_bar, win.quad_view
    picks = iter([6, None, 16])
    cb.count_chooser = lambda anchor: next(picks)
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)            # 6분할 고름
    assert win._quad and qv.count() == 6 and cb.btn_quad.text() == "6분할"
    for _ in range(6):
        assert _wait(qapp, lambda: qv._active is not None and qv._frames[qv._active] is not None)
        QTest.mouseClick(cb.btn_freeze, Qt.MouseButton.LeftButton)
    assert qv.is_full() and qv.composite().shape[1] == qv._frames[0].shape[1] * 3     # 3×2
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)            # 끄기
    assert not win._quad and cb.btn_quad.text() == "분할"
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)            # 고르기 취소 → 켜지지 않음
    assert not win._quad and not cb.btn_quad.isChecked()
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)            # 16분할
    assert qv.count() == 16 and cb.btn_quad.text() == "16분할"
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)
    win.config.split_count = 8                                           # 저장된 칸 수로 단축키 Q
    win._run_action("quad")
    assert win._quad and qv.count() == 8
    win._run_action("quad")
    assert not win._quad


def test_split_choice_saved_by_menu(qapp, win, monkeypatch):
    monkeypatch.setattr(win, "_exec_menu", lambda menu, pos: menu.actions()[3])     # '8분할' 고름
    assert win._choose_split_count(win.control_bar.btn_quad) == 8 and win.config.split_count == 8
    from app.ui.quad_view import split_grid
    assert [split_grid(n) for n in (2, 4, 6, 8, 16)] == [(2, 1), (2, 2), (3, 2), (4, 2), (4, 4)]


def test_send_to_folder_all_formats(qapp, win, tmp_path, monkeypatch):
    from app.ui import main_window as mw
    from app.ui.send_dialog import SendDialog
    caps = _photos(win, 2)
    win.config.send_method = "folder"
    win.config.send_folder = str(tmp_path / "out")
    win.config.send_formats = ["original", "jpg", "pdf", "dicom"]
    infos = []
    monkeypatch.setattr(SendDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: infos.append(a[2]))
    monkeypatch.setattr(mw.QMessageBox, "exec", lambda self: (infos.append(self.text()), 0)[1])
    QTest.mouseClick(win.control_bar.btn_send, Qt.MouseButton.LeftButton)   # 고른 사진 없음 → 안내
    assert infos and "고른 뒤" in infos[-1]
    for c in caps:
        QTest.mouseClick(win.thumbnail_panel._widgets[c.id], Qt.MouseButton.LeftButton)
    QTest.mouseClick(win.control_bar.btn_send, Qt.MouseButton.LeftButton)
    assert "보냈습니다" in infos[-1]
    files = sorted(p.suffix for p in (tmp_path / "out").rglob("*") if p.is_file())
    assert files.count(".dcm") == 2 and files.count(".jpg") == 2 and files.count(".png") == 2 and files.count(".pdf") == 1
    import pydicom
    dcm = next((tmp_path / "out").rglob("*.dcm"))
    ds = pydicom.dcmread(str(dcm))
    assert ds.Modality == "OP" and ds.PatientName == "버튼"


def test_send_service_pacs_with_local_scp(qapp, win, tmp_path):
    """PACS(DICOM C-STORE) 전송 — 이 PC 에 임시 PACS(Storage SCP)를 띄워 실제로 보내 본다."""
    from types import SimpleNamespace
    from pynetdicom import AE, evt
    from app.analysis import dicom_export
    from app.services import send_service as ss
    caps = _photos(win, 2)
    got = []

    def on_store(event):
        got.append(event.dataset.SOPInstanceUID)
        return 0x0000
    scp = AE(ae_title="TESTPACS")
    scp.add_supported_context(dicom_export.OP_8BIT, "1.2.840.10008.1.2.1")
    scp.add_supported_context("1.2.840.10008.1.1")
    server = scp.start_server(("127.0.0.1", 0), block=False, evt_handlers=[(evt.EVT_C_STORE, on_store)])
    try:
        port = server.server_address[1]
        cfg = SimpleNamespace(send_method="pacs", send_pacs_host="127.0.0.1", send_pacs_port=port,
                              send_pacs_ae="TESTPACS", send_pacs_calling_ae="SLITLAMP", clinic_name="")
        assert ss.test_pacs(cfg) == (True, "연결 성공")
        res = ss.send(caps, win._current_patient, cfg)
        assert res.ok and len(res.sent) == 2 and len(got) == 2
    finally:
        server.shutdown()
    bad = SimpleNamespace(send_method="pacs", send_pacs_host="127.0.0.1", send_pacs_port=1,
                          send_pacs_ae="X", send_pacs_calling_ae="Y", clinic_name="")
    assert ss.test_pacs(bad)[0] is False


def test_settings_send_tab_saves(qapp, app_env):
    from app.ui.settings_dialog import SettingsDialog
    config, _db = app_env
    config.save = lambda: None
    dlg = SettingsDialog(config)
    assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())][:3] == ["설정 안내", "화면 · 소리", "촬영"]
    assert dlg.page_of(dlg.send_method) == "전송"
    dlg.send_method.setCurrentIndex(dlg.send_method.findData("pacs"))
    assert not dlg.send_formats["jpg"].isEnabled() and dlg.send_formats["dicom"].isEnabled()
    dlg.pacs_host.setText("10.0.0.5")
    dlg.pacs_port.setValue(11112)
    dlg.send_formats["dicom"].setChecked(True)
    dlg.apply_to_config()
    assert config.send_method == "pacs" and config.send_pacs_host == "10.0.0.5" and config.send_pacs_port == 11112
    assert "dicom" in config.send_formats


def test_capture_presets_save_switch_and_live_props(qapp, win, monkeypatch):
    from app.capture import presets
    cfg = win.config
    assert win.control_bar.btn_preset.text() == "기본"                   # '기본 (자동)'
    # 지금 설정(피킹 켬 · 6분할 · 밝기 +60)을 '형광' 모드로 저장
    cfg.smart_peaking, cfg.split_count, cfg.camera_props = True, 6, {"brightness": 60}
    assert win._save_current_preset("형광")
    assert win.control_bar.btn_preset.text() == "형광" and cfg.active_preset == "형광"
    # 기본으로 바꾸면 밝기 자동 · 피킹 끔 · 4분할
    assert win.apply_preset(presets.DEFAULT_NAME)
    assert cfg.camera_props == {} and not cfg.smart_peaking and cfg.split_count == 4
    assert not win.smart_bar.btn_peaking.isChecked()
    # 촬영 모드는 설정 → 일반의 '촬영 모드' 에서 고른다 (촬영 도크의 '모드' 버튼은 없어짐)
    assert not win.control_bar.btn_preset.isVisible()
    names = [p["name"] for p in presets.all_presets(cfg)]
    from app.ui.settings_dialog import SettingsDialog

    def pick_in_settings(dlg):
        assert [dlg.preset_combo.itemText(i) for i in range(dlg.preset_combo.count())] == names
        dlg.preset_combo.setCurrentIndex(names.index("형광"))
        return True
    monkeypatch.setattr(SettingsDialog, "exec", pick_in_settings)
    win._open_settings()
    assert cfg.active_preset == "형광" and cfg.smart_peaking and cfg.split_count == 6
    assert win.smart_bar.btn_peaking.isChecked() and win.control_bar.split_count == 6
    # 설정을 닫으면 카메라를 다시 열고, 열 때 모드의 카메라 값이 적용된다
    assert _wait(qapp, lambda: getattr(win.worker, "_source", None) is not None
                 and win.worker._source.get_property("brightness") == 60, 8)
    # 해상도가 다른 모드는 카메라를 다시 연다
    w0 = cfg.capture_width
    nw, nh = (1280, 720) if w0 != 1280 else (1920, 1080)
    presets.save(cfg, dict(presets.find(cfg, "형광"), name="다른 해상도", capture_width=nw, capture_height=nh))
    old_worker = win.worker
    assert win.apply_preset("다른 해상도") and win.worker is not old_worker
    assert _wait(qapp, lambda: win._last_frame is not None and win._last_frame.shape[1] == nw, 8)


def test_settings_preset_tab_edit(qapp, app_env):
    from app.capture import presets
    from app.ui.settings_dialog import SettingsDialog
    config, _db = app_env
    config.save = lambda: None
    previews = []
    dlg = SettingsDialog(config, actions={"camera_props": previews.append})
    dlg.show_tab("촬영 모드")
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "촬영 모드" and dlg.preset_list.count() == 1
    dlg.btn_p_add.click()                                               # 새 모드
    dlg.p_name.setText("세극 조명")
    dlg._preset_edited(rename=True)
    chk, spin = dlg.p_props["exposure"]
    chk.setChecked(True)
    spin.setValue(-7)
    dlg.p_split.setCurrentIndex(dlg.p_split.findData(8))
    dlg.btn_p_preview.click()
    assert previews == [{"exposure": -7.0}] and dlg.previewed
    dlg.btn_p_use.click()
    dlg.apply_to_config()
    p = presets.find(config, "세극 조명")
    assert p and p["camera_props"] == {"exposure": -7.0} and p["split_count"] == 8
    assert config.active_preset == "세극 조명"
    assert len(presets.all_presets(config)) == 2


def test_center_buttons_stay_put_in_every_mode(qapp, win):
    cb = win.control_bar
    def centers():
        qapp.processEvents()
        return [b.mapTo(cb, b.rect().center()).x() for b in (cb.btn_freeze, cb.btn_capture, cb.btn_record)]
    base = centers()
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)             # 분할: 저장 · 비우기 생김
    assert centers() == base
    QTest.mouseClick(cb.btn_quad, Qt.MouseButton.LeftButton)
    caps = _photos(win, 2)
    for c in caps:
        QTest.mouseClick(win.thumbnail_panel._widgets[c.id], Qt.MouseButton.LeftButton)
    _menu(qapp, win.thumbnail_panel, caps[-1], "multi")                  # 멀티: 저장 생김
    assert centers() == base


# ---------------- 멀티: 버튼 위치 · 돋보기 기본 · Pro 검토 편집 ----------------
def _enter_multi(qapp, win, n=3):
    caps = _photos(win, n)
    panel = win.thumbnail_panel
    for c in caps:
        QTest.mouseClick(panel._widgets[c.id], Qt.MouseButton.LeftButton)
    _menu(qapp, panel, caps[-1], "multi")
    assert win._multi and win.multi_view.count() == n
    return caps


def test_thumbnail_header_has_no_multi_button_and_menu_has_send_delete_multi(qapp, win):
    """오른쪽 촬영 이미지 칸 위의 '멀티' 버튼은 없고, 사진 오른쪽 클릭 메뉴에 전송 · 삭제 · 멀티."""
    panel, cb = win.thumbnail_panel, win.control_bar
    qapp.processEvents()
    assert not hasattr(panel, "btn_multi")
    assert not cb.btn_multi.isVisible()                                  # 촬영 도크에서도 숨김
    texts = [a.text() for a in panel.context_menu.actions() if a.text() and a.isEnabled() and a.isVisible()]
    assert texts == ["전송", "멀티", "삭제"]                               # 기본 메뉴 (전송(삭제)는 설정에서 켬)
    _enter_multi(qapp, win, 2)
    assert cb.btn_multi.isChecked() and cb.btn_close_multi.isVisible()
    QTest.mouseClick(cb.btn_close_multi, Qt.MouseButton.LeftButton)     # '초기화' 로 닫기
    assert not win._multi


def test_multi_has_pro_side_menus_and_magnifier_default(qapp, win):
    _enter_multi(qapp, win, 3)
    mp, mv = win.multi_pro, win.multi_view
    assert win.view_stack.currentWidget() is mp and mp.mode() == "grid" and mp.embedded
    assert not mp.toolbar_scroll.isVisible()                             # Pro 위 도구줄은 없음
    assert [b.text() for b in mp.tab_buttons] == ["보정", "편집", "분석"]   # 필터는 편집에, 스마트는 없고, 분석은 하나
    assert all(b.isVisible() for b in mp.tab_buttons)
    assert [b.text() for b in win.pro.tab_buttons] == ["보정", "필터", "편집", "스마트", "분석", "정밀 분석"]  # Pro 검토는 그대로
    assert mv.tool() == "magnify" and all(cv.tool() == "magnify" for cv in mv.canvases())
    assert mp.tool_buttons["magnify"].isChecked()
    cv1 = mv.canvases()[1]
    vp = cv1.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vp.rect().center())
    assert cv1._lens is not None                                          # 누르는 동안 확대 렌즈
    assert mv.active_canvas() is cv1 and mp.active_canvas() is cv1        # 누른 칸이 편집 대상
    assert mv._cells[1].property("active") and not mv._cells[0].property("active")
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vp.rect().center())
    assert cv1._lens is None
    assert not mp.info_card.isVisible() and win.pro.info_card.isVisibleTo(win.pro)   # 멀티에는 '영상 정보' 없음 (Pro 는 그대로)


def test_multi_pro_edit_adjust_and_tools(qapp, win):
    caps = _enter_multi(qapp, win, 3)
    mp, mv = win.multi_pro, win.multi_view
    cv0, cv1 = mv.canvases()[:2]
    mv.set_active(cv1)
    h, w = cv1.image().shape[:2]
    mp.b_rot_r.click()                                                    # 편집 탭 회전은 누른 칸만
    assert cv1.image().shape[:2] == (w, h) and cv0.image().shape[:2] == (h, w)
    assert mp.b_undo.isEnabled() and mp.b_edit_save.isEnabled()
    mp.b_undo.click()
    assert cv1.image().shape[:2] == (h, w)
    mp.b_redo.click()
    assert cv1.geometry_changed()
    mp.b_orig.click()
    assert not cv1.is_modified()
    # 주석: 편집 탭 화살표 도구로 끌기 → 주석 지우기는 누른 칸만
    mp.tool_buttons["arrow"].click()
    assert all(cv.tool() == "arrow" for cv in mv.canvases())
    vp = cv1.viewport()
    r = vp.rect()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, r.center())
    QTest.mouseMove(vp, r.center() + QPoint_(30, 20))
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, r.center() + QPoint_(30, 20))
    assert cv1.annotation_kinds() == ["arrow"] and mp.b_clear.isEnabled()
    mp.b_clear.click()
    assert not cv1.has_annotations()
    mp.set_tool("crop")                                                   # 자르기 · 눈금도 멀티에서
    assert mv.tool() == "crop"
    mp.set_tool("magnify")
    # 보정 탭: 누른 칸만 → '모든 칸에 같이 적용'
    mp.s_bright.setValue(40)
    assert cv1.adjust().brightness == 40 and cv0.adjust().brightness == 0
    mv.set_active(cv0)                                                    # 칸을 바꾸면 슬라이더도 그 칸 값
    assert mp.s_bright.value() == 0
    mv.set_active(cv1)
    assert mp.s_bright.value() == 40
    mp.c_sharpen.setCurrentIndex(2)                                       # 필터 탭도 같은 칸에
    assert cv1.adjust().sharpen == 2 and cv0.adjust().sharpen == 0
    assert mp.chk_adj_all.isVisible() or mp.tabs.currentIndex() != 0
    mp.chk_adj_all.setChecked(True)
    mp.s_contrast.setValue(150)
    assert all(cv.adjust().contrast == 1.5 for cv in mv.canvases())
    mp.chk_adj_all.setChecked(False)
    # 고른 사진이 바뀌어도 남은 사진의 편집은 그대로
    QTest.mouseClick(win.thumbnail_panel._widgets[caps[2].id], Qt.MouseButton.LeftButton)   # 3번째 선택 해제
    assert mv.count() == 2 and mv.canvas_for(caps[1].id) is cv1 and cv1.adjust().brightness == 40
    comp = mv.composite()                                                 # 멀티 화면 저장(합성)에 보정이 들어간다
    assert comp is not None and comp.mean() > 0


def QPoint_(x, y):
    from PySide6.QtCore import QPoint
    return QPoint(x, y)


def test_multi_edit_tab_order_and_always_visible_edit_bar(qapp, win):
    """멀티 편집 탭: 보기 → 도구 → 필터 → 회전 · 반전. 실행 취소 · 다시 실행 · 원본 · 주석 지우기 · 편집본 저장은 어느 탭에서나."""
    _enter_multi(qapp, win, 2)
    mp = win.multi_pro
    page = mp.tabs.widget(1).widget()                                     # 편집 (스크롤 안)
    assert mp.tabs.tabText(1) == "편집" and page.isAncestorOf(mp.c_sharpen) and page.isAncestorOf(mp.b_rot_l)
    mp.tabs.setCurrentIndex(1)
    qapp.processEvents()
    y = lambda w: w.mapTo(page, w.rect().topLeft()).y()  # noqa: E731
    heads = sorted((lbl for lbl in page.findChildren(QLabel) if lbl.objectName() == "PanelHeader"), key=y)
    assert [h.text() for h in heads] == ["보기", "도구 (주석 · 측정)", "필터", "회전 · 반전"]
    assert y(mp.b_fit) < y(mp.tool_buttons["calibrate"]) < y(mp.c_sharpen) < y(mp.b_rot_l)
    assert mp.edit_bar.isVisible() and not page.isAncestorOf(mp.b_undo)  # 편집 이력은 메뉴 아래 늘 보이는 줄
    for i in range(mp.tabs.count()):
        mp.tabs.setCurrentIndex(i)
        qapp.processEvents()
        assert mp.b_edit_save.isVisible() and mp.b_undo.isVisible() and mp.b_clear.isVisible()
    assert win.pro.edit_bar is None                                       # Pro 검토는 편집 탭 안에 그대로


def test_multi_analysis_one_flow_on_active_cell(qapp, win):
    """멀티 분석 탭: 측정 종류 하나 고르고 '측정 실행' → 결과 → 기록 — 빠른 측정 · 정밀 분석 · 백내장이 한 흐름."""
    from app.analysis import clinical
    caps = _enter_multi(qapp, win, 2)
    mp, mv = win.multi_pro, win.multi_view
    an = mp.analysis
    assert an is mp.advanced_analysis and mp.tabs.tabText(2) == "분석"
    mp.tabs.setCurrentIndex(2)
    qapp.processEvents()
    kinds = [an.c_kind.itemData(i) for i in range(an.c_kind.count())]
    assert kinds[:5] == list(clinical.KINDS) and kinds[5:] == ["fluo_quick", "redness", "nuclear", "cortical"]
    mv.set_active(mv.canvases()[1])
    an.set_kind("redness")
    assert an.red.isVisibleTo(an) and not an.cat_box.isVisibleTo(an)      # 종류에 맞는 것만 보임
    assert an.b_run.isEnabled() and not an.b_note.isEnabled() and not an.b_save.isEnabled()
    an.b_run.click()                                                      # 누른 칸에서 측정
    assert mp._analysis_text.startswith("충혈 지수") and mv.canvases()[1].has_overlay()
    assert "충혈 지수" in an.lbl_result.text() and an.b_note.isEnabled() and not an.b_save.isEnabled()
    an.s_red_sens.setValue(2)                                             # 민감도를 바꾸면 바로 다시 측정
    assert "민감도 2" in mp._analysis_text and an.b_note.isEnabled()
    an.b_note.click()                                                     # 코멘트에 기록 → 그 칸 사진에
    note = win.db.captures.get(caps[1].id).note
    assert note and "충혈 지수" in note
    an.set_kind("quality")                                                # 정밀 종류도 같은 버튼으로
    assert not an.red.isVisibleTo(an) and not an.b_note.isEnabled()      # 다른 종류의 결과로는 기록 못 함
    an.b_run.click()
    assert an.result is not None and an.result.kind == "quality" and an._result_canvas is mv.canvases()[1]
    assert not mv.canvases()[1].has_overlay()                             # 빠른 측정 표시는 지워짐
    assert an.b_save.isEnabled() and an.b_note.isEnabled() and "영상 품질" in an.lbl_result.text()
    an.b_clear.click()
    assert an.result is None and an.lbl_result.text() == "-" and not an.b_clear.isEnabled()
    an.set_kind("nuclear")
    assert an.cat_box.isVisibleTo(an) and an.b_note.text() == "측정 기록"
    best = an.open_sharpest()                                             # 가장 선명한 칸을 편집 대상으로
    assert best in (0, 1) and mv.active_canvas() is mv.canvases()[best]


def test_multi_save_edited_and_double_click(qapp, win, monkeypatch):
    caps = _enter_multi(qapp, win, 2)
    mp, mv = win.multi_pro, win.multi_view
    panel = win.thumbnail_panel
    cv0 = mv.canvases()[0]
    mv.set_active(cv0)
    mp.b_flip_h.click()
    n0 = panel.count()
    mp.b_edit_save.click()                                                # 편집 탭 '편집본 저장'
    assert _wait(qapp, lambda: panel.count() == n0 + 1)
    newest = win.db.captures.list_by_patient(win._current_patient.id)[0]
    assert newest.note == "편집본" and newest.eye == caps[0].eye
    assert not cv0.is_modified() and not mp.b_edit_save.isEnabled()       # 같은 편집을 두 번 저장하지 않게
    assert win._multi and [c.id for c in panel.selected_captures()] == [c.id for c in caps]   # 고른 사진은 그대로
    opened = []
    mv.cellActivated.disconnect()                                         # 실제 큰 화면 창은 띄우지 않음
    mv.cellActivated.connect(lambda cap: opened.append(cap.id))
    vp = mv.canvases()[1].viewport()
    QTest.mouseDClick(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vp.rect().center())
    assert opened == [caps[1].id]                                         # 두 번 누르면 큰 화면으로


# ---------------- 참조 선택(숨긴 참조 줄): 고르면 멀티에 같이 — v1.20.3 부터 화면 아래 참조 줄 · 도크 '참조' 없음 ----------------
def _add_reference(win, tmp_path, title="참조 백내장"):
    import numpy as np
    from app.services.capture_service import CaptureService
    svc = CaptureService(win.db, win.config)
    cap = svc.save_photo(np.full((90, 120, 3), 200, np.uint8), win._current_patient, eye="OD")
    assert win._register_reference(cap, values=("수정체 · 백내장", title, ""))
    return win.db.references.list()[0]


def test_reference_bar_removed_from_screen(qapp, win):
    cb = win.control_bar
    assert not hasattr(win, "btn_refs")                                   # 상단바 '참조 영상' 은 없앰
    assert not cb.btn_refs.isVisible() and cb.btn_refs.parent() is cb     # 도크 '참조' 도 없앰 (속성 · 신호만 남김)
    assert not hasattr(win, "_set_ref_bar_visible")
    rb = win.ref_bar
    assert not rb.isVisible() and not rb.isWindow() and rb.parent() is not None   # 숨긴 선택 모델 (따로 창이 뜨지 않게)
    cb.refsToggled.emit(True)                                             # 예전 신호가 와도 보이지 않음
    rb.hideRequested.emit()
    win._set_fullscreen(True)
    win._set_fullscreen(False)
    qapp.processEvents()
    assert not rb.isVisible()


def test_reference_bar_stays_hidden_with_old_config(qapp, app_env):
    """예전 설정 파일에 ref_bar_visible = true 가 있어도 아래 참조 줄은 보이지 않는다."""
    from app.ui.main_window import MainWindow
    config, db = app_env
    config.source_mode = "synthetic"
    config.save = lambda: None
    config.ref_bar_visible = True
    w = MainWindow(config, db)
    try:
        w.show()
        qapp.processEvents()
        assert not w.ref_bar.isVisible() and not w.control_bar.btn_refs.isVisible()
    finally:
        w.close()
        w.deleteLater()
        qapp.processEvents()


def test_reference_bar_pick_shows_in_multi(qapp, win, tmp_path):
    ref = _add_reference(win, tmp_path)
    caps = _photos(win, 1)
    panel = win.thumbnail_panel
    rb = win.ref_bar
    rb.reload()                                                           # 숨긴 모델: 질환 아이콘이 처음 고를 때 읽음
    assert _wait(qapp, lambda: len(rb.refs()) == 1)
    assert any("수정체" in rb.c_cat.itemText(i) for i in range(rb.c_cat.count()))
    rb.toggle(0)                                                          # 참조 사진을 고르면 바로 멀티로
    assert win._multi and win.multi_view.count() == 1
    ref_cell = win.multi_view.captures()[0]
    assert ref_cell.is_reference and ref_cell.ref.id == ref.id
    QTest.mouseClick(panel._widgets[caps[0].id], Qt.MouseButton.LeftButton)   # 지금 환자 사진도 고르면 나란히
    got = win.multi_view.captures()
    assert [getattr(c, "is_reference", False) for c in got] == [False, True]  # 환자 사진 → 참조 순
    assert "참조" in win.multi_view.canvases()[1]._caption
    mp = win.multi_pro
    win.multi_view.set_active(win.multi_view.canvases()[1])               # 참조 칸: 측정은 되고 기록은 안 됨
    assert mp.current_capture() is None
    mp.analysis.set_kind("redness")
    assert mp.analysis.b_run.isEnabled()
    mp.analysis.b_run.click()
    assert mp._analysis_text.startswith("충혈 지수") and not mp.analysis.b_note.isEnabled()
    mp.b_flip_h.click()                                                   # 참조 칸 편집은 편집본 저장에서 빠짐
    assert not mp.b_edit_save.isEnabled()
    comp = win.multi_view.composite()
    assert comp is not None and comp.shape[1] > comp.shape[0]
    rb.toggle(0)                                                          # 참조를 빼면 환자 사진만
    assert win._multi and win.multi_view.count() == 1
    rb.toggle(0)
    QTest.mouseClick(win.control_bar.btn_close_multi, Qt.MouseButton.LeftButton)   # '초기화' 는 참조 선택도 초기화
    assert not win._multi and rb.selected_refs() == [] and panel.selected_captures() == []


def test_multi_fits_small_screen(qapp, win):
    """1920×1080 화면 · 150% 배율 = 1280×720 (작업 표시줄 빼면 약 680) — 멀티 화면에서도 창이 화면보다 커지지 않게."""
    _enter_multi(qapp, win, 2)
    for _ in range(5):
        qapp.processEvents()
    assert not win.ref_bar.isVisible()                                   # 참조 줄은 없음 (v1.20.3)
    hint = win.minimumSizeHint()
    assert hint.height() <= 680 and hint.width() < 1200


# ---------------- 오른쪽 클릭 = 멀티 · 큰 보기 돋보기 · 충혈 민감도 ----------------
def test_right_click_menu_opens_multi(qapp, win):
    caps = _photos(win, 3)
    panel = win.thumbnail_panel
    QTest.mouseClick(panel._widgets[caps[0].id], Qt.MouseButton.LeftButton)       # 1장 고름
    QTest.mouseClick(panel._widgets[caps[2].id], Qt.MouseButton.RightButton)      # 다른 사진 오른쪽 클릭 → 그 사진도 고르고 메뉴만
    qapp.processEvents()
    assert panel.context_menu.isVisible() and not win._multi
    assert [c.id for c in panel.selected_captures()] == [caps[0].id, caps[2].id]
    panel.menu_actions["multi"].trigger()
    for _ in range(5):
        qapp.processEvents()
    assert win._multi and [c.id for c in win.multi_view.captures()] == [caps[0].id, caps[2].id]
    assert win.control_bar.btn_multi.isChecked() and not panel.context_menu.isVisible()
    _menu(qapp, panel, caps[2], "multi")                                           # 이미 고른 사진 → 그대로
    assert win._multi and win.multi_view.count() == 2
    QTest.mouseClick(win.control_bar.btn_close_multi, Qt.MouseButton.LeftButton)  # '초기화' 로 닫기 (선택 초기화)
    assert not win._multi and panel.selected_captures() == []
    QTest.mouseClick(win.video_view, Qt.MouseButton.RightButton)                  # 고른 사진 없이 영상 오른쪽 클릭 → 안내만
    assert not win._multi and "오른쪽 클릭" in win.statusBar().currentMessage()
    QTest.mouseClick(panel._widgets[caps[1].id], Qt.MouseButton.LeftButton)
    QTest.mouseClick(win.video_view, Qt.MouseButton.RightButton)                  # 라이브 영상 오른쪽 클릭 → 고른 사진으로 멀티
    for _ in range(5):
        qapp.processEvents()
    assert win._multi and [c.id for c in win.multi_view.captures()] == [caps[1].id]


def test_image_viewer_has_magnifier(qapp, win):
    caps = _photos(win, 1)
    from app.ui.image_viewer import ImageViewerDialog
    dlg = ImageViewerDialog(caps[0], win)
    dlg.show()
    qapp.processEvents()
    cv = dlg.canvas
    assert cv.image() is not None and cv.tool() == "magnify" and dlg.tool_buttons["magnify"].isChecked()
    vp = cv.viewport()
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vp.rect().center())
    assert cv._lens is not None                                                    # 누르는 동안 확대 렌즈
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vp.rect().center())
    assert cv._lens is None
    dlg.tool_buttons["pan"].click()
    assert cv.tool() == "pan"
    dlg.b_fit.click()
    dlg.close()


def test_redness_sensitivity_slider_remeasures(qapp, win):
    import re

    import numpy as np
    from app.services.capture_service import CaptureService
    img = np.zeros((120, 160, 3), np.uint8)
    img[:] = (90, 110, 200)                                                        # 옅게 붉은 바탕 (BGR)
    img[40:80, 40:120] = (60, 70, 230)                                             # 더 붉은 부위
    cap = CaptureService(win.db, win.config).save_photo(img, win._current_patient, eye="OD")
    panel = win.thumbnail_panel
    panel.set_patient(win._current_patient.id)
    qapp.processEvents()
    _menu(qapp, panel, cap, "multi")                                                # 오른쪽 클릭 → 멀티
    mp = win.multi_pro
    assert mp.analysis.s_red_sens.value() == 5 and "기본" in mp.analysis.lbl_red_sens.text()
    r5 = mp.measure_redness()
    assert "민감도 5" in mp._analysis_text and r5["vessel_pct"] > 90
    mp.analysis.s_red_sens.setValue(1)                                             # 낮추면 바로 다시 측정 (더 붉은 곳만)
    pct1 = float(re.search(r"혈관 화소 ([\d.]+)%", mp._analysis_text).group(1))
    assert "민감도 1" in mp._analysis_text and pct1 < r5["vessel_pct"] and win.config.redness_sensitivity == 1
    mp.clear_analysis()
    mp.analysis.s_red_sens.setValue(8)                                             # 측정 안 했으면 다시 재지 않음
    assert mp._analysis_text == "" and win.config.redness_sensitivity == 8


def test_multi_reset_button_returns_to_live(qapp, win, tmp_path):
    caps = _enter_multi(qapp, win, 2)
    cb, panel = win.control_bar, win.thumbnail_panel
    assert cb.btn_close_multi.isVisible() and cb.btn_close_multi.text() == "초기화"
    assert not cb.btn_clear_quad.isVisible()
    x_send = cb.btn_send.mapTo(cb, cb.btn_send.rect().topLeft()).x()
    assert cb.btn_close_multi.mapTo(cb, cb.btn_close_multi.rect().topLeft()).x() > x_send   # 전송 오른쪽
    QTest.mouseClick(cb.btn_close_multi, Qt.MouseButton.LeftButton)
    for _ in range(5):
        qapp.processEvents()
    assert not win._multi and win.view_stack.currentIndex() == 0                      # 라이브 촬영 화면으로
    assert panel.selected_captures() == [] and not cb.btn_multi.isChecked()
    assert not cb.btn_close_multi.isVisible()
    _menu(qapp, panel, caps[0], "multi")                                             # 다시 오른쪽 클릭 → 멀티
    assert win._multi and cb.btn_close_multi.isVisible()


def test_long_photo_notes_do_not_widen_window(qapp, win):
    """코멘트가 아주 긴 사진(샘플 출처 · 주소)이 있어도 창 최소 폭이 화면(1920)보다 커지지 않게 — Pro MULTI 캡션."""
    import numpy as np
    from app.services.capture_service import CaptureService
    svc = CaptureService(win.db, win.config)
    note = "[샘플] 안검염 염증 및 마이봄샘 입구/도관 폐쇄 (저해상도 600×454)\n출처: University of Iowa EyeRounds; " + \
           "https://eyerounds.org/atlas/pages/Blepharitis-and-meibomian-gland-dysfunction-with-a-very-long-address.html " * 3
    for i in range(3):
        svc.save_photo(np.full((120, 160, 3), 60 + i * 40, np.uint8), win._current_patient, eye="OD", note=note)
    win.pro.reload()
    win.thumbnail_panel.set_patient(win._current_patient.id)
    for _ in range(5):
        qapp.processEvents()
    assert win.pro.minimumSizeHint().width() < 1200
    assert win.minimumSizeHint().width() < 1200


def test_right_click_menu_send_and_delete(qapp, win, monkeypatch):
    """오른쪽 클릭 메뉴 '전송' = 고른 사진 전송, '삭제' = 확인 후 고른 사진 삭제 (멀티 중이면 닫고)."""
    from PySide6.QtWidgets import QMessageBox
    caps = _photos(win, 3)
    panel = win.thumbnail_panel
    sent = []
    monkeypatch.setattr(win, "_on_send", lambda: sent.append([c.id for c in panel.selected_captures()]))
    QTest.mouseClick(panel._widgets[caps[0].id], Qt.MouseButton.LeftButton)
    _menu(qapp, panel, caps[1], "send")
    assert sent == [[caps[0].id, caps[1].id]]
    answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answers.pop(0))
    n0 = panel.count()
    _menu(qapp, panel, caps[1], "delete")                                # 아니오 → 그대로
    assert panel.count() == n0
    _menu(qapp, panel, caps[1], "multi")
    assert win._multi
    _menu(qapp, panel, caps[1], "delete")                                # 예 → 2장 삭제 · 멀티 닫힘
    assert panel.count() == n0 - 2 and not win._multi and panel.selected_captures() == []
    left = [c.id for c in win.db.captures.list_by_patient(win._current_patient.id)]
    assert caps[2].id in left and caps[0].id not in left and caps[1].id not in left


def test_live_multi_setting_left_click_shows_multi(qapp, win):
    """설정 '실시간 멀티화면': 왼쪽 클릭으로 고르는 즉시 가운데 멀티 화면, 다 빼면 라이브로 (끄면 예전처럼 고르기만)."""
    caps = _photos(win, 2)
    panel = win.thumbnail_panel
    QTest.mouseClick(panel._widgets[caps[0].id], Qt.MouseButton.LeftButton)
    assert not win._multi                                                  # 기본은 꺼짐
    panel.clear_selection()
    win.config.live_multi = True
    QTest.mouseClick(panel._widgets[caps[0].id], Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert win._multi and win.multi_view.count() == 1
    QTest.qWait(QApplication.doubleClickInterval() + 100)
    QTest.mouseClick(panel._widgets[caps[1].id], Qt.MouseButton.LeftButton)
    assert win._multi and win.multi_view.count() == 2
    QTest.qWait(QApplication.doubleClickInterval() + 100)
    QTest.mouseClick(panel._widgets[caps[0].id], Qt.MouseButton.LeftButton)
    QTest.qWait(QApplication.doubleClickInterval() + 100)
    QTest.mouseClick(panel._widgets[caps[1].id], Qt.MouseButton.LeftButton)  # 다 빼면 라이브
    assert not win._multi and win.view_stack.currentIndex() == 0


def test_settings_has_live_multi(qapp, app_env):
    from app.ui.settings_dialog import SettingsDialog
    config, _ = app_env
    config.save = lambda: None
    assert config.live_multi is False
    dlg = SettingsDialog(config)
    assert not dlg.live_multi.isChecked()
    dlg.live_multi.setChecked(True)
    dlg.apply_to_config()
    assert config.live_multi is True
    dlg.deleteLater()


def test_thumbnail_sharp_from_original_and_retina(qapp, app_env, tmp_path):
    """예전 120px 썸네일은 원본 사진에서 480px 로 다시 읽고, 레티나(2배)에서는 2배 크기로 그린다."""
    import cv2
    import numpy as np
    from app.models.capture import Capture
    from app.ui import thumbnail_widget as tw
    big, small = tmp_path / "big.jpg", tmp_path / "small.jpg"
    cv2.imwrite(str(big), np.full((1080, 1920, 3), 90, np.uint8))
    cv2.imwrite(str(small), np.full((68, 120, 3), 90, np.uint8))
    cap = Capture(id=1, patient_id=1, media_type="photo", file_path=str(big), thumbnail_path=str(small),
                  captured_at="2026-10-31 11:45:00", eye="OD")
    tw._cache.clear()
    w = tw.ThumbnailWidget(cap)
    assert w._pm is not None and w._pm.width() == 120                        # 작은 것 먼저 (화면이 멈추지 않게)
    assert _wait(qapp, lambda: w._pm.width() == tw.SHARP_W, 5)             # 뒤에서 원본으로 선명하게 바꿔 그림
    assert cv2.imread(str(small)).shape[1] == tw.SHARP_W                     # 썸네일 파일도 새로 → 다음엔 바로
    assert tw.ThumbnailWidget(cap)._pm.width() == tw.SHARP_W
    w.resize(230, 172)
    w.devicePixelRatioF = lambda: 2.0
    s = w._sharp_pixmap()
    assert s.devicePixelRatio() == 2.0 and s.width() >= 2 * 220            # 화면 배율만큼 크게
    w.deleteLater()
    from app.config import AppConfig
    assert AppConfig().thumbnail_max_side >= 480


def test_right_click_action_setting_multi_direct(qapp, win, app_env):
    """설정 '사진 오른쪽 클릭' = 멀티 화면 바로 → 메뉴 없이 멀티 (기본은 메뉴)."""
    from app.ui.settings_dialog import SettingsDialog
    caps = _photos(win, 2)
    panel = win.thumbnail_panel
    assert win.config.right_click_action == "menu" and panel.right_click_action == "menu"
    dlg = SettingsDialog(win.config)
    assert [dlg.right_click.itemData(i) for i in range(dlg.right_click.count())] == ["menu", "multi"]
    dlg.right_click.setCurrentIndex(1)
    dlg.apply_to_config()
    dlg.deleteLater()
    assert win.config.right_click_action == "multi"
    panel.right_click_action = win.config.right_click_action          # (메인 창은 설정 창을 닫을 때 반영)
    QTest.mouseClick(panel._widgets[caps[0].id], Qt.MouseButton.RightButton)
    for _ in range(5):
        qapp.processEvents()
    assert win._multi and not panel.context_menu.isVisible() and win.multi_view.count() == 1


def test_menu_items_setting_and_send_then_delete(qapp, win, monkeypatch):
    """설정 '메뉴 항목'으로 전송 · 전송(삭제) · 멀티 · 삭제 중 고르기 — 전송(삭제)는 모두 보냈을 때만 확인 후 지운다."""
    from PySide6.QtWidgets import QMessageBox
    from app.services.send_service import SendResult
    from app.ui.settings_dialog import SettingsDialog
    caps = _photos(win, 3)
    panel = win.thumbnail_panel
    dlg = SettingsDialog(win.config)
    assert [k for k, cb in dlg.menu_items.items() if cb.isChecked()] == ["send", "multi", "delete"]
    dlg.menu_items["send_delete"].setChecked(True)
    dlg.menu_items["multi"].setChecked(False)
    dlg.apply_to_config()
    dlg.deleteLater()
    assert win.config.right_click_items == ["send", "send_delete", "delete"]
    panel.set_menu_items(win.config.right_click_items)
    assert panel.menu_items() == ["send", "send_delete", "delete"]
    shown = [a.text() for a in panel.context_menu.actions() if a.isVisible() and a.isEnabled() and a.text()]
    assert shown == ["전송", "전송(삭제)", "삭제"]
    results = [SendResult(sent=["a"], errors=["PACS 응답 없음"]), SendResult(sent=["a", "b"])]
    monkeypatch.setattr(win, "_on_send", lambda: results.pop(0))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    n0 = panel.count()
    QTest.mouseClick(panel._widgets[caps[0].id], Qt.MouseButton.LeftButton)
    _menu(qapp, panel, caps[1], "send_delete")                           # 오류가 있으면 지우지 않음
    assert panel.count() == n0
    _menu(qapp, panel, caps[1], "send_delete")                           # 모두 보냄 → 확인 후 2장 삭제
    assert panel.count() == n0 - 2
    panel.set_menu_items([])                                              # 비면 기본
    assert panel.menu_items() == ["send", "multi", "delete"]


def test_eye_badges_od_right_os_left(qapp, app_env, tmp_path):
    """썸네일 눈 표시: OD(우안)는 오른쪽 위 · OS(좌안)는 왼쪽 위, 크게 — 필터 · 눈 선택도 OS 왼쪽 · OD 오른쪽."""
    import cv2
    import numpy as np
    from PySide6.QtGui import QImage
    from app.models.capture import Capture
    from app.ui import control_bar, thumbnail_panel
    from app.ui import thumbnail_widget as tw
    img = tmp_path / "g.png"
    cv2.imwrite(str(img), np.full((300, 400, 3), 128, np.uint8))

    def badge_side(eye):
        cap = Capture(id=1, patient_id=1, media_type="photo", file_path=str(img), captured_at="2026-10-31 11:45:00", eye=eye)
        w = tw.ThumbnailWidget(cap)
        w.resize(240, 180)
        im = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        col = tw.EYE_COLORS[eye].lstrip("#")
        want = tuple(int(col[i:i + 2], 16) for i in (0, 2, 4))
        xs = [x for x in range(im.width()) for y in range(4, 4 + tw.EYE_BADGE_H)
              if sum(abs(a - b) for a, b in zip(im.pixelColor(x, y).getRgb()[:3], want)) < 60]
        w.deleteLater()
        return sum(xs) / len(xs) if xs else None
    od, os_ = badge_side("OD"), badge_side("OS")
    assert od is not None and os_ is not None and od > 120 > os_
    assert [k for k, _t in thumbnail_panel._FILTERS] == ["all", "OS", "OD"]
    assert [c for c, *_x in control_bar._EYE_BUTTONS][:2] == ["OS", "OD"]


def test_patient_dropdown_removed_search_picks_patient(qapp, win):
    """상단 환자 선택 드롭다운은 없고, 검색 칸에 치면 결과 목록 — 누르거나 Enter 로 고르고, 지금 환자는 검색 칸에 흐리게."""
    pp = win.patient_panel
    win.db.patients.insert(Patient(chart_no="K77", name="김선명"))
    win.db.patients.insert(Patient(chart_no="K78", name="김선택"))
    pp.select_patient(win._current_patient.id)
    qapp.processEvents()
    assert not pp.combo.isVisible() and pp.search.isVisible()
    assert pp.search.placeholderText().startswith("환자: ") and win._current_patient.name in pp.search.placeholderText()
    pp.search.setFocus()
    pp.search.setText("김선")                                          # (한글 IME 입력 = 글자가 바뀜)
    qapp.processEvents()
    assert [p.chart_no for p in pp._matches] == ["K77", "K78"] or sorted(p.chart_no for p in pp._matches) == ["K77", "K78"]
    assert win._current_patient.chart_no == "B1"                      # 치기만 해서는 바뀌지 않음
    first = pp._matches[0]
    QTest.keyClick(pp.search, Qt.Key.Key_Return)                       # Enter → 첫 결과
    qapp.processEvents()
    assert win._current_patient.id == first.id and pp.search.text() == ""
    assert first.name in pp.search.placeholderText()
    pp.search.setText("K78")
    qapp.processEvents()
    pp._pick_match(0)                                                  # 결과 목록에서 누름
    QTest.qWait(50)
    assert win._current_patient.chart_no == "K78" and pp.search.text() == ""


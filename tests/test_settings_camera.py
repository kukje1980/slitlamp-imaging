"""설정 창 카메라 목록 — 이름으로 고르기, 저장한 카메라가 없을 때 안내, 다시 찾기."""
from __future__ import annotations

import app.ui.settings_dialog as sd


def test_camera_list_by_name_and_missing_hint(qapp, app_env, monkeypatch):
    config, _ = app_env
    config.source_mode = "synthetic"
    config.camera_name = "Sony Camera (Imaging Edge)"
    monkeypatch.setattr(sd, "device_names", lambda: ["Camera Sensor Front"])
    dlg = sd.SettingsDialog(config)
    items = [dlg.camera_index.itemText(i) for i in range(dlg.camera_index.count())]
    assert items == ["0: Camera Sensor Front"]
    assert not dlg.camera_index.isEditable()          # 글자 칸이 아니라 목록에서 고르기
    assert not dlg.camera_hint.isHidden()
    assert "Sony Camera (Imaging Edge)" in dlg.camera_hint.text()

    # 소니 카메라를 USB 스트리밍으로 연결한 뒤 '다시 찾기'
    monkeypatch.setattr(sd, "device_names", lambda: ["Camera Sensor Front", "ZV-E10M2"])
    dlg._fill_cameras()
    items = [dlg.camera_index.itemText(i) for i in range(dlg.camera_index.count())]
    assert items == ["0: Camera Sensor Front", "1: ★ ZV-E10M2"]
    dlg.camera_index.setCurrentIndex(1)
    dlg.source_mode.setCurrentIndex(dlg.source_mode.findData("device"))
    dlg.apply_to_config()
    assert (config.source_mode, config.camera_index, config.camera_name) == ("device", 1, "ZV-E10M2")


def test_saved_camera_selected_by_name(qapp, app_env, monkeypatch):
    config, _ = app_env
    config.source_mode = "synthetic"
    config.camera_index, config.camera_name = 0, "ZV-E10M2"     # 장치 순서가 바뀌어도 이름으로
    monkeypatch.setattr(sd, "device_names", lambda: ["Camera Sensor Front", "ZV-E10M2"])
    dlg = sd.SettingsDialog(config)
    assert dlg.camera_index.currentData() == 1
    assert dlg.camera_hint.isHidden()


def test_qss_has_visible_arrows():
    from pathlib import Path

    from app.ui.theme import build_qss
    qss = build_qss("dark")
    assert "QComboBox::down-arrow" in qss and "QSpinBox::up-arrow" in qss
    url = qss.split('QComboBox::down-arrow {{ image: url("'.replace("{{", "{"))[1].split('")')[0]
    assert Path(url).exists()


def test_picking_camera_switches_to_device_mode(qapp, app_env, monkeypatch):
    config, _ = app_env
    config.source_mode = "synthetic"
    config.save = lambda: None
    monkeypatch.setattr(sd, "device_names", lambda: ["Camera Sensor Front", "ZV-E10M2"])
    dlg = sd.SettingsDialog(config)
    dlg.camera_index.setCurrentIndex(1)
    dlg.camera_index.activated.emit(1)                # 사용자가 목록에서 고름
    assert dlg.source_mode.currentData() == "device"
    dlg.apply_to_config()
    assert (config.source_mode, config.camera_name) == ("device", "ZV-E10M2")

"""컴퓨터 사양 안내 — 이 PC 확인, 판정, 설정 창 탭."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app import system_specs as sp


def test_detect_reports_this_machine(tmp_path):
    info = sp.detect(str(tmp_path))
    assert info["cores"] and info["cores"] > 0
    assert info["ram_gb"] is None or info["ram_gb"] > 0.5      # 리눅스·맥·윈도우 모두 읽힘, 못 읽으면 None
    assert info["free_gb"] is not None and info["free_gb"] > 0
    assert info["os"]
    assert "코어" in sp.summary_line(info)


def test_free_space_follows_missing_subfolder(tmp_path):
    # 아직 만들지 않은 저장 폴더라도 상위 폴더가 있는 드라이브의 여유 공간을 읽는다
    assert sp.detect(str(tmp_path / "아직" / "없는" / "폴더"))["free_gb"] > 0
    assert sp.detect(None)["free_gb"] is None


def test_verdict_thresholds():
    assert sp.verdict({"cores": 8, "ram_gb": 15.8})[0] == "good"      # 16GB 는 보통 15.x 로 읽힘
    assert sp.verdict({"cores": 6, "ram_gb": 16.0})[0] == "good"
    assert sp.verdict({"cores": 4, "ram_gb": 7.8})[0] == "min"
    assert sp.verdict({"cores": 8, "ram_gb": 8.0})[0] == "min"        # 코어는 충분해도 메모리 8GB 면 최소
    assert sp.verdict({"cores": 2, "ram_gb": 16.0})[0] == "below"
    assert sp.verdict({"cores": 4, "ram_gb": 4.0})[0] == "below"
    assert sp.verdict({"cores": None, "ram_gb": None})[0] == "unknown"


def test_html_has_both_tables_and_modes():
    h = sp.html({"os": "Windows 11", "cores": 8, "ram_gb": 15.9, "free_gb": 20.0})
    for s in ("최소 사양", "권장 사양", "일반 (기본)", "고화질", "CRF 23", "CRF 18", "16GB", "4코어",
              "그래픽카드", "USB 3.0", "권장 사양을 충족", "여유 공간이 20GB"):
        assert s in h, s
    h2 = sp.html()                                                    # 이 PC 정보 없이도 표만 그린다
    assert "이 PC" not in h2 and "최소 사양" in h2


def test_settings_dialog_has_specs_tab(qtbot, app_env):
    from app.ui.settings_dialog import SettingsDialog
    config, _ = app_env
    dlg = SettingsDialog(config)
    qtbot.addWidget(dlg)
    names = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert names[:3] == ["설정 안내", "화면 · 소리", "촬영"] and names[-1] == "컴퓨터 사양"
    text = dlg.specs_label.text()
    assert "권장 사양" in text and "고화질" in text and "이 PC" in text
    dlg.show_tab("컴퓨터 사양")
    assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == "컴퓨터 사양"

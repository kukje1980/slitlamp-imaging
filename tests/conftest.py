"""테스트 공통 픽스처. Qt는 offscreen 플랫폼으로 강제."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.config import AppConfig
from app.db.database import Database


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """APPDATA를 임시 폴더로 돌려 설정/DB/미디어를 격리한다."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    config = AppConfig.load()
    config.export_root = str(tmp_path / "export")
    config.start_screen = "capture"              # 테스트는 촬영 화면에서 시작 (홈 화면 테스트는 따로 지정)
    # v1.20.0 의 촬영 직후 보기 창 · 멀티 메뉴 접기는 tests/test_dx_bar.py 에서 켜고 확인한다
    # (다른 테스트는 예전처럼 촬영 뒤 창 없이, 멀티 메뉴가 펴진 상태로)
    config.view_after_capture = False
    config.multi_side_visible = True
    db = Database(config.db_path).init()
    yield config, db
    db.close()

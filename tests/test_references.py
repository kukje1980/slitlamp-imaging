"""기본판 · Keeler 판(윈도우 · 맥 앱)의 참조 영상 — 샘플 사진 질환별 보기 · 지금 사진과 비교 · 참조 등록,
Keeler 판 상단바(홈페이지 색: 네이비 · 흰 글자 로고 · 파랑 선 · 주황 셔터 링)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app import brand
from app.models.patient import Patient


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
    for i, (rel, info) in enumerate({
        "01_건성안/리사민그린_1.jpg": {"description": "중증 건성각결막염", "license": "CC BY-NC-ND 3.0"},
        "04_백내장/핵백내장_1.jpg": {"title": "핵경화 백내장"},
    }.items()):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        img = np.full((90, 120, 3), 40 + i * 80, np.uint8)
        data = cv2.imencode(".jpg", img)[1].tobytes()
        path.write_bytes(data)
        meta.append({"file": rel, "sha256": hashlib.sha256(data).hexdigest(), **info})
    (root / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return root


def _make_window(qapp, app_env, monkeypatch, brand_key="default"):
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 320, 240
    config.save = lambda: None
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setenv(brand.ENV_KEY, brand_key)
    db.patients.insert(Patient(chart_no="R1", name="참조 환자", diagnosis="백내장"))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.show()
    assert _wait(qapp, lambda: w._last_frame is not None)
    return w


@pytest.fixture()
def win(qapp, app_env, monkeypatch):
    w = _make_window(qapp, app_env, monkeypatch)
    yield w
    w.close()
    brand.select("default")


def test_standard_window_reference_library(qapp, win, tmp_path):
    w = win
    assert not hasattr(w, "btn_refs")                          # 상단바 '참조 영상' 없앰
    assert not w.control_bar.btn_refs.isVisible()              # v1.20.3: 아래 도크 '참조' 줄도 없앰 (설정 → 참조 영상 라이브러리)
    dlg = w.open_references()                                  # 사진·샘플이 없어도 열림
    assert dlg.refs() == [] and not dlg.b_compare.isEnabled() and dlg.b_register.isHidden()
    dlg.close()

    assert w._import_samples(folder=str(_samples(tmp_path / "s")))
    if w._current_patient is None or w._current_patient.chart_no != "R1":
        w.patient_panel.select_patient(next(p.id for p in w.db.patients.list_all() if p.chart_no == "R1"))
    w._on_capture()
    assert _wait(qapp, lambda: any(c.media_type == "photo"
                                   for c in w.db.captures.list_by_patient(w._current_patient.id)))
    cap = w._reference_capture()
    assert cap is not None and cap.patient_id == w._current_patient.id

    dlg = w.open_references()
    assert len(dlg.refs()) == 2 and all(r.is_sample for r in dlg.refs())   # 샘플 사진이 질환별로
    assert any(dlg.cats.item(i).text().startswith("건성안") for i in range(dlg.cats.count()))
    assert dlg.b_compare.isEnabled() and not dlg.b_register.isHidden()
    cmp = dlg.compare()
    assert len(cmp.canvases) == 2 and cmp.canvases[0]._caption.startswith("참조 환자")
    dlg.close()

    assert w._register_reference(cap, values=("수정체 · 백내장", "지금 환자", ""))
    dlg = w.open_references()
    assert len(dlg.refs()) == 3 and w.db.references.categories() == ["수정체 · 백내장"]
    dlg.close()


def test_keeler_standard_header_colors(qapp, app_env, monkeypatch):
    w = _make_window(qapp, app_env, monkeypatch, "keeler")
    try:
        assert w.brand_logo is not None and not w.brand_logo.pixmap().isNull()
        before = w.brand_logo.pixmap().toImage()
        w._toggle_theme()                                      # 밝은 테마여도 상단바는 네이비 · 흰 글자 로고
        assert w.brand_logo.pixmap().toImage() == before
        from app.ui.theme import build_qss
        for mode in ("dark", "light"):
            qss = build_qss(mode)
            assert "QFrame#TopBar { background: #052133;" in qss
            # 아래 선 대신 로고 K 4색(하늘 · 파랑 · 네이비 · 주황) 줄무늬
            assert "QFrame#TopBar { background: #052133; border: none; border-bottom: none; }" in qss
            stripe = qss[qss.index("QFrame#BrandStripe"):].split("}", 1)[0]
            assert all(c in stripe for c in ("#6cbce8", "#1981b7", "#052133", "#ed6f1e"))
            assert "rgba(237, 111, 30, 0.85)" in qss
    finally:
        w.close()
        brand.select("default")
    from app.ui.theme import build_qss
    assert "QFrame#TopBar { background: #052133;" not in build_qss("light")   # 기본판은 그대로

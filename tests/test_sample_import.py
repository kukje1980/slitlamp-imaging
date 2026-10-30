"""질환별 샘플 사진 가져오기 — 질환 폴더별 샘플 환자, 원본 그대로 복사, 중복·변조 건너뛰기, zip."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services.sample_import import group_of, import_samples, sample_note


def _jpg(path: Path, w: int, h: int, seed: int) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    path.write_bytes(buf.tobytes())
    return buf.tobytes()


def _library(root: Path) -> Path:
    """실제 수집본과 같은 모양: 질환별 하위 폴더 + metadata.json."""
    files = {
        "01_건성안/리사민그린_1.jpg": (1200, 800, {"description": "중증 건성각결막염; 리사민그린 염색.",
                                            "author": "EyeRounds", "license": "CC BY-NC-ND 3.0",
                                            "source": "https://example.org/kcs"}),
        "06_결막하출혈/결막하출혈_1.jpg": (1500, 1200, {"title": "결막하출혈", "license": "CC BY-NC-ND 3.0",
                                                 "source_context": "Category(ies)\n: Cornea, External Disease, Trauma\n"}),
        "09_감염각막염/세균각막염_1.jpg": (400, 309, {"title": "세균각막염"}),
    }
    meta = []
    for i, (rel, (w, h, info)) in enumerate(files.items()):
        data = _jpg(root / rel, w, h, i)
        meta.append({"file": rel, "width": w, "height": h, "sha256": hashlib.sha256(data).hexdigest(), **info})
    (root / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return root


def test_group_names():
    assert group_of("02_안검염_MGD") == ("02", "안검염·마이봄샘 기능장애(MGD)")
    assert group_of("10_백내장수술후_후낭혼탁") == ("10", "백내장 수술 후 후낭혼탁(PCO)")
    assert group_of("12_원추각막") == ("12", "원추각막")


def test_note_tags():
    note = sample_note({"title": "세균각막염", "author": "A", "license": "CC BY"}, "x", 400, 309)
    assert note.splitlines()[0] == "[샘플] 세균각막염 (저해상도 400×309)"
    assert note.splitlines()[1] == "출처: A · CC BY"
    trauma = sample_note({"title": "찰과상", "source_context": "Category(ies)\n: Cornea, Trauma"}, "x", 2048, 1600)
    assert trauma.splitlines()[0] == "[샘플] 찰과상 (외상)"


def test_import_creates_patient_per_disease(app_env, tmp_path):
    config, db = app_env
    lib = _library(tmp_path / "lib")
    r = import_samples(db, config, lib)
    assert (r.patients_created, r.photos_imported) == (3, 3)
    assert not r.mismatched and not r.failed

    pats = {p.chart_no: p for p in db.patients.list_all()}
    assert set(pats) == {"SAMPLE-01", "SAMPLE-06", "SAMPLE-09"}
    assert pats["SAMPLE-06"].name == "샘플 · 결막하출혈"
    assert pats["SAMPLE-06"].diagnosis == "결막하출혈"

    caps = db.captures.list_by_patient(pats["SAMPLE-01"].id)
    assert len(caps) == 1
    cap = caps[0]
    # 원본 그대로 복사 (변경 금지 라이선스) + 썸네일 + export 하지 않음
    assert Path(cap.file_path).read_bytes() == (lib / "01_건성안/리사민그린_1.jpg").read_bytes()
    assert Path(cap.file_path).name == "리사민그린_1.jpg"
    assert cap.thumbnail_path and Path(cap.thumbnail_path).exists()
    assert cap.export_path is None and cap.eye == "NA"
    assert cap.note.startswith("[샘플] 중증 건성각결막염")
    assert "CC BY-NC-ND 3.0" in cap.note and "https://example.org/kcs" in cap.note

    low = db.captures.list_by_patient(pats["SAMPLE-09"].id)[0]
    assert "저해상도 400×309" in low.note
    trauma = db.captures.list_by_patient(pats["SAMPLE-06"].id)[0]
    assert "(외상)" in trauma.note


def test_reimport_skips_duplicates(app_env, tmp_path):
    config, db = app_env
    lib = _library(tmp_path / "lib")
    import_samples(db, config, lib)
    r = import_samples(db, config, lib)
    assert (r.patients_created, r.patients_reused, r.photos_imported, r.duplicates_skipped) == (0, 3, 0, 3)
    assert sum(len(db.captures.list_by_patient(p.id)) for p in db.patients.list_all()) == 3


def test_tampered_file_is_not_imported(app_env, tmp_path):
    config, db = app_env
    lib = _library(tmp_path / "lib")
    _jpg(lib / "09_감염각막염/세균각막염_1.jpg", 400, 309, 99)      # metadata 의 sha256 과 달라짐
    r = import_samples(db, config, lib)
    assert r.mismatched == ["세균각막염_1.jpg"]
    assert r.photos_imported == 2
    assert "SAMPLE-09" not in {p.chart_no for p in db.patients.list_all()}


def test_folder_without_metadata(app_env, tmp_path):
    config, db = app_env
    _jpg(tmp_path / "lib/05_다래끼_콩다래끼/콩다래끼_1.jpg", 900, 300, 1)
    r = import_samples(db, config, tmp_path / "lib")
    assert r.photos_imported == 1
    p = db.patients.list_all()[0]
    assert p.name == "샘플 · 다래끼·콩다래끼"
    assert db.captures.list_by_patient(p.id)[0].note == "[샘플] 콩다래끼_1 (저해상도 900×300)"


def test_zip_import_and_wrapper_folder(app_env, tmp_path):
    config, db = app_env
    lib = _library(tmp_path / "lib")
    zpath = tmp_path / "samples.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for f in lib.rglob("*"):
            if f.is_file():
                zf.write(f, "일반외래_슬릿램프_질환순/" + f.relative_to(lib).as_posix())
    r = import_samples(db, config, zpath)
    assert (r.patients_created, r.photos_imported) == (3, 3)
    assert not r.mismatched


def test_zip_path_traversal_rejected(app_env, tmp_path):
    config, db = app_env
    zpath = tmp_path / "bad.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../evil.jpg", b"x")
    with pytest.raises(ValueError):
        import_samples(db, config, zpath)
    assert not (tmp_path / "evil.jpg").exists()


def test_empty_or_missing_folder(app_env, tmp_path):
    config, db = app_env
    with pytest.raises(FileNotFoundError):
        import_samples(db, config, tmp_path / "nope")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError):
        import_samples(db, config, tmp_path / "empty")


def test_main_window_button(app_env, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    qapp = QApplication.instance() or QApplication([])
    config, db = app_env
    config.source_mode = "synthetic"
    config.save = lambda: None
    lib = _library(tmp_path / "lib")
    shown = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(lib))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    try:
        w._import_samples()
        qapp.processEvents()
        assert shown and "사진 3장을 등록했습니다" in shown[0]
        items = [w.patient_panel.combo.itemText(i) for i in range(w.patient_panel.combo.count())]
        assert "샘플 · 건성안 (SAMPLE-01)" in items
    finally:
        w.close()


def test_bundled_samples_registered_once(app_env):
    from app.services.sample_import import BUNDLE_ID, bundled_samples_dir, ensure_bundled_samples
    config, db = app_env
    src = bundled_samples_dir()
    assert src is not None and (src / "metadata.json").is_file()
    r = ensure_bundled_samples(db, config)
    assert r is not None and r.photos_imported == 26 and not r.mismatched and not r.failed
    assert config.bundled_samples == BUNDLE_ID
    charts = sorted(p.chart_no for p in db.patients.list_all() if p.chart_no.startswith("SAMPLE-"))
    assert charts == [f"SAMPLE-{i:02d}" for i in range(11)]
    # 두 번째 실행부터는 다시 등록하지 않음 (사용자가 지운 샘플도 되살리지 않음)
    assert ensure_bundled_samples(db, config) is None

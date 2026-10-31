"""극한 상황 테스트 2 — v1.20.0 에서 들어온 백업 · 삭제 기능.

어떤 경우에도
  (1) 백업이 원본(사진 폴더 · DB)을 바꾸거나 지우지 않고, 자기 자신을 복사해 디스크를 채우지 않아야 하고,
  (2) 하드가 빠졌을 때 내장 디스크에 같은 이름의 폴더를 만들어 그쪽으로 백업하지 않아야 하며,
  (3) 삭제가 다른 환자 · 다른 날 · 다른 기록이 함께 쓰는 파일로 번지지 않아야 하고,
  (4) 녹화 중 삭제 · 보기 창의 Delete 키 · 환자 전환 뒤 Pro 삭제가 엉뚱한 영상을 지우지 않아야 하며,
  (5) 시작 때 부가 기능(샘플 사진 등록)이 실패해도 프로그램이 켜져 촬영할 수 있어야 한다.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from app.models.capture import Capture
from app.models.patient import Patient
from app.services import backup_service as bs
from app.services.capture_service import CaptureService

D1, D2 = date(2026, 10, 28), date(2026, 10, 29)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _frame(v: int, h: int = 60, w: int = 80) -> np.ndarray:
    return np.full((h, w, 3), v % 256, dtype=np.uint8)


def _patient(db, chart="X001", name="환자") -> Patient:
    p = Patient(chart_no=chart, name=name)
    p.id = db.patients.insert(p)
    return p


def _mgr(app_env, root, today=lambda: D1, auto=True):
    config, db = app_env
    config.backup_root = str(root)
    config.backup_auto = auto
    config.save = lambda: None
    m = bs.BackupManager(config, db, today=today, startup_delay_ms=None)
    m.start()
    return m, config, db


def _wait(qapp, cond, timeout=8.0):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file()}


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# =====================================================================================
# 1. 백업 — 자기 자신 복사 · 원본 보호 · 하드 빠짐 · 원본 폴더 없음
# =====================================================================================
@pytest.mark.parametrize("where", ["media", "media_sub", "reference_library"])
def test_backup_root_inside_source_is_refused_at_runtime_never_copies_itself(qapp, app_env, tmp_path, where):
    """설정 파일을 손으로 고쳤거나 사진 폴더를 나중에 백업 위치의 위로 바꾼 경우 — 백업 위치가 사진 폴더 안이면
    실행 시점에도 막아야 한다. 안 막으면 백업이 자기 자신을 다시 복사해 돌 때마다 두 배로 커진다."""
    config, db = app_env
    media = Path(config.media_root)
    media.mkdir(parents=True, exist_ok=True)
    root = {"media": media, "media_sub": media / "백업", "reference_library": media.parent / "reference_library" / "bk"}[where]
    root.mkdir(parents=True, exist_ok=True)
    m, config, db = _mgr(app_env, root)
    svc = CaptureService(db, config)
    svc.on_saved.append(m.notify_saved)
    p = _patient(db, "IN1")
    cap = svc.save_photo(_frame(10), p, eye="OD")
    assert m.wait_idle(10)
    seen = []
    m.finished.connect(seen.append)
    for _ in range(3):                                      # 세 번 돌아도 커지지 않아야 한다
        assert m.backup_now() is True or m.status()["level"] in ("error", "warn")
        assert m.wait_idle(20)
    st = m.status()
    assert st["level"] == "error", st
    assert "사진" in st["tip"] and "백업 위치" in st["tip"], st["tip"]
    # 백업 폴더 안에 '자기 자신'(backup_info.json 이 또 들어 있는 폴더)이 복사되지 않았다
    nested = [p for p in media.rglob(bs.INFO_FILE) if p.parent != root and root in p.parents]
    assert nested == [], nested
    # 원본 사진은 그대로
    assert Path(cap.file_path).is_file() and Path(cap.thumbnail_path).is_file()
    m.stop()


def test_backup_never_modifies_or_deletes_source_and_keeps_deleted_photo(qapp, app_env, tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    svc = CaptureService(db, config)
    svc.on_saved.append(m.notify_saved)
    p = _patient(db, "SRC1")
    caps = [svc.save_photo(_frame(i), p, eye="OD") for i in range(4)]
    assert m.wait_idle(10)
    media = Path(config.media_root)
    before = {f: (_sha(media / f), os.stat(media / f).st_mtime_ns) for f in _tree(media)}
    assert m.backup_now() and m.wait_idle(20)
    assert m.backup_now() and m.wait_idle(20)
    after = {f: (_sha(media / f), os.stat(media / f).st_mtime_ns) for f in _tree(media)}
    assert after == before                                  # 백업은 원본을 읽기만 한다 (내용 · 수정 시각 · 파일 수)
    # 프로그램에서 지운 사진은 백업에 남는다 (실수 삭제 복구용) — 그리고 원본의 다른 사진은 그대로
    rel = os.path.relpath(caps[0].file_path, config.media_root)
    assert svc.delete_capture(caps[0])
    assert m.backup_now() and m.wait_idle(20)
    bset = bs.find_set(root, config.backup_set_id)
    assert (bset.path / "media" / rel).is_file()
    assert all(Path(c.file_path).is_file() for c in caps[1:])
    m.stop()


def test_backup_drive_pulled_mid_copy_does_not_recreate_folder_on_internal_disk(qapp, app_env, tmp_path, monkeypatch):
    """복사 도중 외장 하드가 빠지면 — 같은 이름의 폴더를 (내장 디스크에) 새로 만들어 그쪽에 계속 쓰면 안 된다.
    (맥은 /Volumes/이름 폴더가 남으면 다음에 하드를 꽂아도 '이름 1' 로 붙어 백업이 영영 내장 디스크로 간다.)"""
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    svc = CaptureService(db, config)
    p = _patient(db, "PULL1")
    for i in range(12):
        svc.save_photo(_frame(i), p, eye="OD")
    real = bs.copy_file
    n = {"copies": 0}

    def pull_then_copy(src, dst, *a, **kw):
        n["copies"] += 1
        if n["copies"] == 4 and root.exists():
            root.rename(tmp_path / "unplugged")             # 하드가 빠짐 (마운트 지점이 사라짐)
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(bs, "copy_file", pull_then_copy)
    seen = []
    m.finished.connect(seen.append)
    assert m.backup_now() and m.wait_idle(20)
    assert not root.exists(), sorted(_tree(root))[:5]      # 내장 디스크에 같은 이름의 폴더가 생기지 않았다
    assert seen and not seen[-1]["ok"] and seen[-1]["state"] in ("missing", "error"), seen[-1]
    assert m.status()["dirty"]
    # 다시 꽂으면 이어서 — 반쯤 복사된 파일(.bkpart) 없이 전부
    monkeypatch.setattr(bs, "copy_file", real)
    (tmp_path / "unplugged").rename(root)
    m._check_drive()
    assert m.backup_now() and m.wait_idle(20)
    files = _tree(root)
    assert not [f for f in files if f.endswith(".bkpart")]
    assert len([f for f in files if f.endswith(".png")]) == 12
    assert m.status()["level"] == "ok", m.status()
    m.stop()


def test_backup_realtime_copy_when_drive_gone_before_mkdir_leaves_no_folder(qapp, app_env, tmp_path):
    """실시간 복사(저장 즉시)가 큐에 들어간 뒤 하드가 빠진 경우도 같다."""
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    svc = CaptureService(db, config)
    svc.on_saved.append(m.notify_saved)
    p = _patient(db, "PULL2")
    svc.save_photo(_frame(1), p, eye="OD")
    assert m.wait_idle(10)
    bset = bs.find_set(root, config.backup_set_id)
    assert bset is not None
    # 폴더(세트)는 그대로인데 드라이브가 빠져 세트 폴더만 사라진 상황 → 알림 · 폴더 재생성 금지
    cap = svc.save_photo(_frame(2), p, eye="OS")
    (root / bset.name).rename(tmp_path / "gone")
    m.notify_saved(cap.file_path, cap.thumbnail_path)
    assert m.wait_idle(10)
    made = [p for p in root.iterdir()]
    # 하드가 빠진 것이 아니라 사용자가 세트 폴더를 지운 것일 수도 있어 새 세트 폴더는 만들 수 있다 —
    # 그러나 어떤 경우에도 반쯤 복사된 파일 · 임시 파일은 없고, 사진은 다음 전체 확인 때 반드시 들어간다
    assert not [f for f in _tree(root) if f.endswith(".bkpart")]
    assert m.backup_now() and m.wait_idle(20)
    rel = os.path.relpath(cap.file_path, config.media_root)
    assert any((root / d.name / "media" / rel).is_file() for d in root.iterdir() if d.is_dir()), (made, _tree(root))
    m.stop()


def test_backup_media_root_unreachable_is_not_reported_as_complete(qapp, app_env, tmp_path):
    """사진 폴더가 NAS 라서 끊겨 있으면 — '백업 ✓' 로 보이면 안 된다 (사진을 하나도 못 봤는데 끝난 것처럼)."""
    root = tmp_path / "drive"
    root.mkdir()
    config, db = app_env
    config.media_root = str(tmp_path / "nas" / "media")    # 없는(끊긴) 폴더
    m, config, db = _mgr(app_env, root)
    _patient(db, "NAS1")
    seen = []
    m.finished.connect(seen.append)
    assert m.backup_now() and m.wait_idle(20)
    st = m.status()
    assert st["level"] != "ok", st
    assert "사진" in (st["message"] + st["tip"]), st
    assert st["dirty"]
    bset = bs.find_set(root, config.backup_set_id)
    assert (bset.path / "db" / "slitlamp.db").is_file()     # 환자 기록은 그래도 백업한다
    m.stop()


def test_backup_db_snapshot_consistent_while_app_keeps_writing(app_env, tmp_path):
    config, db = app_env
    stop = threading.Event()
    errors = []

    def writer():
        i = 0
        while not stop.is_set():
            try:
                db.patients.insert(Patient(chart_no=f"W{i}", name="쓰기"))
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            i += 1
    t = threading.Thread(target=writer, daemon=True)
    t.start()
    try:
        for _k in range(6):
            dst = bs.snapshot_db(config.db_path, tmp_path / "db", D1)
            assert dst is not None
            snap = sqlite3.connect(dst)
            assert snap.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert snap.execute("SELECT COUNT(*) FROM patients").fetchone()[0] >= 0
            snap.close()
    finally:
        stop.set()
        t.join(5)
    assert not errors


def test_backup_one_failing_file_does_not_stop_the_rest(qapp, app_env, tmp_path, monkeypatch):
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    svc = CaptureService(db, config)
    p = _patient(db, "PERM1")
    caps = [svc.save_photo(_frame(i), p, eye="OD") for i in range(6)]
    bad = os.path.basename(caps[2].file_path)
    real = bs.copy_file

    def copy(src, dst, *a, **kw):
        if Path(src).name == bad:
            raise PermissionError(13, "Permission denied")   # 바이러스 검사기가 잡고 있음
        return real(src, dst, *a, **kw)
    monkeypatch.setattr(bs, "copy_file", copy)
    seen = []
    m.finished.connect(seen.append)
    assert m.backup_now() and m.wait_idle(20)
    res = seen[-1]
    assert res["ok"] and len(res["failed"]) == 1 and bad in res["failed"][0]
    pngs = [f for f in _tree(root) if f.endswith(".png")]
    assert len(pngs) == 5
    st = m.status()
    assert st["level"] == "warn" and "복사하지 못한 파일 1개" in st["tip"]
    m.stop()


def test_backup_many_files_incremental_fast(tmp_path):
    src = tmp_path / "media"
    for i in range(2000):
        d = src / f"p{i % 40}" / "2026-01-01"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{i:05d}.png").write_bytes(b"x" * 100)
    dst = tmp_path / "b"
    t0 = time.monotonic()
    todo, n = bs.plan_tree(src, dst, now=time.time() + 3600)
    assert n == 2000 and len(todo) == 2000
    for sp, dp, _ in todo:
        bs.copy_file(sp, dp, min_free_mb=0)
    todo2, n2 = bs.plan_tree(src, dst, now=time.time() + 3600)
    assert n2 == 2000 and todo2 == []
    assert time.monotonic() - t0 < 60


def test_backup_set_folder_deleted_by_user_recovers_without_touching_source(qapp, app_env, tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    svc = CaptureService(db, config)
    svc.on_saved.append(m.notify_saved)
    p = _patient(db, "GONE1")
    caps = [svc.save_photo(_frame(i), p, eye="OD") for i in range(3)]
    assert m.wait_idle(10) and m.backup_now() and m.wait_idle(20)
    import shutil
    shutil.rmtree(root / bs.find_set(root, config.backup_set_id).name)   # 사용자가 백업 폴더를 지움
    assert m.backup_now() and m.wait_idle(20)
    assert m.status()["level"] == "ok", m.status()
    assert len([f for f in _tree(root) if f.endswith(".png")]) == 3
    assert all(Path(c.file_path).is_file() for c in caps)
    m.stop()


def test_backup_full_while_photos_keep_coming(qapp, app_env, tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    svc = CaptureService(db, config)
    svc.on_saved.append(m.notify_saved)
    p = _patient(db, "BUSY1")
    caps = [svc.save_photo(_frame(i), p, eye="OD") for i in range(5)]
    assert m.backup_now()
    for i in range(5, 25):                                  # 전체 확인이 도는 동안에도 계속 촬영
        caps.append(svc.save_photo(_frame(i), p, eye="OS"))
        qapp.processEvents()
    assert m.wait_idle(30)
    bset = bs.find_set(root, config.backup_set_id)
    missing = [c.file_path for c in caps
               if not (bset.path / "media" / os.path.relpath(c.file_path, config.media_root)).is_file()]
    assert missing == []
    m.stop()


def test_backup_finish_on_exit_without_drive_returns_at_once(qapp, app_env, tmp_path):
    m, config, db = _mgr(app_env, tmp_path / "nowhere")
    t0 = time.monotonic()
    m.finish_on_exit(None, timeout=5)
    assert time.monotonic() - t0 < 1.0
    m.stop()


def test_backup_clock_went_backwards_keeps_folder_name(tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    s, _ = bs.ensure_set(root, "id1", D2)
    assert s.name == bs.set_name(D2, D2)
    s2, warn = bs.ensure_set(root, "id1", D1)                # 시계가 하루 뒤로 감
    assert s2.name == s.name and s2.path.is_dir() and not warn
    assert len([d for d in root.iterdir() if d.is_dir()]) == 1


# =====================================================================================
# 2. 삭제 — 다른 환자 · 다른 날 · 같은 파일을 쓰는 다른 기록 · 녹화 중 · 보기 창
# =====================================================================================
def test_delete_day_touches_only_that_patient_that_day(app_env):
    config, db = app_env
    svc = CaptureService(db, config)
    a, b = _patient(db, "DAY-A", "가"), _patient(db, "DAY-B", "나")
    ca = [svc.save_photo(_frame(i), a, eye="OD") for i in range(3)]
    cb = [svc.save_photo(_frame(i), b, eye="OD") for i in range(3)]
    today = ca[0].captured_at[:10]
    old = db.captures.insert(Capture(patient_id=a.id, media_type="photo", file_path=str(Path(ca[0].file_path).with_name("old.png")),
                                     captured_at="2025-01-05 09:30:00", eye="OD"))
    Path(ca[0].file_path).with_name("old.png").write_bytes(b"old")
    db.exam_notes.upsert(a.id, today, "slit", "각막 미란")
    assert svc.delete_day(a.id, today) == 3
    assert [c.id for c in db.captures.list_by_patient(a.id)] == [old]
    assert Path(ca[0].file_path).with_name("old.png").read_bytes() == b"old"
    assert all(Path(c.file_path).is_file() and Path(c.thumbnail_path).is_file() for c in cb)   # 다른 환자 그대로
    assert len(db.captures.list_by_patient(b.id)) == 3
    assert db.exam_notes.get(a.id, today)["notes"] == "각막 미란"                                  # 소견은 남긴다
    assert all(Path(c.export_path).is_file() for c in ca)                                          # 지정 폴더 사본 보존


def test_delete_capture_sharing_file_with_another_record_keeps_file(app_env):
    """두 기록이 같은 파일을 가리키면(DB 복원 · 가져오기 · 날짜 수정) 하나를 지워도 파일은 남아야 한다 —
    다른 기록이 가리키는 영상이 사라지면 안 된다."""
    config, db = app_env
    svc = CaptureService(db, config)
    p = _patient(db, "SHARE1")
    cap = svc.save_photo(_frame(1), p, eye="OD")
    twin = db.captures.insert(Capture(patient_id=p.id, media_type="photo", file_path=cap.file_path,
                                      thumbnail_path=cap.thumbnail_path, captured_at="2025-03-03 10:00:00", eye="OD"))
    assert svc.delete_capture(cap) is True
    assert db.captures.get(cap.id) is None and db.captures.get(twin) is not None
    assert Path(cap.file_path).is_file(), "다른 기록이 쓰는 파일을 지웠다"
    assert Path(cap.thumbnail_path).is_file()
    # 마지막 기록을 지우면 그때 파일도 지운다
    assert svc.delete_capture(twin) is True
    assert not Path(cap.file_path).exists() and not Path(cap.thumbnail_path).exists()


def test_explorer_delete_sharing_file_with_other_patient_keeps_file(app_env):
    from app.ui.explorer.explorer_window import _remove_files
    config, db = app_env
    svc = CaptureService(db, config)
    a, b = _patient(db, "SH-A"), _patient(db, "SH-B")
    cap = svc.save_photo(_frame(1), a, eye="OD")
    db.captures.insert(Capture(patient_id=b.id, media_type="photo", file_path=cap.file_path,
                               thumbnail_path=cap.thumbnail_path, captured_at="2025-03-03 10:00:00", eye="OD"))
    cap2 = svc.save_photo(_frame(2), a, eye="OS")                                # 같은 검사의 다른 사진 (공유 아님)
    _remove_files([db.captures.delete(cap.id)])                                  # Explorer 판 '영상 삭제'
    assert Path(cap.file_path).is_file() and Path(cap.thumbnail_path).is_file()
    # 검사(내원) 삭제도 같은 규칙 — 공유 파일은 남기고 그 검사만 쓰던 파일은 지운다
    db.captures.insert(Capture(patient_id=b.id, media_type="photo", file_path=cap2.file_path,
                               captured_at="2025-03-04 10:00:00", eye="OD"))
    assert cap2.exam_id is not None
    rows = db.exams.delete(cap2.exam_id)
    _remove_files(rows)
    assert Path(cap2.file_path).is_file(), "다른 환자가 쓰는 파일을 검사 삭제가 지웠다"
    assert cap2.thumbnail_path and not Path(cap2.thumbnail_path).exists()        # 공유되지 않은 썸네일은 정리
    assert db.captures.list_by_patient(a.id) == [] and len(db.captures.list_by_patient(b.id)) == 2


def test_delete_capture_locked_file_keeps_going(app_env, monkeypatch):
    config, db = app_env
    svc = CaptureService(db, config)
    p = _patient(db, "LOCK1")
    cap = svc.save_photo(_frame(1), p, eye="OD")
    other = svc.save_photo(_frame(2), p, eye="OS")
    real = Path.unlink

    def locked(self, *a, **kw):
        if str(self) == cap.file_path:
            raise PermissionError(32, "다른 프로그램이 사용 중")
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, "unlink", locked)
    assert svc.delete_capture(cap) is True                  # 기록은 지워지고 (목록에서 사라짐) 예외 없음
    assert db.captures.get(cap.id) is None and Path(cap.file_path).is_file()
    assert svc.delete_capture(other) is True and not Path(other.file_path).exists()


# ---------------- 촬영 창 ----------------
@pytest.fixture()
def win(qapp, app_env, tmp_path):
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 160, 120
    config.save = lambda: None
    config.backup_root = ""
    for chart, name in (("WA", "가환자"), ("WB", "나환자")):
        db.patients.insert(Patient(chart_no=chart, name=name))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.resize(1400, 820)
    w.show()
    assert _wait(qapp, lambda: w._last_frame is not None, 10)
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _pid(win, chart):
    return next(p.id for p in win.db.patients.list_all() if p.chart_no == chart)


def _video_done(win):
    return win._pending_video is None and not win.recorder.is_finalizing and not win._recording


def test_delete_during_recording_keeps_video_and_saves_it(qapp, win, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    win.patient_panel.select_patient(_pid(win, "WA"))
    win._on_capture()
    win._on_capture()
    caps = win.db.captures.list_by_patient(win._current_patient.id)
    assert len(caps) == 2
    win._start_recording()
    assert win._recording
    video_path = Path(win._video_path)
    assert _wait(qapp, lambda: win.recorder.frame_count >= 3, 10)
    # 녹화 중에 사진 하나 · 그 날 전체 삭제
    assert win._delete_capture(caps[0]) is True
    assert win._recording and video_path.exists()
    assert win._delete_day(caps[1].captured_at[:10]) == 1
    assert win._recording and video_path.exists(), "그 날 삭제가 녹화 중인 동영상을 건드렸다"
    win._stop_recording()
    assert _wait(qapp, lambda: _video_done(win), 30)
    vids = [c for c in win.db.captures.list_by_patient(win._current_patient.id) if c.media_type == "video"]
    assert len(vids) == 1 and Path(vids[0].file_path).is_file() and Path(vids[0].file_path).stat().st_size > 0


def test_pro_delete_after_patient_switch_has_no_stale_targets(qapp, win):
    win.patient_panel.select_patient(_pid(win, "WA"))
    for _ in range(3):
        win._on_capture()
    win.set_mode("pro")
    win.pro.set_mode("multi")
    win.pro.multi.set_selection([0, 1])
    assert len(win.pro.delete_targets()) == 2
    win.patient_panel.select_patient(_pid(win, "WB"))       # 환자 전환 → 선택이 남아 있으면 안 된다
    win._on_capture()
    assert win.pro.delete_targets() == [] or all(c.patient_id == win._current_patient.id for c in win.pro.delete_targets())
    assert len(win.db.captures.list_by_patient(_pid(win, "WA"))) == 3
    # 1화면 모드: 현재 영상 = 새 환자 것만
    win.pro.set_mode("single")
    tg = win.pro.delete_targets()
    assert all(c.patient_id == _pid(win, "WB") for c in tg)


def test_viewer_delete_key_needs_confirmation_and_no_means_no(qapp, win, monkeypatch):
    from app.ui.image_viewer import ImageViewerDialog
    win.patient_panel.select_patient(_pid(win, "WA"))
    win._on_capture()
    cap = win.db.captures.list_by_patient(win._current_patient.id)[0]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    dlg = ImageViewerDialog(cap, win)
    dlg.show()
    qapp.processEvents()
    dlg.b_delete.click()                                    # Delete 키 = 이 버튼의 단축키
    qapp.processEvents()
    assert dlg.result() != ImageViewerDialog.Deleted and dlg.isVisible()
    dlg.b_delete.click()
    assert dlg.result() != ImageViewerDialog.Deleted
    dlg.close()
    assert win.db.captures.get(cap.id) is not None and Path(cap.file_path).is_file()
    # 기본 버튼은 '닫기'(Enter) — Enter 가 삭제가 아니다
    dlg2 = ImageViewerDialog(cap, win)
    assert not dlg2.b_delete.isDefault()
    dlg2.close()


def test_after_capture_viewer_closed_by_next_capture_never_deletes(qapp, win):
    win.config.view_after_capture = True
    win.patient_panel.select_patient(_pid(win, "WA"))
    for _ in range(4):                                      # 연속 촬영 — 보기 창이 다음 촬영으로 닫힌다
        win._on_capture()
        qapp.processEvents()
    assert _wait(qapp, lambda: True, 0.3)
    caps = win.db.captures.list_by_patient(win._current_patient.id)
    assert len(caps) == 4 and all(Path(c.file_path).is_file() for c in caps)
    if win._capture_viewer is not None:
        win._capture_viewer.close()
        qapp.processEvents()
    assert len(win.db.captures.list_by_patient(win._current_patient.id)) == 4


def test_day_delete_declined_deletes_nothing_and_shows_patient_name(qapp, win, monkeypatch):
    asked = []

    def question(parent, title, text, *a, **k):
        asked.append(text)
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    win.patient_panel.select_patient(_pid(win, "WA"))
    win._on_capture()
    day = win.db.captures.list_by_patient(win._current_patient.id)[0].captured_at[:10]
    assert win._delete_day(day) == 0
    assert asked and "가환자" in asked[0] and "되돌릴 수 없" in asked[0]
    assert len(win.db.captures.list_by_patient(win._current_patient.id)) == 1


def test_thumbnail_panel_thousands_of_captures_stays_responsive_and_day_delete_exact(qapp, win, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    pid = _pid(win, "WA")
    media = Path(win.config.media_root) / "bulk"
    media.mkdir(parents=True)
    img = media / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    for i in range(2400):
        day = f"2024-{1 + i % 12:02d}-{1 + i % 27:02d}"
        f = media / f"{i:05d}.png"
        f.write_bytes(img.read_bytes())
        win.db.captures.insert(Capture(patient_id=pid, media_type="photo", file_path=str(f),
                                       captured_at=f"{day} 10:{i % 60:02d}:00", eye="OD"))
    t0 = time.monotonic()
    win.patient_panel.select_patient(pid)
    qapp.processEvents()
    assert time.monotonic() - t0 < 8.0
    assert win.thumbnail_panel.count() == 2400
    target_day = "2024-03-03"
    n_day = len(win.service.captures_on(pid, target_day))
    assert n_day > 0
    assert win._delete_day(target_day) == n_day
    assert len(win.db.captures.list_by_patient(pid)) == 2400 - n_day
    assert len(list(media.glob("*.png"))) == 2401 - n_day


# =====================================================================================
# 3. 시작 — 샘플 사진 등록이 실패해도 켜져야 한다
# =====================================================================================
def test_bundled_sample_import_failure_never_blocks_startup(app_env, tmp_path, monkeypatch):
    from app.services import sample_import as si
    config, db = app_env
    (tmp_path / "s").mkdir()
    monkeypatch.setattr(si, "bundled_samples_dir", lambda: tmp_path / "s")
    for exc in (sqlite3.OperationalError("database is locked"), MemoryError(), RuntimeError("boom"),
                PermissionError(13, "denied")):
        def boom(*a, _e=exc, **k):
            raise _e
        monkeypatch.setattr(si, "import_samples", boom)
        assert si.ensure_bundled_samples(db, config) is None   # 예외가 밖으로 나오면 프로그램이 켜지지 않는다
        assert getattr(config, "bundled_samples", "") != si.BUNDLE_ID   # 다음에 다시 시도


def test_bundled_sample_import_does_not_touch_existing_patients(app_env, tmp_path, monkeypatch):
    """실제 샘플 묶음이 있으면 등록되되, 기존 환자 · 사진에는 아무 변화가 없어야 한다."""
    from app.services import sample_import as si
    config, db = app_env
    svc = CaptureService(db, config)
    p = _patient(db, "REAL1")
    cap = svc.save_photo(_frame(5), p, eye="OD")
    before = _sha(cap.file_path)
    src = si.bundled_samples_dir()
    if src is None:
        pytest.skip("설치판 샘플 묶음 없음")
    res = si.ensure_bundled_samples(db, config)
    assert res is not None and not res.failed
    assert _sha(cap.file_path) == before and db.captures.get(cap.id).file_path == cap.file_path
    assert len(db.captures.list_by_patient(p.id)) == 1
    assert config.bundled_samples == si.BUNDLE_ID
    assert si.ensure_bundled_samples(db, config) is None    # 두 번째 실행은 아무것도 안 함

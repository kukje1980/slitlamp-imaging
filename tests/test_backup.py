"""백업 (외장 하드 · 다른 드라이브) — 폴더 이름(시작+마지막 날짜) · 증분 복사 · DB 사본 · 실시간 · 자동 · 수동 · 드라이브 빠짐.

실제 외장 하드 대신 임시 폴더를 백업 위치로 쓴다 (빠짐 = 폴더 이름 바꾸기 / 지우기).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from app.services import backup_service as bs

D1, D2, D3 = date(2026, 10, 28), date(2026, 10, 29), date(2026, 11, 3)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _old(path: Path, age_s: float = 3600) -> None:
    t = time.time() - age_s
    os.utime(path, (t, t))


# ---------------- 이름 · 세트 폴더 ----------------
def test_set_name_matches_example_and_parse():
    assert bs.set_name(date(2021, 9, 8), date(2026, 10, 28)) == "210908261028"     # 사용자 예시
    assert bs.parse_set_name("210908261028") == (date(2021, 9, 8), date(2026, 10, 28))
    assert bs.parse_set_name("210908261028-2") == (date(2021, 9, 8), date(2026, 10, 28))
    assert bs.parse_set_name("backup") is None and bs.parse_set_name("219999261028") is None


def test_ensure_set_creates_renames_daily_and_avoids_collision(tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    a, warn = bs.ensure_set(root, "A", D1, {"machine": "pc1"})
    assert a.name == "261028261028" and warn == "" and (a.path / bs.INFO_FILE).is_file()
    info = json.loads((a.path / bs.INFO_FILE).read_text(encoding="utf-8"))
    assert info["set_id"] == "A" and info["start"] == "2026-10-28" and info["machine"] == "pc1"
    assert bs.ensure_set(root, "A", D1)[0].name == "261028261028"                     # 같은 날은 그대로
    b, _ = bs.ensure_set(root, "B", D1)                                                 # 다른 PC 가 같은 날 시작
    assert b.name == "261028261028-2"
    a2, _ = bs.ensure_set(root, "A", D2)                                                # 다음 날 → 뒤 날짜가 바뀜
    assert a2.name == "261028261029" and not (root / "261028261028").exists()
    assert json.loads((a2.path / bs.INFO_FILE).read_text(encoding="utf-8"))["last"] == "2026-10-29"
    b2, _ = bs.ensure_set(root, "B", D2)
    assert b2.name == "261028261029-2"
    sets = bs.list_sets(root)
    assert [(s.name, s.set_id) for s in sets] == [("261028261029", "A"), ("261028261029-2", "B")]
    a3, _ = bs.ensure_set(root, "A", D1)                                                # 시계가 거꾸로 가도 줄지 않음
    assert a3.name == "261028261029"
    with pytest.raises(bs.DriveMissing):
        bs.ensure_set(tmp_path / "unplugged", "A", D1)


def test_rename_failure_keeps_backing_up_in_old_folder(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    root.mkdir()
    a, _ = bs.ensure_set(root, "A", D1)

    def locked(self, target):
        raise PermissionError(13, "다른 프로그램이 사용 중")
    monkeypatch.setattr(Path, "rename", locked)
    a2, warn = bs.ensure_set(root, "A", D2)
    assert a2.path == a.path and "바꾸지 못했습니다" in warn and "261028261029" in warn
    assert a2.last == D2                                                               # 마지막 날짜는 기록 → 다음에 다시 시도
    monkeypatch.undo()
    a3, warn = bs.ensure_set(root, "A", D2)
    assert a3.name == "261028261029" and warn == ""


# ---------------- 복사 ----------------
def test_plan_and_copy_incremental_skips_temp_busy_recent_video(tmp_path):
    src, dst = tmp_path / "media", tmp_path / "backup" / "media"
    (src / "P1" / "2026-10-28" / ".thumbs").mkdir(parents=True)
    photo = src / "P1" / "2026-10-28" / "101010_000_OD.png"
    photo.write_bytes(b"png" * 100)
    thumb = src / "P1" / "2026-10-28" / ".thumbs" / "101010_000_OD.jpg"
    thumb.write_bytes(b"jpg")
    video = src / "P1" / "2026-10-28" / "101500_000_OS.mp4"
    video.write_bytes(b"mp4" * 1000)
    rec = src / "P1" / "2026-10-28" / "102000_000_OD.mp4"                               # 녹화 중
    rec.write_bytes(b"x")
    (src / "P1" / "2026-10-28" / ".101010_000_OD.png.part").write_bytes(b"half")        # 쓰는 중인 임시 파일
    (src / "P1" / "Thumbs.db").write_bytes(b"junk")
    for p in (photo, thumb, rec):
        _old(p)
    todo, n = bs.plan_tree(src, dst, busy=[rec])
    names = sorted(sp.name for sp, *_ in todo)
    assert names == ["101010_000_OD.jpg", "101010_000_OD.png"]                           # 방금 바뀐 동영상 · 녹화 중 · 임시 제외
    todo, _ = bs.plan_tree(src, dst, busy=[rec], known=[video])                          # 저장이 끝났다고 알린 동영상은 포함
    assert "101500_000_OS.mp4" in [sp.name for sp, *_ in todo]
    for sp, dp, _size in todo:
        bs.copy_file(sp, dp)
    assert (dst / "P1" / "2026-10-28" / ".thumbs" / "101010_000_OD.jpg").read_bytes() == b"jpg"
    assert not list(dst.rglob("*.bkpart")) and not list(dst.rglob("*.part"))
    again, _ = bs.plan_tree(src, dst, busy=[rec], known=[video])
    assert again == []                                                                  # 두 번째는 복사할 것 없음
    photo.write_bytes(b"changed")                                                       # 바뀐 파일만 다시
    _old(photo, 60)
    again, _ = bs.plan_tree(src, dst, busy=[rec], known=[video])
    assert [sp.name for sp, *_ in again] == ["101010_000_OD.png"]
    photo.unlink()                                                                      # 원본에서 지워도 백업은 남김
    bs.plan_tree(src, dst)
    assert (dst / "P1" / "2026-10-28" / "101010_000_OD.png").is_file()


def test_copy_file_stops_when_backup_drive_full(tmp_path, monkeypatch):
    src = tmp_path / "a.png"
    src.write_bytes(b"0" * 1000)
    dst = tmp_path / "b" / "a.png"
    monkeypatch.setattr(bs.shutil, "disk_usage", lambda p: type("U", (), {"free": 10 * 1024 * 1024})())
    with pytest.raises(bs.BackupDiskFull):
        bs.copy_file(src, dst)
    assert not dst.exists() and not list(dst.parent.glob("*.bkpart"))


def test_snapshot_db_live_copy_and_daily_history(tmp_path):
    db = tmp_path / "slitlamp.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (v)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()                                                                         # 연결을 연 채로 (앱이 쓰는 중)
    out = bs.snapshot_db(db, tmp_path / "set" / "db", D1, keep=2)
    assert sqlite3.connect(out).execute("SELECT v FROM t").fetchall() == [(1,)]
    for d in (D2, D3):
        bs.snapshot_db(db, tmp_path / "set" / "db", d, keep=2)
    hist = sorted(p.name for p in (tmp_path / "set" / "db" / "history").iterdir())
    assert hist == ["slitlamp-20261029.db", "slitlamp-20261103.db"]                       # 날짜별 · 최근 keep 개
    con.close()
    assert bs.snapshot_db(tmp_path / "none.db", tmp_path / "set" / "db", D1) is None


def test_describe_target_same_drive_and_inside_media(tmp_path):
    media = tmp_path / "data" / "media"
    media.mkdir(parents=True)
    assert bs.describe_target("", str(media))[0] == "unset"
    assert bs.describe_target(str(media / "backup"), str(media))[0] == "error"
    assert bs.describe_target(str(tmp_path / "unplugged"), str(media))[0] == "missing"
    (tmp_path / "bk").mkdir()
    assert bs.describe_target(str(tmp_path / "data" / "reference_library" / "bk"), str(media))[0] == "error"
    level, text = bs.describe_target(str(tmp_path / "bk"), str(media))
    assert level == "warn" and "같은 드라이브" in text                                   # 임시 폴더는 같은 디스크
    a, _ = bs.ensure_set(tmp_path / "bk", "A", D1)
    level, text = bs.describe_target(str(a.path), str(media))                            # 날짜 폴더 자체를 고름
    assert level == "error" and "백업 폴더 자체" in text
    (a.path / "media").mkdir()
    assert bs.describe_target(str(a.path / "media"), str(media))[0] == "error"            # 그 안의 폴더도


def test_copy_cancel_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "CHUNK", 1024)
    src = tmp_path / "big.mp4"
    src.write_bytes(os.urandom(64 * 1024))
    dst = tmp_path / "b" / "big.mp4"
    calls = []

    def cancel():
        calls.append(1)
        return len(calls) > 5                                                            # 5 KB 쯤에서 중지
    with pytest.raises(bs.Cancelled):
        bs.copy_file(src, dst, min_free_mb=0, cancel=cancel)
    assert not dst.exists() and not list(dst.parent.iterdir())                           # 반쯤 복사된 파일 없음
    assert bs.copy_file(src, dst, min_free_mb=0) == 64 * 1024 and dst.read_bytes() == src.read_bytes()
    assert abs(dst.stat().st_mtime - src.stat().st_mtime) < 0.01                         # 수정 시각 보존 → 다음엔 건너뜀


# ---------------- BackupManager ----------------
def _mgr(app_env, root, today=lambda: D1, auto=True):
    config, db = app_env
    config.backup_root = str(root)
    config.backup_auto = auto
    config.save = lambda: None
    m = bs.BackupManager(config, db, today=today, startup_delay_ms=None)
    m.start()
    return m, config, db


def _save(config, db):
    from app.models.patient import Patient
    from app.services.capture_service import CaptureService
    svc = CaptureService(db, config)
    pid = db.patients.insert(Patient(chart_no="BK1", name="백업"))
    p = db.patients.get(pid)
    return svc, p


def test_manager_realtime_copy_full_backup_and_status(qapp, app_env, tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    type(config).save(config)                                                            # config.json
    svc, p = _save(config, db)
    svc.on_saved.append(m.notify_saved)
    seen = []
    m.finished.connect(seen.append)
    cap = svc.save_photo(np.full((60, 80, 3), 120, np.uint8), p, eye="OD")
    assert m.wait_idle(10)
    folder = root / "261028261028"
    rel = os.path.relpath(cap.file_path, config.media_root)
    assert (folder / "media" / rel).is_file()                                            # 저장 즉시 복사 (실시간)
    assert (folder / "media" / os.path.relpath(cap.thumbnail_path, config.media_root)).is_file()
    assert seen and seen[-1]["kind"] == "files" and seen[-1]["ok"]
    assert m.backup_now()                                                                # 전체 (수동 '지금 백업')
    assert m.wait_idle(10)
    res = seen[-1]
    assert res["kind"] == "full" and res["ok"] and res["copied"] == 0                     # 이미 복사된 것은 건너뜀
    snap = sqlite3.connect(folder / "db" / "slitlamp.db")
    assert snap.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 1               # 환자 기록 DB 사본
    snap.close()
    assert (folder / "db" / "history" / "slitlamp-20261028.db").is_file()
    assert (folder / bs.README_FILE).read_text(encoding="utf-8-sig").startswith("슬릿램프 영상 시스템 백업 — 261028261028")
    assert (folder / "config" / "config.json").is_file()
    st = m.status()
    assert st["level"] == "ok" and st["text"].startswith("백업 ✓") and config.backup_last_at
    m.stop()


def test_manager_drive_missing_then_reconnect_catches_up(qapp, app_env, tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    svc, p = _save(config, db)
    svc.on_saved.append(m.notify_saved)
    root.rename(tmp_path / "unplugged")                                                  # 외장 하드 빠짐
    cap = svc.save_photo(np.full((60, 80, 3), 50, np.uint8), p, eye="OS")
    assert m.wait_idle(10)
    st = m.status()
    assert st["state"] == "missing" and st["text"] == "백업 드라이브 없음" and st["dirty"]
    (tmp_path / "unplugged").rename(root)                                                # 다시 꽂음
    m._check_drive()                                                                     # (20초마다 보는 것을 바로)
    assert m.wait_idle(10)
    assert (root / "261028261028" / "media" / os.path.relpath(cap.file_path, config.media_root)).is_file()
    assert m.status()["state"] == "ok" and not m.status()["dirty"]
    m.stop()


def test_manager_manual_mode_new_set_and_next_day(qapp, app_env, tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    day = {"d": D1}
    m, config, db = _mgr(app_env, root, today=lambda: day["d"], auto=False)
    svc, p = _save(config, db)
    svc.on_saved.append(m.notify_saved)
    cap = svc.save_photo(np.full((60, 80, 3), 90, np.uint8), p)
    assert m.wait_idle(5)
    assert not list(root.iterdir())                                                      # 수동: 저장해도 복사 안 함
    assert m.status()["dirty"] and "수동" in m.status()["text"]
    assert m.backup_now() and m.wait_idle(10)
    folder = root / "261028261028"
    assert (folder / "media" / os.path.relpath(cap.file_path, config.media_root)).is_file()
    day["d"] = D3                                                                        # 며칠 뒤 백업 → 이름의 뒤 날짜만 바뀜
    assert m.backup_now() and m.wait_idle(10)
    assert (root / "261028261103").is_dir() and not folder.exists()
    old_id = config.backup_set_id
    assert m.new_set() and m.wait_idle(10)                                              # 새 백업 폴더 시작
    assert config.backup_set_id != old_id
    new = root / "261103261103"
    assert new.is_dir() and (new / "media" / os.path.relpath(cap.file_path, config.media_root)).is_file()
    assert (root / "261028261103").is_dir()                                              # 예전 폴더는 그대로
    m.stop()


def test_manager_cancel_and_db_change_watch(qapp, app_env, tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    media = Path(config.media_root) / "P" / "d"
    media.mkdir(parents=True)
    for i in range(30):
        f = media / f"{i:03d}.png"
        f.write_bytes(b"1" * 2000)
        _old(f)
    m.cancel()
    m._cancel.set()
    job = m._job("full", reason="test")
    m._q.put(job)                                                                        # 중지 표시가 켜진 채 시작 → 바로 멈춤
    assert m.wait_idle(10)
    assert m.status()["dirty"] and "중지" in m.status()["message"]
    assert m.backup_now() and m.wait_idle(10)                                           # 다시 누르면 이어서 전부
    assert len(list((root / "261028261028" / "media").rglob("*.png"))) == 30
    from app.models.patient import Patient
    m._db_seen = db.change_count()
    db.patients.insert(Patient(chart_no="W1", name="기록"))                               # 환자 정보만 바뀜 → DB 만 다시
    seen = []
    m.finished.connect(seen.append)
    m._check_db()
    assert m.wait_idle(10) and seen and seen[-1]["kind"] == "db" and seen[-1]["ok"]
    snap = sqlite3.connect(root / "261028261028" / "db" / "slitlamp.db")
    assert snap.execute("SELECT COUNT(*) FROM patients WHERE chart_no='W1'").fetchone()[0] == 1
    snap.close()
    m.stop()


# ---------------- 촬영 창 · 설정 탭 ----------------
@pytest.fixture()
def win(qapp, app_env, tmp_path, monkeypatch):
    from app.models.patient import Patient
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 160, 120
    config.save = lambda: None
    (tmp_path / "drive").mkdir()
    config.backup_root = str(tmp_path / "drive")
    db.patients.insert(Patient(chart_no="BW1", name="백업창"))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.resize(1400, 820)
    w.show()
    end = time.time() + 10
    while time.time() < end and w._last_frame is None:
        qapp.processEvents()
        time.sleep(0.02)
    yield w
    w.close()
    w.deleteLater()
    qapp.processEvents()


def test_main_window_capture_backed_up_and_status_button(qapp, win, tmp_path):
    from datetime import date as _date
    folder = tmp_path / "drive" / bs.set_name(_date.today(), _date.today())
    win.patient_panel.select_patient(next(p.id for p in win.db.patients.list_all() if p.chart_no == "BW1"))
    win._on_capture()
    assert win.backup.wait_idle(10)
    cap = win.db.captures.list_by_patient(win._current_patient.id)[0]
    assert (folder / "media" / os.path.relpath(cap.file_path, win.config.media_root)).is_file()
    assert win.btn_backup.text().startswith("백업 ✓") and win.btn_backup.isVisible()
    assert win.btn_backup.property("level") == "ok"


def test_settings_backup_tab_values_and_validation(qapp, win, tmp_path, monkeypatch):
    from app.ui import settings_dialog as sd
    from app.ui.settings_dialog import SettingsDialog
    warned = []
    monkeypatch.setattr(sd.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    dlg = SettingsDialog(win.config, win)
    tab = dlg.backup_tab
    assert dlg.tabs.tabText(dlg.tabs.indexOf(tab)) == "백업" and tab.manager is win.backup
    assert tab.root() == win.config.backup_root and tab.chk_auto.isChecked() and tab.chk_exit.isChecked()
    from PySide6.QtWidgets import QLabel
    assert any("210908261028" in lb.text() for lb in tab.findChildren(QLabel))           # 이름 규칙 예시
    tab.e_root.setText(str(Path(win.config.media_root) / "bk"))                            # 사진 폴더 안 → 막음
    dlg.accept()
    assert dlg.result() == 0 and warned and "저장 폴더 안" in warned[-1]
    other = tmp_path / "drive2"
    other.mkdir()
    tab.e_root.setText(str(other))
    tab.c_interval.setCurrentIndex(tab.c_interval.findData(240))
    tab.chk_exit.setChecked(False)
    assert tab.problem() is None and tab.b_now.isEnabled() and "같은 드라이브" in tab.lbl_drive.text()
    dlg.apply_to_config()
    assert (win.config.backup_root, win.config.backup_interval_min, win.config.backup_on_exit) == (str(other), 240, False)
    win.backup.reconfigure()                                                              # 위치를 바꾸면 바로 전체 백업
    assert win.backup.wait_idle(10)
    from datetime import date as _date
    assert (other / bs.set_name(_date.today(), _date.today()) / "db" / "slitlamp.db").is_file()
    tab.refresh()
    assert tab.list_sets.count() == 1 and "이 PC" in tab.list_sets.item(0).text()
    dlg.close()
    calls = []
    monkeypatch.setattr(tab, "refresh", lambda: calls.append(1))
    win.backup._emit_status()                                                            # 닫힌 설정 창은 디스크를 읽지 않음
    win.backup.progress.emit(1.0, 2.0, "복사 중")
    assert calls == []


def test_close_finishes_db_backup(qapp, app_env, tmp_path):
    from app.models.patient import Patient
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 160, 120
    config.save = lambda: None
    root = tmp_path / "drive"
    root.mkdir()
    config.backup_root = str(root)
    db.patients.insert(Patient(chart_no="BX1", name="닫기"))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.show()
    qapp.processEvents()
    db.patients.insert(Patient(chart_no="BX2", name="닫기 직전"))                         # 닫기 직전에 바뀐 기록
    w.close()
    from datetime import date as _date
    snap = root / bs.set_name(_date.today(), _date.today()) / "db" / "slitlamp.db"
    assert snap.is_file()
    con = sqlite3.connect(snap)
    assert con.execute("SELECT COUNT(*) FROM patients WHERE chart_no='BX2'").fetchone()[0] == 1
    con.close()
    assert w.backup._thread is None                                                      # 작업 스레드도 멈춤
    w.deleteLater()


def test_stop_during_big_copy_returns_quickly_without_partial_file(qapp, app_env, tmp_path, monkeypatch):
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    big = Path(config.media_root) / "P" / "d" / "big.mp4"
    big.parent.mkdir(parents=True)
    big.write_bytes(os.urandom(4 * 1024 * 1024))
    _old(big)
    monkeypatch.setattr(bs, "CHUNK", 8)                                                  # 아주 느린 복사 (큰 동영상 대신)
    assert m.backup_now()
    end = time.time() + 10
    while time.time() < end and not list(root.rglob(".big.mp4.bkpart")):
        qapp.processEvents()
        time.sleep(0.005)
    assert list(root.rglob(".big.mp4.bkpart")) and m.is_running()                        # 복사하는 중에
    t0 = time.monotonic()
    m.stop()                                                                             # 프로그램 닫기
    assert time.monotonic() - t0 < 3 and m._thread is None
    assert not list(root.rglob("*.bkpart")) and not list(root.rglob("big.mp4"))           # 반쯤 복사된 파일 없음


def test_deleted_before_copy_is_not_a_failure_and_manual_exit_backs_up(qapp, app_env, tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    m, config, db = _mgr(app_env, root)
    seen = []
    m.finished.connect(seen.append)
    gone = Path(config.media_root) / "P" / "d" / "gone.png"
    m.notify_saved(str(gone))                                                            # 저장 직후 지운 사진
    assert m.wait_idle(10) and seen[-1]["ok"] and seen[-1]["failed"] == []
    assert m.status()["level"] == "ok"
    config.backup_auto = False                                                           # 수동 + 닫을 때 마무리
    svc, p = _save(config, db)
    svc.on_saved.append(m.notify_saved)
    cap = svc.save_photo(np.full((60, 80, 3), 10, np.uint8), p)
    assert m.wait_idle(5) and m.status()["dirty"]
    m.finish_on_exit(None, timeout=20)
    assert (root / "261028261028" / "media" / os.path.relpath(cap.file_path, config.media_root)).is_file()
    assert not m.status()["dirty"]
    config.backup_on_exit = False                                                        # 끄면 닫을 때 아무것도 안 함
    n = len(seen)
    m.finish_on_exit(None, timeout=5)
    assert len(seen) == n
    m.stop()


def test_explorer_window_backs_up_capture_and_menu(qapp, app_env, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from app.models.patient import Patient
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 160, 120
    config.save = lambda: None
    db.patients.insert(Patient(chart_no="EX1", name="탐색기 백업"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    from app.ui.explorer.explorer_window import ExplorerWindow
    w = ExplorerWindow(config, db)
    w.show()
    try:
        end = time.time() + 10
        while time.time() < end and w.camera.last_frame is None:
            qapp.processEvents()
            time.sleep(0.02)
        opened = []
        monkeypatch.setattr(w, "open_settings", lambda tab=None: opened.append(tab))
        assert w.backup_to_drive() is False and opened == ["백업"]                        # 위치를 안 정했으면 설정 → 백업
        assert w.btn_backup.text() == "백업 설정 안 됨"
        (tmp_path / "drive").mkdir()
        config.backup_root = str(tmp_path / "drive")
        w.backup.reconfigure()
        assert w.backup.wait_idle(10)
        cap = w.capture()
        assert w.backup.wait_idle(10)
        from datetime import date as _date
        folder = tmp_path / "drive" / bs.set_name(_date.today(), _date.today())
        assert (folder / "media" / os.path.relpath(cap.file_path, config.media_root)).is_file()
        assert w.backup_to_drive() is True and w.backup.wait_idle(10)
        assert w.btn_backup.text().startswith("백업 ✓")
        assert w.act_backup_drive.text() == "외장 하드로 지금 백업"
    finally:
        w.close()
        w.deleteLater()
        qapp.processEvents()

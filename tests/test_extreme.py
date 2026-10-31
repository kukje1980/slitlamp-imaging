"""극한 상황 테스트 — 어떤 경우에도 환자 영상이 사라지거나, 덮어써지거나, 다른 환자와 섞이지 않아야 한다.

같은 밀리초 연속 촬영 · 여러 스레드 동시 저장 · 디스크 가득 참 · 내보내기 폴더 끊김 · 한글/아주 긴/악의적인
환자 정보 · 이상한 프레임 · 시계 변경 · 인코더 멈춤 · 강제 종료(전원 차단) · 손상된 DB/설정 파일을 다룬다.
"""
from __future__ import annotations

import errno
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import paths
from app.config import AppConfig
from app.db.database import Database
from app.db.database import DatabaseError as AppDatabaseError
from app.models.capture import Capture
from app.models.patient import Patient
from app.services import capture_service as cs_mod
from app.services.capture_service import CaptureService

ROOT = Path(__file__).resolve().parents[1]


def _frame(v: int, h: int = 60, w: int = 80) -> np.ndarray:
    return np.full((h, w, 3), v % 256, dtype=np.uint8)


def _patient(db, chart="X001", pid=None, name="환자") -> Patient:
    p = Patient(chart_no=chart, patient_id=pid, name=name)
    p.id = db.patients.insert(p)
    return p


def _read(path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    assert img is not None, f"읽을 수 없는 영상 파일: {path}"
    return img


class _FrozenClock:
    """datetime.now() 가 항상 같은 순간을 돌려준다 (같은 밀리초 연속 촬영 / 시계 해상도가 낮은 PC)."""

    fixed = datetime(2026, 1, 2, 3, 4, 5, 678000)

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


def _fail_writes_under(monkeypatch, root, err):
    """root 아래에 쓰는 파일은 반쯤 쓰다가 OSError(err) — 디스크 가득 참 / 네트워크 드라이브 끊김."""
    import builtins
    real_open = builtins.open

    class _Dying:
        def __init__(self, f):
            self._f = f

        def write(self, b):
            self._f.write(b[: len(b) // 2])
            raise OSError(err, os.strerror(err))

        def __getattr__(self, n):
            return getattr(self._f, n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._f.close()

    def dying_open(file, mode="r", *a, **k):
        f = real_open(file, mode, *a, **k)
        if "b" in mode and ("w" in mode or "x" in mode) and str(file).startswith(str(root)):
            return _Dying(f)
        return f

    monkeypatch.setattr(builtins, "open", dying_open)


def _inside(child, root) -> bool:
    child, root = Path(child).resolve(), Path(root).resolve()
    return child == root or root in child.parents


# ============================================================ 저장: 덮어쓰기·동시성

def test_burst_capture_same_millisecond_never_overwrites(app_env, monkeypatch):
    """같은 밀리초에 여러 장(4분할 저장·연타·시계 해상도 15ms PC) — 앞 사진이 덮어써지면 안 된다."""
    config, db = app_env
    monkeypatch.setattr(cs_mod, "datetime", _FrozenClock)
    service = CaptureService(db, config)
    p = _patient(db)

    caps = [service.save_photo(_frame(i * 20), p, eye="OD") for i in range(10)]

    files = [c.file_path for c in caps]
    assert len(set(files)) == 10, "같은 파일 이름에 덮어씀"
    exports = [c.export_path for c in caps]
    assert None not in exports and len(set(exports)) == 10
    thumbs = [c.thumbnail_path for c in caps]
    assert len(set(thumbs)) == 10
    for i, c in enumerate(caps):                 # 각 파일에 그 순간의 영상이 그대로 남아 있어야 한다
        assert int(_read(c.file_path)[0, 0, 0]) == (i * 20) % 256
        assert int(_read(c.export_path)[0, 0, 0]) == (i * 20) % 256
    rows = db.captures.list_by_patient(p.id)
    assert sorted(r.file_path for r in rows) == sorted(files)


def test_concurrent_saves_from_many_threads(app_env, monkeypatch):
    """여러 스레드가 같은 순간에 저장 — 파일·DB 모두 하나도 빠지거나 겹치지 않는다."""
    config, db = app_env
    monkeypatch.setattr(cs_mod, "datetime", _FrozenClock)
    service = CaptureService(db, config)
    p = _patient(db)
    errors, caps, lock = [], [], threading.Lock()
    start = threading.Barrier(8)

    def run(k):
        start.wait()
        for j in range(15):
            try:
                c = service.save_photo(_frame(k * 16 + j), p, eye="OS")
                with lock:
                    caps.append((k * 16 + j, c))
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

    ts = [threading.Thread(target=run, args=(k,)) for k in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert not errors, errors[:3]
    assert len(caps) == 120
    assert len({c.file_path for _, c in caps}) == 120
    for v, c in caps:
        assert int(_read(c.file_path)[0, 0, 0]) == v % 256, "다른 스레드의 영상으로 덮어써짐"
    assert len(db.captures.list_by_patient(p.id)) == 120
    with db._lock:
        assert db._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_two_patients_same_folder_name_do_not_mix(app_env, monkeypatch):
    """폴더 이름이 같아지는 두 환자(특수문자만 다른 차트번호)라도 서로의 영상을 덮어쓰지 않는다."""
    config, db = app_env
    monkeypatch.setattr(cs_mod, "datetime", _FrozenClock)
    service = CaptureService(db, config)
    a = _patient(db, chart="A/1", pid="P")
    b = _patient(db, chart="A:1", pid="P")       # 둘 다 'A_1_P' 폴더
    ca = service.save_photo(_frame(11), a, eye="OD")
    cb = service.save_photo(_frame(222), b, eye="OD")
    assert ca.file_path != cb.file_path
    assert int(_read(ca.file_path)[0, 0, 0]) == 11
    assert int(_read(cb.file_path)[0, 0, 0]) == 222
    assert [c.patient_id for c in db.captures.list_by_patient(a.id)] == [a.id]
    assert [c.patient_id for c in db.captures.list_by_patient(b.id)] == [b.id]


def test_video_path_is_unique_too(app_env, monkeypatch):
    config, db = app_env
    monkeypatch.setattr(cs_mod, "datetime", _FrozenClock)
    service = CaptureService(db, config)
    p = _patient(db)
    p1, _ = service.begin_video(p, eye="OD")
    p1.write_bytes(b"first")                     # 녹화기가 이미 쓰고 있는 파일
    p2, _ = service.begin_video(p, eye="OD")
    assert p1 != p2


# ============================================================ 저장: 경로·이름

EVIL = ["../../../../etc", "..", ".", "/abs/olute", "C:\\Windows\\System32", "\\\\server\\share\\x",
        "a/../../b", "x\x00y", "con", "NUL", "COM1", "lpt9", "abc. ", "   ", "\u202egnp.exe", "😀👁",
        "a" * 400, "가" * 300, "%s%n{0}{chart_no}", "$(rm -rf ~)", "'; DROP TABLE patients;--"]


@pytest.mark.parametrize("evil", EVIL)
def test_hostile_patient_fields_stay_inside_storage(app_env, evil):
    """환자 정보에 무엇이 들어 와도 저장 위치를 벗어나지 않고, 저장은 성공해야 한다."""
    config, db = app_env
    config.subfolder_template = "{chart_no}/{patient_id}/{name}/{date}"
    service = CaptureService(db, config)
    p = Patient(chart_no=evil, patient_id=evil, name=evil)
    p.id = db.patients.insert(p)

    cap = service.save_photo(_frame(77), p, eye="OD")

    assert _inside(cap.file_path, config.media_root)
    assert cap.export_path and _inside(cap.export_path, config.export_root)
    assert int(_read(cap.file_path)[0, 0, 0]) == 77
    for part in Path(cap.file_path).relative_to(config.media_root).parts:
        assert len(part.encode("utf-8")) <= 200, "폴더/파일 이름이 너무 길어 OS가 거부할 수 있음"
        assert part not in (".", "..") and not part.endswith((".", " "))
        assert part.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT9"}
    assert db.patients.get(p.id).name == evil   # DB 에는 입력한 그대로 보관


@pytest.mark.parametrize("evil", ["가" * 300, "a" * 400, "😀" * 200])
def test_long_names_with_default_template(app_env, evil):
    """기본 폴더 규칙(차트번호_환자ID)에서 둘 다 길어도 이름 한 칸이 OS 한도(255바이트)를 넘지 않는다."""
    config, db = app_env
    service = CaptureService(db, config)
    cap = service.save_photo(_frame(8), _patient(db, chart=evil, pid=evil, name=evil))
    for part in Path(cap.file_path).relative_to(config.media_root).parts:
        assert len(part.encode("utf-8")) <= 200


def test_hostile_template_cannot_escape(app_env):
    config, db = app_env
    p = _patient(db)
    service = CaptureService(db, config)
    for tpl in ("../../{chart_no}", "/etc/{date}", "..\\..\\{chart_no}", "C:\\x\\{date}", "{date}/../../.."):
        config.subfolder_template = tpl
        cap = service.save_photo(_frame(1), p)
        assert _inside(cap.file_path, config.media_root), (tpl, cap.file_path)


def test_bad_template_falls_back_instead_of_failing(app_env):
    """설정의 폴더 규칙이 잘못돼도(오타 토큰) 촬영은 저장되어야 한다."""
    config, db = app_env
    config.subfolder_template = "{chart}_{oops}/{date"
    service = CaptureService(db, config)
    cap = service.save_photo(_frame(5), _patient(db))
    assert Path(cap.file_path).exists() and _inside(cap.file_path, config.media_root)


def test_korean_storage_path_does_not_depend_on_cv2_file_io(app_env, monkeypatch):
    """윈도우의 OpenCV 는 한글 경로(예: C:\\Users\\홍길동)에 imwrite 하지 못한다 — 그래도 저장되어야 한다."""
    config, db = app_env
    base = Path(config.media_root).parent
    config.media_root = str(base / "영상 저장소" / "홍길동 원장")
    config.export_root = str(base / "내보내기 폴더")
    real = cv2.imwrite

    def windows_like_imwrite(path, *a, **k):
        if any(ord(ch) > 127 for ch in str(path)):
            return False                         # 윈도우에서 실제로 일어나는 일
        return real(path, *a, **k)

    monkeypatch.setattr(cs_mod.cv2, "imwrite", windows_like_imwrite)
    service = CaptureService(db, config)
    p = _patient(db, name="홍길동")
    for fmt in ("png", "jpg"):
        config.photo_format = fmt
        cap = service.save_photo(_frame(90), p, eye="OS")
        assert abs(int(_read(cap.file_path)[0, 0, 0]) - 90) <= 2
        assert cap.thumbnail_path and Path(cap.thumbnail_path).exists()
        assert cap.export_path and Path(cap.export_path).exists()


# ============================================================ 저장: 디스크·폴더 장애

def test_export_drive_failures_never_block_primary_save(app_env, monkeypatch):
    config, db = app_env
    service = CaptureService(db, config)
    p = _patient(db)
    blocker = Path(config.export_root).parent / "export_is_a_file"
    blocker.write_text("x")
    for root in (str(blocker), str(blocker / "sub")):     # 폴더 자리에 파일 / 그 아래
        config.export_root = root
        cap = service.save_photo(_frame(3), p)
        assert Path(cap.file_path).exists() and cap.export_path is None and cap.id

    # 복사 도중 네트워크 드라이브(NAS)가 끊김 — 반쯤 복사된 파일이 '정상 사본'처럼 남으면 안 된다
    config.export_root = str(Path(config.media_root).parent / "nas")
    _fail_writes_under(monkeypatch, config.export_root, errno.EHOSTDOWN)
    cap = service.save_photo(_frame(4), p)
    monkeypatch.undo()
    assert Path(cap.file_path).exists() and cap.export_path is None
    assert not [f for f in Path(config.export_root).rglob("*") if f.is_file()], "반쯤 복사된 내보내기 파일이 남음"
    assert len(db.captures.list_by_patient(p.id)) == 3


def test_disk_full_reports_error_and_leaves_nothing_half_written(app_env, monkeypatch):
    """기본 저장소가 가득 차면 '저장됨'으로 보이면 안 된다 — 오류를 알리고, 깨진 파일·DB 기록을 남기지 않는다."""
    config, db = app_env
    service = CaptureService(db, config)
    p = _patient(db)
    _fail_writes_under(monkeypatch, config.media_root, errno.ENOSPC)
    monkeypatch.setattr(cs_mod.cv2, "imwrite", lambda *a, **k: False)   # 직접 쓰기 경로도 실패
    with pytest.raises(Exception):
        service.save_photo(_frame(9), p)
    monkeypatch.undo()
    assert db.captures.list_by_patient(p.id) == []
    leftovers = [f for f in Path(config.media_root).rglob("*") if f.is_file() and ".thumbs" not in f.parts]
    assert leftovers == [], f"깨진 파일이 남음: {leftovers}"


def test_db_failure_after_file_write_keeps_the_image(app_env, monkeypatch):
    """DB 가 잠겨 기록이 실패해도 찍은 영상 파일은 지우지 않는다 (나중에 복구 가능) — 오류는 알린다."""
    config, db = app_env
    service = CaptureService(db, config)
    p = _patient(db)

    def locked(_):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db.captures, "insert", locked)
    with pytest.raises(sqlite3.OperationalError):
        service.save_photo(_frame(33), p)
    files = [f for f in Path(config.media_root).rglob("*.png") if ".thumbs" not in f.parts]
    assert len(files) == 1 and int(_read(files[0])[0, 0, 0]) == 33


# ============================================================ 이상한 프레임

@pytest.mark.parametrize("frame", [
    np.zeros((1, 1, 3), np.uint8),
    np.zeros((1, 4000, 3), np.uint8),
    np.zeros((4320, 7680, 3), np.uint8),                         # 8K
    np.random.randint(0, 255, (480, 640, 3), np.uint8)[::2, ::3],  # 연속되지 않은 메모리
    np.zeros((240, 320), np.uint8),                              # 흑백
    np.zeros((240, 320, 4), np.uint8),                           # BGRA
    np.zeros((240, 320, 3), np.uint16),                          # 16bit
], ids=["1x1", "1x4000", "8K", "strided", "gray", "bgra", "u16"])
def test_unusual_frames_save(app_env, frame):
    config, db = app_env
    service = CaptureService(db, config)
    cap = service.save_photo(frame, _patient(db))
    img = _read(cap.file_path)
    assert img.shape[:2] == frame.shape[:2]
    assert cap.thumbnail_path and Path(cap.thumbnail_path).exists()


def test_none_frame_is_rejected_without_db_row(app_env):
    config, db = app_env
    service = CaptureService(db, config)
    p = _patient(db)
    with pytest.raises(ValueError):
        service.save_photo(None, p)
    assert db.captures.list_by_patient(p.id) == []


# ============================================================ DB: 강제 종료·손상·제약

_KILL_SCRIPT = r"""
import sys, time
sys.path.insert(0, sys.argv[2])
from app.db.database import Database
from app.models.patient import Patient
from app.models.capture import Capture
db = Database(sys.argv[1]).init()
p = Patient(chart_no="KILL", name="강제종료")
p.id = db.patients.insert(p)
print("ready", flush=True)
i = 0
while True:
    db.captures.insert(Capture(patient_id=p.id, media_type="photo", file_path=f"/x/{i}.png",
                               captured_at="2026-01-01 00:00:00", eye="OD"))
    print(i, flush=True)
    i += 1
"""


def test_db_survives_hard_kill_mid_write(tmp_path):
    """저장 도중 프로그램이 강제 종료(전원 차단·작업관리자)돼도 DB 는 깨지지 않고, 확정된 기록은 남는다."""
    if sys.platform.startswith("win"):
        pytest.skip("SIGKILL 시뮬레이션은 POSIX 전용")
    dbp = tmp_path / "kill.db"
    script = tmp_path / "w.py"
    script.write_text(_KILL_SCRIPT, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), str(dbp), str(ROOT)],
                            stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "ready"
    last = -1
    t0 = time.time()
    while time.time() - t0 < 1.0:
        line = proc.stdout.readline().strip()
        if line.isdigit():
            last = int(line)
    proc.kill()
    proc.wait(10)
    assert last > 10

    db = Database(str(dbp)).init()
    with db._lock:
        assert db._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        n = db._conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
    db.close()
    assert n >= last + 1, f"확정 보고된 {last + 1}건 중 {n}건만 남음"


# 손상 DB 는 앱 고유 오류(복구 안내 포함)로 알린다 — 어느 쪽이든 파일은 그대로여야 한다
_DB_ERRORS = (sqlite3.DatabaseError, AppDatabaseError)


def test_corrupt_db_is_never_overwritten(tmp_path):
    """DB 파일이 손상돼 있으면 열기를 거부해야지, 새 DB 로 덮어쓰거나 지우면 안 된다 (복구 가능하게)."""
    dbp = tmp_path / "bad.db"
    junk = os.urandom(8192)
    dbp.write_bytes(junk)
    with pytest.raises(_DB_ERRORS):
        Database(str(dbp)).init()
    assert dbp.read_bytes() == junk


def test_truncated_db_is_never_overwritten(app_env, tmp_path):
    config, db = app_env
    _patient(db)
    db.close()
    raw = Path(config.db_path).read_bytes()
    bad = tmp_path / "trunc.db"
    bad.write_bytes(raw[:100])
    with pytest.raises(_DB_ERRORS):
        Database(str(bad)).init()
    assert bad.read_bytes() == raw[:100]


def test_failed_insert_does_not_poison_later_writes(app_env):
    """차트번호 중복 오류 뒤에도 다음 저장은 정상적으로 디스크에 확정돼야 한다."""
    config, db = app_env
    _patient(db, chart="DUP")
    with pytest.raises(sqlite3.IntegrityError):
        _patient(db, chart="DUP")
    good = _patient(db, chart="OK2")
    db.captures.insert(Capture(patient_id=good.id, media_type="photo", file_path="/a.png",
                               captured_at="2026-01-01 00:00:00", eye="OD"))
    other = sqlite3.connect(config.db_path)          # 다른 연결(=재시작)에서 보이는가
    try:
        assert other.execute("SELECT COUNT(*) FROM patients WHERE chart_no='OK2'").fetchone()[0] == 1
        assert other.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 1
    finally:
        other.close()


def test_capture_for_deleted_patient_is_refused(app_env):
    """그 사이 환자가 삭제됐다면 남의 환자/없는 환자에게 영상이 붙지 않는다."""
    config, db = app_env
    p = _patient(db)
    db.patients.delete(p.id)
    with pytest.raises(sqlite3.IntegrityError):
        db.captures.insert(Capture(patient_id=p.id, media_type="photo", file_path="/a.png",
                                   captured_at="2026-01-01 00:00:00", eye="OD"))


def test_db_constraints_reject_garbage(app_env):
    config, db = app_env
    p = _patient(db)
    for bad in (dict(media_type="exe"), dict(eye="LEFT")):
        kw = dict(patient_id=p.id, media_type="photo", file_path="/a", captured_at="x", eye="OD")
        kw.update(bad)
        with pytest.raises(sqlite3.IntegrityError):
            db.captures.insert(Capture(**kw))
    with pytest.raises(sqlite3.IntegrityError):
        db.patients.insert(Patient(chart_no="S1", name="x", sex="Z"))


def test_search_handles_wildcards_and_injection(app_env):
    config, db = app_env
    _patient(db, chart="111", name="김철수")
    _patient(db, chart="222", name="이영희")
    assert db.patients.search("'; DROP TABLE patients;--") == []
    assert len(db.patients.list_all()) == 2
    assert db.patients.search("%") == [] and db.patients.search("_") == [], \
        "와일드카드 한 글자로 모든 환자가 검색됨"
    assert [x.name for x in db.patients.search("김")] == ["김철수"]


def test_many_patients_and_captures_scale(app_env):
    config, db = app_env
    with db._lock:
        db._conn.executemany("INSERT INTO patients(chart_no, name) VALUES (?, ?)",
                             [(f"C{i:06d}", f"환자{i}") for i in range(20000)])
        db._conn.commit()
    p = db.patients.search("C019999")[0]
    with db._lock:
        db._conn.executemany(
            "INSERT INTO captures(patient_id, media_type, file_path, captured_at, eye) VALUES (?,?,?,?,?)",
            [(p.id, "photo", f"/p/{i}.png", f"2026-01-01 00:{i // 60 % 60:02d}:{i % 60:02d}", "OD")
             for i in range(5000)])
        db._conn.commit()
    t = time.time()
    rows = db.captures.list_by_patient(p.id)
    hits = db.patients.search("환자1999")
    assert len(rows) == 5000 and hits
    assert time.time() - t < 2.0


# ============================================================ 설정 파일

def _write_cfg(raw: bytes):
    p = AppConfig.config_path()
    p.write_bytes(raw)
    return p


def test_config_saved_with_bom_keeps_storage_locations(app_env, tmp_path):
    """메모장으로 고친 설정(UTF-8 BOM)도 읽어야 한다 — 못 읽으면 저장 폴더가 몰래 기본값으로 바뀐다."""
    nas = str(tmp_path / "병원 NAS")
    _write_cfg(b"\xef\xbb\xbf" + json.dumps({"export_root": nas, "camera_index": 2},
                                              ensure_ascii=False).encode("utf-8"))
    cfg = AppConfig.load()
    assert cfg.export_root == nas and cfg.camera_index == 2


@pytest.mark.parametrize("raw", [b"", b"\x00\x00\x00", b"[1,2,3]", b"123", b'"str"', b"null",
                                 b'{"camera_index": 1', os.urandom(256)])
def test_config_garbage_starts_with_defaults(app_env, raw):
    _write_cfg(raw)
    cfg = AppConfig.load()
    assert cfg.media_root and cfg.db_path and cfg.photo_format == "png"


@pytest.mark.parametrize("key,val", [
    ("photo_format", "exe"), ("photo_format", "../../x"), ("photo_format", ""),
    ("thumbnail_max_side", 0), ("thumbnail_max_side", -5),
    ("capture_width", -1920), ("capture_height", 0), ("camera_index", -3),
    ("subfolder_template", ""), ("subfolder_template", "{"), ("right_click_action", "delete-all"), ("right_click_items", ["rm"]), ("right_click_items", []),
])
def test_config_out_of_range_values_are_corrected(app_env, key, val):
    """값의 형식은 맞지만 말이 안 되는 설정 — 촬영이 전부 실패하지 않도록 기본값으로 되돌린다."""
    _write_cfg(json.dumps({key: val}).encode())
    cfg = AppConfig.load()
    d = AppConfig()
    assert getattr(cfg, key) == getattr(d, key), (key, getattr(cfg, key))


def test_config_with_out_of_range_values_still_captures(app_env):
    config, db = app_env
    config.photo_format = "exe"
    config.thumbnail_max_side = 0
    config.subfolder_template = "{"
    service = CaptureService(db, config)
    cap = service.save_photo(_frame(7), _patient(db))
    assert Path(cap.file_path).exists()


# ============================================================ 동영상 녹화

def _recorder(tmp_path, name="v.mp4", size=(160, 120), fps=30.0):
    from app.capture.video_recorder import VideoRecorder
    rec = VideoRecorder()
    try:
        rec.start(tmp_path / name, fps=fps, size=size)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"인코더 없음: {e}")
    return rec


@pytest.mark.parametrize("jump", [-3600.0, +3600.0], ids=["clock-back-1h", "clock-forward-1h"])
def test_wall_clock_change_during_recording(tmp_path, monkeypatch, jump):
    """녹화 중 윈도우 시간 동기화로 시계가 바뀌어도 길이·프레임 수가 실제 시간과 같아야 한다."""
    from app.capture import video_recorder as vr

    class _Clock:
        offset = 0.0

        def time(self):
            return time.time() + self.offset

        def __getattr__(self, n):
            return getattr(time, n)

    clock = _Clock()
    monkeypatch.setattr(vr, "time", clock)
    rec = _recorder(tmp_path)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.0:
        if time.monotonic() - t0 > 0.5:
            clock.offset = jump
        rec.feed(_frame(10, 120, 160))
        time.sleep(1 / 30)
    real = (time.monotonic() - t0) * 1000        # 길이는 멈춘 순간 기준 (인코더 마무리 시간은 제외)
    _, count, duration = rec.stop()
    assert abs(duration - real) < 400, (duration, real)
    assert 20 <= count <= 45, count


def test_stop_while_encoder_stalled_still_finalizes_file(tmp_path):
    """인코더가 잠시 멈춘 채(느린 PC·디스크) 큐가 가득 찬 상태에서 멈춰도, 결국 파일을 닫아 영상이 완성돼야 한다."""
    from app.capture import video_recorder as vr
    rec = _recorder(tmp_path)
    gate = threading.Event()
    real_write = rec._write
    closed = threading.Event()
    real_close = rec._close_writer

    def slow_write(frame):
        gate.wait(4)
        real_write(frame)

    def close():
        real_close()
        closed.set()

    rec._write = slow_write
    rec._close_writer = close
    rec._start_ts -= 30                          # 30초 녹화한 것처럼 → feed 마다 반복 프레임으로 큐를 채움
    for _ in range(vr._QUEUE_MAX):
        rec.feed(_frame(1, 120, 160))
    assert rec._queue.full()
    threading.Timer(3.0, gate.set).start()
    t = time.monotonic()
    _, count, _ = rec.stop()
    assert time.monotonic() - t < 15
    assert closed.wait(20), "인코더 스레드가 파일을 닫지 못함 (영상 손상)"
    assert count > 0


def test_new_recording_while_previous_still_finalizing_is_safe(tmp_path, monkeypatch):
    """stop() 이 기다리다 먼저 돌아온 뒤(느린 PC) 곧바로 새 녹화를 눌러도 앞 영상이 사라지거나 섞이지 않는다."""
    import imageio.v2 as iio

    from app.capture import video_recorder as vr
    monkeypatch.setattr(vr, "_STOP_WAIT", 0.3)
    rec = _recorder(tmp_path, "a.mp4")
    gate = threading.Event()
    real_write = rec._write
    rec._write = lambda f: (gate.wait(5), real_write(f))[1]
    for _ in range(5):
        rec.feed(_frame(1, 120, 160))
        time.sleep(0.04)
    rec.stop()                                    # 인코더가 막혀 있어 0.3초 뒤 먼저 돌아옴
    assert rec.is_finalizing
    with pytest.raises(RuntimeError):
        rec.start(tmp_path / "b.mp4", fps=30.0, size=(160, 120))
    gate.set()
    assert rec.wait_finalized(15)
    _, count_a, _ = rec.result()
    assert count_a > 0
    del rec._write
    rec.start(tmp_path / "b.mp4", fps=30.0, size=(160, 120))
    for _ in range(10):
        rec.feed(_frame(200, 120, 160))
        time.sleep(0.04)
    rec.stop()
    assert rec.wait_finalized(15)
    for name, v in (("a.mp4", 1), ("b.mp4", 200)):
        r = iio.get_reader(str(tmp_path / name))
        frames = [f for f in r]
        r.close()
        assert frames and all(abs(int(f[..., 1].mean()) - v) < 30 for f in frames), f"{name} 에 다른 녹화가 섞임"


def test_gui_waits_for_slow_encoder_before_saving(qapp, win, monkeypatch):
    """인코더가 늦어 stop() 이 먼저 돌아오면, 파일이 다 닫힌 뒤에야 DB·내보내기에 기록한다."""
    from app.capture import video_recorder as vr
    monkeypatch.setattr(vr, "_STOP_WAIT", 0.2)
    p = _select(win, "G1")
    gate = threading.Event()
    real_write = win.recorder._write
    win.recorder._write = lambda f: (gate.wait(10), real_write(f))[1]
    win.control_bar.btn_record.setChecked(True)
    _wait(qapp, lambda: False, 0.5)
    win.control_bar.btn_record.setChecked(False)
    assert win.recorder.is_finalizing and win._pending_video is not None
    assert [c for c in win.db.captures.list_by_patient(p.id) if c.media_type == "video"] == []
    win.control_bar.btn_record.setChecked(True)               # 마무리 중 새 녹화 → 안내만, 섞이지 않음
    assert not win._recording
    gate.set()
    assert _wait(qapp, lambda: win._pending_video is None, 20)
    del win.recorder._write
    rows = _videos_ok(win.db, p.id)
    assert len(rows) == 1
    exp = Path(rows[0].export_path)
    assert exp.stat().st_size == Path(rows[0].file_path).stat().st_size, "반쯤 쓴 파일이 내보내기로 복사됨"


def test_4k_recorder_queue_memory_is_bounded(tmp_path):
    """4K 녹화에서 인코더가 밀려도 대기 큐가 수 GB 로 불어나 메모리 부족으로 꺼지지 않는다."""
    from app.capture import video_recorder as vr
    assert vr.queue_len((1920, 1080)) == vr._QUEUE_MAX                 # 1080p 는 그대로
    for size in ((3840, 2160), (4096, 2160), (7680, 4320)):
        assert vr.queue_len(size) * size[0] * size[1] * 3 <= vr._QUEUE_MAX_BYTES + size[0] * size[1] * 3 * 8
    rec = _recorder(tmp_path, "4k.mp4", size=(3840, 2160))
    gate = threading.Event()
    real_write = rec._write
    rec._write = lambda f: (gate.wait(10), real_write(f))[1]           # 인코더가 못 따라오는 PC
    rec._start_ts -= 60
    frame = np.zeros((2160, 3840, 3), np.uint8)
    for _ in range(200):
        rec.feed(frame.copy())                                           # 프레임마다 새 메모리 (실제 카메라처럼)
    assert rec._queue.qsize() * frame.nbytes <= vr._QUEUE_MAX_BYTES + frame.nbytes
    gate.set()
    rec.stop()
    assert rec.wait_finalized(60)


def test_encoder_crash_mid_recording_keeps_written_part(tmp_path):
    import imageio.v2 as iio
    rec = _recorder(tmp_path)
    real_write = rec._write
    n = {"i": 0}

    def flaky(frame):
        n["i"] += 1
        if n["i"] > 12:
            raise OSError(errno.ENOSPC, "No space left on device")
        real_write(frame)

    rec._write = flaky
    for _ in range(40):
        rec.feed(_frame(50, 120, 160))
        time.sleep(1 / 30)
    path, count, duration = rec.stop()
    assert count == 12 and 300 <= duration <= 500
    r = iio.get_reader(path)
    assert r.count_frames() == 12, "쓴 부분까지는 재생 가능한 파일이어야 함"
    r.close()


def test_recorder_abuse_sequences(tmp_path):
    """정지만 연타 · 시작 두 번 · 30회 빠른 시작/정지 — 예외·스레드 누수 없음."""
    from app.capture.video_recorder import VideoRecorder
    rec = VideoRecorder()
    assert rec.stop() == ("", 0, 0)
    base = threading.active_count()
    for i in range(30):
        try:
            rec.start(tmp_path / f"r{i}.mp4", fps=30.0, size=(64, 48))
        except Exception as e:  # pragma: no cover
            pytest.skip(f"인코더 없음: {e}")
        rec.start(tmp_path / f"dup{i}.mp4", fps=30.0, size=(64, 48))   # 무시돼야 함
        rec.feed(_frame(i, 48, 64))
        rec.stop()
        rec.stop()
    deadline = time.monotonic() + 10
    while threading.active_count() > base and time.monotonic() < deadline:
        time.sleep(0.05)
    assert threading.active_count() <= base
    assert not list(tmp_path.glob("dup*.mp4"))


def test_recorder_start_failure_in_unwritable_place(tmp_path):
    from app.capture.video_recorder import VideoRecorder
    blocker = tmp_path / "file"
    blocker.write_text("x")
    rec = VideoRecorder()
    try:
        rec.start(blocker / "sub" / "x.mp4", fps=30.0, size=(64, 48))
    except Exception:
        assert not rec.is_recording
        return
    rec.feed(_frame(1, 48, 64))
    time.sleep(0.5)
    _, count, _ = rec.stop()
    assert count == 0                             # 쓸 수 없는 곳이면 '녹화됨'으로 보고하지 않는다


# ============================================================ 카메라 워커

class _Src:
    size = (64, 48)
    fps = 30.0

    def __init__(self, reads):
        self._reads = iter(reads)
        self.released = False

    def open(self):
        pass

    def read(self):
        r = next(self._reads, "end")
        if r == "end":
            time.sleep(0.01)
            return False, None
        if isinstance(r, Exception):
            raise r
        time.sleep(0.001)
        return r

    def release(self):
        self.released = True


def _run_worker(src, timeout=30):
    from PySide6.QtCore import Qt

    from app.capture.camera_worker import CameraWorker
    w = CameraWorker(lambda: src)
    got = {"frames": 0, "errors": [], "stopped": False}
    w.frame_ready.connect(lambda f: got.__setitem__("frames", got["frames"] + 1),
                          Qt.ConnectionType.DirectConnection)
    w.error.connect(got["errors"].append, Qt.ConnectionType.DirectConnection)
    w.stopped.connect(lambda: got.__setitem__("stopped", True), Qt.ConnectionType.DirectConnection)
    t = threading.Thread(target=w.run, daemon=True)
    t.start()
    t.join(timeout)
    return w, t, got


def test_camera_unplugged_is_reported_and_released():
    ok = (True, np.zeros((48, 64, 3), np.uint8))
    src = _Src([ok] * 5)                          # 5장 뒤 계속 실패 = 케이블 뽑힘
    _, t, got = _run_worker(src)                  # 연속 실패 50회 — 느린 CI(맥)에선 sleep 이 길어져 수 초 걸림
    assert not t.is_alive() and got["frames"] == 5 and got["errors"] and src.released and got["stopped"]


def test_camera_driver_exception_is_reported_and_released():
    ok = (True, np.zeros((48, 64, 3), np.uint8))
    src = _Src([ok, ok, RuntimeError("DirectShow 드라이버 오류")])
    _, t, got = _run_worker(src)
    assert not t.is_alive() and got["errors"] and src.released


def test_camera_open_failure_is_reported():
    class _Bad(_Src):
        def open(self):
            raise RuntimeError("장치 사용 중 (다른 프로그램)")
    src = _Bad([])
    _, t, got = _run_worker(src)
    assert not t.is_alive() and got["errors"] and got["frames"] == 0


def test_camera_intermittent_glitches_do_not_disconnect():
    ok = (True, np.zeros((48, 64, 3), np.uint8))
    bad = (False, None)
    reads = ([bad] * 20 + [ok]) * 5 + [ok] * 3
    src = _Src(reads)
    _, t, got = _run_worker(src, timeout=30)
    assert got["frames"] >= 8                     # 순간 끊김 때문에 녹화·촬영이 중단되지 않음


# ============================================================ 화면(GUI) 통합 — 실제 사용 흐름의 극한

def _wait(qapp, cond, timeout=8.0):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def win(qapp, app_env, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    config, db = app_env
    config.source_mode = "synthetic"
    config.capture_width, config.capture_height = 320, 240
    config.save = lambda: None
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2] if len(a) > 2 else a))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: warnings.append(a[2] if len(a) > 2 else a))
    db.patients.insert(Patient(chart_no="G1", name="첫환자"))
    db.patients.insert(Patient(chart_no="G2", name="둘째환자"))
    from app.ui.main_window import MainWindow
    w = MainWindow(config, db)
    w.warnings = warnings
    w.show()
    assert _wait(qapp, lambda: w._last_frame is not None)
    yield w
    w.close()


def _select(win, chart):
    p = next(x for x in win.db.patients.list_all() if x.chart_no == chart)
    win._on_patient_changed(p)
    return p


def _videos_ok(db, pid):
    rows = [c for c in db.captures.list_by_patient(pid) if c.media_type == "video"]
    for c in rows:
        assert Path(c.file_path).exists() and Path(c.file_path).stat().st_size > 0, c.file_path
        assert (c.duration_ms or 0) >= 0
    return rows


def test_gui_rapid_record_toggling(qapp, win):
    p = _select(win, "G1")
    for _ in range(8):
        win.control_bar.btn_record.setChecked(True)
        _wait(qapp, lambda: False, 0.25)
        win.control_bar.btn_record.setChecked(False)
        _wait(qapp, lambda: False, 0.05)
    assert not win._recording
    rows = _videos_ok(win.db, p.id)
    assert len(rows) == 8, win.warnings


def test_gui_switch_patient_while_recording_saves_to_original(qapp, win):
    """녹화 중 다른 환자를 고르면 녹화를 끝내 앞 환자에게 저장 — 새 환자의 눈이 앞 환자 영상에 들어가지 않는다."""
    a = _select(win, "G1")
    win.control_bar.btn_record.setChecked(True)
    _wait(qapp, lambda: False, 0.5)
    b = _select(win, "G2")
    assert not win._recording and not win.control_bar.btn_record.isChecked()
    assert len(_videos_ok(win.db, a.id)) == 1
    assert [c for c in win.db.captures.list_by_patient(b.id) if c.media_type == "video"] == []
    win.control_bar.btn_record.setChecked(True)
    _wait(qapp, lambda: False, 0.4)
    win._on_patient_changed(win.db.patients.get(b.id))      # 같은 환자 재선택(검색·정보 수정)은 녹화를 끊지 않음
    assert win._recording
    win.control_bar.btn_record.setChecked(False)
    assert len(_videos_ok(win.db, a.id)) == 1 and len(_videos_ok(win.db, b.id)) == 1


def test_gui_camera_lost_while_recording_saves_what_was_recorded(qapp, win):
    p = _select(win, "G1")
    win.control_bar.btn_record.setChecked(True)
    _wait(qapp, lambda: False, 0.6)
    win._on_camera_error("카메라 연결이 끊어졌습니다.")
    assert not win._recording
    assert len(_videos_ok(win.db, p.id)) == 1


def test_gui_close_while_recording_saves_video(qapp, app_env, win):
    p = _select(win, "G1")
    win.control_bar.btn_record.setChecked(True)
    _wait(qapp, lambda: False, 0.6)
    win.close()
    config, _ = app_env
    db2 = Database(config.db_path).init()
    try:
        rows = [c for c in db2.captures.list_by_patient(p.id) if c.media_type == "video"]
        assert len(rows) == 1 and Path(rows[0].file_path).stat().st_size > 0
    finally:
        db2.close()


def test_gui_capture_burst(qapp, win):
    p = _select(win, "G1")
    for _ in range(25):
        win._on_capture()
    rows = win.db.captures.list_by_patient(p.id)
    assert len(rows) == 25 and len({r.file_path for r in rows}) == 25
    assert all(Path(r.file_path).exists() for r in rows)


def test_gui_capture_without_patient_is_blocked(qapp, win):
    win._on_patient_changed(None)
    win._on_capture()
    win.control_bar.btn_record.setChecked(True)
    assert not win._recording
    with win.db._lock:
        assert win.db._conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 0


def test_gui_restart_camera_while_recording(qapp, win):
    p = _select(win, "G1")
    win.control_bar.btn_record.setChecked(True)
    _wait(qapp, lambda: False, 0.5)
    win._restart_camera()
    assert not win._recording
    assert len(_videos_ok(win.db, p.id)) == 1
    assert _wait(qapp, lambda: win.worker is not None and win._last_frame is not None)


def test_gui_disk_full_on_capture_tells_user(qapp, win, monkeypatch):
    p = _select(win, "G1")

    def full(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(win.service, "save_photo", full)
    n = len(win.warnings)
    win._on_capture()
    assert len(win.warnings) == n + 1 and "저장 실패" in str(win.warnings[-1])
    assert win.db.captures.list_by_patient(p.id) == []

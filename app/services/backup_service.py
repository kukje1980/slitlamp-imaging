"""외장 하드 · 다른 드라이브로 백업 — 실시간(저장 즉시) · 자동(켤 때 · 주기적으로 빠진 파일 확인) · 수동(지금 백업).

백업 위치(config.backup_root) 아래에 '백업 폴더(세트)' 를 만든다. 폴더 이름 = 백업을 시작한 날짜 + 마지막으로 백업한 날짜
(YYMMDD + YYMMDD, 12자리). 예: 2021-09-08 에 시작해 2026-10-28 까지 백업했으면 '210908261028'. 날짜가 바뀐 뒤 처음
백업할 때 뒤의 날짜가 오늘로 바뀐다 (폴더 이름 바꾸기). '새 백업 폴더 시작' 을 누르면 그 폴더는 그대로 두고 오늘 날짜로
새 폴더를 만들어 처음부터 다시 백업한다. 외장 하드를 여러 개 번갈아 써도 하드마다 자기 폴더가 이어진다(같은 세트 번호).
같은 이름이 이미 있으면(다른 PC 가 같은 날 시작) 뒤에 -2, -3 … 을 붙인다.

세트 폴더 안
  backup_info.json      세트 번호(set_id) · 시작 · 마지막 날짜 · 마지막 백업 시각 · PC 이름 · 원래 위치
  media/                사진 · 동영상 · 썸네일 (config.media_root 그대로 — 프로그램에서 지운 사진도 백업에는 남긴다)
  reference_library/    참조 영상 라이브러리
  db/slitlamp.db        환자 · 검사 · 영상 기록 (SQLite online backup — 쓰는 중에도 일관된 사본)
  db/history/slitlamp-YYYYMMDD.db   날짜별 DB (그날 마지막 상태, 최근 DB_HISTORY_KEEP 일) — 특정 날짜로 되돌릴 때
  config/config.json    설정
  복원 방법.txt
안전
- 복사는 임시 이름(.이름.bkpart)에 다 쓴 뒤 바꿔 넣는다 — 하드를 뽑거나 꺼져도 반쯤 복사된 파일이 진짜처럼 남지 않는다.
- 같은 크기 · 같은 수정 시각(±2초, FAT · exFAT)인 파일은 건너뛴다 → 두 번째부터는 새 파일 · 바뀐 파일만 복사.
- 녹화 중인 동영상(busy)과 방금 바뀐 동영상(SETTLE_S 안, 저장이 끝났다고 알려 준 것 제외)은 이번에 건너뛴다.
- 백업 드라이브 여유가 MIN_FREE_MB 보다 적어지면 멈추고 알린다. 백업은 사진 저장 · 촬영을 막지 않는다(백그라운드 스레드).
- 전체 백업은 환자 기록 DB 를 먼저 (가장 작고 중요) · 파일 복사 · DB 를 한 번 더. 중지 · 닫기는 4 MB 마다 확인해 바로 멈춘다.
- 백업은 원본(사진 폴더 · DB · 설정)을 읽기만 한다 — 원본을 바꾸거나 지우지 않는다.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import platform
import queue
import re
import shutil
import sqlite3
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger(__name__)

INFO_FILE = "backup_info.json"
README_FILE = "복원 방법.txt"
NAME_RE = re.compile(r"^(\d{6})(\d{6})(?:-(\d+))?$")
SOURCES = (("media", "사진 · 동영상"), ("reference_library", "참조 영상"))
DB_HISTORY_KEEP = 60             # 날짜별 DB 사본을 남길 날 수
MIN_FREE_MB = 200                # 백업 드라이브에 남겨 둘 여유 공간
MTIME_SLACK_S = 2.0              # FAT32 · exFAT 의 수정 시각 오차
SETTLE_S = 10.0                  # 전체 확인 때 이보다 최근에 바뀐 동영상은 쓰는 중일 수 있어 다음 차례로
INTERVALS = ((30, "30분마다"), (60, "1시간마다"), (240, "4시간마다"), (1440, "하루 한 번"))
VIDEO_EXT = (".mp4", ".avi", ".mov", ".mkv", ".m4v")
_JUNK = {"thumbs.db", ".ds_store", "desktop.ini"}
_PART_SUFFIXES = (".part", ".bkpart", ".tmp")
CHUNK = 4 * 1024 * 1024          # 복사 단위 — 이만큼마다 중지 · 닫기를 확인
_MAC = sys.platform == "darwin"
EXAMPLE_ROOT = "/Volumes/외장하드/슬릿램프 백업" if _MAC else "E:\\슬릿램프 백업"


class BackupError(RuntimeError):
    """백업을 할 수 없음 — 사용자에게 보여 줄 글을 담는다."""


class DriveMissing(BackupError):
    """백업 위치(외장 하드)가 연결되어 있지 않다."""


class BackupDiskFull(BackupError):
    """백업 드라이브 여유 공간이 부족하다."""


class Cancelled(BackupError):
    """중지 버튼 · 프로그램 닫기로 멈췄다."""


# ================= 이름 =================
def set_name(start: date, last: date) -> str:
    """백업 폴더 이름: 시작 날짜 + 마지막 백업 날짜 (YYMMDDYYMMDD). 2021-09-08 ~ 2026-10-28 → '210908261028'."""
    return f"{start:%y%m%d}{last:%y%m%d}"


def parse_set_name(name: str) -> tuple[date, date] | None:
    m = NAME_RE.match(name or "")
    if not m:
        return None
    try:
        return (datetime.strptime(m.group(1), "%y%m%d").date(), datetime.strptime(m.group(2), "%y%m%d").date())
    except ValueError:
        return None


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def is_inside(path, folder) -> bool:
    """path 가 folder 와 같거나 그 안에 있는가."""
    if not path or not folder:
        return False
    a, b = _norm(path), _norm(folder)
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:                       # 다른 드라이브 (Windows)
        return False


def _existing(p: Path) -> Path | None:
    p = Path(p)
    while not p.exists():
        if p.parent == p:
            return None
        p = p.parent
    return p


def same_drive(a, b) -> bool:
    """두 경로가 같은 드라이브(볼륨)에 있는가 — 같으면 하드 고장 대비가 되지 않는다."""
    pa, pb = _existing(Path(a or "")), _existing(Path(b or ""))
    if pa is None or pb is None:
        return False
    try:
        return os.stat(pa).st_dev == os.stat(pb).st_dev
    except OSError:
        return False


def source_roots(media_root: str) -> list[tuple[str, str]]:
    """백업할 폴더 [(원래 위치, 백업 폴더 안 이름)] — 사진 · 동영상, 참조 영상 라이브러리."""
    media = str(media_root or "")
    return [(media, "media"), (str(Path(media).parent / "reference_library"), "reference_library")]


def _set_folder_of(p: Path) -> Path | None:
    """p 또는 그 위 폴더가 백업 폴더(backup_info.json 이 있는)면 그 폴더."""
    for q in (p, *p.parents):
        if (q / INFO_FILE).is_file():
            return q
    return None


def describe_target(root: str, media_root: str) -> tuple[str, str]:
    """설정 탭에 보여 줄 백업 위치 상태 (등급, 글): unset · error · missing · warn · ok."""
    root = (root or "").strip()
    if not root:
        return "unset", f"백업 위치를 고르세요 — 외장 하드나 다른 드라이브의 폴더 (예: {EXAMPLE_ROOT})"
    if any(src and is_inside(root, src) for src, _sub in source_roots(media_root)):
        return "error", "사진 · 참조 영상 저장 폴더 안은 백업 위치로 쓸 수 없습니다 — 외장 하드나 다른 드라이브의 폴더를 고르세요"
    p = Path(root)
    if not p.is_dir():
        return "missing", "지금 연결되어 있지 않습니다 — 외장 하드를 꽂으면 자동으로 이어서 백업합니다"
    inner = _set_folder_of(p)
    if inner is not None:
        return "error", (f"'{inner.name}' 은 백업 폴더 자체입니다 — 그 위의 폴더({inner.parent})를 고르세요 "
                         "(백업 폴더는 그 안에 날짜 이름으로 만들어집니다)")
    try:
        free = shutil.disk_usage(p).free / 1024 ** 3
        text = f"연결됨 · 여유 {free:,.1f} GB"
    except OSError:
        text = "연결됨"
    if same_drive(root, media_root):
        return "warn", (text + " · ⚠ 사진을 저장하는 드라이브와 같은 드라이브입니다 — 하드 고장에 대비하려면 외장 하드나 "
                        "다른 드라이브를 고르세요")
    return "ok", text


# ================= 세트 폴더 =================
@dataclass
class BackupSet:
    path: Path
    set_id: str
    start: date
    last: date
    info: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def last_at(self) -> str:
        return str(self.info.get("last_at") or "")


def _read_info(folder: Path) -> dict | None:
    try:
        data = json.loads((Path(folder) / INFO_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("set_id") else None


def _anchor_missing(anchor) -> bool:
    return anchor is not None and not Path(anchor).is_file()


def _mkdir_guarded(parent: Path, anchor=None) -> list[Path]:
    """parent 폴더를 만든다 (없는 윗 폴더까지). 만든 폴더 목록(깊은 것부터)을 돌려준다.

    anchor(백업 폴더 안의 표지 파일 backup_info.json)가 주어지면 만들기 전후로 그 파일이 있는지 본다 — 없으면
    외장 하드가 빠진 것이라 방금 만든 빈 폴더를 되돌리고 DriveMissing. 그냥 만들면 내장 디스크에 같은 이름의 폴더
    (맥: /Volumes/이름/…)가 생겨 그쪽으로 백업하게 되고, 다음에 하드를 꽂으면 '이름 1' 로 붙는다.
    """
    if _anchor_missing(anchor):
        raise DriveMissing(f"백업 위치가 연결되어 있지 않습니다 (복사 중 빠짐): {Path(anchor).parent}")
    made: list[Path] = []
    p = Path(parent)
    while not p.exists():
        made.append(p)
        if p.parent == p:
            break
        p = p.parent
    Path(parent).mkdir(parents=True, exist_ok=True)
    if _anchor_missing(anchor):
        _rmdirs(made)
        raise DriveMissing(f"백업 위치가 연결되어 있지 않습니다 (복사 중 빠짐): {Path(anchor).parent}")
    return made


def _rmdirs(dirs) -> None:
    for d in dirs:                           # 깊은 것부터 — 비어 있는 것만 지워진다
        try:
            Path(d).rmdir()
        except OSError:
            pass


def _atomic_write(path: Path, data: bytes, anchor=None) -> None:
    path = Path(path)
    made = _mkdir_guarded(path.parent, anchor)
    tmp = path.with_name(f".{path.name}.bkpart")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if _anchor_missing(anchor):
            raise DriveMissing(f"백업 위치가 연결되어 있지 않습니다 (복사 중 빠짐): {Path(anchor).parent}")
        os.replace(tmp, path)
    except (OSError, DriveMissing) as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        if isinstance(e, DriveMissing) or _anchor_missing(anchor):
            _rmdirs(made)                    # 하드가 빠진 뒤 내장 디스크에 만들어진 빈 폴더는 되돌린다
        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
            raise BackupDiskFull("백업 드라이브 공간이 부족합니다") from e
        raise


def _write_info(folder: Path, info: dict, anchor=None) -> None:
    _atomic_write(Path(folder) / INFO_FILE, json.dumps(info, ensure_ascii=False, indent=2).encode("utf-8"), anchor)


def _as_date(s, fallback):
    try:
        return date.fromisoformat(str(s))
    except (TypeError, ValueError):
        return fallback


def list_sets(root) -> list[BackupSet]:
    """백업 위치 바로 아래의 백업 폴더들 (backup_info.json 이 있는 것), 시작 날짜 순."""
    root = Path(root or "")
    out = []
    try:
        entries = list(os.scandir(root)) if str(root) else []
    except OSError:
        return []
    for e in entries:
        try:
            if not e.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        info = _read_info(Path(e.path))
        if info is None:
            continue
        parsed = parse_set_name(e.name)
        start = _as_date(info.get("start"), parsed[0] if parsed else None)
        if start is None:
            continue
        last = _as_date(info.get("last"), parsed[1] if parsed else start)
        out.append(BackupSet(Path(e.path), str(info["set_id"]), start, last or start, info))
    out.sort(key=lambda s: (s.start, s.name))
    return out


def find_set(root, set_id: str) -> BackupSet | None:
    return next((s for s in list_sets(root) if s.set_id == set_id), None) if set_id else None


def _free_name(root: Path, start: date, last: date, own: Path | None = None) -> str:
    base = set_name(start, last)
    n = 1
    while True:
        cand = base if n == 1 else f"{base}-{n}"
        p = root / cand
        if not p.exists() or (own is not None and _norm(p) == _norm(own)):
            return cand
        n += 1


def ensure_set(root, set_id: str, today: date, extra: dict | None = None) -> tuple[BackupSet, str]:
    """이 PC 의 백업 폴더를 찾거나 만든다. 날짜가 바뀌었으면 이름의 뒤 날짜를 오늘로 바꾼다. (세트, 경고 글)."""
    root = Path(root)
    if not root.is_dir():
        raise DriveMissing(f"백업 위치가 연결되어 있지 않습니다: {root}")
    cur = find_set(root, set_id)
    if cur is None:
        path = root / _free_name(root, today, today)
        try:
            path.mkdir()
        except OSError as e:
            raise BackupError(f"백업 위치에 폴더를 만들 수 없습니다: {path} ({e.strerror or e})") from e
        info = {"set_id": set_id, "start": today.isoformat(), "last": today.isoformat(),
                "created_at": datetime.now().isoformat(timespec="seconds"), **(extra or {})}
        _write_info(path, info)
        log.info("새 백업 폴더: %s", path)
        return BackupSet(path, set_id, today, today, info), ""
    warn = ""
    last = max(cur.last, today)
    want = _free_name(root, cur.start, last, own=cur.path)
    if cur.path.name != want:
        new = root / want
        try:
            cur.path.rename(new)
            log.info("백업 폴더 이름 바꿈: %s → %s", cur.path.name, want)
            cur.path = new
        except OSError as e:                 # 폴더 안의 파일을 다른 프로그램이 열어 둔 경우 (Windows)
            warn = (f"백업 폴더 이름을 {want} 로 바꾸지 못했습니다 ({e.strerror or e}) — 그 폴더 · 파일을 연 창을 닫으면 "
                    "다음 백업 때 바뀝니다 (백업은 지금 폴더에 계속합니다)")
            log.warning("백업 폴더 이름 바꾸기 실패: %s → %s", cur.path, want, exc_info=True)
    info = dict(cur.info)
    info.update(extra or {})
    info["last"] = last.isoformat()
    if info != cur.info:
        _write_info(cur.path, info, anchor=cur.path / INFO_FILE)
    cur.last, cur.info = last, info
    return cur, warn


def finish_set(bset: BackupSet, **values) -> None:
    """백업을 마친 시각 · 개수를 backup_info.json 에 적는다."""
    info = dict(bset.info)
    info["last_at"] = datetime.now().isoformat(timespec="seconds")
    info.update({k: v for k, v in values.items() if v is not None})
    _write_info(bset.path, info, anchor=bset.path / INFO_FILE)
    bset.info = info


# ================= 복사 =================
@dataclass
class SyncStats:
    scanned: int = 0
    copied: int = 0
    bytes: int = 0
    failed: list = field(default_factory=list)
    cancelled: bool = False


def _skip_name(name: str) -> bool:
    low = name.lower()
    return low in _JUNK or low.endswith(_PART_SUFFIXES)


def _needs_copy(size: int, mtime: float, dst: Path) -> bool:
    try:
        st = dst.stat()
    except OSError:
        return True
    return st.st_size != size or abs(st.st_mtime - mtime) > MTIME_SLACK_S


def copy_file(src, dst, min_free_mb: float = MIN_FREE_MB, cancel=None, anchor=None) -> int:
    """src → dst 를 임시 이름(.이름.bkpart)에 다 쓰고 디스크에 내린 뒤 바꿔 넣는다 (수정 시각 보존). 복사한 바이트 수.

    cancel() 이 참이면 CHUNK 마다 확인해 Cancelled — 임시 파일은 지운다 (반쯤 복사된 파일이 남지 않게).
    anchor(백업 폴더의 backup_info.json)를 주면 폴더를 만들 때와 바꿔 넣기 직전에 그 파일이 있는지 보고, 없으면
    (외장 하드가 빠짐) 만든 폴더 · 임시 파일을 되돌리고 DriveMissing — 내장 디스크에 백업 폴더가 생기지 않게.
    """
    src, dst = Path(src), Path(dst)
    size = src.stat().st_size
    made = _mkdir_guarded(dst.parent, anchor)
    try:
        free = shutil.disk_usage(dst.parent).free
    except OSError:
        free = None
    if free is not None and free < size + min_free_mb * 1024 * 1024:
        raise BackupDiskFull(f"백업 드라이브 공간이 부족합니다 (여유 {free / 1024 ** 2:,.0f} MB, 필요 "
                             f"{size / 1024 ** 2 + min_free_mb:,.0f} MB)")
    tmp = dst.with_name(f".{dst.name}.bkpart")
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                if cancel is not None and cancel():
                    raise Cancelled("중지했습니다")
                buf = fi.read(CHUNK)
                if not buf:
                    break
                fo.write(buf)
            fo.flush()
            os.fsync(fo.fileno())            # 하드를 바로 뽑아도 바꿔 넣은 파일은 내용까지 있게
        shutil.copystat(src, tmp)
        if _anchor_missing(anchor):
            raise DriveMissing(f"백업 위치가 연결되어 있지 않습니다 (복사 중 빠짐): {Path(anchor).parent}")
        os.replace(tmp, dst)
    except BaseException as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        if isinstance(e, DriveMissing) or _anchor_missing(anchor):
            _rmdirs(made)                    # 하드가 빠진 뒤 내장 디스크에 만들어진 빈 폴더는 되돌린다
        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
            raise BackupDiskFull("백업 드라이브 공간이 부족합니다") from e
        raise
    return size


def plan_tree(src_root, dst_root, busy=(), known=(), now: float | None = None, cancel=None,
              on_scan=None) -> tuple[list, int]:
    """src_root 아래에서 dst_root 로 복사해야 할 파일 [(원본, 대상, 크기)] 와 살펴본 파일 수.

    busy(녹화 중) · 임시 파일 · 방금 바뀐 동영상(known 에 없으면)은 뺀다. 원본에서 지운 파일은 백업에서 지우지 않는다.
    """
    src_root, dst_root = Path(src_root), Path(dst_root)
    if not src_root.is_dir():
        return [], 0
    now = time.time() if now is None else now
    busy = {_norm(p) for p in busy}
    known = {_norm(p) for p in known}
    todo, n = [], 0
    for dirpath, dirnames, filenames in os.walk(src_root):
        if dirpath != str(src_root) and os.path.isfile(os.path.join(dirpath, INFO_FILE)):
            dirnames[:] = []                 # 사진 폴더 안에 백업 폴더가 있어도 자기 자신은 복사하지 않는다
            continue
        dirnames.sort()
        for fn in sorted(filenames):
            if cancel is not None and cancel():
                return todo, n
            if _skip_name(fn):
                continue
            sp = Path(dirpath) / fn
            key = _norm(sp)
            if key in busy:
                continue
            try:
                st = sp.stat()
            except OSError:
                continue
            if fn.lower().endswith(VIDEO_EXT) and key not in known and now - st.st_mtime < SETTLE_S:
                continue                     # 쓰는 중일 수 있는 동영상 — 저장이 끝나면 따로 알려 온다
            n += 1
            dp = dst_root / sp.relative_to(src_root)
            if _needs_copy(st.st_size, st.st_mtime, dp):
                todo.append((sp, dp, st.st_size))
            if on_scan is not None and n % 200 == 0:
                on_scan(n)
    return todo, n


def _serialize_db(db_path: Path) -> bytes:
    """사용 중인 SQLite DB 의 일관된 사본(바이트) — 앱의 연결과 따로 연다 (잠금을 오래 잡지 않게)."""
    src = sqlite3.connect(str(db_path), timeout=10)
    try:
        mem = sqlite3.connect(":memory:")
        try:
            src.backup(mem)
            if hasattr(mem, "serialize"):
                return mem.serialize()
        finally:
            mem.close()
        import tempfile
        with tempfile.TemporaryDirectory() as d:  # serialize 가 없는 SQLite
            tmp = Path(d) / "snap.db"
            out = sqlite3.connect(str(tmp))
            try:
                src.backup(out)
            finally:
                out.close()
            return tmp.read_bytes()
    finally:
        src.close()


def snapshot_db(db_path, dest_dir, today: date, keep: int = DB_HISTORY_KEEP, anchor=None) -> Path | None:
    """DB 를 dest_dir/<이름> 과 dest_dir/history/<이름>-YYYYMMDD 로 (그날 것은 마지막 상태로 덮어씀)."""
    src = Path(db_path or "")
    if not src.is_file():
        return None
    data = _serialize_db(src)
    dest_dir = Path(dest_dir)
    dst = dest_dir / src.name
    _atomic_write(dst, data, anchor)
    hist_dir = dest_dir / "history"
    _atomic_write(hist_dir / f"{src.stem}-{today:%Y%m%d}{src.suffix}", data, anchor)
    old = sorted(hist_dir.glob(f"{src.stem}-*{src.suffix}"))
    for p in old[:-keep] if keep > 0 else []:
        try:
            p.unlink()
        except OSError:
            pass
    return dst


def _readme(bset: BackupSet, job: dict) -> str:
    src = {sub: path for path, sub in job["sources"]}
    sep = os.sep
    rows = [
        f"슬릿램프 영상 시스템 백업 — {bset.name}",
        f"시작 {bset.start:%Y-%m-%d} · 마지막 백업 {bset.last_at.replace('T', ' ') or bset.last.isoformat()}"
        f" · PC {bset.info.get('machine', '')}",
        "",
        "폴더 이름 = 백업을 시작한 날짜 + 마지막으로 백업한 날짜 (YYMMDDYYMMDD).",
        "",
        "원래 위치 → 이 폴더",
    ]
    for sub, label in SOURCES:
        rows.append(f"  {label}: {src.get(sub, '')}  →  {sub}{sep}")
    rows += [f"  환자 · 검사 · 영상 기록(DB): {job.get('db_path', '')}  →  db{sep}{Path(job.get('db_path') or 'slitlamp.db').name}",
             f"  (날짜별 DB: db{sep}history — 그날 마지막 상태)",
             f"  설정: {job.get('config_path', '')}  →  config{sep}",
             "",
             "복원 방법",
             "  1. 프로그램을 닫습니다.",
             "  2. media · reference_library 폴더를 위의 원래 위치에 그대로 복사합니다.",
             f"  3. db 폴더의 DB 파일을 원래 위치에 복사합니다 (특정 날짜로 되돌리려면 db{sep}history 의 그 날짜 파일을",
             "     원래 이름으로 바꿔서). 원래 위치에 같은 이름의 -wal · -shm 파일이 있으면 지웁니다.",
             "  4. 프로그램을 켭니다.",
             "  ※ DB 에는 사진 위치가 원래 경로로 적혀 있어, 같은 위치로 되돌려야 사진이 열립니다.",
             "  ※ 프로그램에서 지운 사진도 백업에는 남아 있습니다 (실수로 지운 사진을 되찾을 수 있게)."]
    return "\r\n".join(rows) + "\r\n"


# ================= 백그라운드 작업 =================
def _fmt_last(iso: str, today: date) -> str:
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{t:%H:%M}" if t.date() == today else f"{t:%m-%d %H:%M}"


class BackupManager(QObject):
    """백업 일정 · 작업 줄 · 상태. 복사는 백그라운드 스레드에서 하고, 결과는 시그널로 GUI 스레드에 알린다.

    - notify_saved(경로들): 사진 · 동영상 저장 직후 (CaptureService.on_saved) — 자동이면 바로 복사(실시간).
    - backup_now(): 전체 확인 (새 파일 · 바뀐 파일만 복사) + DB · 설정 · 안내 글.
    - 켤 때(startup_delay_ms 뒤) · config.backup_interval_min 마다 · 외장 하드를 다시 꽂았을 때 자동으로 전체 확인.
    - DB 가 바뀌면(사진 기록 · 환자 정보 · 소견) DB_WATCH_MS 안에 DB 만 다시 백업 (자동일 때).
    - finish_on_exit(): 닫을 때 DB · 못 한 파일 마무리.
    작업 스레드는 데몬 스레드 — 백업 드라이브가 응답하지 않아도 프로그램 종료를 막지 않는다.
    """

    statusChanged = Signal(dict)
    progress = Signal(float, float, str)      # 한 것, 전체 (바이트, 0 = 모름), 글 — 2 GB 넘는 값도 (float)
    finished = Signal(dict)                   # 작업 결과
    _sig_done = Signal(dict)
    _sig_state = Signal(str)
    _sig_progress = Signal(float, float, str)

    DB_WATCH_MS = 30_000
    DRIVE_WATCH_MS = 20_000
    STOPPED_MSG = "중지했습니다 — 남은 파일은 다음 백업 때 이어서 복사합니다"

    def __init__(self, config, db=None, parent=None, today=date.today, startup_delay_ms: int | None = 20_000):
        super().__init__(parent)
        self.config = config
        self.db = db
        self._today = today
        self._startup_delay = startup_delay_ms
        self._q: queue.Queue = queue.Queue()
        self._cancel = threading.Event()      # 중지 버튼 — 전체 확인만 멈춘다 (실시간 복사는 계속)
        self._halt = threading.Event()        # 프로그램 닫기 — 모두 멈춘다
        self._busy: set[str] = set()
        self._known: set[str] = set()
        self._dirty = False
        self._state = "idle"
        self._message = ""
        self._prog = (0.0, 0.0, "")
        self._running = False
        self._set_name = ""
        self._set_path = ""
        self._db_seen = None
        self._thread: threading.Thread | None = None
        self._sig_done.connect(self._on_done)
        self._sig_state.connect(self._on_state)
        self._sig_progress.connect(self._on_progress)
        self._periodic = QTimer(self)
        self._periodic.timeout.connect(self._auto_periodic)
        self._watch = QTimer(self)
        self._watch.setInterval(self.DRIVE_WATCH_MS)
        self._watch.timeout.connect(self._check_drive)
        self._dbw = QTimer(self)
        self._dbw.setInterval(self.DB_WATCH_MS)
        self._dbw.timeout.connect(self._check_db)
        self._startup = QTimer(self)
        self._startup.setSingleShot(True)
        self._startup.timeout.connect(self._auto_startup)
        self._stopped = True
        self._last_root = self.root()

    # ---- 설정 ----
    def root(self) -> str:
        return (getattr(self.config, "backup_root", "") or "").strip()

    def auto(self) -> bool:
        return bool(getattr(self.config, "backup_auto", True))

    def sources(self) -> list[tuple[str, str]]:
        return source_roots(self.config.media_root)

    def _save_config(self) -> None:
        try:
            self.config.save()
        except Exception:  # noqa: BLE001
            log.warning("백업 설정 저장 실패", exc_info=True)

    def set_id(self) -> str:
        if not getattr(self.config, "backup_set_id", ""):
            self.config.backup_set_id = uuid.uuid4().hex
            self._save_config()
        return self.config.backup_set_id

    # ---- 시작 · 멈춤 ----
    def start(self) -> None:
        self._stopped = False
        self._halt.clear()
        self._start_worker()
        self._watch.start()
        self._dbw.start()
        self._apply_schedule()
        if self._startup_delay is not None and self.auto() and self.root():
            self._startup.start(self._startup_delay)
        self._check_drive()                   # 켤 때 외장 하드가 없으면 바로 '백업 드라이브 없음'
        self._emit_status()

    def _start_worker(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="backup", daemon=True)
            self._thread.start()

    def stop(self, timeout_ms: int = 10_000) -> None:
        """타이머 · 작업 스레드를 멈춘다. 전체 확인은 바로 멈추고, 실시간 복사는 잠깐 기다려 준다."""
        self._stopped = True
        for t in (self._periodic, self._watch, self._dbw, self._startup):
            t.stop()
        self._cancel.set()
        t, self._thread = self._thread, None
        if t is None:
            return
        self._q.put(None)
        t.join(timeout_ms / 2000)
        if t.is_alive():
            self._halt.set()                  # 큰 동영상 복사 중 → 4 MB 안에 멈춤
            t.join(timeout_ms / 2000)
        if t.is_alive():
            log.warning("백업 작업이 멈추지 않았습니다 (백업 드라이브가 응답하지 않음?) — 두고 닫습니다")

    def reconfigure(self) -> None:
        """설정 창에서 바꾼 뒤 — 주기를 다시 맞추고, 백업 위치를 처음 정했거나 바꿨으면 바로 전체 백업."""
        self._apply_schedule()
        root = self.root()
        changed = root != self._last_root
        self._last_root = root
        if changed:
            self._state, self._message, self._set_name, self._set_path = "idle", "", "", ""
        self._check_drive()
        if changed and root and self.auto() and os.path.isdir(root):
            self.backup_now(reason="settings")
        self._emit_status()

    def _apply_schedule(self) -> None:
        if self.auto() and self.root():
            mins = max(5, int(getattr(self.config, "backup_interval_min", 60) or 60))
            if not self._periodic.isActive() or self._periodic.interval() != mins * 60_000:
                self._periodic.start(mins * 60_000)
        else:
            self._periodic.stop()

    # ---- 작업 넣기 ----
    def _job(self, kind: str, root: str | None = None, **kw) -> dict:
        cfg = self.config
        from app import __version__
        db_path = getattr(cfg, "db_path", "")
        job = {"kind": kind, "root": (self.root() if root is None else (root or "").strip()), "set_id": self.set_id(),
               "sources": self.sources(), "db_path": db_path, "media_root": str(getattr(cfg, "media_root", "") or ""),
               "config_path": str(type(cfg).config_path()) if hasattr(type(cfg), "config_path") else "",
               "busy": set(self._busy), "known": set(self._known),
               "info": {"machine": platform.node(), "app_version": __version__,
                        "sources": {sub: src for src, sub in self.sources()} | {"db": db_path}}}
        job.update(kw)
        return job

    def _enqueue(self, job: dict) -> bool:
        if not job["root"] or self._stopped:
            return False
        self._start_worker()
        self._q.put(job)
        return True

    def notify_saved(self, *paths) -> None:
        """사진 · 동영상을 저장했다 (CaptureService.on_saved). 자동이면 바로 백업 위치에 복사."""
        paths = [str(p) for p in paths if p]
        if not paths or not self.root():
            return
        self._known.update(_norm(p) for p in paths)
        if self.auto():
            self._enqueue(self._job("files", paths=paths))
        else:
            self._dirty = True
            self._emit_status()

    def set_busy(self, paths) -> None:
        """녹화 중이라 아직 쓰고 있는 파일 (전체 확인에서 뺀다)."""
        self._busy = {_norm(p) for p in paths or () if p}

    def backup_now(self, root: str | None = None, reason: str = "manual") -> bool:
        """전체 확인 — 새 파일 · 바뀐 파일만 복사 + DB · 설정 · 안내 글. root 를 주면 그 위치로 (설정 창의 '지금 백업')."""
        job = self._job("full", root=root, reason=reason)
        if not job["root"] or self._stopped:
            return False
        self._cancel.clear()
        return self._enqueue(job)

    def new_set(self, root: str | None = None) -> bool:
        """지금 백업 폴더는 그대로 두고 오늘 날짜로 새 백업 폴더를 시작한다 (처음부터 모두 다시 복사)."""
        if self._stopped or not (self.root() if root is None else (root or "").strip()):
            return False
        self.config.backup_set_id = uuid.uuid4().hex
        self._save_config()
        self._set_name = self._set_path = ""
        return self.backup_now(root=root, reason="new_set")

    def cancel(self) -> None:
        self._cancel.set()

    def is_running(self) -> bool:
        return self._running or self._q.unfinished_tasks > 0

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """작업 줄이 빌 때까지 이벤트를 돌리며 기다린다 (시험 · 종료용)."""
        from PySide6.QtWidgets import QApplication
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QApplication.processEvents()
            if self._q.unfinished_tasks == 0:
                QApplication.processEvents()
                return True
            time.sleep(0.02)
        return False

    def finish_on_exit(self, parent=None, timeout: float = 180.0) -> None:
        """닫을 때: DB(환자 기록)와 아직 못 한 파일을 마무리 (외장 하드가 없거나 끈 설정이면 건너뜀).

        자동이면 남은 실시간 복사 + DB (빠진 파일이 있었으면 전체 확인), 수동이면 전체 백업 (하루 끝에 한 번).
        0.8초 넘게 걸리면 '백업을 마무리하는 중' 창 — '건너뛰기' 로 멈출 수 있다 (남은 것은 다음에 켤 때).
        """
        root = self.root()
        if self._stopped or not root or not getattr(self.config, "backup_on_exit", True) or not os.path.isdir(root):
            return
        if self._dirty or not self.auto():
            self.backup_now(reason="exit")
        else:
            self._enqueue(self._job("db"))
        from PySide6.QtWidgets import QApplication, QProgressDialog
        dlg = None
        end = time.monotonic() + timeout
        started = time.monotonic()
        while self._q.unfinished_tasks > 0 and time.monotonic() < end:
            QApplication.processEvents()
            if dlg is None and time.monotonic() - started > 0.8 and parent is not None:
                dlg = QProgressDialog("백업을 마무리하는 중입니다 … (외장 하드를 뽑지 마세요)", "건너뛰기", 0, 0, parent)
                dlg.setWindowTitle("백업")
                dlg.setMinimumDuration(0)
                dlg.show()
            if dlg is not None:
                done, total, text = self._prog
                if total > 0:
                    dlg.setRange(0, 1000)
                    dlg.setValue(int(1000 * done / total))
                if text:
                    dlg.setLabelText("백업을 마무리하는 중입니다 … (외장 하드를 뽑지 마세요)\n" + text)
                if dlg.wasCanceled():
                    self.cancel()
                    break
            time.sleep(0.03)
        QApplication.processEvents()          # 마지막 결과(마친 시각 · 상태)까지 반영
        if dlg is not None:
            dlg.close()
            dlg.deleteLater()

    # ---- 자동 일정 ----
    def _auto_startup(self) -> None:
        self._auto("startup")

    def _auto_periodic(self) -> None:
        self._auto("auto")

    def _auto(self, reason: str) -> None:
        if self.auto() and self.root() and os.path.isdir(self.root()):
            self.backup_now(reason=reason)   # 이미 돌고 있으면 줄에서 하나로 합쳐진다

    def _check_drive(self) -> None:
        root = self.root()
        if not root or self._stopped:
            return
        present = os.path.isdir(root)
        if not present and self._state != "missing" and not self._running:
            self._state, self._message = "missing", ""
            self._dirty = True
            self._emit_status()
        elif present and self._state == "missing":
            self._state, self._message = "idle", ""
            self._emit_status()
            if self.auto():
                self.backup_now(reason="reconnect")   # 빠져 있던 동안 저장한 사진을 이어서

    def _check_db(self) -> None:
        if self.db is None or not self.auto() or not self.root() or self._running or self._stopped:
            return
        try:
            n = self.db.change_count()
        except Exception:  # noqa: BLE001
            return
        if self._db_seen is None:
            self._db_seen = n
        elif n != self._db_seen and os.path.isdir(self.root()):
            self._db_seen = n
            self._enqueue(self._job("db"))

    # ---- 상태 ----
    def _on_state(self, state: str) -> None:
        self._running = state == "running"
        if self._running:
            self._prog = (0.0, 0.0, "")
        self._emit_status()

    def _on_progress(self, done: float, total: float, text: str) -> None:
        self._prog = (done, total, text)
        self.progress.emit(done, total, text)
        self._emit_status()

    def _on_done(self, res: dict) -> None:
        self._running = False                # 줄에 작업이 더 있으면 작업 스레드가 곧 다시 'running' 을 알린다
        if res.get("ok"):
            self._state = "ok"
            self._message = res.get("warn") or ""
            self._set_name, self._set_path = res.get("set_name", ""), res.get("set_path", "")
            self.config.backup_last_at = datetime.now().isoformat(timespec="seconds")
            self._save_config()
            if res.get("kind") == "full" and not res.get("failed"):
                self._dirty = False
                self._known.clear()
            if res.get("failed"):
                self._dirty = True
                self._message = (self._message + " · " if self._message else "") + \
                    f"복사하지 못한 파일 {len(res['failed'])}개 (다음 확인 때 다시)"
            if res.get("source_missing"):
                self._dirty = True
                self._message = (self._message + " · " if self._message else "") + \
                    f"사진 저장 폴더를 찾을 수 없어 사진 · 동영상은 백업하지 못했습니다: {res['source_missing']} " \
                    "(연결을 확인하세요 — 환자 기록 DB 는 백업했습니다)"
        else:
            self._state = res.get("state", "error")
            self._message = res.get("message", "")
            self._dirty = True
        if self.db is not None and res.get("db"):
            try:
                self._db_seen = self.db.change_count()
            except Exception:  # noqa: BLE001
                pass
        self._emit_status()
        self.finished.emit(res)

    def status(self) -> dict:
        """상태줄 버튼 · 설정 탭에 보여 줄 상태 — level(ok · running · warn · error · unset), text, tip …"""
        root, auto = self.root(), self.auto()
        today = self._today()
        last = _fmt_last(getattr(self.config, "backup_last_at", ""), today)
        st = {"root": root, "auto": auto, "state": self._state, "message": self._message,
              "set_name": self._set_name, "set_path": self._set_path, "last": last, "dirty": self._dirty,
              "progress": self._prog, "running": self._running}
        mode = "자동 (저장 즉시 복사)" if auto else "수동 ('지금 백업' 을 누를 때)"
        if not root:
            st.update(level="unset", text="백업 설정 안 됨",
                      tip="설정 → 백업 에서 외장 하드나 다른 드라이브의 폴더를 고르면 사진 · 동영상 · 환자 기록을 백업합니다")
        elif self._running:
            done, total, text = self._prog
            pct = f" {int(100 * done / total)}%" if total else ""
            st.update(level="running", text=f"백업 중{pct}", tip=(text or "백업 중 …") + f"\n{self._set_path or root}")
        elif self._state == "missing":
            st.update(level="warn", text="백업 드라이브 없음",
                      tip=f"백업 위치가 연결되어 있지 않습니다: {root}\n외장 하드를 꽂으면 "
                          + ("자동으로 이어서 백업합니다" if auto else "'지금 백업' 으로 백업하세요"))
        elif self._state == "full":
            st.update(level="error", text="백업 드라이브 가득 참", tip=self._message)
        elif self._state == "error":
            st.update(level="error", text="백업 실패", tip=self._message)
        elif self._state == "stopped":
            st.update(level="warn", text="백업 중지됨", tip=self._message or self.STOPPED_MSG)
        else:
            if auto:
                text = ("백업 ⚠ 남은 파일 있음" if self._dirty else f"백업 ✓ {last}") if last else "백업 대기"
            else:
                text = "백업 (수동) · 새 사진 있음" if self._dirty else (
                    f"백업 (수동) ✓ {last}" if last else "백업 (수동) · 아직 안 함")
            tip = f"백업 {mode} · 마지막 {last or '-'}\n{self._set_path or root}"
            if self._dirty:
                tip += ("\n백업하지 않은 새 사진이 있습니다 — 설정 → 백업 → '지금 백업'" if not auto else
                        "\n아직 복사하지 못한 파일이 있습니다 — 다음 확인 때 이어서 복사합니다")
            if self._message:
                tip += "\n" + self._message
            st.update(level="warn" if (self._message or self._dirty) else "ok", text=text, tip=tip)
        return st

    def _emit_status(self) -> None:
        self.statusChanged.emit(self.status())

    # ---- 작업 (백그라운드 스레드) ----
    def _loop(self) -> None:
        while True:
            batch = [self._q.get()]
            while True:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            jobs = [j for j in batch if j is not None]
            try:
                if jobs:
                    self._run(jobs)
            except Exception:  # noqa: BLE001   (창이 먼저 닫혀 시그널을 못 보내는 경우 등)
                log.exception("백업 작업 실패")
            finally:
                for _ in batch:
                    self._q.task_done()
            if len(jobs) != len(batch):
                return

    def _run(self, jobs: list) -> None:
        full = next((j for j in reversed(jobs) if j["kind"] == "full"), None)
        job = full if full is not None else jobs[-1]
        files = [p for j in jobs if j["kind"] == "files" for p in j["paths"]]
        want_db = any(j["kind"] == "db" for j in jobs)
        res = {"kind": "full" if full else ("files" if files else "db"), "reason": (full or {}).get("reason", ""),
               "ok": False, "root": job["root"]}
        self._sig_state.emit("running")
        try:
            root = job["root"]
            if not root or not os.path.isdir(root):
                raise DriveMissing(f"백업 위치가 연결되어 있지 않습니다: {root}")
            level, text = describe_target(root, job.get("media_root", ""))
            if level == "error":             # 사진 폴더 안 · 백업 폴더 자체 (설정 파일을 손으로 고쳤거나 사진 폴더를 나중에
                raise BackupError(text)      # 바꾼 경우) — 그대로 돌리면 자기 자신을 복사해 돌 때마다 두 배로 커진다
            today = self._today()
            bset, warn = ensure_set(root, job["set_id"], today, job["info"])
            res.update(set_name=bset.name, set_path=str(bset.path))
            if full is not None:
                stats = self._full(bset, job, today, res)
            else:
                stats = self._files(bset, job, files)
                if want_db:
                    self._require(bset)
                    snapshot_db(job["db_path"], bset.path / "db", today, anchor=bset.path / INFO_FILE)
                    res["db"] = True
            self._require(bset)
            finish_set(bset, files=stats.scanned or None)
            res.update(ok=True, set_name=bset.name, set_path=str(bset.path), warn=warn, copied=stats.copied,
                       bytes=stats.bytes, failed=stats.failed, scanned=stats.scanned)
        except Cancelled:
            res.update(state="stopped", message=self.STOPPED_MSG)
        except DriveMissing as e:
            res.update(state="missing", message=str(e))
        except BackupDiskFull as e:
            res.update(state="full", message=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("백업 실패")
            res.update(state="error", message=f"{type(e).__name__}: {e}")
        self._sig_done.emit(res)

    @staticmethod
    def _require(bset: BackupSet) -> None:
        """백업 폴더가 아직 있는지 (외장 하드가 빠졌으면 없다) — 없으면 바로 멈춘다.

        그냥 계속 쓰면 폴더 만들기(mkdir)가 내장 디스크에 같은 이름의 폴더를 다시 만들어 그쪽으로 백업하게 된다
        (맥은 /Volumes/이름 폴더가 남으면 다음에 하드를 꽂아도 '이름 1' 로 붙어 백업이 영영 내장 디스크로 간다).
        """
        if not (bset.path / INFO_FILE).is_file():
            raise DriveMissing(f"백업 위치가 연결되어 있지 않습니다 (복사 중 빠짐): {bset.path.parent}")

    def _target(self, bset: BackupSet, job: dict, path: str) -> Path | None:
        for src, sub in job["sources"]:
            if src and is_inside(path, src):
                return bset.path / sub / os.path.relpath(path, src)
        return None

    def _files(self, bset: BackupSet, job: dict, files: list) -> SyncStats:
        """실시간 — 방금 저장한 파일만 복사 (중지 버튼과 상관없이, 닫을 때만 멈춤)."""
        stats = SyncStats()
        for p in dict.fromkeys(files):
            dst = self._target(bset, job, p)
            if dst is None:
                continue
            try:
                if not _needs_copy(*_size_mtime(p), dst):
                    continue
                self._require(bset)
                stats.bytes += copy_file(p, dst, cancel=self._halt.is_set, anchor=bset.path / INFO_FILE)
                stats.copied += 1
            except FileNotFoundError:
                continue                     # 그 사이에 지운 파일
            except (BackupDiskFull, Cancelled):
                raise
            except OSError as e:
                log.warning("백업 복사 실패: %s", p, exc_info=True)
                stats.failed.append(f"{p} ({e.strerror or e})")
        return stats

    def _full(self, bset: BackupSet, job: dict, today: date, res: dict) -> SyncStats:
        """전체 확인: DB 먼저 → 새 파일 · 바뀐 파일 복사 → DB 한 번 더 · 설정 · 안내 글."""
        def cancel() -> bool:
            return self._cancel.is_set() or self._halt.is_set()

        stats = SyncStats()
        self._sig_progress.emit(0.0, 0.0, "환자 기록(DB) 백업 중")
        self._require(bset)
        if snapshot_db(job["db_path"], bset.path / "db", today, anchor=bset.path / INFO_FILE) is not None:
            res["db"] = True
        plans = []
        for src, sub in job["sources"]:
            if sub == "media" and not (src and os.path.isdir(src)):
                res["source_missing"] = src  # 사진 폴더(NAS 등)가 끊김 — 사진은 못 봤으니 '백업 ✓' 로 보이면 안 된다
            todo, n = plan_tree(src, bset.path / sub, busy=job["busy"], known=job["known"], cancel=cancel,
                                on_scan=lambda k, base=stats.scanned: self._sig_progress.emit(
                                    0.0, 0.0, f"확인 중 · 파일 {base + k:,}개"))
            plans += todo
            stats.scanned += n
            if cancel():
                raise Cancelled("중지했습니다")
        total = float(sum(s for *_x, s in plans))
        self._sig_progress.emit(0.0, max(total, 1.0), f"복사할 파일 {len(plans):,}개 · {total / 1024 ** 2:,.0f} MB")
        done, shown = 0.0, 0.0
        for i, (sp, dp, size) in enumerate(plans, 1):
            try:
                self._require(bset)
                copy_file(sp, dp, cancel=cancel, anchor=bset.path / INFO_FILE)
                stats.copied += 1
                stats.bytes += size
            except FileNotFoundError:
                pass                         # 그 사이에 지운 파일
            except (BackupDiskFull, Cancelled):
                raise
            except OSError as e:
                log.warning("백업 복사 실패: %s", sp, exc_info=True)
                stats.failed.append(f"{sp} ({e.strerror or e})")
            done += size
            now = time.monotonic()
            if now - shown > 0.2 or i == len(plans):
                shown = now
                self._sig_progress.emit(done, max(total, 1.0), f"복사 중 {i:,} / {len(plans):,} 파일")
        self._require(bset)
        if plans:
            snapshot_db(job["db_path"], bset.path / "db", today, anchor=bset.path / INFO_FILE)   # 복사하는 동안 바뀐 기록까지
        cfg = Path(job.get("config_path") or "")
        if cfg.is_file():
            copy_file(cfg, bset.path / "config" / cfg.name, min_free_mb=0, anchor=bset.path / INFO_FILE)
        bset.info["last_at"] = datetime.now().isoformat(timespec="seconds")
        _atomic_write(bset.path / README_FILE, _readme(bset, job).encode("utf-8-sig"), anchor=bset.path / INFO_FILE)
        return stats


def _size_mtime(p) -> tuple[int, float]:
    st = os.stat(p)
    return st.st_size, st.st_mtime

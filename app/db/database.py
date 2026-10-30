from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from app.db.agent_events_dao import AgentEventsDAO
from app.db.captures_dao import CapturesDAO
from app.db.exam_notes_dao import ExamNotesDAO
from app.db.exams_dao import ExamsDAO
from app.db.measurements_dao import MeasurementsDAO
from app.db.patients_dao import PatientsDAO
from app.db.ref_images_dao import RefImagesDAO

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
log = logging.getLogger(__name__)

BUSY_TIMEOUT_SEC = 10.0          # 다른 프로세스(두 번째 실행·DB 뷰어)가 잠근 동안 기다리는 시간
BACKUP_DIR_NAME = "backups"
BACKUP_KEEP = 14                 # 보관할 일일 백업 개수


class DatabaseError(RuntimeError):
    """DB 를 열 수 없거나 손상된 경우 — 사용자에게 보여줄 메시지를 담는다."""


class Database:
    """SQLite 연결/마이그레이션/백업 관리 + DAO 보유.

    GUI 스레드와 백그라운드 작업이 같은 연결을 쓸 수 있도록
    check_same_thread=False + Lock 직렬화. 단일 PC/단일 writer라 충분하다.

    안전성:
    - busy_timeout: 프로그램이 두 번 실행되거나 외부 도구가 DB 를 열어 둔 동안
      즉시 실패하지 않고 기다린다.
    - DAO 의 모든 쓰기는 `with lock, conn:` 안에서 실행 → 실패하면 롤백
      (반쯤 쓰인 상태가 남지 않음).
    - 시작 시 quick_check 로 파일 손상을 확인하고, 하루 한 번 online backup API 로 사본을 남긴다.
    - WAL + synchronous=FULL: 정전·강제 종료에도 커밋된 레코드는 보존된다.
    """

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self.patients: PatientsDAO | None = None
        self.captures: CapturesDAO | None = None
        self.measurements: MeasurementsDAO | None = None
        self.last_backup: Path | None = None
        self.exam_notes: ExamNotesDAO | None = None
        self.exams: ExamsDAO | None = None
        self.references: RefImagesDAO | None = None

    # ---- 열기 / 닫기 ----
    def init(self, backup: bool = True) -> "Database":
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=BUSY_TIMEOUT_SEC, check_same_thread=False)
        except (OSError, sqlite3.Error) as e:
            raise DatabaseError(f"데이터베이스 파일을 열 수 없습니다.\n{self.db_path}\n{e}") from e
        conn.row_factory = sqlite3.Row
        existed = Path(self.db_path).is_file() and Path(self.db_path).stat().st_size > 0
        try:
            self._check_integrity(conn)          # 가장 먼저: 손상된 파일이면 PRAGMA 도 실패한다
            conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SEC * 1000)}")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
        except DatabaseError:
            conn.close()
            raise
        except sqlite3.Error as e:
            conn.close()
            raise DatabaseError(f"데이터베이스를 준비하지 못했습니다.\n{self.db_path}\n{e}") from e
        self._conn = conn
        if backup and existed:
            self.daily_backup()              # 기존 DB 는 마이그레이션(스키마 변경) 전에 사본
        try:
            self._migrate()
        except sqlite3.Error as e:
            self.close()
            raise DatabaseError(
                f"데이터베이스 스키마를 갱신하지 못했습니다.\n{self.db_path}\n{e}") from e
        if backup and not existed:
            self.daily_backup()              # 새 DB 는 스키마가 만들어진 뒤 (빈 사본이 남지 않게)
        self.patients = PatientsDAO(self._conn, self._lock)
        self.captures = CapturesDAO(self._conn, self._lock)
        self.measurements = MeasurementsDAO(self._conn, self._lock)
        self.agent_events = AgentEventsDAO(self._conn, self._lock)
        self.exam_notes = ExamNotesDAO(self._conn, self._lock)
        self.exams = ExamsDAO(self._conn, self._lock)
        self.references = RefImagesDAO(self._conn, self._lock)
        return self

    def _check_integrity(self, conn: sqlite3.Connection) -> None:
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
        except sqlite3.DatabaseError as e:        # "file is not a database" 등
            raise DatabaseError(
                f"데이터베이스 파일이 손상되었거나 SQLite 파일이 아닙니다.\n{self.db_path}\n{e}\n\n"
                f"'{self.backup_dir}' 폴더의 최근 백업으로 복구할 수 있습니다.") from e
        problems = [r[0] for r in rows if r[0] != "ok"]
        if problems:
            raise DatabaseError(
                f"데이터베이스 손상이 감지되었습니다.\n{self.db_path}\n"
                + "\n".join(problems[:5])
                + f"\n\n'{self.backup_dir}' 폴더의 최근 백업으로 복구할 수 있습니다.")

    def _migrate(self) -> None:
        ddl = SCHEMA_PATH.read_text(encoding="utf-8")
        with self._lock:
            self._conn.executescript(ddl)
            with self._conn:
                _add_missing_columns(self._conn, "patients",
                                     {"diagnosis": "TEXT", "doctor": "TEXT"})
                _add_missing_columns(self._conn, "captures",
                                     {"exam_id": "INTEGER REFERENCES exams(id) ON DELETE SET NULL"})
                self._conn.execute("CREATE INDEX IF NOT EXISTS idx_captures_exam ON captures(exam_id)")
                _backfill_exams(self._conn)

    # ---- 복원 (Explorer 판 파일 메뉴 — 백업은 아래 backup_to) ----
    def restore_from(self, src_path) -> None:
        """백업 파일 내용으로 지금 DB 를 바꾼다 (연결은 그대로 — 다시 시작할 필요 없음)."""
        src = sqlite3.connect(str(src_path))
        try:
            ok = src.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if ok != "ok" or not {"patients", "captures"} <= tables:
                raise ValueError("슬릿램프 영상 시스템의 백업 파일이 아닙니다.")
            with self._lock:
                src.backup(self._conn)
        finally:
            src.close()
        self._migrate()                      # 구버전 백업이면 새 컬럼·검사 묶음을 채움

    def close(self) -> None:
        if self._conn is None:
            return
        with self._lock:
            conn, self._conn = self._conn, None
            try:
                # WAL 내용을 본 파일에 합쳐 -wal 파일이 크게 남지 않게 한다
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                log.warning("wal_checkpoint 실패 (다음 실행 때 자동 복구)", exc_info=True)
            conn.close()
        self.patients = None
        self.captures = None
        self.measurements = None
        self.agent_events = None
        self.exam_notes = None
        self.exams = None
        self.references = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    def change_count(self) -> int:
        """이 연결이 지금까지 바꾼 행 수 (SQLite total_changes) — 백업이 'DB 가 바뀌었는가' 를 볼 때."""
        with self._lock:
            return self._conn.total_changes if self._conn is not None else 0


    # ---- 백업 ----
    @property
    def backup_dir(self) -> Path:
        return Path(self.db_path).parent / BACKUP_DIR_NAME

    def backup_to(self, dest) -> Path:
        """SQLite online backup API 로 일관된 사본을 만든다 (WAL 내용 포함, 사용 중에도 안전)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        with self._lock:
            out = sqlite3.connect(tmp)
            try:
                self._conn.backup(out)
            finally:
                out.close()
        tmp.replace(dest)
        return dest

    def daily_backup(self, now: datetime | None = None) -> Path | None:
        """오늘 백업이 없으면 만든다. 실패해도 프로그램 실행은 막지 않는다 (경고 로그만)."""
        now = now or datetime.now()
        stem = Path(self.db_path).stem
        dest = self.backup_dir / f"{stem}-{now:%Y%m%d}.db"
        if dest.exists():
            self.last_backup = dest
            return dest
        try:
            self.backup_to(dest)
            self.last_backup = dest
            self._prune_backups(stem)
            log.info("DB 백업 생성: %s", dest)
            return dest
        except Exception:  # noqa: BLE001
            log.warning("DB 백업 실패: %s", dest, exc_info=True)
            return None

    def _prune_backups(self, stem: str, keep: int = BACKUP_KEEP) -> None:
        files = sorted(self.backup_dir.glob(f"{stem}-*.db"))
        for old in files[:-keep] if keep > 0 else files:
            try:
                old.unlink()
            except OSError:
                log.warning("오래된 백업 삭제 실패: %s", old, exc_info=True)

    # ---- 진단 ----
    def quick_check(self) -> list[str]:
        """현재 연결에서 무결성 검사. 문제가 없으면 빈 목록."""
        with self._lock:
            rows = self._conn.execute("PRAGMA quick_check").fetchall()
        return [r[0] for r in rows if r[0] != "ok"]

    def stats(self) -> dict:
        with self._lock:
            p = self._conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
            c = self._conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
        return {"patients": p, "captures": c, "path": self.db_path,
                "backup": str(self.last_backup) if self.last_backup else None}


def _add_missing_columns(conn: sqlite3.Connection, table: str, cols: dict) -> None:
    """기존 DB에 새 컬럼이 없으면 추가 (구버전 DB 자동 업그레이드)."""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in cols.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")



def _backfill_exams(conn: sqlite3.Connection) -> None:
    """검사가 없는 캡처(구버전·다른 판에서 찍은 영상)를 촬영 날짜별 검사로 묶는다."""
    rows = conn.execute(
        """SELECT DISTINCT patient_id, substr(captured_at, 1, 10) AS d
           FROM captures WHERE exam_id IS NULL"""
    ).fetchall()
    for pid, day in rows:
        r = conn.execute(
            "SELECT id FROM exams WHERE patient_id=? AND exam_date=? ORDER BY id DESC LIMIT 1", (pid, day)
        ).fetchone()
        eid = r[0] if r else conn.execute(
            "INSERT INTO exams (patient_id, exam_date, title) VALUES (?,?,?)", (pid, day, "세극등 검사")
        ).lastrowid
        conn.execute(
            "UPDATE captures SET exam_id=? WHERE exam_id IS NULL AND patient_id=? AND substr(captured_at,1,10)=?",
            (eid, pid, day),
        )

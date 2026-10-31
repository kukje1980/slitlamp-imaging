from __future__ import annotations

import sqlite3
from typing import List, Optional

from app.models.capture import Capture


class CapturesDAO:
    """captures 테이블 접근. 쓰기는 `with self._lock, self._c:` 로 묶어
    성공 시 commit, 예외 시 rollback 되게 한다 (열린 트랜잭션이 남지 않음)."""

    def __init__(self, conn: sqlite3.Connection, lock):
        self._c = conn
        self._lock = lock

    def insert(self, cap: Capture) -> int:
        with self._lock, self._c:
            cur = self._c.execute(
                """INSERT INTO captures
                   (patient_id, media_type, file_path, export_path, thumbnail_path,
                    captured_at, eye, duration_ms, note, exam_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (cap.patient_id, cap.media_type, cap.file_path, cap.export_path,
                 cap.thumbnail_path, cap.captured_at, cap.eye, cap.duration_ms, cap.note,
                 cap.exam_id),
            )
            return cur.lastrowid

    def list_by_patient(self, pid: int, order: str = "desc") -> List[Capture]:
        o = "DESC" if order.lower() == "desc" else "ASC"
        with self._lock:
            rows = self._c.execute(
                f"""SELECT * FROM captures WHERE patient_id=?
                    ORDER BY captured_at {o}, id {o}""",
                (pid,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def list_by_exam(self, exam_id: int, order: str = "asc") -> List[Capture]:
        o = "DESC" if order.lower() == "desc" else "ASC"
        with self._lock:
            rows = self._c.execute(
                f"""SELECT * FROM captures WHERE exam_id=?
                    ORDER BY captured_at {o}, id {o}""",
                (exam_id,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def set_exam(self, cid: int, exam_id: Optional[int]) -> None:
        """영상을 다른 검사로 옮긴다 (Explorer 판 트리에서 끌어 놓기·메뉴)."""
        with self._lock, self._c:
            self._c.execute("UPDATE captures SET exam_id=? WHERE id=?", (exam_id, cid))

    def list_all(self) -> List[Capture]:
        """모든 영상 기록, 기록 순서 (프로그램을 끌 때 사진 지우기용)."""
        with self._lock:
            rows = self._c.execute("SELECT * FROM captures ORDER BY id ASC").fetchall()
        return [self._row(r) for r in rows]

    def max_id(self) -> int:
        """지금까지 기록된 가장 큰 영상 id (없으면 0) — 이번에 켠 뒤 찍은 영상을 가려내는 기준."""
        with self._lock:
            r = self._c.execute("SELECT MAX(id) FROM captures").fetchone()
        return int(r[0] or 0)

    def summary_by_patient(self) -> dict:
        """{환자 id: (영상 수, 최근 촬영 시각)} — 환자 목록 화면용."""
        with self._lock:
            rows = self._c.execute(
                "SELECT patient_id, COUNT(*) AS n, MAX(captured_at) AS last FROM captures GROUP BY patient_id"
            ).fetchall()
        return {r["patient_id"]: (r["n"], r["last"]) for r in rows}

    def get(self, cid: int) -> Optional[Capture]:
        with self._lock:
            r = self._c.execute("SELECT * FROM captures WHERE id=?", (cid,)).fetchone()
        return self._row(r)

    def update_note(self, cid: int, note: Optional[str]) -> None:
        """영상별 코멘트 수정 (Pro 모드 Edit Data)."""
        with self._lock, self._c:
            self._c.execute("UPDATE captures SET note=? WHERE id=?", (note, cid))

    def delete(self, cid: int) -> Optional[dict]:
        """레코드를 지우고, 디스크 정리를 위해 관련 경로를 반환한다.

        다른 기록이 아직 같은 파일을 가리키면(DB 복원 · 가져오기 · 날짜 수정으로 생길 수 있음) 그 경로는 None 으로
        돌려준다 — 부르는 쪽이 그 파일을 지우지 않게 (다른 기록의 영상이 사라지면 안 된다).
        """
        with self._lock, self._c:
            r = self._c.execute(
                "SELECT file_path, export_path, thumbnail_path FROM captures WHERE id=?",
                (cid,),
            ).fetchone()
            if r is None:
                return None
            self._c.execute("DELETE FROM captures WHERE id=?", (cid,))
            return self._unshared(dict(r))

    def _unshared(self, row: dict) -> dict:
        """row 의 file_path · thumbnail_path 중 남은 기록이 아직 쓰는 것은 None 으로 (잠금 안에서 부른다)."""
        for key in ("file_path", "thumbnail_path"):
            p = row.get(key)
            if p and self._c.execute(
                    "SELECT 1 FROM captures WHERE file_path=? OR thumbnail_path=? LIMIT 1", (p, p)).fetchone():
                row[key] = None
        return row

    @staticmethod
    def _row(r) -> Optional[Capture]:
        if r is None:
            return None
        return Capture(**{k: r[k] for k in r.keys()})

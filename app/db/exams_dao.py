"""검사(내원) — 환자 › 검사 › 영상 트리의 가운데 단계 (Explorer 판).

캡처할 때 검사를 고르지 않으면 그날 날짜의 검사를 찾아 쓰고, 없으면 새로 만든다 (ensure_for_date).
검사를 지우면 그 검사의 캡처 레코드도 함께 지운다 (디스크 파일 정리를 위해 경로를 돌려준다).
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from app.models.exam import Exam

DEFAULT_TITLE = "세극등 검사"


class ExamsDAO:
    def __init__(self, conn: sqlite3.Connection, lock):
        self._c = conn
        self._lock = lock

    def insert(self, e: Exam) -> int:
        with self._lock, self._c:
            cur = self._c.execute(
                "INSERT INTO exams (patient_id, exam_date, title, diagnosis, memo) VALUES (?,?,?,?,?)",
                (e.patient_id, e.exam_date, e.title or DEFAULT_TITLE, e.diagnosis, e.memo),
            )
            return cur.lastrowid

    def update(self, e: Exam) -> None:
        with self._lock, self._c:
            self._c.execute(
                "UPDATE exams SET exam_date=?, title=?, diagnosis=?, memo=? WHERE id=?",
                (e.exam_date, e.title, e.diagnosis, e.memo, e.id),
            )

    def get(self, eid: int) -> Optional[Exam]:
        with self._lock:
            r = self._c.execute("SELECT * FROM exams WHERE id=?", (eid,)).fetchone()
        return self._row(r)

    def list_by_patient(self, pid: int) -> List[Exam]:
        """최근 검사가 위로. 각 검사의 사진·동영상 수를 함께 채운다."""
        with self._lock:
            rows = self._c.execute(
                """SELECT e.*,
                          (SELECT COUNT(*) FROM captures c WHERE c.exam_id=e.id) AS n_captures
                   FROM exams e WHERE e.patient_id=?
                   ORDER BY e.exam_date DESC, e.id DESC""",
                (pid,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def ensure_for_date(self, pid: int, exam_date: str) -> int:
        """그날 검사가 있으면 가장 최근 것을, 없으면 새로 만들어 id 를 돌려준다."""
        with self._lock, self._c:
            r = self._c.execute(
                "SELECT id FROM exams WHERE patient_id=? AND exam_date=? ORDER BY id DESC LIMIT 1",
                (pid, exam_date),
            ).fetchone()
            if r is not None:
                return r[0]
            cur = self._c.execute(
                "INSERT INTO exams (patient_id, exam_date, title) VALUES (?,?,?)",
                (pid, exam_date, DEFAULT_TITLE),
            )
            return cur.lastrowid

    def delete(self, eid: int) -> List[dict]:
        """검사와 그 캡처 레코드를 지우고, 디스크 정리용 경로 목록을 반환한다.

        다른 기록(다른 검사 · 다른 환자)이 아직 같은 파일을 가리키면 그 경로는 None 으로 돌려준다 — 지우지 않게.
        """
        with self._lock, self._c:
            rows = self._c.execute(
                "SELECT file_path, export_path, thumbnail_path FROM captures WHERE exam_id=?", (eid,)
            ).fetchall()
            self._c.execute("DELETE FROM captures WHERE exam_id=?", (eid,))
            self._c.execute("DELETE FROM exams WHERE id=?", (eid,))
            out = []
            for r in rows:
                row = dict(r)
                for key in ("file_path", "thumbnail_path"):
                    p = row.get(key)
                    if p and self._c.execute(
                            "SELECT 1 FROM captures WHERE file_path=? OR thumbnail_path=? LIMIT 1", (p, p)).fetchone():
                        row[key] = None
                out.append(row)
            return out

    @staticmethod
    def _row(r) -> Optional[Exam]:
        if r is None:
            return None
        keys = r.keys()
        e = Exam(**{k: r[k] for k in keys if k != "n_captures"})
        if "n_captures" in keys:
            e.n_captures = r["n_captures"]
        return e

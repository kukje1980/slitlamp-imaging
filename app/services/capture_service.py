"""캡처 오케스트레이션: 기본 저장 + 썸네일 + export 폴더 + DB 한 곳에서 처리.

요구사항 핵심: 사진/동영상 저장 시 ① 환자별 폴더에 기본 저장,
② 지정 export 폴더에도 복사, ③ DB(captures)에 레코드 삽입.
export 실패는 기본 저장/DB를 막지 않으며 export_path=NULL로 기록한다.

안전성:
- 이미지는 메모리에서 인코딩한 뒤 임시 파일에 쓰고 os.replace 로 바꿔 넣는다
  → 저장 중 꺼져도 반쯤 쓰인 파일이 남지 않고, Windows 한글 경로(cv2.imwrite 가 못 쓰는)도 안전하다.
- 같은 밀리초에 두 번 저장되어도 (4분할 저장 등) 기존 파일을 덮어쓰지 않고 새 이름을 쓴다.
  이름은 새 파일로 먼저 잡아 두므로('x' 모드) 여러 스레드가 동시에 저장해도 겹치지 않는다.
- 저장 전에 디스크 여유 공간을 확인해 공간 부족을 미리 알린다.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from app import paths
from app.capture.frame_utils import make_thumbnail
from app.models.capture import Capture
from app.models.patient import Patient
from app.models.ref_image import RefImage

try:
    import cv2
except ImportError:
    cv2 = None

log = logging.getLogger(__name__)

MIN_FREE_MB_PHOTO = 50          # 사진 한 장 저장에 필요한 최소 여유 공간
MIN_FREE_MB_VIDEO = 500         # 녹화 시작에 필요한 최소 여유 공간 (1080p H.264 수 분 분량)
LOW_SPACE_WARN_MB = 2048        # 이보다 적으면 시작 시 경고


class DiskFullError(RuntimeError):
    """저장 폴더의 디스크 여유 공간이 부족하다."""


def free_space_mb(path) -> Optional[float]:
    """path(또는 가장 가까운 존재하는 상위 폴더)가 있는 드라이브의 여유 공간(MB).
    알 수 없으면 None."""
    p = Path(path)
    while not p.exists():
        if p.parent == p:
            return None
        p = p.parent
    try:
        return shutil.disk_usage(p).free / (1024 * 1024)
    except OSError:
        return None


def ensure_free_space(path, min_mb: float, what: str = "저장") -> None:
    free = free_space_mb(path)
    if free is not None and free < min_mb:
        raise DiskFullError(f"{what} 폴더의 디스크 여유 공간이 부족합니다 ({free:.0f}MB 남음, "
                            f"최소 {min_mb:.0f}MB 필요).\n{path}")


def reserve_unique(path: Path) -> Path:
    """아직 없는 이름(path, _1, _2 …)을 빈 새 파일로 잡아 두고 돌려준다 — 원자적이라
    여러 스레드가 같은 순간 같은 이름을 골라도 서로 덮어쓰지 않는다."""
    for cand in (path, *(path.with_name(f"{path.stem}_{i}{path.suffix}") for i in range(1, 1000))):
        try:
            with open(cand, "xb"):
                return cand
        except FileExistsError:
            continue
    raise RuntimeError(f"저장 파일 이름을 만들 수 없습니다: {path}")


def unique_path(path: Path) -> Path:
    """이미 있는 파일이면 _1, _2 … 를 붙여 덮어쓰지 않는 경로를 돌려준다."""
    if not path.exists():
        return path
    for i in range(1, 1000):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"저장 파일 이름을 만들 수 없습니다: {path}")


class CaptureService:
    def __init__(self, db, config):
        self.db = db
        self.config = config

    # ---- 사진 ----
    def save_photo(self, frame: np.ndarray, patient: Patient,
                   eye: str = "NA", note: str = "", exam_id: Optional[int] = None) -> Capture:
        if frame is None:
            raise ValueError("저장할 프레임이 없습니다.")
        if patient is None or patient.id is None:
            raise ValueError("환자가 선택되지 않았습니다.")
        ts = datetime.now()
        ext = (self.config.photo_format or "png").lower()
        if ext not in ("png", "jpg", "jpeg"):
            ext = "png"
        primary_dir = self._patient_dir(self.config.media_root, patient, ts)
        paths.ensure_dir(primary_dir)
        ensure_free_space(primary_dir, MIN_FREE_MB_PHOTO, "사진 저장")
        primary_path = reserve_unique(primary_dir / self._filename(ts, eye, ext))
        fname = primary_path.name
        try:
            self._imwrite(primary_path, frame, ext)
        except BaseException:
            primary_path.unlink(missing_ok=True)     # 잡아 둔 빈 파일을 남기지 않는다
            raise

        thumb_path = self._write_thumbnail(primary_dir, primary_path.stem, frame)
        export_path = self._export(primary_path, patient, ts, fname)

        cap = Capture(
            patient_id=patient.id, media_type="photo",
            file_path=str(primary_path),
            export_path=(str(export_path) if export_path else None),
            thumbnail_path=(str(thumb_path) if thumb_path else None),
            captured_at=ts.strftime("%Y-%m-%d %H:%M:%S"),
            eye=eye, duration_ms=None, note=note,
            exam_id=self._exam_for(patient, ts, exam_id),
        )
        cap.id = self._insert_record(cap)
        log.info("photo saved id=%s path=%s export=%s", cap.id, primary_path, export_path)
        return cap

    # ---- 동영상 ----
    def begin_video(self, patient: Patient, eye: str = "NA") -> Tuple[Path, datetime]:
        """녹화기가 직접 쓸 대상 경로를 만들어 반환한다."""
        if patient is None or patient.id is None:
            raise ValueError("환자가 선택되지 않았습니다.")
        ts = datetime.now()
        primary_dir = self._patient_dir(self.config.media_root, patient, ts)
        paths.ensure_dir(primary_dir)
        ensure_free_space(primary_dir, MIN_FREE_MB_VIDEO, "녹화")
        return unique_path(primary_dir / self._filename(ts, eye, "mp4")), ts

    def finish_video(self, video_path, patient: Patient, eye: str, ts: datetime,
                     duration_ms: int, thumb_frame: Optional[np.ndarray] = None,
                     note: str = "", exam_id: Optional[int] = None) -> Capture:
        video_path = Path(video_path)
        if not video_path.exists() or video_path.stat().st_size == 0:
            raise RuntimeError(f"녹화 파일이 비어 있거나 없습니다: {video_path}")
        thumb_path = None
        if thumb_frame is not None:
            thumb_path = self._write_thumbnail(video_path.parent, video_path.stem, thumb_frame)
        export_path = self._export(video_path, patient, ts, video_path.name)

        cap = Capture(
            patient_id=patient.id, media_type="video",
            file_path=str(video_path),
            export_path=(str(export_path) if export_path else None),
            thumbnail_path=(str(thumb_path) if thumb_path else None),
            captured_at=ts.strftime("%Y-%m-%d %H:%M:%S"),
            eye=eye, duration_ms=duration_ms, note=note,
            exam_id=self._exam_for(patient, ts, exam_id),
        )
        cap.id = self._insert_record(cap)
        log.info("video saved id=%s path=%s export=%s", cap.id, video_path, export_path)
        return cap

    # ---- 가져오기 / 참조 영상 (Explorer 판) ----
    def import_image(self, src_path, patient: Patient, eye: str = "NA",
                     exam_id: Optional[int] = None) -> Capture:
        """외부 사진 파일(다른 장비·이전 프로그램)을 환자 영상으로 가져온다."""
        frame = read_image(src_path)
        if frame is None:
            raise ValueError(f"이미지를 읽을 수 없습니다: {src_path}")
        return self.save_photo(frame, patient, eye=eye, note="가져옴", exam_id=exam_id)

    def add_reference(self, capture: Capture, category: str, title: str = "",
                      description: str = "") -> RefImage:
        """영상을 참조 라이브러리에 복사해 등록 (원본 캡처를 지워도 참조 영상은 남는다)."""
        frame = read_image(capture.file_path)
        if frame is None:
            raise ValueError("참조로 등록할 이미지를 읽을 수 없습니다.")
        lib = paths.ensure_dir(Path(self.config.media_root).parent / "reference_library"
                               / paths.sanitize(category or "기타"))
        ts = datetime.now()
        stem = f"{ts:%Y%m%d_%H%M%S}_{ts.microsecond // 1000:03d}"
        path = lib / f"{stem}.png"
        self._imwrite(path, frame, "png")
        thumb = self._write_thumbnail(lib, stem, frame)
        ref = RefImage(file_path=str(path), thumbnail_path=(str(thumb) if thumb else None),
                       category=category or "기타", title=title or None,
                       description=description or None, source_capture_id=capture.id)
        ref.id = self.db.references.insert(ref)
        return ref

    # ---- 삭제 ----
    def delete_capture(self, capture) -> bool:
        """영상 한 건 삭제: DB 레코드와 기본 저장 파일·썸네일을 지운다.

        지정 폴더(export)에 복사된 사본은 백업으로 남겨 둔다 (Explorer 의 삭제와 같은 규칙).
        파일 삭제 실패(이미 없음·잠김)는 경고만 남기고 레코드 삭제는 그대로 둔다.
        반환값은 레코드가 실제로 있었는지 여부.
        """
        cid = getattr(capture, "id", capture)
        row = self.db.captures.delete(cid)
        if row is None:
            return False
        for key in ("file_path", "thumbnail_path"):
            path = row.get(key)
            if not path:
                continue
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                log.warning("파일 삭제 실패 (%s): %s", key, path, exc_info=True)
        log.info("capture deleted id=%s path=%s", cid, row.get("file_path"))
        return True

    def delete_captures(self, captures) -> int:
        """여러 영상 삭제. 지워진 레코드 수를 돌려준다."""
        return sum(1 for c in list(captures) if self.delete_capture(c))

    def captures_on(self, patient_id: int, day: str) -> list:
        """한 환자의 특정 촬영일('YYYY-MM-DD') 영상(사진 · 동영상) 목록, 촬영 순서."""
        return [c for c in self.db.captures.list_by_patient(patient_id, order="asc")
                if (c.captured_at or "")[:10] == day]

    def delete_day(self, patient_id: int, day: str) -> int:
        """그 날 촬영한 영상을 모두 삭제 (레코드 · 기본 저장 파일 · 썸네일, export 사본은 보존).

        검사 소견(exam_notes)은 임상 기록이라 남겨 둔다. 지워진 장수를 돌려준다.
        """
        return self.delete_captures(self.captures_on(patient_id, day))

    def low_space_warning(self) -> Optional[str]:
        """시작 시 상태바에 보여줄 디스크 여유 공간 경고 (문제 없으면 None)."""
        free = free_space_mb(self.config.media_root)
        if free is not None and free < LOW_SPACE_WARN_MB:
            return f"저장 드라이브 여유 공간 부족: {free / 1024:.1f}GB 남음 — 공간을 확보하세요"
        return None

    # ---- 내부 헬퍼 ----
    def _insert_record(self, cap: Capture) -> int:
        """DB 기록. 실패해도 파일은 지우지 않고 (영상은 이미 디스크에 있음)
        경로를 로그에 남겨 복구할 수 있게 한다."""
        try:
            return self.db.captures.insert(cap)
        except Exception:
            log.error("DB 기록 실패 — 파일은 보존됨: %s (export=%s)",
                      cap.file_path, cap.export_path)
            raise

    def _exam_for(self, patient: Patient, ts: datetime, exam_id: Optional[int]) -> Optional[int]:
        """검사를 고르지 않았으면 그날 검사(없으면 새로 만듦)에 넣는다."""
        if exam_id:
            return exam_id
        exams = getattr(self.db, "exams", None)
        if exams is None or patient.id is None:
            return None
        return exams.ensure_for_date(patient.id, ts.strftime("%Y-%m-%d"))

    def _patient_dir(self, root, patient, ts) -> Path:
        return paths.patient_subdir(Path(root), patient, self.config.subfolder_template, ts)

    @staticmethod
    def _filename(ts: datetime, eye: str, ext: str) -> str:
        e = paths.sanitize(eye or "NA", 8)
        return f"{ts:%H%M%S}_{ts.microsecond // 1000:03d}_{e}.{ext}"

    def _write_thumbnail(self, parent_dir: Path, stem: str, frame) -> Optional[Path]:
        try:
            thumb_dir = paths.ensure_dir(Path(parent_dir) / ".thumbs")
            thumb_path = thumb_dir / f"{stem}.jpg"
            self._imwrite(thumb_path, make_thumbnail(frame, self.config.thumbnail_max_side), "jpg")
            return thumb_path
        except Exception:  # noqa: BLE001
            log.warning("thumbnail 생성 실패", exc_info=True)
            return None

    def _export(self, primary_path: Path, patient, ts, fname) -> Optional[Path]:
        if not self.config.export_root:
            return None
        try:
            export_dir = self._patient_dir(self.config.export_root, patient, ts)
            paths.ensure_dir(export_dir)
            return _copy_new(primary_path, export_dir / fname)
        except Exception:  # noqa: BLE001
            log.warning("export 복사 실패 (export_path=NULL로 기록)", exc_info=True)
            return None

    def _imwrite(self, path: Path, frame, ext: str) -> None:
        """메모리에서 인코딩 → 임시 파일 → os.replace.
        (cv2.imwrite 는 Windows 한글 경로에서 실패하고, 쓰는 도중 꺼지면 깨진 파일이 남는다.)"""
        path = Path(path)
        ext = ext.lower()
        if cv2 is not None:
            params = []
            if ext in ("jpg", "jpeg"):
                params = [cv2.IMWRITE_JPEG_QUALITY, 95]
            ok, buf = cv2.imencode(f".{ext}", np.ascontiguousarray(frame), params)
            if not ok:
                raise RuntimeError(f"이미지 인코딩 실패 ({ext}): {path}")
            data = buf.tobytes()
        else:
            import io

            import imageio
            rgb = frame
            if frame.ndim == 3 and frame.shape[2] >= 3:  # BGR(A) → RGB(A) (흑백은 그대로 — 좌우가 뒤집히지 않게)
                rgb = frame[..., [2, 1, 0, *range(3, frame.shape[2])]]
            bio = io.BytesIO()
            imageio.imwrite(bio, np.ascontiguousarray(rgb), extension=f".{ext}")
            data = bio.getvalue()
        atomic_write_bytes(path, data)


def _copy_new(src: Path, dest: Path) -> Path:
    """dest(또는 _1, _2 …) 새 파일로 복사 — 기존 사본을 덮어쓰지 않고, 복사가 끊기면(NAS 끊김)
    반쯤 복사된 파일을 남기지 않는다."""
    dest = reserve_unique(dest)
    try:
        with open(src, "rb") as inp, open(dest, "wb") as out:
            shutil.copyfileobj(inp, out, 1024 * 1024)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    try:
        shutil.copystat(src, dest)               # 촬영 시각 보존 — 일부 NAS 는 거부하지만 사본 자체는 유효
    except OSError:
        pass
    return dest


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """임시 파일에 다 쓰고 fsync 한 뒤 최종 이름으로 바꾼다 — 반쯤 쓰인 파일이 남지 않는다."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.part")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def read_image(path) -> Optional[np.ndarray]:
    """BGR 이미지 읽기 (한글 경로 대응)."""
    if not path or cv2 is None or not Path(path).is_file():
        return None
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)

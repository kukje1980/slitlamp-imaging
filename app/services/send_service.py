"""전송: 고른 사진을 폴더 · 클라우드(동기화 폴더) · PACS(DICOM 네트워크)로 보낸다.

방법(send_method)
    folder  지정한 폴더 (네트워크 공유 폴더 포함)
    cloud   OneDrive · Google Drive · Dropbox · iCloud 등 PC 에 설치된 동기화 폴더 (그 앱이 올려 준다)
    pacs    DICOM C-STORE 로 PACS 서버에 직접 전송 (DICOM 형식만)
형식(send_formats, 여러 개 가능)
    original 원본 그대로 (사진 · 동영상)   jpg JPG 로 변환   pdf 사진 + 환자 정보 + 소견란 보고서 1부   dicom DICOM(.dcm)
폴더 · 클라우드는 <대상>/<차트번호>_<이름>/<날짜>/ 아래에 저장한다.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

log = logging.getLogger(__name__)

METHODS = [("folder", "폴더"), ("cloud", "클라우드 (동기화 폴더)"), ("pacs", "PACS (DICOM 네트워크)")]
FORMATS = [("original", "원본 사진 · 동영상"), ("jpg", "JPG 사진"), ("pdf", "PDF 보고서"), ("dicom", "DICOM 파일")]
_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


@dataclass
class SendResult:
    sent: list = field(default_factory=list)       # 만든 파일 경로 또는 'PACS: ...'
    skipped: list = field(default_factory=list)    # (이름, 까닭)
    errors: list = field(default_factory=list)     # 오류 글

    @property
    def ok(self) -> bool:
        return bool(self.sent) and not self.errors

    def summary(self) -> str:
        lines = [f"{len(self.sent)}개를 보냈습니다."]
        if self.skipped:
            lines.append(f"건너뜀 {len(self.skipped)}개: " + ", ".join(f"{n}({w})" for n, w in self.skipped[:4]))
        if self.errors:
            lines.append("오류: " + " / ".join(self.errors[:3]))
        return "\n".join(lines)


def cloud_candidates() -> list[Path]:
    """이 PC 에서 찾은 클라우드 동기화 폴더들 (있는 것만)."""
    home = Path.home()
    names = [home / "OneDrive", home / "Google Drive", home / "Dropbox", home / "iCloudDrive",
             home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"]
    for env in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        if os.environ.get(env):
            names.insert(0, Path(os.environ[env]))
    cs = home / "Library" / "CloudStorage"                       # macOS: OneDrive-·GoogleDrive-·Dropbox
    if cs.is_dir():
        names += sorted(p for p in cs.iterdir() if p.is_dir())
    out, seen = [], set()
    for p in names:
        try:
            if p.is_dir() and str(p) not in seen:
                seen.add(str(p))
                out.append(p)
        except OSError:
            continue
    return out


def _safe(s: str, max_bytes: int = 90) -> str:
    """폴더 · 파일 이름 한 조각. 아주 긴 한글 이름 · 차트번호도 이름 한 칸 255바이트(맥 · 리눅스)를 넘지 않게 자른다
    (한글 1자 = 3바이트, 차트번호_이름 · 차트번호_시각_눈_번호.확장자 로 이어 붙여도 남게)."""
    s = _UNSAFE.sub("_", (s or "").strip()).strip("_")
    s = s.encode("utf-8")[:max_bytes].decode("utf-8", "ignore").rstrip("._ ")
    return s or "_"


def patient_dir(root, patient, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    return Path(root) / f"{_safe(patient.chart_no or 'noChart')}_{_safe(patient.name)}" / when.strftime("%Y-%m-%d")


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    return path


def _stamp(cap) -> str:
    try:
        return datetime.strptime(cap.captured_at, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d_%H%M%S")
    except (TypeError, ValueError):
        return datetime.now().strftime("%Y%m%d_%H%M%S")


def _base_name(patient, cap) -> str:
    eye = cap.eye if cap.eye in ("OD", "OS", "OU") else "NA"
    return f"{_safe(patient.chart_no or 'patient')}_{_stamp(cap)}_{eye}_{cap.id}"


def save_to_folder(captures: list, patient, folder) -> list[Path]:
    """고른 사진·동영상 원본을 폴더에 복사 (이름: 차트번호_촬영시각_안구_번호, 같은 이름이 있으면 _2 …)."""
    out_dir = Path(folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for cap in captures:
        src = Path(cap.file_path or "")
        if not src.is_file():
            continue
        dst = _unique(out_dir / f"{_base_name(patient, cap)}{src.suffix.lower()}")
        shutil.copy2(src, dst)
        saved.append(dst)
    return saved


def load_bgr(path):
    if not path or cv2 is None or not os.path.exists(path):
        return None
    data = np.fromfile(path, dtype=np.uint8)               # 한글 경로 대응
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def _write_jpg(img, path: Path) -> None:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise OSError("JPG 변환 실패")
    path.parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(path))


def destination(config) -> str:
    method = getattr(config, "send_method", "folder")
    if method == "cloud":
        return getattr(config, "send_cloud_folder", "") or ""
    if method == "pacs":
        return f"{config.send_pacs_host}:{config.send_pacs_port} ({config.send_pacs_ae})"
    return getattr(config, "send_folder", "") or ""


def send(captures: list, patient, config, formats: list | None = None, notes: str = "",
         pdf_writer=None) -> SendResult:
    """captures 를 config 의 전송 방법으로 보낸다. pdf_writer(path, patient, items, notes) 는 PDF 를 만드는 함수(화면 쪽에서 넘김)."""
    res = SendResult()
    method = getattr(config, "send_method", "folder")
    fmts = list(formats if formats is not None else getattr(config, "send_formats", ["original"]))
    if not captures:
        res.errors.append("보낼 사진이 없습니다")
        return res
    if method == "pacs":
        return _send_pacs(captures, patient, config, res)
    root = destination(config)
    if not root:
        res.errors.append("설정에서 전송할 폴더를 먼저 고르세요")
        return res
    if not fmts:
        res.errors.append("설정에서 파일 형식을 하나 이상 고르세요")
        return res
    try:
        out_dir = patient_dir(root, patient)
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        res.errors.append(f"폴더를 만들 수 없습니다: {e}")
        return res
    pdf_items = []
    for cap in captures:
        base = _base_name(patient, cap)
        is_video = cap.media_type == "video"
        img = None if is_video else load_bgr(cap.file_path)
        for f in fmts:
            try:
                if f == "original":
                    if not os.path.exists(cap.file_path):
                        res.skipped.append((base, "원본 없음"))
                        continue
                    dst = _unique(out_dir / (base + Path(cap.file_path).suffix.lower()))
                    shutil.copy2(cap.file_path, dst)
                    res.sent.append(str(dst))
                elif f == "jpg":
                    if img is None:
                        res.skipped.append((base, "동영상은 JPG 제외" if is_video else "사진을 읽지 못함"))
                        continue
                    dst = _unique(out_dir / (base + ".jpg"))
                    _write_jpg(img, dst)
                    res.sent.append(str(dst))
                elif f == "dicom":
                    if img is None:
                        res.skipped.append((base, "동영상은 DICOM 제외" if is_video else "사진을 읽지 못함"))
                        continue
                    from app.analysis import dicom_export
                    dst = _unique(out_dir / (base + ".dcm"))
                    dicom_export.export(img, patient, dst, capture=cap,
                                        institution=getattr(config, "clinic_name", "") or "")
                    res.sent.append(str(dst))
                elif f == "pdf":
                    if img is None:                  # PDF 에 넣지 못한 것은 건너뜀으로 적는다 — 안 적으면 '모두 보냄' 으로
                        res.skipped.append((base, "동영상은 PDF 제외" if is_video else "사진을 읽지 못함"))  # 보여 전송(삭제)가 지운다
                        continue
                    pdf_items.append((img, cap))
            except Exception as e:  # noqa: BLE001
                log.exception("send %s %s failed", f, base)
                res.errors.append(f"{base}.{f}: {e}")
    if "pdf" in fmts:
        if not pdf_items:
            res.skipped.append(("PDF", "사진이 없음"))
        elif pdf_writer is None:
            res.skipped.append(("PDF", "PDF 를 만들 수 없음"))
        else:
            dst = _unique(out_dir / f"{_safe(patient.chart_no or 'patient')}_{datetime.now():%Y%m%d_%H%M%S}_보고서.pdf")
            try:
                pdf_writer(str(dst), patient, pdf_items, notes)
                res.sent.append(str(dst))
            except Exception as e:  # noqa: BLE001
                log.exception("send pdf failed")
                res.errors.append(f"PDF: {e}")
    return res


def _send_pacs(captures, patient, config, res: SendResult) -> SendResult:
    host = (getattr(config, "send_pacs_host", "") or "").strip()
    port = int(getattr(config, "send_pacs_port", 104) or 104)
    called = (getattr(config, "send_pacs_ae", "") or "PACS").strip()[:16]
    calling = (getattr(config, "send_pacs_calling_ae", "") or "SLITLAMP").strip()[:16]
    if not host:
        res.errors.append("설정에서 PACS 주소(호스트)를 먼저 입력하세요")
        return res
    try:
        from pynetdicom import AE
        from app.analysis import dicom_export
    except ImportError as e:
        res.errors.append(f"DICOM 전송 모듈을 불러오지 못했습니다: {e}")
        return res
    datasets = []
    for cap in captures:
        img = None if cap.media_type == "video" else load_bgr(cap.file_path)
        if img is None:
            res.skipped.append((_base_name(patient, cap), "동영상 · 사진 없음"))
            continue
        datasets.append((cap, dicom_export.build_dataset(img, patient, cap,
                                                         institution=getattr(config, "clinic_name", "") or "")))
    if not datasets:
        res.errors.append("PACS 로 보낼 사진이 없습니다")
        return res
    ae = AE(ae_title=calling)
    ae.add_requested_context(dicom_export.OP_8BIT, "1.2.840.10008.1.2.1")   # Explicit VR Little Endian
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = 15
    assoc = ae.associate(host, port, ae_title=called)
    if not assoc.is_established:
        res.errors.append(f"PACS({host}:{port}, {called}) 에 연결하지 못했습니다")
        return res
    try:
        for cap, ds in datasets:
            status = assoc.send_c_store(ds)
            code = getattr(status, "Status", None) if status else None
            if code == 0x0000:
                res.sent.append(f"PACS: {_base_name(patient, cap)}")
            else:
                res.errors.append(f"{_base_name(patient, cap)}: PACS 응답 {hex(code) if code is not None else '없음'}")
    finally:
        assoc.release()
    return res


def test_pacs(config) -> tuple[bool, str]:
    """PACS 연결 확인 (C-ECHO)."""
    host = (getattr(config, "send_pacs_host", "") or "").strip()
    port = int(getattr(config, "send_pacs_port", 104) or 104)
    called = (getattr(config, "send_pacs_ae", "") or "PACS").strip()[:16]
    calling = (getattr(config, "send_pacs_calling_ae", "") or "SLITLAMP").strip()[:16]
    if not host:
        return False, "PACS 주소를 입력하세요"
    try:
        from pynetdicom import AE
    except ImportError as e:
        return False, f"DICOM 전송 모듈 없음: {e}"
    ae = AE(ae_title=calling)
    ae.add_requested_context("1.2.840.10008.1.1")                  # Verification
    ae.acse_timeout = ae.dimse_timeout = ae.network_timeout = 8
    assoc = ae.associate(host, port, ae_title=called)
    if not assoc.is_established:
        return False, f"{host}:{port} ({called}) 에 연결하지 못했습니다"
    try:
        st = assoc.send_c_echo()
        ok = bool(st) and getattr(st, "Status", None) == 0x0000
    finally:
        assoc.release()
    return ok, "연결 성공" if ok else "PACS 가 응답하지 않았습니다"

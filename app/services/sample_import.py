"""질환별 샘플 사진 가져오기.

외부에서 모은 참고 사진 폴더(질환별 하위 폴더 + metadata.json)를 읽어
질환마다 '샘플' 환자를 하나 만들고 사진을 그 환자의 촬영 영상으로 등록한다.

    <폴더 또는 .zip>/
        metadata.json                 # [{file, title, description, author, license, source, width, height, sha256}, ...]
        00_정상대조/정상각막_1.jpg
        01_건성안/...

- 사진은 원본 그대로 복사한다 (변경 금지 라이선스 — 색·크기·워터마크를 건드리지 않음).
- 사진 파일은 저장소·설치파일에 넣지 않는다. 각 PC에서 받아 둔 폴더를 한 번 가져오는 방식.
- 원문 진단·출처·라이선스는 영상 코멘트에, 질환 설명은 환자 메모에 남긴다. 등급은 붙이지 않는다.
- 같은 폴더를 다시 가져와도 이미 등록된 사진(내용이 같은 파일)은 건너뛴다.
- metadata.json 의 sha256 과 다른 파일은 손상·변조로 보고 등록하지 않는다.
- 가져온 샘플 사진은 Explorer 판 참조 영상 라이브러리에 질환별로 바로 보인다 (sample_references — 복사하지 않음).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from app import paths
from app.capture.frame_utils import make_thumbnail
from app.models.capture import Capture
from app.models.patient import Patient
from app.models.ref_image import RefImage

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

log = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
CHART_PREFIX = "SAMPLE-"
LOW_RES_SIDE = 1000          # 긴 변이 이보다 작으면 '저해상도 참고용'
PATIENT_MEMO = ("외부 샘플 사진 (교육·참고용, 실제 환자 아님). 진단은 각 사진의 원문 기준이며 "
                "등급을 따로 붙이지 않았습니다. 사진별 출처·라이선스는 영상 코멘트에 있습니다.")

# 알려진 폴더 → 화면에 보일 질환 이름 (모르는 폴더는 이름에서 번호를 떼고 그대로 씀)
KNOWN_GROUPS: Dict[str, str] = {
    "정상대조": "정상 대조",
    "건성안": "건성안",
    "안검염_MGD": "안검염·마이봄샘 기능장애(MGD)",
    "결막염": "결막염 (알레르기·바이러스)",
    "백내장": "백내장",
    "다래끼_콩다래끼": "다래끼·콩다래끼",
    "결막하출혈": "결막하출혈",
    "익상편_검열반": "익상편·검열반",
    "각막미란_이물": "각막찰과상·상피결손·각막이물",
    "감염각막염": "감염성 각막염·각막궤양",
    "백내장수술후_후낭혼탁": "백내장 수술 후 후낭혼탁(PCO)",
}

_GROUP_RE = re.compile(r"^(\d+)[_\-\s]*(.*)$")
_CATEGORY_RE = re.compile(r"Category\(ies\)\s*:\s*([^\n]+)")   # 원문 페이지 분류 (예: Cornea, Trauma)


@dataclass
class SampleImportResult:
    patients_created: int = 0
    patients_reused: int = 0
    photos_imported: int = 0
    duplicates_skipped: int = 0
    mismatched: List[str] = field(default_factory=list)   # sha256 불일치로 건너뛴 파일
    failed: List[str] = field(default_factory=list)       # 읽기·저장 실패
    patient_ids: List[int] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"샘플 환자 {self.patients_created + self.patients_reused}명 "
                 f"(새로 {self.patients_created}명), 사진 {self.photos_imported}장을 등록했습니다."]
        if self.duplicates_skipped:
            lines.append(f"이미 등록된 사진 {self.duplicates_skipped}장은 건너뛰었습니다.")
        if self.mismatched:
            lines.append(f"metadata.json 과 내용이 다른 파일 {len(self.mismatched)}장은 등록하지 않았습니다: "
                         + ", ".join(self.mismatched))
        if self.failed:
            lines.append(f"읽거나 저장하지 못한 파일 {len(self.failed)}장: " + ", ".join(self.failed))
        return "\n".join(lines)


def group_of(folder_name: str) -> tuple[str, str]:
    """'02_안검염_MGD' → ('02', '안검염·마이봄샘 기능장애(MGD)')."""
    m = _GROUP_RE.match(folder_name.strip())
    num, rest = (m.group(1), m.group(2)) if m else ("", folder_name.strip())
    name = KNOWN_GROUPS.get(rest) or rest.replace("_", " · ").strip() or folder_name
    return num, name


def import_samples(db, config, source) -> SampleImportResult:
    """폴더(또는 .zip)를 읽어 질환별 샘플 환자·사진을 등록한다."""
    source = Path(source)
    if source.is_file() and source.suffix.lower() == ".zip":
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(source) as zf:
                _safe_extract(zf, Path(tmp))
            return import_samples(db, config, _content_root(Path(tmp)))
    if not source.is_dir():
        raise FileNotFoundError(f"샘플 사진 폴더를 찾을 수 없습니다: {source}")

    meta = _load_metadata(source)
    entries = _collect_entries(source, meta)
    if not entries:
        raise ValueError("가져올 사진이 없습니다. 질환별 하위 폴더에 사진이 있는지 확인하세요.")

    result = SampleImportResult()
    patients: Dict[str, Patient] = {}
    known_hashes: Dict[int, set] = {}
    now = datetime.now()
    for folder, img_path, info in entries:
        try:
            data = img_path.read_bytes()
        except OSError:
            result.failed.append(img_path.name)
            continue
        digest = hashlib.sha256(data).hexdigest()
        expected = (info.get("sha256") or "").lower()
        if expected and expected != digest:
            log.warning("sample sha256 mismatch: %s", img_path)
            result.mismatched.append(img_path.name)
            continue

        patient = patients.get(folder)
        if patient is None:
            patient, created = _ensure_patient(db, folder)
            patients[folder] = patient
            result.patient_ids.append(patient.id)
            if created:
                result.patients_created += 1
            else:
                result.patients_reused += 1
            known_hashes[patient.id] = _existing_hashes(db, patient.id)
        if digest in known_hashes[patient.id]:
            result.duplicates_skipped += 1
            continue

        try:
            cap = _store(db, config, patient, img_path, data, info, now)
        except Exception:  # noqa: BLE001
            log.warning("sample import failed: %s", img_path, exc_info=True)
            result.failed.append(img_path.name)
            continue
        known_hashes[patient.id].add(digest)
        result.photos_imported += 1
        log.info("sample imported id=%s patient=%s file=%s", cap.id, patient.chart_no, img_path.name)
    return result


# ---- 참조 영상으로 보기 (Explorer 판 참조 영상 라이브러리) ----
BUNDLE_NAME = "일반외래_슬릿램프_질환순"
BUNDLE_ID = "outpatient-26-v1"   # 설치판에 넣은 샘플 묶음 — 바뀌면 다음 실행 때 새 사진만 더 등록


def bundled_samples_dir() -> Optional[Path]:
    """설치판에 들어 있는 질환별 샘플 사진 폴더 (assets/samples/…). 없으면 None."""
    base = getattr(sys, "_MEIPASS", None)          # PyInstaller 번들 안
    root = Path(base) / "assets" if base else Path(__file__).resolve().parents[2] / "assets"
    d = root / "samples" / BUNDLE_NAME
    return d if (d / "metadata.json").is_file() else None


def ensure_bundled_samples(db, config) -> Optional[SampleImportResult]:
    """설치판의 샘플 사진을 처음 한 번만 질환별 샘플 환자로 등록한다.

    등록을 마치면 설정에 묶음 이름을 적어 둔다 — 사용자가 샘플 환자를 지워도 다시 넣지 않는다.
    이미 같은 사진이 있으면(예전에 '샘플' 로 가져옴) 내용 해시로 건너뛴다.
    """
    if getattr(config, "bundled_samples", "") == BUNDLE_ID:
        return None
    src = bundled_samples_dir()
    if src is None:
        return None
    try:
        result = import_samples(db, config, src)
    except (OSError, ValueError):
        log.warning("bundled sample import failed", exc_info=True)
        return None
    if not result.failed:
        config.bundled_samples = BUNDLE_ID
        try:
            config.save()
        except Exception:  # noqa: BLE001
            log.warning("bundled sample flag save failed", exc_info=True)
    log.info("bundled samples: %s", result.summary())
    return result


def is_sample_patient(patient) -> bool:
    return bool(patient is not None and (patient.chart_no or "").startswith(CHART_PREFIX))


def sample_patients(db) -> List[Patient]:
    """샘플 환자 (차트번호 SAMPLE-NN) — 질환 폴더 번호 순."""
    found = [p for p in db.patients.search(CHART_PREFIX, limit=1000) if is_sample_patient(p)]
    return sorted(found, key=lambda p: p.chart_no or "")


def disease_of(patient) -> str:
    return patient.diagnosis or (patient.name or "").replace("샘플 · ", "", 1) or patient.chart_no or "샘플"


def sample_reference(patient, cap) -> RefImage:
    """샘플 사진 1장을 참조 영상 항목으로 (원본 파일을 그대로 가리킴). 코멘트 첫 줄 = 원문 진단, 나머지 = 출처·라이선스."""
    lines = [ln.strip() for ln in (cap.note or "").splitlines() if ln.strip()]
    title = lines[0] if lines else Path(cap.file_path).stem
    if title.startswith("[샘플]"):
        title = title[len("[샘플]"):].strip()
    return RefImage(file_path=cap.file_path, thumbnail_path=cap.thumbnail_path, category=disease_of(patient),
                    title=title, description="\n".join(lines[1:]) or None, source_capture_id=cap.id,
                    created_at=cap.captured_at, source="sample")


def sample_references(db, category: Optional[str] = None, q: str = "") -> List[RefImage]:
    """가져온 샘플 사진을 참조 영상 목록으로. category = 질환 이름, q = 제목·설명·질환 검색."""
    q = (q or "").strip().lower()
    out = []
    for p in sample_patients(db):
        disease = disease_of(p)
        if category and disease != category:
            continue
        for cap in db.captures.list_by_patient(p.id, order="asc"):
            if cap.media_type != "photo":
                continue
            ref = sample_reference(p, cap)
            if q and q not in " ".join(x for x in (ref.title, ref.description, disease) if x).lower():
                continue
            out.append(ref)
    return out


def sample_categories(db) -> List[tuple]:
    """[(질환 이름, 사진 수), ...] — 사진이 있는 샘플 환자만."""
    out = []
    for p in sample_patients(db):
        n = sum(1 for c in db.captures.list_by_patient(p.id) if c.media_type == "photo")
        if n:
            out.append((disease_of(p), n))
    return out


# ---- 내부 ----
def _load_metadata(root: Path) -> Dict[str, dict]:
    p = root / "metadata.json"
    if not p.exists():
        return {}
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("metadata.json 을 읽지 못해 사진만 가져옵니다", exc_info=True)
        return {}
    out = {}
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and it.get("file"):
            out[_nfc(str(it["file"]).replace("\\", "/"))] = it
    return out


def _collect_entries(root: Path, meta: Dict[str, dict]):
    """(질환 폴더 이름, 사진 경로, 메타) 목록 — 폴더 이름순, 그 안에서 파일 이름순."""
    entries = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for img in sorted(sub.iterdir()):
            if img.is_file() and img.suffix.lower() in IMAGE_EXTS:
                rel = _nfc(f"{sub.name}/{img.name}")
                entries.append((_nfc(sub.name), img, meta.get(rel, {})))
    return entries


def _ensure_patient(db, folder: str) -> tuple[Patient, bool]:
    num, name = group_of(folder)
    chart = f"{CHART_PREFIX}{num or paths.sanitize(folder)}"
    for p in db.patients.search(chart):
        if p.chart_no == chart:
            return p, False
    p = Patient(chart_no=chart, name=f"샘플 · {name}", diagnosis=name, memo=PATIENT_MEMO)
    p.id = db.patients.insert(p)
    return p, True


def _existing_hashes(db, patient_id: int) -> set:
    hashes = set()
    for cap in db.captures.list_by_patient(patient_id):
        try:
            hashes.add(hashlib.sha256(Path(cap.file_path).read_bytes()).hexdigest())
        except OSError:
            continue
    return hashes


def _store(db, config, patient: Patient, img_path: Path, data: bytes, info: dict, ts: datetime) -> Capture:
    target_dir = paths.patient_subdir(Path(config.media_root), patient, config.subfolder_template, ts)
    paths.ensure_dir(target_dir)
    target = _unique(target_dir / paths.sanitize(_nfc(img_path.name)))
    target.write_bytes(data)                       # 원본 그대로 (변경 금지)

    frame = _decode(data)
    thumb_path = None
    if frame is not None:
        try:
            thumb_dir = paths.ensure_dir(target_dir / ".thumbs")
            thumb_path = thumb_dir / f"{target.stem}.jpg"
            ok, buf = cv2.imencode(".jpg", make_thumbnail(frame, config.thumbnail_max_side),
                                   [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ok:
                raise RuntimeError("thumbnail encode failed")
            thumb_path.write_bytes(buf.tobytes())
        except Exception:  # noqa: BLE001
            log.warning("sample thumbnail 생성 실패: %s", img_path, exc_info=True)
            thumb_path = None

    h, w = (frame.shape[:2] if frame is not None else (info.get("height"), info.get("width")))
    cap = Capture(
        patient_id=patient.id, media_type="photo", file_path=str(target), export_path=None,
        thumbnail_path=(str(thumb_path) if thumb_path else None),
        captured_at=ts.strftime("%Y-%m-%d %H:%M:%S"), eye="NA", duration_ms=None,
        note=sample_note(info, img_path.stem, w, h),
    )
    cap.id = db.captures.insert(cap)
    return cap


def sample_note(info: dict, fallback_title: str, width=None, height=None) -> str:
    """영상 코멘트: 첫 줄은 원문 진단(썸네일에 보이는 부분), 다음 줄에 출처·라이선스."""
    desc = (info.get("description") or "").strip()
    title = (info.get("title") or fallback_title).strip()
    first = f"[샘플] {desc or title}"
    tags = []
    try:
        if width and height and max(int(width), int(height)) < LOW_RES_SIDE:
            tags.append(f"저해상도 {int(width)}×{int(height)}")
    except (TypeError, ValueError):
        pass
    m = _CATEGORY_RE.search(info.get("source_context") or "")
    if m and "trauma" in m.group(1).lower():
        tags.append("외상")
    if tags:
        first += " (" + ", ".join(tags) + ")"
    lines = [first]
    src = " · ".join(x for x in (info.get("author"), info.get("license")) if x)
    if src:
        lines.append(f"출처: {src}")
    if info.get("source"):
        lines.append(str(info["source"]))
    return "\n".join(lines)


def _decode(data: bytes) -> Optional[np.ndarray]:
    if cv2 is not None:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            return img
    try:
        import io

        from PIL import Image
        rgb = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
        return np.ascontiguousarray(rgb[..., ::-1])
    except Exception:  # noqa: BLE001
        return None


def _nfc(s: str) -> str:
    """macOS 가 한글 이름을 자모로 나눠(NFD) 저장해도 metadata.json 과 맞도록 합쳐 쓴다."""
    return unicodedata.normalize("NFC", s)


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"파일 이름을 정하지 못했습니다: {path}")


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """zip 안의 '../' 같은 경로로 폴더 밖에 쓰지 않도록 하나씩 확인하며 푼다."""
    dest = dest.resolve()
    for info in zf.infolist():
        name = info.filename
        if info.flag_bits & 0x800 == 0:          # UTF-8 표시가 없으면 원래 바이트를 UTF-8 → CP949 순으로 해석
            raw = name.encode("cp437", errors="ignore")
            for enc in ("utf-8", "cp949"):
                try:
                    name = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
        target = (dest / name).resolve()
        if dest not in target.parents and target != dest:
            raise ValueError(f"zip 안에 허용되지 않는 경로가 있습니다: {info.filename}")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _content_root(root: Path) -> Path:
    """zip 이 폴더 하나로 감싸여 있으면 그 안을 가리킨다 (macOS 의 __MACOSX 는 무시)."""
    if (root / "metadata.json").exists():
        return root
    subs = [p for p in root.iterdir() if p.is_dir() and p.name != "__MACOSX" and not p.name.startswith(".")]
    if len(subs) == 1 and not any(f.is_file() and f.suffix.lower() in IMAGE_EXTS for f in subs[0].iterdir()):
        return subs[0]
    return root

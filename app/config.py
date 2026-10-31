"""애플리케이션 설정. %APPDATA%/SlitLampImaging/config.json 에 JSON으로 저장."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from app import paths

CONFIG_FILE = "config.json"
log = logging.getLogger(__name__)


def _agent_edition() -> bool:
    try:
        from app import edition

        return edition.is_agent()
    except Exception:  # noqa: BLE001
        return False


@dataclass
class AppConfig:
    camera_index: int = 0
    camera_name: str = ""                    # 선택한 카메라 이름 (장치 순서가 바뀌어도 이름으로 다시 찾음)
    camera_backend: str = "dshow"            # dshow(Windows; macOS에선 자동으로 AVFoundation) | avfoundation | any
    capture_width: int = 1920
    capture_height: int = 1080
    source_mode: str = "device"              # device | file | synthetic
    test_file_path: str = ""                 # source_mode=file 일 때 재생할 파일
    media_root: str = ""                     # 기본 저장 루트 (비면 appdata/media)
    export_root: str = ""                    # 동시 저장 폴더 (비면 appdata/export)
    db_path: str = ""                        # SQLite 경로 (비면 appdata/slitlamp.db)
    photo_format: str = "png"                # png | jpg
    video_quality: str = "normal"            # 동영상 화질: normal(일반 — 가볍고 파일 작음) | high(고화질) (app/capture/video_recorder.py)
    video_fallback_fourcc: str = "mp4v"      # ffmpeg 실패 시 cv2 폴백 코덱
    subfolder_template: str = "{chart_no}_{patient_id}/{date}"
    thumbnail_max_side: int = 480            # 사진 목록 썸네일 긴 변 (카드 약 230px × 레티나 2배 — 작으면 흐림)
    newest_on_top: bool = True
    split_count: int = 4                     # 분할 화면 칸 수 (2 · 4 · 6 · 8 · 16)
    split_count_saved: bool = False          # True = 칸 수를 한 번 골라 저장함 → 분할 버튼은 묻지 않고 그 칸 수로
    explorer_thumbs_vertical: bool = False   # Explorer 판 영상 목록: False = 아래 가로 · True = 오른쪽 세로
    # 촬영 모드 (자주 쓰는 카메라 · 촬영 설정 묶음) — app/capture/presets.py
    capture_presets: list = field(default_factory=list)   # 저장한 촬영 모드들
    active_preset: str = ""                                # 지금 쓰는 촬영 모드 이름
    # 전송 (app/services/send_service.py)
    send_method: str = "folder"              # folder | cloud | pacs
    send_folder: str = ""                    # 폴더 전송 위치 (네트워크 공유 폴더 가능)
    send_cloud_folder: str = ""              # 클라우드 동기화 폴더 (OneDrive · Google Drive · Dropbox · iCloud)
    send_formats: list = field(default_factory=lambda: ["original"])   # original | jpg | pdf | dicom
    send_pacs_host: str = ""
    send_pacs_port: int = 104
    send_pacs_ae: str = "PACS"               # 받는 쪽(PACS) AE Title
    send_pacs_calling_ae: str = "SLITLAMP"   # 보내는 쪽(이 프로그램) AE Title
    thumbs_split_eyes: bool = False          # 오른쪽 사진 목록을 OD(우안) · OS(좌안) 로 나눠 보기
    theme: str = "dark"                      # dark(기본, 영상 작업 공간) | light
    start_screen: str = "home"               # 시작 화면: home(홈 메뉴) | capture(바로 촬영)
    home_image: str = ""                     # 홈 화면 큰 사진 (비면 브랜드 기본 사진 · 그림)
    sounds_enabled: bool = True              # 효과음 (캡처 찰칵 · 정지영상 · 녹화 시작/중지)
    sound_volume: int = 100                  # 효과음 크기 0~100
    clinic_name: str = ""                    # 인쇄 보고서 머리말 기관명
    px_per_mm: float = 0.0                   # 측정 눈금 보정 (0 = 미보정, px 단위)
    brand: str = ""                          # 화면 브랜드 (비면 기본, "keeler" = Keeler 전용)
    shortcuts: dict = field(default_factory=dict)   # 사용자 지정 단축키 {동작 id: "키"} (app/shortcuts.py)
    usb_buttons: dict = field(default_factory=dict)  # 슬릿램프 USB 버튼 {"1": {"key", "action"}} (app/usb_buttons.py, 비면 기본값)
    usb_eye_auto: bool = True                # 슬릿램프 좌우 신호(F6 → OD · F7 → OS)로 검사눈 자동 인식 (끄면 OD · OS 는 직접)
    start_eye: str = "OD"                    # 프로그램을 켰을 때 미리 골라 둘 눈 OD · OS · NA(미지정) — 좌우 신호가 오면 그대로 바뀜
    usb_device_check: bool = True            # Windows Raw Input 으로 VID 04D9 / PID 1203 장치의 신호만 좌우 신호로 (app/usb_raw_input.py)
    # 백업 (app/services/backup_service.py) — 외장 하드 · 다른 드라이브의 '시작날짜+마지막날짜' 폴더 (예: 210908261028)
    backup_root: str = ""                    # 백업 위치 (비면 백업 안 함)
    backup_auto: bool = True                 # 자동: 저장 즉시 복사(실시간) + 켤 때 · 주기적으로 빠진 파일 확인 (끄면 수동)
    backup_interval_min: int = 60            # 빠진 파일 확인 주기 (분)
    backup_on_exit: bool = True              # 프로그램을 닫을 때 환자 기록 DB · 못 한 파일 마무리
    backup_set_id: str = ""                  # 이 PC 의 지금 백업 폴더 번호 (폴더 안 backup_info.json 의 set_id)
    backup_last_at: str = ""                 # 마지막으로 백업을 마친 시각 (표시용)
    # 스마트 캡처 (Pro LIVE) — app/capture/smart_capture.py
    smart_auto_mode: str = "off"             # off | freeze(초점 맞으면 자동 정지) | save(자동 저장)
    smart_roi_frac: float = 0.5              # 초점 판정 관심 영역 (화면 중앙 비율 0.2~1.0)
    pro_smart_peaking_sensitivity: int = 2       # 1(적게) ~ 3(많이)
    redness_sensitivity: int = 5             # 분석 탭 충혈 민감도 1(가장 붉은 곳만) ~ 10, 5 = 기본 (app/capture/analysis.py)
    # Smart Capture (app/capture/smart_capture.py)
    smart_mode: str = "off"                  # off | freeze(초점 맞는 순간 자동 정지) | snapshot(자동 저장)
    smart_peaking: bool = False              # 포커스 피킹 표시
    smart_peaking_sensitivity: int = 50      # 0~100
    smart_peaking_color: str = "red"         # red | green | yellow | cyan
    smart_roi: list = field(default_factory=list)   # 정규화 [x, y, w, h] (비면 화면 중앙 60%)
    smart_bar_visible: bool = True           # 촬영 화면의 Smart 도구줄 보이기 (숨겨도 자동 촬영 설정은 유지)
    ref_bar_visible: bool = False            # (v1.20.3 부터 쓰지 않음 — 화면 아래 참조 줄 없앰. 예전 설정 파일 호환용)
    multi_side_visible: bool = False         # 멀티 화면 오른쪽 메뉴(보정 · 편집 · 분석) 보이기 (기본: 접힘)
    right_click_items: list = field(default_factory=lambda: ["send", "multi", "delete"])   # 메뉴 항목: send · send_delete · multi · delete
    right_click_action: str = "menu"         # 사진 목록 오른쪽 클릭: menu(전송 · 삭제 · 멀티 메뉴) | multi(멀티 화면 바로)
    live_multi: bool = False                 # 실시간 멀티화면: 사진 목록에서 왼쪽 클릭으로 고르면 바로 가운데 멀티 화면 (다 빼면 라이브로)
    view_after_capture: bool = True          # 사진을 찍으면 큰 화면으로 바로 보기 ('다시 촬영' 으로 촬영 화면 복귀)
    # 촬영 직후 큰 화면 · 멀티 화면에서 캡처(촬영 버튼 · 촬영 단축키 · USB 사진 버튼)를 누르면:
    # reset(초기화 — 보기 창 · 멀티를 닫고 고른 사진 · 참조 · 질환 아이콘을 비워 라이브 촬영 화면으로, 사진은 찍지 않음)
    # | capture(예전처럼 바로 촬영)
    capture_in_view: str = "reset"
    smart_best_shot: bool = True             # 사진 캡처 시 최근 0.6초 중 가장 선명한 프레임 저장
    smart_ready_pct: float = 85.0            # 초점 판정 기준 (최근 최고 선명도 대비 %)
    smart_cooldown_s: float = 2.5            # 자동 촬영 사이 최소 간격(초)
    # AI 에이전트 (app/agent) — 에이전트 에디션에서 기본 켜짐
    agent_enabled: bool = False              # 장면 인식·체크리스트·추천 (agent 에디션은 True)
    agent_auto_capture: bool = True          # 장면 안정 + 초점 → 눈·장면당 한 번 자동 저장
    agent_logging: bool = True               # 사용 이벤트 로그 (익명 세션, 환자 정보 없음)
    agent_protocol: str = "basic"            # basic | cataract | dry_eye | meibo | free
    agent_save_clips: bool = True            # 촬영 전후 클립(mp4) 을 환자 영상으로 함께 저장
    agent_clip_before_s: float = 4.0
    agent_clip_after_s: float = 2.0
    agent_candidates: int = 4                # 추천 프레임 수
    agent_model_path: str = ""               # 장면 모델 경로 (비면 assets/models, 없으면 규칙)
    camera_props: dict = field(default_factory=dict)  # 카메라 속성 {노출·게인·WB…} (app/capture/camera_props.py)
    report_per_page: int = 2                 # 보고서 한 쪽 영상 수 (1 / 2 / 4)
    slideshow_seconds: int = 3               # 슬라이드쇼 넘김 간격(초)
    bundled_samples: str = ""               # 설치판 샘플 사진을 등록한 묶음 이름 (한 번만 등록)

    @classmethod
    def config_path(cls) -> Path:
        return paths.get_appdata_dir() / CONFIG_FILE

    @classmethod
    def load(cls) -> "AppConfig":
        cfg = cls()
        p = cls.config_path()
        data: dict = {}
        if p.exists():
            try:
                # utf-8-sig: 메모장 등이 붙이는 BOM 이 있어도 읽는다 (못 읽으면 저장 폴더가 기본값으로 바뀜)
                data = json.loads(p.read_text(encoding="utf-8-sig"))
                # v0.2 Pro 설정의 1~3 감도를 별도 필드로 이관한다.
                old_sens = data.get("smart_peaking_sensitivity")
                if "smart_mode" not in data and isinstance(old_sens, int) and 1 <= old_sens <= 3:
                    data.setdefault("pro_smart_peaking_sensitivity", old_sens)
                    data["smart_peaking_sensitivity"] = 50
                defaults = cls()
                for f in fields(cls):
                    if f.name not in data:
                        continue
                    v, d = data[f.name], getattr(defaults, f.name)
                    # 형식이 다른 값(예: "camera_index": "1", "shortcuts": [..])은 버리고 기본값 유지
                    if isinstance(d, bool) != isinstance(v, bool):
                        continue
                    if isinstance(d, float) and isinstance(v, int):
                        v = float(v)
                    if isinstance(v, type(d)):
                        setattr(cfg, f.name, v)
                    else:
                        log.warning("config %s 값 형식이 잘못되어 무시: %r", f.name, v)
            except Exception:
                # 손상된 설정은 지우지 않고 옆에 보관한 뒤 기본값으로 시작 (저장 위치 등을 나중에 되살릴 수 있게)
                log.exception("config.json 을 읽지 못했습니다 — config.json.broken 으로 보관")
                try:
                    os.replace(p, p.with_name(p.name + ".broken"))
                except OSError:
                    pass
        cfg._fix_out_of_range()
        cfg._apply_defaults()
        if _agent_edition() and "agent_enabled" not in (data if isinstance(data, dict) else {}):
            cfg.agent_enabled = True             # 에이전트 에디션은 처음부터 켜진 상태로 시작
        return cfg

    def _fix_out_of_range(self) -> None:
        """형식은 맞지만 쓸 수 없는 값(예: photo_format "exe", 크기 0)은 기본값으로 — 촬영이 전부 실패하지 않게."""
        d = type(self)()
        ok = {
            "photo_format": self.photo_format.lower() in ("png", "jpg", "jpeg"),
            "video_quality": self.video_quality in ("normal", "high"),
            "right_click_action": self.right_click_action in ("menu", "multi"),
            "capture_in_view": self.capture_in_view in ("reset", "capture"),
            "right_click_items": (isinstance(self.right_click_items, list) and bool(self.right_click_items)
                                  and all(x in ("send", "send_delete", "multi", "delete") for x in self.right_click_items)),
            "thumbnail_max_side": self.thumbnail_max_side >= 480,   # 예전 기본 120 은 흐려서 480 으로
            "capture_width": self.capture_width > 0,
            "capture_height": self.capture_height > 0,
            "camera_index": self.camera_index >= 0,
            "subfolder_template": _template_ok(self.subfolder_template),
            "px_per_mm": self.px_per_mm >= 0,
        }
        for name, good in ok.items():
            if not good:
                log.warning("config %s 값을 쓸 수 없어 기본값으로: %r", name, getattr(self, name))
                setattr(self, name, getattr(d, name))

    def _apply_defaults(self) -> None:
        base = paths.get_appdata_dir()
        if not self.media_root:
            self.media_root = str(base / "media")
        if not self.export_root:
            self.export_root = str(base / "export")
        if not self.db_path:
            self.db_path = str(base / "slitlamp.db")
        paths.ensure_dir(self.media_root)
        paths.ensure_dir(Path(self.db_path).parent)

    def save(self) -> None:
        p = self.config_path()
        paths.ensure_dir(p.parent)
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        # 임시 파일에 다 쓴 뒤 바꿔치기 — 저장 중 꺼져도 설정 파일이 반쯤 지워지지 않는다
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)


def _template_ok(tpl: str) -> bool:
    if not tpl or not tpl.strip("/\\ ."):
        return False
    try:
        tpl.format(chart_no="c", patient_id="p", name="n", date="d")
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return False
    return True

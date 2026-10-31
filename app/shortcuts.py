"""데스크탑 앱 단축키 (사용자 지정) — 웹의 단축키 설정과 같은 동작 목록.

config.json 의 "shortcuts" 에 {동작 id: "키"} 로 저장한다. 키 문자열은 Qt QKeySequence 형식
(예: "C", "Space", "F9", "Ctrl+Shift+C"). 빈 문자열은 단축키 없음.
"""
from __future__ import annotations

# (id, 이름, 기본 키, 적용 화면: capture = 촬영 화면에서만, all = 모든 화면)
ACTIONS: list[tuple[str, str, str, str]] = [
    ("capture", "사진 캡처", "C", "capture"),
    ("record", "녹화 시작 / 중지", "R", "capture"),
    ("freeze", "정지영상 ↔ 라이브 (4분할 칸 채우기)", "X", "capture"),
    ("quad", "4분할 켜기 / 끄기", "Q", "capture"),
    ("smart", "Smart 자동 촬영 모드 (끄기 → 자동 정지 → 자동 저장)", "S", "capture"),
    ("peaking", "포커스 피킹 켜기 / 끄기", "P", "capture"),
    ("eyeOD", "안구 OD (우안)", "1", "all"),
    ("eyeOS", "안구 OS (좌안)", "2", "all"),
    ("eyeNA", "안구 미지정", "0", "all"),
    ("fullscreen", "전체 화면", "F", "all"),
    ("mode", "촬영 ↔ Pro 검토 전환", "F2", "all"),
    ("patients", "환자 목록", "L", "all"),
    ("newPatient", "신규 환자", "N", "all"),
]
# 기본(촬영) 앱에서는 쓰지 않는 동작 — 단축키를 걸지 않고 단축키 설정 창에도 보이지 않는다 (저장된 값은 그대로 둠).
# "mode" 는 v1.20.3 에서 Pro 검토 화면 입구를 없애 촬영 앱에서는 쓸 곳이 없다 (Explorer 판은 F2 라이브 ↔ 영상 전환으로 계속 씀).
HIDDEN_IN_STANDARD = frozenset({"mode"})
# 화면 안에서 이미 쓰는 키 (Pro: ←/→ 이동, Ctrl+Z/Y 실행 취소, Esc 도구 해제)
RESERVED = {"Esc", "Escape", "Left", "Right", "Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z", "Tab"}


def defaults() -> dict[str, str]:
    return {a[0]: a[2] for a in ACTIONS}


def load(config) -> dict[str, str]:
    keys = defaults()
    saved = getattr(config, "shortcuts", None) or {}
    for k, v in saved.items():
        if k in keys and isinstance(v, str):
            keys[k] = v
    return keys


def label(action_id: str) -> str:
    return next((a[1] for a in ACTIONS if a[0] == action_id), action_id)


def scope(action_id: str) -> str:
    return next((a[3] for a in ACTIONS if a[0] == action_id), "all")

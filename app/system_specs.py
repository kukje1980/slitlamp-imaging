"""컴퓨터 사양 안내 — 최소 · 권장 사양, 동영상 화질 모드 설명, 이 PC 의 사양 확인.

설정 창 '컴퓨터 사양' 탭(app/ui/settings_dialog.py)과 README 가 같은 내용을 쓴다.

기준 측정 (4코어 2.1GHz 서버용 CPU, 그래픽카드 없음, app/capture/video_recorder.py 의 인코더 설정):
  1080p 30fps 녹화 — 일반·고화질 모두 실시간 / 4K 30fps 녹화 — 두 모드 모두 150장 중 150장 기록.
그래서 최소 사양을 '4코어' 로 잡는다. 보통 진료실 PC 는 이보다 코어당 2배쯤 빠르다.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from typing import Optional

# ---- 사양 표 ----
# (항목, 최소 — 1080p 촬영·녹화(일반 화질)가 끊기지 않는 선, 권장 — 4K 촬영·고화질 녹화까지 여유 있게)
SPEC_ROWS = [
    ("운영체제", "Windows 10 64비트(버전 22H2) 또는 Windows 11 · macOS 14(Sonoma) 이상",
     "Windows 11 64비트 · macOS 14 이상"),
    ("CPU", "4코어 이상, 최근 6년 안의 CPU (Intel Core i3 10세대 · AMD Ryzen 3 3000 이상)",
     "6코어 이상 (Intel Core i5 12세대 · AMD Ryzen 5 5600 이상) — 8코어면 더 여유"),
    ("메모리(RAM)", "8GB", "16GB"),
    ("그래픽카드", "내장 그래픽으로 충분 (영상 압축은 CPU 가 함 — 별도 그래픽카드 불필요)",
     "내장 그래픽으로 충분"),
    ("저장장치", "SSD 256GB 이상, 여유 공간 50GB 이상 유지", "NVMe SSD 1TB 이상 + 네트워크(NAS)·외장 백업"),
    ("카메라 연결", "USB 2.0 (1080p 캡처 장비) · 4K 는 USB 3.0", "USB 3.0 이상 포트에 직접 연결 (USB 허브 없이)"),
    ("모니터", "1920×1080", "1920×1080 이상 · 4K 영상을 세밀하게 보려면 27인치 4K"),
]

# 동영상 화질 모드 — (모드, 쓰는 때, 화질, 파일 크기 1080p, 파일 크기 4K, 필요한 PC)
VIDEO_MODE_ROWS = [
    ("일반 (기본)", "진료 기록용 — 가볍고 파일이 작음",
     "표준 화질 (H.264 CRF 23)", "분당 약 20~150MB", "분당 약 100~500MB", "최소 사양 PC 에서도 실시간(30fps)"),
    ("고화질", "세밀한 관찰 · 발표 · 교육 자료",
     "원본과 눈으로 구분이 어려운 수준 (H.264 CRF 18) — 파일 약 2~2.5배",
     "분당 약 50~300MB", "분당 약 300MB~1GB", "최소 사양 PC 에서도 실시간 — 압축 속도 설정은 같고 화질만 다름"),
]
# 웹 버전(브라우저 녹화)은 비트레이트를 고정해서 파일 크기가 일정하다
WEB_VIDEO_ROWS = [
    ("일반", "1080p 5Mbps ≈ 분당 38MB", "4K 20Mbps ≈ 분당 150MB"),
    ("고화질", "1080p 12Mbps ≈ 분당 90MB", "4K 50Mbps ≈ 분당 375MB"),
]

NOTES = [
    "두 화질 모드 모두 실시간(초당 30장)으로 녹화되도록 해상도에 맞는 압축 속도를 씁니다 (1080p veryfast · 4K ultrafast). "
    "모드의 차이는 압축 정도(화질)와 파일 크기입니다.",
    "파일 크기는 영상의 움직임·노이즈에 따라 크게 달라집니다. 긴 녹화 전에는 저장 폴더의 여유 공간을 확인하세요 "
    "(여유 공간이 사진 50MB · 녹화 500MB 아래로 내려가면 저장을 막고 알립니다).",
    "4K 녹화는 압축 대기 메모리를 최대 약 800MB 씁니다. 다른 프로그램을 함께 쓰려면 16GB 를 권장합니다.",
    "측정 기준: 4코어 2.1GHz 서버용 CPU(그래픽카드 없음)에서 1080p·4K 모두 두 모드가 실시간으로 녹화됐습니다. "
    "보통 진료실 PC 는 이보다 빠릅니다.",
]

MIN_CORES, MIN_RAM_GB = 4, 7.5           # 8GB 로 표시되는 PC 는 보통 7.6~7.9GB 로 읽힌다
GOOD_CORES, GOOD_RAM_GB = 6, 15.5
MIN_FREE_GB = 50


# ---- 이 PC 확인 ----
def detect(media_root: Optional[str] = None) -> dict:
    """이 PC 의 운영체제 · CPU 코어 수 · 메모리 · 저장 폴더 여유 공간. 알 수 없는 값은 None."""
    return {
        "os": _os_name(),
        "cores": os.cpu_count(),
        "ram_gb": _ram_gb(),
        "free_gb": _free_gb(media_root),
    }


def verdict(info: dict) -> tuple[str, str]:
    """('good' | 'min' | 'below' | 'unknown', 한 줄 설명)."""
    cores, ram = info.get("cores"), info.get("ram_gb")
    if cores is None or ram is None:
        return "unknown", "CPU·메모리를 자동으로 확인하지 못했습니다. 아래 표와 직접 비교해 보세요."
    if cores >= GOOD_CORES and ram >= GOOD_RAM_GB:
        return "good", "권장 사양을 충족합니다 — 4K 촬영·고화질 녹화까지 여유 있게 쓸 수 있습니다."
    if cores >= MIN_CORES and ram >= MIN_RAM_GB:
        return "min", ("최소 사양을 충족합니다 — 1080p 촬영·녹화(일반 화질)는 문제없이 됩니다. "
                       "4K 고화질 녹화나 다른 프로그램과 함께 쓰려면 메모리 16GB · 6코어 이상을 권합니다.")
    return "below", ("최소 사양에 미치지 못합니다 — 1080p 녹화가 끊기거나 저장이 느릴 수 있습니다. "
                     "사진 캡처는 됩니다.")


def summary_line(info: dict) -> str:
    """'Windows 11 · 8코어 · 메모리 16GB · 저장 폴더 여유 320GB' 꼴."""
    bits = [info.get("os") or "운영체제 확인 불가"]
    bits.append(f"{info['cores']}코어" if info.get("cores") else "코어 수 확인 불가")
    bits.append(f"메모리 {info['ram_gb']:.0f}GB" if info.get("ram_gb") else "메모리 확인 불가")
    if info.get("free_gb") is not None:
        bits.append(f"저장 폴더 여유 {info['free_gb']:.0f}GB")
    return " · ".join(bits)


def _os_name() -> str:
    try:
        if sys.platform.startswith("win"):
            build = int(platform.version().split(".")[-1] or 0)
            return "Windows 11" if build >= 22000 else f"Windows {platform.release()}"
        if sys.platform == "darwin":
            return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
        return f"{platform.system()} {platform.release()}"
    except Exception:  # noqa: BLE001
        return platform.system() or "알 수 없음"


def _ram_gb() -> Optional[float]:
    try:
        if sys.platform.startswith("win"):
            import ctypes

            class _Mem(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _Mem()
            m.dwLength = ctypes.sizeof(_Mem)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.ullTotalPhys / 1024 ** 3
            return None
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3)
            return int(out.stdout.strip()) / 1024 ** 3
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 ** 2
    except Exception:  # noqa: BLE001
        pass
    return None


def _free_gb(path: Optional[str]) -> Optional[float]:
    if not path:
        return None
    try:
        p = path
        while not os.path.exists(p):
            parent = os.path.dirname(p)
            if parent == p:
                return None
            p = parent
        return shutil.disk_usage(p).free / 1024 ** 3
    except OSError:
        return None


# ---- 설정 창용 HTML (Qt 리치 텍스트) ----
def html(info: Optional[dict] = None) -> str:
    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def table(headers, rows):
        head = "".join(f"<th align='left'>{esc(h)}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
        return (f"<table border='1' cellpadding='5' cellspacing='0' width='100%'>"
                f"<tr>{head}</tr>{body}</table>")

    parts = []
    if info is not None:
        kind, text = verdict(info)
        mark = {"good": "✅", "min": "🟡", "below": "⚠️"}.get(kind, "ℹ️")
        parts.append(f"<p><b>이 PC:</b> {esc(summary_line(info))}<br/>{mark} {esc(text)}</p>")
        free = info.get("free_gb")
        if free is not None and free < MIN_FREE_GB:
            parts.append(f"<p>⚠️ 저장 폴더 여유 공간이 {free:.0f}GB 입니다. {MIN_FREE_GB}GB 이상을 유지하세요.</p>")
    parts.append("<h3>컴퓨터 사양</h3>")
    parts.append("<p><b>최소</b>: 1080p 촬영·녹화(일반 화질)가 끊기지 않는 선 &nbsp;·&nbsp; "
                 "<b>권장</b>: 4K 촬영·고화질 녹화까지 여유 있게 — 이 이상이면 편하게 쓸 수 있습니다.</p>")
    parts.append(table(["항목", "최소 사양", "권장 사양"], SPEC_ROWS))
    parts.append("<h3>동영상 화질 모드 (설정 → 카메라 · 저장 → 동영상 화질)</h3>")
    parts.append(table(["모드", "쓰는 때", "화질", "파일 크기 1080p", "파일 크기 4K", "필요한 PC"], VIDEO_MODE_ROWS))
    parts.append("<p>웹 버전(브라우저 녹화)은 비트레이트를 고정해 파일 크기가 일정합니다.</p>")
    parts.append(table(["모드", "1080p", "4K"], WEB_VIDEO_ROWS))
    parts.append("<ul>" + "".join(f"<li>{esc(n)}</li>" for n in NOTES) + "</ul>")
    return "".join(parts)

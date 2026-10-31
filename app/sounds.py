"""효과음 — 사진 캡처(필름 카메라 '찰칵') · 정지영상(스틸 '틱') · 녹화 시작/중지('삐').

음원은 assets/sounds/*.wav (tools/generate_sounds.py 로 합성, 외부 저작물 없음).
재생 백엔드는 OS 에 맞춰 고른다. 어떤 경우에도 예외를 밖으로 내지 않는다
(소리가 촬영을 막아선 안 된다).

    Windows  → winsound (표준 라이브러리, 비동기, 지연 거의 없음)
    그 외    → QtMultimedia QSoundEffect → afplay(macOS) / paplay·aplay(Linux) → 없으면 조용히 생략

사용:  sounds.set_enabled(config.sounds_enabled);  sounds.set_volume(config.sound_volume)
      sounds.play("shutter")

음량(0~100): QSoundEffect·afplay·paplay 는 자체 음량 값을 쓰고, winsound·aplay 처럼 음량 조절이 없는
재생 수단은 샘플을 줄인 WAV 사본(<앱데이터>/cache/sounds/<이름>_<음량>.wav)을 만들어 재생한다.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import sys
import wave
from pathlib import Path

log = logging.getLogger(__name__)

NAMES = ("shutter", "freeze", "rec_start", "rec_stop")
LABELS = {
    "shutter": "사진 캡처 (필름 카메라 찰칵)",
    "freeze": "정지영상 (스틸)",
    "rec_start": "녹화 시작",
    "rec_stop": "녹화 중지",
}

_enabled = True
_volume = 100              # 0~100
_backend = None            # 처음 재생할 때 고른다 (import 시점엔 Qt 앱이 없을 수 있음)


def assets_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)          # PyInstaller 번들 안
    if base:
        return Path(base) / "assets"
    return Path(__file__).resolve().parent.parent / "assets"


def sound_path(name: str) -> Path:
    return assets_dir() / "sounds" / f"{name}.wav"


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


def is_enabled() -> bool:
    return _enabled


def set_volume(pct) -> None:
    global _volume
    try:
        _volume = int(round(float(pct)))
    except (TypeError, ValueError):
        _volume = 100
    _volume = max(0, min(100, _volume))


def get_volume() -> int:
    return _volume


def scale_wav(data: bytes, volume: int) -> bytes:
    """16-bit PCM WAV 의 샘플을 volume(0~100)% 로 줄인 WAV 바이트. 100 이면 그대로."""
    volume = max(0, min(100, int(volume)))
    if volume >= 100:
        return data
    with wave.open(io.BytesIO(data)) as w:
        params = w.getparams()
        frames = w.readframes(w.getnframes())
    if params.sampwidth != 2:
        return data
    import numpy as np

    pcm = np.frombuffer(frames, "<i2").astype(np.int32)
    pcm = (pcm * volume) // 100
    out = io.BytesIO()
    with wave.open(out, "wb") as o:
        o.setparams(params)
        o.writeframes(pcm.astype("<i2").tobytes())
    return out.getvalue()


def _cache_dir() -> Path:
    from app import paths

    return paths.get_appdata_dir() / "cache" / "sounds"


def scaled_path(path: Path, volume: int) -> Path:
    """음량을 적용한 WAV 사본 경로 (없으면 만든다). 100 이면 원본 경로."""
    volume = max(0, min(100, int(volume)))
    if volume >= 100:
        return path
    out = _cache_dir() / f"{path.stem}_{volume}.wav"
    try:
        if not out.exists() or out.stat().st_mtime < path.stat().st_mtime:
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            tmp.write_bytes(scale_wav(path.read_bytes(), volume))
            tmp.replace(out)
    except Exception:  # noqa: BLE001
        log.warning("효과음 음량 사본 생성 실패 — 원본으로 재생", exc_info=True)
        return path
    return out


# ---------------------------------------------------------------- 백엔드
class _WinsoundBackend:
    name = "winsound"

    def __init__(self):
        import winsound  # Windows 전용 — 다른 OS 에선 ImportError

        self._ws = winsound

    def play(self, path: Path, volume: int = 100) -> None:
        ws = self._ws
        # winsound 에는 음량 API 가 없다 (메모리 재생 + 비동기도 불가) → 음량을 적용한 파일 사본
        ws.PlaySound(str(scaled_path(path, volume)),
                     ws.SND_FILENAME | ws.SND_ASYNC | ws.SND_NODEFAULT)


class _QtBackend:
    """QSoundEffect — WAV 를 직접 디코딩하므로 지연이 짧다. 효과 객체는 이름별로 한 번만 만든다."""

    name = "qt"

    def __init__(self):
        from PySide6.QtCore import QCoreApplication, QUrl
        from PySide6.QtMultimedia import QSoundEffect

        if QCoreApplication.instance() is None:
            raise RuntimeError("Qt 애플리케이션이 없습니다")
        self._QUrl, self._Effect = QUrl, QSoundEffect
        self._effects: dict[str, object] = {}

    def _effect(self, path: Path):
        key = str(path)
        eff = self._effects.get(key)
        if eff is None:
            eff = self._Effect()
            eff.setSource(self._QUrl.fromLocalFile(key))
            eff.setVolume(1.0)
            self._effects[key] = eff
        return eff

    def preload(self, paths) -> None:
        for p in paths:
            self._effect(p)

    def play(self, path: Path, volume: int = 100) -> None:
        eff = self._effect(path)
        eff.setVolume(max(0.0, min(1.0, volume / 100.0)))
        eff.play()


class _CommandBackend:
    """외부 재생 명령 (afplay / paplay / aplay) 을 기다리지 않고 띄운다."""

    name = "command"

    def __init__(self):
        cands = ["afplay"] if sys.platform == "darwin" else ["paplay", "aplay"]
        self._cmd = next((shutil.which(c) for c in cands if shutil.which(c)), None)
        if not self._cmd:
            raise RuntimeError("재생 명령 없음")

    def play(self, path: Path, volume: int = 100) -> None:
        name = Path(self._cmd).name
        v = max(0, min(100, int(volume)))
        if name == "afplay":
            args = [self._cmd, "-v", f"{v / 100:.2f}", str(path)]
        elif name == "paplay":
            args = [self._cmd, f"--volume={int(65536 * v / 100)}", str(path)]
        else:                                   # aplay: 음량 옵션 없음 → 사본
            args = [self._cmd, str(scaled_path(path, v))]
        subprocess.Popen(args, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _NullBackend:
    name = "none"

    def play(self, path: Path, volume: int = 100) -> None:
        pass


def _select_backend():
    order = [_WinsoundBackend] if sys.platform.startswith("win") else []
    order += [_QtBackend, _CommandBackend]
    for cls in order:
        try:
            b = cls()
            log.info("효과음 백엔드: %s", b.name)
            return b
        except Exception as e:  # noqa: BLE001 — 다음 후보로
            log.debug("효과음 백엔드 %s 사용 불가: %s", cls.__name__, e)
    log.info("효과음 재생 수단이 없어 소리를 끕니다")
    return _NullBackend()


def backend():
    global _backend
    if _backend is None:
        _backend = _select_backend()
    return _backend


def reset_backend() -> None:
    """(테스트용) 다음 재생 때 백엔드를 다시 고른다."""
    global _backend
    _backend = None


def preload() -> None:
    """앱 시작 뒤 한 번 불러 첫 재생 지연을 줄인다 (백엔드가 지원할 때만)."""
    if not _enabled:
        return
    try:
        b = backend()
        pre = getattr(b, "preload", None)
        if pre is not None:
            pre([sound_path(n) for n in NAMES if sound_path(n).exists()])
    except Exception:  # noqa: BLE001
        log.debug("효과음 미리 읽기 실패", exc_info=True)


def play(name: str, volume=None) -> bool:
    """효과음을 비동기로 재생한다. 실제로 재생을 시도했으면 True (꺼짐·파일 없음·오류면 False).

    volume 을 주면 그 음량(0~100)으로 한 번만 재생한다 (설정 창 '들어보기').
    """
    if not _enabled or name not in NAMES:
        return False
    vol = _volume if volume is None else max(0, min(100, int(volume)))
    if vol <= 0:
        return False
    path = sound_path(name)
    if not path.exists():
        log.warning("효과음 파일이 없습니다: %s", path)
        return False
    try:
        backend().play(path, vol)
        return True
    except Exception:  # noqa: BLE001
        log.warning("효과음 재생 실패: %s", name, exc_info=True)
        return False

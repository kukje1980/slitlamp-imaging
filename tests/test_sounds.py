"""효과음: 음원 파일 · 켜기/끄기 · 백엔드 오류 무시 · 촬영 동작과의 연결."""
from __future__ import annotations

import io
import time
import wave

import pytest
from PySide6.QtWidgets import QApplication

from app import sounds
from app.config import AppConfig
from app.models.patient import Patient


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeBackend:
    name = "fake"

    def __init__(self, fail=False):
        self.played, self.fail = [], fail

    def play(self, path, volume=100):
        if self.fail:
            raise RuntimeError("no audio device")
        self.played.append(path.stem)


@pytest.fixture()
def fake(monkeypatch):
    b = FakeBackend()
    monkeypatch.setattr(sounds, "_backend", b)
    monkeypatch.setattr(sounds, "_enabled", True)
    yield b
    sounds.reset_backend()


def test_wav_files_exist_and_are_short_mono_pcm():
    # 데스크탑(assets)과 웹(static) 에 같은 파일이 있어야 한다
    web_dir = sounds.assets_dir().parent / "web" / "static" / "sounds"
    for name in sounds.NAMES:
        p = sounds.sound_path(name)
        assert p.exists(), p
        with wave.open(str(p)) as w:
            assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 44100
            ms = w.getnframes() / w.getframerate() * 1000
            assert 100 <= ms <= 400, (name, ms)            # 짧은 효과음 — 촬영 흐름을 방해하지 않게
        assert (web_dir / p.name).read_bytes() == p.read_bytes(), name
    assert set(sounds.LABELS) == set(sounds.NAMES)


def test_real_recordings_are_the_chosen_candidates():
    """사진 캡처·녹화 소리는 사용자가 고른 실제 녹음(tools/sound_candidates) 과 같아야 한다."""
    cand = sounds.assets_dir().parent / "tools" / "sound_candidates"
    chosen = {"shutter": "firefoxos_shutter.wav", "rec_start": "android_rec_start.wav",
              "rec_stop": "android_rec_stop.wav"}
    for name, src in chosen.items():
        assert sounds.sound_path(name).read_bytes() == (cand / src).read_bytes(), name
    assert (sounds.assets_dir() / "sounds" / "SOURCES.md").exists()        # 출처·라이선스 동봉


def test_play_respects_enabled_and_names(fake):
    assert sounds.play("shutter") and fake.played == ["shutter"]
    assert not sounds.play("bogus")
    sounds.set_enabled(False)
    assert not sounds.is_enabled() and not sounds.play("freeze")
    assert fake.played == ["shutter"]


def test_backend_errors_are_swallowed(monkeypatch):
    monkeypatch.setattr(sounds, "_backend", FakeBackend(fail=True))
    monkeypatch.setattr(sounds, "_enabled", True)
    assert sounds.play("rec_start") is False               # 예외 없이 False
    sounds.reset_backend()


def test_backend_selection_never_raises(qapp, monkeypatch):
    sounds.reset_backend()
    b = sounds.backend()
    assert hasattr(b, "play") and b.name in ("winsound", "qt", "command", "none")
    sounds.preload()                                        # 지원하지 않는 백엔드여도 조용히 지나감
    sounds.reset_backend()


def test_config_round_trip(app_env):
    config, _ = app_env
    assert config.sounds_enabled is True
    config.sounds_enabled = False
    config.save()
    assert AppConfig.load().sounds_enabled is False


def test_settings_dialog_toggles_sound(qapp, app_env):
    from app.ui.settings_dialog import SettingsDialog

    config, _ = app_env
    config.source_mode = "synthetic"
    dlg = SettingsDialog(config)
    assert dlg.sounds.isChecked()
    dlg.sounds.setChecked(False)
    dlg.apply_to_config()
    assert config.sounds_enabled is False


def _wait(qapp, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


def test_capture_actions_play_sounds(qapp, app_env, fake, monkeypatch):
    """사진 캡처 = 찰칵, 정지영상 = freeze, 녹화 = rec_start/rec_stop, 4분할 저장 = 찰칵."""
    config, db = app_env
    config.source_mode = "synthetic"
    config.save = lambda: None
    db.patients.insert(Patient(chart_no="S1", name="효과음"))
    from app.ui import main_window
    from app.ui.main_window import MainWindow

    w = MainWindow(config, db)
    w.show()
    try:
        assert _wait(qapp, lambda: w._last_frame is not None)
        w._current_patient = db.patients.list_all()[0]
        # 녹화 결과 경고창(프레임 0개 등)이 테스트를 막지 않게
        monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *a, **k: None)
        monkeypatch.setattr(main_window.QMessageBox, "information", lambda *a, **k: None)

        w._on_freeze()                                         # 단일 정지영상
        assert fake.played[-1] == "freeze"
        w._on_freeze()                                         # 라이브 복귀 — 소리 없음
        assert fake.played.count("freeze") == 1

        w._on_capture()
        assert fake.played[-1] == "shutter"

        w.control_bar.btn_quad.setChecked(True)
        assert _wait(qapp, lambda: w.quad_view._frames[0] is not None)   # 라이브 칸에 프레임 도착
        w._on_freeze()                                         # 4분할 칸 채우기
        assert fake.played[-1] == "freeze"
        w._on_save_quad()
        assert fake.played[-1] == "shutter"
        w.control_bar.btn_quad.setChecked(False)

        w._start_recording()
        assert fake.played[-1] == "rec_start"
        _wait(qapp, lambda: False, 0.6)                        # 프레임 몇 장 기록
        w._stop_recording()
        assert fake.played[-1] == "rec_stop"

        sounds.set_enabled(False)
        n = len(fake.played)
        w._on_capture()
        assert len(fake.played) == n                           # 꺼지면 조용
    finally:
        w.close()


# ---------------------------------------------------------------- 음량
def test_scale_wav_reduces_peak():
    import wave as _wave

    import numpy as np

    data = sounds.sound_path("shutter").read_bytes()
    half = sounds.scale_wav(data, 50)
    assert sounds.scale_wav(data, 100) is data                 # 100% 는 그대로
    with _wave.open(io.BytesIO(half)) as w, _wave.open(io.BytesIO(data)) as o:
        assert w.getparams()[:3] == o.getparams()[:3]
        a = np.abs(np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(int)).max()
        b = np.abs(np.frombuffer(o.readframes(o.getnframes()), "<i2").astype(int)).max()
    assert abs(a - b // 2) <= 1
    with _wave.open(io.BytesIO(sounds.scale_wav(data, 0))) as w:
        assert max(np.abs(np.frombuffer(w.readframes(w.getnframes()), "<i2"))) == 0


def test_scaled_path_caches_copy(app_env, monkeypatch):
    p = sounds.sound_path("rec_start")
    assert sounds.scaled_path(p, 100) == p
    c = sounds.scaled_path(p, 30)
    assert c != p and c.exists() and c.name == "rec_start_30.wav" and "cache" in str(c)
    mtime = c.stat().st_mtime
    assert sounds.scaled_path(p, 30) == c and c.stat().st_mtime == mtime   # 두 번째는 재사용


def test_volume_is_passed_to_backend(monkeypatch):
    class Backend:
        name = "fake"

        def __init__(self):
            self.calls = []

        def play(self, path, volume=100):
            self.calls.append((path.stem, volume))

    b = Backend()
    monkeypatch.setattr(sounds, "_backend", b)
    monkeypatch.setattr(sounds, "_enabled", True)
    sounds.set_volume(35)
    assert sounds.get_volume() == 35
    sounds.set_volume(250)
    assert sounds.get_volume() == 100
    sounds.set_volume("bad")
    assert sounds.get_volume() == 100
    sounds.set_volume(40)
    assert sounds.play("shutter") and b.calls[-1] == ("shutter", 40)
    assert sounds.play("freeze", volume=70) and b.calls[-1] == ("freeze", 70)   # 들어보기
    sounds.set_volume(0)
    assert sounds.play("shutter") is False and len(b.calls) == 2              # 0 = 무음
    sounds.set_volume(100)
    sounds.reset_backend()


def test_qt_backend_sets_volume(qapp):
    pytest.importorskip("PySide6.QtMultimedia")
    try:
        b = sounds._QtBackend()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"QtMultimedia 사용 불가: {e}")
    p = sounds.sound_path("freeze")
    b.play(p, 25)
    assert abs(b._effect(p).volume() - 0.25) < 1e-6


def test_volume_config_and_settings_dialog(qapp, app_env):
    from app.config import AppConfig
    from app.ui.settings_dialog import SettingsDialog

    config, _ = app_env
    assert config.sound_volume == 100
    config.source_mode = "synthetic"
    dlg = SettingsDialog(config)
    assert dlg.volume.value() == 100 and dlg.volume_label.text() == "100%"
    dlg.volume.setValue(45)
    assert dlg.volume_label.text() == "45%"
    dlg.apply_to_config()
    assert config.sound_volume == 45 and AppConfig.load().sound_volume == 45

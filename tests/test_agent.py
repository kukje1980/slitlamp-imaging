"""AI 에이전트: 장면 인식 · 링버퍼/클립 · 상태 머신 · 이벤트 로그 · 학습 데이터 · 에디션."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import edition
from app.agent import dataset as agent_dataset
from app.agent.core import AgentCore, AgentSettings
from app.agent.events import EventLog
from app.agent.protocol import PROTOCOLS, Checklist
from app.agent.ringbuffer import FrameRingBuffer
from app.agent.scene import (
    FEATURE_NAMES,
    LABELS,
    HeuristicSceneClassifier,
    LearnedSceneClassifier,
    SceneClassifier,
    extract_features,
    feature_vector,
)
from app.capture.smart_capture import FrameQuality
from app.models.patient import Patient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "agent_train"))
import synth  # noqa: E402  (tools/agent_train/synth.py)


def _q(focus=95.0, usable=True, stability=90.0, exposure=70.0):
    return FrameQuality(t=0.0, focus=100.0, focus_pct=focus, exposure=exposure, stability=stability,
                        reflection=0.0, motion=0.0, usable=usable, in_focus=usable and focus >= 85)


# ---------------------------------------------------------------- 장면 인식
@pytest.mark.parametrize("label", ["slit", "fluorescein", "ir_meibo", "retro", "external",
                                   "unusable", "diffuse"])
def test_heuristic_recognizes_synthetic_scenes(label):
    rng = np.random.default_rng(3)
    clf = HeuristicSceneClassifier()
    hits = sum(clf.classify(synth.GENERATORS[label](rng)).label == label for _ in range(8))
    assert hits >= 6, f"{label}: {hits}/8"


def test_features_are_finite_and_named():
    f = extract_features(synth.slit(np.random.default_rng(0)))
    v = feature_vector(f)
    assert v.shape == (len(FEATURE_NAMES),) and np.all(np.isfinite(v))
    r = HeuristicSceneClassifier().classify(synth.slit(np.random.default_rng(1)))
    assert r.label == "slit" and r.slit_width_px > 0 and set(r.scores) == set(LABELS)


def test_learned_model_roundtrip(tmp_path):
    """합성 데이터 → 학습(JSON) → 앱 분류기 로드 → 추론 (scikit-learn 이 있을 때)."""
    pytest.importorskip("sklearn")
    import train_scene  # noqa: PLC0415 (tools/agent_train)

    data = tmp_path / "synth"
    synth.make_dataset(data, per_class=12, seed=1)
    xs, ys = train_scene.load_folder(data)
    pipe, _ = train_scene.train(xs, ys, folds=0)
    out = tmp_path / "scene_model.json"
    train_scene.export(pipe, out, {"test": True})
    m = LearnedSceneClassifier(out)
    rng = np.random.default_rng(9)
    assert m.classify(synth.retro(rng)).label == "retro"
    assert m.classify(synth.ir_meibo(rng)).label == "ir_meibo"
    sc = SceneClassifier(str(out))
    assert sc.model is not None and sc.classify(synth.fluorescein(rng)).source == "model"
    assert SceneClassifier(str(tmp_path / "missing.json")).model is None   # 없으면 규칙 기반


def test_onnx_model_path(tmp_path):
    """ONNX 추론 경로: 작은 그래프를 만들어 onnxruntime 로 돌려 본다 (onnx·onnxruntime 설치 시)."""
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import TensorProto, helper

    n = len(LABELS)
    w = np.zeros((3, n), np.float32)
    w[2, LABELS.index("fluorescein")] = 5.0     # 파란 채널(RGB 의 B) 이 크면 fluorescein
    w[0, LABELS.index("external")] = 5.0        # 빨간 채널이 크면 external
    nodes = [helper.make_node("GlobalAveragePool", ["input"], ["pooled"]),
             helper.make_node("Flatten", ["pooled"], ["flat"]),
             helper.make_node("Gemm", ["flat", "W", "B"], ["logits"])]
    inp = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, 32, 32])
    g = helper.make_graph(nodes, "tiny", [inp],
                          [helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, n])],
                          [helper.make_tensor("W", TensorProto.FLOAT, [3, n], w.flatten().tolist()),
                           helper.make_tensor("B", TensorProto.FLOAT, [n], [0.0] * n)])
    model = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    p = tmp_path / "scene_model.onnx"
    onnx.save(model, str(p))
    p.with_suffix(".json").write_text(json.dumps({"classes": list(LABELS), "mean": [0, 0, 0],
                                                  "std": [1, 1, 1]}))
    from app.agent.scene import OnnxSceneClassifier

    clf = OnnxSceneClassifier(p)
    blue = np.zeros((90, 160, 3), np.uint8)
    blue[..., 0] = 255                                   # BGR 의 B
    assert clf.classify(blue).label == "fluorescein" and clf.classify(blue).source == "onnx"


# ---------------------------------------------------------------- 링버퍼 · 클립
def test_ringbuffer_keeps_window_and_picks_top_k(tmp_path):
    buf = FrameRingBuffer(seconds=2.0, max_fps=100.0)
    rng = np.random.default_rng(0)
    for i in range(60):
        frame = synth.diffuse(rng)
        cv2.putText(frame, str(i), (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
        buf.add(frame, t=i * 0.05, score=float(i % 10), usable=(i % 7 != 0), scene="diffuse")
    assert buf.span() <= 2.0 + 1e-6 and len(buf) <= 41
    top = buf.top_k(3, t0=1.0, t1=3.0, min_gap_s=0.25)
    assert len(top) == 3 and top[0].score >= top[1].score >= top[2].score
    assert all(abs(a.t - b.t) >= 0.25 for a in top for b in top if a is not b)
    assert all(c.frame.shape == (synth.H, synth.W, 3) for c in top)
    clip = tmp_path / "clip.mp4"
    n = buf.write_clip(clip, t0=1.5, t1=2.95)
    assert n >= 20 and clip.exists() and clip.stat().st_size > 1000
    assert buf.write_clip(tmp_path / "none.mp4", t0=-5, t1=-4) == 0


# ---------------------------------------------------------------- 상태 머신
def test_core_auto_captures_once_per_scene_and_eye():
    core = AgentCore(AgentSettings(stable_s=0.5, cooldown_s=1.0, min_conf=0.6))
    scene = HeuristicSceneClassifier().classify(synth.slit(np.random.default_rng(2)))
    assert scene.label == "slit"
    kinds = []

    def run(i0, i1, eye, q=None):
        """앱처럼: auto_capture 결정이 나오면 저장했다고 알린다(note_capture)."""
        for i in range(i0, i1):
            t = i * 0.1
            for d in core.decide(q or _q(), scene, t, eye):
                kinds.append((round(t, 1), d.kind))
                if d.kind == "auto_capture":
                    assert "슬릿" in d.reason and d.eye == eye
                    for e in core.note_capture("slit", eye, t, auto=True):
                        kinds.append((round(t, 1), e.kind))

    run(0, 20, "OD")                                     # 0.0 ~ 1.9 초, 안정된 슬릿 장면
    autos = [t for t, k in kinds if k == "auto_capture"]
    assert len(autos) == 1 and autos[0] >= 0.5                    # 안정 시간 뒤 한 번만
    assert ("checklist_item" in [k for _, k in kinds])
    assert core.checklist.has("slit", "OD") and not core.checklist.has("slit", "OS")
    # 같은 장면이라도 다른 눈이면 다시 찍는다 (쿨다운 뒤) — 역시 한 번만
    kinds.clear()
    run(20, 40, "OS")
    assert sum(k == "auto_capture" for _, k in kinds) == 1
    # 저장을 알리지 않아도(실패) 쿨다운은 지킨다: 2초 동안 최대 2번
    core.reset_checklist()
    n = sum(d.kind == "auto_capture" for i in range(40, 60)
            for d in core.decide(_q(), scene, i * 0.1, "OD"))
    assert 1 <= n <= 2
    # 초점이 안 맞거나 흔들리면 안 찍는다
    core.reset_checklist()
    out = [d for i in range(60, 80) for d in core.decide(_q(focus=60), scene, i * 0.1, "OD")]
    assert not any(d.kind == "auto_capture" for d in out)
    out = [d for i in range(80, 100) for d in core.decide(_q(stability=10), scene, i * 0.1, "OD")]
    assert not any(d.kind == "auto_capture" for d in out)


def test_core_respects_mode_and_unusable():
    core = AgentCore(AgentSettings(stable_s=0.2, auto_capture=False))
    scene = HeuristicSceneClassifier().classify(synth.slit(np.random.default_rng(2)))
    out = [d for i in range(10) for d in core.decide(_q(), scene, i * 0.1, "OD")]
    assert not any(d.kind == "auto_capture" for d in out)
    core.update_settings(AgentSettings(stable_s=0.2, auto_capture=True, protocol="cataract"))
    assert core.checklist.protocol == "cataract" and len(core.checklist.items) == 3
    dark = HeuristicSceneClassifier().classify(synth.unusable(np.random.default_rng(5)))
    out = [d for i in range(10) for d in core.decide(_q(), dark, 1 + i * 0.1, "OD")]
    assert not any(d.kind == "auto_capture" for d in out)
    assert any(d.kind == "hint" and "판독" in d.reason for d in out)


def test_checklist_protocols():
    cl = Checklist.for_protocol("basic")
    assert [it.text for it in cl.items][:2] == ["확산광 전안부 우안", "확산광 전안부 좌안"]
    assert cl.mark("diffuse", "OU") and all(it.done for it in cl.items if it.scene == "diffuse")
    cl.mark("slit", "OD")
    assert not cl.complete and [it.text for it in cl.remaining()] == ["슬릿 단면 좌안"]
    cl.mark("slit", "OS")
    assert cl.complete
    cl.reset()
    assert not cl.taken and not cl.complete
    assert Checklist.for_protocol("bogus").protocol == "basic" and "free" in PROTOCOLS


# ------------------------------------------------- 이벤트 로그 · 학습 데이터 내보내기
def test_event_log_jsonl_and_db(app_env, tmp_path):
    _config, db = app_env
    ev = EventLog(tmp_path / "agent", db=db, enabled=True)
    scene = HeuristicSceneClassifier().classify(synth.slit(np.random.default_rng(2)))
    pid = db.patients.insert(Patient(chart_no="E1", name="이벤트"))
    ev.log("capture", capture_id=None, eye="OD", scene_label=scene.label, scene=scene, quality=_q())
    ev.log("settings", agent_auto_capture=False)
    files = list((tmp_path / "agent" / "events").glob("*.jsonl"))
    assert len(files) == 1
    lines = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert [ln["type"] for ln in lines] == ["capture", "settings"]
    assert lines[0]["data"]["scene"]["label"] == "slit"
    assert "patient" not in json.dumps(lines[0])
    assert len(db.agent_events.recent(10)) == 2
    ev.set_enabled(False)
    ev.log("pause", on=True)
    assert len(files[0].read_text(encoding="utf-8").splitlines()) == 2        # 꺼지면 쓰지 않음
    assert ev.tail[-1]["type"] == "pause"
    assert pid


def test_export_training_data(app_env, tmp_path):
    from app.services.capture_service import CaptureService

    config, db = app_env
    svc = CaptureService(db, config)
    p = db.patients.get(db.patients.insert(Patient(chart_no="T1", name="학습")))
    rng = np.random.default_rng(4)
    c1 = svc.save_photo(synth.slit(rng), p, eye="OD")
    svc.save_photo(synth.fluorescein(rng), p, eye="OS", note="형광염색 검사")
    c3 = svc.save_photo(synth.diffuse(rng), p, eye="OD")
    ev = EventLog(tmp_path / "agent", db=db)
    ev.log("auto_capture", capture_id=c1.id, scene_label="slit")
    ev.log("candidate_accepted", capture_id=c1.id, scene_label="slit")
    counts = agent_dataset.export_training_data(db, tmp_path / "out")
    assert counts == {"slit": 1, "fluorescein": 1, "unlabeled": 1}
    rows = (tmp_path / "out" / "manifest.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("file,label,eye,source") and len(rows) == 4
    assert any(",slit,OD,doctor," in r for r in rows)
    assert any(",fluorescein,OS,note," in r for r in rows)
    assert not any("학습" in r or "T1" in r for r in rows)                    # 환자 정보 없음
    assert (tmp_path / "out" / "slit").exists() and c3.id


# ---------------------------------------------------------------- 에디션
def test_edition_selection(monkeypatch):
    from app import paths

    monkeypatch.delenv(edition.ENV_KEY, raising=False)
    assert edition.key_from(["x"]) == "standard"
    assert edition.key_from(["x", "--edition", "agent"]) == "agent"
    assert edition.key_from(["x", "--edition=agent"]) == "agent"
    ed = edition.select("agent")
    try:
        assert ed["agent"] and edition.is_agent() and paths.APP_DIR_NAME == "SlitLampAgent"
        assert "에이전트" in edition.current()["title_suffix"]
    finally:
        edition.select("standard")
    assert not edition.is_agent() and paths.APP_DIR_NAME == "SlitLampImaging"
    assert edition.select("bogus")["key"] == "standard"


def test_agent_edition_config_default(app_env, monkeypatch):
    from app.config import AppConfig

    edition.select("agent")
    try:
        cfg = AppConfig.load()
        assert cfg.agent_enabled is True
        cfg.agent_enabled = False
        cfg.save()
        assert AppConfig.load().agent_enabled is False        # 사용자가 끈 것은 유지
    finally:
        edition.select("standard")


# ---------------------------------------------------------------- 메인 윈도우 (합성 카메라)
def _wait(qapp, cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.02)
    return cond()


def test_agent_in_main_window(app_env, monkeypatch):
    from PySide6.QtWidgets import QApplication

    qapp = QApplication.instance() or QApplication([])
    config, db = app_env
    config.source_mode = "synthetic"
    config.agent_enabled = True
    config.agent_auto_capture = False
    config.agent_clip_before_s, config.agent_clip_after_s = 1.0, 0.5
    config.save = lambda: None
    db.patients.insert(Patient(chart_no="W1", name="창"))
    from app.ui import main_window as mw
    from app.ui.main_window import MainWindow

    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: None)
    w = MainWindow(config, db)
    w.show()
    try:
        assert _wait(qapp, lambda: w._last_frame is not None)
        assert w.agent_bar.isVisible() and w.agent_bar.is_enabled()
        assert _wait(qapp, lambda: w.agent.scene().confidence > 0)     # 장면 인식이 돌고 있음
        assert _wait(qapp, lambda: w.agent.buffer.span() > 1.2)
        w._current_patient = db.patients.list_all()[0]
        n0 = w.thumbnail_panel.count()
        w._on_capture()
        assert w.thumbnail_panel.count() == n0 + 1
        # 추천 프레임 — 워커가 after_s 뒤 링버퍼 프레임을 풀어 점수를 매긴 다음 보내므로, 느린 CI(윈도우)에선 8초를 넘길 수 있다
        assert _wait(qapp, lambda: w.agent_bar.strip.isVisible(), 20.0)
        w._on_agent_candidate(0)
        assert w.thumbnail_panel.count() == n0 + 2
        assert _wait(qapp, lambda: any(e["type"] == "clip_saved" for e in w.events.tail),
                     20.0)
        kinds = [e["type"] for e in w.events.tail]
        assert "capture" in kinds and "candidate_shown" in kinds and "candidate_accepted" in kinds
        vids = [c for c in db.captures.list_by_patient(w._current_patient.id)
                if c.media_type == "video"]
        assert len(vids) == 1 and vids[0].duration_ms > 0 and "클립" in vids[0].note
        w.agent_bar.btn_on.setChecked(False)
        assert config.agent_enabled is False and not w.agent._running
    finally:
        w.close()

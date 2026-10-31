"""웹 서버 극한 상황 테스트 2 — v1.19.1 ~ v1.20.0 의 삭제 기능 (영상 한 건 · 그날 전체 · 환자).

삭제가 다른 환자의 파일 · 미디어 폴더 밖 파일 · 방금 올라온 새 영상으로 번지면 안 되고, 삭제와 업로드가 동시에
몰려도 DB 기록과 디스크 파일이 어긋나지 않아야 한다.
"""
from __future__ import annotations

import importlib
import io
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _png(tag) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + str(tag).encode() * 64


def _patient(client, chart="D001", name="삭제", **kw):
    r = client.post("/api/v1/patients", json={"chart_no": chart, "name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, pid, data=b"", ext="png", media_type="photo", **form):
    return client.post("/api/v1/captures",
                       files={"file": ("x." + ext, io.BytesIO(data or _png(1)), "application/octet-stream")},
                       data={"patient_id": str(pid), "media_type": media_type, "ext": ext, **form})


def _rel(c: dict) -> str:
    """응답의 url(/media/…) → 미디어 폴더 안 상대 경로."""
    return c["url"].removeprefix("/media/")


def _files(root: Path):
    return sorted(p for p in Path(root).rglob("*") if p.is_file())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SLITLAMP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SLITLAMP_MEDIA_DIR", str(tmp_path / "data" / "media"))
    monkeypatch.setenv("SLITLAMP_DB_PATH", str(tmp_path / "data" / "x.db"))
    monkeypatch.delenv("SLITLAMP_PASSWORD", raising=False)
    monkeypatch.delenv("SLITLAMP_STORAGE", raising=False)
    from app import config as config_mod
    importlib.reload(config_mod)
    from app import main as main_mod
    importlib.reload(main_mod)
    with TestClient(main_mod.app) as c:
        yield c, config_mod, main_mod


def test_delete_capture_does_not_follow_symlink_outside_media(env, tmp_path):
    """DB 의 경로가 미디어 폴더 안을 가리키더라도 그것이 밖으로 나가는 링크면 밖의 파일을 지우면 안 된다."""
    client, config, _ = env
    p = _patient(client)
    cap = _upload(client, p["id"]).json()
    f = config.MEDIA_DIR / _rel(cap)
    outside = tmp_path / "중요문서.png"
    outside.write_bytes(b"keep me")
    f.unlink()
    try:
        os.symlink(outside, f)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 불가")
    assert client.delete(f"/api/v1/captures/{cap['id']}").status_code == 204
    assert outside.read_bytes() == b"keep me"                                       # 밖의 파일은 그대로
    assert client.get(f"/api/v1/captures?patient_id={p['id']}").json() == []


def test_delete_patient_keeps_other_patient_files_in_same_folder(env):
    """차트번호가 'A/1' 과 'A:1' 처럼 폴더 이름이 같아지는 두 환자 — 한 환자를 지워도 다른 환자 파일 · 폴더는 남는다."""
    client, config, _ = env
    a = _patient(client, chart="A/1", name="가", patient_id="P1")
    b = _patient(client, chart="A:1", name="나", patient_id="P1")
    ca = [_upload(client, a["id"], _png(f"a{i}")).json() for i in range(3)]
    cb = [_upload(client, b["id"], _png(f"b{i}")).json() for i in range(3)]
    fa = [config.MEDIA_DIR / _rel(c) for c in ca]
    fb = [config.MEDIA_DIR / _rel(c) for c in cb]
    assert {f.parent for f in fa} == {f.parent for f in fb}, "같은 폴더를 쓰는 상황이어야 한다"
    assert client.delete(f"/api/v1/patients/{a['id']}").status_code == 204
    assert not any(f.exists() for f in fa)
    assert all(f.exists() for f in fb) and fb[0].parent.is_dir()
    assert [c["id"] for c in client.get(f"/api/v1/captures?patient_id={b['id']}").json()] == [c["id"] for c in reversed(cb)] \
        or len(client.get(f"/api/v1/captures?patient_id={b['id']}").json()) == 3
    # 이제 나 환자도 지우면 빈 폴더까지 정리되지만 미디어 루트는 남는다
    assert client.delete(f"/api/v1/patients/{b['id']}").status_code == 204
    assert not fb[0].parent.exists() and config.MEDIA_DIR.is_dir()


def test_deleted_capture_id_is_never_reused(env):
    """마지막 영상을 지운 뒤 새로 올라온 영상이 같은 번호를 받으면 — 다른 화면에 남아 있던 '삭제' 가 새 영상을 지운다."""
    client, config, _ = env
    a = _patient(client, chart="R1", name="가")
    b = _patient(client, chart="R2", name="나")
    old = _upload(client, a["id"]).json()
    assert client.delete(f"/api/v1/captures/{old['id']}").status_code == 204
    new = _upload(client, b["id"], _png("new")).json()
    assert new["id"] > old["id"]
    assert client.delete(f"/api/v1/captures/{old['id']}").status_code == 204       # 옛 화면의 삭제 — 아무 일도 없음
    assert (config.MEDIA_DIR / _rel(new)).is_file()
    assert [c["id"] for c in client.get(f"/api/v1/captures?patient_id={b['id']}").json()] == [new["id"]]


def test_concurrent_uploads_and_deletes_keep_db_and_disk_consistent(env):
    client, config, _ = env
    keep = _patient(client, chart="K1", name="보존")
    churn = _patient(client, chart="C1", name="삭제반복")
    kept = [_upload(client, keep["id"], _png(f"k{i}")).json() for i in range(10)]
    errors, ids = [], []
    lock = threading.Lock()

    def uploader(k):
        for i in range(12):
            r = _upload(client, churn["id"], _png(f"{k}-{i}"))
            if r.status_code != 201:
                errors.append(("up", r.status_code, r.text))
                continue
            with lock:
                ids.append(r.json()["id"])

    def deleter():
        for _ in range(60):
            with lock:
                cid = ids.pop() if ids else None
            if cid is None:
                continue
            r = client.delete(f"/api/v1/captures/{cid}")
            if r.status_code != 204:
                errors.append(("del", r.status_code, r.text))
    ts = [threading.Thread(target=uploader, args=(k,)) for k in range(4)] + [threading.Thread(target=deleter) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert not errors, errors[:5]
    left = client.get(f"/api/v1/captures?patient_id={churn['id']}").json()
    on_disk = {str(p.relative_to(config.MEDIA_DIR)) for p in _files(config.MEDIA_DIR)}
    in_db = {_rel(c) for c in left} | {_rel(c) for c in kept}
    assert in_db <= on_disk, in_db - on_disk                                            # DB 에 있는 파일은 모두 디스크에
    churn_dir_files = {f for f in on_disk if not f.startswith("K1_")}
    assert churn_dir_files == {_rel(c) for c in left}, "지운 영상의 파일이 남았거나 남은 영상의 파일이 없다"
    assert len(client.get(f"/api/v1/captures?patient_id={keep['id']}").json()) == 10   # 다른 환자는 그대로
    assert all((config.MEDIA_DIR / _rel(c)).is_file() for c in kept)


def test_delete_capture_of_patient_being_deleted_no_crash(env):
    client, config, _ = env
    p = _patient(client, chart="PD1")
    caps = [_upload(client, p["id"], _png(i)).json() for i in range(20)]
    codes = []

    def del_patient():
        codes.append(("p", client.delete(f"/api/v1/patients/{p['id']}").status_code))

    def del_caps():
        for c in caps:
            codes.append(("c", client.delete(f"/api/v1/captures/{c['id']}").status_code))
    ts = [threading.Thread(target=del_patient), threading.Thread(target=del_caps)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert all(code == 204 for _, code in codes), codes
    assert client.get(f"/api/v1/patients/{p['id']}").status_code == 404
    assert not any((config.MEDIA_DIR / _rel(c)).exists() for c in caps)


def test_delete_with_bad_ids_is_harmless(env):
    client, config, _ = env
    p = _patient(client, chart="BAD1")
    cap = _upload(client, p["id"]).json()
    for bad in ("-1", "0", "99999999", "abc", "1;DROP", "1%20OR%201=1"):
        r = client.delete(f"/api/v1/captures/{bad}")
        assert r.status_code in (204, 404, 422), (bad, r.status_code)
    assert (config.MEDIA_DIR / _rel(cap)).is_file()
    assert len(client.get(f"/api/v1/captures?patient_id={p['id']}").json()) == 1


def test_frontend_delete_confirms_name_patient_and_guard_double_click():
    web = Path(__file__).resolve().parents[1]
    js = (web / "static/js/app.js").read_text(encoding="utf-8")
    html = (web / "templates/index.html").read_text(encoding="utf-8")
    # 보기 창 삭제 · 그날 삭제 모두 확인 창을 거치고, 되돌릴 수 없음을 알리며, 그날 삭제는 누구의 영상인지 보여 준다
    assert "if (!confirm(`이 ${kind}" in js and "되돌릴 수 없습니다" in js
    assert "who.name + ' 환자의 '" in js
    # 두 번 눌러도 한 번만 (deletingDay · deletingViewed)
    assert "if (deletingDay || !caps || !caps.length) return 0;" in js and "if (!c || deletingViewed) return false;" in js
    # 보기 창에는 글 입력 칸이 없어 Delete 키가 글자 지우기와 겹치지 않는다
    body = html[html.index('id="viewer-dialog"'):html.index("</dialog>", html.index('id="viewer-dialog"'))]
    assert "<input" not in body and "<textarea" not in body and "contenteditable" not in body

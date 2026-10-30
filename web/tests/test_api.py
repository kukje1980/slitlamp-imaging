from __future__ import annotations

import io


def _make_patient(client, chart="C001", name="홍길동"):
    r = client.post("/api/v1/patients", json={
        "chart_no": chart, "name": name, "sex": "M",
        "phone": "010-1234-5678", "symptoms": "충혈", "memo": "초진",
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "슬릿램프" in r.text


def test_favicon(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(("image/", "application/"))


def test_patient_crud(client):
    p = _make_patient(client)
    pid = p["id"]

    got = client.get(f"/api/v1/patients/{pid}").json()
    assert got["name"] == "홍길동"

    r = client.put(f"/api/v1/patients/{pid}", json={**got, "name": "김철수"})
    assert r.status_code == 200
    assert client.get(f"/api/v1/patients/{pid}").json()["name"] == "김철수"

    lst = client.get("/api/v1/patients?q=김철수").json()
    assert len(lst) == 1

    assert client.delete(f"/api/v1/patients/{pid}").status_code == 204
    assert client.get(f"/api/v1/patients/{pid}").status_code == 404


def test_duplicate_chart_no(client):
    _make_patient(client, chart="DUP")
    r = client.post("/api/v1/patients", json={"chart_no": "DUP", "name": "다른사람"})
    assert r.status_code == 400


def test_capture_with_note(client):
    """4분할 저장이 쓰는 note 필드가 저장·반환되는지."""
    p = _make_patient(client, chart="Q100")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    r = client.post("/api/v1/captures", files={"file": ("q.png", io.BytesIO(png), "image/png")},
                    data={"patient_id": str(p["id"]), "media_type": "photo", "eye": "OD",
                          "ext": "png", "note": "4분할 합성"})
    assert r.status_code == 201, r.text
    assert r.json()["note"] == "4분할 합성"
    assert client.get(f"/api/v1/captures?patient_id={p['id']}").json()[0]["note"] == "4분할 합성"


def test_capture_upload_and_list(client):
    p = _make_patient(client, chart="C100")
    pid = p["id"]

    # 가짜 PNG 바이트 업로드
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 128
    r = client.post("/api/v1/captures", files={"file": ("shot.png", io.BytesIO(png), "image/png")},
                    data={"patient_id": str(pid), "media_type": "photo", "eye": "OD", "ext": "png"})
    assert r.status_code == 201, r.text
    cap = r.json()
    assert cap["media_type"] == "photo"
    assert cap["eye"] == "OD"
    assert cap["url"].startswith("/media/")

    # 목록 조회
    lst = client.get(f"/api/v1/captures?patient_id={pid}").json()
    assert len(lst) == 1

    # 업로드한 파일이 /media 로 제공되는지
    assert client.get(cap["url"]).status_code == 200

    # 삭제
    assert client.delete(f"/api/v1/captures/{cap['id']}").status_code == 204
    assert client.get(f"/api/v1/captures?patient_id={pid}").json() == []


def test_capture_cascade_on_patient_delete(client):
    p = _make_patient(client, chart="C200")
    pid = p["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    client.post("/api/v1/captures", files={"file": ("s.png", io.BytesIO(png), "image/png")},
                data={"patient_id": str(pid), "media_type": "photo", "eye": "OS", "ext": "png"})
    assert len(client.get(f"/api/v1/captures?patient_id={pid}").json()) == 1
    client.delete(f"/api/v1/patients/{pid}")
    assert client.get(f"/api/v1/captures?patient_id={pid}").json() == []


def test_patient_diagnosis_doctor(client):
    r = client.post("/api/v1/patients", json={"chart_no": "DX1", "name": "진단", "diagnosis": "백내장",
                                               "doctor": "박원장"})
    assert r.status_code == 201
    p = r.json()
    assert (p["diagnosis"], p["doctor"]) == ("백내장", "박원장")
    r = client.put(f"/api/v1/patients/{p['id']}", json={**p, "diagnosis": "녹내장 의증"})
    assert r.json()["diagnosis"] == "녹내장 의증"


def test_patch_capture_note(client):
    p = _make_patient(client, chart="PN1")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    cap = client.post("/api/v1/captures", files={"file": ("a.png", io.BytesIO(png), "image/png")},
                      data={"patient_id": str(p["id"]), "media_type": "photo", "ext": "png"}).json()
    r = client.patch(f"/api/v1/captures/{cap['id']}", json={"note": "  경과관찰  "})
    assert r.status_code == 200 and r.json()["note"] == "경과관찰"
    assert client.patch("/api/v1/captures/99999", json={"note": "x"}).status_code == 404


def test_old_web_db_migrated(tmp_path):
    import sqlite3
    from app.db.database import Database
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE patients (id INTEGER PRIMARY KEY AUTOINCREMENT, chart_no TEXT UNIQUE,
            patient_id TEXT, name TEXT NOT NULL, birth_date TEXT, sex TEXT, phone TEXT,
            memo TEXT, symptoms TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
        INSERT INTO patients (chart_no, name) VALUES ('OLD', '기존');
    """)
    con.commit(); con.close()
    db = Database(path).init()
    try:
        p = db.list_patients("기존")[0]
        assert p["diagnosis"] is None
        db.update_patient(p["id"], {**p, "diagnosis": "결막염", "doctor": "이의사"})
        assert db.get_patient(p["id"])["doctor"] == "이의사"
    finally:
        db.close()


def test_capture_rejects_non_media_extension(client):
    """html·svg 같은 확장자는 같은 출처에서 스크립트 실행 위험 → 거부."""
    p = _make_patient(client, chart="X1")
    for bad in ("html", "svg", "js"):
        r = client.post("/api/v1/captures", files={"file": ("x", io.BytesIO(b"<script>1</script>"), "text/html")},
                        data={"patient_id": str(p["id"]), "media_type": "photo", "ext": bad})
        assert r.status_code == 400, bad
    ok = client.post("/api/v1/captures", files={"file": ("v.webm", io.BytesIO(b"\x1aE\xdf\xa3"), "video/webm")},
                     data={"patient_id": str(p["id"]), "media_type": "video", "ext": "webm"})
    assert ok.status_code == 201


def test_delete_patient_removes_media_files(client):
    from app import config
    p = _make_patient(client, chart="DEL1")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    cap = client.post("/api/v1/captures", files={"file": ("a.png", io.BytesIO(png), "image/png")},
                      data={"patient_id": str(p["id"]), "media_type": "photo", "ext": "png"}).json()
    f = config.MEDIA_DIR / cap["url"].removeprefix("/media/")
    assert f.exists()
    assert client.delete(f"/api/v1/patients/{p['id']}").status_code == 204
    assert not f.exists()
    assert not f.parent.exists()                      # 빈 날짜 폴더도 정리
    assert client.get(cap["url"]).status_code == 404


def test_update_keeps_unsent_fields(client):
    """화면에 없는 patient_id 를 보내지 않고 수정해도 기존 값이 지워지지 않는다."""
    r = client.post("/api/v1/patients", json={"chart_no": "K1", "name": "가", "patient_id": "H-77"})
    pid = r.json()["id"]
    client.put(f"/api/v1/patients/{pid}", json={"chart_no": "K1", "name": "나"})
    got = client.get(f"/api/v1/patients/{pid}").json()
    assert got["name"] == "나" and got["patient_id"] == "H-77"


def test_same_millisecond_uploads_do_not_overwrite(client, monkeypatch):
    from datetime import datetime
    from app import config
    p = _make_patient(client, chart="T1")
    monkeypatch.setattr(config, "now", lambda: datetime(2026, 1, 2, 3, 4, 5, 6000))
    urls = []
    for body in (b"one", b"two"):
        r = client.post("/api/v1/captures", data={"patient_id": p["id"], "media_type": "photo", "ext": "png"},
                        files={"file": ("a.png", io.BytesIO(body), "image/png")})
        assert r.status_code == 201, r.text
        urls.append(r.json()["url"])
    assert urls[0] != urls[1]
    assert [client.get(u).content for u in urls] == [b"one", b"two"]
    assert r.json()["captured_at"] == "2026-01-02 03:04:05"


def test_upload_size_limit(client, monkeypatch):
    from app import config
    p = _make_patient(client, chart="T2")
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    r = client.post("/api/v1/captures", data={"patient_id": p["id"], "media_type": "video", "ext": "mp4"},
                    files={"file": ("a.mp4", io.BytesIO(b"0" * (1024 * 1024 + 10)), "video/mp4")})
    assert r.status_code == 413
    assert not list(config.MEDIA_DIR.rglob("*.mp4"))                  # 잘린 파일이 남지 않음


def test_now_uses_korea_time():
    from datetime import datetime, timedelta, timezone
    from app import config
    kst = datetime.now(timezone(timedelta(hours=9))).replace(tzinfo=None)
    assert abs((config.now() - kst).total_seconds()) < 5


def test_exam_notes_api(client):
    """검사 기록: 촬영일별 요약 + 소견 저장/수정, 환자 삭제 시 함께 삭제."""
    p = _make_patient(client, chart="X100")
    pid = p["id"]
    assert client.get(f"/api/v1/patients/{pid}/exams").json() == []
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    for eye in ("OD", "OS", "OD"):
        r = client.post("/api/v1/captures", files={"file": ("x.png", io.BytesIO(png), "image/png")},
                        data={"patient_id": str(pid), "media_type": "photo", "eye": eye, "ext": "png"})
        assert r.status_code == 201, r.text
    exams = client.get(f"/api/v1/patients/{pid}/exams").json()
    assert len(exams) == 1 and exams[0]["count"] == 3 and exams[0]["od"] == 2 and exams[0]["os"] == 1
    assert exams[0]["notes"] is None
    day = exams[0]["exam_date"]
    r = client.put(f"/api/v1/patients/{pid}/exams/{day}", json={"exam_type": "건성안", "notes": " TBUT 5초 "})
    assert r.status_code == 200, r.text
    assert r.json()["exam_type"] == "건성안" and r.json()["notes"] == "TBUT 5초" and r.json()["count"] == 3
    r = client.put(f"/api/v1/patients/{pid}/exams/{day}", json={"exam_type": "", "notes": "수정"})
    assert r.json()["exam_type"] is None and r.json()["notes"] == "수정"
    # 영상이 없는 날짜에도 소견만 남길 수 있다
    r = client.put(f"/api/v1/patients/{pid}/exams/2025-01-05", json={"notes": "외래 소견"})
    assert r.status_code == 200 and r.json()["count"] == 0
    assert [x["exam_date"] for x in client.get(f"/api/v1/patients/{pid}/exams").json()] == [day, "2025-01-05"]
    assert client.put(f"/api/v1/patients/{pid}/exams/20250105", json={"notes": "x"}).status_code == 400
    assert client.get("/api/v1/patients/999999/exams").status_code == 404
    assert client.delete(f"/api/v1/patients/{pid}").status_code == 204
    assert client.get(f"/api/v1/patients/{pid}/exams").status_code == 404


def test_index_shows_version(client):
    from app import __version__
    assert f"Version {__version__}" in client.get("/").text


def test_measurements_record_list_and_cascade(client):
    """측정 기록 (혼탁 지수 · 촬영 조건 · LOCS 의사 입력) — 경과 그래프용."""
    pid = client.post("/api/v1/patients", json={"chart_no": "M1", "name": "측정"}).json()["id"]
    other = client.post("/api/v1/patients", json={"chart_no": "M2", "name": "다른"}).json()["id"]
    cap = client.post("/api/v1/captures", data={"patient_id": str(pid), "media_type": "photo", "eye": "OD"},
                      files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")}).json()
    body = {"patient_id": pid, "capture_id": cap["id"], "eye": "OD", "kind": "nuclear", "value": 42.4,
            "detail": {"ratio": 0.48, "has_cornea": True}, "locs": {"NO": 3, "NC": "2.5", "X": 1, "C": 9},
            "conditions": "슬릿 0.2mm · 45°"}
    r = client.post("/api/v1/measurements", json=body)
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["locs"] == {"NO": 3.0, "NC": 2.5} and m["detail"]["ratio"] == 0.48 and m["measured_at"]
    client.post("/api/v1/measurements", json={**body, "eye": "OS", "value": 10, "capture_id": None})
    client.post("/api/v1/measurements", json={**body, "kind": "cortical", "value": 150, "capture_id": None})
    od = client.get(f"/api/v1/measurements?patient_id={pid}&kind=nuclear&eye=OD").json()
    assert [x["value"] for x in od] == [42.4]
    assert len(client.get(f"/api/v1/measurements?patient_id={pid}").json()) == 3
    cort = client.get(f"/api/v1/measurements?patient_id={pid}&kind=cortical").json()
    assert cort[0]["value"] == 100.0                                  # 0~100 으로 제한
    # 잘못된 요청: 없는 환자 · 모르는 종류 · 다른 환자의 영상
    assert client.post("/api/v1/measurements", json={**body, "patient_id": 9999}).status_code == 404
    assert client.post("/api/v1/measurements", json={**body, "kind": "diagnosis"}).status_code == 400
    assert client.post("/api/v1/measurements", json={**body, "patient_id": other}).status_code == 400
    # 영상을 지워도 측정 기록은 남고(capture_id=NULL), 환자를 지우면 함께 삭제
    client.delete(f"/api/v1/captures/{cap['id']}")
    assert client.get(f"/api/v1/measurements?patient_id={pid}&kind=nuclear&eye=OD").json()[0]["capture_id"] is None
    assert client.delete(f"/api/v1/measurements/{m['id']}").status_code == 204
    client.delete(f"/api/v1/patients/{pid}")
    assert client.get(f"/api/v1/measurements?patient_id={pid}").json() == []


def test_db_failed_write_rolls_back(client):
    """차트번호 중복 등 실패한 쓰기가 트랜잭션을 열어 둔 채 남기지 않는다."""
    import sqlite3

    import pytest
    db = client.app.state.db
    r = client.post("/api/v1/patients", json={"chart_no": "RB1", "name": "a"})
    assert r.status_code == 201
    with pytest.raises(sqlite3.IntegrityError):
        db.create_patient({"chart_no": "RB1", "name": "b"})
    assert not db._conn.in_transaction
    assert db._conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 10000
    r = client.post("/api/v1/patients", json={"chart_no": "RB2", "name": "c"})
    assert r.status_code == 201


def test_reference_samples_library(client):
    """참조 샘플: 부위·정상/비정상·질환명으로 등록, 필터 조회, 파일 제공, 삭제 (환자와 무관)."""
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    def up(region, cond, disease=None, ext="png"):
        data = {"region": region, "condition": cond, "ext": ext}
        if disease:
            data["disease"] = disease
        return client.post("/api/v1/refs", data=data, files={"file": ("a.png", png, "image/png")})
    r1 = up("cornea", "normal")
    assert r1.status_code == 201, r1.text
    r2 = up("cornea", "abnormal", "각막염")
    up("lens", "abnormal", "핵백내장")
    assert up("retina", "normal").status_code == 400                 # 모르는 부위
    assert up("cornea", "suspect").status_code == 400                # 구분은 normal/abnormal
    assert up("cornea", "normal", ext="html").status_code == 400     # 영상 형식만
    cornea = client.get("/api/v1/refs?region=cornea").json()
    assert [x["condition"] for x in cornea] == ["normal", "abnormal"] and cornea[1]["disease"] == "각막염"
    assert len(client.get("/api/v1/refs?condition=abnormal").json()) == 2
    url = r2.json()["url"]
    assert url.startswith("/media/_reference/cornea/") and client.get(url).content == png
    assert client.delete(f"/api/v1/refs/{r2.json()['id']}").status_code == 204
    assert client.get(url).status_code == 404
    assert len(client.get("/api/v1/refs").json()) == 2


def test_delete_capture_removes_media_file(client):
    from app import config
    p = _make_patient(client, chart="DEL2")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    caps = []
    for k in range(2):
        r = client.post("/api/v1/captures",
                        files={"file": (f"{k}.png", io.BytesIO(png), "image/png")},
                        data={"patient_id": str(p["id"]), "media_type": "photo", "ext": "png"})
        caps.append(r.json())
    f0 = config.MEDIA_DIR / caps[0]["url"].removeprefix("/media/")
    f1 = config.MEDIA_DIR / caps[1]["url"].removeprefix("/media/")
    assert f0.exists() and f1.exists()
    assert client.delete(f"/api/v1/captures/{caps[0]['id']}").status_code == 204
    assert not f0.exists() and f1.exists()                 # 선택한 영상 파일만 삭제
    left = client.get(f"/api/v1/captures?patient_id={p['id']}").json()
    assert [c["id"] for c in left] == [caps[1]["id"]]
    # 이미 지운 영상을 다시 지워도 오류 없음
    assert client.delete(f"/api/v1/captures/{caps[0]['id']}").status_code == 204

"""프런트엔드 정적 검사: Pro 모드 스크립트가 참조하는 요소가 템플릿에 모두 있는지."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
HTML = (WEB / "templates" / "index.html").read_text(encoding="utf-8")
PRO_JS = (WEB / "static" / "js" / "pro.js").read_text(encoding="utf-8")
HTML_IDS = set(re.findall(r'\bid="([^"]+)"', HTML))


def test_pro_js_ids_exist_in_template():
    used = set(re.findall(r"\$\('([a-z0-9-]+)'\)", PRO_JS))
    used |= set(re.findall(r"\['(adj-[a-z]+)'", PRO_JS))
    assert used, "pro.js 에서 참조 id 를 찾지 못함"
    missing = sorted(used - HTML_IDS)
    assert not missing, f"템플릿에 없는 id: {missing}"


def test_edit_tabs_and_tools_present():
    tabs = re.findall(r'class="ptab[^"]*"[^>]*data-tab="(\w+)"', HTML)
    panes = re.findall(r'class="ppane[^"]*"\s+data-pane="(\w+)"', HTML)
    assert tabs == ["smart", "adj", "flt", "edit", "meibo", "anal", "ref"] and sorted(panes) == sorted(tabs)
    tools = re.findall(r'data-tool="(\w+)"', HTML)
    assert set(tools) == {"pan", "magnify", "crop", "text", "arrow", "circle", "length", "angle", "calibrate", "tarsus", "dropout"}
    for t in tools:                                 # 모든 도구에 사용 가능 모드 정의
        assert re.search(rf"\b{t}: \[", PRO_JS), t


def test_channel_and_filter_options_match_engine():
    chans = re.findall(r'<option value="(\w+)"', HTML[HTML.index('id="adj-channel"'):HTML.index("</select>", HTML.index('id="adj-channel"'))])
    assert chans == ["rgb", "green", "red", "blue", "gray"]
    for ch in chans[1:]:
        assert f"'{ch}'" in PRO_JS
    dn = re.findall(r'<option value="(\w+)"', HTML[HTML.index('id="adj-denoise"'):HTML.index("</select>", HTML.index('id="adj-denoise"'))])
    assert dn == ["none", "median", "average"]


def test_meibo_mode_wired():
    APP_JS = (Path(__file__).resolve().parents[1] / "static/js/app.js").read_text(encoding="utf-8")
    for el in ("btn-meibo", "mb-lid-seg", "btn-mb-enh", "btn-mb-inv", "hud-meibo", "meibo-guide",
               "mb-lid", "mb-enhance", "mb-auto", "mb-clear", "mb-save", "mb-result"):
        assert f'id="{el}"' in HTML, el
    assert "id: 'meibo'" in APP_JS and "id: 'lid'" in APP_JS       # 단축키 M / U
    assert "마이봄 ${LID_KO[meibo.lid]}" in APP_JS                 # 촬영 메모 → Pro 에서 안검 자동 선택
    # Arita 경계 (1/3, 2/3) 와 Pult 5단계
    assert "100 / 3" in PRO_JS and "200 / 3" in PRO_JS
    for cat in ("'0%'", "'<25%'", "'25–50%'", "'51–75%'", "'>75%'"):
        assert cat in PRO_JS


def test_sounds_wired():
    """효과음: 사진 캡처 '찰칵' · 정지영상 · 녹화 시작/중지 — 파일·스크립트·설정 버튼이 모두 있어야 한다."""
    APP_JS = (WEB / "static/js/app.js").read_text(encoding="utf-8")
    SND_JS = (WEB / "static/js/sounds.js").read_text(encoding="utf-8")
    for name in ("shutter", "freeze", "rec_start", "rec_stop"):
        f = WEB / "static" / "sounds" / f"{name}.wav"
        assert f.exists() and f.read_bytes()[:4] == b"RIFF", name
        assert f"/static/sounds/{name}.wav" in SND_JS
    assert '<script src="/static/js/sounds.js"></script>' in HTML
    assert HTML.index("sounds.js") < HTML.index("js/app.js")      # app.js 보다 먼저
    assert 'id="btn-sounds"' in HTML and 'id="sounds-label"' in HTML

    def body(start, end=None):
        i = APP_JS.index(start)
        return APP_JS[i:APP_JS.index(end, i)] if end else APP_JS[i:]

    # 촬영 동작마다 효과음
    assert "sfx('shutter')" in body("async function capturePhoto", "function toggleRecord")
    assert "sfx('shutter')" in body("async function saveQuad")
    assert "sfx('rec_start')" in body("function startRecording", "function stopRecording")
    assert "sfx('rec_stop')" in body("function stopRecording", "// 서버 + 지정 폴더 동시 저장")
    assert "sfx('freeze')" in body("function togglePause", "// ---- 효과음")
    assert "sfx('freeze')" in body("function freezeFrame", "function setQuadActive")
    assert "App.sfx('freeze')" in PRO_JS and "App.sfx('shutter')" in PRO_JS


def test_smart_and_analysis_tabs_wired():
    """스마트 캡처 · 분석 탭 (smart.js + smartcv.js): 참조하는 요소가 모두 있고, 스크립트 순서가 맞아야 한다."""
    SMART_JS = (WEB / "static/js/smart.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\('([a-z0-9-]+)'\)", SMART_JS))
    used |= {f"sm-q-{k}" for k in ("focus", "exposure", "stability", "reflection")}
    used |= {f"sm-v-{k}" for k in ("focus", "exposure", "stability", "reflection")}
    used |= {f"cat-locs-{k}" for k in ("NO", "NC", "C", "P")}
    assert len(used) > 30
    missing = sorted(used - HTML_IDS)
    assert not missing, f"템플릿에 없는 id: {missing}"
    order = [HTML.index(f"/static/js/{n}.js") for n in ("app", "pro", "smartcv", "smart", "refs")]
    assert order == sorted(order)                                   # pro.js → smartcv.js → smart.js
    # pro.js 연결점: Best Shot 으로 CAPTURE/SAVE, 표시 전용 레이어는 저장본(renderFull)에 넣지 않음
    assert "window.SlitSmart && window.SlitSmart.bestFrame()" in PRO_JS
    rf = PRO_JS[PRO_JS.index("renderFull() {"):PRO_JS.index("const cursorFor")]
    assert "aOverlay" not in rf and "ghost" not in rf
    assert "'slit:pro-mode'" in PRO_JS and "'slit:pro-image'" in PRO_JS
    # 프로그램이 판정하는 진단 표현을 쓰지 않는다 (측정·기록 보조). '정상/비정상' 은 참조 샘플에 사용자가 붙이는 분류 라벨로만 쓴다
    for word in ("백내장 3등급", "정상입니다", "비정상입니다", "진단 결과"):
        assert word not in SMART_JS and word not in HTML


def test_tabs_in_toolbar_and_reference_tab_wired():
    """탭 메뉴는 상단 Pro 도구줄에, 참조 탭(부위별 정상/비정상 샘플)의 요소가 모두 있어야 한다."""
    toolbar = HTML[HTML.index('class="pro-toolbar"'):HTML.index('class="pro-body"')]
    side = HTML[HTML.index('<aside class="pro-side">'):HTML.index("</aside>")]
    assert 'class="ptabs"' in toolbar and 'class="ptabs"' not in side
    assert len(re.findall(r'class="ptab(?: on)?"', toolbar)) == 7
    modes = HTML[HTML.index('id="pro-modes"'):HTML.index("</div>", HTML.index('id="pro-modes"'))]
    assert re.search(r'data-mode="live".*?</button>\s*<button class="ptab"[^>]*data-tab="smart"', modes, re.S)   # 스마트는 LIVE 바로 옆
    REFS_JS = (WEB / "static/js/refs.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\$\('([a-z0-9-]+)'\)", REFS_JS))
    assert {"ref-list", "ref-cmp-cur", "ref-cmp-normal", "ref-add-cur", "ref-add-file", "ref-file"} <= used
    missing = sorted(used - HTML_IDS)
    assert not missing, f"템플릿에 없는 id: {missing}"
    regions = re.findall(r'<option value="(\w+)"', HTML[HTML.index('id="ref-region"'):HTML.index("</select>", HTML.index('id="ref-region"'))])
    assert set(regions) == {"eyelid", "conjunctiva", "cornea", "ac", "iris", "lens", "meibo", "other"}
    assert "compareImages" in PRO_JS and "src.ref" in PRO_JS                # 참조 샘플은 환자 영상으로 저장하지 않음


def test_video_quality_modes_wired():
    """동영상 화질 일반 / 고화질 — 설정 칸, 저장, 녹화 비트레이트."""
    app_js = (WEB / "static/js/app.js").read_text(encoding="utf-8")
    sel = HTML[HTML.index('id="vq-select"'):HTML.index("</select>", HTML.index('id="vq-select"'))]
    assert re.findall(r'<option value="(\w+)"', sel) == ["normal", "high"]
    assert "prefSet('slitlamp-vq'" in app_js and "videoBitsPerSecond: recordBitrate()" in app_js
    assert "VQ_BPP = { normal: 0.08, high: 0.2 }" in app_js


def test_capture_safety_guards_wired():
    """촬영본 보호 장치 (실제 브라우저 검증은 tests/e2e_extreme.js)."""
    app_js = (WEB / "static/js/app.js").read_text(encoding="utf-8")
    # 녹화 중·저장 중 창 닫기/새로고침 경고
    assert "addEventListener('beforeunload'" in app_js and "state.saving > 0" in app_js
    # 카메라 끊김 → 촬영 막기 + 녹화 화면 정리
    assert "addEventListener('ended'" in app_js and "endRecordingUi()" in app_js
    # 드롭다운으로 환자를 바꿔도 표와 같은 경로(녹화 종료) 를 탄다
    assert "if (id) { selectPatient(id); return; }" in app_js
    # 서버·폴더 모두 실패하면 내려받기로 보존
    assert "a.download = filename" in app_js


def test_system_specs_dialog_wired():
    """설정 → 컴퓨터 사양: 최소·권장 사양 표와 동영상 화질(일반·고화질) 설명."""
    app_js = (WEB / "static/js/app.js").read_text(encoding="utf-8")
    css = (WEB / "static/css/app.css").read_text(encoding="utf-8")
    for el in ("btn-specs", "specs-dialog", "specs-x", "specs-this-pc"):
        assert f'id="{el}"' in HTML, el
    body = HTML[HTML.index('id="specs-dialog"'):HTML.index("</dialog>", HTML.index('id="specs-dialog"'))]
    for s in ("최소 사양", "권장 사양", "메모리(RAM)", "16GB", "그래픽카드", "USB 3.0", "일반", "고화질",
              "분당 38MB", "분당 375MB", "Chrome"):
        assert s in body, s
    assert "$('btn-specs').addEventListener" in app_js and "navigator.hardwareConcurrency" in app_js
    assert ".spec-table" in css and ".specs-dialog" in css

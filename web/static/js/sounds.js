/* 슬릿램프 영상 시스템 — 효과음
 *
 *   shutter    사진 캡처: 예전 필름 카메라의 '찰칵'
 *   freeze     정지영상(스틸): 가벼운 '틱' 과 짧은 여운
 *   rec_start  녹화 시작: 캠코더식 '삐' 한 번
 *   rec_stop   녹화 중지: 낮은 '삐삐' 두 번
 *
 * 음원은 /static/sounds/*.wav (tools/generate_sounds.py 로 합성). Web Audio 로 재생해 지연이 거의 없다.
 * 브라우저 자동재생 정책 때문에 AudioContext 는 첫 클릭/키 입력 때 만든다 (효과음은 항상 사용자 조작 뒤에 난다).
 * 켜기/끄기: localStorage 'slitlamp-sounds' = 'on'(기본) | 'off'  →  설정 창의 '효과음' 버튼.
 * 크기: localStorage 'slitlamp-sound-volume' = 0~100 (기본 100)  →  설정 창의 '효과음 크기' 슬라이더.
 */
(() => {
  const KEY = 'slitlamp-sounds';
  const VOL_KEY = 'slitlamp-sound-volume';
  const FILES = {
    shutter: '/static/sounds/shutter.wav',
    freeze: '/static/sounds/freeze.wav',
    rec_start: '/static/sounds/rec_start.wav',
    rec_stop: '/static/sounds/rec_stop.wav',
  };
  const raw = {};        // name → ArrayBuffer (fetch 결과)
  const buffers = {};    // name → AudioBuffer (디코딩 결과)
  let ctx = null;

  function enabled() {
    try { return localStorage.getItem(KEY) !== 'off'; } catch (_) { return true; }
  }
  function setEnabled(on) {
    try { localStorage.setItem(KEY, on ? 'on' : 'off'); } catch (_) { /* 저장 불가 */ }
    if (on) unlock();
  }
  function volume() {
    try {
      const v = parseInt(localStorage.getItem(VOL_KEY), 10);
      return Number.isFinite(v) ? Math.max(0, Math.min(100, v)) : 100;
    } catch (_) { return 100; }
  }
  function setVolume(v) {
    const n = Math.max(0, Math.min(100, Math.round(Number(v) || 0)));
    try { localStorage.setItem(VOL_KEY, String(n)); } catch (_) { /* 저장 불가 */ }
    return n;
  }

  function context() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      try { ctx = new AC(); } catch (_) { return null; }
    }
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    return ctx;
  }

  // 페이지가 열리면 파일만 미리 받아 두고(컨텍스트 불필요), 첫 사용자 조작 때 디코딩한다
  function prefetch() {
    if (!window.fetch) return;
    for (const [name, url] of Object.entries(FILES)) {
      if (raw[name]) continue;
      fetch(url).then((r) => (r.ok ? r.arrayBuffer() : null)).then((ab) => { if (ab) raw[name] = ab; }).catch(() => {});
    }
  }
  async function decode(name) {
    if (buffers[name]) return buffers[name];
    const c = context();
    if (!c) return null;
    let ab = raw[name];
    if (!ab) {
      try { const r = await fetch(FILES[name]); if (!r.ok) return null; ab = await r.arrayBuffer(); } catch (_) { return null; }
    }
    try {
      // 한 번 디코딩한 ArrayBuffer 는 분리(detached)되므로 복사본을 넘긴다
      buffers[name] = await c.decodeAudioData(ab.slice(0));
    } catch (_) { return null; }
    return buffers[name];
  }
  function unlock() {
    if (!enabled()) return;
    const c = context();
    if (!c) return;
    Object.keys(FILES).forEach((n) => { decode(n); });
  }

  function start(c, buf, vol) {
    const v = (vol == null ? volume() : vol) / 100;
    if (v <= 0) return;
    const s = c.createBufferSource();
    s.buffer = buf;
    const g = c.createGain();
    g.gain.value = v;
    s.connect(g);
    g.connect(c.destination);
    s.start();
  }

  /** 효과음 재생. 재생을 시도했으면 true (꺼짐·미지원이면 false). 디코딩이 아직이면 끝나는 대로 낸다. */
  function play(name, vol) {
    if (!FILES[name] || (vol == null && !enabled())) return false;
    const c = context();
    if (!c) return false;
    const buf = buffers[name];
    if (buf) { start(c, buf, vol); return true; }
    decode(name).then((b) => { if (b) start(c, b, vol); });
    return true;
  }

  document.addEventListener('DOMContentLoaded', prefetch);
  document.addEventListener('pointerdown', unlock, { once: true, capture: true });
  document.addEventListener('keydown', unlock, { once: true, capture: true });

  window.SlitSounds = { play, enabled, setEnabled, volume, setVolume, names: Object.keys(FILES) };
})();

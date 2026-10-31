/* 슬릿램프 영상 시스템 — Pro 모드 (전문가용 영상 검토, VK-2 스타일 워크플로)
 *
 *  LIVE     실시간 영상. CAPTURE(Space)로 정지/재개, SAVE로 환자 DB 저장
 *  1화면    단일 영상. PREV/NEXT(←/→), 휠 확대·드래그 이동
 *  MULTI    16장(4×4) / 36장(6×6) 그리드. 클릭 = 비교 선택(최대 2), 더블클릭 = 1화면
 *  비교     선택한 2장 좌우 비교. 확대·이동 동기화, 보정 동일 적용
 *  보정     밝기·대비·감마·채도·RGB 색균형·레벨(자동 대비)·채널(Red-free 등)·네거티브, 히스토그램
 *  필터     노이즈 제거(Median/평균)·선명하게(언샤프)·국소 대비 향상(CLAHE)
 *  편집     회전·반전·자르기, 텍스트/화살표/원 주석, 길이·각도 측정(mm 눈금 보정), 돋보기,
 *           실행 취소/다시 실행(20단계), 원본 복원
 *  편집본 저장 · 코멘트 · 환자정보 · 인쇄/PDF(A4 보고서)
 *  삭제     MULTI에서 선택한 영상(최대 2장) / 1화면의 현재 영상을 삭제 (Delete 키)
 *  스마트 · 분석 탭은 smart.js, 참조 탭(부위별 정상/비정상 샘플 비교)은 refs.js
 *  (이 파일의 window.SlitPro 와 'slit:pro-*' 이벤트를 사용)
 *
 * app.js 의 window.SlitApp API 와 'slit:patient' 이벤트에 의존한다.
 */
(() => {
  const App = window.SlitApp;
  if (!App) return;
  const $ = (id) => document.getElementById(id);
  const EYE_KO = { OD: '우안(OD)', OS: '좌안(OS)', OU: '양안(OU)' };
  // 보정 파라미터 — 윈도우앱 image_process.Adjust 와 같은 필드·처리 순서
  //   노이즈 제거 → 채널 → RGB 게인·채도 → 레벨(흑·백점) → 대비·밝기 → 감마 → 국소 대비(CLAHE) → 선명화 → 네거티브
  const IDENTITY = {
    brightness: 0, contrast: 1, gamma: 1, negative: false, channel: 'rgb',
    saturation: 1, r_gain: 1, g_gain: 1, b_gain: 1, black: 0, white: 255,
    sharpen: 0, denoise: 'none', enhance: 0,
  };
  const SHARP_AMOUNT = { 1: 0.6, 2: 1.2, 3: 2.0 };
  const CLAHE_CLIP = { 1: 1.6, 2: 2.6, 3: 4.0 };
  const HISTORY_MAX = 20;
  const HISTORY_PIXELS = 120e6;                              // 실행 취소용으로 보관하는 편집 단계 영상의 총 화소 (약 480MB)
  const ANN_COLOR = '#facc15', MEAS_COLOR = '#22d3ee';
  const DRAG_TOOLS = new Set(['arrow', 'circle', 'length', 'crop', 'calibrate']);
  // 도구별 사용 가능 모드 (윈도우앱 _TOOLS 와 동일)
  const TOOL_MODES = {
    pan: ['single', 'compare', 'multi', 'live'], magnify: ['single', 'compare'], crop: ['single'],
    text: ['single', 'compare'], arrow: ['single', 'compare'], circle: ['single', 'compare'],
    length: ['single', 'compare'], angle: ['single', 'compare'], calibrate: ['single'],
    tarsus: ['single'], dropout: ['single'],               // 마이봄: 검판 영역 / 소실 영역 (다각형)
  };
  const POLY_TOOLS = new Set(['tarsus', 'dropout']);

  const P = {
    on: false, mode: 'multi', patient: null, caps: [], idx: -1,
    per: 16, page: 0, sel: [], adj: { ...IDENTITY }, tool: 'pan', frozen: false, pair: [null, null],
    active: null, pxmm: 0,
  };
  try { P.pxmm = Math.max(0, parseFloat(localStorage.getItem('slitlamp-pxmm')) || 0); } catch (_) { /* 저장소 차단 */ }

  const BRAND = { report_title: '슬릿램프 영상 보고서', logo: null, ...(window.SLIT_BRAND || {}) };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const clamp = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x);

  // ================= 영상 처리 엔진 =================
  const isIdentity = (a) => Object.keys(IDENTITY).every((k) => a[k] === IDENTITY[k]);
  const newCanvas = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
  const ctx2d = (c) => c.getContext('2d', { willReadFrequently: true });
  function toCanvas(src) {
    const w = src.naturalWidth || src.videoWidth || src.width, h = src.naturalHeight || src.videoHeight || src.height;
    const c = newCanvas(w, h);
    ctx2d(c).drawImage(src, 0, 0, w, h);
    return c;
  }
  const pixels = (c) => ctx2d(c).getImageData(0, 0, c.width, c.height);

  // 3×3 median 정렬망 (Devillard opt_med9)
  const MED9 = [1, 2, 4, 5, 7, 8, 0, 1, 3, 4, 6, 7, 1, 2, 4, 5, 7, 8, 0, 3, 5, 8, 4, 7, 3, 6, 1, 4, 2, 5, 4, 7, 4, 2, 6, 4, 4, 2];
  function denoise(im, mode) {
    const w = im.width, h = im.height, s = im.data, out = new ImageData(w, h), d = out.data;
    const p = new Int32Array(9), med = mode === 'median';
    for (let y = 0; y < h; y++) {
      const r0 = Math.max(0, y - 1) * w, r1 = y * w, r2 = Math.min(h - 1, y + 1) * w;
      for (let x = 0; x < w; x++) {
        const c0 = Math.max(0, x - 1), c2 = Math.min(w - 1, x + 1), o = (r1 + x) * 4;
        for (let ch = 0; ch < 3; ch++) {
          p[0] = s[(r0 + c0) * 4 + ch]; p[1] = s[(r0 + x) * 4 + ch]; p[2] = s[(r0 + c2) * 4 + ch];
          p[3] = s[(r1 + c0) * 4 + ch]; p[4] = s[(r1 + x) * 4 + ch]; p[5] = s[(r1 + c2) * 4 + ch];
          p[6] = s[(r2 + c0) * 4 + ch]; p[7] = s[(r2 + x) * 4 + ch]; p[8] = s[(r2 + c2) * 4 + ch];
          if (med) {
            for (let k = 0; k < MED9.length; k += 2) {
              const a = MED9[k], b = MED9[k + 1];
              if (p[a] > p[b]) { const t = p[a]; p[a] = p[b]; p[b] = t; }
            }
            d[o + ch] = p[4];
          } else d[o + ch] = (p[0] + p[1] + p[2] + p[3] + p[4] + p[5] + p[6] + p[7] + p[8]) / 9 + 0.5;
        }
        d[o + 3] = 255;
      }
    }
    return out;
  }

  function buildLut(a) {
    const lut = new Uint8ClampedArray(256), span = a.white - a.black;
    for (let v = 0; v < 256; v++) {
      let x = span > 0 ? clamp((v - a.black) * 255 / span, 0, 255) : v;
      x = clamp((x - 127.5) * a.contrast + 127.5 + a.brightness, 0, 255);
      if (a.gamma !== 1) x = 255 * Math.pow(x / 255, 1 / a.gamma);
      lut[v] = x;
    }
    return lut;
  }

  // 휘도에 CLAHE(8×8 타일) 적용 후 RGB를 같은 비율로 스케일
  function clahe(d, w, h, clipLimit) {
    const N = w * h, Y = new Uint8Array(N), T = 8;
    for (let i = 0, j = 0; j < N; i += 4, j++) Y[j] = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2] + 0.5;
    const tw = Math.ceil(w / T), th = Math.ceil(h / T), maps = new Float32Array(T * T * 256);
    const hist = new Float64Array(256);
    for (let ty = 0; ty < T; ty++) for (let tx = 0; tx < T; tx++) {
      const x0 = tx * tw, x1 = Math.min(w, x0 + tw), y0 = ty * th, y1 = Math.min(h, y0 + th);
      const m = (ty * T + tx) * 256, n = Math.max(0, x1 - x0) * Math.max(0, y1 - y0);
      if (!n) { for (let v = 0; v < 256; v++) maps[m + v] = v; continue; }
      hist.fill(0);
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) hist[Y[y * w + x]]++;
      const clip = Math.max(1, clipLimit * n / 256);
      let excess = 0;
      for (let v = 0; v < 256; v++) if (hist[v] > clip) { excess += hist[v] - clip; hist[v] = clip; }
      const add = excess / 256;
      let cdf = 0;
      for (let v = 0; v < 256; v++) { cdf += hist[v] + add; maps[m + v] = Math.min(255, cdf * 255 / n); }
    }
    for (let y = 0; y < h; y++) {
      let fy = (y + 0.5) / th - 0.5, ty0 = Math.floor(fy);
      if (ty0 < 0) { ty0 = 0; fy = 0; }
      const ty1 = Math.min(T - 1, ty0 + 1), ay = clamp(fy - ty0, 0, 1);
      for (let x = 0; x < w; x++) {
        let fx = (x + 0.5) / tw - 0.5, tx0 = Math.floor(fx);
        if (tx0 < 0) { tx0 = 0; fx = 0; }
        const tx1 = Math.min(T - 1, tx0 + 1), ax = clamp(fx - tx0, 0, 1);
        const j = y * w + x, v = Y[j];
        const a = maps[(ty0 * T + tx0) * 256 + v], b = maps[(ty0 * T + tx1) * 256 + v];
        const c = maps[(ty1 * T + tx0) * 256 + v], e = maps[(ty1 * T + tx1) * 256 + v];
        const y2 = (1 - ay) * ((1 - ax) * a + ax * b) + ay * ((1 - ax) * c + ax * e);
        const r = (y2 + 1) / (v + 1), i = j * 4;
        d[i] *= r; d[i + 1] *= r; d[i + 2] *= r;
      }
    }
  }

  // 언샤프 마스크 (가우시안 σ=1.2, 분리형 9탭 — 경계 인덱스는 미리 계산)
  function unsharp(d, w, h, amount) {
    const R = 4, K = 2 * R + 1, k = new Float32Array(K);
    let ks = 0;
    for (let i = 0; i < K; i++) { k[i] = Math.exp(-((i - R) ** 2) / (2 * 1.2 * 1.2)); ks += k[i]; }
    for (let i = 0; i < K; i++) k[i] /= ks;
    const xi = new Int32Array(w * K), yi = new Int32Array(h * K);
    for (let x = 0; x < w; x++) for (let t = 0; t < K; t++) xi[x * K + t] = clamp(x + t - R, 0, w - 1);
    for (let y = 0; y < h; y++) for (let t = 0; t < K; t++) yi[y * K + t] = clamp(y + t - R, 0, h - 1) * w;
    const tmp = new Float32Array(w * h * 3);
    for (let y = 0, row = 0; y < h; y++, row += w) {
      for (let x = 0; x < w; x++) {
        let r = 0, g = 0, b = 0;
        for (let t = 0, q = x * K; t < K; t++) {
          const i = (row + xi[q + t]) << 2, kk = k[t];
          r += d[i] * kk; g += d[i + 1] * kk; b += d[i + 2] * kk;
        }
        const o = (row + x) * 3; tmp[o] = r; tmp[o + 1] = g; tmp[o + 2] = b;
      }
    }
    for (let y = 0; y < h; y++) {
      const q = y * K;
      for (let x = 0; x < w; x++) {
        let r = 0, g = 0, b = 0;
        for (let t = 0; t < K; t++) {
          const o = (yi[q + t] + x) * 3, kk = k[t];
          r += tmp[o] * kk; g += tmp[o + 1] * kk; b += tmp[o + 2] * kk;
        }
        const i = (y * w + x) << 2;
        d[i] += amount * (d[i] - r); d[i + 1] += amount * (d[i + 1] - g); d[i + 2] += amount * (d[i + 2] - b);
      }
    }
  }

  /** base(캔버스) → 보정된 새 캔버스. cache 는 노이즈 제거 결과(가장 느린 단계)를 재사용 */
  function processCanvas(base, a, cache) {
    const w = base.width, h = base.height, out = newCanvas(w, h), octx = ctx2d(out);
    if (isIdentity(a)) { octx.drawImage(base, 0, 0); return out; }
    let src;
    if (a.denoise !== 'none') {
      if (cache && cache.base === base && cache.mode === a.denoise) src = cache.data;
      else { src = denoise(pixels(base), a.denoise); if (cache) Object.assign(cache, { base, mode: a.denoise, data: src }); }
    } else src = pixels(base);
    const im = new ImageData(new Uint8ClampedArray(src.data), w, h), d = im.data;
    const lut = buildLut(a), ch = a.channel, sat = a.saturation;
    const rg = a.r_gain, gg = a.g_gain, bg = a.b_gain, color = sat !== 1 || rg !== 1 || gg !== 1 || bg !== 1;
    for (let i = 0; i < d.length; i += 4) {
      let r = d[i], g = d[i + 1], b = d[i + 2];
      if (ch === 'green') { r = g; b = g; }                                  // Red-free
      else if (ch === 'red') { g = r; b = r; }
      else if (ch === 'blue') { r = b; g = b; }
      else if (ch === 'gray') { r = g = b = 0.299 * r + 0.587 * g + 0.114 * b; }
      if (color) {
        r *= rg; g *= gg; b *= bg;
        if (sat !== 1) { const y = 0.299 * r + 0.587 * g + 0.114 * b; r = y + (r - y) * sat; g = y + (g - y) * sat; b = y + (b - y) * sat; }
      }
      d[i] = lut[clamp(r + 0.5, 0, 255) | 0]; d[i + 1] = lut[clamp(g + 0.5, 0, 255) | 0]; d[i + 2] = lut[clamp(b + 0.5, 0, 255) | 0];
    }
    if (a.enhance) clahe(d, w, h, CLAHE_CLIP[a.enhance]);
    if (a.sharpen) unsharp(d, w, h, SHARP_AMOUNT[a.sharpen]);
    if (a.negative) for (let i = 0; i < d.length; i += 4) { d[i] = 255 - d[i]; d[i + 1] = 255 - d[i + 1]; d[i + 2] = 255 - d[i + 2]; }
    octx.putImageData(im, 0, 0);
    return out;
  }
  const processed = (src, a) => processCanvas(toCanvas(src), a);

  /** 휘도 히스토그램 양끝 clip 비율을 잘라 흑·백점 추정 */
  function autoLevels(c, clip = 0.005) {
    const d = pixels(c).data, hist = new Uint32Array(256), n = d.length / 4;
    for (let i = 0; i < d.length; i += 4) hist[(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2] + 0.5) | 0]++;
    let lo = 0, hi = 255, acc = 0;
    for (let v = 0; v < 256; v++) { acc += hist[v]; if (acc > clip * n) { lo = v; break; } }
    acc = 0;
    for (let v = 255; v >= 0; v--) { acc += hist[v]; if (acc > clip * n) { hi = v; break; } }
    return hi - lo < 10 ? [0, 255] : [lo, hi];
  }

  // 기하 변환
  function rotate90(c, k) {
    const W = c.width, H = c.height, o = newCanvas(H, W), x = ctx2d(o);
    if (k > 0) { x.translate(H, 0); x.rotate(Math.PI / 2); } else { x.translate(0, W); x.rotate(-Math.PI / 2); }
    x.drawImage(c, 0, 0);
    return o;
  }
  function flipCanvas(c, horizontal) {
    const o = newCanvas(c.width, c.height), x = ctx2d(o);
    if (horizontal) { x.translate(c.width, 0); x.scale(-1, 1); } else { x.translate(0, c.height); x.scale(1, -1); }
    x.drawImage(c, 0, 0);
    return o;
  }
  function cropCanvas(c, x0, y0, w, h) {
    const o = newCanvas(w, h);
    ctx2d(o).drawImage(c, x0, y0, w, h, 0, 0, w, h);
    return o;
  }
  const PT_KEYS = [['x', 'y'], ['x1', 'y1'], ['x2', 'y2'], ['x3', 'y3']];
  function mapAnn(list, f) {
    return list.map((a) => {
      const b = { ...a };
      for (const [kx, ky] of PT_KEYS) if (kx in b) [b[kx], b[ky]] = f(b[kx], b[ky]);
      if (Array.isArray(b.pts)) b.pts = b.pts.map(([x, y]) => f(x, y));
      return b;
    });
  }

  // 측정
  const lengthPx = (a) => Math.hypot(a.x2 - a.x1, a.y2 - a.y1);
  function angleDeg(a) {
    const v1 = Math.atan2(a.y1 - a.y2, a.x1 - a.x2), v2 = Math.atan2(a.y3 - a.y2, a.x3 - a.x2);
    let d = Math.abs(v1 - v2) * 180 / Math.PI;
    return d > 180 ? 360 - d : d;
  }
  const lengthLabel = (px) => (P.pxmm > 0 ? `${(px / P.pxmm).toFixed(2)} mm` : `${Math.round(px)} px`);

  // ================= 주석 그리기 =================
  function drawArrow(ctx, a, s) {
    const { x1, y1, x2, y2 } = a;
    ctx.strokeStyle = ANN_COLOR; ctx.fillStyle = ANN_COLOR;
    ctx.lineWidth = 3 * s; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    const ang = Math.atan2(y2 - y1, x2 - x1), L = 18 * s;
    ctx.beginPath();
    ctx.moveTo(x2, y2);
    ctx.lineTo(x2 - L * Math.cos(ang - Math.PI / 7), y2 - L * Math.sin(ang - Math.PI / 7));
    ctx.lineTo(x2 - L * Math.cos(ang + Math.PI / 7), y2 - L * Math.sin(ang + Math.PI / 7));
    ctx.closePath(); ctx.fill();
  }
  const FONT = '"Malgun Gothic","Apple SD Gothic Neo","Noto Sans KR",sans-serif';
  function boxedLabel(ctx, text, x, y, s, color) {
    ctx.font = `bold ${Math.round(17 * s)}px ${FONT}`;
    ctx.textBaseline = 'middle'; ctx.textAlign = 'center';
    const w = ctx.measureText(text).width + 12 * s, h = 24 * s;
    ctx.fillStyle = 'rgba(0,0,0,.7)'; ctx.fillRect(x - w / 2, y - h / 2, w, h);
    ctx.fillStyle = color; ctx.fillText(text, x, y);
    ctx.textAlign = 'left';
  }
  function drawOne(ctx, a, s) {
    ctx.setLineDash([]);
    if (a.type === 'text') {
      ctx.font = `bold ${Math.round(26 * s)}px ${FONT}`;
      ctx.textBaseline = 'top'; ctx.lineJoin = 'round';
      ctx.lineWidth = 4 * s; ctx.strokeStyle = '#000'; ctx.strokeText(a.text, a.x, a.y);
      ctx.fillStyle = ANN_COLOR; ctx.fillText(a.text, a.x, a.y);
    } else if (a.type === 'arrow') drawArrow(ctx, a, s);
    else if (a.type === 'circle') {
      ctx.strokeStyle = ANN_COLOR; ctx.lineWidth = 3 * s;
      ctx.beginPath();
      ctx.ellipse((a.x1 + a.x2) / 2, (a.y1 + a.y2) / 2, Math.abs(a.x2 - a.x1) / 2, Math.abs(a.y2 - a.y1) / 2, 0, 0, Math.PI * 2);
      ctx.stroke();
    } else if (a.type === 'length' || a.type === 'calibrate') {
      const ang = Math.atan2(a.y2 - a.y1, a.x2 - a.x1), nx = -Math.sin(ang) * 8 * s, ny = Math.cos(ang) * 8 * s;
      ctx.strokeStyle = MEAS_COLOR; ctx.lineWidth = 2.5 * s; ctx.lineCap = 'round';
      if (a.type === 'calibrate') ctx.setLineDash([10 * s, 6 * s]);
      ctx.beginPath(); ctx.moveTo(a.x1, a.y1); ctx.lineTo(a.x2, a.y2); ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(a.x1 - nx, a.y1 - ny); ctx.lineTo(a.x1 + nx, a.y1 + ny);
      ctx.moveTo(a.x2 - nx, a.y2 - ny); ctx.lineTo(a.x2 + nx, a.y2 + ny);
      ctx.stroke();
      const lx = (a.x1 + a.x2) / 2 - Math.sin(ang) * 22 * s, ly = (a.y1 + a.y2) / 2 + Math.cos(ang) * 22 * s;
      boxedLabel(ctx, a.type === 'calibrate' ? `${Math.round(lengthPx(a))} px` : lengthLabel(lengthPx(a)), lx, ly, s, MEAS_COLOR);
    } else if (a.type === 'angle') {
      ctx.strokeStyle = MEAS_COLOR; ctx.lineWidth = 2.5 * s; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(a.x1, a.y1); ctx.lineTo(a.x2, a.y2);
      if (a.x3 != null) ctx.lineTo(a.x3, a.y3);
      ctx.stroke();
      if (a.x3 == null) return;
      const t1 = Math.atan2(a.y1 - a.y2, a.x1 - a.x2), t2 = Math.atan2(a.y3 - a.y2, a.x3 - a.x2);
      let d = t2 - t1;
      while (d > Math.PI) d -= 2 * Math.PI;
      while (d < -Math.PI) d += 2 * Math.PI;
      const r = 28 * s;
      ctx.beginPath(); ctx.arc(a.x2, a.y2, r, t1, t1 + d, d < 0); ctx.stroke();
      const bis = t1 + d / 2;
      boxedLabel(ctx, `${angleDeg(a).toFixed(1)}°`, a.x2 + Math.cos(bis) * (r + 30 * s), a.y2 + Math.sin(bis) * (r + 30 * s), s, MEAS_COLOR);
    } else if (a.type === 'tarsus' || a.type === 'dropout') {
      // 마이봄: 검판(관심 영역) = 하늘색 점선, 소실 영역 = 붉은 채움
      const pts = a.pts || [];
      if (!pts.length) return;
      ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
      for (const [x, y] of pts.slice(1)) ctx.lineTo(x, y);
      if (!a.open) ctx.closePath();
      ctx.lineWidth = 2.5 * s; ctx.lineJoin = 'round';
      if (a.type === 'tarsus') {
        ctx.setLineDash([10 * s, 6 * s]); ctx.strokeStyle = '#22d3ee'; ctx.stroke(); ctx.setLineDash([]);
      } else {
        if (!a.open) { ctx.fillStyle = 'rgba(239,68,68,.32)'; ctx.fill(); }
        ctx.strokeStyle = '#ef4444'; ctx.stroke();
      }
      for (const [x, y] of a.open ? pts : []) { ctx.fillStyle = a.type === 'tarsus' ? '#22d3ee' : '#ef4444'; ctx.beginPath(); ctx.arc(x, y, 4 * s, 0, Math.PI * 2); ctx.fill(); }
    } else if (a.type === 'mbsum') {
      ctx.font = `bold ${Math.round(17 * s)}px ${FONT}`; ctx.textBaseline = 'top';
      const lines = String(a.text).split('\n'), lh = 24 * s;
      const w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 20 * s;
      ctx.fillStyle = 'rgba(0,0,0,.72)'; ctx.fillRect(a.x, a.y, w, lines.length * lh + 12 * s);
      ctx.fillStyle = '#fef08a';
      lines.forEach((l, i) => ctx.fillText(l, a.x + 10 * s, a.y + 6 * s + i * lh));
    } else if (a.type === 'crop') {
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2 * s; ctx.setLineDash([8 * s, 6 * s]);
      ctx.strokeRect(Math.min(a.x1, a.x2), Math.min(a.y1, a.y2), Math.abs(a.x2 - a.x1), Math.abs(a.y2 - a.y1));
      ctx.setLineDash([]);
    }
  }

  // ================= 캔버스 뷰어 (편집 상태 보유) =================
  class Viewer {
    constructor(canvas) {
      this.cv = canvas; this.ctx = canvas.getContext('2d');
      this.orig = null; this.base = null; this.proc = null; this.adj = { ...IDENTITY }; this.ann = [];
      this.undoStack = []; this.redoStack = []; this.lastKind = null; this.lastT = 0; this.dn = {};
      this.caption = ''; this.peer = null; this.onEdit = null; this.onActivate = null;
      this.zoom = 1; this.px = 0; this.py = 0; this.autofit = true; this.drag = null; this.temp = null;
      this.lens = null; this.apts = [];
      this.aOverlay = null; this.ghost = null;           // 표시 전용 (smart.js): 분석 마스크 · 경과 비교 고스트 — 저장본에는 안 들어감
      canvas.addEventListener('wheel', (e) => this.onWheel(e), { passive: false });
      canvas.addEventListener('pointerdown', (e) => this.onDown(e));
      canvas.addEventListener('dblclick', () => { if (this.poly) this.finishPoly(); });
      canvas.addEventListener('pointermove', (e) => this.onMove(e));
      canvas.addEventListener('pointerup', (e) => this.onUp(e));
      canvas.addEventListener('pointercancel', () => { this.drag = null; this.temp = null; this.lens = null; this.draw(); });
      canvas.addEventListener('pointerleave', () => { if (this.apts.length && !this.drag) { this.temp = null; this.draw(); } });
      new ResizeObserver(() => this.resize()).observe(canvas);
    }
    get iw() { return this.proc ? this.proc.width : 0; }
    get ih() { return this.proc ? this.proc.height : 0; }
    get src() { return this.orig; }
    // ---- 상태 ----
    setSource(src, adj = P.adj) {
      this.mbOverlay = null; this.mbAuto = null; this.poly = null; this.aOverlay = null; this.ghost = null;
      this.orig = src ? toCanvas(src) : null; this.base = this.orig;
      this.adj = { ...IDENTITY, ...adj }; this.ann = []; this.apts = []; this.temp = null; this.drag = null; this.lens = null;
      this.undoStack = []; this.redoStack = []; this.lastKind = null; this.dn = {};
      this.reprocess();
      this.autofit = true; this.fit(); this.draw(); this.edited();
    }
    resetAll() { if (this.orig) { this.setSource(this.orig, IDENTITY); } }
    reprocess() { this.proc = this.base ? processCanvas(this.base, this.adj, this.dn) : null; }
    edited() { if (this.onEdit) this.onEdit(this); }
    geometryChanged() { return !!this.base && this.base !== this.orig; }
    isModified() { return !!this.base && (this.geometryChanged() || !isIdentity(this.adj) || this.ann.length > 0); }
    snapshot() { return { base: this.base, adj: { ...this.adj }, ann: this.ann.map((a) => ({ ...a })), mbOverlay: this.mbOverlay, mbAuto: this.mbAuto }; }
    record(kind) {
      const now = performance.now();
      if (kind === 'adj' && this.lastKind === 'adj' && now - this.lastT < 1000) { this.lastT = now; return; }
      this.undoStack.push(this.snapshot());
      this.redoStack = []; this.lastKind = kind; this.lastT = now;
      // 회전·자르기마다 전체 해상도 캔버스가 쌓이므로 개수와 함께 총 화소 수로도 제한 (대용량 영상에서 탭이 멈추지 않게)
      const px = () => { const seen = new Set([this.orig, this.base]); let t = 0;
        for (const x of [...this.undoStack, ...this.redoStack]) if (x.base && !seen.has(x.base)) { seen.add(x.base); t += x.base.width * x.base.height; }
        return t; };
      while (this.undoStack.length > HISTORY_MAX || (this.undoStack.length > 1 && px() > HISTORY_PIXELS)) this.undoStack.shift();
    }
    restore(s) {
      const geo = s.base !== this.base;
      this.apts = []; this.poly = null; this.temp = null; this.drag = null;   // 그리던 도형은 좌표가 달라지므로 취소
      this.base = s.base; this.adj = s.adj; this.ann = s.ann; this.lastKind = null;
      const hasTarsus = s.ann.some((a) => a.type === 'tarsus');
      this.mbOverlay = hasTarsus && 'mbOverlay' in s ? s.mbOverlay : null;
      this.mbAuto = hasTarsus && 'mbAuto' in s ? s.mbAuto : null;
      this.reprocess();
      if (geo) { this.autofit = true; this.fit(); }
      this.draw(); this.edited();
    }
    undo() { if (!this.undoStack.length) return false; this.redoStack.push(this.snapshot()); this.restore(this.undoStack.pop()); return true; }
    redo() { if (!this.redoStack.length) return false; this.undoStack.push(this.snapshot()); this.restore(this.redoStack.pop()); return true; }
    restoreOriginal() {
      if (!this.isModified()) return false;
      this.record('orig');
      this.restore({ base: this.orig, adj: { ...IDENTITY }, ann: [], mbOverlay: null, mbAuto: null });
      return true;
    }
    setAdjust(a, record = false) {
      if (!this.base) { this.adj = { ...a }; return; }
      if (Object.keys(IDENTITY).every((k) => a[k] === this.adj[k])) return;
      if (record) this.record('adj');
      this.adj = { ...a }; this.reprocess(); this.draw(); this.edited();
    }
    addAnn(a) { this.record('ann'); this.ann.push(a); this.draw(); this.edited(); }
    clearAnn() { if (!this.ann.length) return; this.record('ann'); this.ann = []; this.mbOverlay = null; this.mbAuto = null; this.draw(); this.edited(); }
    geom(newBase, f) {
      this.record('geo');
      this.apts = []; this.poly = null; this.temp = null; this.drag = null;
      this.mbOverlay = null; this.mbAuto = null; this.aOverlay = null;
      this.base = newBase; this.ann = mapAnn(this.ann, f);
      this.reprocess(); this.autofit = true; this.fit(); this.draw(); this.edited();
    }
    rotate(k) {
      if (!this.base) return;
      const W = this.base.width, H = this.base.height;
      this.geom(rotate90(this.base, k), k > 0 ? (x, y) => [H - y, x] : (x, y) => [y, W - x]);
    }
    flip(horizontal) {
      if (!this.base) return;
      const W = this.base.width, H = this.base.height;
      this.geom(flipCanvas(this.base, horizontal), horizontal ? (x, y) => [W - x, y] : (x, y) => [x, H - y]);
    }
    cropTo(x0, y0, x1, y1) {
      if (!this.base) return false;
      const W = this.base.width, H = this.base.height;
      const ax = Math.round(clamp(Math.min(x0, x1), 0, W)), bx = Math.round(clamp(Math.max(x0, x1), 0, W));
      const ay = Math.round(clamp(Math.min(y0, y1), 0, H)), by = Math.round(clamp(Math.max(y0, y1), 0, H));
      if (bx - ax < 8 || by - ay < 8) return false;
      this.geom(cropCanvas(this.base, ax, ay, bx - ax, by - ay), (x, y) => [x - ax, y - ay]);
      return true;
    }
    measurements() {
      return this.ann.filter((a) => a.type === 'length' || a.type === 'angle')
        .map((a) => [a.type, a.type === 'length' ? lengthLabel(lengthPx(a)) : `${angleDeg(a).toFixed(1)}°`]);
    }
    // ---- 보기 ----
    resize() {
      const r = this.cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
      const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
      if (this.cv.width !== w || this.cv.height !== h) { this.cv.width = w; this.cv.height = h; }
      if (this.autofit) this.fit();
      this.draw();
    }
    fit() {
      if (!this.proc) return;
      const W = this.cv.width, H = this.cv.height;
      this.zoom = Math.min(W / this.iw, H / this.ih);
      this.px = (W - this.iw * this.zoom) / 2; this.py = (H - this.ih * this.zoom) / 2;
    }
    resetView() { this.autofit = true; this.fit(); this.draw(); }
    zoomAt(f, cx, cy, fromPeer = false) {
      if (!this.proc) return;
      if (cx == null) { cx = this.cv.width / 2; cy = this.cv.height / 2; }
      this.autofit = false;
      this.px = cx - (cx - this.px) * f; this.py = cy - (cy - this.py) * f; this.zoom *= f;
      this.draw();
      if (this.peer && !fromPeer) this.peer.zoomAt(f, cx, cy, true);
    }
    pan(dx, dy, fromPeer = false) {
      this.autofit = false; this.px += dx; this.py += dy; this.draw();
      if (this.peer && !fromPeer) this.peer.pan(dx, dy, true);
    }
    toImg(e) {
      const r = this.cv.getBoundingClientRect(), k = this.cv.width / r.width;
      const sx = (e.clientX - r.left) * k, sy = (e.clientY - r.top) * k;
      return { x: (sx - this.px) / this.zoom, y: (sy - this.py) / this.zoom, sx, sy };
    }
    // ---- 입력 ----
    onWheel(e) {
      if (!this.proc) return;
      e.preventDefault();
      const p = this.toImg(e);
      this.zoomAt(e.deltaY < 0 ? 1.25 : 0.8, p.sx, p.sy);
    }
    onDown(e) {
      if (!this.proc || e.button !== 0) return;
      if (this.onActivate) this.onActivate(this);
      const p = this.toImg(e), tool = P.tool;
      if (tool === 'text') {
        const t = window.prompt('텍스트 주석:');
        if (t && t.trim()) this.addAnn({ type: 'text', x: p.x, y: p.y, text: t.trim() });
        return;
      }
      if (POLY_TOOLS.has(tool)) {                      // 다각형: 클릭으로 점 추가, 첫 점 클릭·더블클릭·Enter 로 닫기
        const poly = this.poly || (this.poly = { type: tool, pts: [] });
        const first = poly.pts[0];
        const nearFirst = first && Math.hypot((first[0] - p.x) * this.zoom, (first[1] - p.y) * this.zoom) < 12;
        if (poly.pts.length >= 3 && nearFirst) { this.finishPoly(); return; }
        const last = poly.pts[poly.pts.length - 1];       // 더블클릭의 두 번째 클릭은 같은 점 — 무시
        if (last && Math.hypot((last[0] - p.x) * this.zoom, (last[1] - p.y) * this.zoom) < 4) return;
        poly.pts.push([p.x, p.y]);
        this.temp = { type: tool, pts: [...poly.pts], open: true };
        this.draw();
        return;
      }
      if (tool === 'angle') {
        this.apts.push([p.x, p.y]);
        if (this.apts.length === 3) {
          const [[x1, y1], [x2, y2], [x3, y3]] = this.apts;
          this.apts = []; this.temp = null;
          this.addAnn({ type: 'angle', x1, y1, x2, y2, x3, y3 });
        } else this.anglePreview(p);
        return;
      }
      this.cv.setPointerCapture(e.pointerId);
      if (tool === 'magnify') { this.drag = { lens: true }; this.lens = { sx: p.sx, sy: p.sy }; this.draw(); return; }
      if (DRAG_TOOLS.has(tool)) { this.drag = { shape: tool }; this.temp = { type: tool, x1: p.x, y1: p.y, x2: p.x, y2: p.y }; return; }
      this.drag = { sx: p.sx, sy: p.sy };
      this.cv.style.cursor = 'grabbing';
    }
    anglePreview(p) {
      const [a, b] = this.apts;
      this.temp = b ? { type: 'angle', x1: a[0], y1: a[1], x2: b[0], y2: b[1], x3: p.x, y3: p.y }
        : { type: 'angle', x1: a[0], y1: a[1], x2: p.x, y2: p.y };
      this.draw();
    }
    finishPoly() {
      const poly = this.poly;
      this.poly = null; this.temp = null;
      if (!poly || poly.pts.length < 3) { this.draw(); return; }
      if (poly.type === 'tarsus') {                     // 검판 영역은 하나만 (다시 그리면 교체)
        this.record('ann');
        this.ann = this.ann.filter((a) => a.type !== 'tarsus' && a.type !== 'mbsum');
        this.ann.push({ type: 'tarsus', pts: poly.pts });
        this.mbOverlay = null; this.mbAuto = null;
        this.draw(); this.edited();
      } else this.addAnn({ type: poly.type, pts: poly.pts });
    }
    onMove(e) {
      if (!this.drag && this.poly) {
        const p = this.toImg(e);
        this.temp = { type: this.poly.type, pts: [...this.poly.pts, [p.x, p.y]], open: true };
        this.draw(); return;
      }
      if (!this.drag) { if (P.tool === 'angle' && this.apts.length) this.anglePreview(this.toImg(e)); return; }
      const p = this.toImg(e);
      if (this.drag.lens) { this.lens = { sx: p.sx, sy: p.sy }; this.draw(); return; }
      if (this.drag.shape) { if (this.temp) { this.temp.x2 = p.x; this.temp.y2 = p.y; this.draw(); } return; }
      this.pan(p.sx - this.drag.sx, p.sy - this.drag.sy);
      this.drag = { sx: p.sx, sy: p.sy };
    }
    onUp() {
      if (!this.drag) return;
      const t = this.temp, shape = this.drag.shape;
      this.drag = null; this.temp = null; this.lens = null;
      this.cv.style.cursor = cursorFor(P.tool);
      if (shape && t && Math.hypot(t.x2 - t.x1, t.y2 - t.y1) > 4) {
        if (shape === 'crop') { this.cropTo(t.x1, t.y1, t.x2, t.y2); return; }
        if (shape === 'calibrate') { this.draw(); calibrateFrom(lengthPx(t)); return; }
        const { type, ...pts } = t;
        this.addAnn({ type, ...pts });
        return;
      }
      this.draw();
    }
    cancelTemp() { this.apts = []; this.poly = null; this.temp = null; this.lens = null; this.drag = null; this.draw(); }
    // ---- 그리기 ----
    annScale() { return Math.max(1, Math.max(this.iw, this.ih) / 900); }
    drawAnn(ctx, s, withTemp = true) {
      for (const a of this.ann) drawOne(ctx, a, s);
      if (withTemp && this.temp) {
        if (this.temp.type === 'crop') {
          const t = this.temp, x = Math.min(t.x1, t.x2), y = Math.min(t.y1, t.y2), w = Math.abs(t.x2 - t.x1), h = Math.abs(t.y2 - t.y1);
          ctx.fillStyle = 'rgba(0,0,0,.5)';
          ctx.beginPath(); ctx.rect(0, 0, this.iw, this.ih); ctx.rect(x, y, w, h); ctx.fill('evenodd');
        }
        drawOne(ctx, this.temp, s);
      }
    }
    paintImage(c) {
      c.translate(this.px, this.py); c.scale(this.zoom, this.zoom);
      c.imageSmoothingQuality = 'high';
      c.drawImage(this.proc, 0, 0);
      if (this.mbOverlay) c.drawImage(this.mbOverlay, 0, 0, this.iw, this.ih);
      if (this.ghost && this.ghost.alpha > 0) {
        c.globalAlpha = this.ghost.alpha; c.drawImage(this.ghost.cv, 0, 0, this.iw, this.ih); c.globalAlpha = 1;
      }
      if (this.aOverlay) c.drawImage(this.aOverlay, 0, 0, this.iw, this.ih);
      this.drawAnn(c, this.annScale());
    }
    draw() {
      const c = this.ctx, W = this.cv.width, H = this.cv.height, dpr = window.devicePixelRatio || 1;
      c.setTransform(1, 0, 0, 1, 0, 0);
      c.fillStyle = '#000'; c.fillRect(0, 0, W, H);
      if (!this.proc) {
        c.fillStyle = '#64748b'; c.font = `${14 * dpr}px sans-serif`;
        c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('영상 없음', W / 2, H / 2); c.textAlign = 'left';
        return;
      }
      c.save(); this.paintImage(c); c.restore();
      if (this.lens) {                                     // 돋보기 렌즈 (2.5배)
        const { sx, sy } = this.lens, R = 90 * dpr, f = 2.5;
        c.save();
        c.beginPath(); c.arc(sx, sy, R, 0, Math.PI * 2); c.clip();
        c.fillStyle = '#000'; c.fillRect(sx - R, sy - R, 2 * R, 2 * R);
        c.translate(sx, sy); c.scale(f, f); c.translate(-sx, -sy);
        this.paintImage(c);
        c.restore();
        c.beginPath(); c.arc(sx, sy, R, 0, Math.PI * 2);
        c.lineWidth = 3 * dpr; c.strokeStyle = '#facc15'; c.stroke();
      }
      if (this.caption) {
        c.font = `${12 * dpr}px sans-serif`; c.textBaseline = 'middle';
        const w = c.measureText(this.caption).width + 16 * dpr;
        c.fillStyle = 'rgba(0,0,0,.6)'; c.fillRect(8 * dpr, 8 * dpr, w, 22 * dpr);
        c.fillStyle = '#e2e8f0'; c.fillText(this.caption, 16 * dpr, 19 * dpr);
      }
    }
    /** 보정 + 편집 + 주석·측정을 영상 해상도로 합성 (임시 미리보기 제외) */
    renderFull() {
      if (!this.proc) return null;
      const c = newCanvas(this.iw, this.ih), x = c.getContext('2d');
      x.drawImage(this.proc, 0, 0);
      if (this.mbOverlay) x.drawImage(this.mbOverlay, 0, 0, this.iw, this.ih);
      this.drawAnn(x, this.annScale(), false);
      return c;
    }
  }
  const cursorFor = (t) => (t === 'pan' ? 'grab' : t === 'magnify' ? 'zoom-in' : 'crosshair');

  const V = { single: new Viewer($('pcv-single')), left: new Viewer($('pcv-left')), right: new Viewer($('pcv-right')) };
  V.left.peer = V.right; V.right.peer = V.left;

  // ================= 데이터 =================
  // 최근 사용 순(LRU) 캐시 — 오래 쓰면 메모리가 계속 늘지 않도록 개수 제한
  const imgCache = new Map();
  const IMG_CACHE_MAX = 48;
  function loadImage(url) {
    if (imgCache.has(url)) {
      const hit = imgCache.get(url);
      imgCache.delete(url); imgCache.set(url, hit);       // 최근 사용으로 이동
      return hit;
    }
    while (imgCache.size >= IMG_CACHE_MAX) imgCache.delete(imgCache.keys().next().value);
    const p = new Promise((res, rej) => {
      const im = new Image();
      im.onload = () => res(im);
      im.onerror = () => rej(new Error('이미지를 불러오지 못했습니다.'));
      im.src = url;
    });
    imgCache.set(url, p);
    p.catch(() => imgCache.delete(url));
    return p;
  }

  const pageCount = () => Math.max(1, Math.ceil(P.caps.length / P.per));

  let reloadSeq = 0;
  async function reload(selectLast = false) {
    const tok = ++reloadSeq;
    const p = App.currentPatient() || null;
    const changed = !p || !P.patient || p.id !== P.patient.id;
    let caps = [];
    if (p) {
      try { caps = (await App.api(`/captures?patient_id=${p.id}&order=asc`)).filter((c) => c.media_type === 'photo'); }
      catch (e) { if (tok === reloadSeq) App.toast('영상 목록 조회 실패: ' + e.message, 'error'); }
    }
    if (tok !== reloadSeq) return;                       // 더 최근 요청(다른 환자)이 있으면 늦은 결과는 버림
    P.patient = p; P.caps = caps;
    if (changed) P.sel = [];
    P.sel = P.sel.filter((i) => i < P.caps.length);
    if (selectLast || changed || P.idx < 0 || P.idx >= P.caps.length) P.idx = P.caps.length - 1;
    P.page = (selectLast || changed) ? pageCount() - 1 : Math.min(P.page, pageCount() - 1);
    updateInfo();
    renderMulti();
    if (P.mode === 'single') await showCurrent(!changed && !selectLast);
    if (P.mode === 'compare' && changed) await setMode('multi');
    updateSide();
  }

  function caption(c, n) {
    const t = [c.captured_at, EYE_KO[c.eye] || '', c.note || ''].filter(Boolean).join('  ·  ');
    return n ? `[${n}/${P.caps.length}]  ${t}` : t;
  }

  function updateInfo() {
    const p = P.patient, el = $('pro-info');
    if (!p) { el.textContent = '환자를 선택하세요'; return; }
    const sex = { M: '남', F: '여', O: '기타' }[p.sex] || '';
    const bits = [`<b>${esc(p.name)}</b> (${esc(p.chart_no || '-')})`];
    if (sex) bits.push(sex);
    if (p.birth_date) bits.push(esc(p.birth_date));
    bits.push(`진단명: <b>${esc(p.diagnosis || '-')}</b>`);
    bits.push(`담당의: ${esc(p.doctor || '-')}`);
    bits.push(`영상 ${P.caps.length}장`);
    el.innerHTML = bits.join('&nbsp;&nbsp;·&nbsp;&nbsp;');
  }

  function updateSide() {
    const el = $('pro-imginfo'), c = P.caps[P.idx];
    if (P.mode === 'single' && c) {
      el.textContent = `${P.idx + 1} / ${P.caps.length}\n촬영: ${c.captured_at}\n안구: ${EYE_KO[c.eye] || '미지정'}\n코멘트: ${c.note || '-'}`;
    } else if (P.mode === 'multi') {
      el.textContent = `페이지 ${P.page + 1} / ${pageCount()}  ·  ${P.per}장 보기\n전체 ${P.caps.length}장  ·  선택 ${P.sel.length}장`;
    } else if (P.mode === 'compare') {
      el.textContent = '2장 비교 (확대·이동 동기화)\n' +
        P.pair.filter(Boolean).map((x, i) => `${i ? '우' : '좌'}: ${x.captured_at} ${x.ref ? '· ' + x.note : EYE_KO[x.eye] || ''}`).join('\n');
    } else if (P.mode === 'live') {
      el.textContent = 'LIVE — ' + (P.frozen ? '정지됨 · SAVE로 저장' : '실시간');
    } else el.textContent = '영상이 없습니다';
    $('pro-selinfo').textContent = P.sel.length
      ? `선택 ${P.sel.length}/2 — 2장이면 '비교', 지우려면 '삭제'(Delete).`
      : "MULTI에서 영상 2장을 클릭해 선택한 뒤 '비교'를 누르세요.";
    $('pro-delete').disabled = !deleteTargets().length;
  }

  // ================= 삭제 =================
  /** 지금 '삭제'를 누르면 지워질 영상 — MULTI: 선택한 영상, 1화면: 현재 영상 (참조 사진은 제외) */
  function deleteTargets() {
    const own = (c) => c && !c.ref;
    if (P.mode === 'multi') return [...P.sel].sort((a, b) => a - b).map((i) => P.caps[i]).filter(own);
    if (P.mode === 'single' && own(P.caps[P.idx])) return [P.caps[P.idx]];
    return [];
  }
  let deleting = false;
  async function deleteSelected(confirmFirst = true) {
    if (deleting) return 0;
    const targets = deleteTargets();
    if (!targets.length) {
      App.toast('삭제할 영상이 없습니다. MULTI에서 영상을 클릭해 선택하거나 1화면에서 열어 주세요.', 'error');
      return 0;
    }
    const desc = targets.length === 1 ? caption(targets[0]) : `${targets.length}장`;
    if (confirmFirst && !window.confirm(`선택한 영상 ${desc}을(를) 삭제하시겠습니까?\n\n저장된 파일과 기록이 함께 지워지며 되돌릴 수 없습니다.`)) return 0;
    deleting = true;
    let n = 0;
    try {
      for (const c of targets) {
        try { await App.api(`/captures/${c.id}`, { method: 'DELETE' }); n++; }
        catch (e) { App.toast(`삭제 실패 (${caption(c)}): ${e.message}`, 'error'); }
      }
    } finally { deleting = false; }
    P.sel = [];
    await reload(false);                                 // 1화면: 같은 자리(다음 영상)를, 끝이었다면 마지막 영상을 표시
    App.renderThumbs();
    if (n) App.toast(`영상 ${n}장을 삭제했습니다.`, 'success');
    return n;
  }

  // ================= 모드 =================
  let modeSeq = 0;
  async function setMode(mode) {
    const tok = ++modeSeq;
    if (mode === 'compare' && pendingPair) applyPair();          // 참조 샘플 비교 등 지정한 두 장
    else if (mode === 'compare' && !(await prepareCompare())) mode = P.mode === 'compare' ? 'multi' : P.mode;
    if (tok !== modeSeq) return;                        // 비교 영상을 불러오는 사이 다른 모드를 고름
    P.mode = mode;
    document.querySelectorAll('#pro-display .pro-view').forEach((v) => v.classList.toggle('on', v.dataset.view === mode));
    document.querySelectorAll('#pro-modes .pbtn').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
    const live = mode === 'live';
    for (const id of ['pro-capture', 'pro-save', 'pro-eye']) $(id).disabled = !live;
    $('pro-per').disabled = mode !== 'multi';
    $('pro-comment').disabled = mode !== 'single';
    if (mode === 'single') { await showCurrent(); V.single.resize(); }
    if (mode === 'compare') { V.left.resize(); V.right.resize(); }
    if (live) attachLive();
    for (const x of [V.left, V.right]) x.cv.classList.toggle('active', mode === 'compare' && x === active());
    setTool(P.tool);
    const av = active();
    if (av) syncControls(av.adj);
    refreshEdit();
    updateSide();
    document.dispatchEvent(new CustomEvent('slit:pro-mode', { detail: mode }));
  }

  let showSeq = 0;
  async function showCurrent(keepEdits = false) {
    const tok = ++showSeq;
    const c = P.caps[P.idx];
    if (!c) { V.single.caption = ''; V.single.capId = null; V.single.setSource(null); updateSide(); return; }
    if (keepEdits && V.single.capId === c.id && V.single.orig) {   // 같은 영상이면 편집 중인 내용 유지 (환자정보 수정 등)
      V.single.caption = caption(c, P.idx + 1); V.single.draw(); updateSide(); return;
    }
    try {
      const im = await loadImage(c.url);
      if (tok !== showSeq) return;                     // 그 사이 다른 영상으로 넘어감 → 늦게 온 영상은 버림
      V.single.caption = caption(c, P.idx + 1);
      V.single.capId = c.id;
      V.single.setSource(im);
      $('mb-lid').value = /하안검/.test(c.note || '') ? 'lower' : 'upper';
      mbRender();
      document.dispatchEvent(new CustomEvent('slit:pro-image', { detail: c }));
    } catch (e) { if (tok === showSeq) App.toast(e.message, 'error'); }
    updateSide();
  }

  // ================= MULTI =================
  function renderMulti() {
    const g = $('pro-multi');
    g.classList.toggle('n36', P.per === 36);
    g.innerHTML = '';
    const start = P.page * P.per;
    for (let k = 0; k < P.per; k++) {
      const i = start + k, c = P.caps[i];
      const cell = document.createElement('div');
      cell.className = 'mcell';
      if (!c) { cell.classList.add('empty'); g.appendChild(cell); continue; }
      cell.dataset.i = String(i);
      const when = (c.captured_at || '').slice(2, 16).replace(/-/g, '.');
      const eye = c.eye && c.eye !== 'NA' ? c.eye : '';
      cell.innerHTML = `<img src="${esc(c.url)}" alt="" loading="lazy" draggable="false">` +
        `<div class="mcap"><span class="mno">${i + 1}</span>${eye ? `<span class="meye e-${esc(eye)}">${esc(eye)}</span>` : ''}` +
        `<span>${esc(when)}</span>${c.note ? `<span class="mnote">${esc(c.note)}</span>` : ''}</div>` +
        '<span class="mbadge" hidden></span>';
      cell.title = caption(c, i + 1);
      cell.addEventListener('click', () => {
        const now = performance.now();
        if (!(lastClick.i === i && now - lastClick.t < 500)) lastClick = { i, t: now, before: [...P.sel] };
        toggleSel(i);
      });
      cell.addEventListener('dblclick', () => {
        // 더블클릭은 '열기' — 앞선 클릭으로 바뀐 선택을 되돌린다
        if (lastClick.i === i && lastClick.before) { P.sel = lastClick.before; refreshSel(); }
        openIndex(i);
      });
      g.appendChild(cell);
    }
    refreshSel();
  }
  let lastClick = { i: -1, t: 0, before: null };
  function refreshSel() {
    document.querySelectorAll('#pro-multi .mcell[data-i]').forEach((cell) => {
      const rank = P.sel.indexOf(+cell.dataset.i);
      cell.classList.toggle('sel', rank >= 0);
      const b = cell.querySelector('.mbadge');
      b.hidden = rank < 0;
      b.textContent = rank >= 0 ? `선택 ${rank + 1}` : '';
    });
    updateSide();
  }
  function toggleSel(i) {
    const k = P.sel.indexOf(i);
    if (k >= 0) P.sel.splice(k, 1);
    else { P.sel.push(i); if (P.sel.length > 2) P.sel.shift(); }
    refreshSel();
  }
  function openIndex(i) { if (P.caps[i]) { P.idx = i; return setMode('single'); } }
  function step(d) {
    if (P.mode === 'multi') {
      const np = Math.min(Math.max(0, P.page + d), pageCount() - 1);
      if (np !== P.page) { P.page = np; renderMulti(); updateSide(); }
    } else if (P.mode === 'single' && P.caps.length) {
      const ni = Math.min(Math.max(0, P.idx + d), P.caps.length - 1);
      if (ni !== P.idx) { P.idx = ni; showCurrent(); }
    }
  }
  function setPer(n) {
    P.per = Number(n) === 36 ? 36 : 16;
    $('pro-per').value = String(P.per);
    P.page = Math.min(P.page, pageCount() - 1);
    renderMulti(); updateSide();
  }

  // ================= 비교 =================
  /** 지정한 두 영상을 비교 화면에 — { src(이미지·캔버스), cap(영상 레코드 또는 {ref:true, captured_at, note}) } */
  let pendingPair = null;
  function applyPair() {
    const [l, r] = pendingPair;
    pendingPair = null;
    P.pair = [l.cap, r.cap];
    V.left.caption = l.caption || caption(l.cap); V.right.caption = r.caption || caption(r.cap);
    V.left.setSource(l.src); V.right.setSource(r.src);
    P.active = V.left;
  }
  function compareImages(left, right) {
    pendingPair = [left, right];
    return setMode('compare');
  }
  async function prepareCompare() {
    let i, j;
    if (P.sel.length === 2) [i, j] = [...P.sel].sort((a, b) => a - b);
    else if (P.mode === 'single' && P.idx > 0) { i = P.idx - 1; j = P.idx; }
    else { App.toast('MULTI에서 비교할 영상 2장을 클릭해 선택하세요.', 'error'); return false; }
    const a = P.caps[i], b = P.caps[j];
    try {
      const [ia, ib] = await Promise.all([loadImage(a.url), loadImage(b.url)]);
      P.pair = [a, b];
      V.left.caption = caption(a, i + 1); V.right.caption = caption(b, j + 1);
      V.left.setSource(ia); V.right.setSource(ib);
      P.active = V.left;
      return true;
    } catch (e) { App.toast(e.message, 'error'); return false; }
  }

  // ================= 보정 · 편집 =================
  const SLIDERS = [  // [id, 필드, 슬라이더→값, 값→슬라이더, 표시]
    ['adj-bright', 'brightness', (v) => v, (x) => x, (x) => String(x)],
    ['adj-contrast', 'contrast', (v) => v / 100, (x) => Math.round(x * 100), (x) => x.toFixed(2)],
    ['adj-gamma', 'gamma', (v) => v / 100, (x) => Math.round(x * 100), (x) => x.toFixed(2)],
    ['adj-sat', 'saturation', (v) => v / 100, (x) => Math.round(x * 100), (x) => x.toFixed(2)],
    ['adj-r', 'r_gain', (v) => v / 100, (x) => Math.round(x * 100), (x) => x.toFixed(2)],
    ['adj-g', 'g_gain', (v) => v / 100, (x) => Math.round(x * 100), (x) => x.toFixed(2)],
    ['adj-b', 'b_gain', (v) => v / 100, (x) => Math.round(x * 100), (x) => x.toFixed(2)],
    ['adj-black', 'black', (v) => v, (x) => x, (x) => String(x)],
    ['adj-white', 'white', (v) => v, (x) => x, (x) => String(x)],
  ];
  const OUT_ID = { 'adj-bright': 'o-bright', 'adj-contrast': 'o-contrast', 'adj-gamma': 'o-gamma', 'adj-sat': 'o-sat',
    'adj-r': 'o-r', 'adj-g': 'o-g', 'adj-b': 'o-b', 'adj-black': 'o-black', 'adj-white': 'o-white' };

  const targets = () => (P.mode === 'single' ? [V.single] : P.mode === 'compare' ? [V.left, V.right] : []);
  /** 편집 명령(회전·실행 취소·자동 대비 등)이 적용될 뷰어 */
  function active() {
    if (P.mode === 'single') return V.single;
    if (P.mode === 'compare') return P.active === V.right ? V.right : V.left;
    return null;
  }

  function readAdj() {
    const a = {};
    for (const [id, k, from, , show] of SLIDERS) { a[k] = from(+$(id).value); $(OUT_ID[id]).value = show(a[k]); }
    // 흑점 ≥ 백점이 되지 않게
    if (a.white - a.black < 5) { a.white = Math.min(255, a.black + 5); $('adj-white').value = a.white; $('o-white').value = String(a.white); }
    a.negative = $('adj-neg').checked;
    a.channel = $('adj-channel').value;
    a.denoise = $('adj-denoise').value;
    a.sharpen = +$('adj-sharpen').value;
    a.enhance = +$('adj-enhance').value;
    P.adj = a;
    return a;
  }
  function syncControls(a) {
    const x = { ...IDENTITY, ...a };
    for (const [id, k, , to] of SLIDERS) $(id).value = to(x[k]);
    $('adj-neg').checked = x.negative;
    $('adj-channel').value = x.channel;
    $('adj-denoise').value = x.denoise;
    $('adj-sharpen').value = String(x.sharpen);
    $('adj-enhance').value = String(x.enhance);
    readAdj();
  }
  let adjRaf = 0, histRaf = 0;
  function applyAdjNow() { for (const v of targets()) v.setAdjust(P.adj, true); refreshEdit(); }
  function onAdj() { readAdj(); cancelAnimationFrame(adjRaf); adjRaf = requestAnimationFrame(applyAdjNow); }
  function setAdj(a) { syncControls(a); cancelAnimationFrame(adjRaf); applyAdjNow(); }
  function autoContrast() {
    const v = active();
    if (!v || !v.base) { App.toast('보정할 영상이 없습니다.', 'error'); return null; }
    const pre = processCanvas(v.base, { ...IDENTITY, channel: P.adj.channel, denoise: P.adj.denoise }, v.dn);
    const [lo, hi] = autoLevels(pre);
    setAdj({ ...P.adj, black: lo, white: hi });
    return [lo, hi];
  }

  function onViewerEdited(v) {
    if (v === V.single) mbRender();
    if (v !== active()) return;
    syncControls(v.adj);
    refreshEdit();
  }
  function onViewerActivated(v) {
    if (P.mode !== 'compare' || P.active === v) return;
    P.active = v;
    for (const x of [V.left, V.right]) x.cv.classList.toggle('active', x === v);
    syncControls(v.adj);
    refreshEdit();
  }
  for (const v of Object.values(V)) { v.onEdit = onViewerEdited; v.onActivate = onViewerActivated; }

  function refreshEdit() {
    const v = active(), single = P.mode === 'single';
    for (const id of ['ed-rot-l', 'ed-rot-r', 'ed-flip-h', 'ed-flip-v']) $(id).disabled = !single || !v?.base;
    $('ed-undo').disabled = !v?.undoStack.length;
    $('ed-redo').disabled = !v?.redoStack.length;
    $('ed-orig').disabled = !v?.isModified();
    $('pro-clear').disabled = !targets().some((x) => x.ann.length);
    document.querySelectorAll('.pro-side [data-tool]').forEach((b) => { b.disabled = !TOOL_MODES[b.dataset.tool].includes(P.mode); });
    $('adj-auto').disabled = !v?.base;
    cancelAnimationFrame(histRaf); histRaf = requestAnimationFrame(drawHist);
  }

  // 히스토그램 (현재 보정 결과 기준: 휘도 채움 + RGB 선)
  function drawHist() {
    const cv = $('pro-hist'), x = cv.getContext('2d'), W = cv.width, H = cv.height, v = active();
    x.clearRect(0, 0, W, H);
    if (!v?.proc) return;
    const d = pixels(v.proc).data, step = Math.max(1, Math.floor(d.length / 4 / 120000)) * 4;
    const hs = { l: new Uint32Array(256), r: new Uint32Array(256), g: new Uint32Array(256), b: new Uint32Array(256) };
    for (let i = 0; i < d.length; i += step) {
      hs.r[d[i]]++; hs.g[d[i + 1]]++; hs.b[d[i + 2]]++;
      hs.l[(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2] + 0.5) | 0]++;
    }
    let max = 1;
    for (const h of Object.values(hs)) for (let k = 1; k < 255; k++) max = Math.max(max, h[k]);   // 양끝 포화는 스케일에서 제외
    const px = (k) => (k / 255) * (W - 1), py = (n) => H - Math.min(H, (n / max) * (H - 2));
    x.beginPath(); x.moveTo(0, H);
    for (let k = 0; k < 256; k++) x.lineTo(px(k), py(hs.l[k]));
    x.lineTo(W, H); x.closePath(); x.fillStyle = 'rgba(203,213,225,.55)'; x.fill();
    for (const [k, col] of [['r', '#ef4444'], ['g', '#22c55e'], ['b', '#3b82f6']]) {
      x.beginPath();
      for (let i = 0; i < 256; i++) (i ? x.lineTo : x.moveTo).call(x, px(i), py(hs[k][i]));
      x.strokeStyle = col; x.lineWidth = 1; x.stroke();
    }
    x.fillStyle = 'rgba(250,204,21,.9)';                     // 흑·백점 표시
    x.fillRect(px(P.adj.black), 0, 1, H); x.fillRect(px(P.adj.white) - 1, 0, 1, H);
  }

  function rotate(k) { if (P.mode === 'single') V.single.rotate(k); }
  function flip(h) { if (P.mode === 'single') V.single.flip(h); }
  function undo() { const v = active(); return !!(v && v.undo()); }
  function redo() { const v = active(); return !!(v && v.redo()); }
  function restoreOriginal() { const v = active(); return !!(v && v.restoreOriginal()); }

  // 눈금 보정 (px/mm) — 브라우저에 보관
  function setPxPerMm(val) {
    P.pxmm = Math.max(0, Number(val) || 0);
    try { localStorage.setItem('slitlamp-pxmm', String(P.pxmm)); } catch (_) { /* 저장소 차단 */ }
    $('pro-calib').textContent = P.pxmm > 0 ? `눈금: 1 mm = ${P.pxmm.toFixed(1)} px` : '눈금: 미보정 (px 단위)';
    $('pro-calib-clear').disabled = !(P.pxmm > 0);
    for (const v of Object.values(V)) v.draw();
  }
  function calibrateFrom(px) {
    const t = window.prompt(`그은 선: ${Math.round(px)} px\n이 선의 실제 길이(mm)를 입력하세요:`, '10');
    const mm = parseFloat(String(t ?? '').replace(',', '.'));
    if (mm > 0) { setPxPerMm(px / mm); App.toast(`눈금 보정: 1 mm = ${(px / mm).toFixed(1)} px`, 'success'); }
    setTool('length');
  }

  // ================= 도구 =================
  function setTool(t) {
    if (!TOOL_MODES[t] || (!TOOL_MODES[t].includes(P.mode) && t !== 'pan')) t = 'pan';
    P.tool = t;
    document.querySelectorAll('.pro-side [data-tool]').forEach((b) => b.classList.toggle('on', b.dataset.tool === t));
    for (const v of Object.values(V)) { v.cancelTemp(); v.cv.style.cursor = cursorFor(t); }
  }
  function clearAnn() { for (const v of targets()) v.clearAnn(); refreshEdit(); }
  // ================= 마이봄샘 분석 (적외선 meibography) =================
  // 검판(tarsal plate) 영역 안에서 샘이 없는 면적 비율 = 소실률(gland dropout)
  //   Arita meiboscore: 0 소실 없음 · 1 < 1/3 · 2 1/3~2/3 · 3 > 2/3  (안검별, 상+하 합산 = 눈별 0~6)
  //   Pult meiboscale: 0% · <25% · 25~50% · 51~75% · >75%
  const LID_KO = { upper: '상안검', lower: '하안검' };
  function polyMask(polys, w, h, sc) {
    const c = newCanvas(w, h), x = c.getContext('2d');
    x.fillStyle = '#fff';
    for (const pts of polys) {
      x.beginPath(); x.moveTo(pts[0][0] * sc, pts[0][1] * sc);
      for (const [px, py] of pts.slice(1)) x.lineTo(px * sc, py * sc);
      x.closePath(); x.fill();
    }
    const d = x.getImageData(0, 0, w, h).data, m = new Uint8Array(w * h);
    for (let i = 0; i < m.length; i++) m[i] = d[i * 4 + 3] > 127 ? 1 : 0;
    return m;
  }
  /** 이진 마스크 팽창 (정사각 반경 r) — 행·열 누적합으로 O(N) */
  function dilate(m, w, h, r) {
    const tmp = new Uint8Array(w * h), out = new Uint8Array(w * h), row = new Int32Array(w + 1), col = new Int32Array(h + 1);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) row[x + 1] = row[x] + m[y * w + x];
      for (let x = 0; x < w; x++) tmp[y * w + x] = row[Math.min(w, x + r + 1)] - row[Math.max(0, x - r)] > 0 ? 1 : 0;
    }
    for (let x = 0; x < w; x++) {
      for (let y = 0; y < h; y++) col[y + 1] = col[y] + tmp[y * w + x];
      for (let y = 0; y < h; y++) out[y * w + x] = col[Math.min(h, y + r + 1)] - col[Math.max(0, y - r)] > 0 ? 1 : 0;
    }
    return out;
  }
  const erode = (m, w, h, r) => { const inv = m.map((v) => 1 - v), d = dilate(inv, w, h, r); return d.map((v) => 1 - v); };
  const scaleOf = (v) => Math.min(1, 900 / Math.max(v.base.width, v.base.height));

  /** 자동 분석 (추정): CLAHE 대비 향상 → 배경 대비 밝은 샘 검출 → 샘 주변이 아닌 검판 면적 = 소실 */
  function mbAuto(v) {
    const tar = v.ann.find((a) => a.type === 'tarsus');
    if (!v.base || !tar) return null;
    const sc = scaleOf(v), w = Math.max(8, Math.round(v.base.width * sc)), h = Math.max(8, Math.round(v.base.height * sc));
    const c = newCanvas(w, h); ctx2d(c).drawImage(v.base, 0, 0, w, h);
    const im = pixels(c), d = im.data, N = w * h;
    for (let i = 0; i < d.length; i += 4) { const y = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]; d[i] = d[i + 1] = d[i + 2] = y; }
    clahe(d, w, h, 3.0);
    const Y = new Float32Array(N);
    for (let j = 0; j < N; j++) Y[j] = d[j * 4];
    // 배경(큰 상자 평균) — 적분 영상
    const r = Math.max(4, Math.round(w / 30)), I = new Float64Array((w + 1) * (h + 1));
    for (let y = 0; y < h; y++) { let rs = 0; for (let x = 0; x < w; x++) { rs += Y[y * w + x]; I[(y + 1) * (w + 1) + x + 1] = I[y * (w + 1) + x + 1] + rs; } }
    const roi = polyMask([tar.pts], w, h, sc);
    const diff = new Float32Array(N);
    let n = 0, sum = 0, sum2 = 0;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const x0 = Math.max(0, x - r), x1 = Math.min(w, x + r + 1), y0 = Math.max(0, y - r), y1 = Math.min(h, y + r + 1);
      const area = (x1 - x0) * (y1 - y0);
      const bg = (I[y1 * (w + 1) + x1] - I[y0 * (w + 1) + x1] - I[y1 * (w + 1) + x0] + I[y0 * (w + 1) + x0]) / area;
      const j = y * w + x, dv = Y[j] - bg;
      diff[j] = dv;
      if (roi[j]) { n++; sum += dv; sum2 += dv * dv; }
    }
    if (n < 50) return null;
    const mean = sum / n, sd = Math.sqrt(Math.max(0, sum2 / n - mean * mean));
    const thr = mean + 0.3 * sd;
    let gland = new Uint8Array(N);
    for (let j = 0; j < N; j++) gland[j] = roi[j] && diff[j] > thr ? 1 : 0;
    gland = dilate(erode(gland, w, h, 1), w, h, 1);              // 잡티 제거 (열림)
    const R = Math.max(3, Math.round(w / 50));
    const cov = dilate(gland, w, h, R);
    let lost = 0;
    const ov = newCanvas(w, h), ox = ov.getContext('2d'), od = ox.createImageData(w, h);
    for (let j = 0; j < N; j++) {
      if (!roi[j]) continue;
      const o = j * 4;
      if (gland[j]) { od.data[o] = 34; od.data[o + 1] = 211; od.data[o + 2] = 238; od.data[o + 3] = 120; }
      else if (!cov[j]) { lost++; od.data[o] = 239; od.data[o + 1] = 68; od.data[o + 2] = 68; od.data[o + 3] = 95; }
    }
    ox.putImageData(od, 0, 0);
    // 샘 개수(추정): 검판 세로 중앙 부근 가로선 7개에서 샘 구간 수의 중앙값
    let ys = [], minY = h, maxY = 0;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (roi[y * w + x]) { if (y < minY) minY = y; if (y > maxY) maxY = y; }
    for (let k = 0; k < 7; k++) {
      const y = Math.round(minY + (maxY - minY) * (0.3 + 0.4 * k / 6));
      let runs = 0, inRun = false, len = 0;
      for (let x = 0; x < w; x++) {
        const g = gland[y * w + x] && roi[y * w + x];
        if (g) { len++; if (!inRun && len >= 2) { runs++; inRun = true; } } else { inRun = false; len = 0; }
      }
      ys.push(runs);
    }
    ys.sort((a, b) => a - b);
    return { loss: lost / n * 100, count: ys[3], overlay: ov };
  }
  function mbResult(v) {
    const tar = v.ann.find((a) => a.type === 'tarsus');
    if (!tar || !v.base) return null;
    const drops = v.ann.filter((a) => a.type === 'dropout' && a.pts && a.pts.length >= 3);
    let loss, method;
    if (drops.length) {                                         // 수동 소실 영역이 있으면 그 값을 우선
      const sc = scaleOf(v), w = Math.round(v.base.width * sc), h = Math.round(v.base.height * sc);
      const roi = polyMask([tar.pts], w, h, sc), dm = polyMask(drops.map((a) => a.pts), w, h, sc);
      let n = 0, k = 0;
      for (let j = 0; j < roi.length; j++) if (roi[j]) { n++; if (dm[j]) k++; }
      loss = n ? k / n * 100 : 0; method = '수동';
    } else if (v.mbAuto) { loss = v.mbAuto.loss; method = '자동 추정'; }
    else return { method: null };
    const arita = loss < 1 ? 0 : loss < 100 / 3 ? 1 : loss <= 200 / 3 ? 2 : 3;
    const pult = loss < 1 ? '0%' : loss < 25 ? '<25%' : loss <= 50 ? '25–50%' : loss <= 75 ? '51–75%' : '>75%';
    return { loss, method, arita, pult, count: v.mbAuto ? v.mbAuto.count : null, lid: $('mb-lid').value };
  }
  const mbNote = (m) => `마이봄 분석 ${LID_KO[m.lid] || ''} · 소실 ${m.loss.toFixed(0)}% · Arita ${m.arita}`;
  function mbRender() {
    const v = V.single, box = $('mb-result');
    if (!box) return;
    if (P.mode !== 'single' || !v.base) { box.innerHTML = '<span class="mb-muted">1화면에서 마이봄 영상을 여세요.</span>'; return; }
    const m = mbResult(v);
    if (!m) { box.innerHTML = '<span class="mb-muted">① [검판 영역]으로 눈꺼풀 검판(샘이 있는 판) 둘레를 클릭해 그리세요.</span>'; return; }
    if (!m.method) { box.innerHTML = '<span class="mb-muted">② [자동 분석]을 누르거나 [소실 영역]으로 샘이 없어진 부분을 그리세요.</span>'; return; }
    box.innerHTML = `<div class="mb-big">${m.loss.toFixed(1)}<small>%</small></div><div class="mb-cap">샘 소실률 (${m.method})</div>`
      + `<div class="mb-grid"><span>Arita meiboscore</span><b>${m.arita} / 3</b><span>Pult meiboscale</span><b>${m.pult}</b>`
      + (m.count != null ? `<span>샘 개수 (추정)</span><b>${m.count}</b>` : '') + `<span>안검</span><b>${LID_KO[m.lid]}</b></div>`;
  }
  function mbEnhance() {
    if (P.mode !== 'single') return;
    setAdj({ ...IDENTITY, channel: 'gray', enhance: 3, sharpen: 1 });
    autoContrast();
  }
  function mbRunAuto() {
    const v = V.single;
    if (P.mode !== 'single' || !v.base) return null;
    if (!v.ann.some((a) => a.type === 'tarsus')) { App.toast('먼저 [검판 영역]을 그리세요.', 'error'); setTool('tarsus'); return null; }
    const r = mbAuto(v);
    if (!r) { App.toast('검판 영역이 너무 작습니다.', 'error'); return null; }
    v.mbAuto = { loss: r.loss, count: r.count }; v.mbOverlay = r.overlay;
    v.draw(); mbRender();
    return v.mbAuto;
  }
  function mbClear() {
    const v = V.single;
    if (!v.ann.some((a) => ['tarsus', 'dropout', 'mbsum'].includes(a.type)) && !v.mbOverlay) return;
    v.record('ann');
    v.ann = v.ann.filter((a) => !['tarsus', 'dropout', 'mbsum'].includes(a.type));
    v.mbOverlay = null; v.mbAuto = null;
    v.draw(); v.edited(); mbRender();
  }
  async function mbSave() {
    const v = V.single, m = P.mode === 'single' ? mbResult(v) : null;
    if (!m || !m.method) { App.toast('분석 결과가 없습니다 (검판 영역 + 자동 분석 또는 소실 영역).', 'error'); return 0; }
    const tar = v.ann.find((a) => a.type === 'tarsus');
    const x = Math.max(4, Math.min(...tar.pts.map((p) => p[0]))), y = Math.max(4, Math.min(...tar.pts.map((p) => p[1])) - 110 * v.annScale());
    v.ann = v.ann.filter((a) => a.type !== 'mbsum');
    v.ann.push({ type: 'mbsum', x, y: Math.max(4, y), text: `${LID_KO[m.lid]} 마이봄샘 소실 ${m.loss.toFixed(1)}% (${m.method})\nArita ${m.arita}/3 · Pult ${m.pult}${m.count != null ? ` · 샘 ${m.count}개` : ''}` });
    return saveEdited();
  }

  function setTab(name) {
    document.querySelectorAll('.ptab').forEach((b) => { const on = b.dataset.tab === name; b.classList.toggle('on', on); b.setAttribute('aria-selected', String(on)); });
    document.querySelectorAll('.ppane').forEach((p) => p.classList.toggle('on', p.dataset.pane === name));
    if (name === 'adj') drawHist();
  }

  // ================= LIVE =================
  function attachLive() {
    const st = App.state.stream, vid = $('pro-video');
    if (st && vid.srcObject !== st) { vid.srcObject = st; const pr = vid.play && vid.play(); if (pr) pr.catch(() => {}); }
    $('pro-live-empty').style.display = st ? 'none' : 'flex';
  }
  function grabVideo() {
    const vid = $('pro-video');
    if (!vid.videoWidth) return null;
    const c = document.createElement('canvas');
    c.width = vid.videoWidth; c.height = vid.videoHeight;
    c.getContext('2d').drawImage(vid, 0, 0);
    return c;
  }
  /** CAPTURE·SAVE 에 쓸 라이브 프레임 — 스마트 탭 Best Shot 이 켜져 있으면 최근 1초 중 가장 선명한 프레임 */
  const liveFrame = () => (window.SlitSmart && window.SlitSmart.bestFrame()) || grabVideo();
  function toggleCapture(src = null) {
    const view = document.querySelector('.pro-view[data-view="live"]');
    if (!P.frozen) {
      const g = src || liveFrame();
      if (!g) { App.toast('라이브 영상이 없습니다.', 'error'); return; }
      const fz = $('pro-freeze');
      fz.width = g.width; fz.height = g.height; fz.getContext('2d').drawImage(g, 0, 0);
      P.frozen = true;
      App.sfx('freeze');
    } else P.frozen = false;
    view.classList.toggle('frozen', P.frozen);
    $('pro-capture').classList.toggle('on', P.frozen);
    $('pro-capture').innerHTML = P.frozen ? '<svg class="ic"><use href="#i-play"/></svg>RESUME' : '<svg class="ic"><use href="#i-freeze"/></svg>CAPTURE';
    updateSide();
  }
  async function saveLive() {
    const p = App.currentPatient();
    if (!p) { App.toast('환자를 먼저 선택하세요.', 'error'); return; }
    const src = P.frozen ? $('pro-freeze') : liveFrame();
    if (!src) { App.toast('저장할 영상이 없습니다.', 'error'); return; }
    App.sfx('shutter');
    const blob = await new Promise((r) => src.toBlob(r, 'image/png'));
    if (!blob) return;
    if (await App.persistCapture(p, 'photo', blob, 'png', $('pro-eye').value, null, null, { silent: true })) {
      App.toast('환자 DB에 저장했습니다.', 'success');
      if (P.frozen) toggleCapture();
      await reload(true);
      App.renderThumbs();
    }
  }

  // ================= 보정본 저장 / 코멘트 / 환자정보 =================
  async function saveEdited() {
    const p = App.currentPatient();
    if (!p) { App.toast('환자를 먼저 선택하세요.', 'error'); return 0; }
    if (!P.patient || P.patient.id !== p.id) { App.toast('환자 영상 목록을 불러오는 중입니다. 잠시 후 다시 저장하세요.', 'error'); return 0; }
    const pairs = P.mode === 'single' ? [[V.single, P.caps[P.idx]]]
      : P.mode === 'compare' ? [[V.left, P.pair[0]], [V.right, P.pair[1]]] : [];
    let n = 0;
    const done = [];
    for (const [v, src] of pairs) {
      if (!v.proc || !src || src.ref || !v.isModified()) continue;   // 참조 샘플은 환자 영상으로 저장하지 않음
      const kinds = new Set(v.ann.map((a) => a.type));
      const mb = kinds.has('tarsus') ? mbResult(v) : null;
      const note = mb ? mbNote(mb)
        : kinds.has('length') || kinds.has('angle') ? '측정'
        : kinds.size ? '주석' : v.geometryChanged() ? '편집본' : '보정본';
      const blob = await new Promise((r) => v.renderFull().toBlob(r, 'image/png'));
      if (blob && await App.persistCapture(p, 'photo', blob, 'png', src.eye || 'NA', null, note, { silent: true })) { n++; done.push(v); }
    }
    if (!n) { App.toast('보정·편집·주석이 없어 저장할 내용이 없습니다.', 'error'); return 0; }
    syncControls(IDENTITY);                            // 저장본에 이미 반영 → 이중 적용 방지
    for (const v of done) v.resetAll();
    await reload(P.mode === 'single');
    App.renderThumbs();
    App.toast(`편집본 ${n}장을 저장했습니다 (원본 보존).`, 'success');
    return n;
  }
  async function setComment(text) {
    const c = P.caps[P.idx];
    if (!c) return;
    try {
      const r = await App.api(`/captures/${c.id}`, { method: 'PATCH', body: JSON.stringify({ note: text }) });
      c.note = r.note;
      renderMulti();
      V.single.caption = caption(c, P.idx + 1); V.single.draw();
      updateSide(); App.renderThumbs();
      App.toast('코멘트를 저장했습니다.', 'success');
    } catch (e) { App.toast(e.message, 'error'); }
  }
  function editComment() {
    const c = P.caps[P.idx];
    if (P.mode !== 'single' || !c) return;
    const t = window.prompt('이 영상의 코멘트:', c.note || '');
    if (t !== null) setComment(t);
  }
  function editPatient() {
    const p = App.currentPatient();
    if (!p) { App.toast('환자를 먼저 선택하세요.', 'error'); return; }
    App.openPatientDialog(p);
  }

  // ================= 인쇄 / PDF 보고서 =================
  async function printItems() {
    if (P.mode === 'single' && V.single.proc) return [[V.single.renderFull(), caption(P.caps[P.idx])]];
    if (P.mode === 'compare') {
      return [[V.left, P.pair[0]], [V.right, P.pair[1]]].filter(([v, c]) => v.proc && c)
        .map(([v, c]) => [v.renderFull(), caption(c)]);
    }
    if (P.mode === 'live') {
      const cv = P.frozen ? $('pro-freeze') : grabVideo();
      return cv ? [[cv, 'LIVE  ' + App.fmtDT(new Date())]] : [];
    }
    if (P.mode === 'multi') {
      const idx = P.sel.length ? [...P.sel].sort((a, b) => a - b)
        : Array.from({ length: Math.max(0, Math.min(P.per, P.caps.length - P.page * P.per)) }, (_, k) => P.page * P.per + k);
      const out = [];
      for (const i of idx) {
        try { out.push([processed(await loadImage(P.caps[i].url), P.adj), caption(P.caps[i])]); } catch (_) { /* skip */ }
      }
      return out;
    }
    return [];
  }
  function toDataURL(cv, max = 1600) {
    const s = Math.min(1, max / Math.max(cv.width, cv.height));
    if (s === 1) return cv.toDataURL('image/jpeg', 0.92);
    const c = document.createElement('canvas');
    c.width = Math.round(cv.width * s); c.height = Math.round(cv.height * s);
    c.getContext('2d').drawImage(cv, 0, 0, c.width, c.height);
    return c.toDataURL('image/jpeg', 0.92);
  }
  async function buildReport() {
    const items = await printItems();
    if (!items.length) return 0;
    const p = P.patient, sex = { M: '남', F: '여', O: '기타' }[p?.sex] || '';
    const pages = [];
    if (items.length > 2) for (let i = 0; i < items.length; i += 4) pages.push(items.slice(i, i + 4));
    else pages.push(items);
    const now = App.fmtDT(new Date());
    const area = $('print-area');
    area.innerHTML = pages.map((pg, pi) => `
      <section class="rp-page">
        <div class="rp-head"><div class="rp-brand">${BRAND.logo ? `<img class="rp-logo" src="${esc(BRAND.logo)}" alt="">` : ''}<div class="rp-title">${esc(BRAND.report_title)}</div></div><div class="rp-date">출력 ${esc(now)}</div></div>
        <div class="rp-pt">
          <div><b>환자: ${esc(p ? p.name : '-')} (${esc(p?.chart_no || '-')})</b>${sex ? '&nbsp; · &nbsp;' + sex : ''}${p?.birth_date ? '&nbsp; · &nbsp;' + esc(p.birth_date) : ''}</div>
          <div class="rp-l2">진단명: ${esc(p?.diagnosis || '-')} &nbsp;&nbsp;&nbsp; 담당의: ${esc(p?.doctor || '-')}</div>
        </div>
        <div class="rp-imgs ${pg.length === 1 ? 'c1' : 'c2'}">
          ${pg.map(([cv, cap]) => `<figure class="rp-fig"><img src="${toDataURL(cv)}" alt=""><figcaption>${esc(cap)}</figcaption></figure>`).join('')}
        </div>
        <div class="rp-notes"><h4>소견</h4><div class="rp-box"></div></div>
        <div class="rp-foot"><span>확인자 서명: ________________</span><span>${pi + 1} / ${pages.length}</span></div>
      </section>`).join('');
    await Promise.all([...area.querySelectorAll('img')].map((im) => (im.decode ? im.decode().catch(() => {}) : null)));
    return pages.length;
  }
  async function doPrint() {
    const n = await buildReport();
    if (!n) { App.toast('출력할 영상이 없습니다.', 'error'); return; }
    App.toast("인쇄 창에서 '대상: PDF로 저장'을 고르면 PDF 보고서가 됩니다.", 'success');
    try { window.print(); } catch (_) { App.toast('이 환경에서는 인쇄 창을 열 수 없습니다.', 'error'); }
  }
  window.addEventListener('afterprint', () => { const a = $('print-area'); if (a) a.innerHTML = ''; });   // 큰 보고서 이미지 메모리 해제

  // ================= 토글 · 이벤트 =================
  async function setPro(on) {
    P.on = on;
    document.body.classList.toggle('pro-on', on);
    $('btn-pro').setAttribute('aria-pressed', String(on));
    if (on) { await reload(false); await setMode(P.mode); }
    else if (P.frozen) toggleCapture();
  }

  $('btn-pro').addEventListener('click', () => setPro(!P.on));
  $('btn-mode-capture').addEventListener('click', () => { if (P.on) setPro(false); });
  document.querySelectorAll('#pro-modes .pbtn').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
  document.querySelectorAll('.pro-side [data-tool]').forEach((b) => b.addEventListener('click', () => setTool(b.dataset.tool)));
  $('pro-capture').addEventListener('click', () => toggleCapture());
  $('pro-save').addEventListener('click', saveLive);
  $('pro-prev').addEventListener('click', () => step(-1));
  $('pro-next').addEventListener('click', () => step(1));
  $('pro-per').addEventListener('change', (e) => setPer(e.target.value));
  $('pro-comment').addEventListener('click', editComment);
  $('pro-patient').addEventListener('click', editPatient);
  $('pro-delete').addEventListener('click', () => deleteSelected());
  $('pro-print').addEventListener('click', doPrint);
  $('mb-enhance').addEventListener('click', mbEnhance);
  $('mb-auto').addEventListener('click', mbRunAuto);
  $('mb-clear').addEventListener('click', mbClear);
  $('mb-save').addEventListener('click', mbSave);
  $('mb-lid').addEventListener('change', mbRender);
  $('pro-fullscreen').addEventListener('click', () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else $('pro-display').requestFullscreen().catch((e) => App.toast('전체 화면을 열 수 없습니다: ' + e.message, 'error'));
  });
  $('pro-clear').addEventListener('click', clearAnn);
  $('pro-save-edited').addEventListener('click', saveEdited);
  $('pro-fit').addEventListener('click', () => { for (const v of Object.values(V)) v.resetView(); });
  $('pro-zin').addEventListener('click', () => (P.mode === 'compare' ? V.left : V.single).zoomAt(1.25));
  $('pro-zout').addEventListener('click', () => (P.mode === 'compare' ? V.left : V.single).zoomAt(0.8));
  $('adj-reset').addEventListener('click', () => setAdj(IDENTITY));
  $('adj-auto').addEventListener('click', autoContrast);
  for (const [id] of SLIDERS) $(id).addEventListener('input', onAdj);
  for (const id of ['adj-channel', 'adj-neg', 'adj-denoise', 'adj-sharpen', 'adj-enhance']) $(id).addEventListener('change', onAdj);
  $('ed-rot-l').addEventListener('click', () => rotate(-1));
  $('ed-rot-r').addEventListener('click', () => rotate(1));
  $('ed-flip-h').addEventListener('click', () => flip(true));
  $('ed-flip-v').addEventListener('click', () => flip(false));
  $('ed-undo').addEventListener('click', undo);
  $('ed-redo').addEventListener('click', redo);
  $('ed-orig').addEventListener('click', restoreOriginal);
  $('pro-calib-clear').addEventListener('click', () => setPxPerMm(0));
  document.querySelectorAll('.ptab').forEach((b) => b.addEventListener('click', () => setTab(b.dataset.tab)));
  // 스마트 캡처는 실시간 영상에 쓰므로 LIVE 옆 버튼을 누르면 LIVE 화면으로 함께 전환
  document.querySelectorAll('#pro-modes .ptab[data-tab="smart"]').forEach((b) => b.addEventListener('click', () => { if (P.mode !== 'live') setMode('live'); }));
  $('pro-start-camera').addEventListener('click', async () => { await App.startCamera(); attachLive(); });

  document.addEventListener('slit:patient', () => { if (P.on) reload(false); });
  document.addEventListener('slit:captures', () => { if (P.on) reload(false); });   // 기본 화면에서 영상 삭제 등
  document.addEventListener('keydown', (e) => {
    if (!P.on || document.querySelector('dialog[open]')) return;
    const el = e.target instanceof Element ? e.target : null;
    if (el && el.matches('input:not([type=range]):not([type=checkbox]), textarea')) return;   // 글자 입력 중
    const ctrl = e.ctrlKey || e.metaKey;
    if (!ctrl && el && el.matches('input, select')) return;     // 슬라이더·선택 상자의 화살표 키는 그대로 (Ctrl+Z/Y 는 허용)
    if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
    else if (e.key === ' ' && P.mode === 'live') { e.preventDefault(); toggleCapture(); }
    else if (e.key === 'Enter' && V.single.poly) { e.preventDefault(); V.single.finishPoly(); }
    else if (e.key === 'Escape') setTool('pan');
    else if (e.key === 'Delete' && !ctrl && deleteTargets().length) { e.preventDefault(); deleteSelected(); }
    else if ((e.ctrlKey || e.metaKey) && e.code === 'KeyZ') { e.preventDefault(); (e.shiftKey ? redo : undo)(); }   // 한글 입력 상태에서도
    else if ((e.ctrlKey || e.metaKey) && e.code === 'KeyY') { e.preventDefault(); redo(); }
  });

  setTool('pan');
  readAdj();
  setPxPerMm(P.pxmm);
  setPer(16);
  document.querySelectorAll('#pro-display .pro-view').forEach((v) => v.classList.toggle('on', v.dataset.view === P.mode));
  document.querySelectorAll('#pro-modes .pbtn').forEach((b) => b.classList.toggle('on', b.dataset.mode === P.mode));

  // 자동화 테스트·디버깅용 핸들
  window.SlitPro = { state: P, viewers: V, setPro, setMode, setPer, openIndex, step, toggleSel, setAdj,
    setTool, saveEdited, setComment, deleteSelected, deleteTargets, buildReport, reload, active, autoContrast, rotate, flip, undo, redo,
    original: restoreOriginal, setPxPerMm, clearAnn, setTab, mbRunAuto, mbResult: () => mbResult(V.single), mbSave, mbClear,
    toggleCapture, saveLive, grabVideo, loadImage, caption, prepareCompare, compareImages };
})();

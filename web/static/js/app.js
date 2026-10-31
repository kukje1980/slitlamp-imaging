/* 슬릿램프 영상 시스템 웹앱 프런트엔드.
 * - navigator.mediaDevices.getUserMedia 로 캡처 장비 라이브
 * - canvas.toBlob (사진) / MediaRecorder (동영상)
 * - 저장: (a) 서버 POST /api/v1/captures  (b) File System Access API 로 지정 폴더에도 동시 write
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const API = '/api/v1';

  const state = {
    patients: [],
    currentPatientId: null,
    stream: null,
    isPaused: false,
    isRecording: false,
    recorder: null,
    saving: 0,           // 저장(업로드·폴더 쓰기) 중인 촬영본 수 — 0 이 아니면 창 닫기/새로고침 전에 경고
    recordStart: 0,
    recordTimer: null,
    editingPatientId: null,
    viewing: null,       // 보기 창에 열어 둔 영상 (삭제 대상)
    statusResetTimer: null,
    dirHandle: null,     // File System Access API 디렉토리 핸들
    // 4분할
    quad: false,
    quadActive: 0,
    quadFrozen: [false, false, false, false],
    quadHasImg: [false, false, false, false],
    quadEyes: ['NA', 'NA', 'NA', 'NA'],
    quadRAF: null,
  };

  const fmt = (d) => d.toLocaleTimeString('ko-KR', { hour12: false });
  const fmtDT = (d) => d.toLocaleString('ko-KR', { hour12: false });
  const parseTs = (s) => {
    // 'YYYY-MM-DD HH:MM:SS' (localtime) → Date
    const [date, time] = (s || '').split(' ');
    if (!date) return new Date();
    const [y, m, d] = date.split('-').map(Number);
    const [hh, mm, ss] = (time || '0:0:0').split(':').map(Number);
    return new Date(y, (m || 1) - 1, d || 1, hh || 0, mm || 0, ss || 0);
  };

  // ---- API helper ----
  const LOCAL_MODE = !!(window.SlitLocal && window.SlitLocal.mode() === 'local');
  async function api(path, options = {}) {
    // '이 PC에만 저장' 모드: 서버로 보내지 않고 브라우저 저장소(IndexedDB)에서 처리
    if (LOCAL_MODE) return window.SlitLocal.api(path, options);
    const res = await fetch(`${API}${path}`, {
      headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
      ...options,
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    return res.json();
  }

  // ---- init ----
  async function init() {
    try {
      const saved = localStorage.getItem('slitlamp-theme');
      if (saved) document.documentElement.setAttribute('data-theme', saved);
    } catch (_) {}
    syncThemeIcon();
    syncStorageUI();
    applyKeyHints();
    try { if (localStorage.getItem('slitlamp-hint-closed') === '1' && !LOCAL_MODE) $('hint').style.display = 'none'; } catch (_) {}
    bindEvents();
    syncEye();
    if (!('showDirectoryPicker' in window)) {
      $('btn-folder').disabled = true;
      $('btn-folder').title = '이 브라우저는 폴더 저장을 지원하지 않습니다 (Chrome/Edge 사용)';
    }
    await loadPatients();
    updateCaptureButtons();
    setStatus('대기 중', 'idle');
  }

  function bindEvents() {
    $('patient-search').addEventListener('input', debounce(loadPatients, 250));
    $('patient-select').addEventListener('change', (e) => {
      const id = parseInt(e.target.value, 10) || null;
      if (id) { selectPatient(id); return; }   // 표에서 고를 때와 같이 — 녹화 중이면 끝내 앞 환자에게 저장
      state.currentPatientId = null;
      renderThumbs();
      updateCaptureButtons();
      emitPatient();
    });
    // 녹화 중이거나 아직 저장 중인 촬영본이 있으면 창 닫기·새로고침 전에 묻는다 (메모리에 있는 영상이 사라짐)
    window.addEventListener('beforeunload', (e) => {
      if (state.isRecording || state.saving > 0) { e.preventDefault(); e.returnValue = ''; }
    });
    $('btn-new-patient').addEventListener('click', () => openPatientDialog(null));
    for (const id of ['btn-edit-patient', 'btn-edit-top']) {
      $(id).addEventListener('click', () => {
        const p = currentPatient();
        if (p) openPatientDialog(p);
      });
    }
    // 생년월일: 입력이 끝나면 YYYY-MM-DD 로 정리, 달력 버튼은 브라우저 날짜 선택창을 연다
    $('p-birth').addEventListener('blur', () => normalizeBirthField());
    $('p-birth').addEventListener('input', () => { $('p-birth').classList.remove('invalid'); $('p-birth-hint').hidden = true; });
    $('p-birth-cal').addEventListener('click', () => {
      const pick = $('p-birth-pick');
      pick.max = new Date().toISOString().slice(0, 10);
      pick.value = normalizeDate($('p-birth').value) || '';
      if (typeof pick.showPicker === 'function') { try { pick.showPicker(); return; } catch (_) { /* 아래로 */ } }
      pick.style.cssText = 'position:static;width:auto;height:auto;opacity:1;pointer-events:auto';
      pick.focus(); pick.click();
    });
    $('p-birth-pick').addEventListener('change', (e) => {
      if (e.target.value) { $('p-birth').value = e.target.value; $('p-birth').classList.remove('invalid'); $('p-birth-hint').hidden = true; }
      e.target.style.cssText = '';
    });
    $('patient-form').addEventListener('submit', savePatient);
    $('patient-cancel').addEventListener('click', () => $('patient-dialog').close());
    $('patient-x').addEventListener('click', () => $('patient-dialog').close());
    $('patient-delete').addEventListener('click', deletePatient);

    $('btn-theme').addEventListener('click', toggleTheme);
    $('btn-sounds').addEventListener('click', () => setSounds(!soundsOn()));
    syncSoundsButton();
    const vol = $('sound-volume'), volOut = $('sound-volume-out');
    vol.value = String(window.SlitSounds ? window.SlitSounds.volume() : 100);
    volOut.textContent = vol.value + '%';
    vol.addEventListener('input', () => {                      // 끌면서 숫자만 갱신
      volOut.textContent = vol.value + '%';
      if (window.SlitSounds) window.SlitSounds.setVolume(vol.value);
    });
    vol.addEventListener('change', () => {                     // 놓으면 그 크기로 들려 준다
      try { if (window.SlitSounds) window.SlitSounds.play('shutter', Number(vol.value)); } catch (_) { /* 무시 */ }
    });
    $('btn-storage').addEventListener('click', openStorageDialog);
    $('storage-x').addEventListener('click', () => $('storage-dialog').close());
    $('storage-cancel').addEventListener('click', () => $('storage-dialog').close());
    $('storage-apply').addEventListener('click', applyStorage);
    document.querySelectorAll('#eye-seg [data-eye]').forEach((b) => b.addEventListener('click', () => setEye(b.dataset.eye)));
    $('eye-select').addEventListener('change', syncEye);
    document.querySelectorAll('#thumb-filter .tf').forEach((b) => b.addEventListener('click', () => {
      state.thumbFilter = b.dataset.f;
      document.querySelectorAll('#thumb-filter .tf').forEach((x) => x.classList.toggle('on', x === b));
      renderThumbs();
    }));
    $('hint-close').addEventListener('click', () => {
      $('hint').style.display = 'none';
      try { localStorage.setItem('slitlamp-hint-closed', '1'); } catch (_) {}
    });
    // 환자 목록 · 전체 화면
    $('pb-chip').addEventListener('click', openPatientList);
    $('btn-patients').addEventListener('click', openPatientList);
    $('pd-close').addEventListener('click', () => $('patient-drawer').close());
    $('pd-new').addEventListener('click', () => openPatientDialog(null));
    $('btn-fullscreen').addEventListener('click', toggleFullscreen);
    $('btn-fs-exit').addEventListener('click', toggleFullscreen);
    $('btn-fs-capture').addEventListener('click', () => { if (!$('btn-capture').disabled) capturePhoto(); });
    $('btn-fs-freeze').addEventListener('click', () => $('btn-freeze').click());
    $('btn-fs-record').addEventListener('click', () => $('btn-record').click());
    document.addEventListener('fullscreenchange', syncFullscreen);
    $('btn-folder').addEventListener('click', pickFolder);

    $('btn-start-camera').addEventListener('click', () => startCamera());
    $('camera-select').addEventListener('change', (e) => { prefSet('slitlamp-cam', e.target.value); startCamera(e.target.value); });
    $('vq-select').value = prefGet('slitlamp-vq') === 'high' ? 'high' : 'normal';
    $('vq-select').addEventListener('change', (e) => prefSet('slitlamp-vq', e.target.value));
    $('view-after-capture').checked = prefGet('slitlamp-view-after') !== '0';      // 기본: 켬
    $('view-after-capture').addEventListener('change', (e) => prefSet('slitlamp-view-after', e.target.checked ? '1' : '0'));
    $('res-select').value = prefGet('slitlamp-res') || '1080';
    $('res-select').addEventListener('change', (e) => {
      prefSet('slitlamp-res', e.target.value);
      if (state.stream) startCamera($('camera-select').value || undefined);
    });
    document.querySelectorAll('.btn-sony-help').forEach((b) => b.addEventListener('click', () => $('sony-dialog').showModal()));
    $('sony-x').addEventListener('click', () => $('sony-dialog').close());
    $('sony-ok').addEventListener('click', () => $('sony-dialog').close());
    // 컴퓨터 사양 · 동영상 화질 설명 — 브라우저가 알려 주는 범위(코어 수 · 대략의 메모리)로 이 PC 를 표시
    $('btn-specs').addEventListener('click', () => {
      const cores = navigator.hardwareConcurrency, mem = navigator.deviceMemory;   // deviceMemory 는 Chrome 만, 8GB 가 상한
      const bits = [];
      if (cores) bits.push(`CPU ${cores}코어`);
      if (mem) bits.push(mem >= 8 ? '메모리 8GB 이상' : `메모리 약 ${mem}GB`);
      let verdict = '';
      if (cores && mem) verdict = (cores >= 4 && mem >= 8) ? ' — 최소 사양 충족 (1080p 촬영·녹화 OK)' : ' — 최소 사양에 미치지 못할 수 있습니다';
      $('specs-this-pc').textContent = bits.length ? `이 PC: ${bits.join(' · ')}${verdict}` : '이 PC 의 사양은 브라우저가 알려 주지 않습니다. 아래 표와 직접 비교해 보세요.';
      $('specs-dialog').showModal();
    });
    $('specs-x').addEventListener('click', () => $('specs-dialog').close());
    $('btn-pause').addEventListener('click', togglePause);
    $('btn-capture').addEventListener('click', capturePhoto);
    $('btn-record').addEventListener('click', toggleRecord);
    $('btn-quad').addEventListener('click', toggleQuad);
    $('btn-meibo').addEventListener('click', () => setMeibo(!meibo.on));
    $('btn-mb-enh').addEventListener('click', () => { meibo.enh = !meibo.enh; saveMeibo(); syncMeibo(); });
    $('btn-mb-inv').addEventListener('click', () => { meibo.inv = !meibo.inv; saveMeibo(); syncMeibo(); });
    document.querySelectorAll('#mb-lid-seg [data-lid]').forEach((x) => x.addEventListener('click', () => setLid(x.dataset.lid)));
    syncMeibo();
    $('btn-freeze').addEventListener('click', freezeFrame);
    $('btn-save-quad').addEventListener('click', saveQuad);
    $('btn-clear-quad').addEventListener('click', clearQuad);
    document.querySelectorAll('#quad-grid .quad-cell').forEach((cell) => {
      cell.addEventListener('click', () => { if (state.quad) setQuadActive(+cell.dataset.i); });
    });

    $('viewer-close').addEventListener('click', () => $('viewer-dialog').close());
    $('viewer-dialog').addEventListener('close', () => { $('viewer-body').innerHTML = ''; state.viewing = null; });   // 닫으면 동영상 재생·내려받기 중지
    $('viewer-close-2').addEventListener('click', () => $('viewer-dialog').close());
    $('viewer-retake').addEventListener('click', () => $('viewer-dialog').close());
    $('viewer-dialog').addEventListener('keydown', (e) => {
      if (!$('viewer-dialog').classList.contains('after-capture')) return;
      if (e.key === 'Enter') { e.preventDefault(); $('viewer-dialog').close(); }
    });
    $('viewer-dialog').addEventListener('close', () => $('viewer-dialog').classList.remove('after-capture'));
    $('viewer-delete').addEventListener('click', deleteViewed);
    $('viewer-dialog').addEventListener('keydown', (e) => { if (e.key === 'Delete') { e.preventDefault(); deleteViewed(); } });

    // 단축키 (사용자 지정 — 단축키 설정 창)
    document.addEventListener('keydown', onShortcutKey);
    $('btn-keys').addEventListener('click', openKeysDialog);
    $('btn-settings').addEventListener('click', () => $('settings-dialog').showModal());
    $('settings-x').addEventListener('click', () => $('settings-dialog').close());
    $('cam-chip').addEventListener('click', () => $('settings-dialog').showModal());
    // 바깥(배경) 클릭으로 설정 창 닫기
    $('settings-dialog').addEventListener('click', (e) => { if (e.target === $('settings-dialog')) $('settings-dialog').close(); });
    $('keys-x').addEventListener('click', () => $('keys-dialog').close());
    $('keys-ok').addEventListener('click', () => $('keys-dialog').close());
    $('keys-reset').addEventListener('click', () => { keymap = defaultKeymap(); saveKeymap(); renderKeysTable(); applyKeyHints(); });
  }

  // ---- 단축키 (사용자 지정) ----
  // 물리 키 기준(e.code)으로 읽어서 한글 입력 상태에서도 동작한다. USB 발판 스위치처럼 키를 보내는 장치도 지정 가능.
  const KEY_ACTIONS = [
    { id: 'capture', label: '사진 캡처', def: 'C', scope: 'capture', btns: ['btn-capture', 'btn-fs-capture'],
      run: () => { if (!$('btn-capture').disabled) capturePhoto(); } },
    { id: 'record', label: '녹화 시작 / 중지', def: 'R', scope: 'capture', btns: ['btn-record', 'btn-fs-record'],
      run: () => { if (!$('btn-record').disabled) toggleRecord(); } },
    { id: 'pause', label: '일시정지 / 재개', def: 'Space', scope: 'capture', btns: ['btn-pause'], run: () => togglePause() },
    { id: 'freeze', label: '정지영상 (4분할 칸 채우기)', def: 'X', scope: 'capture', btns: ['btn-freeze', 'btn-fs-freeze'],
      run: () => { if (!$('btn-freeze').disabled) freezeFrame(); } },
    { id: 'quad', label: '4분할 켜기 / 끄기', def: 'Q', scope: 'capture', btns: ['btn-quad'], run: () => toggleQuad() },
    { id: 'meibo', label: '마이봄샘 촬영 모드 켜기 / 끄기', def: 'M', scope: 'capture', btns: ['btn-meibo'], run: () => setMeibo(!meibo.on) },
    { id: 'lid', label: '마이봄 상안검 ↔ 하안검', def: 'U', scope: 'capture', run: () => { if (meibo.on) setLid(meibo.lid === 'upper' ? 'lower' : 'upper'); } },
    { id: 'eyeOD', label: '안구 OD (우안)', def: '1', scope: 'all', run: () => setEyeAny('OD') },
    { id: 'eyeOS', label: '안구 OS (좌안)', def: '2', scope: 'all', run: () => setEyeAny('OS') },
    { id: 'eyeOU', label: '안구 OU (양안)', def: '3', scope: 'all', run: () => setEyeAny('OU') },
    { id: 'eyeNA', label: '안구 미지정', def: '0', scope: 'all', run: () => setEyeAny('NA') },
    { id: 'fullscreen', label: '전체 화면', def: 'F', scope: 'all', btns: ['btn-fullscreen', 'pro-fullscreen'], run: () => toggleFullscreen() },
    { id: 'mode', label: '촬영 ↔ Pro 검토 전환', def: 'P', scope: 'all', btns: ['btn-pro'], run: () => $('btn-pro').click() },
    { id: 'patients', label: '환자 목록', def: 'L', scope: 'all', btns: ['btn-patients'], run: () => openPatientList() },
    { id: 'newPatient', label: '신규 환자', def: 'N', scope: 'all', btns: ['btn-new-patient'], run: () => openPatientDialog(null) },
  ];
  const RESERVED = new Set(['Escape', 'Tab', 'Ctrl+Z', 'Ctrl+Y', 'Ctrl+Shift+Z', 'Meta+Z', 'Meta+Y', 'ArrowLeft', 'ArrowRight']);
  const defaultKeymap = () => Object.fromEntries(KEY_ACTIONS.map((a) => [a.id, a.def]));
  let keymap = defaultKeymap();
  try { keymap = { ...keymap, ...JSON.parse(localStorage.getItem('slitlamp-keys') || '{}') }; } catch (_) { /* 기본값 */ }
  function saveKeymap() { try { localStorage.setItem('slitlamp-keys', JSON.stringify(keymap)); } catch (_) { /* 저장 불가 */ } }

  /** 키 이벤트 → 'Ctrl+Shift+C', 'Space', 'F5', 'Num1' 같은 이름 (수정키만 누르면 null) */
  function keyName(e) {
    if (['Control', 'Shift', 'Alt', 'Meta', 'Process'].includes(e.key) || e.isComposing && !e.code) return null;
    let k;
    if (/^Key[A-Z]$/.test(e.code)) k = e.code.slice(3);
    else if (/^Digit\d$/.test(e.code)) k = e.code.slice(5);
    else if (/^Numpad\d$/.test(e.code)) k = 'Num' + e.code.slice(6);
    else if (e.code === 'Space') k = 'Space';
    else if (e.key && e.key.length === 1) k = e.key.toUpperCase();
    else k = e.key || e.code;
    const mods = [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift', e.metaKey && 'Meta'].filter(Boolean);
    return [...mods, k].join('+');
  }
  function setEyeAny(v) {
    if (document.body.classList.contains('pro-on')) {
      const s = $('pro-eye'); if (s) s.value = v;
      toast(`안구: ${v === 'NA' ? '미지정' : v}`, 'success');
    } else setEye(v);
  }
  function onShortcutKey(e) {
    if (capturingKey) return;
    if (e.repeat) return;                              // 키·발판을 누르고 있어도 한 번만 실행
    const vd = $('viewer-dialog');
    if (vd.open && vd.classList.contains('after-capture') && keyName(e) === keymap.capture) {
      // 촬영 직후 보기 창에서 촬영 키 → 닫고 바로 다시 찍기
      e.preventDefault();
      vd.close();
      if (!$('btn-capture').disabled) capturePhoto();
      return;
    }
    if ((e.target instanceof Element && e.target.matches('input, textarea, select')) || document.querySelector('dialog[open]')) return;
    const name = keyName(e);
    if (!name) return;
    const pro = document.body.classList.contains('pro-on');
    const act = KEY_ACTIONS.find((a) => keymap[a.id] === name && (a.scope === 'all' || (a.scope === 'capture' && !pro)));
    if (!act) return;
    e.preventDefault();
    act.run();
  }
  /** 버튼 툴팁에 현재 단축키 표시 */
  function applyKeyHints() {
    for (const a of KEY_ACTIONS) {
      for (const id of a.btns || []) {
        const b = $(id); if (!b) continue;
        const base = (b.dataset.tip ??= (b.title || '').replace(/\s*\([^)]*\)\s*$/, ''));
        b.title = keymap[a.id] ? `${base} (${keymap[a.id]})` : base;
      }
    }
    document.querySelectorAll('#eye-seg [data-eye]').forEach((b) => {
      const k = keymap['eye' + b.dataset.eye];
      b.title = (b.dataset.tip ??= b.title) + (k ? ` (${k})` : '');
    });
  }

  let capturingKey = null;
  function openKeysDialog() { renderKeysTable(); $('keys-dialog').showModal(); }
  function renderKeysTable() {
    const tb = $('keys-rows');
    tb.innerHTML = '';
    for (const a of KEY_ACTIONS) {
      const tr = document.createElement('tr');
      const k = keymap[a.id];
      tr.innerHTML = `<td>${escHtml(a.label)}<small>${a.scope === 'capture' ? '촬영 화면' : '모든 화면'}</small></td>`
        + `<td><kbd class="${k ? '' : 'none'}">${escHtml(k || '없음')}</kbd></td>`
        + '<td class="keys-act"><button class="btn btn-sm" data-act="set">변경</button><button class="btn btn-sm btn-ghost" data-act="clear" title="단축키 없애기">지우기</button></td>';
      tr.querySelector('[data-act="set"]').addEventListener('click', () => startCapture(a, tr));
      tr.querySelector('[data-act="clear"]').addEventListener('click', () => { keymap[a.id] = ''; saveKeymap(); renderKeysTable(); applyKeyHints(); });
      tb.appendChild(tr);
    }
  }
  function startCapture(action, tr) {
    const kbd = tr.querySelector('kbd');
    kbd.textContent = '키를 누르세요… (Esc 취소)'; kbd.className = 'waiting';
    const handler = (e) => {
      e.preventDefault(); e.stopPropagation();
      if (e.key === 'Escape') { finish(); return; }
      const name = keyName(e);
      if (!name) return;                              // 수정키만 눌렀으면 계속 기다림
      if (RESERVED.has(name)) { kbd.textContent = `${name} 는 쓸 수 없습니다 — 다른 키`; return; }
      const other = KEY_ACTIONS.find((a) => a.id !== action.id && keymap[a.id] === name);
      if (other) { keymap[other.id] = ''; toast(`'${other.label}'에 쓰던 ${name} 를 옮겼습니다`, 'success'); }
      keymap[action.id] = name;
      saveKeymap(); applyKeyHints();
      finish();
    };
    function finish() {
      document.removeEventListener('keydown', handler, true);
      capturingKey = null;
      renderKeysTable();
    }
    if (capturingKey) capturingKey();
    capturingKey = finish;
    document.addEventListener('keydown', handler, true);
  }

  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  // ---- patients ----
  function currentPatient() {
    return state.patients.find(p => p.id === state.currentPatientId)
      || (state.selectedPatient && state.selectedPatient.id === state.currentPatientId ? state.selectedPatient : undefined);
  }

  async function loadPatients() {
    const q = ($('patient-search').value || '').trim();
    const prev = currentPatient();
    if (prev) state.selectedPatient = prev;           // 검색 결과에 없어도 현재 환자 정보는 유지
    try {
      state.patients = await api(`/patients?q=${encodeURIComponent(q)}`);
    } catch (e) {
      toast('환자 목록을 불러오지 못했습니다: ' + e.message, 'error');
      state.patients = [];
    }
    const sel = $('patient-select');
    sel.innerHTML = '';
    if (state.patients.length === 0) {
      const opt = document.createElement('option');
      opt.textContent = q ? '(검색 결과 없음)' : '(등록된 환자 없음)';
      opt.disabled = true;
      sel.appendChild(opt);
      if (!q) state.currentPatientId = null;          // 검색 중에는 선택한 환자를 바꾸지 않는다
    } else {
      for (const p of state.patients) {
        const opt = document.createElement('option');
        opt.value = String(p.id);
        opt.textContent = `${p.name} (${p.chart_no || '-'})`;
        sel.appendChild(opt);
      }
      let inList = state.patients.find(p => p.id === state.currentPatientId);
      if (!inList && state.currentPatientId && !q) {
        // 목록(최대 500명)에 없는 방금 저장한 환자도 선택이 유지되도록 따로 불러와 맨 앞에 둔다
        try {
          inList = await api(`/patients/${state.currentPatientId}`);
          state.patients.unshift(inList);
          const opt = document.createElement('option');
          opt.value = String(inList.id);
          opt.textContent = `${inList.name} (${inList.chart_no || '-'})`;
          sel.insertBefore(opt, sel.firstChild);
        } catch (_) { inList = null; }
      }
      // 검색 결과에 현재 환자가 없어도 선택은 유지 (검색만 하고 닫았는데 다른 환자로 바뀌어 촬영되는 일 방지)
      if (!inList && !(q && state.currentPatientId)) state.currentPatientId = state.patients[0].id;
      sel.value = String(state.currentPatientId);
    }
    updateCaptureButtons();
    renderPatientTable();
    await renderThumbs();
    emitPatient();
  }

  // ---- 환자 목록 (표) ----
  const SEX_KO = { M: '남', F: '여', O: '기타' };
  function openPatientList() {
    renderPatientTable();
    const d = $('patient-drawer');
    if (!d.open) d.showModal();
    setTimeout(() => $('patient-search').focus(), 30);
  }
  function renderPatientTable() {
    const tb = $('pd-rows');
    tb.innerHTML = '';
    $('pd-count').textContent = state.patients.length;
    $('pd-empty').hidden = state.patients.length > 0;
    $('pd-empty').textContent = ($('patient-search').value || '').trim() ? '검색 결과가 없습니다' : '등록된 환자가 없습니다';
    for (const p of state.patients) {
      const tr = document.createElement('tr');
      tr.tabIndex = 0;
      tr.classList.toggle('on', p.id === state.currentPatientId);
      const age = ageOf(p.birth_date);
      const sexAge = [SEX_KO[p.sex] || '', age != null ? age + '세' : ''].filter(Boolean).join(' · ');
      tr.innerHTML = `<td class="pd-name">${escHtml(p.name)}</td><td class="pd-chart">${escHtml(p.chart_no || '-')}</td>`
        + `<td>${escHtml(sexAge || '-')}</td><td>${escHtml(p.birth_date || '-')}</td>`
        + `<td class="pd-dx" title="${escHtml(p.diagnosis || '')}">${escHtml(p.diagnosis || '-')}</td><td>${escHtml(p.doctor || '-')}</td>`
        + '<td><button class="btn btn-sm btn-ghost pd-edit" title="정보 수정" aria-label="정보 수정"><svg class="ic"><use href="#i-edit"/></svg></button></td>';
      tr.addEventListener('click', (e) => {
        if (e.target.closest('.pd-edit')) { openPatientDialog(p); return; }
        selectPatient(p.id);
        $('patient-drawer').close();
      });
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') { selectPatient(p.id); $('patient-drawer').close(); } });
      tb.appendChild(tr);
    }
  }
  function selectPatient(id) {
    if (state.isRecording && state.recordPatientId !== id) {   // 다른 환자로 바꾸면 녹화를 끝내 앞 환자에게 저장
      stopRecording();
      toast('환자를 바꿔 녹화를 끝내고 이전 환자에게 저장했습니다.', 'success');
    }
    state.currentPatientId = id;
    state.selectedPatient = state.patients.find((p) => p.id === id) || null;
    $('patient-select').value = String(id);
    renderPatientTable();
    renderThumbs();
    updateCaptureButtons();
    emitPatient();
  }

  // ---- 전체 화면 ----
  function toggleFullscreen() {
    const el = document.body.classList.contains('pro-on') ? $('pro-display') : $('video-frame');
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else if (el.requestFullscreen) el.requestFullscreen().catch((e) => toast('전체 화면을 열 수 없습니다: ' + e.message, 'error'));
  }
  function syncFullscreen() {
    const on = !!document.fullscreenElement;
    for (const id of ['btn-fullscreen', 'pro-fullscreen']) {
      const b = $(id);
      if (b) b.innerHTML = on ? '<svg class="ic"><use href="#i-shrink"/></svg>전체 화면 끝' : '<svg class="ic"><use href="#i-expand"/></svg>전체 화면';
    }
  }

  function emitPatient() {
    renderBanner();
    document.dispatchEvent(new CustomEvent('slit:patient', { detail: currentPatient() || null }));
  }

  // ---- 환자 배너 · 영상 HUD ----
  const escHtml = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // 사람이 친 생년월일 → 'YYYY-MM-DD'. 빈 값은 '', 해석 불가는 null.
  //   19800315 · 800315(주민번호 앞자리, 올해 두 자리보다 크면 19xx) · 1980.3.15 · 1980/03/15
  function normalizeDate(text, today) {
    const t = (text || '').trim();
    if (!t) return '';
    today = today || new Date();
    const century = (yy) => (yy > today.getFullYear() % 100 ? 1900 : 2000) + yy;
    const digits = t.replace(/\D/g, '');
    const parts = t.split(/[^\d]+/).filter(Boolean);
    let y, m, d;
    if (parts.length === 3 && (parts[0].length === 2 || parts[0].length === 4)) {
      [y, m, d] = parts.map(Number);
      if (parts[0].length === 2) y = century(y);
    } else if (digits.length === 8) {
      y = +digits.slice(0, 4); m = +digits.slice(4, 6); d = +digits.slice(6, 8);
    } else if (digits.length === 6) {
      y = century(+digits.slice(0, 2)); m = +digits.slice(2, 4); d = +digits.slice(4, 6);
    } else return null;
    const v = new Date(y, m - 1, d);
    if (v.getFullYear() !== y || v.getMonth() !== m - 1 || v.getDate() !== d) return null;
    if (y < 1880 || v > today) return null;
    return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }
  window.normalizeDate = normalizeDate;

  function normalizeBirthField() {
    const el = $('p-birth');
    const v = normalizeDate(el.value);
    const ok = v !== null;
    if (ok && v !== el.value) el.value = v;
    el.classList.toggle('invalid', !ok);
    $('p-birth-hint').hidden = ok;
    return ok;
  }

  function ageOf(birth) {
    const m = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(birth || '');
    if (!m) return null;
    const now = new Date(), b = new Date(+m[1], +m[2] - 1, +m[3]);
    let a = now.getFullYear() - b.getFullYear();
    if (now < new Date(now.getFullYear(), b.getMonth(), b.getDate())) a--;
    return a >= 0 && a < 150 ? a : null;
  }
  function renderBanner() {
    const p = currentPatient();
    $('btn-edit-patient').disabled = !p;
    $('btn-edit-top').disabled = !p;
    if (!p) {
      $('pb-avatar').innerHTML = '<svg class="ic"><use href="#i-user"/></svg>';
      $('pb-name').textContent = '환자를 선택하세요';
      $('pb-chart').textContent = '';
      $('pb-meta').innerHTML = '<span>검색하거나 신규 환자를 등록하세요</span>';
      $('hud-patient').textContent = '환자 미선택';
      return;
    }
    $('pb-avatar').textContent = (p.name || '?').trim().charAt(0);
    $('pb-name').textContent = p.name;
    $('pb-chart').textContent = p.chart_no ? '#' + p.chart_no : '';
    const sex = { M: '남', F: '여', O: '기타' }[p.sex] || '';
    const age = ageOf(p.birth_date);
    const bits = [];
    if (sex || age != null) bits.push(`<span>${[sex, age != null ? age + '세' : ''].filter(Boolean).join(' · ')}</span>`);
    if (p.birth_date) bits.push(`<span><b>생년월일</b>${escHtml(p.birth_date)}</span>`);
    bits.push(`<span><b>진단명</b><span class="dx">${escHtml(p.diagnosis || '-')}</span></span>`);
    bits.push(`<span><b>담당의</b>${escHtml(p.doctor || '-')}</span>`);
    $('pb-meta').innerHTML = bits.join('');
    $('hud-patient').textContent = `${p.name}${p.chart_no ? '  ·  ' + p.chart_no : ''}`;
  }
  function setEye(v) {
    const sel = $('eye-select');
    if (sel.value !== v) { sel.value = v; }
    syncEye();
  }
  function syncEye() {
    const v = $('eye-select').value || 'NA';
    document.querySelectorAll('#eye-seg [data-eye]').forEach((b) => {
      const on = b.dataset.eye === v;
      b.classList.toggle('on', on); b.setAttribute('aria-checked', String(on));
    });
    const fs = $('fs-eye');
    if (fs) fs.textContent = v === 'NA' ? '안구 —' : v;
  }

  function openPatientDialog(patient) {
    state.editingPatientId = patient?.id ?? null;
    $('patient-dialog-title').textContent = patient ? '환자 정보 수정' : '신규 환자';
    $('patient-delete').style.display = patient ? 'inline-flex' : 'none';
    $('p-chart').value = patient?.chart_no || '';
    $('p-name').value = patient?.name || '';
    $('p-birth').value = patient?.birth_date || '';
    $('p-sex').value = patient?.sex || '';
    $('p-phone').value = patient?.phone || '';
    $('p-memo').value = patient?.memo || '';
    $('p-symptoms').value = patient?.symptoms || '';
    $('p-diagnosis').value = patient?.diagnosis || '';
    $('p-doctor').value = patient?.doctor || '';
    $('patient-dialog').showModal();
  }

  async function savePatient(e) {
    e.preventDefault();
    if (!normalizeBirthField()) { $('p-birth').focus(); return; }
    const body = {
      chart_no: $('p-chart').value.trim(),
      name: $('p-name').value.trim(),
      birth_date: normalizeDate($('p-birth').value) || null,
      sex: $('p-sex').value || null,
      phone: $('p-phone').value.trim() || null,
      memo: $('p-memo').value.trim() || null,
      symptoms: $('p-symptoms').value.trim() || null,
      diagnosis: $('p-diagnosis').value.trim() || null,
      doctor: $('p-doctor').value.trim() || null,
    };
    if (!body.chart_no || !body.name) return;
    try {
      let saved;
      if (state.editingPatientId) {
        saved = await api(`/patients/${state.editingPatientId}`, { method: 'PUT', body: JSON.stringify(body) });
      } else {
        saved = await api('/patients', { method: 'POST', body: JSON.stringify(body) });
      }
      state.currentPatientId = saved.id;
      $('patient-dialog').close();
      $('patient-search').value = '';
      await loadPatients();
      toast('환자 정보가 저장되었습니다.', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  async function deletePatient() {
    if (!state.editingPatientId) return;
    if (!confirm('이 환자와 관련 촬영 기록을 삭제하시겠습니까?')) return;
    try {
      await api(`/patients/${state.editingPatientId}`, { method: 'DELETE' });
      $('patient-dialog').close();
      state.currentPatientId = null;
      await loadPatients();
      toast('삭제되었습니다.', 'success');
    } catch (err) {
      toast(err.message, 'error');
    }
  }

  // ---- 저장 방식 (서버 / 이 PC에만) ----
  function syncStorageUI() {
    const btn = $('btn-storage');
    $('storage-label').textContent = LOCAL_MODE ? '저장: 이 PC 전용' : '저장: 서버';
    btn.classList.toggle('local', LOCAL_MODE);
    if (window.SlitLocal.policy !== 'choice') {
      btn.disabled = true;
      btn.title = '이 사이트는 저장 방식이 고정되어 있습니다 (' + (LOCAL_MODE ? '이 PC 전용' : '서버') + ')';
    }
    if (LOCAL_MODE) {
      $('hint-body').innerHTML = '<strong>이 PC 전용 모드</strong> — 환자 정보·사진·동영상을 인터넷 서버로 보내지 않고 이 PC의 브라우저에만 보관합니다. '
        + '브라우저 기록 삭제 시 함께 지워지므로 상단 <strong>"저장 폴더"</strong>를 지정해 PC 폴더에도 파일을 남기세요(Chrome/Edge).';
    }
  }
  async function openStorageDialog() {
    document.querySelectorAll('input[name="storage"]').forEach((r) => { r.checked = r.value === (LOCAL_MODE ? 'local' : 'server'); });
    const u = await window.SlitLocal.usage();
    $('storage-note').textContent = u && LOCAL_MODE
      ? `이 PC 저장소 사용량: ${(u.used / 1048576).toFixed(1)}MB / 사용 가능 ${(u.quota / 1073741824).toFixed(1)}GB`
      : '바꾸면 화면을 다시 불러옵니다. 두 저장 방식의 기록은 서로 옮겨지지 않습니다.';
    $('storage-dialog').showModal();
  }
  async function applyStorage() {
    const v = (document.querySelector('input[name="storage"]:checked') || {}).value || 'server';
    $('storage-dialog').close();
    if ((v === 'local') === LOCAL_MODE) return;
    window.SlitLocal.setMode(v);
    if (v === 'local') await window.SlitLocal.persist();   // 브라우저가 저장소를 임의로 비우지 않도록 요청
    location.reload();
  }

  // ---- theme ----
  function toggleTheme() {
    const root = document.documentElement;
    const isDark = root.getAttribute('data-theme') !== 'light';   // 기본은 어두운 영상 작업 공간
    const next = isDark ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('slitlamp-theme', next); } catch (_) {}
    syncThemeIcon();
  }
  function syncThemeIcon() {
    const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
    $('theme-icon-light').style.display = isDark ? 'block' : 'none';
    $('theme-icon-dark').style.display = isDark ? 'none' : 'block';
    $('theme-label').textContent = isDark ? '밝은 테마로' : '어두운 테마로';
  }

  // ---- folder (File System Access API) ----
  async function pickFolder() {
    try {
      state.dirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
      const status = $('folder-status');
      status.classList.add('linked');
      status.innerHTML = `<span class="dot"></span> ${state.dirHandle.name} 폴더 연결됨`;
      toast('지정 폴더가 연결되었습니다. 이후 캡처가 이 폴더에도 저장됩니다.', 'success');
    } catch (err) {
      if (err && err.name !== 'AbortError') toast('폴더 연결 실패: ' + err.message, 'error');
    }
  }

  async function saveToFolder(filename, blob) {
    if (!state.dirHandle) return false;
    try {
      const fh = await state.dirHandle.getFileHandle(filename, { create: true });
      const w = await fh.createWritable();
      await w.write(blob);
      await w.close();
      return true;
    } catch (err) {
      toast('지정 폴더 저장 실패: ' + err.message, 'error');
      return false;
    }
  }

  // ---- camera ----
  // ---- 카메라 (소니 USB 스트리밍 자동 인식 · 해상도) ----
  // 장치 이름 끝의 USB id '(054c:0e10)' 는 떼고 단어 단위로 비교 (웹캠 id 의 'a7' 오인식 방지). 054c = 소니 제조사 id
  const SONY_RE = /\bsony\b|\bzv-?e10|\bilme\b|\bfx30?\b|\bilce\b|\ba7\w*\b|\balpha\b/i;
  const isSony = (label) => /\(054c:[0-9a-f]{4}\)/i.test(label || '') || SONY_RE.test((label || '').replace(/\([0-9a-f]{4}:[0-9a-f]{4}\)/ig, ''));
  const RES = { '2160': [3840, 2160], '1080': [1920, 1080], '720': [1280, 720] };
  function prefGet(k) { try { return localStorage.getItem(k); } catch (_) { return null; } }
  function prefSet(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* 저장 불가 */ } }

  let camGen = 0;
  async function startCamera(deviceId) {
    const gen = ++camGen;                              // 연달아 바꿀 때 늦게 끝난 이전 요청의 스트림은 버린다
    const stale = (st) => { if (gen === camGen) return false; if (st) st.getTracks().forEach((t) => t.stop()); return true; };
    if (state.isRecording) stopRecording();
    if (state.isPaused) togglePause();                 // 정지 화면이 새 카메라 영상 위에 남지 않게
    if (state.stream) { state.stream.getTracks().forEach(t => t.stop()); state.stream = null; }
    try {
      const video = $('video');
      const [w, h] = RES[$('res-select').value] || RES['1080'];
      deviceId = deviceId || prefGet('slitlamp-cam') || undefined;
      const want = { width: { ideal: w }, height: { ideal: h }, frameRate: { ideal: 30 } };
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: deviceId ? { ...want, deviceId: { exact: deviceId } } : want, audio: false });
      } catch (e) {
        if (!deviceId) throw e;
        if (stale()) return;
        stream = await navigator.mediaDevices.getUserMedia({ video: want, audio: false });   // 저장된 장치가 없어졌으면 기본 장치
      }
      if (stale(stream)) return;
      // 처음 권한을 받은 뒤 소니 카메라(USB 스트리밍)가 연결돼 있으면 그쪽으로 자동 전환
      if (!deviceId && !prefGet('slitlamp-cam')) {
        const cams = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
        if (stale(stream)) return;
        const cur = stream.getVideoTracks()[0];
        const sony = cams.find((c) => isSony(c.label));
        if (sony && !isSony(cur && cur.label)) {
          stream.getTracks().forEach((t) => t.stop());
          stream = await navigator.mediaDevices.getUserMedia({ video: { ...want, deviceId: { exact: sony.deviceId } }, audio: false });
          if (stale(stream)) return;
          toast(`소니 카메라를 찾아 연결했습니다: ${sony.label}`, 'success');
        }
      }
      state.stream = stream;
      video.srcObject = stream;
      const track = stream.getVideoTracks()[0];
      // 케이블 빠짐·전원 꺼짐 — 멈춘 마지막 화면이 새 사진처럼 저장되지 않게 촬영을 막고 알린다
      if (track) track.addEventListener('ended', () => {
        if (state.stream !== stream) return;
        state.stream = null;
        if (state.isRecording) stopRecording();
        video.srcObject = null;
        $('video-placeholder').style.display = 'flex';
        updateCaptureButtons();
        setStatus('카메라 연결 끊김', 'idle');
        toast('카메라 연결이 끊어졌습니다. 연결을 확인한 뒤 카메라를 다시 시작하세요.', 'error');
      });
      const set = track && track.getSettings ? track.getSettings() : {};
      if (set.deviceId) prefSet('slitlamp-cam', set.deviceId);
      $('video-placeholder').style.display = 'none';
      video.onloadedmetadata = () => {
        const sonyTag = isSony(track && track.label) ? ' · Sony' : '';
        setStatus(`${video.videoWidth}×${video.videoHeight}${sonyTag}`, 'ready');
      };
      await populateCameraSelect();
      updateCaptureButtons();
    } catch (err) {
      if (gen !== camGen) return;
      $('video').srcObject = null;                     // 멈춘 이전 영상으로 촬영되지 않게 버튼도 끈다
      updateCaptureButtons();
      showHintError(`카메라 접근 실패: ${err.message || err.name} (HTTPS/localhost 필요)`);
      $('video-placeholder').style.display = 'flex';
      setStatus('대기 중', 'idle');
    }
  }

  async function populateCameraSelect() {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const cams = devices.filter(d => d.kind === 'videoinput');
      const sel = $('camera-select');
      const currentId = state.stream?.getVideoTracks?.()[0]?.getSettings?.().deviceId;
      sel.innerHTML = '';
      cams.forEach((c, i) => {
        const opt = document.createElement('option');
        opt.value = c.deviceId;
        opt.textContent = (isSony(c.label) ? '★ ' : '') + (c.label || `카메라 ${i + 1}`);
        sel.appendChild(opt);
      });
      if (cams.length === 0) {
        const opt = document.createElement('option'); opt.textContent = '(카메라 없음)'; opt.disabled = true; sel.appendChild(opt);
      } else if (currentId) {
        sel.value = currentId;
      }
    } catch (_) {}
  }

  function togglePause() {
    if (!state.stream) return;
    const video = $('video'), frame = $('video-frame');
    if (!state.isPaused) {
      const cnv = $('freeze');
      cnv.width = video.videoWidth || 640;
      cnv.height = video.videoHeight || 480;
      cnv.getContext('2d').drawImage(video, 0, 0, cnv.width, cnv.height);
      video.pause();
      frame.classList.add('paused');
      state.isPaused = true;
      sfx('freeze');
      $('btn-pause').innerHTML = '<svg class="ic"><use href="#i-play"/></svg>재개';
    } else {
      video.play();
      frame.classList.remove('paused');
      state.isPaused = false;
      $('btn-pause').innerHTML = '<svg class="ic"><use href="#i-pause"/></svg>일시정지';
    }
  }

  // ---- 효과음 (sounds.js) ----
  // 사진 캡처 = 필름 카메라 '찰칵', 정지영상(스틸) = '틱', 녹화 시작/중지 = '삐' / '삐삐'
  function sfx(name) { try { if (window.SlitSounds) window.SlitSounds.play(name); } catch (_) { /* 소리는 촬영을 막지 않는다 */ } }
  function soundsOn() { return !window.SlitSounds || window.SlitSounds.enabled(); }
  function setSounds(on) {
    if (window.SlitSounds) window.SlitSounds.setEnabled(on);
    syncSoundsButton();
    if (on) sfx('freeze');                                    // 켜면 바로 들려 준다
    toast(on ? '효과음 켬' : '효과음 끔', 'success');
  }
  function syncSoundsButton() {
    const on = soundsOn();
    $('btn-sounds').setAttribute('aria-pressed', String(on));
    $('sounds-label').textContent = on ? '효과음 켜짐' : '효과음 꺼짐';
  }

  // ---- capture ----
  let _fnSeq = 0;
  function buildFilename(patient, mediaType, ts, ext, eye, note) {
    const pad = (n, w = 2) => String(n).padStart(w, '0');
    const chart = (patient.chart_no || 'noChart').replace(/[^0-9A-Za-z._가-힣-]+/g, '_');
    const stamp = `${ts.getFullYear()}${pad(ts.getMonth() + 1)}${pad(ts.getDate())}_` +
      `${pad(ts.getHours())}${pad(ts.getMinutes())}${pad(ts.getSeconds())}${pad(ts.getMilliseconds(), 3)}`;
    const seq = pad((_fnSeq++) % 1000, 3);
    const tag = note === '4분할 합성' ? '_quad' : (note === '4분할' ? '_q' : '');
    return `${chart}_${stamp}_${seq}_${eye || 'NA'}${tag}.${ext}`;
  }

  // ---- 마이봄샘 촬영 모드 ----
  // 근적외선(NIR, 850nm 권장) 조명 + 뒤집은 눈꺼풀. 화면은 흑백·대비 강화, 저장은 흑백 원본(PNG) + 메모 '마이봄 상안검/하안검'
  const meibo = { on: false, lid: 'upper', enh: true, inv: false };
  try { Object.assign(meibo, JSON.parse(localStorage.getItem('slitlamp-meibo') || '{}'), { on: false }); } catch (_) { /* 기본값 */ }
  const LID_KO = { upper: '상안검', lower: '하안검' };
  function saveMeibo() { try { localStorage.setItem('slitlamp-meibo', JSON.stringify({ lid: meibo.lid, enh: meibo.enh, inv: meibo.inv })); } catch (_) { /* 저장 불가 */ } }
  function syncMeibo() {
    const b = document.body;
    b.classList.toggle('meibo-on', meibo.on);
    b.classList.toggle('mb-enh', meibo.enh);
    b.classList.toggle('mb-inv', meibo.inv);
    b.classList.toggle('mb-lower', meibo.lid === 'lower');
    $('btn-meibo').setAttribute('aria-pressed', String(meibo.on));
    $('btn-mb-enh').setAttribute('aria-pressed', String(meibo.enh));
    $('btn-mb-inv').setAttribute('aria-pressed', String(meibo.inv));
    document.querySelectorAll('#mb-lid-seg [data-lid]').forEach((x) => x.setAttribute('aria-checked', String(x.dataset.lid === meibo.lid)));
    $('hud-meibo').textContent = `MEIBO · ${LID_KO[meibo.lid]}`;
  }
  function setMeibo(on) {
    if (on && state.quad) toggleQuad();             // 마이봄은 1화면으로
    meibo.on = !!on; syncMeibo();
    toast(meibo.on ? `마이봄 촬영 모드 · ${LID_KO[meibo.lid]} (눈꺼풀을 뒤집어 틀에 맞추세요)` : '마이봄 촬영 모드 끔', 'success');
  }
  function setLid(lid) { meibo.lid = lid === 'lower' ? 'lower' : 'upper'; saveMeibo(); syncMeibo(); }

  async function capturePhoto() {
    const patient = currentPatient();
    if (!patient) { toast('환자를 먼저 선택하세요.', 'error'); return; }
    const video = $('video');
    if (!video.videoWidth) { toast('영상이 아직 준비되지 않았습니다.', 'error'); return; }
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    sfx('shutter');
    const src = state.isPaused ? $('freeze') : video;
    const cx = canvas.getContext('2d');
    if (meibo.on) cx.filter = 'grayscale(1)';      // 마이봄: 흑백 원본 (대비 강화·반전은 화면 보기 전용)
    cx.drawImage(src, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(res => canvas.toBlob(res, 'image/png'));
    if (!blob) return;
    const eye = $('eye-select').value;
    state.lastSaved = null;
    const ok = await persistCapture(patient, 'photo', blob, 'png', eye, null, meibo.on ? `마이봄 ${LID_KO[meibo.lid]}` : null);
    // 찍은 사진을 큰 화면으로 — '다시 촬영' · Enter · Esc 로 촬영 화면, 촬영 키는 닫고 바로 다시 찍기
    const rec = state.lastSaved;
    if (ok && rec && rec.url && $('view-after-capture').checked && !document.fullscreenElement && !document.querySelector('dialog[open]')) {
      openViewer(rec, parseTs(rec.captured_at), { afterCapture: true });
    }
  }

  function toggleRecord() {
    if (state.isRecording) stopRecording();
    else startRecording();
  }

  function startRecording() {
    if (!state.stream) return;
    const patient = currentPatient();
    if (!patient) { toast('환자를 먼저 선택하세요.', 'error'); return; }
    const mimes = ['video/mp4;codecs=avc1', 'video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'];
    const mime = mimes.find(m => window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m));
    let recorder;
    try {
      recorder = new MediaRecorder(state.stream, { ...(mime ? { mimeType: mime } : {}), videoBitsPerSecond: recordBitrate() });
    } catch (err) { toast('녹화 시작 실패: ' + (err.message || err.name), 'error'); return; }
    const chunks = [];
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    const eye = $('eye-select').value, start = Date.now();   // 시작할 때의 안구·시각으로 저장
    recorder.onstop = async () => {
      if (state.recorder === recorder && state.isRecording) {
        // 누르지 않았는데 멈춤 (카메라 끊김·브라우저 오류) — 계속 녹화 중인 것처럼 보이지 않게
        endRecordingUi();
        toast('녹화가 중단되었습니다 (카메라 연결 확인). 그때까지 녹화한 영상은 저장합니다.', 'error');
      }
      try {
        const blob = new Blob(chunks, { type: recorder.mimeType || 'video/webm' });
        const durationMs = (recorder.stoppedAt || Date.now()) - start;
        const ext = (blob.type && blob.type.includes('mp4')) ? 'mp4' : 'webm';
        await persistCapture(patient, 'video', blob, ext, eye, durationMs);
      } finally {
        state.saving--;
      }
    };
    recorder.start();
    state.saving++;                                 // 녹화분이 저장될 때까지
    sfx('rec_start');
    state.recorder = recorder;
    state.isRecording = true;
    state.recordStart = start;
    state.recordPatientId = patient.id;
    $('rec-badge').classList.add('on');
    $('btn-record').classList.add('recording');
    $('rec-btn-label').textContent = '녹화 중지';
    $('btn-fs-record').classList.toggle('recording', true); $('fs-rec-label').textContent = true ? '녹화 중지' : '녹화';
    setStatus('녹화 중', 'recording');
    state.recordTimer = setInterval(() => {
      const s = Math.floor((Date.now() - state.recordStart) / 1000);
      $('rec-time').textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
    }, 250);
  }

  // 동영상 화질 (설정 → 동영상 화질) — 지정하지 않으면 브라우저가 해상도와 관계없이 낮은 기본값으로 녹화한다
  const VQ_BPP = { normal: 0.08, high: 0.2 };   // 픽셀·프레임당 비트: 1080p30 ≈ 5 / 12 Mbps, 4K30 ≈ 20 / 50 Mbps
  function recordBitrate() {
    const t = state.stream && state.stream.getVideoTracks()[0];
    const s = (t && t.getSettings) ? t.getSettings() : {};
    const w = s.width || $('video').videoWidth || 1920, h = s.height || $('video').videoHeight || 1080;
    const fps = Math.min(60, s.frameRate || 30);
    return Math.round(w * h * fps * VQ_BPP[$('vq-select').value === 'high' ? 'high' : 'normal']);
  }

  function stopRecording() {
    if (!state.isRecording || !state.recorder) return;
    state.recorder.stoppedAt = Date.now();
    if (state.recorder.state !== 'inactive') state.recorder.stop();
    sfx('rec_stop');
    endRecordingUi();
  }

  function endRecordingUi() {
    state.isRecording = false;
    clearInterval(state.recordTimer);
    $('rec-badge').classList.remove('on');
    $('rec-time').textContent = '00:00';
    $('btn-record').classList.remove('recording');
    $('rec-btn-label').textContent = '녹화 시작';
    $('btn-fs-record').classList.toggle('recording', false); $('fs-rec-label').textContent = false ? '녹화 중지' : '녹화';
  }

  // 서버 + 지정 폴더 동시 저장
  // 인터넷 호스팅(Vercel 등)은 업로드 크기 제한(약 4.5MB)이 있어, 큰 PNG 사진은 고화질 JPEG 로 바꿔 올린다
  const IS_LOCAL = ['localhost', '127.0.0.1', '[::1]', ''].includes(location.hostname);
  const UPLOAD_SOFT_LIMIT = 4 * 1024 * 1024;
  async function shrinkPhoto(blob) {
    try {
      const bmp = await createImageBitmap(blob);
      const c = document.createElement('canvas');
      c.width = bmp.width; c.height = bmp.height;
      c.getContext('2d').drawImage(bmp, 0, 0);
      for (const q of [0.95, 0.9, 0.82]) {
        const j = await new Promise((res) => c.toBlob(res, 'image/jpeg', q));
        if (j && j.size <= UPLOAD_SOFT_LIMIT) return j;
      }
    } catch (_) { /* 변환 실패 시 원본 그대로 */ }
    return null;
  }

  // 촬영 직후 썸네일을 바로 보여 준다 (서버 저장이 끝나면 목록을 다시 그려 교체)
  function addPendingThumb(blob, eye, ts) {
    const list = $('thumb-list');
    $('thumb-empty').style.display = 'none';
    const el = document.createElement('div');
    el.className = 'thumb pending';
    const url = URL.createObjectURL(blob);
    el.innerHTML = `<img src="${url}" alt="">
      <span class="thumb-badges">${eye && eye !== 'NA' ? `<span class="thumb-badge e-${eye}">${eye}</span>` : ''}<span class="thumb-badge">저장 중…</span></span>
      <div class="thumb-caption"><span><span class="thumb-time">${hms(ts)}</span></span></div>`;
    const first = list.querySelector('.thumb-day, .thumb');
    list.insertBefore(el, first);
    return { el, url };
  }

  async function persistCapture(...args) {
    state.saving++;
    try { return await persistCaptureNow(...args); } finally { state.saving--; }
  }

  async function persistCaptureNow(patient, mediaType, blob, ext, eye, durationMs, note, opts = {}) {
    const ts = new Date();
    // 지정 폴더에는 원본(무손실 PNG), 인터넷 서버에는 고화질 JPEG(약 1/3 크기)로 올려 저장 시간을 줄인다
    const folderBlob = blob, folderExt = ext;
    let upBlob = blob, upExt = ext;
    if (!IS_LOCAL && !LOCAL_MODE && mediaType === 'photo' && ext === 'png') {
      const j = await shrinkPhoto(blob);
      if (j) { upBlob = j; upExt = 'jpg'; }
    }
    const filename = buildFilename(patient, mediaType, ts, folderExt, eye, note);
    const upName = buildFilename(patient, mediaType, ts, upExt, eye, note);
    const pending = !opts.silent && mediaType === 'photo' ? addPendingThumb(folderBlob, eye, ts) : null;
    if (!opts.silent) setStatus('저장 중…', 'ready');

    // (a) 서버 저장 (환자 DB + 미디어 폴더) 과 (b) 지정 로컬 폴더 저장을 동시에
    const serverP = (async () => {
      try {
        const fd = new FormData();
        fd.append('patient_id', String(patient.id));
        fd.append('media_type', mediaType);
        fd.append('eye', eye || 'NA');
        fd.append('ext', upExt);
        if (durationMs != null) fd.append('duration_ms', String(durationMs));
        if (note) fd.append('note', note);
        fd.append('file', upBlob, upName);
        state.lastSaved = await api('/captures', { method: 'POST', body: fd });
        return true;
      } catch (err) {
        toast((LOCAL_MODE ? '이 PC 저장 실패: ' : '서버 저장 실패: ') + err.message, 'error');
        return false;
      }
    })();
    const folderP = state.dirHandle ? saveToFolder(filename, folderBlob) : Promise.resolve(false);
    const [serverOk, folderOk] = await Promise.all([serverP, folderP]);
    if (pending) {
      setTimeout(() => URL.revokeObjectURL(pending.url), 30000);
      if (!serverOk) pending.el.remove();
    }
    blob = folderBlob;

    // (c) 서버·폴더 모두 실패하면 촬영본을 잃지 않도록 파일로 내려받는다
    //     (예: 인터넷 호스팅의 업로드 크기 제한에 걸린 긴 동영상)
    if (!serverOk && !folderOk) {
      try {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = filename;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 60000);
        toast(`서버에 저장하지 못해 '${filename}' 파일로 내려받았습니다. 상단 '저장 폴더'를 지정하면 PC 폴더에 자동 보관됩니다.`, 'error');
      } catch (_) { /* 내려받기도 불가한 환경 */ }
    }

    if (!serverOk && !opts.silent) setStatus('저장 실패', 'idle', true);
    if (serverOk) document.dispatchEvent(new CustomEvent('slit:captures'));
    if (serverOk && !opts.silent) {
      renderThumbs();                      // 기다리지 않음 — 미리 보인 썸네일을 서버 목록으로 교체
      const base = LOCAL_MODE ? '이 PC' : '서버';
      const where = folderOk ? `${base} + 지정 폴더` : (state.dirHandle ? `${base}(폴더 실패)` : base);
      setStatus(`${mediaType === 'photo' ? '사진' : '동영상'} 저장됨 · ${where}`, 'ready', true);
    }
    return serverOk;
  }

  // ---- 4분할 정지영상 ----
  function quadCells() {
    return Array.from(document.querySelectorAll('#quad-grid .quad-cell'));
  }

  function toggleQuad() {
    if (!state.quad && meibo.on) setMeibo(false);
    state.quad = !state.quad;
    const frame = $('video-frame');
    frame.classList.toggle('quad', state.quad);
    $('btn-quad').classList.toggle('active', state.quad);
    $('btn-pause').style.display = state.quad ? 'none' : '';
    $('btn-save-quad').style.display = state.quad ? '' : 'none';
    $('btn-clear-quad').style.display = state.quad ? '' : 'none';
    if (state.quad) {
      clearQuad();
      startQuadLoop();
      setStatus("4분할 · '정지영상'으로 각 칸을 채우세요", 'ready');
    } else {
      stopQuadLoop();
      setStatus('단일 모드', 'ready');
    }
  }

  function startQuadLoop() {
    const cells = quadCells();
    const video = $('video');
    const tick = () => {
      if (!state.quad) return;
      const i = state.quadActive;
      if (i != null && !state.quadFrozen[i] && video.videoWidth) {
        const cnv = cells[i].querySelector('canvas');
        if (cnv.width !== video.videoWidth) { cnv.width = video.videoWidth; cnv.height = video.videoHeight; }
        cnv.getContext('2d').drawImage(video, 0, 0, cnv.width, cnv.height);
        state.quadHasImg[i] = true;
      }
      state.quadRAF = requestAnimationFrame(tick);
    };
    stopQuadLoop();
    state.quadRAF = requestAnimationFrame(tick);
  }
  function stopQuadLoop() {
    if (state.quadRAF) cancelAnimationFrame(state.quadRAF);
    state.quadRAF = null;
  }

  function refreshQuadCells() {
    quadCells().forEach((cell, i) => {
      cell.classList.toggle('active', i === state.quadActive);
      cell.classList.toggle('frozen', state.quadFrozen[i]);
      const tag = cell.querySelector('.quad-tag');
      if (i === state.quadActive) tag.textContent = '● 라이브';
      else if (state.quadFrozen[i]) tag.textContent = '정지' + (state.quadEyes[i] !== 'NA' ? ' · ' + state.quadEyes[i] : '');
      else tag.textContent = '';
    });
  }

  function freezeFrame() {
    if (!state.quad) {
      // 단일 모드: 일시정지(정지영상)와 동일하게 현재 프레임 고정 토글
      togglePause();
      return;
    }
    const i = state.quadActive;
    if (i == null || !state.quadHasImg[i]) { toast('라이브 영상이 준비되지 않았습니다.', 'error'); return; }
    state.quadFrozen[i] = true;
    state.quadEyes[i] = $('eye-select').value;
    sfx('freeze');
    // 다음 빈 칸 찾기
    let nxt = null;
    for (let j = 1; j <= 4; j++) { const k = (i + j) % 4; if (!state.quadFrozen[k]) { nxt = k; break; } }
    if (nxt == null) {
      state.quadActive = null;
      setStatus("4칸 모두 채움 · '4분할 저장' 또는 칸 클릭으로 교체", 'ready');
    } else {
      state.quadActive = nxt;
      state.quadHasImg[nxt] = false;
      setStatus('정지영상 추가됨 (다음 칸으로 이동)', 'ready');
    }
    refreshQuadCells();
  }

  function setQuadActive(idx) {
    state.quadActive = idx;
    state.quadFrozen[idx] = false;
    state.quadHasImg[idx] = false;
    refreshQuadCells();
  }

  function clearQuad() {
    state.quadActive = 0;
    state.quadFrozen = [false, false, false, false];
    state.quadHasImg = [false, false, false, false];
    state.quadEyes = ['NA', 'NA', 'NA', 'NA'];
    quadCells().forEach((cell) => {
      const c = cell.querySelector('canvas');
      if (c.width) c.getContext('2d').clearRect(0, 0, c.width, c.height);
    });
    refreshQuadCells();
  }

  async function saveQuad() {
    const patient = currentPatient();
    if (!patient) { toast('환자를 먼저 선택하세요.', 'error'); return; }
    const cells = quadCells();
    const indices = [0, 1, 2, 3].filter(i => state.quadFrozen[i] || i === state.quadActive);
    const withImg = indices.filter(i => state.quadHasImg[i]);
    if (withImg.length === 0) { toast('저장할 정지영상이 없습니다.', 'error'); return; }
    sfx('shutter');
    let saved = 0;
    // 개별 저장
    for (const i of withImg) {
      const cnv = cells[i].querySelector('canvas');
      const blob = await new Promise(res => cnv.toBlob(res, 'image/png'));
      if (!blob) continue;
      const eye = state.quadFrozen[i] ? state.quadEyes[i] : $('eye-select').value;
      const ok = await persistCapture(patient, 'photo', blob, 'png', eye, null, '4분할', { silent: true });
      if (ok) saved++;
    }
    // 2×2 합성 저장
    const ref = cells[withImg[0]].querySelector('canvas');
    const cw = ref.width, ch = ref.height;
    const comp = document.createElement('canvas');
    comp.width = cw * 2; comp.height = ch * 2;
    const cx = comp.getContext('2d');
    cx.fillStyle = '#000'; cx.fillRect(0, 0, comp.width, comp.height);
    for (const i of withImg) {
      const cnv = cells[i].querySelector('canvas');
      const r = Math.floor(i / 2), c = i % 2;
      cx.drawImage(cnv, c * cw, r * ch, cw, ch);
    }
    const compBlob = await new Promise(res => comp.toBlob(res, 'image/png'));
    if (compBlob) {
      const ok = await persistCapture(patient, 'photo', compBlob, 'png', 'NA', null, '4분할 합성', { silent: true });
      if (ok) saved++;
    }
    await renderThumbs();
    setStatus(`4분할 저장됨 (개별+합성 ${saved}장)`, 'ready', true);
    toast(`4분할 저장 완료 (${saved}장)`, 'success');
  }

  // ---- thumbnails ----
  const DOW = ['일', '월', '화', '수', '목', '금', '토'];
  const hms = (d) => [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':');
  let thumbSeq = 0;
  async function renderThumbs() {
    const my = ++thumbSeq, pid = state.currentPatientId;
    const list = $('thumb-list');
    const empty = $('thumb-empty');
    const clear = () => Array.from(list.querySelectorAll('.thumb, .thumb-day')).forEach(n => n.remove());
    if (!pid) {
      clear();
      empty.style.display = 'block';
      empty.querySelector('div').innerHTML = '환자를 선택하세요';
      $('thumb-count').textContent = '0';
      return;
    }
    let items = [];
    try {
      items = await api(`/captures?patient_id=${pid}&order=desc`);
    } catch (e) {
      if (my === thumbSeq) toast('촬영 목록 조회 실패: ' + e.message, 'error');
    }
    if (my !== thumbSeq || pid !== state.currentPatientId) return;   // 그 사이 다른 환자·필터로 바뀜 → 늦은 결과는 버림
    clear();
    $('thumb-count').textContent = items.length;
    const f = state.thumbFilter || 'all';
    const shown = items.filter((c) => f === 'all' || (f === 'video' ? c.media_type === 'video' : c.eye === f));
    if (shown.length === 0) {
      empty.style.display = 'block';
      empty.querySelector('div').innerHTML = items.length ? '조건에 맞는 영상이 없습니다' : '선택한 환자의<br>촬영 이미지가 없습니다';
      return;
    }
    empty.style.display = 'none';
    // 촬영일별 묶음 (최근 → 과거)
    let day = '';
    const perDay = {};
    for (const c of shown) { const d = (c.captured_at || '').slice(0, 10); perDay[d] = (perDay[d] || 0) + 1; }
    for (const c of shown) {
      const ts = parseTs(c.captured_at);
      const d = (c.captured_at || '').slice(0, 10);
      if (d !== day) {
        day = d;
        const h = document.createElement('div');
        h.className = 'thumb-day';
        h.innerHTML = `<span>${d.replace(/-/g, '.')} (${DOW[ts.getDay()] || ''})</span><span class="thumb-day-r"><span>${perDay[d]}장</span>`
          + `<button type="button" class="thumb-day-del" title="${d.replace(/-/g, '.')} 촬영 영상 모두 삭제" aria-label="이 날 영상 모두 삭제"><svg class="ic"><use href="#i-trash"/></svg></button></span>`;
        const dayCaps = items.filter((x) => (x.captured_at || '').slice(0, 10) === d);     // 필터와 무관하게 그 날 전체
        h.querySelector('.thumb-day-del').addEventListener('click', (e) => { e.stopPropagation(); deleteDay(d, dayCaps); });
        list.appendChild(h);
      }
      const el = document.createElement('div');
      el.className = 'thumb';
      el.tabIndex = 0;
      const eye = c.eye && c.eye !== 'NA' ? c.eye : '';
      const isVideo = c.media_type === 'video';
      const dur = isVideo ? `▶ ${Math.round((c.duration_ms || 0) / 1000)}s` : '';
      el.innerHTML = `
        ${isVideo
          ? '<div class="tvid"><svg class="ic"><use href="#i-play"/></svg></div>'
          : `<img src="${c.url}" alt="캡처 ${fmt(ts)}" loading="lazy">`}
        <span class="thumb-badges">
          ${eye ? `<span class="thumb-badge e-${eye}">${eye}</span>` : ''}
          ${dur ? `<span class="thumb-badge video">${dur}</span>` : ''}
        </span>
        <div class="thumb-caption">
          <span><span class="thumb-time">${hms(ts)}</span>${c.note ? `<span class="thumb-note">${escHtml(c.note)}</span>` : ''}</span>
        </div>`;
      el.title = [fmtDT(ts), eye, c.note].filter(Boolean).join(' · ');
      el.addEventListener('click', () => openViewer(c, ts));
      el.addEventListener('keydown', (e) => { if (e.key === 'Enter') openViewer(c, ts); });
      list.appendChild(el);
    }
  }

  function openViewer(c, ts, opts = {}) {
    const dlg = $('viewer-dialog');
    const body = $('viewer-body');
    body.innerHTML = '';
    const after = !!opts.afterCapture;
    dlg.classList.toggle('after-capture', after);
    $('viewer-retake').style.display = after ? '' : 'none';      // .btn 의 display 가 hidden 속성을 덮으므로 직접
    $('viewer-close-2').style.display = after ? 'none' : '';
    const patient = state.patients.find(p => p.id === c.patient_id);
    $('viewer-title').textContent = `${after ? '촬영한 사진 · ' : ''}${patient?.name || ''} · ${fmtDT(ts)}${c.eye && c.eye !== 'NA' ? ' · ' + c.eye : ''}`;
    if (c.media_type === 'photo') {
      const img = document.createElement('img'); img.src = c.url; body.appendChild(img);
    } else {
      const v = document.createElement('video'); v.src = c.url; v.controls = true; v.autoplay = true; body.appendChild(v);
    }
    const dl = $('viewer-download');
    dl.href = c.url;
    dl.download = c.filename || (c.url.split('/').pop()) || 'capture';
    state.viewing = c;
    dlg.showModal();
  }

  /** 촬영일 제목 줄의 삭제 — 그 날 촬영한 사진 · 동영상을 모두 지운다 (검사 소견은 남김) */
  let deletingDay = false;
  async function deleteDay(day, caps) {
    if (deletingDay || !caps || !caps.length) return 0;
    const nPhoto = caps.filter((c) => c.media_type !== 'video').length, nVideo = caps.length - nPhoto;
    const what = [nPhoto ? `사진 ${nPhoto}장` : '', nVideo ? `동영상 ${nVideo}개` : ''].filter(Boolean).join(' · ');
    const who = state.patients.find((p) => p.id === caps[0].patient_id);          // 누구의 영상인지 확인 창에 (앱과 같게)
    if (!confirm(`${who ? who.name + ' 환자의 ' : ''}${day.replace(/-/g, '.')} 촬영 영상을 모두 삭제하시겠습니까?\n\n${what} — 저장된 파일과 기록이 함께 지워지며 되돌릴 수 없습니다.\n(그 날의 검사 소견은 남겨 둡니다)`)) return 0;
    deletingDay = true;
    let n = 0;
    try {
      for (const c of caps) {
        try { await api(`/captures/${c.id}`, { method: 'DELETE' }); n++; }
        catch (e) { toast(`삭제 실패 (${fmtDT(parseTs(c.captured_at))}): ${e.message}`, 'error'); }
      }
    } finally { deletingDay = false; }
    await renderThumbs();
    document.dispatchEvent(new CustomEvent('slit:captures'));     // Pro 화면 목록도 갱신
    if (n) toast(`${day.replace(/-/g, '.')} 촬영 영상 ${n}장을 삭제했습니다.`, 'success');
    return n;
  }

  /** 보기 창에서 '삭제' — 열어 둔 영상 한 건을 DB·파일에서 지우고 목록을 갱신한다 */
  let deletingViewed = false;
  async function deleteViewed() {
    const c = state.viewing;
    if (!c || deletingViewed) return false;
    const kind = c.media_type === 'video' ? '동영상' : '사진';
    if (!confirm(`이 ${kind}(${fmtDT(parseTs(c.captured_at))})을(를) 삭제하시겠습니까?\n\n저장된 파일과 기록이 함께 지워지며 되돌릴 수 없습니다.`)) return false;
    deletingViewed = true;
    try {
      await api(`/captures/${c.id}`, { method: 'DELETE' });
    } catch (e) {
      toast('삭제 실패: ' + e.message, 'error');
      return false;
    } finally { deletingViewed = false; }
    state.viewing = null;
    $('viewer-dialog').close();
    await renderThumbs();
    document.dispatchEvent(new CustomEvent('slit:captures'));     // Pro 화면 목록도 갱신
    toast(`${kind}을(를) 삭제했습니다.`, 'success');
    return true;
  }

  // ---- status / toast ----
  function updateCaptureButtons() {
    const has = !!state.stream && !!state.currentPatientId;
    $('btn-capture').disabled = !has;
    $('btn-record').disabled = !has;
    $('btn-pause').disabled = !state.stream;
    $('btn-fs-capture').disabled = !has;
    $('btn-fs-freeze').disabled = !has;
    $('btn-fs-record').disabled = !has;
    $('video-frame').classList.toggle('live', !!state.stream);
    $('btn-freeze').disabled = !has;
    $('btn-save-quad').disabled = !has;
  }
  function syncCamChip() {
    const v = $('video'), track = state.stream && state.stream.getVideoTracks()[0];
    const dot = $('cam-chip-dot');
    dot.classList.toggle('ready', !!track);
    if (!track) { $('cam-chip-text').textContent = '카메라 꺼짐'; return; }
    const name = (track.label || '카메라').replace(/\s*\([0-9a-f]{4}:[0-9a-f]{4}\)\s*$/i, '');
    $('cam-chip-text').textContent = `${isSony(track.label) ? '★ ' : ''}${name}${v.videoWidth ? ` · ${v.videoHeight >= 2000 ? '4K' : v.videoHeight + 'p'}` : ''}`;
    $('cam-chip').title = `${track.label} · ${v.videoWidth}×${v.videoHeight} (설정에서 변경)`;
  }

  function setStatus(text, kind, transient) {
    syncCamChip();
    $('status-text').textContent = text;
    const dot = $('status-dot');
    dot.classList.remove('ready', 'recording');
    if (kind === 'ready') dot.classList.add('ready');
    if (kind === 'recording') dot.classList.add('recording');
    if (state.statusResetTimer) clearTimeout(state.statusResetTimer);
    if (transient) {
      state.statusResetTimer = setTimeout(() => {
        const v = $('video'), tr = state.stream && state.stream.getVideoTracks()[0];
        if (state.isRecording) setStatus('녹화 중', 'recording');
        else if (v.videoWidth) setStatus(`${v.videoWidth}×${v.videoHeight}${isSony(tr && tr.label) ? ' · Sony' : ''}`, 'ready');
        else setStatus('대기 중', 'idle');
      }, 2500);
    }
  }
  function showHintError(msg) {
    const hint = $('hint');
    hint.classList.add('error');
    $('hint-body').innerHTML = `<strong>안내:</strong> ${msg}`;
    hint.style.display = 'flex';
  }
  function toast(msg, kind) {
    const wrap = $('toast-wrap');
    const el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.textContent = msg;
    wrap.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
      el.classList.remove('show');
      setTimeout(() => el.remove(), 220);
    }, 3200);
  }

  // Pro 모드(pro.js)에서 쓰는 공개 API
  window.SlitApp = {
    state, api, currentPatient, persistCapture, renderThumbs, toast, setStatus,
    startCamera, openPatientDialog, parseTs, fmtDT, openViewer, sfx,
  };

  init();
})();

// 브라우저 극한 시나리오 (실제 화면을 가짜 카메라로 조작): 연타 촬영, 서버 오류·네트워크 끊김 중 촬영,
// 녹화 중 환자 전환·카메라 끊김·새로고침, 카메라 끊긴 뒤 촬영.
//
//   실행 (web/ 에서):  npm i playwright && npx playwright install chromium   (처음 한 번)
//                      PYTHON=python node tests/e2e_extreme.js
//   PYTHON 은 fastapi·uvicorn 이 설치된 파이썬 (기본 python3). 임시 폴더에 서버를 띄우고 끝나면 지운다.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs'); const os = require('os'); const path = require('path');
const WEB = path.resolve(__dirname, '..');
const DATA = fs.mkdtempSync(path.join(os.tmpdir(), 'slitlamp-e2e-'));
const PORT = 8765 + Math.floor(Math.random() * 500);
const BASE = `http://127.0.0.1:${PORT}`;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const results = [];
const check = (name, ok, info = '') => { results.push({ name, ok, info }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  ${info}`); };

(async () => {
  const srv = spawn(process.env.PYTHON || 'python3', ['-m', 'uvicorn', 'app.main:app', '--port', String(PORT), '--log-level', 'warning'],
    { cwd: WEB, env: { ...process.env, SLITLAMP_DATA_DIR: DATA, SLITLAMP_PASSWORD: '' }, stdio: 'ignore' });
  for (let i = 0; i < 100; i++) { try { if ((await fetch(BASE + '/health')).ok) break; } catch (_) {} await sleep(100); }
  const api = async (p, o = {}) => (await fetch(BASE + '/api/v1' + p, { headers: { 'Content-Type': 'application/json' }, ...o })).json();
  const A = await api('/patients', { method: 'POST', body: JSON.stringify({ chart_no: 'A1', name: '가환자' }) });
  const B = await api('/patients', { method: 'POST', body: JSON.stringify({ chart_no: 'B1', name: '나환자' }) });
  const caps = async (pid) => api(`/captures?patient_id=${pid}`);

  const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
  const ctx = await browser.newContext({ acceptDownloads: true, permissions: ['camera'] });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => console.log('PAGEERROR', e.message));
  // 촬영 화면으로 들어가 카메라 영상이 나올 때까지 (홈 화면 → '촬영', 필요하면 '카메라 시작')
  const ready = async () => {
    await page.waitForFunction(() => window.SlitApp, null, { timeout: 15000 });
    if (await page.evaluate(() => document.body.classList.contains('home-on'))) await page.click('#btn-mode-capture');
    const live = () => page.waitForFunction(() => document.getElementById('video').videoWidth > 0, null, { timeout: 8000 });
    await live().catch(async () => { await page.click('#btn-start-camera').catch(() => {}); await live(); });
  };
  await page.goto(BASE + '/');
  await ready();
  const select = async (pid) => page.evaluate((id) => { const s = document.getElementById('patient-select'); s.value = String(id); s.dispatchEvent(new Event('change')); }, pid);
  await select(A.id); await sleep(300);
  
  // 1) 연타 촬영 15회
  for (let i = 0; i < 15; i++) await page.click('#btn-capture', { noWaitAfter: true });
  await sleep(4000);
  check('연타 15장 모두 서버에 저장', (await caps(A.id)).length === 15, `got ${(await caps(A.id)).length}`);

  // 2) 서버 500 / 3) 네트워크 끊김 — 촬영본이 내려받기로 보존되는가
  for (const mode of ['500', 'abort']) {
    await page.route('**/api/v1/captures', (r) => r.request().method() === 'POST'
      ? (mode === '500' ? r.fulfill({ status: 500, body: '{"detail":"boom"}', contentType: 'application/json' }) : r.abort('connectionreset'))
      : r.continue());
    const dl = page.waitForEvent('download', { timeout: 8000 }).catch(() => null);
    await page.click('#btn-capture');
    const d = await dl;
    let ok = false, info = 'no download';
    if (d) { const p = await d.path(); const buf = fs.readFileSync(p); ok = buf.slice(1, 4).toString() === 'PNG'; info = `${d.suggestedFilename()} ${buf.length}B`; }
    check(`서버 ${mode} 때 사진이 파일로 보존`, ok, info);
    await page.unroute('**/api/v1/captures');
  }

  // 4) 정상 녹화
  const before = (await caps(A.id)).filter(c => c.media_type === 'video').length;
  await page.click('#btn-record'); await sleep(1500); await page.click('#btn-record'); await sleep(2500);
  check('정상 녹화 저장', (await caps(A.id)).filter(c => c.media_type === 'video').length === before + 1);

  // 5) 녹화 중 다른 환자 선택 → 앞 환자에게 저장
  await page.click('#btn-record'); await sleep(1200);
  await select(B.id); await sleep(2500);
  const recAfterSwitch = await page.evaluate(() => window.SlitApp.state.isRecording);
  if (recAfterSwitch) { await page.click('#btn-record'); await sleep(2500); }
  check('녹화 중 환자 전환 → 녹화를 끝냄 (새 환자 눈이 앞 환자 영상에 들어가지 않게)', !recAfterSwitch);
  check('녹화 중 환자 전환 → 앞 환자에게만 저장', (await caps(A.id)).filter(c => c.media_type === 'video').length === before + 2
        && (await caps(B.id)).filter(c => c.media_type === 'video').length === 0,
        `A=${(await caps(A.id)).filter(c => c.media_type === 'video').length} B=${(await caps(B.id)).filter(c => c.media_type === 'video').length} stillRecording=${recAfterSwitch}`);
  await select(A.id); await sleep(500);

  // 5-1) 동영상 화질 일반 / 고화질 — 녹화기가 받은 비트레이트, 새로고침 뒤에도 기억
  const brOf = async (mode) => {
    await page.click('#btn-settings'); await page.selectOption('#vq-select', mode); await page.click('#settings-x');
    await page.click('#btn-record'); await sleep(800);
    const br = await page.evaluate(() => window.SlitApp.state.recorder.videoBitsPerSecond);
    await page.click('#btn-record'); await sleep(2000);
    return br;
  };
  const brN = await brOf('normal'), brH = await brOf('high');
  check('동영상 화질: 고화질이 일반보다 높은 비트레이트로 녹화', brH > brN * 2 && brN > 1e6, `일반 ${(brN / 1e6).toFixed(1)}Mbps · 고화질 ${(brH / 1e6).toFixed(1)}Mbps`);

  // 5-2) 설정 → 컴퓨터 사양: 최소·권장 표와 화질 모드 설명이 뜨고 '이 PC' 줄이 채워진다
  await page.click('#btn-settings'); await page.click('#btn-specs'); await sleep(300);
  const specs = await page.evaluate(() => ({ open: document.getElementById('specs-dialog').open,
    text: document.getElementById('specs-dialog').innerText, pc: document.getElementById('specs-this-pc').textContent }));
  check('설정 → 컴퓨터 사양 설명이 열리고 내용이 있음', specs.open && specs.text.includes('권장 사양') && specs.text.includes('고화질') && specs.pc.length > 0, specs.pc);
  await page.click('#specs-x'); await page.click('#settings-x'); await sleep(200);

  // 6) 녹화 중 카메라 끊김 (USB 빠짐)
  const v0 = (await caps(A.id)).filter(c => c.media_type === 'video').length;
  await page.click('#btn-record'); await sleep(1200);
  await page.evaluate(() => window.SlitApp.state.stream.getVideoTracks().forEach(t => { t.stop(); t.dispatchEvent(new Event('ended')); }));
  await sleep(3000);
  const ui = await page.evaluate(() => ({ rec: window.SlitApp.state.isRecording, badge: document.getElementById('rec-badge').classList.contains('on'),
                                          recState: window.SlitApp.state.recorder && window.SlitApp.state.recorder.state }));
  check('카메라 끊김 → 화면이 계속 "녹화 중"으로 보이지 않음', !ui.rec && !ui.badge, JSON.stringify(ui));
  check('카메라 끊김 → 그때까지 녹화분 저장', (await caps(A.id)).filter(c => c.media_type === 'video').length === v0 + 1);
  if (ui.rec) await page.click('#btn-record').catch(() => {});
  await page.evaluate(() => window.SlitApp.startCamera()); await sleep(2000);

  // 7) 녹화 중 새로고침/창 닫기 → 경고가 떠야 함
  await page.click('#btn-record'); await sleep(800);
  let dialog = null;
  page.once('dialog', async (d) => { dialog = d.type(); await d.dismiss(); });
  await page.reload({ timeout: 4000 }).catch(() => {});
  await sleep(500);
  check('녹화 중 새로고침 시 경고(beforeunload)', dialog === 'beforeunload', `dialog=${dialog}`);

  // 8) 업로드 중 새로고침 → 경고
  await page.click('#btn-record').catch(() => {}); await sleep(2500);   // 녹화 끝내고 저장까지 기다림
  await page.goto(BASE + '/').catch(() => {});
  await ready();
  await select(A.id); await sleep(300);
  await page.route('**/api/v1/captures', async (r) => { if (r.request().method() === 'POST') { await sleep(3000); return r.continue(); } return r.continue(); });
  await page.click('#btn-capture'); await sleep(300);
  dialog = null;
  page.once('dialog', async (d) => { dialog = d.type(); await d.dismiss(); });
  await page.reload({ timeout: 4000 }).catch(() => {});
  await sleep(500);
  check('저장 중 새로고침 시 경고(beforeunload)', dialog === 'beforeunload', `dialog=${dialog}`);

  await sleep(4000);
  // 9) 카메라 끊긴 뒤 촬영 버튼으로 멈춘 화면이 저장되지 않음
  await page.goto(BASE + '/').catch(() => {});
  await ready();
  await select(A.id); await sleep(300);
  check('동영상 화질 선택이 새로고침 뒤에도 유지', (await page.inputValue('#vq-select')) === 'high');
  const n0 = (await caps(A.id)).length;
  await page.evaluate(() => window.SlitApp.state.stream.getVideoTracks().forEach(t => { t.stop(); t.dispatchEvent(new Event('ended')); }));
  await sleep(300);
  const disabled = await page.evaluate(() => document.getElementById('btn-capture').disabled);
  await page.click('#btn-capture', { force: true, timeout: 2000 }).catch(() => {}); await sleep(1500);
  check('카메라 끊긴 뒤에는 촬영 버튼이 꺼지고 멈춘 화면이 저장되지 않음', disabled && (await caps(A.id)).length === n0);

  await browser.close(); srv.kill();
  fs.rmSync(DATA, { recursive: true, force: true });
  const failed = results.filter(r => !r.ok).length;
  console.log(`\n${results.length - failed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });

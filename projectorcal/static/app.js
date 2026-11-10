'use strict';
/* ProjectorCal — wizard UI. No external libraries (works offline). */

// ============================================================ utilities
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const isNum = v => typeof v === 'number' && isFinite(v);
const fmt = (v, d = 2) => isNum(v) ? v.toFixed(d) : '—';
const fmtS = (v, d = 1) => isNum(v) ? (v > 0 ? '+' : '') + v.toFixed(d) : '—';
function fmtY(v) {
  if (!isNum(v)) return '—';
  const a = Math.abs(v);
  return v.toFixed(a >= 100 ? 1 : a >= 10 ? 2 : a >= 1 ? 3 : 4);
}
const lvCls = lv => lv === 'good' ? 'good' : lv === 'ok' ? 'ok' : lv === 'bad' ? 'bad' : '';
const VERDICT = { good: ['good', 'OK'], ok: ['ok', 'within tolerance'], bad: ['bad', 'needs work'] };
const verdictPill = v => v ? `<span class="pill ${VERDICT[v][0]}">${VERDICT[v][1]}</span>` : '<span class="pill">no data</span>';

const S = {
  state: null, sv: null, cat: null, rev: -1, stepSig: '', fails: 0, pending: false, localNotices: [],
  ui: { chartLog: {}, check: null, checking: false, exportResult: null, settings: false, log: null, mockSig: '' },
};

async function api(path, body) {
  const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let j = {};
  try { j = await r.json(); } catch (e) { /* empty response */ }
  if (!r.ok) throw new Error(j.error || ('Error ' + r.status));
  return j;
}
async function act(path, body) {
  try { await api(path, body); } catch (e) { localNotice('bad', e.message); }
  await poll(true);
}
function localNotice(level, text) {
  const id = 'l' + Math.random().toString(36).slice(2);
  S.localNotices.push({ id, level, text, local: true, t: Date.now() / 1000 });
  renderNotices();
  setTimeout(() => { S.localNotices = S.localNotices.filter(n => n.id !== id); renderNotices(); }, 7000);
}
const P = id => (S.cat && S.cat[id]) || { id, name: id, filename: '' };
const sess = () => S.sv.session;
const an = id => S.sv.analysis[id];

// ============================================================ steps
const STEPS = [
  { id: 'prep', title: 'Preparation' },
  { id: 'full', title: 'Full check (auto)' },
  { id: 'black', title: 'Black and contrast' },
  { id: 'laser', title: 'Laser level' },
  { id: 'gray', title: 'Gray scale (balance)' },
  { id: 'gpoints', title: 'Gamma by points' },
  { id: 'color', title: 'Colors (CMS)' },
  { id: 'verify', title: 'SDR verification' },
  { id: 'eyes', title: 'Visual check' },
  { id: 'hdr', title: 'HDR10' },
  { id: 'dv', title: 'Dolby Vision' },
  { id: 'summary', title: 'Summary and report' },
];
const ICON = { todo: '○', active: '◐', done: '●', skipped: '–', recheck: '⚠' };
const STATUS_NAME = { todo: 'not started', active: 'in progress', done: 'done', skipped: 'skipped', recheck: 'recheck' };
const stepIdx = id => STEPS.findIndex(s => s.id === id);

const PREP_CHECK = [
  ['warm', 'Projector warmed up ≥ 45 minutes (any patch playing, gray 50% is best)'],
  ['mode', 'Picture mode Filmmaker; Brightness mode = Custom; EOTF = Auto'],
  ['dyn', 'Off: Dynamic Contrast, Temperature Adaptation, Comfort Vision / Eye protection'],
  ['memc', 'MEMC off (Movie mode is acceptable)'],
  ['ai', 'All AI features off (AI SR, AI Contrast, AI Color, AI Depth)'],
  ['geo', 'Position, zoom, focus, keystone fixed (do not change after calibration)'],
  ['box', 'Player / streaming box: output as for movies (4K, HDR/DV on), picture enhancers off'],
  ['room', 'Room lights off, curtains closed'],
  ['meter', 'Meter mounted facing the screen, diffuser moved away from the optics, meter plugged directly into USB'],
  ['profiler', 'Calibrite PROFILER was run once with this meter (unlocks the HL for Argyll; Display Plus HL only)'],
];
const BLACK_CHECK = [
  ['pluge', 'PLUGE (SDR_52): bars 1–3 invisible, 5 barely visible, 6–7 visible → Black Level control'],
  ['clip', 'Clipping (SDR_53): bars 5, 6, 7 (231/233/235) are distinguishable; 7–9 may merge → Contrast control'],
  ['pluge2', 'PLUGE rechecked after Contrast'],
];
const EYES_CHECK = [
  ['gray', 'Gray scale SDR_01…21: the gray looks neutral on every step (compare with a calibrated monitor if you have one)'],
  ['skin', 'Skin tones look natural on familiar material'],
  ['meta', 'Do not compare saturation with a monitor (metamerism: laser ≠ LCD)'],
  ['fix', 'If needed — small Hue/Sat tweaks of red and yellow (±1–2 steps)'],
  ['recheck', 'After the tweaks — a quick gray recheck (“Summary” step → recheck 100/50/20/10)'],
];
const DV_CHECK = [
  ['open', 'Open a Dolby Vision movie (Netflix / Apple TV+ / Plex)'],
  ['mode', 'Check which mode the projector switched to: Dolby Vision Dark / Bright (Dark is usual in a dark room)'],
  ['wb', 'If DV has its own white balance — enter the same Gain/Offset as in HDR10'],
  ['gray', 'Check gray and shadows by eye on familiar scenes'],
  ['fallback', 'If DV looks worse than HDR10 — disable Dolby Vision on the player and watch HDR10'],
];

// ============================================================ server polling
async function poll(force) {
  try {
    const st = await api('/api/state');
    S.state = st;
    S.fails = 0;
    setOffline(false);
    if (!S.cat) S.cat = (await api('/api/catalog')).patches;
    if (force || st.rev !== S.rev || !S.sv) {
      S.sv = await api('/api/session');
      S.rev = S.sv.rev;
      renderAll();
    } else {
      renderDynamic();
    }
  } catch (e) {
    S.fails++;
    if (S.fails >= 3) setOffline(true);
  }
}
async function loop() {
  await poll(false);
  setTimeout(loop, 600);
}
function setOffline(on) {
  let el = $('#offline');
  if (on && !el) {
    el = document.createElement('div');
    el.id = 'offline'; el.className = 'offline';
    el.innerHTML = 'ProjectorCal is not responding.<br><span class="small">The program is closed or restarting. Start ProjectorCal again — the session is saved.</span>';
    document.body.appendChild(el);
  } else if (!on && el) el.remove();
}

// ============================================================ rendering: common frame
function applyTheme() {
  const t = (S.sv && S.sv.settings.theme) || 'dark';
  let th = t;
  if (t === 'auto') th = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  document.documentElement.dataset.theme = th;
}
function renderAll() {
  applyTheme();
  if (location.hash === '#report') { renderReport(); return; }
  $('#app').hidden = false; $('#report').hidden = true;
  renderHeader(); renderNav(); renderStep(true); renderMeter(); renderNotices(); renderModal();
}
function renderDynamic() {
  if (location.hash === '#report') return;
  renderHeader(); renderMeter(); renderNotices(); renderModal();
  renderStep(false);
  updateLive();
}
function editing() {
  const a = document.activeElement;
  return a && $('#center').contains(a) && (a.tagName === 'TEXTAREA' || a.tagName === 'SELECT' ||
    (a.tagName === 'INPUT' && ['text', 'number', 'search'].includes(a.type)));
}

function renderHeader() {
  const h = S.state.header, m = S.state.meter, s = sess();
  const mtxt = meterStatusText(m);
  const html = `
    <span class="brand">ProjectorCal</span>
    <span class="muted small">${esc(s.name)}</span>
    <span class="kv"><span class="muted">Laser</span><b>${esc(h.laserLevel || '—')}</b></span>
    <span class="kv"><span class="muted">White</span><b>${h.whiteY ? fmt(h.whiteY, 1) + ' nits' : '—'}</b></span>
    <span class="kv"><span class="muted">Gamma</span><b>${h.gamma === 'bt1886' ? 'BT.1886' : esc(h.gamma)}</b></span>
    <span class="kv"><span class="dot ${m.state}${m.busy ? ' busy' : ''}"></span><span>${esc(mtxt)}</span></span>
    <span class="spacer"></span>
    <button class="btn" data-act="report">Report</button>
    <button class="btn" data-act="settings">Settings</button>`;
  setHTML($('#hdr'), html);
}
function setHTML(el, html) { if (el._html !== html) { el.innerHTML = html; el._html = html; } }

function meterStatusText(m) {
  const mock = m.mode === 'mock' ? ' (simulation)' : '';
  switch (m.state) {
    case 'ready': return 'Meter ready' + mock;
    case 'starting': return 'Connecting…';
    case 'calibration': return 'Meter calibration needed';
    case 'error': return 'Meter: error';
    default: return 'Meter not connected';
  }
}

function renderNav() {
  const s = sess(), cur = s.currentStep;
  const items = STEPS.map((st, i) => {
    const status = s.steps[st.id].status;
    return `<div class="st ${status}${st.id === cur ? ' cur' : ''}" data-act="goto" data-arg="${st.id}" title="${STATUS_NAME[status]}">
      <span class="n">${i}</span><span class="ic">${ICON[status]}</span><span>${st.title}</span></div>`;
  }).join('');
  setHTML($('#nav'), items + `<div class="foot small muted">○ not started · ◐ in progress · ● done · ⚠ recheck</div>`);
}

function renderNotices() {
  const all = (S.state ? S.state.notices : []).concat(S.localNotices).slice(-5);
  const html = all.map(n => `<div class="notice ${n.level}">${esc(n.text)}<span class="x" data-act="dismissNotice" data-arg="${n.id}"${n.local ? ' data-local="1"' : ''}>✕</span></div>`).join('');
  setHTML($('#notices'), html);
}

function renderModal() {
  const m = S.state && S.state.meter;
  let html = '';
  if (m && m.state === 'calibration') {
    html = `<div class="modal-bg"><div class="modal">
      <h2>The meter asks for the calibration position</h2>
      <p>Cover the meter optics (turn the diffuser lever over the lens or put the cap on), then press “Continue”.</p>
      <p class="muted">After calibration put the diffuser back in the measuring position (away from the optics) and do not move the meter.</p>
      <div class="row"><button class="btn primary big" data-act="calibContinue">Continue</button></div></div></div>`;
  } else if (S.ui.settings) {
    html = settingsModal();
  } else if (S.ui.log) {
    html = `<div class="modal-bg" data-act="closeLog"><div class="modal" data-stop="1">
      <h2>Meter log (spotread)</h2><p class="small muted mono">${esc(S.ui.log.file)}</p>
      <div class="pre">${esc(S.ui.log.lines.join('\n')) || 'empty'}</div>
      <div class="row" style="margin-top:12px"><a class="btn" href="/export/spotread.log">Download log</a>
      <button class="btn" data-act="closeLog">Close</button></div></div></div>`;
  }
  const el = $('#modal');
  if (el._html !== html) {
    // do not re-render the settings window while the user is typing in it
    if (S.ui.settings && el._html && el._html.includes('id="settingsForm"') && html.includes('id="settingsForm"')) return;
    el.innerHTML = html; el._html = html;
  }
}

// ============================================================ meter panel
function renderMeter() {
  const el = $('#meter');
  if (!el._built) {
    el.innerHTML = `<div class="mp">
      <div class="head"><h2 style="margin:0">Meter</h2><span class="small" id="mpStatus"></span></div>
      <div id="mpErr" class="errbox" hidden></div>
      <div class="Y" id="mpY">—<small> nits</small></div>
      <div class="small muted" id="mpYsub"></div>
      <div class="kvs small">
        <span>x, y</span><span id="mpXY">—</span>
        <span>CCT</span><span id="mpCCT">—</span>
        <span>Δu′v′ to D65</span><span id="mpDuv">—</span>
        <span>Y spread</span><span id="mpSd">—</span>
      </div>
      <div class="bars" id="mpBars">${barsSkeleton('mp')}</div>
      <div class="row" style="margin-top:12px">
        <span id="mpStab" class="pill">—</span>
      </div>
      <div class="row" style="margin-top:12px">
        <button class="btn primary" id="mpMeasure" data-act="measureNow">Measure now</button>
        <button class="btn" id="mpLive" data-act="freeLive">Live</button>
      </div>
      <p class="small muted" id="mpHint"></p>
      <div id="mpMock"></div>
      <div class="row small" style="margin-top:14px"><a href="#" data-act="showLog">Meter log</a></div>
    </div>`;
    el._built = true;
  }
  const st = S.state, m = st.meter, r = st.reading;
  $('#mpStatus').innerHTML = `<span class="dot ${m.state}${m.busy ? ' busy' : ''}"></span>${esc(meterStatusText(m))}`;
  const err = $('#mpErr');
  if (m.state === 'error' && m.error) {
    const html = `<b>${esc(m.error.message)}</b>
      <div class="row" style="margin-top:8px"><button class="btn" data-act="retry">Retry</button>
      <span class="small muted">${m.retryIn !== null ? 'auto-retry in ' + m.retryIn + ' s' : ''}</span></div>
      ${m.error.detail ? `<details><summary class="small">spotread output</summary><pre>${esc(m.error.detail)}</pre></details>` : ''}`;
    if (err._html !== html.replace(/auto-retry in \d+ s/, '')) { err.innerHTML = html; err._html = html.replace(/auto-retry in \d+ s/, ''); }
    else { const sp = err.querySelector('.small.muted'); if (sp) sp.textContent = m.retryIn !== null ? 'auto-retry in ' + m.retryIn + ' s' : ''; }
    err.hidden = false;
  } else if (m.state === 'off' || (m.state === 'starting' && !r)) {
    err.hidden = false;
    const html = m.state === 'starting' ? 'Connecting the meter…' : `Meter not connected. <button class="btn" data-act="retry">Connect</button>`;
    if (err._html !== html) { err.innerHTML = html; err._html = html; }
  } else err.hidden = true;

  const d = r ? (r.mean || r.last) : null;
  $('#mpY').innerHTML = d ? `${fmtY(d.Y)}<small> nits</small>` : '—<small> nits</small>';
  $('#mpYsub').textContent = r ? `last ${fmtY(r.last.Y)} · mean of ${r.n || 1} · ${r.age < 2 ? 'now' : r.age.toFixed(0) + ' s ago'}` : 'no readings yet';
  $('#mpXY').textContent = d ? `${fmt(d.x, 4)}, ${fmt(d.y, 4)}` : '—';
  $('#mpCCT').textContent = d && d.cct ? `${d.cct.toFixed(0)} K` : '—';
  $('#mpDuv').textContent = d ? fmtS(d.duv, 4) : '—';
  $('#mpSd').textContent = r && isNum(r.sd) ? `±${r.sd.toFixed(2)}%` : '—';
  const neutral = d && Math.hypot(d.x - 0.3127, d.y - 0.3290) < 0.05;
  updateBars('mp', neutral ? d.bal : null);
  $('#mpBars').title = d && !neutral ? 'RGB balance is computed for gray only' : '';
  $('#mpBars').style.opacity = d && !neutral ? 0.35 : 1;
  const stab = $('#mpStab');
  if (r && r.n >= 2) { stab.className = 'pill ' + (r.stable ? 'good' : 'ok'); stab.textContent = r.stable ? '● stable' : '○ changing'; }
  else { stab.className = 'pill'; stab.textContent = '—'; }
  const a = st.activity;
  const busyJob = m.jobs > 0;
  const mb = $('#mpMeasure');
  mb.disabled = m.state !== 'ready' || busyJob;
  mb.textContent = busyJob ? 'Measuring…' : (a && a.type === 'pass' ? 'Measure and record' : st.activity && st.activity.type === 'live' ? 'Single reading' : 'Measure now');
  const lb = $('#mpLive');
  lb.hidden = !!(a && a.type !== 'free');
  lb.textContent = a && a.type === 'free' ? 'Stop live' : 'Live';
  lb.classList.toggle('on', !!(a && a.type === 'free'));
  $('#mpHint').textContent = a && a.type === 'live' ? 'Live: continuous readings, the mean of the latest ones is shown.' :
    a && a.type === 'pass' && a.auto ? 'Auto capture: the program detects the patch change and stability by itself.' : '';
  renderMock();
}

function barsSkeleton(p, big) {
  return ['R', 'G', 'B'].map(c => `<div class="bar"><span class="ch" style="color:var(--${c.toLowerCase()})">${c}</span>
    <div class="track"><div class="zone2" style="left:25%;width:50%"></div><div class="zone" style="left:40%;width:20%"></div>
    <div class="center" style="left:50%"></div><div class="mark" id="${p}-${c}-m" style="left:50%;background:var(--${c.toLowerCase()});opacity:0"></div></div>
    <span class="val" id="${p}-${c}-v">—</span></div>`).join('');
}
function updateBars(p, bal) {
  ['R', 'G', 'B'].forEach((c, i) => {
    const m = document.getElementById(`${p}-${c}-m`), v = document.getElementById(`${p}-${c}-v`);
    if (!m) return;
    if (!bal || !isNum(bal[i])) { m.style.opacity = 0; v.textContent = '—'; v.className = 'val'; return; }
    const b = bal[i];
    const pos = Math.max(0, Math.min(100, 50 + (b - 100) * 5)); // scale 90…110
    m.style.opacity = 1; m.style.left = pos + '%';
    const lv = Math.abs(b - 100) <= 2 ? 'good' : Math.abs(b - 100) <= 5 ? 'ok' : 'bad';
    const arrow = b < 98 ? '<span class="arrow">↑</span>' : b > 102 ? '<span class="arrow">↓</span>' : '';
    v.innerHTML = b.toFixed(1) + arrow; v.className = 'val ' + lv;
  });
}

function renderMock() {
  const el = $('#mpMock');
  const mk = S.state.mock;
  if (!mk) { if (el._html) { el.innerHTML = ''; el._html = ''; } return; }
  const sig = 'v1';
  if (el._html !== sig) {
    const sl = (k, i, lab) => `<label class="row small" style="gap:6px"><span style="width:62px">${lab}</span>
      <input type="range" min="-40" max="40" step="1" data-mock="${k}" data-i="${i}" style="flex:1"><span class="mono" id="mk-${k}-${i}" style="width:28px;text-align:right"></span></label>`;
    el.innerHTML = `<div class="card flat" style="margin-top:14px"><h3>Projector simulation</h3>
      <p class="small muted">“Projector menu” controls for testing the program without hardware.</p>
      <label class="chk small"><input type="checkbox" data-mock="follow"> The “user” plays the right patch by themselves</label>
      <label class="row small">On screen: <select data-mock="displayed" id="mk-disp" style="flex:1"></select></label>
      <div style="margin-top:8px">${sl('gain', 0, 'Gain R')}${sl('gain', 1, 'Gain G')}${sl('gain', 2, 'Gain B')}
      ${sl('offset', 0, 'Offset R')}${sl('offset', 1, 'Offset G')}${sl('offset', 2, 'Offset B')}</div>
      <label class="row small">Projector gamma <input type="number" step="0.05" min="1.8" max="2.8" data-mock="gamma" style="width:80px"></label>
      <label class="row small">White, nits <input type="number" step="5" min="5" max="2000" data-mock="whiteNits" style="width:80px"></label></div>`;
    el._html = sig;
    const sel = $('#mk-disp');
    sel.innerHTML = Object.values(S.cat).sort((a, b) => a.mode.localeCompare(b.mode) * -1 || a.order - b.order)
      .map(p => `<option value="${p.id}">${p.id} · ${esc(p.name)}</option>`).join('') + '<option value="PLEX_MENU">player menu</option>';
  }
  const a = document.activeElement;
  el.querySelectorAll('[data-mock]').forEach(inp => {
    if (inp === a) return;
    const k = inp.dataset.mock;
    if (k === 'follow') inp.checked = mk.follow;
    else if (k === 'displayed') { inp.value = mk.displayed; inp.disabled = mk.follow; }
    else if (k === 'gain' || k === 'offset') { const i = +inp.dataset.i; inp.value = mk[k][i]; $(`#mk-${k}-${i}`).textContent = mk[k][i]; }
    else if (k === 'gamma') inp.value = mk.gamma;
    else if (k === 'whiteNits') inp.value = mk.whiteNits;
  });
}

// ============================================================ center: the step
function stepSignature() {
  const st = S.state, p = st.pass, l = st.live;
  const au = st.auto;
  return JSON.stringify([S.rev, au && [au.state, au.patchId, Math.floor((au.fileTime || 0) / 5)], S.ui.fullTab, S.ui.psOpen, st.currentStep, st.activity && st.activity.type, st.activity && st.activity.patchId, st.meter.job, p && [p.idx, p.auto, p.pass, p.state, p.wrong && p.wrong.text, p.list.length],
    l && [l.patchId, !!l.suspect], st.drift, st.meter.state === 'ready', st.meter.jobs > 0, S.ui.check, S.ui.checking, S.ui.exportResult, S.ui.chartLog]);
}
function renderStep(force) {
  const sig = stepSignature();
  if (!force && sig === S.stepSig) return;
  if (editing()) { S.pending = true; return; }
  S.stepSig = sig;
  S.pending = false;
  const id = S.state.currentStep;
  const views = { prep: viewPrep, full: viewFull, black: viewBlack, laser: viewLaser, gray: viewGray, gpoints: viewGpoints, color: viewColor,
    verify: viewVerify, eyes: viewEyes, hdr: viewHDR, dv: viewDV, summary: viewSummary };
  const i = stepIdx(id);
  const s = sess().steps[id];
  const top = `<div class="steptitle"><span class="num">${i}</span><h1 style="margin:0">${STEPS[i].title}</h1>
    <span class="pill ${s.status === 'done' ? 'good' : s.status === 'recheck' ? 'ok' : s.status === 'active' ? 'info' : ''}">${STATUS_NAME[s.status]}</span></div>`;
  const center = $('#center');
  const scroll = center.scrollTop;
  center.innerHTML = driftBanner() + top + views[id](s) + stepNav(id);
  center.scrollTop = scroll;
  updateLive();
  if (id === 'prep' && !S.ui.check && !S.ui.checking && S.sv.settings.meterMode !== 'mock') runCheck();
}
function stepNav(id) {
  const i = stepIdx(id);
  const prev = i > 0 ? `<button class="btn" data-act="goto" data-arg="${STEPS[i - 1].id}">← ${STEPS[i - 1].title}</button>` : '';
  const next = i < STEPS.length - 1 ? STEPS[i + 1] : null;
  return `<div class="stepnav">${prev}<span style="flex:1"></span>
    ${next ? `<button class="btn" data-act="stepSkip" data-arg="${id}">Skip step</button>` : ''}
    <button class="btn primary big" data-act="stepDone" data-arg="${id}">${next ? 'Step done → ' + next.title : 'Done'}</button></div>`;
}
function driftBanner() {
  const d = S.state.drift;
  if (!d) return '';
  return `<div class="banner"><b>Brightness changed by ${fmtS(d.pct, 1)}%</b> (white ${fmtY(d.Y)} nits, fixed at ${fmtY(d.whiteY)} nits).
    The white balance may have shifted. Restore the laser level or re-measure.
    <div class="row" style="margin-top:8px"><button class="btn" data-act="goto" data-arg="laser">Go to “Laser level”</button>
    <button class="btn" data-act="acceptWhite">Accept the new white</button><button class="btn" data-act="dismissDrift">Hide</button></div></div>`;
}
function checklist(stepId, items) {
  const cl = sess().steps[stepId].checklist || {};
  return items.map(([k, t]) => `<label class="chk"><input type="checkbox" data-check="${stepId}" data-key="${k}" ${cl[k] ? 'checked' : ''}><span>${t}</span></label>`).join('');
}
function nowBox(pid, extra, cls) {
  const p = P(pid);
  return `<div class="now ${cls || ''}"><div class="lbl">Play on the player (Plex / USB)</div>
    <div class="what"><span class="pid">${esc(pid)}</span> — ${esc(p.name)}</div>
    <div class="file">${esc(p.filename)}</div>${extra || ''}</div>`;
}
function menuInputs(mode, keys) {
  const mv = sess().menuValues[mode];
  return `<div class="menuvals">${keys.map(k => `<label><span>${esc(k)}</span><input type="text" data-menu="${mode}" data-key="${esc(k)}" value="${esc(mv[k] || '')}"></label>`).join('')}</div>`;
}
function notes(stepId) {
  return `<details><summary>Step notes</summary><textarea data-notes="${stepId}" placeholder="What you changed, observations…">${esc(sess().steps[stepId].notes)}</textarea></details>`;
}

// ------------------------------------------------------------ 0. Preparation
function viewPrep() {
  const ck = S.ui.check, mock = S.sv.settings.meterMode === 'mock';
  let chk;
  if (mock) chk = '<p><b>Meter simulation</b> is on (Settings → Meter). For a real calibration switch to “ArgyllCMS spotread”.</p>';
  else if (S.ui.checking) chk = '<p class="muted">Checking spotread and the meter… (up to 20 seconds)</p>';
  else if (!ck) chk = '';
  else if (!ck.found) chk = `<p class="bad"><b>spotread not found.</b> Install ArgyllCMS in Terminal:</p>
      ${codeBox('brew install argyll-cms')}
      <p class="small muted">No Homebrew? Install it first from <span class="mono">brew.sh</span>. After installing press “Check again”.</p>`;
  else chk = `<p><span class="good">✓ spotread found</span> <span class="mono small muted">${esc(ck.exe)}</span>${ck.version ? ` · ArgyllCMS ${esc(ck.version)}` : ''}</p>
      ${ck.instruments.length ? `<p><span class="good">✓ Meter detected:</span> ${ck.instruments.map(esc).join('<br>')}</p>` :
      `<p class="ok">No meter in the spotread list. Plug the meter directly into USB (no hub) and press “Check again”. For a Calibrite Display Plus HL: if that does not help, run Calibrite PROFILER once and close it.</p>`}
      <details><summary class="small">spotread -? output</summary><div class="pre">${esc(ck.raw)}</div></details>`;
  const ex = S.ui.exportResult;
  const testOk = (sess().steps.prep.checklist || {})._testOk;
  return `
  <div class="now"><div class="lbl">Now</div><div class="what" style="font-size:22px">Go through the checklist, mount the meter and take a test reading</div></div>
  <div class="grid2">
   <div class="card"><h2>1. Program and meter</h2>${chk}
     <div class="row"><button class="btn" data-act="runCheck" ${S.ui.checking || mock ? 'disabled' : ''}>Check again</button></div>
     <h3 style="margin-top:14px">Test reading</h3>
     <p class="small muted">Play SDR_01 (white) on the player and press — the result appears on the right and in a notification.</p>
     <div class="row"><button class="btn primary" data-act="measureTest" ${S.state.meter.state !== 'ready' ? 'disabled' : ''}>Test reading</button>
     ${testOk ? '<span class="good">✓ reading received</span>' : ''}</div>
     <h3 style="margin-top:14px">Room light</h3>
     <p class="small muted">Cover the projector lens (with the cap or a thick cloth, without blocking the meter) and press. This shows how much room light is added to black.</p>
     <div class="row"><button class="btn" data-act="measureAmbient" ${S.state.meter.state !== 'ready' ? 'disabled' : ''}>Measure room light</button>
     ${isNum(sess().ambientY) ? `<span class="${sess().ambientY < 0.003 ? 'good' : 'ok'}">${fmtY(sess().ambientY)} nits</span>` : ''}</div>
   </div>
   <div class="card"><h2>2. Patches: Plex or USB stick</h2>
     <p>74 video patches (SDR and HDR10, 4K HEVC 10-bit, limited range) plus two auto-run files. Copy the folder to your Plex server or to a USB stick.</p>
     <div class="row"><input type="text" id="exportDest" value="${esc(S.ui.exportDest || '~/Desktop/ProjectorCal Patches')}" style="flex:1;min-width:200px">
     <button class="btn primary" data-act="exportPatches">Save patches…</button></div>
     ${ex ? `<p class="${ex.error ? 'bad' : 'good'} small">${ex.error ? esc(ex.error) : `✓ Saved ${ex.files} files to <span class="mono">${esc(ex.dest)}</span>`}</p>` : ''}
     <details><summary>How to play them</summary><ol class="small">
     <li><b>Plex:</b> copy the folder to the Plex server (e.g. <span class="mono">/srv/media/Calibration/</span>), Plex → Add library → <b>Other Videos</b> → the folder → Scan.</li>
     <li><b>USB stick:</b> copy the folder to an exFAT-formatted stick, plug it into the projector and open the files in its built-in media player.</li>
     <li>The number at the start of each name is the order.</li>
     <li>Each file is a still frame lasting 10 minutes: just play it. If the player bar is over the picture, wait for it to disappear.</li></ol></details>
   </div>
  </div>
  <div class="grid2">
   <div class="card"><h2>3. Projector and player</h2>${checklist('prep', PREP_CHECK)}</div>
   <div class="card"><h2>4. Meter placement</h2>${setupSVG()}
     <ul class="small"><li>Use the meter's built-in stand (on a Display Plus HL: lift the diffuser lever and turn it half a turn back) or a tripod (1/4″ thread).</li>
     <li>The optics face the center of the screen, 0.7–1.5 m away.</li>
     <li>The meter sits below the beam: its shadow falls on the bottom edge of the picture, not the center.</li>
     <li>The diffuser does not cover the optics. Do not move the meter after mounting it.</li>
     <li>The projector menu must not cover the center of the screen during readings.</li></ul></div>
  </div>
  ${notes('prep')}`;
}
function codeBox(cmd) {
  return `<div class="codebox"><code>${esc(cmd)}</code><button class="btn" data-act="copy" data-arg="${esc(cmd)}">Copy</button></div>`;
}
function setupSVG() {
  return `<svg class="chart" viewBox="0 0 420 170" role="img" aria-label="Meter placement diagram">
    <rect x="330" y="10" width="10" height="150" fill="var(--muted)" opacity=".5"/>
    <text x="300" y="168">screen</text>
    <rect x="20" y="22" width="56" height="30" rx="5" fill="var(--panel2)" stroke="var(--muted)"/><text x="22" y="16">projector</text>
    <path d="M76 30 L330 12 M76 44 L330 158" stroke="var(--accent)" stroke-dasharray="4 4" opacity=".7"/>
    <circle cx="180" cy="120" r="13" fill="var(--panel2)" stroke="var(--text)"/><path d="M193 120 L330 85" stroke="var(--good)" stroke-width="2"/>
    <rect x="172" y="133" width="16" height="22" fill="var(--muted)" opacity=".6"/><text x="120" y="166">meter (below the beam)</text>
    <circle cx="330" cy="85" r="4" fill="var(--good)"/><text x="345" y="88">center</text>
    <text x="200" y="110">0.7–1.5 m</text></svg>`;
}

// ------------------------------------------------------------ 1. Black and contrast
function viewBlack(s) {
  const a = an('black'), r = a.summary.ratio95;
  let ratioTxt = '<span class="muted">not measured</span>';
  if (isNum(r)) ratioTxt = `<b>${r.toFixed(3)}</b> — ${r >= 0.97 ? '<span class="bad">white is clipped (95% almost equals 100%): lower Contrast</span>' :
    r < 0.80 ? '<span class="ok">95% is noticeably darker than expected — check Contrast/gamma</span>' : '<span class="good">normal (target ≈ 0.88 at γ 2.4)</span>'}`;
  return `
  ${nowBox('SDR_52', '<div class="small" style="margin-top:6px">Then SDR_53 (white clipping), then SDR_52 again.</div>')}
  <div class="grid2">
   <div class="card"><h2>PLUGE — Black Level</h2>${plugeSVG()}
    <p class="small">7 bars on a black background. Bars 1–3 are “below black” — <b>invisible</b>; 4 equals the background; 5 is barely visible; 6–7 are visible.</p></div>
   <div class="card"><h2>Clipping — Contrast</h2>${clipSVG()}
    <p class="small">9 bright bars. 5, 6, 7 (231/233/235) must be distinguishable. 7–9 (235/240/250) may merge — that is “above white”. If 5–6 merge too, contrast is too high, lower it.</p></div>
  </div>
  <div class="card"><h2>Checklist</h2>${checklist('black', BLACK_CHECK)}
   ${menuInputs('sdr', ['Black Level', 'Contrast'])}</div>
  <div class="card"><h2>Meter check (optional)</h2>
   <p class="small">Ratio Y(95%) / Y(100%): ≈ 0.88 — normal; ≥ 0.97 — white is clipped. Measure SDR_01 and SDR_02.</p>
   <p>Y95/Y100: ${ratioTxt}</p>
   ${passBlock('black', a, { compact: true })}</div>
  ${notes('black')}`;
}
function plugeSVG() {
  const codes = [4, 8, 12, 16, 20, 24, 28];
  const bars = codes.map((c, i) => { const v = Math.max(16, c); const l = Math.round(10 + (v - 16) * 6); return `<rect x="${40 + i * 48}" y="15" width="44" height="70" fill="rgb(${l},${l},${l})" stroke="${i === 4 ? 'var(--accent)' : 'none'}"/><text x="${55 + i * 48}" y="100">${i + 1}</text>`; }).join('');
  return `<svg class="chart" viewBox="0 0 420 110"><rect width="420" height="90" fill="rgb(10,10,10)"/>${bars}</svg>`;
}
function clipSVG() {
  const codes = [200, 215, 225, 229, 231, 233, 235, 240, 250];
  const bars = codes.map((c, i) => { const l = Math.min(235, c) + 10; return `<rect x="${12 + i * 44}" y="15" width="40" height="70" fill="rgb(${l},${l},${l})" stroke="${i >= 4 && i <= 6 ? 'var(--accent)' : 'none'}" stroke-width="2"/><text x="${26 + i * 44}" y="100">${i + 1}</text>`; }).join('');
  return `<svg class="chart" viewBox="0 0 420 110"><rect width="420" height="90" fill="rgb(10,10,10)"/>${bars}</svg>`;
}

// ------------------------------------------------------------ 2. Laser level
function viewLaser() {
  const s = sess();
  return `
  ${nowBox('SDR_01', '<div class="small" style="margin-top:6px">White 100%. Readings run continuously (live).</div>')}
  <div class="grid2">
   <div class="card"><h2>White luminance now</h2>
    <div class="Y" style="font-size:56px;font-weight:600;color:var(--strong)" id="lvY">—</div>
    <p class="small muted" id="lvSub"></p>
    <p>For maximum brightness set the highest <b>Brightness level</b>. If your eyes get tired or the fan is noisy — lower it.
    No target in nits is set: all targets are relative to the measured white.</p>
    <p class="small muted">White balance depends on the laser level, so the level is chosen first and not changed afterwards.</p></div>
   <div class="card"><h2>Fix it</h2>
    <label class="row">Laser level (Brightness level): <input type="text" id="laserLevel" value="${esc(s.laserLevel || '')}" style="width:90px" placeholder="e.g. 10"></label>
    <div class="row" style="margin-top:12px"><button class="btn primary big" data-act="fixWhite" ${S.state.meter.state !== 'ready' ? 'disabled' : ''}>Fix the white</button></div>
    <p>${s.whiteY ? `Fixed: <b>${fmt(s.whiteY, 2)} nits</b>, level <b>${esc(s.laserLevel || '—')}</b> <span class="muted small">(${esc((s.whiteFixedAt || '').replace('T', ' ').slice(0, 16))})</span>` : '<span class="muted">Not fixed yet.</span>'}</p>
    <h3>SDR target gamma</h3>
    <div class="seg">${['2.2', '2.3', '2.4', 'bt1886'].map(g => `<button class="${s.gamma === g ? 'sel' : ''}" data-act="setGamma" data-arg="${g}">${g === 'bt1886' ? 'BT.1886' : g}</button>`).join('')}</div>
    <p class="small muted">2.4 — dark room (default). Compare on a dark scene: if shadows are crushed — 2.3. BT.1886 depends on the black measurement, which is unreliable with consumer colorimeters.</p></div>
  </div>${notes('laser')}`;
}

// ------------------------------------------------------------ 3. Gray scale (balance + gamma in one circle)
const CIRCLE_PIDS = ['SDR_01', 'SDR_05', 'SDR_09', 'SDR_13', 'SDR_17', 'SDR_19'];
function viewGray(s) {
  const a = an('gray'), plan = an('grayPlan'), hist = an('grayHistory');
  const act = S.state.activity;
  if (act && act.type === 'live' && act.stepId === 'gray') return grayLiveView(act.patchId);
  const lv = plan.level === 'good' ? 'done' : plan.level === 'bad' ? 'warn' : '';
  const planCard = `<div class="now ${lv}"><div class="lbl">Next action</div>
    <div class="what" style="font-size:24px">${esc(plan.title)}</div>
    <div style="font-size:17px;margin-top:4px">${esc(plan.text)}</div>
    <div class="row" style="margin-top:10px">
      ${plan.patchId ? `<button class="btn primary" data-act="grayLive" data-arg="${plan.patchId}">Adjust live on ${esc(plan.patchId)} (${esc(P(plan.patchId).name)})</button>` : ''}
      ${plan.goto ? `<button class="btn primary" data-act="goto" data-arg="${plan.goto}">Go to: Gamma by points →</button>` : ''}
      <span class="small muted">One menu change → a new circle. This shows what exactly helped and nothing breaks.</span></div></div>`;
  const histT = hist.length ? `<div class="card"><h2>Circles</h2><div class="tablewrap"><table class="t">
    <tr><th class="l">Circle</th><th>Time</th><th>Patches</th><th>Color ΔE mean</th><th>γ</th><th>Balance max</th><th class="l">Verdict</th></tr>
    ${hist.map(h => `<tr${h.pass === s.pass ? ' class="cur"' : ''}><td class="l">№${h.pass}</td><td>${esc(h.t)}</td><td>${h.n}</td><td>${fmt(h.deMean, 2)}</td><td>${fmt(h.gMean, 2)}</td><td>${isNum(h.balMax) ? fmt(h.balMax, 1) + '%' : '—'}</td><td class="l">${verdictPill(h.verdict)}</td></tr>`).join('')}
    </table></div><p class="small muted">If a new circle is worse than the previous one — revert the last menu change.</p></div>` : '';
  const full = `<label class="chk" style="padding:0"><input type="checkbox" data-opt="full" data-step="gray" ${s.full ? 'checked' : ''}> Full circle (21 steps)</label>`;
  return `${planCard}
  ${passBlock('gray', a, { extra: full })}
  ${histT}
  ${grayResults('gray', a)}
  <div class="card"><h2>Order and what affects what</h2><ol class="small">
   <li><b>Gain</b> (100%) mostly moves the bright steps, <b>Offset</b> (20%) the shadows, but both touch everything a bit (on the Vidda Offset is very coarse). So Gain first, then Offset, then a new circle.</li>
   <li><b>Gamma</b> (Gamma / Control Point) — only when the 100% and 20% balance is within tolerance: a gamma change moves step luminance and barely touches the balance.</li>
   <li>Each circle is 6 patches (100/80/60/40/20/10), about 2 minutes. 3–5 circles are usually enough.</li>
   <li>Below 10% is not used as a criterion: colorimeter noise and the bluish DLP black.</li>
   <li>Enter the values in the menu fields below — when you change them the program marks which steps to recheck.</li></ol></div>
  <div class="card"><h2>Gamma → Control Point / Gain</h2><ul class="small">
   <li><b>Control Point</b> picks a curve node (1…20 at 5% steps: 1 — 5%, 10 — 50%), <b>Gain</b> moves the luminance at that node: minus — darker, plus — brighter. Color (balance) barely changes.</li>
   <li>One step is about 2.2% of that step's luminance; neighboring steps are affected less. Steps near black (5–10%) are noisy, do not chase them.</li>
   <li>So: first pick the Gamma preset closest to the target, then let the program compute the points (the “Gamma by points” step or the full check).</li>
   <li>To map an unknown point: on a full circle (21 steps) move one point by −10 and do a new circle — the “Δ vs previous” column shows its zone. Revert or keep.</li>
   <li>Changing the Gamma preset or the picture mode may reset the points: after HDR and after any changes check in the menu that the point values are still there.</li></ul></div>
  <div class="card"><h2>Menu values (SDR)</h2>${menuInputs('sdr', ['Color Temp', 'Gain R', 'Gain G', 'Gain B', 'Offset R', 'Offset G', 'Offset B', 'Gamma', 'Gamma Control Points'])}
   <p class="small muted">“Gamma Control Points” — write all changed points, e.g. “1:−14, 3:−2”.</p></div>
  ${notes('gray')}`;
}

// ------------------------------------------------------------ 1. Full check (auto run of one video file)
const PS_SECTIONS = [
  ['Basics: mode, brightness, black, contrast, color', f => /^sdr\.(imageMode|laser|blackLevel|contrast|colorTemp|colorSpace|saturation|tint|gammaPreset|sharpness)$/.test(f[0])],
  ['SDR white balance (Gain — bright steps, Offset — shadows)', f => /^sdr\.(gain|offset)\./.test(f[0])],
  ['Gamma points (Gamma → Adjust → Control Point → Gain)', f => /^sdr\.gammaPoints\./.test(f[0])],
  ['CMS (Color Adjustment)', f => /^sdr\.cms\./.test(f[0])],
  ['Picture processing (best to turn everything off)', f => /^sdr\.proc\./.test(f[0])],
  ['Custom settings — anything not listed above (write “name = value”)', f => /^sdr\.(extra\.|other$)/.test(f[0])],
  ['HDR10', f => /^hdr\./.test(f[0])],
];
function psValue(path) {
  let cur = sess().psettings;
  for (const p of path.split('.')) { if (cur == null) return ''; cur = cur[p]; }
  return cur == null ? '' : cur;
}
function psForm() {
  const F = S.sv.psFields || [];
  return PS_SECTIONS.map(([title, test]) => {
    const fs = F.filter(test);
    if (title.startsWith('CMS')) {
      const cols = ['red', 'yellow', 'green', 'cyan', 'blue', 'magenta', 'skin'];
      const lab = { red: 'Red', yellow: 'Yellow', green: 'Green', cyan: 'Cyan', blue: 'Blue', magenta: 'Magenta (Pink)', skin: 'Skin' };
      return `<h3>${title}</h3><div class="tablewrap"><table class="t"><tr><th class="l">Color</th><th>Color tone</th><th>Saturation</th><th>Bright</th></tr>
        ${cols.map(c => `<tr><td class="l">${lab[c]}</td>${['tone', 'sat', 'bright'].map(f => `<td><input type="number" class="psv" data-ps="sdr.cms.${c}.${f}" value="${esc(psValue(`sdr.cms.${c}.${f}`))}" style="width:64px"></td>`).join('')}</tr>`).join('')}</table></div>`;
    }
    if (title.startsWith('Gamma points')) {
      return `<h3>${title}</h3><div class="tablewrap"><table class="t"><tr>${fs.map(f => `<th>${f[0].split('.').pop()}<br><span class="muted small">${+f[0].split('.').pop() * 5}%</span></th>`).join('')}</tr>
        <tr>${fs.map(f => `<td><input type="number" class="psv" data-ps="${f[0]}" value="${esc(psValue(f[0]))}" style="width:46px;padding:3px"></td>`).join('')}</tr></table></div>
        <p class="small muted">Point k = step k·5%: 1 → 5%, 2 → 10%, … 10 → 50%, … 20 → 100% (verified on a Vidda C3S). Minus — darker. Leave point 20 (white) alone.</p>`;
    }
    return `<h3>${title}</h3><div class="menuvals">${fs.map(f => `<label><span>${esc(f[1])}</span><input type="${f[3] === 'int' ? 'number' : 'text'}" class="psv" data-ps="${f[0]}" value="${esc(psValue(f[0]))}"></label>`).join('')}</div>`;
  }).join('');
}
function viewFull() {
  const F = an('full') || { runs: [], latest: {}, knowledge: [], sens: [], sequences: {} };
  const au = S.state.auto;
  const tab = S.ui.fullTab || (F.latest.sdr ? 'sdr' : F.latest.hdr ? 'hdr' : 'sdr');
  const ready = S.state.meter.state === 'ready';
  const mmss = t => `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, '0')}`;
  let runBox;
  if (au) {
    const p = au.patchId ? P(au.patchId) : null;
    runBox = au.state === 'arming' ? `<div class="now warn"><div class="lbl">Full check ${au.mode.toUpperCase()} · waiting for the start mark (${au.waited} s)</div>
        <div class="what" style="font-size:24px">Play <span class="pid">${esc(au.file)}</span> (Plex or USB)</div>
        <div style="font-size:16px">You can do it now. The file starts with 8 s of gray, 12 s of white and gray again — the program syncs on them. Do not touch the remote afterwards.</div>
        <div class="row" style="margin-top:10px"><button class="btn" data-act="fullStop" data-arg="0">Cancel</button></div></div>`
      : `<div class="now"><div class="lbl">Full check ${au.mode.toUpperCase()} · running · do not touch the remote</div>
        <div class="what" style="font-size:24px">${p ? `${esc(p.id)} — ${esc(p.name)}` : '…'}${au.role && au.role !== 'patch' ? ' <span class="muted small">(service)</span>' : ''}</div>
        <div style="height:10px;background:var(--panel);border-radius:6px;margin:10px 0;overflow:hidden"><div style="height:100%;width:${Math.round((au.progress || 0) * 100)}%;background:var(--accent)"></div></div>
        <div class="small muted">${mmss(au.fileTime || 0)} of ${mmss(au.duration)} · readings ${au.readings}</div>
        <div class="row" style="margin-top:10px"><button class="btn" data-act="fullStop" data-arg="1">Stop and analyze what was captured</button><button class="btn" data-act="fullStop" data-arg="0">Cancel</button></div></div>`;
  } else {
    const seq = F.sequences || {};
    runBox = `<div class="now"><div class="lbl">Full check — all patches in one file, automatically</div>
      <div class="what" style="font-size:22px">1) Enter ALL current projector settings below · 2) Start · 3) Play the file</div>
      <div class="row" style="margin-top:10px">
        <button class="btn primary big" data-act="fullStart" data-arg="sdr" ${ready ? '' : 'disabled'}>Start SDR (${seq.sdr ? mmss(seq.sdr.duration) : '14:04'})</button>
        <button class="btn big" data-act="fullStart" data-arg="hdr" ${ready ? '' : 'disabled'}>Start HDR10 (${seq.hdr ? mmss(seq.hdr.duration) : '6:40'})</button></div>
      <p class="small muted">Files: <span class="mono">AUTO/${esc(seq.sdr ? seq.sdr.file : '')}</span> and <span class="mono">AUTO/${esc(seq.hdr ? seq.hdr.file : '')}</span> — in the patch folder (“Preparation → Save patches”). Play may be pressed before or after “Start” (8 s margin). Use the v3 files; older versions have a different schedule. The program finds the mark, assigns the readings to patches, checks each one and computes everything together.</p></div>`;
  }
  const psCard = `<div class="card"><div class="row" style="justify-content:space-between"><h2 style="margin:0">Projector settings (what is set in the menu)</h2>
    <button class="btn" data-act="psToggle">${S.ui.psOpen ? 'Collapse' : 'Open / edit'}</button></div>
    <p class="small muted">Enter the current values before a run. A snapshot is stored with every run — this way the program sees what you changed and learns how each setting affects the picture.</p>
    ${S.ui.psOpen ? psForm() : `<p class="small">Gain ${['R', 'G', 'B'].map(c => fmtGp(psValue('sdr.gain.' + c))).join('/')} · Offset ${['R', 'G', 'B'].map(c => fmtGp(psValue('sdr.offset.' + c))).join('/')} · Black ${esc(psValue('sdr.blackLevel') || '—')} · Contrast ${esc(psValue('sdr.contrast') || '—')} · Gamma ${esc(psValue('sdr.gammaPreset') || '—')} · points: ${esc(sess().menuValues.sdr['Gamma Control Points'] || 'all 0')}</p>`}</div>`;
  const tabs = `<div class="seg" style="margin:6px 0 12px">${['sdr', 'hdr'].map(m => `<button class="${tab === m ? 'sel' : ''}" data-act="fullTab" data-arg="${m}">${m === 'sdr' ? 'SDR' : 'HDR10'}${F.latest[m] ? '' : ' (none)'}</button>`).join('')}</div>`;
  const L = F.latest[tab];
  let res = '';
  if (L) {
    const A = L.analysis, m = A.metrics, sync = L.run.sync || {};
    const total = L.run.segments.filter(x => x.role === 'patch').length;
    const changed = L.settingsChanged || [];
    res += `<div class="card"><h2>Run ${esc(L.run.id)} · ${esc(L.run.startedAt.replace('T', ' ').slice(0, 16))}</h2>
      <p>Counted <b>${total - A.unreliable.length}</b> of ${total} patches · sync: ${sync.endMarker ? '<span class="good">the video reached the end without a shift</span>' : '<span class="ok">finish mark not found</span>'}${sync.maxShift > 0.5 ? ` · pause correction up to ${fmt(sync.maxShift, 1)} s` : ''}${A.drift ? ` · white drift ${fmt(A.drift.pct, 1)}%` : ''}</p>
      ${A.unreliable.length ? `<p class="ok small">Not counted: ${A.unreliable.map(u => `${esc(u.patchId)} (${esc(u.flags.join(', '))})`).join(' · ')}</p>` : ''}
      ${changed.length ? `<div class="banner">Changed in the settings after this run: ${changed.map(c => `${esc(c.label)} ${esc(c.old ?? '—')}→${esc(c.new ?? '—')}`).join(', ')}. Repeat the check to see the effect.</div>` : ''}</div>`;
    const V = L.verdict;
    if (V) {
      res += `<div class="card"><h2>Verdict for run ${esc(L.run.id)}</h2>
        <div style="font-size:19px" class="${lvCls(V.level)}"><b>${esc(V.headline)}</b></div>
        ${V.summary ? `<p class="small muted">${esc(V.summary)}</p>` : ''}
        ${V.set.length ? `<div class="tablewrap"><table class="t" style="width:auto;margin-top:6px"><tr><th class="l">What</th><th class="l">Setting</th><th>Now</th><th>Set to</th></tr>
          ${V.set.map(x => `<tr><td class="l small">${esc(x.title)}</td><td class="l">${esc(x.label)}</td><td>${fmtGp(x.from)}</td><td><b>${fmtGp(x.to)}</b></td></tr>`).join('')}</table></div>
          <div class="row" style="margin-top:6px"><button class="btn" data-act="verdictApply" data-arg="${tab}">I set all this in the menu → save to settings</button></div>` : ''}
        <p>${esc(V.next)}</p>
        ${L.run.hasRaw ? `<div class="row"><button class="btn" data-act="fullReanalyze" data-arg="${esc(L.run.id)}">Re-analyze the run</button></div>` : ''}</div>`;
    }
    res += `<div class="card"><h2>Adjustment plan (by priority)</h2>${L.plan.map((it, i) => `<div style="padding:8px 0;border-bottom:1px solid var(--line)">
        <div style="font-size:17px" class="${lvCls(it.level)}"><b>${i + 1}. ${esc(it.title)}</b></div><div>${esc(it.text)}</div>
        ${it.set && it.set.length ? `<div class="tablewrap"><table class="t" style="width:auto;margin-top:6px"><tr><th class="l">Setting</th><th>Now</th><th>Set to</th></tr>${it.set.map(x => `<tr><td class="l">${esc(x.label)}</td><td>${fmtGp(x.from)}</td><td><b>${fmtGp(x.to)}</b></td></tr>`).join('')}</table></div>
        <div class="row" style="margin-top:6px"><button class="btn" data-act="planApply" data-arg="${tab}:${i}">I set this in the menu → save to settings</button></div>` : ''}</div>`).join('')}
      <p class="small muted">Rule: one group of changes per run (balance only, or gamma only, or CMS colors) — then the program knows exactly what each setting did and the next plan is more precise.</p></div>`;
    if (tab === 'sdr') {
      const g = A.gray, sm = g.summary;
      res += `<div class="card"><div class="kvgrid">
        <div><div class="k">White</div><div class="v">${fmtY(m.whiteY)} <span class="small muted">nits · ${m.whiteCCT ? m.whiteCCT.toFixed(0) + ' K' : ''}</span></div></div>
        <div><div class="k">Black / contrast</div><div class="v">${isNum(m.blackY) ? fmtY(m.blackY) : '—'} <span class="small muted">${m.contrast ? m.contrast.toFixed(0) + ':1' : ''}</span></div></div>
        <div><div class="k">γ eff. 10–90%</div><div class="v">${fmt(m.gMean, 2)}</div></div>
        <div><div class="k">Gray ΔE mean/max</div><div class="v">${fmt(m.deMean, 2)} / ${fmt(m.deMax, 2)}</div></div>
        <div><div class="k">Balance 20–100%, max</div><div class="v">${isNum(sm.balMax) ? fmt(sm.balMax, 1) + '%' : '—'}</div></div>
        <div><div class="k">Color ΔE (weighted / 100%)</div><div class="v">${fmt(m.colorDeW, 2)} / ${fmt(m.colorDeMean, 2)}</div></div>
        <div><div class="k">Sat 25–75%: ΔC / ΔY</div><div class="v">${isNum(m.innerDC) ? fmtS(m.innerDC, 0) + ' / ' + fmtS(m.innerDY, 0) + '%' : '—'}</div></div>
        <div><div class="k">Gray verdict</div><div class="v">${verdictPill(sm.verdict)}</div></div></div></div>
        <div class="card"><h2>Gamma</h2>${gammaChart(g, sess().gamma, !!S.ui.chartLog.full)}${devChart(g)}
         <div class="seg"><button class="${!S.ui.chartLog.full ? 'sel' : ''}" data-act="chartLog" data-step="full" data-arg="0">linear</button><button class="${S.ui.chartLog.full ? 'sel' : ''}" data-act="chartLog" data-step="full" data-arg="1">log</button></div></div>
        <div class="card"><h2>Gray scale</h2>${grayTable(g, false)}</div>
        <div class="card"><h2>Colors and saturations</h2>${colorTable(A.color, false)}</div>
        <div class="card"><h2>xy diagram</h2>${xyChart(A.color)}</div>`;
    } else {
      const h = A.hdr, sm = h.summary;
      res += `<div class="card"><div class="kvgrid">
        <div><div class="k">Peak</div><div class="v">${fmt(sm.peak, 1)} <span class="small muted">nits</span></div></div>
        <div><div class="k">Reference white (203 nits)</div><div class="v">${fmt(sm.refWhite, 1)} <span class="small muted">nits · ${fmt(sm.refWhitePct, 0)}% of peak</span></div></div>
        <div><div class="k">Shadows/mids vs PQ target</div><div class="v">${fmt(sm.scale, 0)}%</div></div>
        <div><div class="k">Balance max</div><div class="v">${fmt(sm.balMax, 1)}%</div></div>
        <div><div class="k">Neutrality</div><div class="v">${verdictPill(sm.verdict)}</div></div></div></div>
        <div class="card"><h2>Y vs PQ target</h2>${hdrChart(h)}</div><div class="card"><h2>Table</h2>${hdrTable(h, false)}</div>`;
    }
    const runsM = F.runs.filter(r => r.mode === tab);
    const last = runsM[runsM.length - 1];
    if (last && last.changes.length) {
      res += `<div class="card"><h2>What changed since the previous run</h2>
        <p><b>Settings:</b> ${last.changes.map(c => `${esc(c.label)} ${esc(c.old ?? '—')}→${esc(c.new ?? '—')}`).join(', ')}</p>
        <div class="tablewrap"><table class="t"><tr><th class="l">Metric</th><th>Was</th><th>Now</th><th>Δ</th></tr>
        ${last.compare.filter(c => c.sig).map(c => `<tr><td class="l">${esc(c.label)}</td><td>${fmtV(c.old)}</td><td>${fmtV(c.new)}</td><td>${isNum(c.rel) ? fmtS(c.rel, 1) + '%' : fmtS(c.delta, 2)}</td></tr>`).join('') || '<tr><td class="l" colspan="4">no noticeable changes</td></tr>'}</table></div></div>`;
    }
  } else {
    res = `<div class="card"><p class="muted">No ${tab === 'sdr' ? 'SDR' : 'HDR10'} runs yet.</p></div>`;
  }
  const know = F.knowledge.length ? `<div class="card"><h2>Setting links (learned from your runs)</h2>
    ${F.sens.length ? `<div class="tablewrap"><table class="t" style="width:auto"><tr><th class="l">Group</th><th class="l">What</th><th>Per step</th></tr>${F.sens.map(x => `<tr><td class="l">${esc(x.group)}</td><td class="l">${esc(x.key)}</td><td>${fmtS(x.value, 2)}</td></tr>`).join('')}</table></div>` : ''}
    ${F.knowledge.map(k => `<div style="padding:6px 0;border-bottom:1px solid var(--line)" class="small"><b>${esc(k.from)} → ${esc(k.to)}</b>: changed ${esc(k.changed)} <span class="muted">(${esc(k.groups)}${k.single ? '' : ' — several groups at once, effect cannot be separated'})</span><br>→ ${esc(k.effects)}</div>`).join('')}</div>` :
    `<div class="card"><h2>Setting links</h2><p class="small muted">Appear after two runs with a settings change between them. Change one group at a time.</p></div>`;
  const hist = F.runs.length ? `<div class="card"><h2>Run history</h2><div class="tablewrap"><table class="t">
    <tr><th class="l">Run</th><th class="l">When</th><th>Patches</th><th>White</th><th>γ</th><th>Gray ΔE</th><th>Color ΔE</th><th>Contrast</th><th class="l">Settings changes</th></tr>
    ${F.runs.map(r => `<tr><td class="l">${esc(r.id)}</td><td class="l">${esc(r.startedAt.replace('T', ' ').slice(5, 16))}</td><td>${r.used ?? '—'}</td><td>${fmtY(r.metrics.whiteY ?? r.metrics.peak)}</td><td>${fmt(r.metrics.gMean, 2)}</td><td>${fmt(r.metrics.deMean, 2)}</td><td>${fmt(r.metrics.colorDeMean, 2)}</td><td>${r.metrics.contrast ? r.metrics.contrast.toFixed(0) : '—'}</td>
      <td class="wrap small">${r.changes.map(c => `${esc(c.label)} ${esc(c.old ?? '—')}→${esc(c.new ?? '—')}`).join(', ') || '—'}</td></tr>`).join('')}</table></div></div>` : '';
  return runBox + psCard + tabs + res + know + hist + notes('full');
}
const fmtV = v => isNum(v) ? (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')) : esc(v ?? '—');

// ------------------------------------------------------------ 4. Gamma by points (corrections, rounds)
function viewGpoints(s) {
  const a = an('gpoints'), hist = an('gpointsHistory');
  const st = s.stage || 'prep';
  const vals = s.values || {};
  const pts = Array.from({ length: 20 }, (_, i) => i + 1);
  const valGrid = (editable) => `<div class="tablewrap"><table class="t">
    <tr><th class="l">Point</th>${pts.map(k => `<th>${k}</th>`).join('')}</tr>
    <tr><td class="l">Step</td>${pts.map(k => `<td class="muted">${k * 5}%</td>`).join('')}</tr>
    <tr><td class="l">Gain</td>${pts.map(k => editable ? `<td><input type="number" class="gpv" data-gpk="${k}" value="${vals[k] || 0}" style="width:52px;padding:3px"></td>` : `<td><b>${fmtGp(vals[k])}</b></td>`).join('')}</tr></table></div>`;
  const stages = [['prep', 'Start'], ['base', 'Measure'], ['plan', 'Corrections'], ['check', 'Check'], ['done', 'Done']];
  const bar = `<div class="seg" style="margin-bottom:12px">${stages.map(([id, t], i) => `<button class="${id === st || (st === 'check' && id === 'check') ? 'sel' : ''}" disabled>${i + 1}. ${t}</button>`).join('')}</div>`;
  const rules = `<div class="card"><h2>How it works</h2><ul class="small">
    <li>The Vidda C3S has <b>20 points</b>: point k controls the step at <b>k·5%</b> (1 → 5%, 10 → 50%, 19 → 95%), smoothly in between. One Gain step ≈ <b>${fmt(s.perStep || 2.3, 2)}%</b> of its step's luminance${s.learn ? ` (refined from results, fit ${Math.round(s.learn.fit * 100)}%)` : ''}.</li>
    <li>The projector applies a point change <b>after leaving the menu</b>, and an open menu spills light into the meter. So points are not tuned live: the program computes corrections for all points at once, you set them, <b>close the menu</b> and do a check pass.</li>
    <li>Point 20 (100%) is white, leave it alone. Point 1 (5%) is almost black: tune it only at night, with no room light.</li>
    <li>Shadows (10–20%) can be measured honestly only in full darkness: in daytime room light adds to black.</li></ul></div>`;
  let body = '';
  if (st === 'prep') {
    body = `<div class="now"><div class="lbl">Start</div><div class="what" style="font-size:24px">Gamma: fine-tune the current points or start from scratch</div>
      <p>Gamma preset: <input type="text" id="gpPreset" value="${esc(s.preset || sess().menuValues.sdr.Gamma || '')}" style="width:120px"> (do not change it later — it resets the points).</p>
      <p><b>Currently set in the menu</b> (check on the projector and correct if different):</p>${valGrid(true)}
      <div class="row" style="margin-top:10px"><button class="btn primary big" data-act="gpContinue">Fine-tune current → full pass</button>
       <button class="btn" data-act="gpStart">From scratch (I set all points = 0)</button></div>
      <p class="small muted">Next: a full pass 100→0% (6–7 min), the program shows corrections for all points.</p></div>`;
  } else if (st === 'base' || st === 'check') {
    const done = an('gpPassDone');
    const pr = s.probe;
    body = `<div class="now"><div class="lbl">${st === 'base' ? 'Base pass' : pr ? 'Point probe ' + pr.point : 'Check pass, round ' + ((s.round || 2) - 1)}</div>
      <div class="what" style="font-size:22px">${st === 'check' ? 'Close the projector menu. ' : ''}Full pass 100% → 0%: play the patches in order</div>
      <div class="small muted">About 6–7 minutes, the program detects patch changes by itself.</div></div>
      ${passBlock('gpoints', a)}
      ${done ? `<div class="now done"><div class="row"><button class="btn primary big" data-act="gpPlan">Pass done → compute corrections</button></div></div>` : ''}
      ${grayResults('gpoints', a)}`;
  } else if (st === 'plan') {
    const plan = s.plan || [], ch = plan.filter(r => r.delta);
    const q = s.queue || [];
    const pr = s.probe;
    const probeRes = pr && pr.result ? `<div class="card"><h2>Point probe ${pr.point} (${fmtS(pr.delta, 0)})</h2>
      <p>Strongest effect at <b>${pr.result.peak}%</b> (noticeable at ${pr.result.from}–${pr.result.to}%), ≈ ${fmt(Math.abs(pr.result.perStep), 1)}% per step.</p>
      <p class="small">${Object.entries(pr.result.effect).map(([k, v]) => `${k}%: ${fmtS(v, 1)}`).join(' · ')}</p>
      <div class="row"><button class="btn primary" data-act="gpProbeRestore">Set point ${pr.point} back to ${fmtGp(pr.restore)} in the menu → check pass</button></div></div>` : '';
    body = `<div class="now ${q.length ? 'warn' : 'done'}"><div class="lbl">Corrections, round ${s.round || 1}</div>
      <div class="what" style="font-size:24px">${q.length ? 'Set the new point values in the menu' : 'Gamma within tolerance ✓'}</div>
      ${q.length ? '' : '<div>You can finish or refine slightly using the table.</div>'}</div>
      ${pr && pr.result ? probeRes : ''}
      <div class="card"><div class="tablewrap"><table class="t"><tr><th class="l">Point</th><th>Step</th><th>Current dev.</th><th>Current Gain</th><th>Set to</th><th>Change</th></tr>
      ${plan.map(r => `<tr${r.delta ? ' class="cur"' : ''}><td class="l">${r.point}</td><td>${r.stim}%</td>
        <td class="${lvCls(isNum(r.dev) ? (Math.abs(r.dev) <= (r.stim >= 30 ? 1.5 : 3) ? 'good' : Math.abs(r.dev) <= (r.stim >= 30 ? 3 : 6) ? 'ok' : 'bad') : '')}">${isNum(r.dev) ? fmtS(r.dev, 1) + '%' : '—'}</td>
        <td>${fmtGp(r.now)}</td><td style="font-size:20px"><b>${fmtGp(r.new)}</b></td><td>${r.delta ? fmtS(r.delta, 0) : ''}</td></tr>`).join('')}</table></div>
      <ol style="font-size:16px;margin-top:10px"><li>In Gamma → Adjust set the points to the values in the “Set to” column.</li>
       <li><b>Leave the menu</b> completely (otherwise the projector will not apply it and the menu will reach the meter).</li>
       <li>Press the button below — the check pass starts.</li></ol>
      <div class="row">${ch.length ? `<button class="btn primary big" data-act="gpApplied">Set ${ch.length} points and left the menu → check</button>` : ''}
       <button class="btn${q.length ? '' : ' primary big'}" data-act="gpFinish">${q.length ? 'Leave as is' : 'Done ✓'}</button></div></div>
      <div class="card"><h2>Point probe (find out which step a point moves)</h2>
       <div class="row">Point <input type="number" id="gpProbeK" min="1" max="20" value="11" style="width:60px"> shift <input type="number" id="gpProbeD" value="-10" style="width:60px">
       <button class="btn" data-act="gpProbe">Set it and left the menu → pass</button></div>
       <p class="small muted">The program compares with the last pass and shows which steps the point affects. Then it reminds you to restore the value.</p></div>
      ${grayResults('gpoints', a)}`;
  } else {
    body = `<div class="now done"><div class="lbl">Done</div><div class="what">Gamma is set ✓</div>
      <div style="font-size:17px">Points: <b>${esc(sess().menuValues.sdr['Gamma Control Points'])}</b> (preset ${esc(s.preset || '—')}) — in the menu values and the report.</div>
      <div class="row" style="margin-top:10px"><button class="btn primary" data-act="gpContinueQuick">Fine-tune more (new pass)</button><button class="btn" data-act="gpRestart">Start over</button></div></div>
      ${grayResults('gpoints', a)}`;
  }
  const histT = hist.length ? `<div class="card"><h2>Passes</h2><table class="t"><tr><th class="l">Pass</th><th>Time</th><th>Color ΔE mean</th><th>γ</th><th>Balance max</th><th class="l">Verdict</th></tr>
    ${hist.map(h => `<tr><td class="l">№${h.pass}</td><td>${esc(h.t)}</td><td>${fmt(h.deMean, 2)}</td><td>${fmt(h.gMean, 2)}</td><td>${isNum(h.balMax) ? fmt(h.balMax, 1) + '%' : '—'}</td><td class="l">${verdictPill(h.verdict)}</td></tr>`).join('')}</table></div>` : '';
  return `${bar}${body}<div class="card"><h2>Point values (kept by the program)</h2>${valGrid(false)}</div>${rules}${histT}${notes('gpoints')}`;
}
const fmtGp = v => v ? (v > 0 ? '+' : '') + v : '0';

function grayLiveView(pid) {
  const p = P(pid), ctl = p.stim >= 0.5 ? 'Gain' : 'Offset';
  return `
  <div class="row" style="margin-bottom:12px"><button class="btn primary" data-act="grayLiveOff">← Back to the circle</button>
   <div class="seg">${CIRCLE_PIDS.map(id => `<button class="${id === pid ? 'sel' : ''}" data-act="grayLive" data-arg="${id}">${chipLabel(id)}</button>`).join('')}</div></div>
  ${nowBox(pid, `<div style="margin-top:6px">Live adjustment: <b>White Balance → ${ctl} R / B</b> (leave green alone). <span class="muted" id="wbExp"></span></div>
    <div class="bad small" id="wbSuspect"></div>`)}
  <div class="card"><div class="bars big">${barsSkeleton('wb', true)}</div>
   <div class="row" style="margin-top:12px;gap:24px"><span>CCT <b id="wbCCT">—</b></span><span>Δu′v′ <b id="wbDuv">—</b></span><span>Y <b id="wbY">—</b></span>
   <span class="pill" id="wbStab">—</span></div>
   <div id="wbHints" style="font-size:20px;margin-top:12px"></div>
   <p class="small muted">Live records nothing. Adjust, close the projector menu (it must not cover the center) and go back to the circle — the circle shows the result on all steps.</p></div>
  <div class="card"><h2>Menu values (SDR)</h2>${menuInputs('sdr', ['Gain R', 'Gain G', 'Gain B', 'Offset R', 'Offset G', 'Offset B'])}</div>`;
}

function updateLive() {
  const st = S.state, r = st.reading, d = r ? (r.mean || r.last) : null;
  const lvY = $('#lvY');
  if (lvY) {
    lvY.textContent = d ? fmtY(d.Y) + ' nits' : '—';
    $('#lvSub').textContent = d ? `spread ±${fmt(r.sd || 0, 2)}% · CCT ${d.cct ? d.cct.toFixed(0) : '—'} K` : (st.meter.state === 'ready' ? 'waiting for a reading…' : 'meter not ready');
  }
  if ($('#wb-R-m')) {
    updateBars('wb', d ? d.bal : null);
    $('#wbCCT').textContent = d && d.cct ? d.cct.toFixed(0) + ' K' : '—';
    $('#wbDuv').textContent = d ? fmtS(d.duv, 4) : '—';
    $('#wbY').textContent = d ? fmtY(d.Y) + ' nits' : '—';
    const l = st.live || {};
    $('#wbExp').textContent = l.expectedY ? `expected ≈ ${fmtY(l.expectedY)} nits` : '';
    $('#wbSuspect').textContent = l.osd || l.suspect || '';
    const stab = $('#wbStab');
    if (r && r.n >= 2) { stab.className = 'pill ' + (r.stable ? 'good' : 'ok'); stab.textContent = r.stable ? 'stable' : 'changing'; }
    const h = l.hints;
    $('#wbHints').innerHTML = !d ? '<span class="muted">waiting for a reading…</span>' : (h && h.length ? h.map(x => `<div class="${x.pct > 5 ? 'bad' : 'ok'}">${x.action === 'up' ? '↑' : '↓'} ${esc(x.text)}</div>`).join('') : '<div class="good">✓ Within tolerance (98–102)</div>');
  }
}

// ------------------------------------------------------------ patch pass (common block)
function passBlock(stepId, a, opts) {
  opts = opts || {};
  const p = S.state.pass;
  if (!p || p.stepId !== stepId) return '<p class="muted">…</p>';
  const s = sess(), cur = p.patchId;
  const measured = new Set(s.measurements.filter(m => m.stepId === stepId && (m.pass || 1) === p.pass).map(m => m.patchId));
  const ready = S.state.meter.state === 'ready';
  let box;
  if (cur && opts.compact) {
    box = `<p style="font-size:18px">Play <b class="mono">${esc(cur)}</b> — ${esc(P(cur).name)} <span class="mono small muted">${esc(P(cur).filename)}</span>
      <span class="small muted">· ${p.idx + 1} of ${p.list.length}${p.expectedY ? ` · expected ≈ ${fmtY(p.expectedY)} nits` : ''}</span></p>`;
  } else if (!cur && opts.compact) {
    box = `<p class="good">✓ Pass #${p.pass} complete</p>`;
  } else if (cur) {
    const exp = p.expectedY ? `<div class="small muted" style="margin-top:4px">expected ≈ ${fmtY(p.expectedY)} nits</div>` : '';
    box = nowBox(cur, exp + `<div class="small muted" style="margin-top:4px">Patch ${p.idx + 1} of ${p.list.length} · pass #${p.pass}</div>`, p.wrong ? 'warn' : '');
  } else {
    box = `<div class="now done"><div class="lbl">Pass #${p.pass}</div><div class="what">Pass complete ✓</div>
      <div class="small muted">See the results below. To re-measure after changes — “New pass”.</div></div>`;
  }
  const job = S.state.meter.job;
  const jobTxt = job && job.purpose === 'pass' ? `<div class="status wrong" style="font-size:18px"><b>Measuring ${esc(job.patchId)}: ${job.i} of ${job.n}.</b> Do not switch the patch until “Recorded” appears.</div>` : '';
  const stTxt = jobTxt || (p.state && p.state.text ? `<div class="status ${p.state.state}">${esc(p.state.text)}</div>` : '');
  const wrong = p.wrong && !jobTxt ? `<div class="status wrong"><b>${p.wrong.manual ? 'Reading not recorded: looks like the wrong patch.' : 'Looks like the wrong file is playing.'}</b> ${esc(p.wrong.text)}
    <div class="row" style="margin-top:6px"><button class="btn" data-act="passAccept" data-arg="${stepId}">Record anyway</button></div></div>` : '';
  const chips = p.list.map((id, i) => `<span class="chip${measured.has(id) ? ' m' : ''}${i === p.idx ? ' cur' : ''}" data-act="passSelect" data-step="${stepId}" data-arg="${i}" title="${esc(P(id).name)}">${measured.has(id) ? '✓ ' : ''}${esc(chipLabel(id))}</span>`).join('');
  const busy = S.state.meter.jobs > 0;
  return `${box}${wrong || stTxt}
  <div class="row">
    <div class="seg"><button class="${p.auto ? 'sel' : ''}" data-act="passAuto" data-step="${stepId}" data-arg="1">Auto</button><button class="${!p.auto ? 'sel' : ''}" data-act="passAuto" data-step="${stepId}" data-arg="0">Manual</button></div>
    <button class="btn primary" data-act="passMeasure" data-arg="${stepId}" ${!ready || !cur || busy ? 'disabled' : ''}>${busy ? 'Measuring…' : 'Measure now'}</button>
    <button class="btn" data-act="passBack" data-arg="${stepId}" ${p.idx === 0 ? 'disabled' : ''}>← Back</button>
    <button class="btn" data-act="passSkip" data-arg="${stepId}" ${!cur ? 'disabled' : ''}>Skip patch</button>
    <button class="btn" data-act="passNew" data-arg="${stepId}">New pass</button>
    ${opts.extra || ''}
  </div>
  <div class="chips">${chips}</div>
  <p class="small muted">${p.auto ? 'Auto: play the patch — the program waits for the picture change and stable readings, checks it is the right patch, records the mean and moves on.' : 'Manual: play the patch, wait 3–5 seconds and press “Measure now”.'}</p>`;
}
function chipLabel(id) {
  const p = P(id);
  if (p.kind === 'gray') return (p.mode === 'hdr10' ? 'PQ ' : '') + Math.round(p.stim * 100) + '%';
  if (p.kind === 'color') return ({ red: 'R', yellow: 'Y', green: 'G', cyan: 'C', blue: 'B', magenta: 'M' })[p.hue] + (p.amp === 1 ? '' : ' ' + Math.round(p.sat * 100));
  return id;
}

// ------------------------------------------------------------ 4. Gamma / 6. Verification
function viewVerify(s) {
  const a = an('verify'), hist = an('verifyHistory');
  const quick = `<label class="chk" style="padding:0"><input type="checkbox" data-opt="quick" data-step="verify" ${s.quick ? 'checked' : ''}> Quick: 100/70/50/30/20/10</label>`;
  return `<p>A final gray scale pass <b>without changes</b> — after the colors. If it is worse than the last gray circle, CMS has affected gray: go back to the “Gray scale” step.</p>
  ${passBlock('verify', a, { extra: quick })}
  ${hist.length > 1 ? `<div class="card"><h2>Checks</h2><table class="t"><tr><th class="l">Pass</th><th>Time</th><th>Color ΔE mean</th><th>γ</th><th>Balance max</th><th class="l">Verdict</th></tr>
    ${hist.map(h => `<tr><td class="l">№${h.pass}</td><td>${esc(h.t)}</td><td>${fmt(h.deMean, 2)}</td><td>${fmt(h.gMean, 2)}</td><td>${isNum(h.balMax) ? fmt(h.balMax, 1) + '%' : '—'}</td><td class="l">${verdictPill(h.verdict)}</td></tr>`).join('')}</table></div>` : ''}
  ${grayResults('verify', a)}
  ${notes('verify')}`;
}
function grayResults(stepId, a) {
  const sm = a.summary;
  const log = !!S.ui.chartLog[stepId];
  const kv = `<div class="kvgrid">
    <div><div class="k">White</div><div class="v">${fmtY(sm.whiteY)} <span class="small muted">nits</span></div></div>
    <div><div class="k">Black / contrast</div><div class="v">${isNum(sm.blackY) ? fmtY(sm.blackY) : '—'}${sm.contrast ? ` <span class="small muted">${sm.contrast.toFixed(0)}:1</span>` : ''}</div></div>
    <div><div class="k">Color ΔE 10–90% mean/max</div><div class="v">${fmt(sm.deMean, 2)} / ${fmt(sm.deMax, 2)}</div></div>
    <div><div class="k">γ eff. 10–90% (target ${sm.gamma === 'bt1886' ? 'BT.1886' : sm.gamma})</div><div class="v">${fmt(sm.gMean, 2)}</div><div class="small muted">${esc(sm.gText || '')}</div></div>
    <div><div class="k">Balance 20–100%, max dev.</div><div class="v">${isNum(sm.balMax) ? fmt(sm.balMax, 1) + '%' : '—'}</div></div>
    <div><div class="k">Verdict</div><div class="v">${verdictPill(sm.verdict)}</div></div></div>`;
  const hints = a.hints.length ? `<div class="card"><h2>Hints</h2>${a.hints.map(h => `<div class="${lvCls(h.level)}" style="margin:4px 0">• ${esc(h.text)}</div>`).join('')}</div>` :
    (sm.measuredCount ? '<div class="card"><span class="good">✓ No hints — the gray scale is within tolerance.</span></div>' : '');
  return `<div class="card">${kv}</div>${hints}
  <div class="card"><div class="row" style="justify-content:space-between"><h2 style="margin:0">Luminance curve</h2>
    <div class="seg"><button class="${!log ? 'sel' : ''}" data-act="chartLog" data-step="${stepId}" data-arg="0">linear</button><button class="${log ? 'sel' : ''}" data-act="chartLog" data-step="${stepId}" data-arg="1">log</button></div></div>
    ${gammaChart(a, sess().gamma, log)}${devChart(a)}</div>
  <div class="card"><h2>Table</h2>${grayTable(a, true)}</div>`;
}
function grayTable(a, del) {
  const rows = a.rows.filter(r => r.measured);
  if (!rows.length) return '<p class="muted">No readings yet.</p>';
  const prev = rows.some(r => isNum(r.chgPrev));
  const head = `<tr><th class="l">Step</th><th>Y meas.</th><th>Y target</th><th>Dev.</th><th>γ eff.</th><th>ΔE color</th><th>ΔE full</th><th>R</th><th>G</th><th>B</th><th>CCT</th><th>±%</th>${prev ? '<th>Δ vs previous</th>' : ''}${del ? '<th></th>' : ''}</tr>`;
  const body = rows.map(r => `<tr><td class="l">${r.stim}%</td><td>${fmtY(r.Y)}</td><td>${fmtY(r.Yt)}</td>
    <td class="${lvCls(r.gLevel)}">${fmtS(r.dev, 1)}${isNum(r.dev) ? '%' : ''}</td><td class="${lvCls(r.gLevel)}">${fmt(r.gEff, 2)}</td>
    <td class="${lvCls(r.deLevel)}">${fmt(r.deColor, 2)}</td><td>${fmt(r.deFull, 2)}</td>
    ${(r.bal || [null, null, null]).map(b => `<td class="${r.balLevel && isNum(b) ? lvCls(Math.abs(b - 100) <= 2 ? 'good' : Math.abs(b - 100) <= 5 ? 'ok' : 'bad') : ''}">${fmt(b, 1)}</td>`).join('')}
    <td>${isNum(r.cct) ? r.cct.toFixed(0) : '—'}</td><td class="muted">${fmt(r.sd, 2)}</td>
    ${prev ? `<td>${isNum(r.chgPrev) ? fmtS(r.chgPrev, 1) + '%' : ''}</td>` : ''}
    ${del ? `<td>${r.mid ? `<span class="muted" style="cursor:pointer" title="Delete reading" data-act="delMeas" data-arg="${r.mid}">✕</span>` : ''}</td>` : ''}</tr>`).join('');
  return `<div class="tablewrap"><table class="t">${head}${body}</table></div>`;
}

// ------------------------------------------------------------ 5. Colors
const HUE_COLOR = { red: '#d0584f', yellow: '#d6c348', green: '#55b06a', cyan: '#4fb8c4', blue: '#5b78d6', magenta: '#c45cbf' };
const HUE_NAME = { red: 'Red', yellow: 'Yellow', green: 'Green', cyan: 'Cyan', blue: 'Blue', magenta: 'Magenta' };
function viewColor(s) {
  const a = an('color'), sm = a.summary;
  if (sm.error) return `<div class="banner">${esc(sm.error)}</div>`;
  const sw = `<label class="chk" style="padding:0"><input type="checkbox" data-opt="sweeps" data-step="color" ${s.sweeps ? 'checked' : ''}> + saturation 25–100% (24 patches, for reference)</label>`;
  return `
  <div class="banner" style="background:var(--chip);border-color:var(--line)">A colorimeter on a laser projector without spectral correction may be off on saturated colors.
   Trust the <b>trend</b> and check by eye (skin tones, gray). Do not chase ΔE &lt; 3.</div>
  <div class="card"><h2>Color Space</h2><p class="small">If the menu has Auto / BT.709 — pick it for SDR. If only Native — CMS compensates partially.</p>${menuInputs('sdr', ['Color Space'])}</div>
  ${passBlock('color', a, { extra: sw })}
  <div class="card"><div class="kvgrid">
    <div><div class="k">White for the calculation</div><div class="v">${fmtY(sm.whiteY)} <span class="small muted">nits</span></div></div>
    <div><div class="k">ΔE2000 of the 6 primaries mean/max</div><div class="v">${fmt(sm.deMean, 2)} / ${fmt(sm.deMax, 2)}</div></div>
    <div><div class="k">Verdict (target mean &lt; 3, acceptable &lt; 5)</div><div class="v">${verdictPill(sm.verdict)}</div></div></div></div>
  ${a.hints.length ? `<div class="card"><h2>Hints (Color Adjustment: Color tone / Saturation / Bright, steps of 1–3)</h2>${a.hints.map(h => `<div class="${lvCls(h.level)}" style="margin:4px 0">• ${esc(h.text)}</div>`).join('')}</div>` : ''}
  <div class="card"><h2>Table</h2>${colorTable(a, true)}</div>
  <div class="card"><h2>xy diagram</h2>${xyChart(a)}<p class="small muted">□ Rec.709 target · ● measured · dashed — the Rec.709 triangle</p></div>
  <div class="card"><h2>CMS values</h2>${menuInputs('sdr', ['CMS Red', 'CMS Yellow', 'CMS Green', 'CMS Cyan', 'CMS Blue', 'CMS Magenta/Pink'])}
   <p class="small muted">After CMS — a quick gray check (100/50/20%). Do not touch Skin until the visual check.</p></div>
  ${notes('color')}`;
}
function colorTable(a, del) {
  const rows = a.rows.filter(r => r.measured);
  if (!rows.length) return '<p class="muted">No readings yet.</p>';
  return `<div class="tablewrap"><table class="t"><tr><th class="l">Color</th><th>x / y meas.</th><th>x / y target</th><th>Y</th><th>ΔE2000</th><th>Δh°</th><th>ΔC%</th><th>ΔY%</th><th class="l">Hint</th>${del ? '<th></th>' : ''}</tr>
  ${rows.map(r => `<tr><td class="l"><span style="color:${HUE_COLOR[r.hue]}">■</span> ${HUE_NAME[r.hue]}${r.amp === 1 ? '' : ` ${Math.round(r.sat * 100)}%`}</td>
   <td>${fmt(r.x, 4)} / ${fmt(r.y, 4)}</td><td class="muted">${fmt(r.tx, 4)} / ${fmt(r.ty, 4)}</td><td>${fmtY(r.Y)}</td>
   <td class="${lvCls(r.deLevel)}">${fmt(r.de, 2)}${isNum(r.prevDe) ? ` <span class="muted small">(was ${fmt(r.prevDe, 1)})</span>` : ''}</td>
   <td>${fmtS(r.dh, 1)}</td><td>${fmtS(r.dC, 1)}</td><td>${fmtS(r.dY, 1)}</td><td class="wrap small">${esc(r.hint)}</td>
   ${del ? `<td><span class="muted" style="cursor:pointer" title="Delete reading" data-act="delMeas" data-arg="${r.mid}">✕</span></td>` : ''}</tr>`).join('')}</table></div>`;
}

// ------------------------------------------------------------ 7. Visual check / 9. DV
function viewEyes() {
  return `<div class="card"><p>Compare the gray scale and skin tones with a calibrated reference monitor if you have one. Laser ≠ LCD: do not compare saturation.</p>${checklist('eyes', EYES_CHECK)}</div>
  <div class="card"><h2>Menu values</h2>${menuInputs('sdr', ['CMS Red', 'CMS Yellow', 'Sharpness'])}</div>${notes('eyes')}`;
}
function viewDV() {
  return `<div class="card"><p>Dolby Vision is not measured (there is no DV patch generator). Only a check and setup by analogy with HDR10.</p>${checklist('dv', DV_CHECK)}</div>${notes('dv')}`;
}

// ------------------------------------------------------------ 8. HDR10
function viewHDR(s) {
  const a = an('hdr'), sm = a.summary;
  return `
  <div class="card"><h2>Before the pass</h2><ol class="small">
   <li>Play <b>HDR_06</b> — the player switches the projector to HDR10. Picture mode Filmmaker/HDR; Dynamic Contrast, AI contrast and dynamic tone mapping — off.</li>
   <li>Laser: if HDR has its own level — maximum; if it is shared with SDR — leave as is (fixed: ${esc(sess().laserLevel || '—')}).</li>
   <li>Target — neutral gray (RGB balance 98–102) over the whole range: White Balance in the HDR memory, Gain on bright steps, Offset on dark ones.</li>
   <li><b>After changing the HDR balance, re-measure SDR</b> (“Summary” step → recheck 100/50/20/10): on the Vidda C3S the balance may be shared between the modes.</li></ol>
   <p class="small muted">Movies carry their own metadata (MaxCLL etc.) and the projector tone mapping adapts: patches set neutrality, not absolute accuracy.</p></div>
  ${passBlock('hdr', a)}
  <div class="card"><div class="kvgrid">
   <div><div class="k">Projector peak ≈</div><div class="v">${isNum(sm.peak) ? sm.peak.toFixed(0) + ' <span class="small muted">nits</span>' : '—'}</div></div>
   <div><div class="k">Shadows/mids vs PQ target</div><div class="v">${isNum(sm.scale) ? sm.scale.toFixed(0) + '%' : '—'}</div><div class="small muted">${sm.rolloff ? `extra compression from PQ ${sm.rolloff.stim}%` : ''}</div></div>
   <div><div class="k">HDR reference white (203 nits)</div><div class="v">${isNum(sm.refWhite) ? fmt(sm.refWhite, 1) + ' <span class="small muted">nits</span>' : '—'}</div><div class="small muted">${isNum(sm.refWhitePct) ? sm.refWhitePct.toFixed(0) + '% of peak, ' + (sm.refWhiteVsSdr || 0).toFixed(0) + '% of SDR white · target 50–70% of peak' : ''}</div></div>
   <div><div class="k">Balance, max dev.</div><div class="v">${isNum(sm.balMax) ? fmt(sm.balMax, 1) + '%' : '—'}</div></div>
   <div><div class="k">Neutrality verdict</div><div class="v">${verdictPill(sm.verdict)}</div></div></div></div>
  ${a.hints.length ? `<div class="card"><h2>Hints</h2>${a.hints.map(h => `<div class="${lvCls(h.level)}" style="margin:4px 0">• ${esc(h.text)}</div>`).join('')}</div>` : ''}
  <div class="card"><h2>Y vs PQ target (log)</h2>${hdrChart(a)}</div>
  <div class="card"><h2>Table</h2>${hdrTable(a, true)}</div>
  <div class="card"><h2>Menu values (HDR10)</h2>${menuInputs('hdr10', Object.keys(sess().menuValues.hdr10))}</div>
  ${notes('hdr')}`;
}
function hdrTable(a, del) {
  const rows = a.rows.filter(r => r.measured);
  if (!rows.length) return '<p class="muted">No readings yet.</p>';
  return `<div class="tablewrap"><table class="t"><tr><th class="l">PQ</th><th>Target, nits</th><th>Y, nits</th><th>% of target</th><th>R</th><th>G</th><th>B</th><th>ΔE color</th><th>CCT</th>${del ? '<th></th>' : ''}</tr>
  ${rows.map(r => `<tr><td class="l">${r.stim}%</td><td>${fmt(r.tnits, 1)}</td><td>${fmtY(r.Y)}</td><td>${fmt(r.ratio, 0)}%</td>
   ${r.bal.map(b => `<td class="${lvCls(Math.abs(b - 100) <= 2 ? 'good' : Math.abs(b - 100) <= 5 ? 'ok' : 'bad')}">${fmt(b, 1)}</td>`).join('')}
   <td>${fmt(r.deColor, 2)}</td><td>${isNum(r.cct) ? r.cct.toFixed(0) : '—'}</td>
   ${del ? `<td><span class="muted" style="cursor:pointer" title="Delete reading" data-act="delMeas" data-arg="${r.mid}">✕</span></td>` : ''}</tr>`).join('')}</table></div>`;
}

// ------------------------------------------------------------ 10. Summary
function viewSummary() {
  const s = sess(), A = S.sv.analysis;
  const g = A.verify.summary.measuredCount ? A.verify : A.gray;
  const balRow = A.gray.rows.filter(r => r.measured && r.bal && r.stim >= 10).map(r => `${r.stim}%: ${r.bal.map(b => fmt(b, 0)).join('/')}`).join(' · ') || '—';
  const mv = (mode) => `<div class="menuvals">${Object.keys(s.menuValues[mode]).map(k => `<label><span>${esc(k)}</span><input type="text" data-menu="${mode}" data-key="${esc(k)}" value="${esc(s.menuValues[mode][k] || '')}"></label>`).join('')}</div>`;
  return `
  <div class="card"><h2>Summary</h2><div class="tablewrap"><table class="t">
   <tr><th class="l">What</th><th class="l">Result</th><th class="l">Status</th></tr>
   <tr><td class="l">Laser level / white</td><td class="l">${esc(s.laserLevel || '—')} / ${s.whiteY ? fmt(s.whiteY, 1) + ' nits' : '—'}</td><td class="l">${s.whiteY ? '<span class="pill good">fixed</span>' : '<span class="pill">no</span>'}</td></tr>
   <tr><td class="l">R/G/B balance (last circle)</td><td class="l small">${balRow}</td><td class="l">${verdictPill(A.gray.summary.verdict)}</td></tr>
   <tr><td class="l">Gray scale (${A.verify.summary.measuredCount ? 'verification' : 'circle'})</td><td class="l">ΔE mean ${fmt(g.summary.deMean, 2)}, max ${fmt(g.summary.deMax, 2)}; γ ${fmt(g.summary.gMean, 2)}${g.summary.contrast ? `; contrast ${g.summary.contrast.toFixed(0)}:1` : ''}</td><td class="l">${verdictPill(g.summary.verdict)}</td></tr>
   <tr><td class="l">SDR colors (6 primaries)</td><td class="l">ΔE2000 mean ${fmt(A.color.summary.deMean, 2)}, max ${fmt(A.color.summary.deMax, 2)}</td><td class="l">${verdictPill(A.color.summary.verdict)}</td></tr>
   <tr><td class="l">HDR10</td><td class="l">peak ≈ ${isNum(A.hdr.summary.peak) ? A.hdr.summary.peak.toFixed(0) + ' nits' : '—'}${isNum(A.hdr.summary.scale) ? `; shadows/mids ≈ ${A.hdr.summary.scale.toFixed(0)}% of PQ` : ''}</td><td class="l">${verdictPill(A.hdr.summary.verdict)}</td></tr>
  </table></div></div>
  <div class="card"><h2>Export</h2><div class="row">
   <button class="btn primary" data-act="report">Report (PDF)</button>
   <a class="btn" href="/export/measurements.csv">CSV of all readings</a>
   <a class="btn" href="/export/session.json">Session JSON</a>
   <a class="btn" href="/export/spotread.log">spotread log</a></div>
   <p class="small muted">The report opens as a page: “Print / PDF” → “Save as PDF”.</p></div>
  <div class="card"><h2>SDR menu values</h2>${mv('sdr')}</div>
  <div class="card"><h2>HDR10 menu values</h2>${mv('hdr10')}</div>
  <div class="card"><h2>Quick recheck (100/50/20/10)</h2><p class="small muted">For future checks: over months the laser and balance may drift.</p>
   ${passBlock('summary', A.summary, { compact: true })}${grayTable(A.summary, true)}</div>
  ${notes('summary')}`;
}

// ============================================================ charts (SVG)
function pqEotf(e) {
  const m1 = 2610 / 16384, m2 = 2523 / 4096 * 128, c1 = 3424 / 4096, c2 = 2413 / 4096 * 32, c3 = 2392 / 4096 * 32;
  const ep = Math.pow(Math.max(e, 0), 1 / m2);
  return 10000 * Math.pow(Math.max(ep - c1, 0) / (c2 - c3 * ep), 1 / m1);
}
function bt1886(V, Lw, Lb) {
  const g = 2.4, a = Math.pow(Math.pow(Lw, 1 / g) - Math.pow(Lb, 1 / g), g), b = Math.pow(Lb, 1 / g) / (Math.pow(Lw, 1 / g) - Math.pow(Lb, 1 / g));
  return a * Math.pow(Math.max(V + b, 0), g);
}
function niceTicks(max) {
  const raw = max / 5, p = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(k => k * p).find(v => v >= raw);
  const out = [];
  for (let v = 0; v <= max; v += step) out.push(+v.toFixed(6));
  return out;
}
function gammaChart(a, gamma, log) {
  const sm = a.summary, Yw = sm.whiteY;
  if (!Yw) return '<p class="muted">White (100%) is needed — measure SDR_01 or fix it in step 2.</p>';
  const W = 640, H = 260, L = 50, R = 12, T = 10, B = 30;
  const Yb = isNum(sm.blackY) ? sm.blackY : 0;
  const tgt = s => gamma === 'bt1886' ? bt1886(s, Yw, Math.max(Yb, 1e-4)) : Yw * Math.pow(s, parseFloat(gamma));
  const pts = a.rows.filter(r => r.measured && isNum(r.Y));
  const minY = log ? Math.max(1e-3, Math.min(Yw * 1e-3, ...pts.filter(p => p.Y > 0).map(p => p.Y)) * 0.8) : 0;
  const maxY = Yw * 1.08;
  const X = s => L + s / 100 * (W - L - R);
  const Yf = y => log ? T + (1 - (Math.log10(Math.max(y, minY)) - Math.log10(minY)) / (Math.log10(maxY) - Math.log10(minY))) * (H - T - B)
    : T + (1 - y / maxY) * (H - T - B);
  let g = '';
  for (let s = 0; s <= 100; s += 10) g += `<line class="grid" x1="${X(s)}" x2="${X(s)}" y1="${T}" y2="${H - B}"/><text x="${X(s) - 8}" y="${H - 12}">${s}%</text>`;
  const ticks = log ? [0.01, 0.1, 1, 10, 100, 1000].filter(v => v >= minY && v <= maxY) : niceTicks(maxY);
  const tf = v => v === 0 ? '0' : v >= 10 ? v.toFixed(0) : v >= 1 ? String(+v.toFixed(1)) : String(+v.toPrecision(2));
  ticks.forEach(v => g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${Yf(v)}" y2="${Yf(v)}"/><text x="4" y="${Yf(v) + 4}">${tf(v)}</text>`);
  let tp = '';
  for (let i = log ? 1 : 0; i <= 100; i++) tp += (tp ? 'L' : 'M') + X(i) + ' ' + Yf(tgt(i / 100));
  const mp = pts.slice().sort((p, q) => p.stim - q.stim).filter(p => !log || p.Y > 0);
  const mpath = mp.map((p, i) => (i ? 'L' : 'M') + X(p.stim) + ' ' + Yf(p.Y)).join('');
  const dots = mp.map(p => `<circle class="pt" cx="${X(p.stim)}" cy="${Yf(p.Y)}" r="3.5"><title>${p.stim}%: ${fmtY(p.Y)} nits (target ${fmtY(p.Yt)})</title></circle>`).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}">${g}<line class="ax" x1="${L}" x2="${L}" y1="${T}" y2="${H - B}"/>
    <path class="tgt" d="${tp}"/><path class="meas" d="${mpath}"/>${dots}</svg>
    <p class="small muted">Dashed — target (${gamma === 'bt1886' ? 'BT.1886' : 'γ ' + gamma}, from the measured white), solid — measured. Y, nits.</p>`;
}
function devChart(a) {
  const rows = a.rows.filter(r => r.measured && isNum(r.dev) && r.stim > 0 && r.stim < 100);
  if (!rows.length) return '';
  const W = 640, H = 150, L = 40, T = 10, B = 24, lim = 20;
  const X = s => L + s / 100 * (W - L - 12);
  const Y = d => T + (1 - (Math.max(-lim, Math.min(lim, d)) + lim) / (2 * lim)) * (H - T - B);
  let g = [-20, -10, 0, 10, 20].map(v => `<line class="grid" x1="${L}" x2="${W - 12}" y1="${Y(v)}" y2="${Y(v)}"/><text x="2" y="${Y(v) + 4}">${v > 0 ? '+' : ''}${v}%</text>`).join('');
  const bw = 14;
  const bars = rows.map(r => { const y0 = Y(0), y1 = Y(r.dev); const col = r.gLevel === 'bad' ? 'var(--bad)' : r.gLevel === 'ok' ? 'var(--ok)' : 'var(--good)';
    return `<rect x="${X(r.stim) - bw / 2}" y="${Math.min(y0, y1)}" width="${bw}" height="${Math.max(1, Math.abs(y1 - y0))}" fill="${col}" opacity=".85"><title>${r.stim}%: ${fmtS(r.dev, 1)}%</title></rect><text x="${X(r.stim) - 9}" y="${H - 6}">${r.stim}</text>`; }).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" style="margin-top:8px">${g}${bars}</svg><p class="small muted">Y deviation from the target by step (±20%), color by γ eff.</p>`;
}
const LOCUS = [[0.1741, 0.0050], [0.1733, 0.0048], [0.1714, 0.0051], [0.1644, 0.0109], [0.1566, 0.0177], [0.1440, 0.0297], [0.1241, 0.0578], [0.1096, 0.0868], [0.0913, 0.1327], [0.0687, 0.2007], [0.0454, 0.2950], [0.0235, 0.4127], [0.0082, 0.5384], [0.0039, 0.6548], [0.0139, 0.7502], [0.0389, 0.8120], [0.0743, 0.8338], [0.1142, 0.8262], [0.1547, 0.8059], [0.2296, 0.7543], [0.3016, 0.6923], [0.3731, 0.6245], [0.4441, 0.5547], [0.5125, 0.4866], [0.5752, 0.4242], [0.6270, 0.3725], [0.6658, 0.3340], [0.6915, 0.3083], [0.7079, 0.2920], [0.7190, 0.2809], [0.7260, 0.2740], [0.7347, 0.2653]];
function xyChart(a) {
  const W = 420, H = 440, L = 30, T = 8, B = 26, R = 8;
  const X = x => L + x / 0.8 * (W - L - R), Y = y => T + (1 - y / 0.9) * (H - T - B);
  let g = '';
  for (let v = 0; v <= 0.8001; v += 0.1) g += `<line class="grid" x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${H - B}"/><text x="${X(v) - 8}" y="${H - 8}">${v.toFixed(1)}</text>`;
  for (let v = 0; v <= 0.9001; v += 0.1) g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="2" y="${Y(v) + 4}">${v.toFixed(1)}</text>`;
  const locus = LOCUS.map((p, i) => (i ? 'L' : 'M') + X(p[0]) + ' ' + Y(p[1])).join('') + 'Z';
  const tri = [[0.64, 0.33], [0.30, 0.60], [0.15, 0.06]].map((p, i) => (i ? 'L' : 'M') + X(p[0]) + ' ' + Y(p[1])).join('') + 'Z';
  const rows = a.rows.filter(r => r.measured || r.amp === 1);
  const marks = rows.map(r => {
    const c = HUE_COLOR[r.hue], op = r.amp === 1 ? 1 : 0.6;
    let s = `<rect x="${X(r.tx) - 4}" y="${Y(r.ty) - 4}" width="8" height="8" fill="none" stroke="${c}" stroke-width="1.5" opacity="${op}"/>`;
    if (r.measured) s += `<line x1="${X(r.tx)}" y1="${Y(r.ty)}" x2="${X(r.x)}" y2="${Y(r.y)}" stroke="${c}" opacity="${op}"/><circle cx="${X(r.x)}" cy="${Y(r.y)}" r="4" fill="${c}" opacity="${op}"><title>${HUE_NAME[r.hue]}: ΔE ${fmt(r.de, 2)}</title></circle>`;
    return s;
  }).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" style="max-width:520px">${g}<path class="locus" d="${locus}"/><path class="rec709" d="${tri}"/>
    <circle cx="${X(0.3127)}" cy="${Y(0.329)}" r="3" fill="var(--text)"/>${marks}</svg>`;
}
function hdrChart(a) {
  const rows = a.rows;
  const W = 640, H = 260, L = 50, R = 12, T = 10, B = 30;
  const minY = 0.05, maxY = 10000;
  const X = s => L + s / 100 * (W - L - R);
  const Yf = y => T + (1 - (Math.log10(Math.max(y, minY)) - Math.log10(minY)) / (Math.log10(maxY) - Math.log10(minY))) * (H - T - B);
  let g = '';
  for (let s = 0; s <= 100; s += 10) g += `<line class="grid" x1="${X(s)}" x2="${X(s)}" y1="${T}" y2="${H - B}"/><text x="${X(s) - 8}" y="${H - 12}">${s}%</text>`;
  [0.1, 1, 10, 100, 1000, 10000].forEach(v => g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${Yf(v)}" y2="${Yf(v)}"/><text x="4" y="${Yf(v) + 4}">${v}</text>`);
  let tp = '';
  for (let i = 5; i <= 100; i++) tp += (tp ? 'L' : 'M') + X(i) + ' ' + Yf(pqEotf(i / 100));
  const mp = rows.filter(r => r.measured && r.Y > 0).sort((p, q) => p.stim - q.stim);
  const mpath = mp.map((p, i) => (i ? 'L' : 'M') + X(p.stim) + ' ' + Yf(p.Y)).join('');
  const dots = mp.map(p => `<circle class="pt" cx="${X(p.stim)}" cy="${Yf(p.Y)}" r="3.5"><title>PQ ${p.stim}%: ${fmtY(p.Y)} nits (target ${fmt(p.tnits, 1)})</title></circle>`).join('');
  const peak = a.summary.peak ? `<line x1="${L}" x2="${W - R}" y1="${Yf(a.summary.peak)}" y2="${Yf(a.summary.peak)}" stroke="var(--ok)" stroke-dasharray="2 4"/>` : '';
  return `<svg class="chart" viewBox="0 0 ${W} ${H}">${g}<line class="ax" x1="${L}" x2="${L}" y1="${T}" y2="${H - B}"/><path class="tgt" d="${tp}"/>${peak}<path class="meas" d="${mpath}"/>${dots}</svg>
    <p class="small muted">Dashed — PQ target (ST.2084), solid — measured, yellow line — projector peak. Below the roll-off start the luminance should match the target.</p>`;
}

// ============================================================ settings
function settingsModal() {
  const st = S.sv.settings, s = sess(), pp = s.projectorProfile || {};
  const opt = (v, cur, lab) => `<option value="${v}" ${String(cur) === String(v) ? 'selected' : ''}>${lab}</option>`;
  const sessions = S.sv.sessions.map(x => `<tr><td class="l">${esc(x.name)}${x.id === s.id ? ' <span class="pill info">current</span>' : ''}</td><td class="muted small">${esc((x.updatedAt || '').replace('T', ' ').slice(0, 16))}</td><td>${x.count}</td>
    <td>${x.id !== s.id ? `<button class="btn" data-act="openSession" data-arg="${x.id}">Open</button>` : ''}</td></tr>`).join('');
  return `<div class="modal-bg"><div class="modal" id="settingsForm" style="width:min(820px,96vw)">
  <h2>Settings</h2>
  <h3>Meter</h3>
  <div class="grid2">
   <label>Reading source<br><select id="sMode">${opt('argyll', st.meterMode, 'ArgyllCMS spotread (interactive)')}${opt('oneshot', st.meterMode, 'ArgyllCMS spotread -O (one reading per launch, fallback)')}${opt('mock', st.meterMode, 'Projector simulation (no meter)')}</select></label>
   <label>Path to spotread (empty = find automatically)<br><input type="text" id="sPath" value="${esc(st.spotreadPath)}" placeholder="/opt/homebrew/bin/spotread" style="width:100%"></label>
   <label>Integration mode (-Y)<br><select id="sRefresh">${opt('', st.refresh, 'auto (default)')}${opt('n', st.refresh, 'n — non-refresh')}${opt('r', st.refresh, 'r — refresh')}</select></label>
   <label>Correction .ccmx / .ccss (-X)<br><input type="text" id="sCcmx" value="${esc(st.ccmx)}" placeholder="~/calib/laser.ccss" style="width:100%"></label>
   <label>Mode flag<br><input type="text" id="sFlag" value="${esc(st.flag)}" style="width:80px"> <span class="small muted">-e (emissive). Change only if there are problems.</span></label>
   <label>Extra spotread arguments<br><input type="text" id="sExtra" value="${esc(st.extraArgs)}" style="width:100%"></label>
   <label>Averaging (readings per point)<br><input type="number" id="sAvg" min="1" max="10" value="${st.avg}" style="width:80px"></label>
   <label>Y stability threshold, %<br><input type="number" id="sStab" min="0.2" max="5" step="0.1" value="${st.stabilityPct}" style="width:80px"> <span class="small muted">for Y ≥ 1 nit; wider on dark patches automatically</span></label>
   <label class="chk"><input type="checkbox" id="sAuto" ${st.autoDefault ? 'checked' : ''}> Auto capture in passes by default</label>
   <label>Theme<br><select id="sTheme">${opt('dark', st.theme, 'Dark (for calibration)')}${opt('light', st.theme, 'Light')}${opt('auto', st.theme, 'System')}</select></label>
  </div>
  <h3 style="margin-top:16px">Session and profile</h3>
  <div class="grid2">
   <label>Session name<br><input type="text" id="sName" value="${esc(s.name)}" style="width:100%"></label>
   <label>SDR target gamma<br><select id="sGamma">${opt('2.2', s.gamma, '2.2')}${opt('2.3', s.gamma, '2.3')}${opt('2.4', s.gamma, '2.4 (dark room)')}${opt('bt1886', s.gamma, 'BT.1886')}</select></label>
   <label>Projector<br><input type="text" id="pName" value="${esc(pp.name || '')}" style="width:100%"></label>
   <label>Screen: diagonal, inches / gain (for reference)<br><input type="number" id="pSize" value="${pp.screenSizeInches || ''}" style="width:90px"> <input type="number" step="0.05" id="pGain" value="${pp.screenGain || ''}" style="width:90px"></label>
   <label style="grid-column:1/-1">Notes (screen, player…)<br><textarea id="pNotes">${esc(pp.notes || '')}</textarea></label>
  </div>
  <div class="row" style="margin-top:12px"><button class="btn primary big" data-act="saveSettings">Save</button><button class="btn" data-act="closeSettings">Cancel</button></div>
  <h3 style="margin-top:20px">Sessions</h3>
  <div class="tablewrap"><table class="t"><tr><th class="l">Name</th><th>Modified</th><th>Readings</th><th></th></tr>${sessions}</table></div>
  <div class="row" style="margin-top:8px"><button class="btn" data-act="newSession">New session</button>
   <button class="btn" data-act="reveal" data-arg="sessions">Sessions folder</button><button class="btn" data-act="showLog">Meter log</button>
   <span style="flex:1"></span><button class="btn danger" data-act="quit">Quit the program</button></div>
  <p class="small muted">ProjectorCal works offline, with no accounts and no telemetry. Data: ~/Library/Application Support/ProjectorCal.</p>
  </div></div>`;
}

// ============================================================ report
function renderReport() {
  $('#app').hidden = true;
  const el = $('#report');
  el.hidden = false;
  const s = sess(), A = S.sv.analysis;
  const date = new Date().toLocaleString('en-GB');
  const g = A.verify.summary.measuredCount ? A.verify : A.gray;
  const mvTable = mode => `<table class="t">${Object.entries(s.menuValues[mode]).filter(([, v]) => v !== '').map(([k, v]) => `<tr><td class="l">${esc(k)}</td><td class="l"><b>${esc(v)}</b></td></tr>`).join('')}</table>`;
  const wbRows = A.gray.rows.filter(r => r.measured && r.bal).map(r => `<tr><td class="l">${r.stim}%</td><td>${fmtY(r.Y)}</td>${r.bal.map(b => `<td>${fmt(b, 1)}</td>`).join('')}<td>${isNum(r.cct) ? r.cct.toFixed(0) : '—'}</td></tr>`).join('');
  const section = (title, body) => `<div class="card"><h2>${title}</h2>${body}</div>`;
  el.innerHTML = `
  <div class="row noprint" style="margin-bottom:16px"><button class="btn" data-act="closeReport">← Back</button><button class="btn primary" data-act="print">Print / PDF</button>
   <span class="small muted">In the print dialog: PDF → “Save as PDF”.</span></div>
  <h1>Calibration report: ${esc((s.projectorProfile || {}).name || 'Vidda C3S')}</h1>
  <p class="muted">${esc(s.name)} · created ${esc(date)} · ProjectorCal</p>
  ${section('Conditions', `<table class="t">
    <tr><td class="l">Source</td><td class="l">player → HDMI → projector; patches from Plex or USB</td></tr>
    <tr><td class="l">Screen</td><td class="l">${esc((s.projectorProfile || {}).notes || '')}${s.projectorProfile && s.projectorProfile.screenSizeInches ? ` · ${s.projectorProfile.screenSizeInches}″` : ''}${s.projectorProfile && s.projectorProfile.screenGain ? ` · gain ${s.projectorProfile.screenGain}` : ''}</td></tr>
    <tr><td class="l">Meter</td><td class="l">ArgyllCMS spotread ${esc((s.meter || {}).flag || '-e')}${(s.meter || {}).refresh ? ' -Y ' + esc(s.meter.refresh) : ''}${(s.meter || {}).ccmx ? ' · correction ' + esc(s.meter.ccmx) : ' · no spectral correction'}</td></tr>
    <tr><td class="l">Laser level / white</td><td class="l">${esc(s.laserLevel || '—')} / ${s.whiteY ? fmt(s.whiteY, 2) + ' nits' : '—'}</td></tr>
    <tr><td class="l">SDR target</td><td class="l">D65, ${s.gamma === 'bt1886' ? 'BT.1886' : 'gamma ' + esc(s.gamma)} from the measured white, Rec.709</td></tr></table>`)}
  ${section('White balance (last gray circle) ' + verdictPill(A.gray.summary.verdict), wbRows ? `<table class="t"><tr><th class="l">Step</th><th>Y, nits</th><th>R</th><th>G</th><th>B</th><th>CCT</th></tr>${wbRows}</table>` : '<p class="muted">no data</p>')}
  ${section(`SDR gray scale (${A.verify.summary.measuredCount ? 'final verification' : 'last circle'}) ` + verdictPill(g.summary.verdict),
    `<p>Color ΔE 10–90%: mean <b>${fmt(g.summary.deMean, 2)}</b>, max <b>${fmt(g.summary.deMax, 2)}</b> · γ eff. <b>${fmt(g.summary.gMean, 2)}</b>${g.summary.contrast ? ` · contrast ${g.summary.contrast.toFixed(0)}:1` : ''}</p>${gammaChart(g, s.gamma, false)}${grayTable(g, false)}`)}
  ${A.color.summary.count ? section('SDR colors ' + verdictPill(A.color.summary.verdict), `<p>ΔE2000 of the 6 primaries: mean <b>${fmt(A.color.summary.deMean, 2)}</b>, max <b>${fmt(A.color.summary.deMax, 2)}</b>. Colorimeter without spectral correction — read colors as a trend.</p>${xyChart(A.color)}${colorTable(A.color, false)}`) : ''}
  ${A.hdr.summary.count ? section('HDR10 ' + verdictPill(A.hdr.summary.verdict), `<p>Peak ≈ <b>${fmt(A.hdr.summary.peak, 0)} nits</b>${isNum(A.hdr.summary.scale) ? `; shadows and mids ≈ ${A.hdr.summary.scale.toFixed(0)}% of the PQ target luminance` : ''}${A.hdr.summary.rolloff ? `; extra compression from PQ ${A.hdr.summary.rolloff.stim}%` : ''}.</p>${hdrChart(A.hdr)}${hdrTable(A.hdr, false)}`) : ''}
  ${section('SDR menu values', mvTable('sdr'))}
  ${section('HDR10 menu values', mvTable('hdr10'))}
  ${section('Steps', `<table class="t">${STEPS.map(st => `<tr><td class="l">${st.title}</td><td class="l">${STATUS_NAME[s.steps[st.id].status]}</td><td class="l small">${esc(s.steps[st.id].notes || '')}</td></tr>`).join('')}</table>`)}
  ${s.events.length ? section('Events', `<table class="t">${s.events.slice(-30).map(e => `<tr><td class="l small muted">${esc(e.t.replace('T', ' ').slice(0, 16))}</td><td class="l">${esc(e.text)}</td></tr>`).join('')}</table>`) : ''}`;
}

// ============================================================ actions
const ACT = {
  goto: el => act('/api/goto', { stepId: el.dataset.arg }),
  stepDone: async el => {
    const id = el.dataset.arg, i = stepIdx(id);
    await api('/api/step', { stepId: id, status: 'done' }).catch(e => localNotice('bad', e.message));
    if (i < STEPS.length - 1) await act('/api/goto', { stepId: STEPS[i + 1].id }); else await poll(true);
  },
  stepSkip: async el => {
    const id = el.dataset.arg, i = stepIdx(id);
    await api('/api/step', { stepId: id, status: 'skipped' }).catch(e => localNotice('bad', e.message));
    await act('/api/goto', { stepId: STEPS[i + 1].id });
  },
  passAuto: el => act('/api/pass', { stepId: el.dataset.step, action: 'auto', arg: el.dataset.arg === '1' }),
  passMeasure: el => act('/api/pass', { stepId: el.dataset.arg, action: 'measure' }),
  passSkip: el => act('/api/pass', { stepId: el.dataset.arg, action: 'skip' }),
  passBack: el => act('/api/pass', { stepId: el.dataset.arg, action: 'back' }),
  passNew: el => { if (confirm('Start a new pass? Previous readings are kept for comparison.')) act('/api/pass', { stepId: el.dataset.arg, action: 'new' }); },
  passAccept: el => act('/api/pass', { stepId: el.dataset.arg, action: 'accept' }),
  passSelect: el => act('/api/pass', { stepId: el.dataset.step, action: 'select', arg: +el.dataset.arg }),
  fullStart: el => { S.ui.fullTab = el.dataset.arg; act('/api/full/start', { mode: el.dataset.arg }); },
  fullStop: el => act('/api/full/stop', { save: el.dataset.arg === '1' }),
  fullReanalyze: el => act('/api/full/reanalyze', { run: el.dataset.arg }),
  fullTab: el => { S.ui.fullTab = el.dataset.arg; renderStep(true); },
  psToggle: () => { S.ui.psOpen = !S.ui.psOpen; renderStep(true); },
  verdictApply: el => {
    const v = an('full').latest[el.dataset.arg].verdict, changes = {};
    (v.set || []).forEach(x => { changes[x.path] = x.to; });
    if (confirm('Save all ' + v.set.length + ' values from the verdict to the settings? Do this only if you have already set them in the projector menu.')) act('/api/psettings', { changes });
  },
  planApply: el => {
    const [mode, i] = el.dataset.arg.split(':');
    const it = an('full').latest[mode].plan[+i], changes = {};
    (it.set || []).forEach(x => { changes[x.path] = x.to; });
    act('/api/psettings', { changes });
  },
  gpStart: () => { if (confirm('Are all 20 points set to 0 in the menu?')) act('/api/gp', { action: 'start', preset: $('#gpPreset').value.trim() }); },
  gpContinue: () => {
    const values = {};
    document.querySelectorAll('.gpv').forEach(i => { values[i.dataset.gpk] = +i.value || 0; });
    act('/api/gp', { action: 'continue', preset: $('#gpPreset').value.trim(), values });
  },
  gpContinueQuick: () => act('/api/gp', { action: 'continue' }),
  gpPlan: () => act('/api/gp', { action: 'plan' }),
  gpApplied: () => act('/api/gp', { action: 'applied' }),
  gpFinish: () => act('/api/gp', { action: 'finish' }),
  gpProbe: () => act('/api/gp', { action: 'probe', point: +$('#gpProbeK').value, delta: +$('#gpProbeD').value }),
  gpProbeRestore: () => act('/api/gp', { action: 'probeRestore' }),
  gpRestart: () => { if (confirm('Start the gamma over?')) act('/api/gp', { action: 'reset' }); },
  grayLive: el => act('/api/graylive', { patchId: el.dataset.arg }),
  grayLiveOff: () => act('/api/graylive', { patchId: null }),
  measureTest: () => act('/api/measure', { purpose: 'test' }),
  measureAmbient: () => act('/api/measure', { purpose: 'ambient' }),
  measureNow: () => {
    const st = S.state, a = st.activity;
    if (a && a.type === 'pass') return act('/api/pass', { stepId: a.stepId, action: 'measure' });
    return ACT.measureTest();
  },
  fixWhite: () => {
    const lvl = $('#laserLevel').value.trim();
    if (!lvl && !confirm('The laser level is not set. Fix the white only?')) return;
    act('/api/measure', { purpose: 'fixwhite', laserLevel: lvl });
  },
  setGamma: el => act('/api/fields', { gamma: el.dataset.arg }),
  freeLive: () => act('/api/live', { on: !(S.state.activity && S.state.activity.type === 'free') }),
  retry: () => act('/api/meter/retry', {}),
  calibContinue: () => act('/api/meter/continue', {}),
  dismissDrift: () => act('/api/dismiss', { what: 'drift' }),
  acceptWhite: () => act('/api/dismiss', { what: 'acceptWhite' }),
  dismissNotice: el => {
    if (el.dataset.local) { S.localNotices = S.localNotices.filter(n => n.id !== el.dataset.arg); renderNotices(); }
    else act('/api/dismiss', { what: 'notice', id: el.dataset.arg });
  },
  runCheck: () => runCheck(),
  exportPatches: async () => {
    const dest = $('#exportDest').value.trim();
    S.ui.exportDest = dest;
    S.ui.exportResult = { dest, files: '…' };
    renderStep(true);
    try { S.ui.exportResult = await api('/api/patches/export', { dest }); }
    catch (e) { S.ui.exportResult = { error: e.message }; }
    renderStep(true);
  },
  copy: async el => {
    try { await navigator.clipboard.writeText(el.dataset.arg); localNotice('ok', 'Copied: ' + el.dataset.arg); }
    catch (e) { localNotice('warn', 'Could not copy — select the command manually.'); }
  },
  chartLog: el => { S.ui.chartLog[el.dataset.step] = el.dataset.arg === '1'; renderStep(true); },
  delMeas: el => { if (confirm('Delete this reading?')) act('/api/measurement/delete', { id: el.dataset.arg }); },
  settings: () => { S.ui.settings = true; $('#modal')._html = null; renderModal(); },
  closeSettings: () => { S.ui.settings = false; $('#modal')._html = null; renderModal(); },
  saveSettings: async () => {
    const v = id => $('#' + id).value;
    try {
      await api('/api/settings', { meterMode: v('sMode'), spotreadPath: v('sPath').trim(), refresh: v('sRefresh'), ccmx: v('sCcmx').trim(),
        flag: v('sFlag').trim() || '-e', extraArgs: v('sExtra').trim(), avg: +v('sAvg') || 3, stabilityPct: +v('sStab') || 0.7,
        autoDefault: $('#sAuto').checked, theme: v('sTheme') });
      await api('/api/fields', { name: v('sName'), gamma: v('sGamma'), projectorProfile: { name: v('pName'), screenSizeInches: +v('pSize') || null,
        screenGain: +v('pGain') || null, notes: v('pNotes') } });
      S.ui.settings = false; S.ui.check = null; $('#modal')._html = null;
      localNotice('ok', 'Settings saved.');
    } catch (e) { localNotice('bad', e.message); }
    await poll(true);
  },
  newSession: async () => {
    const name = prompt('New session name:', 'Calibration ' + new Date().toISOString().slice(0, 10));
    if (name === null) return;
    S.ui.settings = false; $('#modal')._html = null;
    await act('/api/session/new', { name });
  },
  openSession: async el => { S.ui.settings = false; $('#modal')._html = null; await act('/api/session/open', { id: el.dataset.arg }); },
  reveal: el => act('/api/reveal', { what: el.dataset.arg }),
  showLog: async () => { try { S.ui.log = await api('/api/log'); S.ui.settings = false; $('#modal')._html = null; renderModal(); } catch (e) { localNotice('bad', e.message); } },
  closeLog: () => { S.ui.log = null; renderModal(); },
  quit: async () => {
    if (!confirm('Quit ProjectorCal? The session is saved, the meter will be disconnected.')) return;
    try { await api('/api/quit', {}); } catch (e) { /* the server is already closing */ }
    document.body.innerHTML = '<div class="offline">ProjectorCal is closed. You can close this window.</div>';
  },
  report: () => { window.open('/report.html', '_blank'); },
  closeReport: () => { history.replaceState(null, '', location.pathname); renderAll(); },
  print: () => window.print(),
};

async function runCheck() {
  S.ui.checking = true; renderStep(true);
  try { S.ui.check = await api('/api/meter/check'); } catch (e) { S.ui.check = { found: false, error: e.message }; }
  S.ui.checking = false; renderStep(true);
}

document.addEventListener('click', ev => {
  const el = ev.target.closest('[data-act]');
  if (!el) return;
  const f = ACT[el.dataset.act];
  if (!f) return;
  if (el.tagName === 'A' || el.dataset.act === 'closeLog') ev.preventDefault();
  if (el.dataset.act === 'closeLog' && el.classList.contains('modal-bg') && ev.target !== el) return;
  f(el, ev);
});
document.addEventListener('change', ev => {
  const el = ev.target;
  if (el.dataset.check) return act('/api/step', { stepId: el.dataset.check, checklist: { [el.dataset.key]: el.checked } });
  if (el.dataset.opt) return act('/api/step', { stepId: el.dataset.step, [el.dataset.opt]: el.checked });
  if (el.dataset.menu) return act('/api/fields', { menuValues: { [el.dataset.menu]: { [el.dataset.key]: el.value } } });
  if (el.dataset.notes) return act('/api/step', { stepId: el.dataset.notes, notes: el.value });
  if (el.id === 'laserLevel') return act('/api/fields', { laserLevel: el.value });
  if (el.dataset.ps) return act('/api/psettings', { changes: { [el.dataset.ps]: el.value } });
  if (el.dataset.mock) return mockChange(el);
});
document.addEventListener('input', ev => {
  const el = ev.target;
  if (el.dataset.mock === 'gain' || el.dataset.mock === 'offset') {
    const sp = $(`#mk-${el.dataset.mock}-${el.dataset.i}`);
    if (sp) sp.textContent = el.value;
  }
});
document.addEventListener('focusout', () => { setTimeout(() => { if (S.pending && !editing()) renderStep(true); }, 50); });
window.addEventListener('hashchange', () => S.sv && renderAll());
function mockChange(el) {
  const mk = S.state.mock, k = el.dataset.mock, body = {};
  if (k === 'follow') body.follow = el.checked;
  else if (k === 'displayed') body.displayed = el.value;
  else if (k === 'gain' || k === 'offset') { const arr = mk[k].slice(); arr[+el.dataset.i] = +el.value; body[k] = arr; }
  else body[k] = +el.value;
  act('/api/mock', body);
}

loop();

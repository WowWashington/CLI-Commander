/* CLI Commander XR: open sessions, new session, new project and a session view
   built for XREAL glasses + Beam Pro (pointer ray, on-screen keyboard). */
(() => {
const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const api = async (method, path, body) => {
  const r = await fetch(path, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(method !== 'GET' ? { 'X-Blades-Request': '1' } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) { const t = await r.text(); let m = t; try { m = JSON.parse(t).error || JSON.parse(t).message || t; } catch (_) {} throw new Error(m.replace(/<[^>]*>/g, '').trim().slice(0, 240) || r.statusText); }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
};
const store = { get(k) { try { return localStorage.getItem(k) || ''; } catch (_) { return ''; } }, set(k, v) { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (_) {} } };
const toast = m => { const t = $('#xrToast'); t.textContent = m; t.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 2600); };
const providerLabel = b => b.provider === 'shell' ? 'Shell' : b.provider === 'codex' ? 'Codex' : b.provider === 'gemini' ? 'Gemini' : 'Claude';
const stateLabel = b => ({ busy: 'Running', attention: 'Needs you', idle: 'Idle', closing: 'Saving', starting: 'Starting', unknown: 'Active' }[b.state] || b.state);
const ORDER = { attention: 0, busy: 1, starting: 2, unknown: 3, idle: 4, closing: 5 };
const age = s => s < 3600 ? Math.max(1, Math.round(s / 60)) + 'm' : s < 86400 ? Math.round(s / 3600) + 'h' : Math.round(s / 86400) + 'd';

const S = { view: 'home', blades: [], projects: [], providers: [], enabled: [], locations: [], home: '', homePage: 0, newPage: 0, session: null, term: null, projGit: true, projLocation: '' };

/* ---------- navigation ---------- */
function go(view, arg) {
  if (view !== 'session') closeTerm();
  S.view = view;
  for (const id of ['Home', 'New', 'Project', 'Session', 'Talk']) $('#view' + id).hidden = id.toLowerCase() !== view;
  if (view === 'home') { location.hash = ''; renderHome(); }
  if (view === 'new') { location.hash = 'new'; S.newPage = 0; renderNew(); }
  if (view === 'project') { location.hash = 'project'; openProjectForm(); }
  if (view === 'session') { location.hash = 'session/' + encodeURIComponent(arg); S.lastSession = arg; openSession(arg); }
  if (view === 'talk') { location.hash = 'talk'; openTalk(); }
  requestAnimationFrame(() => $('#view' + view[0].toUpperCase() + view.slice(1)).querySelector('button, input, textarea')?.focus({ preventScroll: true }));
}
document.addEventListener('click', e => { const b = e.target.closest('[data-go]'); if (b) go(b.dataset.go); });
$('#xrHome').onclick = () => go('home');

/* ---------- data ---------- */
async function loadBlades() { const d = await api('GET', '/api/blades'); S.blades = d.live.sort((a, b) => (ORDER[a.state] ?? 3) - (ORDER[b.state] ?? 3) || a.project.localeCompare(b.project)); renderSummary(); }
async function loadProjects() { S.projects = (await api('GET', '/api/projects')).filter(p => p.exists !== false && !p.hidden && p.slug !== 'shell-commands'); }
async function loadProviders() { const d = await api('GET', '/api/onboarding'); S.providers = d.providers || []; S.enabled = d.completed ? d.enabled_providers : []; }
async function loadStatus() {
  try {
    // The viewport in the query is only for tuning layout defaults (visible in the server log).
    const st = await api('GET', `/api/status?xr=${innerWidth}x${innerHeight}@${devicePixelRatio}`), n = st.instance_name || '', node = $('#xrInstance');
    // Voice needs a secure page: move to the HTTPS address (same view) when one is published.
    if (!window.isSecureContext && st.https_url) { location.replace(st.https_url + '/xr' + location.hash); return; }
    node.textContent = n; node.hidden = !n; node.dataset.kind = /prod/i.test(n) ? 'prod' : /dev/i.test(n) ? 'dev' : '';
    document.title = (n ? n + ' · ' : '') + 'CLI Commander XR';
    if (st.kbdebug && !S.diag) startDiagnostics();
  } catch (_) {}
}
function renderSummary() {
  const need = S.blades.filter(b => b.state === 'attention').length;
  $('#xrSummary').textContent = `${S.blades.length} open${need ? ` · ${need} need${need === 1 ? 's' : ''} you` : ''}`;
}

/* ---------- paging (no scrolling with a pointer ray) ---------- */
function pager(box, page, pages, onPage) {
  box.innerHTML = ''; if (pages <= 1) return;
  const prev = el('button', 'xr-btn', '‹ Previous'); prev.disabled = page === 0; prev.onclick = () => onPage(page - 1);
  const next = el('button', 'xr-btn', 'Next ›'); next.disabled = page >= pages - 1; next.onclick = () => onPage(page + 1);
  box.append(prev, el('span', '', `${page + 1} / ${pages}`), next);
}
// Items per page = columns the grid lays out × rows that fit its height.
function capacity(grid, itemHeight) {
  const cols = Math.max(1, getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length);
  const gap = parseFloat(getComputedStyle(grid).rowGap) || 0, height = grid.clientHeight || innerHeight * .5;
  return cols * Math.max(1, Math.floor((height + gap) / (itemHeight + gap)));
}
const unit = name => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--tap')) * (name === 'card' ? 1.9 : 1.35) || 80;

/* ---------- size (per device) ---------- */
const scale = () => Math.min(1.8, Math.max(0.6, Number(store.get('xr.scale')) || 1));
function applyScale(next) {
  if (next != null) store.set('xr.scale', String(Math.round(next * 10) / 10));
  document.documentElement.style.setProperty('--scale', scale());
  $('#xrSmaller').disabled = scale() <= 0.6; $('#xrLarger').disabled = scale() >= 1.8;
  if (S.term) { S.term.term.options.fontSize = termFont(); S.term.onResize(); }
  if (S.view === 'home') renderHome(); if (S.view === 'new') renderNew();
}
const termFont = () => Math.max(10, Math.round(innerHeight / 50 * scale()));
$('#xrSmaller').onclick = () => applyScale(scale() - 0.1);
$('#xrLarger').onclick = () => applyScale(scale() + 0.1);

/* ---------- home ---------- */
function renderHome() {
  if (S.view !== 'home') return;
  const grid = $('#homeGrid'), size = capacity(grid, unit('card'));
  const pages = Math.max(1, Math.ceil(S.blades.length / size)); S.homePage = Math.min(S.homePage, pages - 1);
  grid.innerHTML = ''; $('#homeEmpty').hidden = !!S.blades.length;
  for (const b of S.blades.slice(S.homePage * size, (S.homePage + 1) * size)) {
    const c = el('button', 'xr-card'); c.dataset.state = b.state;
    const s = el('span', 's'); s.append(el('span', 'dot'), document.createTextNode(`${providerLabel(b)} · ${stateLabel(b)} · ${age(b.age_sec)}`));
    c.append(el('span', 't', b.project), s); if (b.note) c.append(el('span', 'n', b.note));
    c.onclick = () => go('session', b.name); grid.append(c);
  }
  pager($('#homePager'), S.homePage, pages, p => { S.homePage = p; renderHome(); });
}

/* ---------- new session ---------- */
function renderNew() {
  const open = new Set(S.blades.map(b => b.slug));
  const list = [...S.projects].sort((a, b) => (open.has(b.slug) - open.has(a.slug)) || String(b.modified || '').localeCompare(String(a.modified || '')) || a.name.localeCompare(b.name));
  const size = capacity($('#newGrid'), unit('tile'));
  const pages = Math.max(1, Math.ceil(list.length / size)); S.newPage = Math.min(S.newPage, pages - 1);
  const grid = $('#newGrid'); grid.innerHTML = '';
  for (const p of list.slice(S.newPage * size, (S.newPage + 1) * size)) {
    const t = el('button', 'xr-tile'); const live = S.blades.filter(b => b.slug === p.slug).length;
    t.append(el('span', 't', p.name), el('span', 'n', live ? `${live} open` : p.modified ? `updated ${p.modified}` : ' '));
    t.onclick = () => pickAssistant(p); grid.append(t);
  }
  if (!list.length) grid.append(el('p', 'xr-empty', 'No projects yet. Use + New project.'));
  pager($('#newPager'), S.newPage, pages, p => { S.newPage = p; renderNew(); });
}
function pickAssistant(project) {
  const box = $('#pickChoices'); box.innerHTML = '';
  $('#pickProject').textContent = project.name;
  const choices = S.providers.filter(p => S.enabled.includes(p.id) && p.available);
  if (!choices.length) box.append(el('p', 'xr-hint', 'No assistants are enabled. Finish setup in the standard view first.'));
  for (const p of choices) {
    const b = el('button', 'xr-choice'); b.append(document.createTextNode(p.id === 'codex' ? 'Codex' : p.label), el('small', '', p.auth?.state === 'ready' ? 'Signed in' : 'Check sign-in'));
    b.onclick = () => launch(project.slug, { provider: p.id }, b); box.append(b);
  }
  const sh = el('button', 'xr-choice'); sh.append(document.createTextNode('Shell'), el('small', '', 'Plain terminal')); sh.onclick = () => launch(project.slug, { shell: true }, sh); box.append(sh);
  $('#pick').hidden = false; box.querySelector('button')?.focus();
}
$('#pickCancel').onclick = () => { $('#pick').hidden = true; };
async function launch(slug, opts, button) {
  button.disabled = true;
  try { const b = await api('POST', '/api/blades', { slug, ...opts }); $('#pick').hidden = true; await loadBlades(); go('session', b.name); }
  catch (e) { toast('Could not open: ' + e.message); button.disabled = false; }
}

/* ---------- new project ---------- */
async function openProjectForm() {
  $('#projError').hidden = true; $('#projName').value = ''; S.projGit = true; renderGit();
  try { const d = await api('GET', '/api/locations'); S.home = d.home; S.locations = d.locations.map(l => l.path); } catch (_) { S.locations = []; }
  S.projLocation = S.locations[0] || ''; renderLocations(); preview();
}
const tilde = p => S.home && p.startsWith(S.home) ? '~' + p.slice(S.home.length) : p;
function renderLocations() {
  const box = $('#projLocations'); box.innerHTML = '';
  if (!S.locations.length) box.append(el('p', 'xr-hint', 'No code locations set. The project goes in ~/Projects.'));
  for (const p of S.locations) {
    const b = el('button', 'xr-btn xr-choice', tilde(p)); b.setAttribute('aria-pressed', p === S.projLocation);
    b.onclick = () => { S.projLocation = p; renderLocations(); preview(); }; box.append(b);
  }
}
function renderGit() { const b = $('#projGit'); b.setAttribute('aria-pressed', S.projGit); b.textContent = `Git repository: ${S.projGit ? 'On' : 'Off'}`; }
$('#projGit').onclick = () => { S.projGit = !S.projGit; renderGit(); };
function preview() {
  const name = $('#projName').value.trim().replace(/[\/\\:]+/g, '-').trim();
  $('#projPreview').textContent = name ? `Creates ${tilde(S.projLocation || S.home + '/Projects')}/${name}` : '';
}
$('#projName').oninput = preview;
$('#projName').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); $('#projCreate').click(); } };
$('#projCreate').onclick = async () => {
  const name = $('#projName').value.trim(), btn = $('#projCreate');
  if (!name) { $('#projError').textContent = 'Enter a project name.'; $('#projError').hidden = false; return; }
  btn.disabled = true; $('#projError').hidden = true;
  try {
    const p = await api('POST', '/api/projects', { create: true, name, git: S.projGit, ...(S.projLocation ? { parent: S.projLocation } : {}) });
    await loadProjects(); toast(`Created ${tilde(p.path)}`); go('new'); pickAssistant(p);
  } catch (e) { $('#projError').textContent = e.message; $('#projError').hidden = false; }
  finally { btn.disabled = false; }
};

/* ---------- session view ---------- */
// Green mode: plain green text on a transparent background (black is see-through
// on the glasses). CLIs set their own colours, so SGR colour/background codes are
// removed from the output stream; bold, underline and inverse are kept.
const greenOn = () => store.get('xr.green') === '1';
const THEME = { background: '#000000', foreground: '#e8edf3', cursor: '#5ad1b4' };
const GREEN = { background: 'rgba(0,0,0,0)', foreground: '#39ff7a', cursor: '#39ff7a', cursorAccent: '#000000', selectionBackground: 'rgba(57,255,122,0.35)',
  black: '#0b3d1c', red: '#39ff7a', green: '#39ff7a', yellow: '#39ff7a', blue: '#39ff7a', magenta: '#39ff7a', cyan: '#39ff7a', white: '#39ff7a',
  brightBlack: '#1f8a45', brightRed: '#8dffb2', brightGreen: '#8dffb2', brightYellow: '#8dffb2', brightBlue: '#8dffb2', brightMagenta: '#8dffb2', brightCyan: '#8dffb2', brightWhite: '#8dffb2' };
function stripColour(params) {
  if (params === '' || params === '0') return params;
  const out = [], p = params.split(';');
  for (let i = 0; i < p.length; i++) {
    const v = p[i];
    if (/^(38|48|58)[:]/.test(v)) continue;                                   // colon form 38:2::r:g:b
    if (v === '38' || v === '48' || v === '58') { i += p[i + 1] === '5' ? 2 : p[i + 1] === '2' ? 4 : 0; continue; }
    const n = Number(v);
    if ((n >= 30 && n <= 37) || (n >= 40 && n <= 47) || (n >= 90 && n <= 97) || (n >= 100 && n <= 107) || n === 39 || n === 49 || n === 59) continue;
    out.push(v);
  }
  return out.join(';');
}
function greenFilter() {
  let carry = '';
  return chunk => {
    let s = carry + chunk; carry = '';
    const tail = s.match(/\x1b(\[[0-9;:]*)?$/); if (tail) { carry = tail[0]; s = s.slice(0, -carry.length); }
    return s.replace(/\x1b\[([0-9;:]*)m/g, (m, params) => { const kept = stripColour(params); return kept === null ? '' : (kept === '' && params !== '' && params !== '0') ? '' : `\x1b[${kept}m`; });
  };
}
function applyGreen() {
  const on = greenOn(), btn = $('#sessGreen'); btn.setAttribute('aria-pressed', on); btn.textContent = on ? 'Green: On' : 'Green: Off';
  $('#sessTerm').classList.toggle('green', on);
  if (S.term) { S.term.term.options.theme = on ? GREEN : THEME; S.term.filter = on ? greenFilter() : null; S.term.reconnect(); }
}

function closeTerm() {
  const t = S.term; if (!t) return; S.term = null; t.closed = true; clearTimeout(t.timer);
  try { t.ws?.close(); } catch (_) {} t.term.dispose(); window.removeEventListener('resize', t.onResize);
}
function openSession(name) {
  closeTerm(); S.session = name; renderSessionInfo();
  const wrap = $('#sessTerm'); wrap.innerHTML = ''; wrap.classList.toggle('green', greenOn());
  const term = new Terminal({ fontFamily: 'SF Mono, Menlo, Consolas, monospace', fontSize: termFont(), theme: greenOn() ? GREEN : THEME, allowTransparency: true, cursorBlink: true, scrollback: 3000, disableStdin: true });
  const fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open(wrap);
  // Input goes through the message box and key buttons: tapping the terminal must
  // not raise the keyboard or fight the Beam Pro's input method.
  const t = { term, fit, ws: null, closed: false, timer: null, attempts: 0, filter: greenOn() ? greenFilter() : null };
  const size = () => { try { fit.fit(); if (t.ws?.readyState === WebSocket.OPEN) t.ws.send('\x00' + JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows })); } catch (_) {} };
  t.onResize = () => requestAnimationFrame(size);
  t.connect = () => {
    if (t.closed) return;
    let dims; try { dims = fit.proposeDimensions(); } catch (_) {}
    const q = dims?.cols ? `?cols=${dims.cols}&rows=${dims.rows}` : '';
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/term/${encodeURIComponent(name)}${q}`); t.ws = ws;
    ws.onmessage = e => { if (t.ws === ws) term.write(t.filter ? t.filter(e.data) : e.data); };
    ws.onopen = () => { t.attempts = 0; term.write('\x1b[!p\x1b[H\x1b[2J'); setTimeout(size, 50); };
    ws.onclose = () => { if (t.closed || t.ws !== ws) return; t.timer = setTimeout(t.connect, Math.min(10000, 800 * 2 ** t.attempts++)); };
  };
  t.reconnect = () => { const old = t.ws; t.ws = null; try { old?.close(); } catch (_) {} term.reset(); t.connect(); };
  window.addEventListener('resize', t.onResize); S.term = t; requestAnimationFrame(() => { size(); t.connect(); });
  const g = $('#sessGreen'), on = greenOn(); g.setAttribute('aria-pressed', on); g.textContent = on ? 'Green: On' : 'Green: Off';
  renderReviewSwitch();
}
function renderSessionInfo() {
  const b = S.blades.find(x => x.name === S.session);
  $('#sessTitle').textContent = b ? `${b.project} · ${providerLabel(b)}` : S.session || '';
  $('#sessState').textContent = b ? `${stateLabel(b)}${b.note ? ' · ' + b.note : ''}` : 'Session ended';
  $('#sessSave').disabled = $('#sessWind').disabled = !b || b.state !== 'idle' || b.provider === 'shell';
}
async function deliver(text, enter, fromBox) {
  try {
    await api('POST', `/api/blades/${encodeURIComponent(S.session)}/send`, { text, enter });
    if (fromBox) { $('#sessText').value = ''; store.set('xr.draft.' + S.session, ''); }
    toast(enter ? 'Sent' : 'Typed (no Enter)'); return true;
  } catch (e) { toast('Not sent: ' + e.message); return false; }
}
const reviewAll = () => store.get('xr.review') === '1';
function send(enter) {
  const text = $('#sessText').value; if (!S.session) return;
  if (!text.trim()) { toast(enter ? 'Type a message first.' : 'Type the text first; Type sends it without pressing Enter.'); $('#sessText').focus(); return; }
  // With "Always review" (e.g. keyboard dictation), nothing leaves without a check.
  if (reviewAll()) return review(text, { source: 'Message box', fromBox: true });
  const btn = enter ? $('#sessSend') : $('#sessType'); btn.disabled = true;
  deliver(text, enter, true).finally(() => { btn.disabled = false; });
}
$('#sessCompose').onsubmit = e => { e.preventDefault(); send(true); };
$('#sessType').onclick = () => send(false);

/* ---------- review: speech is never sent unchecked ---------- */
function review(text, { source, alternatives = [], confidence = null, fromBox = false } = {}) {
  S.reviewing = { text, fromBox };
  $('#reviewSource').textContent = source + (confidence != null ? ` · ${Math.round(confidence * 100)}% sure` : '');
  const shown = $('#reviewText'); shown.textContent = text; shown.classList.toggle('low', confidence != null && confidence < 0.75);
  $('#reviewTitle').textContent = confidence != null && confidence < 0.75 ? 'Did it hear you right?' : 'Send this?';
  const alts = $('#reviewAlts'); alts.innerHTML = '';
  for (const alt of alternatives.filter(a => a && a !== text).slice(0, 2)) {
    const b = el('button', 'xr-btn xr-choice', alt); b.title = 'Use this version instead';
    b.onclick = () => review(alt, { source: source + ' · alternative', fromBox }); alts.append(b);
  }
  if (alternatives.length > 1) alts.prepend(el('span', 'xr-hint', 'Or did you say:'));
  $('#review').hidden = false; $('#reviewEdit').focus();
}
const closeReview = () => { $('#review').hidden = true; S.reviewing = null; };
$('#reviewSend').onclick = async () => { const r = S.reviewing; if (r && await deliver(r.text, true, r.fromBox)) closeReview(); };
$('#reviewType').onclick = async () => { const r = S.reviewing; if (r && await deliver(r.text, false, r.fromBox)) closeReview(); };
$('#reviewEdit').onclick = () => { const r = S.reviewing; if (!r) return; $('#sessText').value = r.text; store.set('xr.draft.' + S.session, r.text); closeReview(); $('#sessText').focus(); };
$('#reviewDiscard').onclick = () => { closeReview(); toast('Discarded'); };
function renderReviewSwitch() { const b = $('#sessReview'), on = reviewAll(); b.setAttribute('aria-pressed', on); b.textContent = `Always review: ${on ? 'On' : 'Off'}`; }
$('#sessReview').onclick = () => { store.set('xr.review', reviewAll() ? '' : '1'); renderReviewSwitch(); };

/* ---------- voice ---------- */
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
// Listen once; onResult(best, alternatives, confidence). The button toggles stop.
function listen(btn, idleLabel, onResult) {
  if (S.listening) { S.listening.stop(); return; }
  if (!Recognition) return toast('Voice isn\'t available in this browser. Use the keyboard\'s microphone key instead.');
  const rec = new Recognition();
  rec.lang = navigator.language || 'en-US'; rec.interimResults = false; rec.maxAlternatives = 3; rec.continuous = false;
  rec.onstart = () => { S.listening = rec; btn.classList.add('listening'); btn.textContent = '■ Stop'; };
  rec.onend = () => { S.listening = null; btn.classList.remove('listening'); btn.textContent = idleLabel; };
  rec.onerror = e => toast(e.error === 'not-allowed' || e.error === 'service-not-allowed'
    ? (window.isSecureContext ? 'Microphone permission was refused.' : 'Voice needs a secure (HTTPS) page. Use the keyboard\'s microphone key instead.')
    : e.error === 'no-speech' ? 'Didn\'t hear anything.' : 'Voice error: ' + e.error);
  rec.onresult = e => {
    const result = e.results[0], alternatives = [...result].map(a => a.transcript.trim()).filter(Boolean);
    if (!alternatives.length) return toast('Didn\'t catch that.');
    onResult(alternatives[0], alternatives, result[0].confidence || null);
  };
  try { rec.start(); } catch (err) { toast('Voice could not start: ' + err.message); }
}
$('#sessVoice').onclick = () => listen($('#sessVoice'), '🎤 Speak', (text, alternatives, confidence) => review(text, { source: 'Heard', alternatives, confidence }));

/* ---------- talk: assistant that reads sessions and proposes actions ---------- */
const T = { conversation: null, events: [], busy: false };
const speakReplies = () => store.get('xr.talk.speak') !== 'off';
function renderSpeakSwitch() { const b = $('#talkSpeak'), on = speakReplies(); b.setAttribute('aria-pressed', on); b.textContent = `Read replies: ${on ? 'On' : 'Off'}`; }
$('#talkSpeak').onclick = () => { store.set('xr.talk.speak', speakReplies() ? 'off' : ''); renderSpeakSwitch(); if (!speakReplies()) window.speechSynthesis?.cancel(); };
function say(text) { if (!speakReplies() || !window.speechSynthesis) return; speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(text); u.lang = navigator.language || 'en-US'; speechSynthesis.speak(u); }
function logMsg(kind, text) { const m = el('div', 'xr-msg ' + kind, text); $('#talkLog').append(m); $('#talkLog').scrollTop = 1e9; return m; }
async function openTalk() {
  renderSpeakSwitch();
  if (!$('#talkLog').children.length) logMsg('note', 'Ask about your sessions, e.g. "What\'s waiting for me?" Changes always need your confirmation.');
  try {
    const st = await api('GET', '/api/agent/status');
    const ready = st.available && st.configured; $('#talkSetup').hidden = ready;
    $('#talkSetup').textContent = ready ? '' : 'The assistant is not set up yet. ' + st.setup;
  } catch (e) { $('#talkSetup').hidden = false; $('#talkSetup').textContent = 'Could not check the assistant: ' + e.message; }
}
async function talk(text) {
  text = (text || '').trim(); if (!text || T.busy) return;
  T.busy = true; logMsg('you', text); const wait = logMsg('ai thinking', '…');
  $('#talkSend').disabled = true;
  try {
    const live = S.blades.some(b => b.name === S.lastSession) ? S.lastSession : null;
    const r = await api('POST', '/api/agent', { text, conversation: T.conversation, session: live, events: T.events.splice(0) });
    T.conversation = r.conversation; wait.textContent = r.reply; wait.classList.remove('thinking'); say(r.reply);
    renderPending(r.pending || []);
  } catch (e) { wait.textContent = 'Sorry: ' + e.message; wait.classList.remove('thinking'); }
  finally { T.busy = false; $('#talkSend').disabled = false; }
}
function renderPending(actions) {
  const box = $('#talkPending'); box.innerHTML = '';
  for (const a of actions) {
    const row = el('div', 'xr-action'); row.append(el('span', '', a.label));
    const ok = el('button', 'xr-btn xr-primary', 'Confirm'), no = el('button', 'xr-btn', 'Cancel');
    ok.onclick = async () => {
      ok.disabled = no.disabled = true;
      try {
        const res = await api(a.request.method, a.request.path, a.request.body);
        T.events.push(`User confirmed and it was done: ${a.label}.`); logMsg('note', '✓ ' + a.label); row.remove(); toast('Done');
        if (a.kind === 'open_session' && res?.name) { await loadBlades(); S.lastSession = res.name; const go2 = el('button', 'xr-btn', 'Go to the new session'); go2.onclick = () => go('session', res.name); $('#talkPending').append(go2); }
      } catch (e) { T.events.push(`User confirmed but it failed: ${a.label} (${e.message}).`); logMsg('note', '✗ ' + e.message); ok.disabled = no.disabled = false; }
    };
    no.onclick = () => { T.events.push(`User cancelled: ${a.label}.`); logMsg('note', 'Cancelled: ' + a.label); row.remove(); };
    row.append(ok, no); box.append(row);
  }
  box.querySelector('.xr-primary')?.focus();
}
$('#talkForm').onsubmit = e => { e.preventDefault(); const t = $('#talkText').value; $('#talkText').value = ''; talk(t); };
$('#talkText').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#talkForm').requestSubmit(); } };
// Spoken requests go straight to the assistant: it only reads, and every change it
// proposes still needs a Confirm tap. What it heard is shown in the conversation.
$('#talkMic').onclick = () => listen($('#talkMic'), '🎤 Ask', text => talk(text));
$('#talkNew').onclick = () => { T.conversation = null; T.events = []; $('#talkLog').innerHTML = ''; $('#talkPending').innerHTML = ''; openTalk(); };
$('#talkBack').onclick = () => { if (S.lastSession && S.blades.some(b => b.name === S.lastSession)) go('session', S.lastSession); else go('home'); };

/* ---------- quick replies ---------- */
document.querySelectorAll('.xr-quick [data-quick]').forEach(b => { b.onclick = () => S.session && deliver(b.dataset.quick, b.dataset.enter === '1', false); });
$('#sessText').oninput = () => S.session && store.set('xr.draft.' + S.session, $('#sessText').value);
document.querySelectorAll('.xr-keys [data-key]').forEach(b => { b.onclick = () => S.session && api('POST', `/api/blades/${encodeURIComponent(S.session)}/send`, { key: b.dataset.key }).catch(e => toast('Key not sent: ' + e.message)); });
$('#sessSave').onclick = async () => { try { const r = await api('POST', '/api/commands/save-state/run', { blades: [S.session] }); toast(String(Object.values(r)[0] || 'Save state sent')); } catch (e) { toast(e.message); } };
$('#sessWind').onclick = async () => {
  const b = $('#sessWind'); if (b.dataset.armed !== '1') { b.dataset.armed = '1'; b.textContent = 'Confirm wind down'; setTimeout(() => { b.dataset.armed = ''; b.textContent = 'Wind down'; }, 4000); return; }
  b.dataset.armed = ''; b.textContent = 'Wind down';
  try { await api('POST', `/api/blades/${encodeURIComponent(S.session)}/winddown`, { command: 'wind-down' }); toast('Saving a handoff, then closing'); } catch (e) { toast(e.message); }
};
$('#sessGreen').onclick = () => { store.set('xr.green', greenOn() ? '' : '1'); applyGreen(); };

/* ---------- pointer-free navigation: arrow keys move between buttons ---------- */
document.addEventListener('keydown', e => {
  if (e.target.matches('input, textarea') || !['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key)) return;
  const scope = !$('#review').hidden ? $('#review') : !$('#pick').hidden ? $('#pick') : $('#view' + S.view[0].toUpperCase() + S.view.slice(1));
  const items = [...scope.querySelectorAll('button:not(:disabled), a.xr-btn, input, textarea')].filter(n => n.offsetParent);
  const i = items.indexOf(document.activeElement); if (!items.length) return;
  e.preventDefault(); items[(i + (e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
});
document.addEventListener('keydown', e => { if (e.key !== 'Escape') return; if (!$('#review').hidden) closeReview(); else if (!$('#pick').hidden) $('#pick').hidden = true; });

/* ---------- on-screen keyboard ---------- */
// In the Beam Pro's glasses mode a focused text box does not raise Android's
// keyboard by itself. Where the VirtualKeyboard API exists, ask for it explicitly
// on each tap (a user gesture), which also re-opens it after it was dismissed.
if (navigator.virtualKeyboard) {
  for (const field of document.querySelectorAll('#sessText, #talkText, #projName')) {
    field.setAttribute('virtualkeyboardpolicy', 'manual');
    field.addEventListener('click', () => {
      field.focus();
      try { navigator.virtualKeyboard.show(); S.diag?.push({ ms: Math.round(performance.now()), type: 'vk-show', target: '#' + field.id }); }
      catch (err) { S.diag?.push({ ms: Math.round(performance.now()), type: 'vk-error', target: err.message }); }
    });
  }
  navigator.virtualKeyboard.addEventListener('geometrychange', e => {
    const r = e.target.boundingRect; S.diag?.push({ ms: Math.round(performance.now()), type: 'vk-geometry', target: `${Math.round(r.width)}x${Math.round(r.height)}` });
  });
}

/* ---------- input diagnostics (only while <BLADES_HOME>/kbdebug.on exists) ---------- */
function startDiagnostics() {
  S.diag = []; const t0 = performance.now(), id = n => n && n.nodeType === 1 ? (n.id ? '#' + n.id : n.tagName.toLowerCase() + (n.className && typeof n.className === 'string' ? '.' + n.className.split(' ')[0] : '')) : String(n);
  const rec = (type, e, extra = {}) => S.diag.push({ ms: Math.round(performance.now() - t0), type, target: id(e?.target), pointer: e?.pointerType, active: id(document.activeElement), vv: window.visualViewport ? Math.round(visualViewport.width) + 'x' + Math.round(visualViewport.height) : '', ...extra });
  for (const type of ['pointerdown', 'pointerup', 'touchstart', 'mousedown', 'click', 'focusin', 'focusout', 'keydown', 'beforeinput', 'input'])
    document.addEventListener(type, e => rec(type, e, { key: e.key, inputType: e.inputType, data: e.data }), true);
  window.visualViewport?.addEventListener('resize', () => rec('viewport-resize'));
  window.addEventListener('resize', () => rec('window-resize', null, { inner: innerWidth + 'x' + innerHeight }));
  rec('start', null, { ua: navigator.userAgent, touchPoints: navigator.maxTouchPoints, vk: !!navigator.virtualKeyboard, coarse: matchMedia('(any-pointer: coarse)').matches, fine: matchMedia('(any-pointer: fine)').matches });
  setInterval(() => { if (S.diag.length) api('POST', '/api/debug/keyboard', { ua: 'xr', events: S.diag.splice(0) }).catch(() => {}); }, 2000);
  toast('Input diagnostics on');
}

/* ---------- start ---------- */
async function refresh() {
  try { await loadBlades(); } catch (_) { return; }
  if (S.view === 'home') renderHome(); else if (S.view === 'session') renderSessionInfo();
}
window.addEventListener('resize', () => { if (S.view === 'home') renderHome(); if (S.view === 'new') renderNew(); });
(async () => {
  applyScale();
  await Promise.allSettled([loadBlades(), loadProjects(), loadProviders(), loadStatus()]);
  const [view, arg] = location.hash.slice(1).split('/');
  if (view === 'session' && arg && S.blades.some(b => b.name === decodeURIComponent(arg))) go('session', decodeURIComponent(arg));
  else if (view === 'new' || view === 'project' || view === 'talk') go(view); else go('home');
  if (S.session) $('#sessText').value = store.get('xr.draft.' + S.session);
  setInterval(refresh, 3000);
})();
})();

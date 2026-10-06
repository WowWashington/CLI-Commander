/* CLI Commander front-end: project panel, blade tabs, xterm terminals, status strip. */
(() => {
const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const api = async (method, path, body) => {
  const r = await fetch(path, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(method !== 'GET' ? { 'X-Blades-Request': '1' } : {}) }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) { const body = await r.text(); let message = body; try { message = JSON.parse(body).error || JSON.parse(body).message || body; } catch (_) {} throw new Error(message.replace(/<[^>]*>/g, '').trim().slice(0, 300) || r.statusText); }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text();
};
const toast = (m) => { const t = $('#toast'); t.textContent = m; t.classList.add('show'); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2200); };
const GH_SVG = '<svg viewBox="0 0 16 16"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>';

const S = { restored: false, quick: [], settings: false, legacy: [], mig: null, projects: [], blades: [], recent: [], providers: [], provider: 'claude', sessionFilter: 'all', sessionSearch: '', commands: [], active: null, terms: {}, editing: false, layout: { order: [], hidden: [] }, filter: '', cfgSlug: null };
const isMobile = () => matchMedia('(max-width: 1024px)').matches;
const providerLabel = b => b.provider === 'shell' || b.shell ? 'Shell' : b.provider === 'codex' ? 'Codex' : b.provider === 'gemini' ? 'Gemini' : 'Claude';
const stateLabel = b => b.idle_pending ? 'Closing soon' : ({ busy: 'Running', attention: 'Needs you', idle: 'Idle', closing: 'Saving', starting: 'Starting', unknown: 'Active · status unknown' }[b.state] || b.state);
const storage = { get(key) { try { return localStorage.getItem(key) || ''; } catch (_) { return ''; } }, set(key, value) { try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); return true; } catch (_) { return false; } } };
const draftKey = name => 'blades.draft.' + name;
// Terminal typing mode is per device: 'auto' | 'live' | 'standard'.
const touchDevice = () => matchMedia('(any-pointer: coarse)').matches || navigator.maxTouchPoints > 0;
const typingSetting = () => ['live', 'standard'].includes(storage.get('blades.typing')) ? storage.get('blades.typing') : 'auto';
const liveTyping = () => typingSetting() === 'live' || (typingSetting() === 'auto' && touchDevice());
const assistantName = p => p.id === 'codex' ? 'Codex · ChatGPT' : p.id === 'gemini' ? 'Gemini' : p.id === 'claude' ? 'Claude' : p.label;
const pageVersion = $('meta[name="ui-version"]')?.content;

/* ---------- projects panel ---------- */
function projBySlug(slug) { return S.projects.find(p => p.slug === slug); }
function renderProjects() {
  renderOpenSessions();
  const box = $('#projects'); box.innerHTML = '';
  const f = S.filter.toLowerCase();
  for (const p of S.projects) {
    if (!S.editing && p.hidden) continue;
    if (f && !(p.name + ' ' + p.keywords).toLowerCase().includes(f)) continue;
    const row = el('div', 'proj' + (p.blades.length ? ' open' : '') + (p.hidden ? ' hidden' : ''));
    row.dataset.slug = p.slug; row.draggable = S.editing;
    const name = el('div', 'name'); if (S.editing) name.append(el('span', 'drag-handle', '⋮⋮ ')); name.append(document.createTextNode(p.name)); row.append(name);
    const badges = el('div', 'badges');
    if (p.attention) badges.append(el('span', 'badge attn', '!'));
    if (p.blades.length) badges.append(el('span', 'badge count', String(p.blades.length)));
    if (p.handoff) { const b = el('span', 'badge handoff', 'handoff'); b.title = 'Handoff note waiting for next open'; badges.append(b); }
    const plus = el('button', 'plus', '+'); plus.title = p.blades.length ? 'Open another blade for this project' : 'Open a blade'; plus.onclick = (e) => { e.stopPropagation(); openBlade(p.slug); }; badges.append(plus);
    if (S.editing) { const h = el('button', '', p.hidden ? 'show' : 'hide'); h.onclick = (e) => { e.stopPropagation(); toggleHidden(p.slug); }; badges.append(h); }
    row.append(badges);
    const meta = el('div', 'meta');
    const g = p.git;
    if (g && g.is_git) {
      const gh = el(g.remote ? 'a' : 'span', 'gh ' + g.state); gh.innerHTML = GH_SVG;
      if (g.remote) { gh.href = g.remote.url; gh.target = '_blank'; gh.onclick = e => e.stopPropagation(); }
      gh.title = (g.remote ? g.remote.owner + '/' + g.remote.repo : 'local repo, no remote') + ` · ${g.state}` + (g.branch ? ` · ${g.branch}` : '') + (g.last_commit ? ` · ${g.last_commit}` : '');
      meta.append(gh);
      const bits = []; if (g.dirty) bits.push(`●${g.dirty}`); if (g.ahead) bits.push(`↑${g.ahead}`); if (g.behind) bits.push(`↓${g.behind}`);
      if (bits.length) meta.append(el('span', 'gitbits ' + g.state, bits.join(' ')));
    }
    if (p.state_md) meta.append(el('span', '', 'STATE ' + p.state_md));
    if (!p.exists) meta.append(el('span', '', 'missing path'));
    row.append(meta);
    row.onclick = () => { const live = S.blades.filter(b => b.slug === p.slug); if (live.length) { activate(live[live.length - 1].name); setView('terminal'); } else openBlade(p.slug); };
    row.oncontextmenu = (e) => { e.preventDefault(); openConfig(p.slug); };
    if (S.editing) wireDrag(row);
    box.append(row);
    for (const b of S.blades.filter(b => b.slug === p.slug)) {
      const c = el('div', 'child' + (b.name === S.active ? ' active' : '')); c.dataset.state = b.state;
      c.append(el('span', 'dot'));
      const n = b.name.split('-').pop();
      const lbl = el('span', 'clabel', `${providerLabel(b)} ${n}`); c.append(lbl);
      c.append(el('span', 'cstate', b.state === 'starting' ? 'starting' : b.state + (b.note ? ' · ' + b.note : '')));
      c.append(el('span', 'cage', fmtAge(b.age_sec)));
      const lk = el('span', 'lock' + (b.protected ? ' on' : ''), b.protected ? '🔒' : '🔓'); lk.title = b.protected ? 'Protected: batch commands skip this CLI. Click to unprotect.' : 'Click to protect from batch commands'; lk.onclick = (e) => { e.stopPropagation(); toggleProtect(b.name); }; c.append(lk);
      const x = el('span', 'x', '×'); x.title = 'Wind down & close'; x.onclick = (e) => { e.stopPropagation(); windDown(b.name); }; c.append(x);
      c.onclick = () => { activate(b.name); setView('terminal'); };
      box.append(c);
    }
  }
}
async function toggleProtect(key) { await api('POST', '/api/protected/toggle', { key }); await refresh(true); await loadLegacy(); _projSig = ''; renderProjectsIfChanged(); }
let _projSig = '';
function renderProjectsIfChanged() {
  const sig = S.blades.map(b => b.name + b.state + (b.note || '') + (b.protected ? 'P' : '')).join('|') + '#' + S.active;
  if (sig !== _projSig) { _projSig = sig; renderProjects(); }
}
function wireDrag(row) {
  row.ondragstart = (e) => { row.classList.add('dragging'); e.dataTransfer.setData('text/plain', row.dataset.slug); };
  row.ondragend = () => { row.classList.remove('dragging'); document.querySelectorAll('.drop-before').forEach(r => r.classList.remove('drop-before')); };
  row.ondragover = (e) => { e.preventDefault(); row.classList.add('drop-before'); };
  row.ondragleave = () => row.classList.remove('drop-before');
  row.ondrop = (e) => { e.preventDefault(); const from = e.dataTransfer.getData('text/plain'); reorder(from, row.dataset.slug); };
}
async function reorder(from, before) {
  const order = S.projects.map(p => p.slug).filter(s => s !== from);
  order.splice(order.indexOf(before), 0, from);
  S.layout.order = order; await api('PUT', '/api/layout', S.layout); await loadProjects();
}
async function toggleHidden(slug) {
  const h = new Set(S.layout.hidden); h.has(slug) ? h.delete(slug) : h.add(slug);
  S.layout.hidden = [...h]; S.layout.order = S.projects.map(p => p.slug);
  await api('PUT', '/api/layout', S.layout); await loadProjects();
}
async function loadProjects() {
  S.projects = await api('GET', '/api/projects');
  S.layout = { order: S.projects.map(p => p.slug), hidden: S.projects.filter(p => p.hidden).map(p => p.slug) };
  _projSig = ''; renderProjectsIfChanged();
  $('#setupProjectCount').textContent = `${S.projects.length} registered ${S.projects.length === 1 ? 'project' : 'projects'}`;
  renderProjectList();
  if (!S.active) renderHomeIndex();
}

function renderInstanceName(name) {
  S.instanceName = name || ''; const node = $('#instanceName');
  node.textContent = S.instanceName; node.hidden = !S.instanceName; node.dataset.kind = /prod/i.test(S.instanceName) ? 'prod' : /dev/i.test(S.instanceName) ? 'dev' : '';
  if (document.activeElement !== $('#instanceInput')) $('#instanceInput').value = S.instanceName; renderStrip();
}
function renderTyping() {
  const ua = navigator.userAgent, os = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1) ? 'iPhone/iPad' : /Mac/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux|X11/.test(ua) ? 'Linux or Android desktop mode' : 'Unknown system';
  const browser = /Edg\//.test(ua) ? 'Edge' : /SamsungBrowser/.test(ua) ? 'Samsung Internet' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'browser';
  $('#typingMode').value = typingSetting();
  $('#typingDetected').textContent = `This device: ${os} · ${browser} · ${touchDevice() ? 'touch screen' : 'no touch screen'}. Automatic uses ${touchDevice() ? 'Live' : 'Standard'} here.`;
}
function renderProjectList() {
  const box = $('#projectList'); if (!box) return; box.innerHTML = '';
  if (!S.projects.length) { box.append(el('p', 'hint', 'No projects yet.')); return; }
  for (const p of S.projects) {
    if (p.slug === 'shell-commands') continue;
    const row = el('div', 'path-row'); const copy = el('div', 'path-copy');
    copy.append(el('strong', '', p.name), el('span', 'mono hint', tildePath(p.path) + (p.exists === false ? ' · missing' : '')));
    const remove = el('button', '', 'Remove');
    if (p.blades?.length) { remove.disabled = true; remove.title = 'Close this project’s live sessions first'; }
    remove.onclick = async () => {
      if (!confirm(`Remove “${p.name}” from CLI Commander?\n\nThe folder and its files are not changed. You can add it again later.`)) return;
      busy(remove, 'Removing…');
      try { await api('DELETE', '/api/projects/' + encodeURIComponent(p.slug)); toast('Project removed'); await loadProjects(); loadAudit(); }
      catch (e) { toast(e.message); unbusy(remove); }
    };
    row.append(copy, remove); box.append(row);
  }
}
async function loadLocations() {
  try { S.locations = await api('GET', '/api/locations'); S.homeDir = S.locations.home; renderLocations(); renderProjectList(); }
  catch (e) { $('#locationList').textContent = 'Could not read code locations: ' + e.message; }
}
async function saveLocations(paths, message) {
  try { S.locations = await api('PUT', '/api/locations', { paths }); renderLocations(); toast(message); loadAudit(); return true; }
  catch (e) { $('#locationError').textContent = e.message; $('#locationError').hidden = false; return false; }
}
function renderLocations() {
  const data = S.locations, box = $('#locationList'), sug = $('#locationSuggest'); box.innerHTML = ''; sug.innerHTML = ''; $('#locationError').hidden = true;
  const current = data.locations.map(l => l.path);
  $('#locationAdd').hidden = data.env_override;
  if (data.env_override) box.append(el('p', 'hint', 'Set by BLADES_PROJECTS_ROOT on this Mac. Ask your setup agent to remove it to manage locations here.'));
  if (!current.length) box.append(el('p', 'hint', 'No code locations yet.'));
  for (const l of data.locations) {
    const row = el('div', 'path-row'); const copy = el('div', 'path-copy');
    copy.append(el('strong', 'mono', tildePath(l.path)), el('span', 'hint', l.exists ? `${l.folders} ${l.folders === 1 ? 'folder' : 'folders'} · ${l.git} git` : 'Folder missing'));
    row.append(copy);
    if (!data.env_override) { const remove = el('button', '', 'Remove'); remove.title = 'Stop listing folders from here. Registered projects stay.'; remove.onclick = () => saveLocations(current.filter(p => p !== l.path), 'Code location removed'); row.append(remove); }
    box.append(row);
  }
  if (!data.env_override && data.suggestions.length) {
    sug.append(el('span', 'hint', 'Found on this Mac:'));
    for (const l of data.suggestions) { const chip = el('button', 'chip', '+ ' + tildePath(l.path)); chip.title = `${l.folders} folders · ${l.git} git`; chip.onclick = () => saveLocations([...current, l.path], 'Code location added'); sug.append(chip); }
  }
}

/* ---------- blades / terminals ---------- */
function bladeByName(n) { return S.blades.find(b => b.name === n); }
async function openBlade(slug, shell = false, provider = null) {
  if (!shell && !provider) return openLauncher(slug);
  try {
    const b = await api('POST', '/api/blades', { slug, shell, ...(shell ? {} : { provider }) });
    toast(`Opened ${b.name}`); await refresh(true); activate(b.name);
    setView('terminal'); return true;
  } catch (e) { toast('Open failed: ' + e.message); return false; }
}
async function loadProviders() {
  try { await loadOnboarding(); S.providerError = false; }
  catch (_) { S.providers = []; S.providerError = true; }
}
async function openLauncher(slug) {
  await loadProviders();
  if (!S.onboarding?.completed) { S.setupPendingLaunch = { slug }; await openSetup(); return; }
  S.launchSlug = slug || null;
  const select = $('#newProject'); select.innerHTML = '';
  for (const p of S.projects.filter(p => p.exists !== false && !p.hidden)) { const option = el('option', '', p.name); option.value = p.slug; select.append(option); }
  if (slug) select.value = slug;
  $('#newModal').classList.toggle('compact-launch', !!slug);
  $('#newTitle').textContent = slug ? (projBySlug(slug)?.name || slug) : 'New session';
  $('#newEyebrow').textContent = slug ? 'OPEN A SESSION' : 'START SOMETHING';
  $('#newProjectLabel').hidden = !!slug; select.hidden = !!slug; $('#newAssistantLabel').hidden = !!slug;
  $('#newModal .launch-footer').hidden = !!slug;
  $('#newAddProject').hidden = !!select.options.length;
  $('#providerChoices').textContent = 'Checking assistants…';
  $('#newModal').classList.add('show');
  const preferred = storage.get('blades.provider');
  const enabled = enabledProviders();
  S.provider = enabled.find(p => p.id === preferred && p.available)?.id || enabled.find(p => p.available)?.id || 'claude';
  renderProviders();
}
function renderProviders() {
  const box = $('#providerChoices'); box.innerHTML = '';
  if (S.providerError) box.append(el('p', 'hint', 'Could not check assistants on this Mac. Close and reopen to try again.'));
  const choices = enabledProviders();
  if (!S.providerError && !choices.length) { const button = el('button', '', 'Choose assistants in setup'); button.onclick = () => { $('#newModal').classList.remove('show'); openSetup(); }; box.append(button); }
  for (const p of choices) {
    const button = el('button', 'provider-choice' + (!S.launchSlug && p.id === S.provider ? ' selected' : ''));
    button.type = 'button'; button.disabled = !p.available || !!S.launching;
    if (!S.launchSlug) button.setAttribute('aria-pressed', String(p.id === S.provider));
    const icon = el('span', 'provider-icon ' + p.id, p.id === 'codex' ? '⌘' : p.id === 'gemini' ? '✧' : '✳');
    const label = assistantName(p);
    const detail = !p.available ? 'Not installed' : S.launchSlug ? 'Open session ↗' : p.id === 'codex' ? 'OpenAI CLI · your ChatGPT account' : (p.detail || 'Ready on this Mac');
    const copy = el('span', 'provider-copy'); copy.append(el('strong', '', label), el('small', '', detail));
    button.append(icon, copy, el('span', 'provider-check', !S.launchSlug && p.id === S.provider ? '✓' : ''));
    button.onclick = () => { S.provider = p.id; storage.set('blades.provider', p.id); if (S.launchSlug) launchAssistant(S.launchSlug, p.id); else renderProviders(); }; box.append(button);
  }
  $('#newLaunch').disabled = !!S.launching || !choices.some(p => p.id === S.provider && p.available) || !$('#newProject').value;
}
async function launchAssistant(slug, provider) {
  if (S.launching) return;
  S.launching = true; renderProviders(); $('#newLaunch').textContent = 'Opening…';
  try { if (await openBlade(slug, false, provider)) $('#newModal').classList.remove('show'); }
  finally { S.launching = false; $('#newLaunch').textContent = 'Open session →'; renderProviders(); }
}

/* Setup stores assistant choices only. Authentication remains in the native CLIs. */
function enabledProviders() { const enabled = S.onboarding?.enabled_providers || []; return S.providers.filter(p => enabled.includes(p.id)); }
const authLabel = p => !p.available ? 'Not installed' : p.auth?.state === 'ready' ? 'Signed in' : p.auth?.state === 'sign_in' ? 'Sign in needed' : 'Check sign-in';
function officialLink(url, label) {
  try { const target = new URL(url); if (target.protocol !== 'https:') return null; const a = el('a', '', label); a.href = target.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; } catch (_) { return null; }
}
async function loadOnboarding(recheck = false) {
  const data = await api('GET', '/api/onboarding' + (recheck ? '?refresh=1' : ''));
  S.onboarding = data; S.providers = data.providers || []; S.providerError = false; renderSetupSummary(); return data;
}
function renderSetupSummary() {
  const box = $('#setupSummary'); box.innerHTML = '';
  const choices = enabledProviders();
  if (!S.onboarding?.completed) box.append(el('span', 'hint', 'Finish setup to choose your assistants.'));
  else for (const p of choices) { const badge = el('span', 'setup-summary-item'); badge.append(el('strong', '', assistantName(p)), el('span', 'auth-state ' + (p.auth?.state || 'unknown'), authLabel(p))); box.append(badge); }
}
async function openSetup() {
  $('#newModal').classList.remove('show'); $('#setupModal').classList.add('show');
  S.setupStep = 0; S.setupShowAllGuides = false; $('#setupBody').textContent = 'Checking assistants on this Mac…'; $('#setupError').hidden = true; $('#setupNext').disabled = true;
  try {
    const data = await loadOnboarding();
    S.setupSelected = new Set(data.completed ? data.enabled_providers : S.providers.filter(p => p.available).map(p => p.id));
    renderSetup(); $('#setupClose').focus();
  } catch (e) { $('#setupBody').textContent = ''; showSetupError('Could not load setup: ' + e.message); $('#setupNext').textContent = 'Try again'; $('#setupNext').disabled = false; S.setupFailed = true; }
}
function showSetupError(message) { $('#setupError').textContent = message; $('#setupError').hidden = false; }
function selectedAssistants() { return S.providers.filter(p => p.available && S.setupSelected?.has(p.id)); }
function renderSetup() {
  S.setupFailed = false; $('#setupError').hidden = true;
  const step = S.setupStep || 0, body = $('#setupBody'); body.innerHTML = '';
  const selected = selectedAssistants(), pending = selected.filter(p => p.auth?.state !== 'ready');
  document.querySelectorAll('#setupProgress span').forEach(node => node.classList.toggle('current', Number(node.dataset.step) === step));
  $('#setupBack').hidden = step !== 1 && step !== 2; $('#setupNext').disabled = !!S.setupBusy || (step === 0 && !selected.length) || (step === 2 && !S.setupLoc);
  $('#setupFootnote').textContent = ['Installed assistants only', 'Sign in through the official CLI', 'Folders stay where they are', 'Change these choices in Settings'][step];
  if (step === 0) {
    $('#setupTitle').textContent = 'Choose your assistants'; $('#setupIntro').textContent = 'We found these on your Mac. Pick the ones you want in each project’s + menu.';
    $('#setupNext').textContent = 'Continue →';
    for (const p of S.providers) {
      const row = el('div', 'setup-choice'); const label = el('label', 'setup-choice-label');
      const check = el('input'); check.type = 'checkbox'; check.dataset.setupProvider = p.id; check.checked = !!p.available && S.setupSelected.has(p.id); check.disabled = !p.available || !!S.setupBusy;
      check.onchange = () => { if (check.checked) S.setupSelected.add(p.id); else S.setupSelected.delete(p.id); renderSetup(); document.querySelector(`[data-setup-provider="${p.id}"]`)?.focus(); };
      const copy = el('span', 'setup-choice-copy'); copy.append(el('strong', '', assistantName(p)), el('small', 'auth-state ' + (p.auth?.state || 'unknown'), authLabel(p)));
      label.append(check, copy); row.append(label);
      if (!p.available) { const link = officialLink(p.install_url, 'Install ↗'); if (link) row.append(link); }
      else row.append(el('span', 'setup-installed', 'Installed'));
      body.append(row);
    }
    if (S.onboarding?.system?.tmux_available === false) body.append(el('p', 'setup-auth-note', 'Terminal support (tmux) is missing on this Mac. Ask your setup agent to install it before starting sessions.'));
    if (S.onboarding?.other_clis?.length) {
      const found = el('details', 'setup-other'); found.append(el('summary', '', 'Also detected · ' + S.onboarding.other_clis.map(p => p.label).join(', ')));
      found.append(el('p', 'hint', 'These are available on this Mac, but managed sessions and safe resume are not supported in CLI Commander yet.')); body.append(found);
    }
    const links = el('div', 'setup-guide-actions');
    const rescan = el('button', 'setup-inline-button', 'Installed something? Check again'); rescan.onclick = recheckSetup;
    const guides = el('button', 'setup-inline-button', 'Review sign-in guides'); guides.disabled = !selected.length; guides.onclick = () => { S.setupShowAllGuides = true; S.setupStep = 1; renderSetup(); };
    links.append(rescan, guides); body.append(links);
  } else if (step === 1) {
    $('#setupTitle').textContent = S.setupShowAllGuides ? 'Sign-in guides' : pending.length ? 'Complete sign-in' : 'Your assistants are ready';
    $('#setupIntro').textContent = S.setupShowAllGuides ? 'Signed-in assistants can keep their current accounts. Use these official commands in Terminal when you need to sign in.' : pending.length ? 'Use Terminal on this Mac. Follow the assistant’s official sign-in flow, then check again here.' : 'The selected assistants report that they are signed in.';
    $('#setupNext').textContent = 'Continue →';
    for (const p of S.setupShowAllGuides ? selected : pending) {
      const card = el('section', 'auth-guide'); const head = el('div', 'auth-guide-head'); head.append(el('strong', '', assistantName(p)), el('span', 'auth-state ' + (p.auth?.state || 'unknown'), authLabel(p))); card.append(head);
      if (p.auth?.detail) card.append(el('p', 'hint', p.auth.detail));
      if (p.auth_command) { const command = el('div', 'setup-command'); const code = el('code', '', p.auth_command); const copy = el('button', '', 'Copy'); copy.type = 'button'; copy.onclick = () => copySetupText(p.auth_command, copy); command.append(code, copy); card.append(command); }
      const steps = Array.isArray(p.auth_help) ? p.auth_help : p.auth_help ? [p.auth_help] : ['Run the command in Terminal and follow the assistant’s sign-in instructions.'];
      const list = el('ol'); steps.forEach(text => list.append(el('li', '', text))); card.append(list); body.append(card);
      const guide = officialLink(p.auth_url, 'Official sign-in guide ↗'); if (guide) card.append(guide);
    }
    if (!pending.length) body.append(el('div', 'setup-success', '✓ Signed in on this Mac'));
    else body.append(el('p', 'setup-auth-note', 'You can continue now. Any assistant that still needs sign-in will prompt when opened. CLI Commander never asks for your password or API key.'));
    const recheck = el('button', '', 'Check sign-in again'); recheck.onclick = recheckSetup; body.append(recheck);
  } else if (step === 2) {
    renderSetupProjects(body);
  } else {
    $('#setupTitle').textContent = 'Your workspace is ready'; $('#setupIntro').textContent = 'Use a project’s + button to start the assistant you want. Your sessions stay on this Mac.';
    $('#setupNext').textContent = S.setupPendingLaunch ? 'Continue to session →' : 'Done';
    const enabled = enabledProviders(); body.append(el('div', 'setup-success', '✓ ' + enabled.map(assistantName).join(' · ')));
    if (!S.projects.some(p => p.exists !== false && !p.hidden)) { const add = el('button', 'primary', '+ Add your first project'); add.onclick = () => { $('#setupModal').classList.remove('show'); S.setupPendingLaunch = null; openProjectDialog(true); }; body.append(add); }
    body.append(el('p', 'hint', 'Idle sessions close after your configured timeout. Archives and conversation resume stay available.'));
  }
}
async function recheckSetup() {
  if (S.setupChecking || S.setupBusy) return;
  S.setupChecking = true; $('#setupBody').inert = true;
  $('#setupFootnote').textContent = 'Checking sign-in…';
  const next = $('#setupNext'); next.disabled = true;
  try { await loadOnboarding(true); renderSetup(); toast('Assistant status updated'); }
  catch (e) { showSetupError('Could not check status: ' + e.message); $('#setupFootnote').textContent = 'Your choices are unchanged'; next.disabled = false; }
  finally { S.setupChecking = false; $('#setupBody').inert = false; }
}
async function nextSetup() {
  if (S.setupBusy || S.setupChecking) return;
  if (S.setupFailed) return openSetup();
  if (S.setupStep === 3) { $('#setupModal').classList.remove('show'); const pending = S.setupPendingLaunch; S.setupPendingLaunch = null; if (pending) openLauncher(pending.slug); return; }
  if (S.setupStep === 2) return saveSetupProjects();
  const selected = selectedAssistants(); if (!selected.length) return;
  if (S.setupStep === 0 && selected.some(p => p.auth?.state !== 'ready')) { S.setupStep = 1; renderSetup(); return; }
  S.setupBusy = true; $('#setupBody').inert = true; $('#setupBack').disabled = true; $('#setupNext').disabled = true; $('#setupNext').textContent = 'Saving…';
  try { await api('POST', '/api/onboarding', { enabled_providers: selected.map(p => p.id) }); await loadOnboarding(); S.setupStep = 2; S.setupLoc = null; }
  catch (e) { showSetupError('Could not save setup: ' + e.message); }
  finally { S.setupBusy = false; $('#setupBody').inert = false; $('#setupBack').disabled = false; if (S.setupStep === 2) { renderSetup(); loadSetupLocations(); } else { $('#setupNext').disabled = false; $('#setupNext').textContent = 'Continue →'; } }
}
/* Setup step 3: where the user's code lives, and which folders to add as projects. */
async function loadSetupLocations() {
  try {
    const data = await api('GET', '/api/locations'); S.homeDir = data.home;
    const known = [...data.locations, ...data.suggestions];
    S.setupLoc = { env: data.env_override, info: Object.fromEntries(known.map(l => [l.path, l])), order: known.map(l => l.path), chosen: new Set(data.locations.map(l => l.path)), folders: [], picked: new Set() };
    await refreshSetupFolders();
  } catch (e) { showSetupError('Could not read code locations: ' + e.message); S.setupLoc = { env: false, info: {}, order: [], chosen: new Set(), folders: [], picked: new Set() }; }
  if (S.setupStep === 2) renderSetup();
}
async function refreshSetupFolders() {
  const loc = S.setupLoc, paths = loc.order.filter(p => loc.chosen.has(p));
  loc.folders = paths.length ? await api('POST', '/api/locations/preview', { paths }) : [];
  const visible = new Set(loc.folders.map(f => f.path)); loc.picked = new Set([...loc.picked].filter(p => visible.has(p)));
}
function renderSetupProjects(body) {
  $('#setupTitle').textContent = 'Where is your code?';
  $('#setupIntro').textContent = 'Choose the folders that hold your projects, then pick the projects to add. Nothing is moved or changed.';
  const loc = S.setupLoc;
  if (!loc) { body.append(el('p', 'hint', 'Looking for code folders on this Mac…')); $('#setupNext').textContent = 'Continue →'; return; }
  const picked = loc.picked.size; $('#setupNext').textContent = picked ? `Add ${picked} ${picked === 1 ? 'project' : 'projects'} →` : 'Skip for now →';
  body.append(el('div', 'setup-subhead', 'Code locations'));
  if (loc.env) body.append(el('p', 'setup-auth-note', 'BLADES_PROJECTS_ROOT sets the code location on this Mac. Ask your setup agent to remove it to choose folders here.'));
  if (!loc.order.length) body.append(el('p', 'hint', 'No common code folders found. Add the folder that holds your projects below.'));
  for (const path of loc.order) {
    const info = loc.info[path] || {}; const row = el('div', 'setup-choice'); const label = el('label', 'setup-choice-label');
    const check = el('input'); check.type = 'checkbox'; check.checked = loc.chosen.has(path); check.disabled = loc.env || !!S.setupBusy;
    check.onchange = async () => { check.checked ? loc.chosen.add(path) : loc.chosen.delete(path); await updateSetupFolders(); };
    const copy = el('span', 'setup-choice-copy'); copy.append(el('strong', 'mono', tildePath(path)), el('small', 'hint', info.folders == null ? 'Added by you' : `${info.folders} ${info.folders === 1 ? 'folder' : 'folders'} · ${info.git} git`));
    label.append(check, copy); row.append(label); body.append(row);
  }
  if (!loc.env) {
    const add = el('form', 'setup-add-location'); const input = el('input'); input.placeholder = '~/path/to/your/code'; input.autocomplete = 'off'; input.autocapitalize = 'off'; input.spellcheck = false;
    const button = el('button', '', 'Add folder'); button.type = 'submit'; add.append(input, button);
    add.onsubmit = async (e) => {
      e.preventDefault(); const value = input.value.trim(); if (!value) return;
      if (!(value.startsWith('/') || value.startsWith('~/'))) { showSetupError('Enter an absolute folder path or one starting with ~/.'); return; }
      const path = value.startsWith('~/') ? S.homeDir ? S.homeDir + value.slice(1) : value : value;
      if (!loc.order.includes(path)) loc.order.push(path); loc.chosen.add(path);
      if (!(await updateSetupFolders())) { loc.chosen.delete(path); loc.order = loc.order.filter(p => p !== path || loc.info[p]); renderSetup(); }
    };
    body.append(add);
  }
  const folders = loc.folders;
  const head = el('div', 'setup-subhead'); head.append(el('span', '', folders.length ? `Projects found · ${folders.length}` : 'Projects found'));
  if (folders.length) { const all = el('button', 'setup-inline-button', picked === folders.length ? 'Select none' : 'Select all'); all.onclick = () => { loc.picked = picked === folders.length ? new Set() : new Set(folders.map(f => f.path)); renderSetup(); }; head.append(all); }
  body.append(head);
  if (!folders.length) body.append(el('p', 'hint', loc.chosen.size ? 'No new folders in the chosen locations. You can add projects later from Settings.' : 'Choose a code location to see its folders, or skip and add projects later.'));
  else {
    const list = el('div', 'setup-folders');
    for (const f of folders) {
      const label = el('label', 'setup-folder'); const check = el('input'); check.type = 'checkbox'; check.checked = loc.picked.has(f.path);
      check.onchange = () => { check.checked ? loc.picked.add(f.path) : loc.picked.delete(f.path); const n = loc.picked.size; $('#setupNext').textContent = n ? `Add ${n} ${n === 1 ? 'project' : 'projects'} →` : 'Skip for now →'; };
      const copy = el('span', 'setup-choice-copy'); copy.append(el('strong', '', f.name), el('small', 'hint', [f.is_git && 'git', f.has_state && 'STATE.md', tildePath(f.root)].filter(Boolean).join(' · ')));
      label.append(check, copy); list.append(label);
    }
    body.append(list);
  }
}
async function updateSetupFolders() {
  $('#setupError').hidden = true; $('#setupBody').inert = true;
  try { await refreshSetupFolders(); return true; }
  catch (e) { showSetupError(e.message); return false; }
  finally { $('#setupBody').inert = false; renderSetup(); }
}
async function saveSetupProjects() {
  const loc = S.setupLoc; if (!loc) return;
  S.setupBusy = true; $('#setupBody').inert = true; $('#setupBack').disabled = true; $('#setupNext').disabled = true; $('#setupNext').textContent = 'Saving…';
  try {
    if (!loc.env) await api('PUT', '/api/locations', { paths: loc.order.filter(p => loc.chosen.has(p)) });
    const failed = [];
    for (const path of loc.picked) { try { await api('POST', '/api/audit/register', { path }); } catch (e) { failed.push(`${path.split('/').pop()}: ${e.message}`); } }
    await loadProjects(); loadAudit();
    if (failed.length) { loc.picked = new Set(); await refreshSetupFolders(); showSetupError('Some projects were not added. ' + failed.join(' · ')); return; }
    S.setupStep = 3;
  } catch (e) { showSetupError('Could not save code locations: ' + e.message); }
  finally {
    S.setupBusy = false; $('#setupBody').inert = false; $('#setupBack').disabled = false;
    if (S.setupStep === 3) renderSetup(); else { const keep = $('#setupError').textContent, shown = !$('#setupError').hidden; renderSetup(); if (shown) showSetupError(keep); }
  }
}
function tildePath(path) { return S.homeDir && (path === S.homeDir || path.startsWith(S.homeDir + '/')) ? '~' + path.slice(S.homeDir.length) : path; }
function copyWithSelection(text, container) {
  const active = document.activeElement, selection = window.getSelection();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
  const field = document.createElement('textarea');
  field.value = text; field.readOnly = true; field.tabIndex = -1;
  field.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;font-size:16px;';
  container.append(field);
  try {
    field.focus({ preventScroll: true }); field.select(); field.setSelectionRange(0, text.length);
    return document.execCommand('copy');
  } catch (_) { return false; }
  finally {
    field.remove();
    if (active?.isConnected) active.focus({ preventScroll: true });
    if (selection) { selection.removeAllRanges(); ranges.forEach(range => selection.addRange(range)); }
  }
}
async function copySetupText(text, button) {
  let copied = false;
  // HTTP Tailscale origins lack the modern API. Copy during the click itself
  // so Safari and other browsers retain the required user gesture.
  if (window.isSecureContext && navigator.clipboard?.writeText && !button.dataset.copyFallback) {
    try { await navigator.clipboard.writeText(text); copied = true; }
    catch (_) { button.dataset.copyFallback = 'true'; }
  }
  if (!copied) copied = copyWithSelection(text, button.parentElement);
  clearTimeout(button.copyTimer);
  if (copied) {
    button.textContent = 'Copied'; toast('Command copied');
    button.copyTimer = setTimeout(() => { button.textContent = 'Copy'; }, 1500);
  } else {
    button.textContent = 'Copy';
    const code = button.parentElement.querySelector('code'), selection = window.getSelection();
    if (code && selection) { const range = document.createRange(); range.selectNodeContents(code); selection.removeAllRanges(); selection.addRange(range); }
    toast('Could not copy automatically. Command selected: press ⌘C / Ctrl+C, or touch and hold to copy.');
  }
}
function openProjectDialog(launchAfter = false, mode = 'new') {
  S.projectLaunchAfterAdd = launchAfter; $('#newModal').classList.remove('show'); $('#projectError').hidden = true;
  $('#projectPath').value = ''; $('#projectName').value = ''; $('#projectNewName').value = ''; $('#projectGit').checked = true;
  $('#projectModal').classList.add('show'); setProjectMode(mode); loadProjectLocations();
}
async function loadProjectLocations() {
  // Offer code locations for the new folder; default to the first one.
  try {
    const data = await api('GET', '/api/locations'); S.homeDir = data.home;
    const paths = data.locations.map(l => tildePath(l.path)), list = $('#projectParentList'); list.innerHTML = '';
    for (const p of paths) { const o = el('option'); o.value = p; list.append(o); }
    if (!$('#projectParent').value) $('#projectParent').value = paths[0] || '~/Projects';
  } catch (_) { if (!$('#projectParent').value) $('#projectParent').value = '~/Projects'; }
  updateProjectPreview();
}
function setProjectMode(mode) {
  S.projectMode = mode; const isNew = mode === 'new';
  $('#projectModeNew').setAttribute('aria-selected', isNew); $('#projectModeExisting').setAttribute('aria-selected', !isNew);
  $('#projectNewFields').hidden = !isNew; $('#projectExistingFields').hidden = isNew; $('#projectError').hidden = true;
  $('#projectSave').textContent = isNew ? 'Create project' : 'Add project';
  $('#projectFootnote').textContent = isNew ? 'Nothing outside the new folder is touched.' : 'Your existing files stay in place.';
  updateProjectPreview();
  if (!isMobile()) (isNew ? $('#projectNewName') : $('#projectPath')).focus();
}
function projectFolderName(name) { return name.trim().replace(/[\/\\:]+/g, '-').trim(); }
function updateProjectPreview() {
  const folder = projectFolderName($('#projectNewName').value), parent = $('#projectParent').value.trim().replace(/\/+$/, '');
  $('#projectPreview').textContent = folder && parent ? `Creates ${parent}/${folder}` : '';
}
async function addProject() {
  if ($('#projectSave').disabled) return;
  const isNew = S.projectMode === 'new', fail = msg => { $('#projectError').textContent = msg; $('#projectError').hidden = false; };
  let body;
  if (isNew) {
    const name = $('#projectNewName').value.trim(), parent = $('#projectParent').value.trim();
    if (!name) return fail('Enter a project name.');
    if (parent && !(parent.startsWith('/') || parent.startsWith('~'))) return fail('Enter an absolute location or one starting with ~/.');
    body = { create: true, name, git: $('#projectGit').checked, ...(parent ? { parent } : {}) };
  } else {
    const path = $('#projectPath').value.trim(), name = $('#projectName').value.trim();
    if (!path || !(path.startsWith('/') || path.startsWith('~/'))) return fail('Enter an absolute folder path or one starting with ~/.');
    body = { path, ...(name ? { name } : {}) };
  }
  const button = $('#projectSave'); busy(button, isNew ? 'Creating…' : 'Adding…'); $('#projectError').hidden = true;
  try {
    const result = await api('POST', '/api/projects', body); await loadProjects(); loadAudit(); $('#projectModal').classList.remove('show');
    toast(isNew ? `Created ${result.path ? tildePath(result.path) : 'project'}` : 'Project added');
    // A new project goes straight on to choosing its assistant.
    if (isNew || S.projectLaunchAfterAdd) openLauncher(result.slug);
  }
  catch (e) { fail(e.message); }
  finally { unbusy(button); }
}
async function loadServerConfig() {
  try { const config = await api('GET', '/api/server-config'); $('#setupPort').value = config.configured_port ?? config.port ?? 5090; renderServerConfig(config); }
  catch (e) { $('#setupPortStatus').textContent = 'Could not read connection settings: ' + e.message; }
}
function renderServerConfig(config) {
  const running = config.port ?? 5090, configured = config.configured_port ?? running;
  $('#setupPortStatus').textContent = `${config.restart_required ? `Restart required · serving on ${running}, configured for ${configured}` : `Serving on port ${running}`}${config.env_override ? ' · an environment override is active' : ''}`;
  const instructions = config.instructions;
  $('#setupPortInstructions').hidden = !instructions; $('#setupPortInstructions').textContent = Array.isArray(instructions) ? instructions.join('\n') : instructions || '';
  const link = $('#setupPortLink'); link.hidden = true;
  if (config.next_url) { try { const url = new URL(location.href); url.port = String(configured); url.pathname = '/'; url.search = ''; url.hash = ''; if (['http:', 'https:'].includes(url.protocol)) { link.href = url.href; link.textContent = `Open port ${configured} after restart ↗`; link.hidden = !config.restart_required; } } catch (_) {} }
}
async function saveServerConfig() {
  const value = $('#setupPort').value.trim(), port = Number(value);
  if (!value || !Number.isInteger(port) || port < 1024 || port > 65535) { $('#setupPortStatus').textContent = 'Choose a port from 1024 to 65535.'; return; }
  const button = $('#setupPortSave'); busy(button, 'Saving…');
  try { const config = await api('POST', '/api/server-config', { port }); renderServerConfig(config); toast(config.restart_required ? 'Port saved · restart the service when ready' : 'Port saved'); }
  catch (e) { $('#setupPortStatus').textContent = e.message; }
  finally { unbusy(button); }
}
function ensureTerm(name) {
  if (S.terms[name]) return S.terms[name];
  const wrap = el('div', 'term'); wrap.dataset.name = name; $('#terms').append(wrap);
  const term = new Terminal({ fontFamily: 'SF Mono, Menlo, monospace', fontSize: isMobile() ? 11 : 12.5, theme: { background: '#000000' }, cursorBlink: true, scrollback: 5000, allowProposedApi: true });
  const fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open(wrap);
  const t = { term, fit, ws: null, wrap, attempts: 0, reconnectTimer: null, disposed: false, connected: false, status: 'Connecting' };
  // Hidden tabs and collapsed layouts report bogus sizes; never resize the shared tmux window from them.
  t.sendResize = () => {
    if (S.active !== name || S.settings || document.hidden || !wrap.clientWidth || !wrap.clientHeight) return;
    // Always (re)assert this view's size: tmux ignores an unchanged size, and a
    // missed send would otherwise leave the window at another client's size.
    try { fit.fit(); if (t.ws?.readyState === WebSocket.OPEN) t.ws.send('\x00' + JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows })); } catch (_) {}
  };
  t.connect = () => {
    if (t.disposed || t.ws && t.ws.readyState < WebSocket.CLOSING) return;
    clearTimeout(t.reconnectTimer);
    if (!navigator.onLine) { t.status = 'Offline'; renderConnection(); return; }
    t.status = t.connected ? 'Reconnecting' : 'Connecting'; renderConnection();
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    // Attach at this browser's size so tmux and the CLI don't redraw at a default size first.
    let dims; try { if (wrap.clientWidth) dims = fit.proposeDimensions(); } catch (_) {}
    const size = dims?.cols && dims?.rows ? `?cols=${dims.cols}&rows=${dims.rows}` : '';
    const ws = new WebSocket(`${proto}://${location.host}/ws/term/${encodeURIComponent(name)}${size}`); t.ws = ws;
    ws.onmessage = e => { if (t.ws === ws && !t.disposed) term.write(e.data); };
    ws.onopen = () => { if (t.disposed || t.ws !== ws) return ws.close(); if (t.connected) term.write('\x1b[!p\x1b[H\x1b[2J'); /* soft reset + clear screen; keep scrollback */ t.connected = true; t.attempts = 0; t.status = 'Connected'; renderConnection(); setTimeout(t.sendResize, 50); };
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      if (t.disposed || t.ws !== ws) return;
      t.status = navigator.onLine ? 'Reconnecting' : 'Offline'; renderConnection();
      if (document.hidden || !navigator.onLine) return;
      const delay = Math.min(15000, 800 * 2 ** Math.min(t.attempts++, 5)) + Math.random() * 400;
      t.reconnectTimer = setTimeout(t.connect, delay);
    };
  };
  // xterm keeps the current line in its hidden textarea until Enter. Phone
  // keyboards then autocorrect or re-compose earlier words, and xterm's
  // before/after diff sends word fragments. Empty it after each finished
  // input (after xterm's own deferred read), so there is no context to rewrite.
  const area = term.textarea; let composing = false;
  {
    // Keyboard diagnostics, only while ~/.cli_blades/kbdebug.on exists on the host:
    // records what a touch keyboard sends to /api/debug/keyboard (keyboard-debug.log).
    const log = [], t0 = performance.now(), on = () => S.kbdebug, rec = (o) => on() && log.push({ ms: Math.round(performance.now() - t0), ...o, value: area.value, sel: [area.selectionStart, area.selectionEnd] });
    for (const type of ['keydown', 'keyup', 'beforeinput', 'input', 'compositionstart', 'compositionupdate', 'compositionend', 'selectionchange'])
      wrap.addEventListener(type, e => { if (e.target === area) rec({ type, key: e.key, code: e.keyCode, inputType: e.inputType, data: e.data, composing: e.isComposing }); }, true);
    term.onData(d => rec({ type: 'SENT', data: d }));
    setInterval(() => { if (log.length) api('POST', '/api/debug/keyboard', { ua: navigator.userAgent, events: log.splice(0) }).catch(() => {}); }, 2000);
    setTimeout(() => { if (on()) toast('Keyboard diagnostics on'); }, 1500);
  }
  const clearArea = () => setTimeout(() => { if (!composing && area.value) area.value = ''; }, 0);
  area.setAttribute('autocomplete', 'off'); area.setAttribute('data-gramm', 'false');
  {
    // Touch keyboards (e.g. Samsung, Gboard) compose every word. xterm only
    // sends it on compositionend, so the cursor sits still and the word
    // vanishes and reappears on space. Send composition changes live instead:
    // keep the common prefix, backspace the rest, type the new tail.
    // xterm registers its textarea listeners first (capture), so intercept on
    // the wrapper's capture phase, before the event reaches the textarea, and
    // keep IME events away from xterm's composition helper and its
    // keyCode-229 textarea diff. Hardware keys still go to xterm untouched.
    let sent = [];
    const send = data => { if (data) term.input(data, true); };
    const sync = text => {
      const next = [...(text || '')]; let same = 0;
      while (same < sent.length && same < next.length && sent[same] === next[same]) same++;
      send('\x7f'.repeat(sent.length - same) + next.slice(same).join('')); sent = next;
    };
    let imeKey = false;
    // Mode is read per event, so changing it in Settings applies immediately.
    const own = (type, handler) => wrap.addEventListener(type, e => { if (e.target === area && liveTyping()) handler(e); }, true);
    own('keydown', e => { imeKey = e.keyCode === 229; if (imeKey || composing) e.stopPropagation(); });
    own('compositionstart', e => { e.stopPropagation(); composing = true; sent = []; });
    own('compositionupdate', e => { e.stopPropagation(); sync(e.data); });
    own('compositionend', e => { e.stopPropagation(); sync(e.data); composing = false; sent = []; clearArea(); });
    own('input', e => {
      if (e.inputType === 'insertCompositionText' || composing) { e.stopPropagation(); return; }
      // Text from an IME key (keyCode 229) that xterm never saw as a keydown.
      if (imeKey && e.inputType === 'insertText' && e.data) { e.stopPropagation(); send(e.data); }
      else if (imeKey && e.inputType === 'deleteContentBackward') { e.stopPropagation(); send('\x7f'); }
      imeKey = false; clearArea();
    });
  }
  area.addEventListener('compositionstart', () => { if (!liveTyping()) composing = true; });
  area.addEventListener('compositionend', () => { if (!liveTyping()) { composing = false; setTimeout(clearArea, 0); } });
  area.addEventListener('input', e => { if (!liveTyping() && !e.isComposing && !composing) clearArea(); });
  // Input is sent once only. Never queue keystrokes across a dropped connection.
  term.onData(data => { if (t.ws?.readyState === WebSocket.OPEN) t.ws.send(data); else toast('Connection interrupted. Reconnect before typing.'); });
  term.textarea?.addEventListener('focus', () => requestAnimationFrame(t.sendResize));
  S.terms[name] = t; t.connect(); return t;
}
function dropTerm(name) { const t = S.terms[name]; if (!t) return; t.disposed = true; clearTimeout(t.reconnectTimer); try { t.ws?.close(); } catch (_) {} t.term.dispose(); t.wrap.remove(); delete S.terms[name]; }
function renderConnection() {
  const t = S.terms[S.active], node = $('#connection'); node.hidden = !t || S.settings;
  if (t) { node.textContent = t.status; node.classList.toggle('connected', t.status === 'Connected'); }
}
function activate(name) {
  const changed = S.active !== (name || null);
  if (changed) saveDraft();
  S.active = name || null;
  storage.set('blades.active', S.active || '');
  S.settings = false; $('#settings').hidden = true;
  document.querySelectorAll('.term').forEach(w => w.classList.toggle('active', w.dataset.name === name));
  $('#home').hidden = !!name;
  if (!name) renderHome();
  const t = name && ensureTerm(name); if (t) { t.wrap.classList.add('active'); setTimeout(() => { t.sendResize(); if (changed && !isMobile() && !document.querySelector('.modal.show')) t.term.focus(); }, 30); }
  if (changed) $('#composeText').value = name ? storage.get(draftKey(name)) : '';
  renderComposer(); renderConnection();
  renderStrip(); renderBladebar(); renderProjectsIfChanged(); syncMobileNav();
}
const STATE_ORDER = { attention: 0, busy: 1, starting: 2, unknown: 3, idle: 4, closing: 5 };
function openSessions() { return [...S.blades].sort((a, b) => (STATE_ORDER[a.state] ?? 3) - (STATE_ORDER[b.state] ?? 3) || a.project.localeCompare(b.project) || a.name.localeCompare(b.name)); }
function sessionLabel(b) {
  const dup = S.blades.filter(x => x.slug === b.slug).length > 1;
  return `${b.project} · ${providerLabel(b)}${dup ? ' ' + b.name.split('-').pop() : ''}`;
}
function renderOpenSessions() {
  const box = $('#openList'); box.innerHTML = ''; const f = S.filter.toLowerCase();
  const list = openSessions().filter(b => !f || (b.project + ' ' + providerLabel(b)).toLowerCase().includes(f));
  $('#openBox').hidden = !list.length; $('#openCount').textContent = String(list.length);
  for (const b of list) {
    const c = el('div', 'child open-session' + (b.name === S.active ? ' active' : '')); c.dataset.state = b.state;
    c.append(el('span', 'dot'), el('span', 'clabel', sessionLabel(b)), el('span', 'cstate', stateLabel(b) + (b.note ? ' · ' + b.note : '')), el('span', 'cage', fmtAge(b.age_sec)));
    c.title = `${b.name}\n${b.cwd || ''}`; c.onclick = () => { activate(b.name); setView('terminal'); };
    box.append(c);
  }
}
function renderStrip() {
  const strip = $('#strip'); strip.innerHTML = '';
  const counts = { idle: 0, busy: 0, attention: 0, closing: 0, starting: 0 };
  for (const b of S.blades) counts[b.state] = (counts[b.state] || 0) + 1;
  // Every open session, most urgent first, so none is hidden under its project.
  for (const b of openSessions()) {
    const t = el('div', 'tile' + (b.name === S.active ? ' active' : '')); t.dataset.state = b.state;
    t.append(el('span', 'dot')); t.append(el('span', 'label', sessionLabel(b)));
    t.title = `${b.name}\n${b.note || b.state}`; t.onclick = () => { activate(b.name); setView('terminal'); }; strip.append(t);
  }
  const sm = $('#summary'); sm.innerHTML = '';
  const add = (k, label, cls) => { const s = el('span', cls || ''); s.innerHTML = `<b>${counts[k] || 0}</b> ${label}`; sm.append(s); };
  add('attention', 'need you', 'attn'); add('busy', 'busy'); add('idle', 'idle'); if (counts.starting) add('starting', 'starting');
  document.title = (counts.attention ? `(${counts.attention}) ` : '') + (S.instanceName ? S.instanceName + ' · ' : '') + 'CLI Commander';
}
function renderBladebar() {
  const b = bladeByName(S.active);
  const info = $('#bladeInfo');
  $('#bladebar').classList.toggle('home', !b);
  if (!b) { info.textContent = S.settings ? 'Settings' : 'WORKSPACE / SESSIONS'; $('#connection').hidden = true; return; }
  const p = projBySlug(b.slug); const age = fmtAge(b.age_sec);
  info.textContent = `${b.project} / ${providerLabel(b)} · ${stateLabel(b)} · open ${age}${idleHint(b) ? ' · ' + idleHint(b) : ''}`;
  info.title = b.cwd;
  $('#keepBtn').hidden = b.protected || b.state === 'closing' || b.idle_remaining_sec == null;
  $('#saveBtn').disabled = b.state !== 'idle' || b.protected || b.provider === 'shell';
  $('#saveBtn').title = b.protected ? 'Unprotect this session before sending a save command' : b.provider === 'shell' ? 'Save-state prompts are available for assistant sessions' : b.state !== 'idle' ? 'Available when the assistant is idle' : 'Send the save-state command';
  $('#closeBtn').disabled = b.state !== 'idle';
  $('#closeBtn').title = b.state !== 'idle' ? 'Wait until this session is idle before winding down' : 'Save a handoff, archive, and close';
}
const fmtAge = (s = 0) => s < 60 ? `${Math.max(0, Math.floor(s))}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
function idleHint(b) { return b.protected ? 'Protected' : b.idle_pending ? 'Saving soon · Keep open to cancel' : b.idle_remaining_sec != null && b.state === 'idle' ? `Auto-save in ${fmtAge(b.idle_remaining_sec)}` : ''; }

let refreshPromise = null;
function refresh(fresh = false) {
  if (refreshPromise) {
    // Pollers share the current read. A mutation must wait for a read begun
    // after it completed, so an older response cannot erase its new session.
    return fresh ? refreshPromise.then(() => refresh()) : refreshPromise;
  }
  refreshPromise = refreshNow().finally(() => { refreshPromise = null; });
  return refreshPromise;
}
async function refreshNow() {
  try {
    const d = await api('GET', '/api/blades'); S.blades = d.live || []; S.recent = d.recent || [];
    const restoreName = !S.restored ? storage.get('blades.active') : ''; S.restored = true;
    for (const name of Object.keys(S.terms)) if (!bladeByName(name)) { dropTerm(name); }
    if (S.active && !bladeByName(S.active)) { saveDraft(); S.active = null; $('#composeText').value = ''; }
    if (S.settings) { renderStrip(); renderBladebar(); renderProjectsIfChanged(); syncMobileNav(); renderBroadcast(); renderProtected(); }
    else activate(S.active || (restoreName && bladeByName(restoreName) ? restoreName : null));
  } catch (e) { $('#accessBadge').textContent = navigator.onLine ? 'Host reconnecting' : 'Offline'; $('#accessBadge').classList.remove('verified'); }
}
async function windDown(name, cmd = 'wind-down') {
  const blade = bladeByName(name); if (blade && blade.state !== 'idle') return toast('Wait until this session is idle before winding down.');
  try { const result = await api('POST', `/api/blades/${name}/winddown`, { command: cmd }); toast(result.ok ? 'Saving a handoff, then closing' : result.already ? 'This session is already winding down' : 'Wind-down was not started'); await refresh(true); } catch (e) { toast(e.message); }
}
async function killBlade(name) {
  if (!name) return;
  const btn = $('#killBtn'); if (btn.dataset.armed !== name) { btn.dataset.armed = name; btn.textContent = 'Force close?'; toast('Tap Force close again to archive output and stop this session.'); setTimeout(() => { btn.dataset.armed = ''; btn.textContent = '✕'; }, 3500); return; }
  btn.dataset.armed = ''; btn.textContent = '✕';
  try { await api('DELETE', `/api/blades/${name}`); toast('Session closed · snapshot preserved in Recently closed'); await refresh(true); } catch (e) { toast('Close failed: ' + e.message); }
}

/* ---------- home: read-only overview ---------- */
const _screens = {};
let _homeTick = 0;
const STATE_RANK = { attention: 0, busy: 1, starting: 2, idle: 3, closing: 4 };
async function renderHome() {
  if (S.active) return;
  const grid = $('#homeGrid'); const now = Date.now();
  renderFleet(); renderHistory();
  const search = S.sessionSearch.toLowerCase();
  const matches = b => !search || [b.project, b.name, providerLabel(b), b.note].join(' ').toLowerCase().includes(search);
  const want = new Set();
  const ordered = S.blades.filter(b => matches(b) && (S.sessionFilter === 'all' || b.state === S.sessionFilter || S.sessionFilter === 'busy' && b.state === 'starting')).sort((a, b) => (STATE_RANK[a.state] ?? 9) - (STATE_RANK[b.state] ?? 9) || a.project.localeCompare(b.project));
  $('#homeEmpty').hidden = ordered.length > 0 || S.sessionFilter === 'saved';
  $('#homeEmpty strong').textContent = S.blades.length ? 'No matching sessions.' : 'Ready when you are.';
  $('#homeEmpty span').textContent = S.blades.length ? 'Try another filter or project name.' : 'Start a session in a project. It stays here when you disconnect.';
  $('#emptyAddProject').hidden = S.projects.some(p => p.exists !== false && !p.hidden) || !!S.blades.length;
  $('#sessionHeading').textContent = S.sessionFilter === 'saved' ? 'Session history' : ({ all: 'Live sessions', busy: 'Running', attention: 'Needs your attention', idle: 'Idle sessions' }[S.sessionFilter]);
  for (const b of ordered) {
    want.add(b.name);
    let card = grid.querySelector(`[data-name="${b.name}"]`);
    if (!card) { card = el('div', 'card'); card.dataset.name = b.name; card.tabIndex = 0; card.setAttribute('role', 'button'); card.innerHTML = '<div class="head"><span class="dot"></span><span class="title"></span><span class="sub"></span></div><div class="note"></div><pre></pre><div class="card-footer"><span class="provider-tag"></span><span class="idle-hint"></span><span class="card-open">Open ↗</span></div>'; card.onclick = () => { activate(b.name); setView('terminal'); }; card.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); card.click(); } }; }
    grid.append(card);                       // append moves an existing node: keeps the sort order
    card.dataset.state = b.state;
    const n = b.name.split('-').pop(); const dup = S.blades.filter(x => x.slug === b.slug).length > 1;
    card.querySelector('.title').textContent = b.project + (dup ? ' · cli ' + n : '');
    card.querySelector('.sub').textContent = `${stateLabel(b)} · open ${fmtAge(b.age_sec)}`;
    card.querySelector('.note').textContent = b.note || b.cwd || '';
    card.querySelector('.provider-tag').textContent = providerLabel(b);
    card.querySelector('.provider-tag').dataset.provider = b.provider || 'claude';
    card.querySelector('.idle-hint').textContent = idleHint(b);
    card.classList.toggle('idle-pending', !!b.idle_pending);
    if (now - (_screens[b.name]?.ts || 0) > 4000) {
      _screens[b.name] = { ts: now, text: _screens[b.name]?.text || '' };
      api('GET', `/api/blades/${b.name}/screen?lines=14`).then(r => { const t = r.text.split('\n').filter(l => l.trim() && !/^[─═\s]+$/.test(l)).slice(-8).join('\n'); _screens[b.name].text = t; const c = grid.querySelector(`[data-name="${b.name}"] pre`); if (c) c.textContent = t; }).catch(() => {});
    } else card.querySelector('pre').textContent = _screens[b.name].text;
  }
  grid.querySelectorAll('.card').forEach(c => { if (!want.has(c.dataset.name)) c.remove(); });
  // Terminal.app windows: compact, collapsed list (they are not blades)
  const det = $('#homeLegacy'); det.hidden = !S.legacy.length;
  $('#homeLegacyCount').textContent = S.legacy.length ? `(${S.legacy.length})` : '';
  const list = $('#homeLegacyList'); list.innerHTML = '';
  for (const l of S.legacy) {
    const row = el('div', 'leg' + (l.protected ? ' protected' : '')); row.dataset.state = l.state;
    row.append(el('span', 'dot'));
    const mid = el('div'); mid.append(el('div', 'name', l.project)); mid.append(el('div', 'sub', `${l.tty} · ${l.etime}${l.note ? ' · ' + l.note : ''}`)); row.append(mid);
    row.append(el('span', 'cage', l.protected ? '🔒' : ''));
    row.onclick = () => openMigrate(l); list.append(row);
  }
  renderHomeIndex();
}
function renderFleet() {
  $('#countAll').textContent = S.blades.length;
  $('#countBusy').textContent = S.blades.filter(b => ['busy', 'starting'].includes(b.state)).length;
  $('#countAttention').textContent = S.blades.filter(b => b.state === 'attention').length;
  $('#countIdle').textContent = S.blades.filter(b => b.state === 'idle').length;
  $('#countSaved').textContent = S.recent.filter(b => b.archive_available || b.resumable).length;
  document.querySelectorAll('#fleet button').forEach(b => { b.classList.toggle('active', b.dataset.state === S.sessionFilter); b.setAttribute('aria-pressed', String(b.dataset.state === S.sessionFilter)); });
}
let historySignature = '';
function renderHistory() {
  const search = S.sessionSearch.toLowerCase();
  const rows = S.recent.filter(b => (!search || [b.project, b.name, providerLabel(b)].join(' ').toLowerCase().includes(search)) && (S.sessionFilter !== 'saved' || b.archive_available || b.resumable));
  $('#historySection').hidden = !rows.length && S.sessionFilter !== 'saved';
  const sig = JSON.stringify(rows); if (sig === historySignature) return; historySignature = sig;
  $('#historyCount').textContent = rows.length;
  const list = $('#historyList'); list.innerHTML = '';
  if (!rows.length) list.append(el('div', 'history-empty', 'Closed sessions with saved output will appear here.'));
  for (const b of rows) {
    const row = el('div', 'history-row');
    const icon = el('span', 'history-icon', '↳');
    const copy = el('div', 'history-copy'); copy.append(el('strong', '', b.project || b.slug));
    const when = b.closed_at ? new Date(b.closed_at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : 'Closed';
    const hasDraft = !!storage.get(draftKey(b.name));
    copy.append(el('span', '', `${providerLabel(b)} · ${when}${hasDraft ? ' · unsent draft' : ''}${b.note ? ' · ' + b.note : ''}`));
    const actions = el('div', 'history-actions');
    const archive = el('button', '', hasDraft ? 'Output + draft' : 'View output'); archive.disabled = !b.archive_available && !hasDraft; archive.title = b.archive_available ? 'Read saved terminal output' : hasDraft ? 'Read the unsent draft saved on this device' : 'No terminal archive available'; archive.onclick = () => openArchive(b);
    const resume = el('button', 'resume-button', 'Resume ↗'); resume.disabled = !b.resumable; resume.title = b.resumable ? 'Continue the same assistant conversation' : 'No resumable assistant conversation recorded'; resume.onclick = () => resumeBlade(b, resume);
    const fresh = el('button', '', 'Fresh'); fresh.title = 'Start a new conversation in this project'; fresh.onclick = () => openLauncher(b.slug);
    actions.append(archive, resume, fresh); row.append(icon, copy, actions); list.append(row);
  }
}
async function openArchive(b) {
  $('#archiveTitle').textContent = b.project + ' / ' + providerLabel(b); $('#archiveMeta').textContent = b.name; $('#archiveText').textContent = 'Loading saved output…'; $('#archiveModal').classList.add('show');
  const draft = storage.get(draftKey(b.name)); const sections = draft ? ['UNSENT DRAFT · THIS DEVICE\n' + draft] : [];
  try { const archive = b.archive_available ? await api('GET', `/api/blades/${encodeURIComponent(b.name)}/archive`) : {}; sections.push(archive.handoff ? 'HANDOFF\n' + archive.handoff : '', archive.screen || (!archive.handoff ? archive.text : '') || 'No terminal output available.'); }
  catch (e) { sections.push('Could not load archive: ' + e.message); }
  $('#archiveText').textContent = sections.filter(Boolean).join('\n\n────────────────────────\n\n');
}
async function resumeBlade(b, button) {
  busy(button, 'Opening…');
  try { const result = await api('POST', `/api/blades/${encodeURIComponent(b.name)}/resume`, {}); await refresh(true); const name = result.name || result.blade?.name; if (name) { const draft = storage.get(draftKey(b.name)); if (draft && !storage.get(draftKey(name))) storage.set(draftKey(name), draft); activate(name); setView('terminal'); toast(draft ? 'Conversation resumed · draft restored' : 'Conversation resumed'); } }
  catch (e) { toast('Resume failed: ' + e.message); }
  finally { unbusy(button); }
}
let _indexSig = '';
function renderHomeIndex() {
  const sig = S.projects.map(p => p.slug + p.status_line + p.modified + (p.git ? p.git.state + p.git.dirty + p.git.ahead : '') + p.blades.length).join('|') + JSON.stringify(S.audit?.counts || {});
  if (sig === _indexSig) return; _indexSig = sig;
  const al = $('#auditLine'); al.innerHTML = '';
  if (S.audit) {
    const c = S.audit.counts; const bit = (n, label, warn) => { const sp = el('span', warn && n ? 'warn' : ''); sp.innerHTML = `<b>${n}</b> ${label}`; return sp; };
    al.append(el('span', '', `Audit ${S.audit.generated}:`), bit(c.unregistered, 'unregistered', true), bit(c.dirty, 'dirty', true), bit(c.unpushed, 'unpushed', true), bit(c.stale_state, 'stale STATE.md', true), bit(c.no_state, 'no STATE.md', true), bit(c.handoff, 'handoff pending'));
    const a = el('a', '', 'details'); a.onclick = () => $('#auditBtn').click(); al.append(a);
  }
  const ul = $('#homeIndex'); ul.innerHTML = '';
  for (const p of S.projects) {
    if (p.hidden) continue;
    const li = el('li', /retired|archived|dormant/i.test(p.status) ? 'dim' : '');
    const pn = el('div', 'pn'); pn.append(document.createTextNode(p.name));
    const g = p.git;
    if (g && g.is_git) { const gh = el(g.remote ? 'a' : 'span', 'gh ' + g.state); gh.innerHTML = GH_SVG; if (g.remote) { gh.href = g.remote.url; gh.target = '_blank'; } gh.title = `git ${g.state}${g.branch ? ' · ' + g.branch : ''}`; pn.append(gh); const bits = []; if (g.dirty) bits.push(`●${g.dirty}`); if (g.ahead) bits.push(`↑${g.ahead}`); if (bits.length) pn.append(el('span', 'gitbits ' + g.state, bits.join(' '))); }
    if (p.blades.length) pn.append(el('span', 'badge count', String(p.blades.length)));
    if (p.handoff) pn.append(el('span', 'badge handoff', 'handoff'));
    const plus = el('button', 'plus', '+'); plus.title = 'Open a blade'; plus.onclick = () => openBlade(p.slug); pn.append(plus);
    pn.onclick = (e) => { if (e.target === plus || e.target.closest('a')) return; openConfig(p.slug); };
    pn.title = 'Click: prompt & notes · +: open a blade';
    li.append(pn);
    const ps = el('div', 'ps'); ps.append(el('span', 'txt', p.status_line || p.status || '')); ps.append(el('span', 'mod', p.modified ? 'modified ' + p.modified : '')); ps.title = p.status_line || '';
    li.append(ps); ul.append(li);
  }
}
$('#homeBtn').onclick = () => activate(null);

/* ---------- audit: unregistered folders + loose ends ---------- */
S.audit = null;
async function loadAudit() {
  try { S.audit = await api('GET', '/api/audit'); } catch (_) { return; }
  const u = S.audit.unregistered; const box = $('#unreg'); box.innerHTML = '';
  $('#unregCount').textContent = u.length ? String(u.length) : ''; $('#unregBox').style.display = u.length ? '' : 'none';
  for (const f of u) {
    const row = el('div', 'unreg'); row.append(el('div', 'name', f.name));
    const btns = el('div', 'btns');
    const reg = el('button', '', 'Register'); reg.title = 'Add ' + f.path + ' as a project'; reg.onclick = async () => { try { await api('POST', '/api/audit/register', { path: f.path }); toast(`Registered ${f.name}`); await loadProjects(); await loadAudit(); } catch (e) { toast(e.message); } };
    const ign = el('button', '', 'Ignore'); ign.title = 'Hide this folder from the audit'; ign.onclick = async () => { await api('POST', '/api/audit/ignore', { name: f.name }); await loadAudit(); };
    btns.append(reg, ign); row.append(btns);
    row.append(el('div', 'sub', `${f.is_git ? 'git' : 'no git'}${f.remote ? ' · remote' : ''}${f.has_state ? ' · STATE.md' : ''} · ${f.files} items · ${f.modified}`));
    box.append(row);
  }
  if ($('#auditModal').classList.contains('show')) renderAudit();
  if (!S.active) renderHomeIndex();
}
function renderAudit() {
  const a = S.audit; if (!a) return; const b = $('#auditBody'); b.innerHTML = ''; b.className = 'audit';
  $('#auditWhen').textContent = a.generated;
  const sec = (title, items, fmt) => { const h = el('h4', '', `${title} (${items.length})`); b.append(h); if (!items.length) return b.append(el('div', 'ok', 'none')); const ul = el('ul'); items.forEach(i => ul.append(el('li', '', fmt(i)))); b.append(ul); };
  sec('Unregistered folders', a.unregistered, i => `${i.name} — ${i.is_git ? 'git' : 'no git'}${i.remote ? ', has remote' : ''}, modified ${i.modified}`);
  sec('Registered but path missing', a.missing, i => `${i.name} (${i.path})`);
  sec('Uncommitted changes', a.dirty, i => `${i.name}: ${i.files} files`);
  sec('Unpushed commits', a.unpushed, i => `${i.name}: ${i.commits}`);
  sec('No GitHub remote', a.no_remote, i => i.name);
  sec('STATE.md stale vs last commit', a.stale_state, i => `${i.name}: STATE ${i.state_md}, commit ${i.last_commit}`);
  sec('No STATE.md', a.no_state, i => i.name);
  sec('Pending handoff notes', a.handoff, i => i.name);
}
$('#auditBtn').onclick = async () => { $('#auditModal').classList.add('show'); $('#auditBody').textContent = 'loading…'; await loadAudit(); renderAudit(); };
$('#auditClose').onclick = () => $('#auditModal').classList.remove('show');
$('#auditWrite').onclick = async () => { const r = await api('POST', '/api/audit/run'); toast('Wrote ' + r.report_path.split('/').pop()); };

/* ---------- legacy Terminal.app sessions ---------- */
async function loadLegacy() {
  try { S.legacy = await api('GET', '/api/legacy'); } catch (_) { return; }
  const box = $('#legacy'); box.innerHTML = '';
  $('#legacyCount').textContent = S.legacy.length ? String(S.legacy.length) : '';
  $('#legacyBox').style.display = S.legacy.length ? '' : 'none';
  if (!S.active) renderHome();
  for (const l of S.legacy) {
    const row = el('div', 'leg' + (l.migrating ? ' migrating' : '')); row.dataset.state = l.state;
    row.append(el('span', 'dot'));
    const mid = el('div'); mid.append(el('div', 'name', l.project)); mid.append(el('div', 'sub', `${l.tty} · ${l.etime}${l.note ? ' · ' + l.note : ''}${l.slug ? '' : ' · not in projects.md'}`)); row.append(mid);
    const btns = el('span', 'lbtns');
    const lk = el('span', 'lock' + (l.protected ? ' on' : ''), l.protected ? '🔒' : '🔓'); lk.title = l.protected ? 'Protected: batch commands skip this window' : 'Protect from batch commands'; lk.onclick = (e) => { e.stopPropagation(); toggleProtect(l.tty); }; btns.append(lk);
    const b = el('button', '', l.migrating ? '…' : 'Migrate'); b.disabled = l.migrating; b.onclick = (e) => { e.stopPropagation(); openMigrate(l); }; btns.append(b); row.append(btns);
    if (l.protected) row.classList.add('protected');
    row.onclick = () => openMigrate(l); box.append(row);
  }
}
async function openMigrate(l) {
  S.mig = l;
  $('#migTitle').textContent = `${l.project} — ${l.tty}`;
  $('#migInfo').textContent = `pid ${l.pid} · up ${l.etime} · ${l.cwd}`;
  $('#migScreen').textContent = 'loading screen…'; $('#migCands').textContent = 'loading transcripts…';
  const sel = $('#migTarget'); sel.innerHTML = '<option value="">(none — just close the Terminal session)</option>';
  for (const p of S.projects) { const o = el('option', '', p.name); o.value = p.slug; if (p.slug === l.slug) o.selected = true; sel.append(o); }
  if (!l.slug) { const guess = S.projects.find(p => (l.cwd || '').startsWith(p.path.replace(/\/$/, '') + '/')); if (guess) sel.value = guess.slug; }
  $('#migModal').classList.add('show');
  api('GET', `/api/legacy/${l.pid}/screen?lines=25`).then(r => $('#migScreen').textContent = r.text.trim() || '(empty)').catch(e => $('#migScreen').textContent = e.message);
  api('GET', `/api/legacy/${l.pid}/candidates`).then(cs => {
    const box = $('#migCands'); box.innerHTML = '';
    if (!cs.length) return box.textContent = 'No transcripts found for this directory.';
    for (const c of cs) {
      const row = el('div', 'cand');
      const left = el('div'); left.append(el('div', '', `${c.modified} · ${c.session_id.slice(0, 8)}`)); left.append(el('div', 'ask', c.first_ask || '(no user message found)')); row.append(left);
      const b = el('button', '', 'Resume in blade'); b.onclick = () => migrate('resume', c.session_id); row.append(b); box.append(row);
    }
  }).catch(e => $('#migCands').textContent = e.message);
}
async function migrate(mode, session_id) {
  const l = S.mig; if (!l) return;
  const target_slug = $('#migTarget').value; if (mode === 'resume' && !target_slug) return toast('Pick a project to resume into');
  try { await api('POST', `/api/legacy/${l.pid}/migrate`, { mode, session_id, target_slug, open_blade: !!target_slug }); toast(mode === 'fresh' ? `Winding down ${l.tty}; blade opens when it finishes` : `Resuming into a blade`); $('#migModal').classList.remove('show'); loadLegacy(); } catch (e) { toast(e.message); }
}
$('#migFresh').onclick = () => migrate('fresh');
$('#migSave').onclick = async () => { try { await api('POST', `/api/legacy/${S.mig.pid}/send`, { command: 'save-state' }); toast('Save state sent to Terminal'); } catch (e) { toast(e.message); } };
$('#migClose').onclick = () => $('#migModal').classList.remove('show');

/* ---------- commands ---------- */
async function loadCommands() { S.commands = await api('GET', '/api/commands'); }
function renderCommands() {
  const box = $('#cmdList'); box.innerHTML = '';
  S.commands.forEach((c, i) => {
    const row = el('div', 'cmd');
    const left = el('div'); const id = el('input'); id.value = c.id; id.placeholder = 'id'; id.style.width = '100%'; id.oninput = () => c.id = id.value.trim().toLowerCase().replace(/[^a-z0-9-]+/g, '-');
    const label = el('input'); label.value = c.label || ''; label.placeholder = 'label'; label.style.width = '100%'; label.style.marginTop = '4px'; label.oninput = () => c.label = label.value;
    const chk = el('label', 'chk'); const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!c.close; cb.onchange = () => c.close = cb.checked; chk.append(cb, document.createTextNode('close after')); left.append(id, label, chk);
    const ta = el('textarea'); ta.value = c.text; ta.oninput = () => c.text = ta.value;
    const right = el('div'); const run = el('button', 'primary', 'Send'); run.onclick = () => runCommand(c.id); const del = el('button', '', '🗑'); del.style.marginTop = '4px'; del.onclick = () => { S.commands.splice(i, 1); renderCommands(); };
    right.append(run, el('br'), del); row.append(left, ta, right); box.append(row);
  });
}
async function runCommand(cid) {
  const tgt = S.active ? [S.active] : 'all';
  if (tgt === 'all' && !confirm(`No blade is selected. Send "${cid}" to ALL live blades?`)) return;
  try { const r = await api('POST', `/api/commands/${cid}/run`, { blades: tgt }); const results = Object.values(r), sent = results.filter(value => value === 'sent' || value === 'winddown').length; toast(`${cid}: ${sent} sent${results.length > sent ? ` · ${results.length - sent} skipped or failed` : ''}`); await refresh(true); } catch (e) { toast(e.message); }
}

/* ---------- settings view ---------- */
async function openSettings() {
  S.settings = true;
  $('#settings').hidden = false; $('#home').hidden = true;
  document.querySelectorAll('.term').forEach(w => w.classList.remove('active'));
  await Promise.all([loadCommands(), loadQuick()]); renderCommands(); renderBroadcast(); renderProtected(); renderQuick();
  loadOnboarding().catch(e => { $('#setupSummary').textContent = 'Could not check assistants: ' + e.message; }); loadServerConfig(); loadLocations(); renderProjectList(); renderTyping();
  try { const st = await api('GET', '/api/settings'); $('#globalPrompt').value = st.prompt_template || ''; $('#idleTimeout').value = (st.idle_timeout_min ?? 1440) / 60; $('#idleGrace').value = st.idle_grace_sec ?? 120; renderIdleHelp(); } catch (_) {}
  $('#keybar').hidden = true; renderComposer(); renderConnection(); renderBladebar(); syncMobileNav();
}
function closeSettings() { S.settings = false; $('#settings').hidden = true; activate(S.active); syncMobileNav(); }
function renderBroadcast() {
  const sel = $('#bcCommand'); const cur = sel.value; sel.innerHTML = '<option value="">— free text —</option>';
  for (const c of S.commands) { const o = el('option', '', `${c.label || c.id}${c.close ? ' (winds down)' : ''}`); o.value = c.id; sel.append(o); }
  sel.value = S.commands.some(c => c.id === cur) ? cur : '';
  onBcCommandChange();
  const live = S.blades.filter(b => !['closing', 'starting'].includes(b.state));
  const pb = live.filter(b => b.protected).length, pl = S.legacy.filter(l => l.protected).length;
  $('#bcTargets').textContent = `${live.length} blade${live.length === 1 ? '' : 's'}${pb ? ` (${pb} protected)` : ''} · ${S.legacy.length} Terminal window${S.legacy.length === 1 ? '' : 's'}${pl ? ` (${pl} protected)` : ''}`;
}
function onBcCommandChange() {
  const c = S.commands.find(x => x.id === $('#bcCommand').value);
  $('#bcText').disabled = !!c; if (c) { $('#bcText').value = c.text; $('#bcClose').checked = !!c.close; }
}
/* quick actions (shell commands outside Claude) */
async function loadQuick() { try { S.quick = await api('GET', '/api/quick'); } catch (_) { S.quick = []; } }
function renderQuick() {
  const box = $('#quickList'); box.innerHTML = '';
  S.quick.forEach((q, i) => {
    const row = el('div', 'cmd');
    const left = el('div');
    const id = el('input'); id.value = q.id; id.placeholder = 'id'; id.style.width = '100%'; id.oninput = () => q.id = id.value.trim().toLowerCase().replace(/[^a-z0-9-]+/g, '-');
    const label = el('input'); label.value = q.label || ''; label.placeholder = 'label'; label.style.width = '100%'; label.style.marginTop = '4px'; label.oninput = () => q.label = label.value;
    const cwd = el('input'); cwd.value = q.cwd || '~'; cwd.placeholder = 'working dir'; cwd.style.width = '100%'; cwd.style.marginTop = '4px'; cwd.oninput = () => q.cwd = cwd.value;
    const chk = el('label', 'chk'); const cb = el('input'); cb.type = 'checkbox'; cb.checked = !!q.keep_open; cb.onchange = () => q.keep_open = cb.checked; chk.append(cb, document.createTextNode('keep open')); left.append(id, label, cwd, chk);
    const ta = el('textarea'); ta.value = q.command; ta.placeholder = 'shell command'; ta.oninput = () => q.command = ta.value;
    const right = el('div');
    const run = el('button', 'primary', '▶ Run'); run.onclick = () => runQuick(q.id, run);
    const log = el('button', '', 'log'); log.style.marginTop = '4px'; log.onclick = () => showQuickLog(q.id);
    const del = el('button', '', '🗑'); del.style.marginTop = '4px'; del.onclick = () => { S.quick.splice(i, 1); renderQuick(); };
    right.append(run, el('br'), log, el('br'), del); row.append(left, ta, right); box.append(row);
  });
  if (!S.quick.length) box.append(el('div', 'hint', 'No quick actions yet.'));
}
async function runQuick(qid, btn) {
  const q = S.quick.find(x => x.id === qid); if (!q) return;
  busy(btn, 'Running…');
  try {
    const r = await api('POST', `/api/quick/${qid}/run`, {});
    if (r.mode === 'keep_open') { toast(`Opened ${r.blade} under Shell Commands`); await loadProjects(); await refresh(true); }
    else { toast(`Started (pid ${r.pid}); output logged`); setTimeout(() => showQuickLog(qid), 2500); }
  } catch (e) { toast(e.message); }
  unbusy(btn);
}
async function showQuickLog(qid) {
  try { const r = await api('GET', `/api/quick/${qid}/log`); const pre = $('#quickLog'); pre.hidden = false; pre.textContent = r.log ? `${r.log} (${r.modified})\n\n${r.text}` : 'no log yet'; pre.scrollTop = pre.scrollHeight; } catch (e) { toast(e.message); }
}
function renderProtected() {
  const items = [...S.blades.filter(b => b.protected).map(b => `${b.project} · ${b.name}`), ...S.legacy.filter(l => l.protected).map(l => `${l.project} · Terminal ${l.tty}`)];
  $('#protList').textContent = items.length ? items.join('\n') : 'none';
}
async function broadcast() {
  const cid = $('#bcCommand').value, text = $('#bcText').value.trim(), close = $('#bcClose').checked;
  const blades = $('#bcBlades').checked, legacyOn = $('#bcLegacy').checked;
  if (!cid && !text) return toast('Pick a command or type a message');
  if (!blades && !legacyOn) return toast('Pick at least one target group');
  const what = close ? 'WIND DOWN (save, exit, close)' : 'send this message to';
  if (!confirm(`${what} every ${[blades ? 'blade' : '', legacyOn ? 'Terminal window' : ''].filter(Boolean).join(' and ')}? Protected windows are skipped.`)) return;
  const btn = $('#bcSend'); busy(btn, 'Sending…');
  try {
    const body = { include_legacy: legacyOn, close };
    if (cid) body.command = cid; else body.text = text;
    if (!blades) body.blades = [];
    const r = await api('POST', '/api/broadcast', body);
    const lines = Object.entries(r.results).map(([k, v]) => `${v.padEnd(20)} ${k}`);
    $('#bcResult').hidden = false; $('#bcResult').textContent = lines.length ? lines.join('\n') : 'no targets';
    toast(`Broadcast: ${lines.length} target${lines.length === 1 ? '' : 's'}`); await refresh(true); await loadLegacy();
  } catch (e) { toast(e.message); }
  unbusy(btn);
}
function busy(btn, label) { btn.dataset.orig = btn.textContent; btn.textContent = label; btn.disabled = true; }
function unbusy(btn) { btn.textContent = btn.dataset.orig; btn.disabled = false; }

/* ---------- project config ---------- */
async function openConfig(slug) {
  const p = projBySlug(slug); if (!p) return; S.cfgSlug = slug;
  const c = await api('GET', `/api/projects/${slug}/config`);
  $('#cfgTitle').textContent = `${p.name} — prompt & notes`;
  $('#cfgNotes').value = c.notes || ''; $('#cfgRelated').value = (c.related || []).join(', '); $('#cfgPrompt').value = c.prompt || '';
  $('#cfgHandoff').textContent = c.handoff || 'none'; $('#cfgPreview').textContent = c.preview || '';
  $('#cfgModal').classList.add('show');
}
async function saveConfig() {
  const body = { notes: $('#cfgNotes').value, prompt: $('#cfgPrompt').value, related: $('#cfgRelated').value.split(',').map(s => s.trim()).filter(Boolean) };
  await api('PUT', `/api/projects/${S.cfgSlug}/config`, body);
  const c = await api('GET', `/api/projects/${S.cfgSlug}/config`); $('#cfgPreview').textContent = c.preview; toast('Saved'); await loadProjects();
}

/* ---------- mobile views: sessions (home) / projects (drawer) / terminal ---------- */
function setView(v) {
  if (!isMobile()) return;
  const side = $('#side'), bd = $('#backdrop');
  if (v === 'projects') { side.classList.add('show'); bd.classList.add('show'); }
  else { side.classList.remove('show'); bd.classList.remove('show'); }
  syncSidebarAccess();
  if (v === 'settings') { openSettings(); return; }
  if (v === 'sessions') { activate(null); }
  if (v === 'terminal') { if (!S.active && S.blades.length) activate(S.blades[S.blades.length - 1].name); else if (S.active) activate(S.active); }
  document.querySelectorAll('#mnav button').forEach(b => b.classList.toggle('active', b.dataset.view === (v === 'terminal' && !S.active ? 'sessions' : v)));
  $('#keybar').hidden = !(v === 'terminal' && S.active);
  renderComposer();
  const t = S.terms[S.active]; if (t && v === 'terminal') setTimeout(() => t.sendResize(), 60);
}
document.querySelectorAll('#mnav button').forEach(b => b.onclick = () => setView(b.dataset.view));
$('#backdrop').onclick = () => setView(S.active ? 'terminal' : 'sessions');
$('#sideClose').onclick = () => setView(S.active ? 'terminal' : 'sessions');
function syncMobileNav() {
  syncSidebarAccess();
  renderComposer();
  if (!isMobile()) { $('#keybar').hidden = !S.active || S.settings; return; }
  const open = $('#side').classList.contains('show');
  const v = open ? 'projects' : S.settings ? 'settings' : (S.active ? 'terminal' : 'sessions');
  document.querySelectorAll('#mnav button').forEach(b => b.classList.toggle('active', b.dataset.view === v));
  $('#keybar').hidden = !(v === 'terminal');
  $('#mnavCount').textContent = S.blades.length ? String(S.blades.length) : '';
}
function syncSidebarAccess() {
  const side = $('#side'); side.inert = isMobile() && !side.classList.contains('show');
}

/* Drafts belong to a session and stay on this device, including when that session closes. */
function saveDraft() {
  if (!S.active) return true;
  const saved = storage.set(draftKey(S.active), $('#composeText').value);
  $('#draftHint').textContent = saved ? 'Draft saved on this device' : 'Draft storage unavailable · keep this page open';
  return saved;
}
function renderComposer() {
  $('#composer').hidden = !S.active || S.settings || $('#side').classList.contains('show');
  $('#composeSend').disabled = !S.active || !$('#composeText').value.trim() || !!$('#composeSend').dataset.sending;
}
const draftKeepalives = new Map();
function keepDraftSessionOpen() {
  const name = S.active, now = Date.now();
  if (!name || now - (draftKeepalives.get(name) || 0) < 30000) return;
  draftKeepalives.set(name, now);
  // Leading throttle: a typed draft cancels grace immediately. Failed calls are never replayed.
  api('POST', `/api/blades/${encodeURIComponent(name)}/keepalive`, {}).catch(() => {
    if (S.active === name) $('#draftHint').textContent = 'Draft saved on this device · could not extend the session';
  });
}
async function sendComposer(e) {
  e.preventDefault(); const name = S.active, text = $('#composeText').value;
  if (!name || !text.trim() || $('#composeSend').dataset.sending) return;
  const button = $('#composeSend'); button.dataset.sending = '1'; busy(button, 'Sending'); saveDraft();
  try {
    await api('POST', `/api/blades/${encodeURIComponent(name)}/send`, { text, enter: true });
    if (S.active === name && $('#composeText').value === text) { $('#composeText').value = ''; storage.set(draftKey(name), ''); }
    else if (storage.get(draftKey(name)) === text) storage.set(draftKey(name), '');
    toast('Message sent'); await refresh(true);
  } catch (e) { toast('Send not confirmed. Check the session before resending; your draft is kept.'); }
  finally { delete button.dataset.sending; unbusy(button); renderComposer(); }
}
async function loadStatus() {
  try {
    const status = await api('GET', '/api/status'); const badge = $('#accessBadge'); S.kbdebug = !!status.kbdebug; renderInstanceName(status.instance_name);
    if (pageVersion && status.ui_version && status.ui_version !== pageVersion) {
      $('#updateBtn').hidden = false; $('header').classList.add('has-update');
    }
    const verified = status.lan_access === false && status.origin_protection === true;
    badge.textContent = verified ? (status.tailscale ? 'Local + Tailscale' : 'Local access') : 'Check access settings';
    badge.classList.toggle('verified', verified); badge.title = status.access || 'Access configuration';
    $('#runtimeInfo').textContent = `${status.access || 'Access status unavailable'} · ${status.origin_protection ? 'Browser origin checks enabled' : 'Origin checks unavailable'}`;
  } catch (_) { $('#accessBadge').textContent = 'Access unverified'; $('#accessBadge').classList.remove('verified'); }
}
function recoverConnections() {
  if (document.hidden) return;
  for (const t of Object.values(S.terms)) t.connect();
  refresh(); loadStatus();
}
function resizeViewport() {
  document.documentElement.style.setProperty('--viewport-height', (window.visualViewport?.height || window.innerHeight) + 'px');
  const t = S.terms[S.active]; if (t) requestAnimationFrame(t.sendResize);
}
function renderIdleHelp() {
  const hours = Number($('#idleTimeout').value);
  $('#idleHelp').textContent = hours > 0 ? `${hours} ${hours === 1 ? 'hour' : 'hours'} after confirmed inactivity; busy sessions stay open. Protected sessions stay open too.` : 'Automatic close is off. Sessions stay open until you close them.';
}

/* ---------- wiring ---------- */
$('#updateBtn')?.addEventListener('click', () => {
  if ($('#composeSend').dataset.sending) return toast('Wait for your message to finish sending before reloading.');
  if (!saveDraft()) return toast('Draft could not be saved. Copy your message before reloading.');
  location.reload();
});
$('#setupNext').onclick = nextSetup;
$('#setupBack').onclick = () => { if (!S.setupBusy) { S.setupStep = 0; renderSetup(); } };
const closeSetup = () => { $('#setupModal').classList.remove('show'); S.setupPendingLaunch = null; };
$('#setupClose').onclick = closeSetup;
$('#setupReview').onclick = () => { S.setupPendingLaunch = null; openSetup(); };
$('#setupRescan').onclick = async () => { const button = $('#setupRescan'); busy(button, 'Checking…'); try { await loadOnboarding(true); toast('Assistant status updated'); } catch (e) { toast('Could not check assistants: ' + e.message); } finally { unbusy(button); } };
$('#setupReset').onclick = async () => {
  const button = $('#setupReset');
  if (!button.dataset.armed) { button.dataset.armed = '1'; button.textContent = 'Reset choices?'; setTimeout(() => { delete button.dataset.armed; if (!button.disabled) button.textContent = 'Reset setup'; }, 5000); return; }
  delete button.dataset.armed; busy(button, 'Resetting…');
  try { await api('POST', '/api/onboarding/reset', {}); await loadOnboarding(); S.setupPendingLaunch = null; storage.set('blades.provider', ''); toast('Assistant choices reset · sessions and accounts preserved'); await openSetup(); }
  catch (e) { toast('Could not reset setup: ' + e.message); }
  finally { button.disabled = false; button.textContent = 'Reset setup'; }
};
$('#settingsAddProject').onclick = () => openProjectDialog(false);
$('#instanceSave').onclick = async () => {
  const button = $('#instanceSave'); busy(button, 'Saving…');
  try { const result = await api('POST', '/api/server-config', { instance_name: $('#instanceInput').value }); renderInstanceName(result.instance_name); toast('Name saved'); }
  catch (e) { toast(e.message); } finally { unbusy(button); }
};
$('#typingMode').onchange = e => { storage.set('blades.typing', e.target.value === 'auto' ? '' : e.target.value); renderTyping(); toast('Typing mode saved on this device'); };
$('#locationAdd').onsubmit = async (e) => {
  e.preventDefault(); const input = $('#locationPath'), value = input.value.trim(); if (!value || !S.locations) return;
  if (!(value.startsWith('/') || value.startsWith('~/'))) { $('#locationError').textContent = 'Enter an absolute folder path or one starting with ~/.'; $('#locationError').hidden = false; return; }
  if (await saveLocations([...S.locations.locations.map(l => l.path), value], 'Code location added')) input.value = '';
};
$('#emptyAddProject').onclick = () => openProjectDialog(true);
$('#newAddProject').onclick = () => openProjectDialog(true);
$('#projectClose').onclick = () => $('#projectModal').classList.remove('show');
$('#projectSave').onclick = addProject;
$('#sideAddProject').onclick = () => openProjectDialog(false);
$('#projectModeNew').onclick = () => setProjectMode('new');
$('#projectModeExisting').onclick = () => setProjectMode('existing');
$('#projectNewName').oninput = updateProjectPreview;
$('#projectParent').oninput = updateProjectPreview;
$('#setupPortSave').onclick = saveServerConfig;
$('#newSessionBtn').onclick = () => openLauncher();
$('#newClose').onclick = () => $('#newModal').classList.remove('show');
$('#newProject').onchange = renderProviders;
$('#newLaunch').onclick = () => launchAssistant($('#newProject').value, S.provider);
$('#archiveClose').onclick = () => $('#archiveModal').classList.remove('show');
document.querySelectorAll('#fleet button').forEach(button => button.onclick = () => { S.sessionFilter = button.dataset.state; renderHome(); });
$('#sessionSearch').oninput = e => { S.sessionSearch = e.target.value; renderHome(); };
$('#composeText').oninput = () => { saveDraft(); renderComposer(); keepDraftSessionOpen(); };
$('#composeText').onkeydown = e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') sendComposer(e); };
$('#composer').onsubmit = sendComposer;
$('#keepBtn').onclick = async () => { try { await api('POST', `/api/blades/${encodeURIComponent(S.active)}/keepalive`, {}); toast('Inactivity timer reset'); await refresh(true); } catch (e) { toast(e.message); } };
$('#idleTimeout').oninput = renderIdleHelp;
$('#idleSave').onclick = async () => {
  const hours = Number($('#idleTimeout').value), grace = Number($('#idleGrace').value);
  if (!Number.isFinite(hours) || hours < 0 || hours > 168 || !Number.isFinite(grace) || grace < 30 || grace > 3600) return toast('Enter 0–168 hours and a grace period of at least 30 seconds.');
  try { await api('PUT', '/api/settings', { idle_timeout_min: Math.round(hours * 60), idle_grace_sec: grace }); toast('Session lifecycle saved'); await refresh(true); } catch (e) { toast(e.message); }
};
$('#menuBtn').onclick = () => setView($('#side').classList.contains('show') ? (S.active ? 'terminal' : 'sessions') : 'projects');
$('#filter').oninput = (e) => { S.filter = e.target.value; renderProjects(); };
$('#editBtn').onclick = () => { S.editing = !S.editing; $('#editBtn').textContent = S.editing ? 'Done' : 'Edit'; renderProjects(); };
$('#settingsBtn').onclick = () => S.settings ? closeSettings() : openSettings();
$('#bcCommand').onchange = onBcCommandChange;
$('#bcSend').onclick = broadcast;
$('#quickAdd').onclick = () => { S.quick.push({ id: 'new-action', label: 'New action', command: '', cwd: '~', keep_open: false }); renderQuick(); };
$('#quickSave').onclick = async () => { await api('PUT', '/api/quick', S.quick); await loadQuick(); renderQuick(); toast('Quick actions saved'); };
$('#cmdAdd').onclick = () => { S.commands.push({ id: 'new-command', label: 'New', text: '', close: false }); renderCommands(); };
$('#cmdSave').onclick = async () => { await api('PUT', '/api/commands', S.commands); await loadCommands(); renderCommands(); renderBroadcast(); toast('Commands saved'); };
$('#cfgBtn').onclick = () => { const b = bladeByName(S.active); if (b) openConfig(b.slug); else toast('Open a blade first, or right-click a project'); };
$('#cfgClose').onclick = () => $('#cfgModal').classList.remove('show');
$('#cfgSave').onclick = saveConfig;
$('#cfgClearHandoff').onclick = async () => { await api('DELETE', `/api/projects/${S.cfgSlug}/handoff`); $('#cfgHandoff').textContent = 'none'; await loadProjects(); };
$('#cfgGlobal').onclick = () => { $('#cfgModal').classList.remove('show'); openSettings(); };
$('#globalSave').onclick = async () => { await api('PUT', '/api/settings', { prompt_template: $('#globalPrompt').value }); toast('Default prompt saved'); };
$('#shellBtn').onclick = () => { const b = bladeByName(S.active); if (b) openBlade(b.slug, true); else toast('Pick a project first'); };
$('#saveBtn').onclick = async () => {
  const name = S.active; if (!name) return;
  try { const results = await api('POST', '/api/commands/save-state/run', { blades: [name] }); const result = results[name]; toast(result === 'sent' ? 'Save-state command sent' : result ? `Save state: ${result}` : 'Save state skipped · session must be idle and unprotected'); await refresh(true); }
  catch (e) { toast(e.message); }
};
$('#closeBtn').onclick = () => S.active && windDown(S.active);
$('#killBtn').onclick = () => killBlade(S.active);
document.querySelectorAll('#keybar [data-key]').forEach(b => { b.onpointerdown = e => e.preventDefault(); b.onclick = () => S.active && api('POST', `/api/blades/${S.active}/send`, { key: b.dataset.key }).catch(e => toast('Key not confirmed: ' + e.message)); });
$('#kbBtn').onclick = () => { const t = S.terms[S.active]; if (t) t.term.focus(); };
document.querySelectorAll('.modal').forEach(m => m.addEventListener('click', e => { if (e.target === m) { if (m.id === 'setupModal') closeSetup(); else m.classList.remove('show'); } }));
window.addEventListener('resize', () => { resizeViewport(); renderStrip(); if (!isMobile()) { $('#side').classList.remove('show'); $('#backdrop').classList.remove('show'); } syncMobileNav(); });
window.visualViewport?.addEventListener('resize', resizeViewport);
window.addEventListener('focus', () => { const t = S.terms[S.active]; if (t) requestAnimationFrame(t.sendResize); });
window.addEventListener('online', recoverConnections);
window.addEventListener('offline', () => { for (const t of Object.values(S.terms)) { t.status = 'Offline'; clearTimeout(t.reconnectTimer); t.ws?.close(); } renderConnection(); $('#accessBadge').textContent = 'Offline'; $('#accessBadge').classList.remove('verified'); });
document.addEventListener('visibilitychange', () => {
  // Brief tab switches keep the connection, so the shared tmux window isn't resized
  // to another client and back. Long-hidden tabs (and backgrounded phones) still detach.
  clearTimeout(S.hiddenTimer);
  if (document.hidden) { saveDraft(); S.hiddenTimer = setTimeout(() => { if (document.hidden) for (const t of Object.values(S.terms)) { clearTimeout(t.reconnectTimer); t.ws?.close(); } }, 120000); }
  else { recoverConnections(); const t = S.terms[S.active]; if (t) requestAnimationFrame(t.sendResize); }
});
window.addEventListener('pagehide', saveDraft);
document.addEventListener('keydown', e => {
  const modal = [...document.querySelectorAll('.modal.show')].pop();
  if (modal && e.key === 'Tab') { const controls = [...modal.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex="0"]')].filter(node => node.offsetParent !== null); const first = controls[0], last = controls[controls.length - 1]; if (first && (!modal.contains(document.activeElement) || e.shiftKey && document.activeElement === first || !e.shiftKey && document.activeElement === last)) { e.preventDefault(); (e.shiftKey ? last : first).focus(); } }
  if ((e.metaKey || e.ctrlKey) && e.key === 'k' && !modal) { e.preventDefault(); if (isMobile()) setView('projects'); $('#filter').focus(); }
  if (e.key === 'Escape' && !e.target.closest('.xterm')) { if ($('#setupModal').classList.contains('show')) closeSetup(); else modal?.classList.remove('show'); }
});

(async () => {
  resizeViewport(); renderBladebar(); renderComposer(); syncMobileNav();
  await Promise.allSettled([loadProjects(), loadCommands(), loadLegacy(), loadProviders(), loadStatus()]);
  loadAudit(); setInterval(loadAudit, 120000);
  await refresh();
  if (!S.onboarding?.completed) await openSetup();
  setInterval(() => { if (!document.hidden) refresh(); }, 2500);
  setInterval(() => { if (!document.hidden) loadProjects().catch(() => {}); }, 20000);
  setInterval(() => { if (!document.hidden) loadLegacy(); }, 10000);
  setInterval(() => { if (!document.hidden) loadStatus(); }, 30000);
})();
})();

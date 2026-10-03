// Actual setup functions in a fake DOM/API. No browser, network or provider.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../blades/static/app.js'), 'utf8');
class Node {
  constructor(tag = 'div', cls = '', text = '') { this.tag = tag; this.children = []; this.dataset = {}; this._text = text; this.classList = { add() {}, remove() {}, toggle() {} }; this.value = ''; }
  append(...nodes) { this.children.push(...nodes); }
  set textContent(text) { this._text = text; this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent || '').join(' '); }
  set innerHTML(value) { assert.equal(value, ''); this.children = []; this._text = ''; }
  focus() {}
}
const nodes = new Map();
const $ = key => { if (!nodes.has(key)) nodes.set(key, new Node()); return nodes.get(key); };
const S = { providers: [], projects: [{ exists: true }], onboarding: null };
let saved = { completed: false, enabled_providers: [], ollama_model: '' }, failSave = false;
const calls = [];
const entry = () => ({ ...saved, providers: [
  { id: 'claude', label: 'Claude', available: true, auth: { state: 'ready', detail: 'Saved account ready' } },
  { id: 'ollama', label: 'Ollama chat', available: true, opt_in: true, auth: { state: 'unavailable', detail: 'Local-only backend unavailable' } }
], system: { tmux_available: true } });
const api = async (method, url, body) => {
  calls.push({ method, url, body });
  if (method === 'POST') {
    if (failSave) throw new Error('Synthetic save rejected');
    saved = { ...saved, ...body, completed: true };
  }
  return entry();
};
const context = vm.createContext({ S, $, api, console, URL, Set,
  document: { querySelectorAll: () => [], querySelector: $, createElement: tag => new Node(tag) },
  el: (tag, cls, text) => new Node(tag, cls, text || ''),
  assistantName: p => p.label, toast: () => {}, openLauncher: () => {}, openProjectDialog: () => {}, copySetupText: () => {} });
const begin = source.indexOf('function enabledProviders()');
const end = source.indexOf('function copyWithSelection(');
assert(begin >= 0 && end > begin);
vm.runInContext(source.slice(begin, end), context);
function all(node) { return [node, ...node.children.flatMap(all)]; }
(async () => {
  await context.openSetup();
  assert.equal(S.setupSelected.has('claude'), true);
  assert.equal(S.setupSelected.has('ollama'), false, 'Ollama must be explicit opt-in');
  let checkbox = all($('#setupBody')).find(n => n.dataset.setupProvider === 'ollama');
  checkbox.checked = true; checkbox.onchange();
  const model = all($('#setupBody')).find(n => n.placeholder === 'model:tag');
  model.value = 'example:local'; model.oninput();
  assert.equal(S.setupOllamaModel, 'example:local');
  assert.match($('#setupBody').textContent, /Finish setup saves this model/);
  await context.recheckSetup();
  assert.equal(S.setupOllamaModel, 'example:local', 'Recheck must preserve draft model');
  assert.equal(saved.ollama_model, '', 'Recheck must not silently save');
  await context.nextSetup();
  assert.equal(S.setupStep, 1);
  assert.equal($('#setupTitle').textContent, 'Check provider readiness');
  assert.doesNotMatch($('#setupBody').textContent, /will prompt when opened|Signed in on this Mac/);
  await context.nextSetup();
  assert.equal(saved.ollama_model, 'example:local');
  assert.deepEqual(Array.from(saved.enabled_providers), ['claude', 'ollama']);
  assert.equal(S.setupStep, 2);
  assert.equal($('#setupTitle').textContent, 'Setup saved');
  assert.match($('#setupBody').textContent, /Local-only backend unavailable/);
  assert.doesNotMatch($('#setupTitle').textContent, /ready/i);
  await context.openSetup();
  assert.equal(S.setupSelected.has('ollama'), true);
  assert.equal(S.setupOllamaModel, 'example:local');
  S.setupStep = 1; failSave = true;
  await context.nextSetup();
  assert.equal(S.setupStep, 1, 'Failed save must not claim setup completion');
  assert.match($('#setupError').textContent, /Synthetic save rejected/);
  assert.equal($('#setupError').hidden, false);
  console.log('PASS: opt-in, draft preservation, saved-model payload, honest readiness and failed save');
})().catch(err => { console.error(err); process.exitCode = 1; });

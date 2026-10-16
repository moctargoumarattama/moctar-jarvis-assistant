'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const dashboard = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');

function sourceOf(name) {
  const start = dashboard.search(new RegExp(`^      (?:async )?function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, `dashboard function ${name} exists`);
  const end = dashboard.indexOf('\n      }', start);
  assert.notEqual(end, -1, `dashboard function ${name} is closed`);
  return dashboard.slice(start, end + '\n      }'.length);
}

function element() {
  const listeners = new Map();
  return {
    value: '', textContent: '', hidden: false, disabled: false, open: false,
    dataset: {}, attributes: {}, children: [],
    classList: { toggle() {} },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(name, listener) {
      const list = listeners.get(name) || [];
      list.push(listener);
      listeners.set(name, list);
    },
    dispatch(name, event = {}) { for (const listener of listeners.get(name) || []) listener(event); },
    focus() { this.focused = true; },
    showModal() { this.open = true; },
    close() { this.open = false; },
    reset() { this.value = ''; }
  };
}

function voiceFixture() {
  const nodes = new Map();
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  const storageWrites = [];
  let resumes = 0;
  let suspends = 0;
  const engine = {
    enabled: true, state: 'wake',
    get listeningDesired() { return this.enabled; },
    async enable() { this.enabled = true; },
    disable() { this.enabled = false; },
    resume() { resumes++; },
    suspend() { suspends++; }
  };
  const document = Object.assign(element(), { hidden: false });
  const window = Object.assign(element(), {
    jarvisVoice: engine,
    MoctarVoice: { create: () => engine },
    speechSynthesis: { resume() {} },
    SpeechRecognition: class {},
    isSecureContext: true
  });
  const context = vm.createContext({
    window, document, $: get, jarvisState: { busy: false },
    speechPlaybackBlocked: false,
    localStorage: { getItem: () => null, setItem: (...args) => storageWrites.push(args) },
    set: (id, value) => { get(id).textContent = value; },
    cancelJarvisSpeech() {}, speakJarvisText() {}, sendJarvisPrompt() {},
    console
  });
  vm.runInContext(['renderBrowserVoice', 'setBrowserVoiceEnabled', 'resumeBrowserVoice', 'initialiseBrowserVoice'].map(sourceOf).join('\n'), context);
  return { context, window, document, engine, storageWrites, get,
    get resumes() { return resumes; }, get suspends() { return suspends; } };
}

test('temporary recognition errors preserve the enabled microphone preference', () => {
  const f = voiceFixture();
  for (const message of ['no-speech', 'aborted', 'audio-capture', 'network']) {
    f.context.renderBrowserVoice('error', { message });
    assert.equal(f.engine.enabled, true);
    assert.equal(f.get('home-voice-toggle').attributes['aria-pressed'], 'true');
  }
  assert.deepEqual(f.storageWrites, []);
});

test('returning focus resumes recognition only when the dashboard is visible', () => {
  const f = voiceFixture();
  f.context.initialiseBrowserVoice();
  f.window.dispatch('focus');
  assert.equal(f.resumes, 1);
  f.document.hidden = true;
  f.window.dispatch('focus');
  assert.equal(f.resumes, 1);
  f.document.hidden = false;
  f.window.dispatch('focus');
  assert.equal(f.resumes, 2);
  f.engine.enabled = false;
  f.window.dispatch('focus');
  assert.equal(f.resumes, 2, 'focus must not reactivate a deliberately disabled microphone');
});

test('visibility suspension retains microphone intent and resumes on return', () => {
  const f = voiceFixture();
  f.context.initialiseBrowserVoice();
  f.document.hidden = true;
  f.document.dispatch('visibilitychange');
  assert.equal(f.suspends, 1);
  assert.equal(f.engine.enabled, true);
  f.document.hidden = false;
  f.document.dispatch('visibilitychange');
  assert.equal(f.resumes, 1);
  assert.deepEqual(f.storageWrites, []);
});

test('explicitly disabling the microphone persists the user decision', async () => {
  const f = voiceFixture();
  await f.context.setBrowserVoiceEnabled(false);
  assert.equal(f.engine.enabled, false);
  assert.deepEqual(f.storageWrites, [['jarvis_wakeword_active', 'false']]);
});

function removalFixture(servers = [{ hostname: 'old-vps', stale: true }]) {
  const nodes = new Map();
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  get('vps-remove-auth').value = 'x-vps-token';
  const requests = [], notices = [], storageWrites = [];
  let reloads = 0;
  let response = { ok: true, status: 200, json: async () => ({ ok: true }) };
  const state = { snapshot: { vps: servers }, vpsRemovalHost: '', vpsRemovalPending: false };
  const context = vm.createContext({
    state, $: get,
    set: (id, value) => { get(id).textContent = value; },
    apiFetch: async (url, options) => { requests.push({ url, options }); return response; },
    readResponse: response => response.json(),
    loadSnapshot: async () => { reloads++; return true; },
    toast: (...args) => notices.push(args),
    localStorage: { setItem: (...args) => storageWrites.push(args) },
    console
  });
  vm.runInContext(['openVpsRemoval', 'removeVpsFromDashboard'].map(sourceOf).join('\n'), context);
  return { context, get, state, requests, notices, storageWrites,
    set response(value) { response = value; }, get reloads() { return reloads; } };
}

test('VPS removal is available for disconnected nodes but refuses fresh or unknown hosts', () => {
  for (const flag of ['stale', 'restored', 'archived']) {
    const f = removalFixture([{ hostname: 'old-vps', [flag]: true }, { hostname: 'live-vps' }]);
    f.get('vps-remove-token').value = 'previous-secret';
    f.context.openVpsRemoval('unknown-vps');
    assert.equal(f.get('vps-remove-dialog').open, false);
    f.context.openVpsRemoval('live-vps');
    assert.equal(f.get('vps-remove-dialog').open, false);
    f.context.openVpsRemoval('old-vps');
    assert.equal(f.get('vps-remove-dialog').open, true);
    assert.equal(f.state.vpsRemovalHost, 'old-vps');
    assert.equal(f.get('vps-remove-host').textContent, 'old-vps');
    assert.equal(f.get('vps-remove-token').value, '', 'a prior credential must never be reused');
  }
});

test('VPS removal sends the selected management header, clears the secret and refreshes the snapshot', async () => {
  for (const header of ['x-vps-token', 'x-gateway-secret']) {
    const hostname = 'old-node.example';
    const f = removalFixture([{ hostname, archived: true }]);
    f.context.openVpsRemoval(hostname);
    f.get('vps-remove-token').value = 'management-secret';
    f.get('vps-remove-auth').value = header;
    let prevented = false;
    const task = f.context.removeVpsFromDashboard({ preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(f.get('vps-remove-token').value, '', 'secret is removed before awaiting the server');
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].url, '/api/vps/' + encodeURIComponent(hostname));
    assert.equal(f.requests[0].options.method, 'DELETE');
    assert.equal(f.requests[0].options.headers[header], 'management-secret');
    assert.equal(f.requests[0].options.body, undefined);
    await task;
    assert.equal(f.reloads, 1);
    assert.equal(f.get('vps-remove-dialog').open, false);
    assert.equal(f.get('vps-remove-submit').disabled, false);
    assert.equal(f.state.vpsRemovalPending, false);
    assert.ok(f.notices.length > 0);
    assert.deepEqual(f.storageWrites, [], 'management credentials must never enter browser storage');
    assert.equal(JSON.stringify(f.state).includes('management-secret'), false);
  }
});

test('VPS removal without a credential leaves the dialog open and sends no request', async () => {
  const f = removalFixture();
  f.context.openVpsRemoval('old-vps');
  await f.context.removeVpsFromDashboard({ preventDefault() {} });
  assert.equal(f.requests.length, 0);
  assert.equal(f.get('vps-remove-dialog').open, true);
  assert.ok(f.get('vps-remove-error').textContent);
  assert.equal(f.get('vps-remove-submit').disabled, false);
});

test('VPS removal errors clear the credential and allow a retry without losing the target', async () => {
  const f = removalFixture();
  f.context.openVpsRemoval('old-vps');
  f.get('vps-remove-token').value = 'bad-secret';
  f.response = { ok: false, status: 403, json: async () => ({ detail: 'Acces refuse' }) };
  await f.context.removeVpsFromDashboard({ preventDefault() {} });
  assert.equal(f.get('vps-remove-dialog').open, true);
  assert.equal(f.state.vpsRemovalHost, 'old-vps');
  assert.equal(f.get('vps-remove-token').value, '');
  assert.ok(f.get('vps-remove-error').textContent);
  assert.equal(f.get('vps-remove-submit').disabled, false);
  assert.equal(f.state.vpsRemovalPending, false);
  assert.equal(f.reloads, 0);
  f.get('vps-remove-token').value = 'good-secret';
  f.response = { ok: true, status: 200, json: async () => ({ ok: true }) };
  await f.context.removeVpsFromDashboard({ preventDefault() {} });
  assert.equal(f.get('vps-remove-dialog').open, false);
  assert.equal(f.requests.length, 2);
  assert.equal(f.reloads, 1);
  assert.deepEqual(f.storageWrites, []);
});

test('VPS removal handles network failure and keeps a usable retry action', async () => {
  const f = removalFixture();
  f.context.openVpsRemoval('old-vps');
  f.get('vps-remove-token').value = 'management-secret';
  f.context.apiFetch = async () => { throw new Error('Network unavailable'); };
  await f.context.removeVpsFromDashboard({ preventDefault() {} });
  assert.equal(f.get('vps-remove-dialog').open, true);
  assert.equal(f.state.vpsRemovalHost, 'old-vps');
  assert.equal(f.get('vps-remove-token').value, '');
  assert.match(f.get('vps-remove-error').textContent, /Network unavailable/);
  assert.equal(f.get('vps-remove-submit').disabled, false);
  assert.equal(f.state.vpsRemovalPending, false);
});

test('VPS removal prevents duplicate submissions while the server is responding', async () => {
  const f = removalFixture();
  f.context.openVpsRemoval('old-vps');
  f.get('vps-remove-token').value = 'management-secret';
  let finishRequest;
  let requests = 0;
  f.context.apiFetch = () => {
    requests++;
    return new Promise(resolve => { finishRequest = resolve; });
  };
  const first = f.context.removeVpsFromDashboard({ preventDefault() {} });
  assert.equal(f.get('vps-remove-submit').disabled, true);
  await f.context.removeVpsFromDashboard({ preventDefault() {} });
  assert.equal(requests, 1);
  finishRequest({ ok: true, status: 200, json: async () => ({ ok: true }) });
  await first;
  assert.equal(f.get('vps-remove-submit').disabled, false);
  assert.equal(f.state.vpsRemovalPending, false);
});

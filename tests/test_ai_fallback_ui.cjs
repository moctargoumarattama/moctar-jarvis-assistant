'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
function section(start, end) {
  const offset = html.indexOf(start);
  assert.notEqual(offset, -1, start);
  const limit = html.indexOf(end, offset);
  assert.notEqual(limit, -1, end);
  return html.slice(offset, limit);
}

function frontend({query, status, queryError, statusError} = {}) {
  const nodes = new Map();
  const messages = [];
  const requests = [];
  function element() {
    return {style: {}, dataset: {}, events: {}, open: false, opens: 0,
      append() {}, focus() {}, remove() {}, addEventListener(name, fn) {this.events[name] = fn;},
      showModal() {this.open = true; this.opens++;}, close() {this.open = false;}};
  }
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
  };
  const context = {
    $, set: (id, text) => {$(id).textContent = text;}, jarvisState: {},
    document: {createElement: element}, window: {open() {}},
    clockFormat: {format: () => '12:00'}, setInterval: () => 1, clearInterval() {},
    console: {warn() {}}, appendChatMessage: (...args) => {messages.push(args);}, speakJarvisText() {},
    readResponse: response => response.json(),
    apiFetch: async (url, options, timeout) => {
      requests.push({url, timeout});
      if (url.endsWith('/query') && queryError) throw queryError;
      if (url.endsWith('/status') && statusError) throw statusError;
      return {ok: true, json: async () => url.endsWith('/query') ? query : status};
    },
  };
  vm.createContext(context);
  vm.runInContext(section('      function jarvisAiHealth(', '      async function openJarvisModal(') +
    section('      async function sendJarvisPrompt(', '      function speakJarvisText('), context);
  return {context, $, requests, messages};
}

test('Gemini query response suppresses stale Ollama offline flags and reports the active model', async () => {
  const ui = frontend({query: {status: 'success', reply: 'Réponse Cloud', online: true,
    active_provider: 'gemini', model: 'gemini-3.5-flash-lite', gemini_online: true,
    ollama_online: false, model_offline: true}});
  ui.$('model-offline-modal').open = true;
  await ui.context.sendJarvisPrompt('analyse');
  assert.equal(ui.$('model-offline-modal').open, false);
  assert.equal(ui.$('model-offline-modal').opens, 0);
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR EN LIGNE');
  assert.equal(ui.$('jarvis-model-badge').textContent, 'Gemini Cloud · gemini-3.5-flash-lite');
  assert.equal(ui.$('home-ai-badge').textContent, 'Gemini Cloud · gemini-3.5-flash-lite');
  assert.equal(ui.messages.at(-1)[1], 'Réponse Cloud');
  assert.equal(ui.requests[0].timeout, 60000);
});

test('autonomous reply alerts only when the response confirms both providers are unavailable', async () => {
  const ui = frontend({query: {status: 'success', reply: 'Tâche locale exécutée',
    online: false, model_offline: true, all_providers_offline: true,
    gemini_online: false, ollama_online: false}});
  await ui.context.sendJarvisPrompt('état système');
  assert.equal(ui.$('model-offline-modal').open, true);
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR HORS LIGNE');
});

test('a legacy model_offline flag alone cannot open the alert', async () => {
  const ui = frontend({query: {status: 'success', reply: 'Réponse', model_offline: true}});
  await ui.context.sendJarvisPrompt('état système');
  assert.equal(ui.$('model-offline-modal').opens, 0);
});

test('an HTTP query timeout checks cloud health and keeps Gemini online without an alert', async () => {
  const ui = frontend({queryError: Object.assign(new Error('timeout'), {name: 'AbortError'}),
    status: {available: true, online: true, gemini_online: true, ollama_online: false,
      active_provider: 'gemini', model: 'gemini-3.5-flash-lite'}});
  await ui.context.sendJarvisPrompt('analyse');
  assert.deepEqual(ui.requests.map(request => request.url), ['/api/jarvis/query', '/api/jarvis/status']);
  assert.equal(ui.$('model-offline-modal').opens, 0);
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR EN LIGNE');
  assert.equal(ui.$('jarvis-send-btn').disabled, false);
});

test('a network failure cannot infer that both model providers are offline', async () => {
  const ui = frontend({queryError: new Error('network'), statusError: new Error('network')});
  await ui.context.sendJarvisPrompt('analyse');
  assert.equal(ui.$('model-offline-modal').opens, 0);
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR STANDBY');
});

test('bridge availability never overrides confirmed offline AI health', async () => {
  const ui = frontend({status: {available: true, status: 'ready', online: false,
    all_providers_offline: true, model_offline: true}});
  await ui.context.fetchJarvisStatus(true);
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR HORS LIGNE');
  assert.equal(ui.$('model-offline-modal').open, true);
});

test('retry accepts Gemini recovery while Ollama remains offline and closes the alert', async () => {
  const ui = frontend({status: {online: true, active_provider: 'gemini',
    gemini_online: true, ollama_online: false, model: 'gemini-3.5-flash-lite'}});
  ui.$('model-offline-modal').open = true;
  await ui.$('model-offline-retry-btn').events.click();
  assert.equal(ui.$('model-offline-modal').open, false);
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR EN LIGNE');
  assert.equal(ui.$('model-offline-retry-btn').disabled, false);
});

test('all dashboard inline scripts remain valid JavaScript', () => {
  for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) {
    new vm.Script(match[1]);
  }
});

test('home and chat display the effective provider and model through fallback and recovery', () => {
  const ui = frontend();
  for (const [provider, model, expected] of [
    ['ollama', 'qwen2.5:1.5b', 'Hybride · Ollama local · qwen2.5:1.5b'],
    ['gemini', 'gemini-3.5-flash-lite', 'Hybride · Gemini Cloud · gemini-3.5-flash-lite'],
    ['ollama', 'custom-local:latest', 'Hybride · Ollama local · custom-local:latest'],
  ]) {
    ui.context.renderJarvisAiStatus({online: true, provider: 'hybrid', active_provider: provider, model});
    for (const id of ['home-ai-badge', 'jarvis-model-badge']) {
      assert.equal(ui.$(id).textContent, expected);
      assert.equal(ui.$(id).title, expected);
      assert.equal(ui.$(id).dataset.tone, 'success');
    }
  }
});

test('autonomous mode clears the previous model when both AI providers are offline', () => {
  const ui = frontend();
  ui.context.renderJarvisAiStatus({online: true, active_provider: 'gemini', model: 'cloud-model'});
  ui.context.renderJarvisAiStatus({online: false, all_providers_offline: true,
    active_provider: 'local', model: 'configured-but-unavailable'});
  assert.equal(ui.$('home-ai-badge').textContent, 'Mode autonome · sans modèle IA');
  assert.equal(ui.$('jarvis-model-badge').textContent, 'Mode autonome · sans modèle IA');
  assert.equal(ui.$('home-ai-badge').dataset.tone, 'warning');
});

test('missing model metadata does not invent the default model', () => {
  const ui = frontend();
  ui.context.renderJarvisAiStatus({online: true, active_provider: 'gemini'});
  assert.equal(ui.$('home-ai-badge').textContent, 'Gemini Cloud · modèle non communiqué');
});

test('status network failures clear stale model information without reporting offline', async () => {
  const ui = frontend({statusError: new Error('network')});
  ui.context.renderJarvisAiStatus({online: true, active_provider: 'gemini', model: 'cloud-model'});
  await ui.context.fetchJarvisStatus();
  assert.equal(ui.$('home-ai-badge').textContent, 'IA · Statut indisponible');
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR STANDBY');
  assert.equal(ui.$('model-offline-modal').opens, 0);
});

test('a stale status request cannot overwrite the provider of a newer answer', async () => {
  const ui = frontend({status: {online: true, active_provider: 'ollama', model: 'old-local-model'}});
  const pending = ui.context.fetchJarvisStatus();
  ui.context.renderJarvisAiStatus({online: true, active_provider: 'gemini', model: 'current-cloud-model'});
  await pending;
  assert.equal(ui.$('home-ai-badge').textContent, 'Gemini Cloud · current-cloud-model');
});

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
  const intervals = [];
  const timeouts = [];
  const events = {};
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
    document: {createElement: element, hidden: false, addEventListener(name, fn) {events[name] = fn;}}, window: {open() {}},
    clockFormat: {format: () => '12:00'},
    setInterval: (fn, delay) => {intervals.push({fn, delay}); return intervals.length;},
    setTimeout: (fn, delay) => {timeouts.push({fn, delay}); return timeouts.length;}, clearInterval() {},
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
  return {context, $, requests, messages, intervals, timeouts, events,
    modalOpens: () => Array.from(nodes.values()).reduce((count, node) => count + node.opens, 0)};
}

test('a direct Gemini answer displays its model on the home page and in chat', async () => {
  const ui = frontend({query: {status: 'success', reply: 'Réponse Cloud', online: true,
    active_provider: 'gemini', model: 'gemini-3.5-flash-lite', gemini_online: true}});
  await ui.context.sendJarvisPrompt('analyse');
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR EN LIGNE');
  assert.equal(ui.$('jarvis-model-badge').textContent, 'gemini-3.5-flash-lite @ Gemini Cloud');
  assert.equal(ui.$('home-ai-badge').textContent, 'gemini-3.5-flash-lite @ Gemini Cloud');
  assert.equal(ui.messages.at(-1)[1], 'Réponse Cloud');
  assert.equal(ui.requests[0].timeout, 60000);
});

test('an autonomous action remains available when Gemini is unavailable without a blocking modal', async () => {
  const ui = frontend({query: {status: 'success', reply: 'Tâche locale exécutée',
    online: false, model_offline: true, gemini_online: false}});
  await ui.context.sendJarvisPrompt('état système');
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MODE AUTONOME');
  assert.equal(ui.$('home-ai-badge').textContent, 'Gemini Cloud indisponible · Mode autonome');
  assert.equal(ui.messages.at(-1)[1], 'Tâche locale exécutée');
  assert.equal(ui.$('jarvis-send-btn').disabled, false);
  assert.equal(ui.modalOpens(), 0);
});

test('a cloud API error displays its message without blocking subsequent requests', async () => {
  const ui = frontend({query: {status: 'error', reply: 'Quota Gemini dépassé.',
    online: false, gemini_online: false}});
  await ui.context.sendJarvisPrompt('analyse');
  assert.match(ui.messages.at(-1)[1], /Quota Gemini dépassé/);
  assert.equal(ui.$('jarvis-input').disabled, false);
  assert.equal(ui.context.jarvisState.busy, false);
  assert.equal(ui.modalOpens(), 0);
});

test('an HTTP query timeout checks Gemini health without declaring the cloud offline', async () => {
  const ui = frontend({queryError: Object.assign(new Error('timeout'), {name: 'AbortError'}),
    status: {available: true, online: true, gemini_online: true,
      active_provider: 'gemini', model: 'gemini-3.5-flash-lite'}});
  await ui.context.sendJarvisPrompt('analyse');
  assert.deepEqual(ui.requests.map(request => request.url), ['/api/jarvis/query', '/api/jarvis/status']);
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR EN LIGNE');
  assert.equal(ui.$('jarvis-send-btn').disabled, false);
  assert.equal(ui.modalOpens(), 0);
});

test('a network failure reports standby without inferring Gemini is offline', async () => {
  const ui = frontend({queryError: new Error('network'), statusError: new Error('network')});
  await ui.context.sendJarvisPrompt('analyse');
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR STANDBY');
  assert.equal(ui.modalOpens(), 0);
});

test('bridge availability cannot hide a confirmed cloud outage', async () => {
  const ui = frontend({status: {available: true, status: 'ready', online: false,
    gemini_online: false, model_offline: true}});
  await ui.context.fetchJarvisStatus();
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MODE AUTONOME');
  assert.equal(ui.$('home-ai-badge').dataset.tone, 'warning');
  assert.equal(ui.modalOpens(), 0);
});

test('Gemini recovery restores the green model badge after an outage', () => {
  const ui = frontend();
  ui.context.renderJarvisAiStatus({online: false, gemini_online: false});
  ui.context.renderJarvisAiStatus({online: true, active_provider: 'gemini', model: 'recovered-cloud-model'});
  assert.equal(ui.$('home-ai-badge').textContent, 'recovered-cloud-model @ Gemini Cloud');
  assert.equal(ui.$('home-ai-badge').dataset.tone, 'success');
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR EN LIGNE');
  assert.equal(ui.modalOpens(), 0);
});

test('all dashboard inline scripts remain valid JavaScript', () => {
  for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) {
    new vm.Script(match[1]);
  }
});

test('home and chat report the effective Gemini model with a steady green badge', () => {
  const ui = frontend();
  for (const model of ['gemini-3.5-flash-lite', 'custom-cloud-model']) {
    ui.context.renderJarvisAiStatus({online: true, provider: 'gemini', active_provider: 'gemini', model});
    for (const id of ['home-ai-badge', 'jarvis-model-badge']) {
      assert.equal(ui.$(id).textContent, model + ' @ Gemini Cloud');
      assert.equal(ui.$(id).title, model + ' @ Gemini Cloud');
      assert.equal(ui.$(id).dataset.tone, 'success');
    }
  }
  const css = fs.readFileSync(path.join(__dirname, '../static/cosmos.css'), 'utf8');
  assert.match(css, /\.ai-runtime-badge\s*\{[^}]*animation:\s*none/);
  assert.match(css, /\.ai-runtime-badge\[data-tone="success"\]\s*\{[^}]*color:\s*var\(--green\)/);
});

test('an unavailable Gemini model is not displayed as active', () => {
  const ui = frontend();
  ui.context.renderJarvisAiStatus({online: true, model: 'cloud-model'});
  ui.context.renderJarvisAiStatus({online: false, gemini_online: false,
    active_provider: 'local', model: 'configured-but-unavailable'});
  assert.equal(ui.$('home-ai-badge').textContent, 'Gemini Cloud indisponible · Mode autonome');
  assert.equal(ui.$('jarvis-model-badge').textContent, 'Gemini Cloud indisponible · Mode autonome');
  assert.equal(ui.$('home-ai-badge').dataset.tone, 'warning');
});

test('missing cloud model metadata uses the configured architecture default', () => {
  const ui = frontend();
  ui.context.renderJarvisAiStatus({online: true, active_provider: 'gemini'});
  assert.equal(ui.$('home-ai-badge').textContent, 'gemini-3.5-flash-lite @ Gemini Cloud');
});

test('status network failures clear stale model information without reporting a cloud outage', async () => {
  const ui = frontend({statusError: new Error('network')});
  ui.context.renderJarvisAiStatus({online: true, active_provider: 'gemini', model: 'cloud-model'});
  await ui.context.fetchJarvisStatus();
  assert.equal(ui.$('home-ai-badge').textContent, 'IA · Statut indisponible');
  assert.equal(ui.$('jarvis-strip-badge').textContent, 'MOCTAR STANDBY');
  assert.equal(ui.modalOpens(), 0);
});

test('a stale status request cannot overwrite the Gemini model of a newer answer', async () => {
  const ui = frontend({status: {online: true, active_provider: 'gemini', model: 'old-cloud-model'}});
  const pending = ui.context.fetchJarvisStatus();
  ui.context.renderJarvisAiStatus({online: true, active_provider: 'gemini', model: 'current-cloud-model'});
  await pending;
  assert.equal(ui.$('home-ai-badge').textContent, 'current-cloud-model @ Gemini Cloud');
});

test('the obsolete offline dialog and local model controls are removed from the dashboard', () => {
  assert.doesNotMatch(html, /model-offline-modal|showModelOfflinePopup|ollama|hybrid/i);
  const mobileCss = fs.readFileSync(path.join(__dirname, '../static/mobile.css'), 'utf8');
  assert.doesNotMatch(mobileCss, /model-offline-/);
  for (const id of ['home-ai-badge', 'jarvis-model-badge']) {
    assert.match(html, new RegExp('id="' + id + '"[^>]*data-tone="success">gemini-3\\.5-flash-lite @ Gemini Cloud'));
  }
});

test('cloud status refreshes every ten seconds and on visibility without interrupting a request', async () => {
  const ui = frontend({status: {online: true, model: 'gemini-3.5-flash-lite'}});
  vm.runInContext(section('      setTimeout(fetchJarvisStatus, 1500);',
    '      // -----------------------------------------------------------------------'), ui.context);
  assert.equal(ui.timeouts[0].delay, 1500);
  assert.equal(ui.intervals[0].delay, 10000);
  ui.intervals[0].fn();
  await new Promise(setImmediate);
  assert.equal(ui.requests.length, 1);
  ui.context.document.hidden = true;
  ui.intervals[0].fn();
  ui.events.visibilitychange();
  await new Promise(setImmediate);
  assert.equal(ui.requests.length, 1);
  ui.context.document.hidden = false;
  ui.context.jarvisState.busy = true;
  ui.intervals[0].fn();
  ui.events.visibilitychange();
  await new Promise(setImmediate);
  assert.equal(ui.requests.length, 1);
  ui.context.jarvisState.busy = false;
  ui.events.visibilitychange();
  await new Promise(setImmediate);
  assert.equal(ui.requests.length, 2);
});

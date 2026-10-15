'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function worker(overrides = {}) {
  const handlers = {};
  const context = {
    URL,
    self: {location: {origin: 'https://noc.test'}, clients: {claim: async () => {}},
      skipWaiting: async () => {}, addEventListener: (name, fn) => {handlers[name] = fn;}},
    caches: {keys: async () => [], delete: async () => true},
    fetch: async () => new Response('online'), ...overrides,
  };
  context.self.addEventListener = (name, fn) => {handlers[name] = fn;};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/service-worker.js'), 'utf8'), context);
  return handlers;
}

function frontend(options = {}) {
  const events = {};
  const elements = [];
  class Element {
    constructor(tag) {this.tag = tag; this.children = []; this.events = {}; this.open = false; elements.push(this);}
    append(...items) {this.children.push(...items);}
    appendChild(item) {this.append(item);}
    setAttribute(name, value) {this[name] = value;}
    addEventListener(name, fn) {this.events[name] = fn;}
    showModal() {this.open = true;}
    close() {this.open = false;}
  }
  const body = new Element('body');
  const toolbar = new Element('header');
  const registrations = [];
  const window = {
    document: {body, createElement: tag => new Element(tag), querySelector: () => toolbar},
    navigator: {userAgent: 'Chrome', platform: 'Win32', maxTouchPoints: 0,
      serviceWorker: {register: async (...args) => {registrations.push(args);}}},
    isSecureContext: true,
    matchMedia: () => ({matches: false}),
    addEventListener: (name, fn) => {events[name] = fn;}, ...options,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/pwa.js'), 'utf8'), {window});
  return {window, elements, events, registrations, button: elements.find(item => item.id === 'pwa-install'),
    help: elements.find(item => item.id === 'pwa-install-help')};
}

test('service worker deletes only dashboard-owned caches and claims existing clients', async () => {
  const deleted = [];
  let claimed = false;
  const handlers = worker({
    caches: {keys: async () => ['moctar-noc-v1', 'jarvis-noc-old', 'other-project', 'noc-shared'],
      delete: async name => {deleted.push(name);}},
    self: {location: {origin: 'https://noc.test'}, clients: {claim: async () => {claimed = true;}},
      skipWaiting: async () => {}},
  });
  let activation;
  handlers.activate({waitUntil: pending => {activation = pending;}});
  await activation;
  assert.deepEqual(deleted.sort(), ['jarvis-noc-old', 'moctar-noc-v1']);
  assert.equal(claimed, true);
});

test('page and API reads always use network with HTTP cache bypassed', async () => {
  for (const pathname of ['/', '/api/snapshot', '/static/pwa.js']) {
    let options;
    const handlers = worker({fetch: async (request, settings) => {options = settings; return {status: 200};}});
    let pending;
    handlers.fetch({request: new Request('https://noc.test' + pathname), respondWith: value => {pending = value;}});
    assert.equal((await pending).status, 200);
    assert.equal(options.cache, 'no-store');
  }
});

test('offline failures never serve stale pages or fabricated API responses', async () => {
  const handlers = worker({fetch: async () => {throw new Error('offline');}});
  let pending;
  handlers.fetch({request: new Request('https://noc.test/api/snapshot'), respondWith: value => {pending = value;}});
  await assert.rejects(pending, /offline/);
});

test('SSE, external requests and modifying commands bypass the worker', () => {
  const handlers = worker({fetch: () => assert.fail('request must pass through')});
  for (const request of [
    new Request('https://noc.test/api/stream', {headers: {Accept: 'text/event-stream'}}),
    new Request('https://another.test/'),
    new Request('https://noc.test/api/jarvis/query', {method: 'POST'}),
  ]) handlers.fetch({request, respondWith: () => assert.fail('must not intercept')});
});

test('install registration disables worker script caching on secure origins', () => {
  const ui = frontend();
  assert.equal(ui.registrations.length, 1);
  assert.equal(ui.registrations[0][0], '/service-worker.js');
  assert.equal(ui.registrations[0][1].scope, '/');
  assert.equal(ui.registrations[0][1].updateViaCache, 'none');
  assert.equal(ui.button.hidden, false);
});

test('insecure LAN origins keep install hidden without registering a worker', () => {
  const ui = frontend({isSecureContext: false});
  assert.equal(ui.button.hidden, true);
  assert.equal(ui.registrations.length, 0);
});

test('native install prompt requires user click and hides the button after acceptance', async () => {
  const ui = frontend();
  let prompted = 0;
  let prevented = false;
  ui.events.beforeinstallprompt({preventDefault: () => {prevented = true;},
    prompt: async () => {prompted++;}, userChoice: Promise.resolve({outcome: 'accepted'})});
  assert.equal(prevented, true);
  assert.equal(prompted, 0);
  await ui.button.events.click();
  assert.equal(prompted, 1);
  assert.equal(ui.button.hidden, true);
});

test('manual iPhone installation explains Safari and the required online connection', async () => {
  const ui = frontend({navigator: {userAgent: 'iPhone', platform: 'iPhone', maxTouchPoints: 1}});
  await ui.button.events.click();
  assert.equal(ui.help.open, true);
  assert.match(ui.help.children[1].textContent, /Safari.*Partager/);
  assert.match(ui.help.children[2].textContent, /sans cache hors ligne/);
  ui.events.appinstalled();
  assert.equal(ui.help.open, false);
  assert.equal(ui.button.hidden, true);
});

test('an already installed standalone app hides installation affordances', () => {
  assert.equal(frontend({matchMedia: () => ({matches: true})}).button.hidden, true);
});

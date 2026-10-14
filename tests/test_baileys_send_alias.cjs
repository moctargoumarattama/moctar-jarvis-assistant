'use strict';

const assert = require('node:assert/strict');
const http = require('node:http');
const test = require('node:test');
const { attachMessageSendAlias } = require('../integrations/baileys-send-alias.cjs');
const token = 'test-private-gateway-secret';
const administrator = '212770010264';

function mockSocket(extra = {}) {
  const sent = [];
  return {
    user: { id: '212770099999@s.whatsapp.net' }, sent,
    async sendMessage(to, content) { sent.push({ to, content }); return { key: { id: '3EB0TEST' } }; },
    ...extra,
  };
}

async function gateway(context, options = {}) {
  const routes = [];
  const app = { post(path, handler) { routes.push({ path, handler }); } };
  attachMessageSendAlias(app, { gatewaySecret: token, ...options });
  const server = http.createServer(async (request, response) => {
    response.status = code => { response.statusCode = code; return response; };
    response.json = body => { response.setHeader('content-type', 'application/json'); response.end(JSON.stringify(body)); return response; };
    request.get = name => request.headers[name.toLowerCase()];
    const route = routes.find(item => request.method === 'POST' && request.url === item.path);
    if (!route) return response.status(404).json({ ok: false });
    try {
      let body = '';
      for await (const chunk of request) body += chunk;
      request.body = body ? JSON.parse(body) : undefined;
      await route.handler(request, response);
    } catch (_) { response.status(500).json({ ok: false }); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  context.after(() => new Promise(resolve => server.close(resolve)));
  const address = `http://127.0.0.1:${server.address().port}`;
  const post = async (body, supplied = token) => {
    const headers = { 'Content-Type': 'application/json', Connection: 'close' };
    if (supplied !== null) headers['X-Gateway-Secret'] = supplied;
    const response = await fetch(`${address}/message/send`, { method: 'POST', headers, body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  };
  return { app, routes, post };
}

test('authenticated POST normalizes PN device JID and awaits send before success', async context => {
  const socket = mockSocket();
  const { post } = await gateway(context, { sessions: new Map([['1', { sock: socket, status: 'connected' }]]) });
  const result = await post({ to: `${administrator}:17@s.whatsapp.net`, text: '  État opérationnel  ' });
  assert.deepEqual(result, { status: 200, body: { ok: true, id: '3EB0TEST' } });
  assert.deepEqual(socket.sent, [{ to: `${administrator}@s.whatsapp.net`, content: { text: 'État opérationnel' } }]);
});

test('missing or invalid machine token cannot send', async context => {
  const socket = mockSocket();
  const { post } = await gateway(context, { sessions: new Map([['1', socket]]) });
  for (const supplied of [null, 'wrong-secret', token + '-extra']) {
    assert.equal((await post({ to: administrator, text: 'statut' }, supplied)).status, 403);
  }
  assert.equal(socket.sent.length, 0);
});

test('empty explicitly configured secret keeps route disabled', async context => {
  const socket = mockSocket();
  const { post } = await gateway(context, { gatewaySecret: '', sessions: new Map([['1', socket]]) });
  assert.deepEqual(await post({ to: administrator, text: 'statut' }), {
    status: 503, body: { ok: false, error: 'gateway_secret_unconfigured' },
  });
  assert.equal(socket.sent.length, 0);
});

test('configured active session wins and body sessionId cannot override it', async context => {
  const first = mockSocket();
  const configured = mockSocket();
  const { post } = await gateway(context, { sessionId: 'school-2', sessions: new Map([
    ['school-1', { sock: first, connected: true }], ['school-2', { socket: configured, status: 'OPEN' }],
  ]) });
  assert.equal((await post({ to: administrator, text: 'reply', sessionId: 'school-1' })).status, 200);
  assert.equal(configured.sent.length, 1);
  assert.equal(first.sent.length, 0);
});

test('disconnected configured session falls back to first connected session', async context => {
  const disconnected = mockSocket();
  const active = mockSocket();
  const { post } = await gateway(context, { sessions: new Map([
    ['1', { sock: disconnected, status: 'DECONNECTE' }],
    ['unready', { sock: mockSocket({ user: undefined }), status: 'connecting' }],
    ['active', { client: active, connected: true }],
  ]) });
  assert.equal((await post({ to: administrator, text: 'reply' })).status, 200);
  assert.equal(disconnected.sent.length, 0);
  assert.equal(active.sent.length, 1);
});

test('created socket without authenticated or open state is unavailable', async context => {
  const socket = mockSocket({ user: undefined });
  const { post } = await gateway(context, { sessions: new Map([['1', { sock: socket }]]) });
  assert.equal((await post({ to: administrator, text: 'reply' })).status, 503);
  assert.equal(socket.sent.length, 0);
});

test('closed websocket wins over stale authenticated user and ready record', async context => {
  const socket = mockSocket({ ws: { isOpen: false, readyState: 3 } });
  const { post } = await gateway(context, { sessions: new Map([['1', { sock: socket, status: 'ready' }]]) });
  assert.equal((await post({ to: administrator, text: 'reply' })).status, 503);
  assert.equal(socket.sent.length, 0);
});

test('direct Baileys socket with open websocket is supported', async context => {
  const socket = mockSocket({ ws: { isOpen: true, readyState: 1 } });
  const { post } = await gateway(context, { sessions: new Map([['1', socket]]) });
  assert.equal((await post({ to: '+212 770-010-264', message: '  réponse  ' })).status, 200);
  assert.deepEqual(socket.sent, [{ to: `${administrator}@s.whatsapp.net`, content: { text: 'réponse' } }]);
});

test('lazy sessions resolver is not evaluated during route registration', async context => {
  let sessions;
  const instance = await gateway(context, { sessions: () => sessions });
  const socket = mockSocket();
  sessions = new Map([['1', socket]]);
  assert.equal((await instance.post({ to: administrator, text: 'reply' })).status, 200);
  assert.equal(socket.sent.length, 1);
});

test('optional asynchronous getSession resolves configured session', async context => {
  const socket = mockSocket();
  const ids = [];
  const { post } = await gateway(context, { sessionId: 'school', getSession: async id => {
    ids.push(id); return { sock: socket, connection: 'open' };
  } });
  assert.equal((await post({ to: administrator, text: 'reply' })).status, 200);
  assert.deepEqual(ids, ['school']);
});

test('no sessions returns 503 instead of claiming success', async context => {
  const { post } = await gateway(context, { sessions: new Map() });
  assert.deepEqual(await post({ to: administrator, text: 'reply' }), {
    status: 503, body: { ok: false, error: 'whatsapp_session_unavailable' },
  });
});

test('failed send is 502 with safe response and safe log', async context => {
  const warnings = [];
  const socket = mockSocket({ async sendMessage() { throw new Error('secret-token recipient-private message-content'); } });
  const { post } = await gateway(context, { sessions: new Map([['1', socket]]), logger: { warn: value => warnings.push(value) } });
  const result = await post({ to: administrator, text: 'private-text' });
  assert.deepEqual(result, { status: 502, body: { ok: false, error: 'whatsapp_send_failed' } });
  assert.equal(warnings.length, 1);
  assert.equal(warnings[0], 'NOC send alias: whatsapp_send_failed');
});

test('invalid recipient or body never reaches Baileys', async context => {
  const socket = mockSocket();
  const { post } = await gateway(context, { sessions: new Map([['1', socket]]) });
  for (const body of [
    null, [], 'message', {},
    { to: `${administrator}@g.us`, text: 'reply' },
    { to: `${administrator}@lid`, senderPn: administrator, text: 'reply' },
    { to: `${administrator}:bad@s.whatsapp.net`, text: 'reply' },
    { to: 'http://example.com', text: 'reply' },
    { to: '123', text: 'reply' }, { to: administrator, text: ' ' },
    { to: administrator, text: 'x'.repeat(1001) }, { to: administrator, text: 42 },
  ]) {
    assert.equal((await post(body)).status, 400);
  }
  assert.equal(socket.sent.length, 0);
});

test('idempotent registration preserves existing gateway routes', async context => {
  const socket = mockSocket();
  const instance = await gateway(context, { sessions: new Map([['1', socket]]) });
  const original = instance.routes[0].handler;
  assert.equal(attachMessageSendAlias(instance.app, { gatewaySecret: 'other' }), original);
  assert.equal(instance.routes.length, 1);
  assert.equal((await instance.post({ to: administrator, text: 'reply' })).status, 200);
});

test('timeout never retries or starts another send while original is unresolved', async context => {
  let release;
  let sends = 0;
  const socket = mockSocket({ sendMessage() { sends++; return new Promise(resolve => { release = resolve; }); } });
  const { post } = await gateway(context, { timeoutMs: 40, sessions: new Map([['1', socket]]) });
  assert.equal((await post({ to: administrator, text: 'first' })).status, 504);
  const second = await post({ to: administrator, text: 'second' });
  assert.equal(second.status, 503);
  assert.equal(second.body.error, 'whatsapp_send_in_progress');
  assert.equal(sends, 1);
  release({ key: { id: 'late-first' } });
  await new Promise(resolve => setImmediate(resolve));
});

test('success is not returned until actual asynchronous delivery resolves', async context => {
  let release;
  const socket = mockSocket({ sendMessage() { return new Promise(resolve => { release = resolve; }); } });
  const { post } = await gateway(context, { sessions: new Map([['1', socket]]) });
  let finished = false;
  const request = post({ to: administrator, text: 'reply' }).then(result => { finished = true; return result; });
  while (!release) await new Promise(resolve => setImmediate(resolve));
  assert.equal(finished, false);
  release({ key: { id: 'real-delivery' } });
  assert.deepEqual(await request, { status: 200, body: { ok: true, id: 'real-delivery' } });
});

test('private webhook token fallback applies only when gateway env key is absent', async context => {
  const previousGateway = process.env.NOC_GATEWAY_SECRET;
  const previousWebhook = process.env.NOC_WHATSAPP_WEBHOOK_TOKEN;
  context.after(() => {
    if (previousGateway === undefined) delete process.env.NOC_GATEWAY_SECRET;
    else process.env.NOC_GATEWAY_SECRET = previousGateway;
    if (previousWebhook === undefined) delete process.env.NOC_WHATSAPP_WEBHOOK_TOKEN;
    else process.env.NOC_WHATSAPP_WEBHOOK_TOKEN = previousWebhook;
  });
  const socket = mockSocket();
  process.env.NOC_WHATSAPP_WEBHOOK_TOKEN = token;
  delete process.env.NOC_GATEWAY_SECRET;
  const fallback = await gateway(context, { gatewaySecret: undefined, sessions: new Map([['1', socket]]) });
  // An explicit option, including undefined, must not silently use another token.
  assert.equal((await fallback.post({ to: administrator, text: 'reply' })).status, 503);
  const app = { handler: null, post(path, handler) { this.handler = handler; } };
  attachMessageSendAlias(app, { sessions: new Map([['1', socket]]) });
  const captured = {};
  const response = { status(code) { captured.status = code; return this; }, json(body) { captured.body = body; return this; } };
  await app.handler({ get: () => token, body: { to: administrator, text: 'reply' } }, response);
  assert.equal(captured.status, 200);
  process.env.NOC_GATEWAY_SECRET = '';
  const disabledApp = { handler: null, post(path, handler) { this.handler = handler; } };
  attachMessageSendAlias(disabledApp, { sessions: new Map([['1', socket]]) });
  await disabledApp.handler({ get: () => token, body: { to: administrator, text: 'reply' } }, response);
  assert.equal(captured.status, 503);
});

'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const https = require('node:https');
const { EventEmitter } = require('node:events');
const { Readable, Writable } = require('node:stream');
const { attachNocWebhook } = require('../integrations/baileys-noc.cjs');

const incoming = (id, message, extra = {}) => ({ key: { id, fromMe: false, remoteJid: '212770010264@s.whatsapp.net', ...extra }, message, messageTimestamp: Math.floor(Date.now() / 1000) });
const event = messages => ({ type: 'notify', sessionId: '1', messages });
const sleep = delay => new Promise(resolve => setTimeout(resolve, delay));
async function until(predicate) {
  for (let attempt = 0; attempt < 150; attempt++) { if (predicate()) return; await sleep(20); }
  assert.fail('Mock bridge did not finish within three seconds');
}
async function fixture(t, overrides = {}, responder) {
  const received = [];
  const logs = [];
  const server = http.createServer((request, response) => {
    const chunks = [];
    request.on('data', chunk => chunks.push(chunk));
    request.on('end', () => {
      received.push({ path: request.url, headers: request.headers, body: Buffer.concat(chunks) });
      if (responder) responder(response, received.length);
      else { response.writeHead(202, { 'Content-Type': 'application/json' }); response.end('{"queued":true}'); }
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const sock = { ev: new EventEmitter(), updateMediaMessage: async message => message };
  const detach = attachNocWebhook(sock, {
    adminPhone: '212770010264', sessionId: '1', webhookToken: 'private-mock-token',
    baseUrl: `http://127.0.0.1:${server.address().port}`, logger: { warn: message => logs.push(message) },
    downloadMediaMessage: async () => Readable.from([Buffer.from('OggS-test')]), ...overrides
  });
  t.after(async () => { detach(); server.closeAllConnections?.(); await new Promise(resolve => server.close(resolve)); });
  return { sock, detach, received, logs };
}

test('text and decrypted audio reach real HTTP mock without disturbing other gateway listeners', async t => {
  let downloads = 0;
  const f = await fixture(t, { downloadMediaMessage: async (_message, mode) => { downloads++; assert.equal(mode, 'stream'); return Readable.from([Buffer.from('OggS-'), Buffer.from('decrypted')]); } });
  let originalCalls = 0;
  f.sock.ev.on('messages.upsert', () => originalCalls++);
  f.sock.ev.emit('messages.upsert', event([incoming('TEXT_1', { conversation: 'statut' }), incoming('AUDIO_1', { audioMessage: { mimetype: 'audio/ogg; codecs=opus', seconds: 2 } })]));
  await until(() => f.received.length === 2);
  assert.equal(f.received[0].path, '/api/whatsapp/webhook');
  assert.equal(JSON.parse(f.received[0].body).data.text, 'statut');
  assert.equal(f.received[1].path, '/api/voice/process');
  assert.equal(f.received[1].headers['x-gateway-secret'], 'private-mock-token');
  assert.equal(f.received[1].headers['x-message-id'], 'AUDIO_1');
  assert.equal(f.received[1].headers['x-session-id'], '1');
  assert.equal(f.received[1].body.toString(), 'OggS-decrypted');
  assert.equal(downloads, 1);
  assert.equal(originalCalls, 1);
  f.detach();
  assert.equal(f.sock.ev.listenerCount('messages.upsert'), 1);
});

test('other sender, groups, fromMe, historical messages and wrong session do not download or POST', async t => {
  const f = await fixture(t, { downloadMediaMessage: async () => assert.fail('unauthorized download') });
  f.sock.ev.emit('messages.upsert', event([
    incoming('OTHER', { conversation: 'statut' }, { remoteJid: '212600000000@s.whatsapp.net' }),
    incoming('GROUP', { conversation: 'statut' }, { remoteJid: 'group@g.us', participant: '212770010264@s.whatsapp.net' }),
    incoming('FROMME', { conversation: 'statut' }, { fromMe: true })
  ]));
  f.sock.ev.emit('messages.upsert', { ...event([incoming('HISTORY', { conversation: 'statut' })]), type: 'append' });
  f.sock.ev.emit('messages.upsert', { ...event([incoming('SESSION', { conversation: 'statut' })]), sessionId: '2' });
  await sleep(100);
  assert.equal(f.received.length, 0);
});

test('self-chat commands from the active administrator are forwarded with the exact session JID', async t => {
  const f = await fixture(t, {adminPhone: '+212 770-010-264,212684056613'});
  f.sock.user = { id: '212770010264:38@s.whatsapp.net', lid: '7654321@lid' };
  f.sock.ev.emit('messages.upsert', event([
    incoming('SELF_PHONE', {conversation: 'statut'}, {remoteJid: '212770010264@s.whatsapp.net', fromMe: true}),
    incoming('SELF_LID', {conversation: 'redemarrage klasora'}, {remoteJid: '7654321@lid', remoteJidAlt: '212770010264@s.whatsapp.net', fromMe: true}),
    incoming('OTHER_ADMIN', {conversation: 'statut'}, {remoteJid: '212684056613@s.whatsapp.net'}),
    incoming('OUTGOING_TO_OTHER_ADMIN', {conversation: 'statut'}, {remoteJid: '212684056613@s.whatsapp.net', fromMe: true}),
    incoming('OUTGOING_TO_THIRD_PARTY', {conversation: 'statut'}, {remoteJid: '212600000000@s.whatsapp.net', fromMe: true}),
  ]));
  await until(() => f.received.length === 3);
  const forwarded = f.received.map(row => ({
    from: JSON.parse(row.body).data.from,
    header: row.headers['x-whatsapp-from'],
  }));
  assert.deepEqual(forwarded, [
    {from: f.sock.user.id, header: f.sock.user.id},
    {from: f.sock.user.id, header: f.sock.user.id},
    {from: '212684056613@s.whatsapp.net', header: '212684056613@s.whatsapp.net'},
  ]);
});

test('self-chat LID requires an active matching session identity and an administrator phone', async t => {
  const f = await fixture(t, {adminPhone: '212770010264,212684056613'});
  f.sock.user = {id: '87654321@lid', lid: '87654321@lid'};
  f.sock.ev.emit('messages.upsert', event([
    incoming('SELF_LID_ADMIN', {conversation: 'statut'}, {remoteJid: '87654321@lid', remoteJidAlt: '212684056613@s.whatsapp.net', fromMe: true}),
    incoming('SELF_LID_NONADMIN', {conversation: 'statut'}, {remoteJid: '87654321@lid', remoteJidAlt: '212600000000@s.whatsapp.net', fromMe: true}),
    incoming('OTHER_LID', {conversation: 'statut'}, {remoteJid: '87654322@lid', remoteJidAlt: '212684056613@s.whatsapp.net', fromMe: true}),
  ]));
  await until(() => f.received.length === 1);
  assert.equal(JSON.parse(f.received[0].body).data.from, '212684056613@s.whatsapp.net');
  assert.equal(f.received[0].headers['x-whatsapp-from'], '212684056613@s.whatsapp.net');
});

test('delivery retries preserve message ID and duplicate upserts are ignored', async t => {
  const f = await fixture(t, {}, (response, count) => { response.writeHead(count === 1 ? 503 : 202); response.end('{}'); });
  const message = incoming('RETRY_1', { conversation: 'statut' });
  f.sock.ev.emit('messages.upsert', event([message, message]));
  await until(() => f.received.length === 2);
  assert.deepEqual(f.received.map(row => row.headers['x-message-id']), ['RETRY_1', 'RETRY_1']);
  assert.equal(f.logs.length, 0);
});

test('stream size limit prevents an oversized upload, without logging media or secrets', async t => {
  const f = await fixture(t, { maxAudioBytes: 128, downloadMediaMessage: async () => Readable.from([Buffer.alloc(256)]) });
  f.sock.ev.emit('messages.upsert', event([incoming('TOO_BIG', { audioMessage: { mimetype: 'audio/ogg' } })]));
  await until(() => f.logs.length > 0);
  assert.equal(f.received.length, 0);
  assert.ok(f.logs.some(line => line.includes('audio_limit')));
  assert.ok(f.logs.every(line => !line.includes('private-mock-token')));
});

test('one active download and bounded queue preserve gateway responsiveness', async t => {
  let current = 0;
  let peak = 0;
  const f = await fixture(t, { maxQueued: 1, downloadMediaMessage: async () => { current++; peak = Math.max(peak, current); await sleep(80); current--; return Readable.from([Buffer.from('OggS')]); } });
  f.sock.ev.emit('messages.upsert', event(['Q1', 'Q2', 'Q3'].map(id => incoming(id, { audioMessage: { mimetype: 'audio/ogg' } }))));
  await until(() => f.received.length === 2);
  assert.equal(peak, 1);
  assert.ok(f.logs.some(line => line.includes('queue_full')));
  assert.deepEqual(f.received.map(row => row.headers['x-message-id']), ['Q1', 'Q2']);
});

test('LID alternate phone and device-specific administrator JIDs remain supported', async t => {
  const f = await fixture(t);
  f.sock.ev.emit('messages.upsert', event([incoming('LID', { conversation: 'statut' }, { remoteJid: '123456@lid', remoteJidAlt: '212770010264:2@s.whatsapp.net' })]));
  await until(() => f.received.length === 1);
  assert.equal(f.received[0].headers['x-whatsapp-from'], '212770010264@s.whatsapp.net');
});

test('multiple administrators keep their real identities and independent message IDs', async t => {
  const second = '22790000000';
  const f = await fixture(t, {adminNumbers: `+212 770-010-264,${second}`});
  f.sock.ev.emit('messages.upsert', event([
    incoming('SAME_ID', {conversation: 'statut'}),
    incoming('SAME_ID', {conversation: 'status'}, {remoteJid: `${second}@s.whatsapp.net`}),
    incoming('MIXED_ADMINS', {conversation: 'restart klasora'}, {remoteJidAlt: `${second}@s.whatsapp.net`}),
  ]));
  await until(() => f.received.length === 2);
  assert.deepEqual(f.received.map(row => JSON.parse(row.body).data.from), ['212770010264@s.whatsapp.net', `${second}@s.whatsapp.net`]);
  assert.equal(f.received[1].headers['x-whatsapp-from'], `${second}@s.whatsapp.net`);
});

test('LID resolved to the second administrator forwards that identity', async t => {
  const second = '22790000000';
  const f = await fixture(t, {adminNumbers: ['212770010264', second]});
  f.sock.signalRepository = {lidMapping: {getPNForLID: async () => `${second}@s.whatsapp.net`}};
  f.sock.ev.emit('messages.upsert', event([
    incoming('SECOND_LID', {conversation: 'statut'}, {remoteJid: '7654321@lid'}),
  ]));
  await until(() => f.received.length === 1);
  assert.equal(JSON.parse(f.received[0].body).data.from, `${second}@s.whatsapp.net`);
});

test('an explicit empty or malformed administrator list fails closed', () => {
  const sock = {ev: new EventEmitter()};
  const options = {adminPhone: '212770010264', webhookToken: 'private-mock-token', downloadMediaMessage: async () => Readable.from([])};
  for (const adminNumbers of ['', [], '212770010264,,22790000000', '212770010264@g.us']) {
    assert.throws(() => attachNocWebhook(sock, {...options, adminNumbers}), /configuration_identity/);
  }
  assert.equal(sock.ev.listenerCount('messages.upsert'), 0);
});

test('public plaintext targets and invalid credentials fail before attaching', () => {
  const sock = { ev: new EventEmitter() };
  const config = { adminPhone: '212770010264', webhookToken: 'private-mock-token', downloadMediaMessage: async () => Readable.from([]) };
  assert.throws(() => attachNocWebhook(sock, { ...config, baseUrl: 'http://8.8.8.8:5050' }), /configuration_transport/);
  assert.throws(() => attachNocWebhook(sock, { ...config, webhookToken: 'bad\r\nheader' }), /configuration_identity/);
  assert.equal(sock.ev.listenerCount('messages.upsert'), 0);
});

function mockHttps(t) {
  const received = [];
  t.mock.method(https, 'request', (url, options, onResponse) => {
    const chunks = [];
    return new Writable({
      write(chunk, encoding, callback) { chunks.push(Buffer.from(chunk)); callback(); },
      final(callback) {
        received.push({ url, options, body: Buffer.concat(chunks) });
        callback();
        const response = Readable.from([Buffer.from('{"accepted":true}')]);
        response.statusCode = 202;
        queueMicrotask(() => onResponse(response));
      }
    });
  });
  return received;
}

test('self-signed local HTTPS delivers text and audio without real certificates', async t => {
  const received = mockHttps(t);
  const f = await fixture(t, { baseUrl: 'https://127.0.0.1:5050' });
  f.sock.ev.emit('messages.upsert', event([
    incoming('HTTPS_TEXT', { conversation: 'statut' }),
    incoming('HTTPS_AUDIO', { audioMessage: { mimetype: 'audio/ogg' } }),
  ]));
  await until(() => received.length === 2);
  assert.deepEqual(received.map(row => row.url.pathname), ['/api/whatsapp/webhook', '/api/voice/process']);
  assert.ok(received.every(row => row.options.rejectUnauthorized === false));
  assert.equal(JSON.parse(received[0].body).data.text, 'statut');
  assert.equal(received[1].body.toString(), 'OggS-test');
  assert.equal(f.logs.length, 0);
});

test('TLS verification is relaxed only for literal local hosts, never public or lookalike names', async t => {
  const received = mockHttps(t);
  const cases = [
    ['localhost', false], ['[::1]', false], ['192.168.11.103', false],
    ['10.42.0.1', false], ['172.16.0.1', false], ['172.31.255.255', false],
    ['100.64.0.1', false], ['100.127.255.254', false],
    ['8.8.8.8', true], ['noc.example.com', true], ['localhost.example.com', true],
    ['192.168.11.103.example.com', true], ['172.32.0.1', true], ['100.128.0.1', true],
  ];
  for (const [index, [host, expected]] of cases.entries()) {
    const f = await fixture(t, { baseUrl: `https://${host}:5050` });
    f.sock.ev.emit('messages.upsert', event([incoming(`TLS_${received.length}`, { conversation: 'statut' })]));
    await until(() => received.length === index + 1);
    assert.equal(received.at(-1).options.rejectUnauthorized, expected, host);
    f.detach();
  }
});

test('NOC_API_BASE_URL selects HTTPS when an explicit baseUrl is absent', async t => {
  const received = mockHttps(t);
  const previous = process.env.NOC_API_BASE_URL;
  let f;
  try {
    process.env.NOC_API_BASE_URL = 'https://192.168.11.103:5050';
    f = await fixture(t, { baseUrl: undefined });
  } finally {
    if (previous === undefined) delete process.env.NOC_API_BASE_URL;
    else process.env.NOC_API_BASE_URL = previous;
  }
  f.sock.ev.emit('messages.upsert', event([incoming('HTTPS_ENV', { conversation: 'status' })]));
  await until(() => received.length === 1);
  assert.equal(received[0].url.origin, 'https://192.168.11.103:5050');
  assert.equal(received[0].options.rejectUnauthorized, false);
});

test('an explicit baseUrl takes precedence over NOC_API_BASE_URL', async t => {
  const previous = process.env.NOC_API_BASE_URL;
  let f;
  try {
    process.env.NOC_API_BASE_URL = 'https://192.168.11.103:5050';
    f = await fixture(t);
  } finally {
    if (previous === undefined) delete process.env.NOC_API_BASE_URL;
    else process.env.NOC_API_BASE_URL = previous;
  }
  f.sock.ev.emit('messages.upsert', event([incoming('HTTP_OVERRIDE', { conversation: 'status' })]));
  await until(() => f.received.length === 1);
});

test('formatted administrator option accepts exact PN text and decrypted audio over HTTP', async t => {
  let downloads = 0;
  const f = await fixture(t, {
    adminPhone: '+212 770-010-264',
    downloadMediaMessage: async () => { downloads++; return Readable.from([Buffer.from('OggS-formatted-admin')]); },
  });
  f.sock.ev.emit('messages.upsert', event([
    incoming('FORMATTED_TEXT', { conversation: 'statut' }, { remoteJid: '212770010264:2@s.whatsapp.net' }),
    incoming('FORMATTED_AUDIO', { audioMessage: { mimetype: 'audio/ogg' } }),
  ]));
  await until(() => f.received.length === 2);
  assert.deepEqual(f.received.map(row => row.path), ['/api/whatsapp/webhook', '/api/voice/process']);
  assert.ok(f.received.every(row => row.headers['x-whatsapp-from'] === '212770010264@s.whatsapp.net'));
  assert.equal(JSON.parse(f.received[0].body).data.from, '212770010264@s.whatsapp.net');
  assert.equal(JSON.parse(f.received[0].body).data.id, 'FORMATTED_TEXT');
  assert.equal(f.received[1].headers['x-message-id'], 'FORMATTED_AUDIO');
  assert.equal(f.received[1].body.toString(), 'OggS-formatted-admin');
  assert.equal(downloads, 1);
});

test('administrator env with plus sign works for text and audio and is restored immediately', async t => {
  const previous = process.env.ADMIN_PHONE_NUMBER;
  let f;
  try {
    process.env.ADMIN_PHONE_NUMBER = '+212770010264';
    f = await fixture(t, { adminPhone: undefined });
  } finally {
    if (previous === undefined) delete process.env.ADMIN_PHONE_NUMBER;
    else process.env.ADMIN_PHONE_NUMBER = previous;
  }
  f.sock.ev.emit('messages.upsert', event([
    incoming('ENV_TEXT', { conversation: 'statut' }),
    incoming('ENV_AUDIO', { audioMessage: { mimetype: 'audio/ogg; codecs=opus' } }),
  ]));
  await until(() => f.received.length === 2);
  assert.deepEqual(f.received.map(row => row.path), ['/api/whatsapp/webhook', '/api/voice/process']);
  assert.ok(f.received.every(row => row.headers['x-whatsapp-from'] === '212770010264@s.whatsapp.net'));
  assert.equal(f.received[1].body.toString(), 'OggS-test');
});

test('formatting administrator config never authorizes a different PN or LID', async t => {
  const f = await fixture(t, {
    adminPhone: '+212 770-010-264',
    downloadMediaMessage: async () => assert.fail('another number must not download'),
  });
  f.sock.ev.emit('messages.upsert', event([
    incoming('FORMATTED_OTHER_TEXT', { conversation: 'statut' }, { remoteJid: '212770010265:2@s.whatsapp.net' }),
    incoming('FORMATTED_OTHER_AUDIO', { audioMessage: { mimetype: 'audio/ogg' } }, { remoteJid: '212770010265@s.whatsapp.net' }),
    incoming('FORMATTED_GROUP', { conversation: 'statut' }, { remoteJid: '212770010264@g.us' }),
    incoming('FORMATTED_FROMME', { conversation: 'statut' }, { fromMe: true }),
    incoming('FORMATTED_FAKE_LID', { conversation: 'statut' }, { remoteJid: '212770010264@lid', remoteJidAlt: '212770010265@s.whatsapp.net' }),
  ]));
  await sleep(100);
  assert.equal(f.received.length, 0);
});

test('an unresolved Baileys download cannot accumulate orphan downloads', async t => {
  let calls = 0;
  let release;
  const f = await fixture(t, {
    timeoutMs: 1000,
    downloadMediaMessage: async () => {
      calls++;
      if (calls === 1) return new Promise(resolve => { release = resolve; });
      return Readable.from([Buffer.from('OggS')]);
    }
  });
  f.sock.ev.emit('messages.upsert', event(['HANG', 'AFTER'].map(id => incoming(id, { audioMessage: { mimetype: 'audio/ogg' } }))));
  await until(() => f.logs.some(line => line.includes('download_timeout')));
  assert.equal(calls, 1);
  release(Readable.from([Buffer.from('discard-late-stream')]));
  await until(() => f.received.length === 1);
  assert.equal(calls, 2);
  assert.equal(f.received[0].headers['x-message-id'], 'AFTER');
});

test('trusted Baileys LID mapping authorizes exact administrator before text or media delivery', async t => {
  let mapped = false;
  let downloads = 0;
  const lookedUp = [];
  const f = await fixture(t, { downloadMediaMessage: async () => {
    assert.equal(mapped, true, 'identity mapping must precede media decryption');
    downloads++;
    return Readable.from([Buffer.from('OggS-authorized')]);
  } });
  const lidMapping = { getPNForLID: async function (lid) {
    assert.equal(this, lidMapping, 'Baileys mapping method must retain its repository binding');
    lookedUp.push(lid);
    mapped = true;
    return '212770010264:2@s.whatsapp.net';
  } };
  f.sock.signalRepository = { lidMapping };
  f.sock.ev.emit('messages.upsert', event([
    incoming('MAPPED_TEXT', { conversation: 'statut' }, { remoteJid: '987654:2@lid' }),
    incoming('MAPPED_AUDIO', { audioMessage: { mimetype: 'audio/ogg' } }, { remoteJid: '987654:2@lid' })
  ]));
  await until(() => f.received.length === 2);
  assert.deepEqual(lookedUp, ['987654:2@lid', '987654:2@lid']);
  assert.equal(downloads, 1);
  assert.ok(f.received.every(row => row.headers['x-whatsapp-from'] === '212770010264@s.whatsapp.net'));
});

test('nonadministrator LID mapping never downloads media or POSTs', async t => {
  const f = await fixture(t, { downloadMediaMessage: async () => assert.fail('unauthorized LID media download') });
  f.sock.signalRepository = { lidMapping: { getPNForLID: async () => '212600000000@s.whatsapp.net' } };
  f.sock.ev.emit('messages.upsert', event([incoming('NONADMIN_LID', { audioMessage: { mimetype: 'audio/ogg' } }, { remoteJid: '987654@lid' })]));
  await until(() => f.logs.some(line => line.includes('lid_mapping_not_admin')));
  assert.equal(f.received.length, 0);
  assert.ok(f.logs.every(line => !line.includes('212600000000') && !line.includes('987654')));
});

test('unknown LID mappings and nonstring mapping results fail closed', async t => {
  const f = await fixture(t, { downloadMediaMessage: async () => assert.fail('unknown LID media download') });
  const results = [null, undefined, { pn: '212770010264@s.whatsapp.net' }, '212770010264@lid'];
  f.sock.signalRepository = { lidMapping: { getPNForLID: async () => results.shift() } };
  f.sock.ev.emit('messages.upsert', event(['NULL_LID', 'UNDEFINED_LID', 'OBJECT_LID', 'FAKE_PHONE_LID'].map(id => incoming(id, { conversation: 'statut' }, { remoteJid: '212770010264@lid' }))));
  await until(() => f.logs.length === 4);
  assert.equal(f.received.length, 0);
  assert.ok(f.logs.some(line => line.includes('lid_mapping_unknown')));
  assert.ok(f.logs.some(line => line.includes('lid_mapping_not_admin')));
});

test('unavailable LID mapping API drops the message with a safe warning', async t => {
  const f = await fixture(t, { downloadMediaMessage: async () => assert.fail('LID without API must not download') });
  f.sock.ev.emit('messages.upsert', event([incoming('NO_MAPPING_API', { audioMessage: { mimetype: 'audio/ogg' } }, { remoteJid: '212770010264@lid' })]));
  await until(() => f.logs.some(line => line.includes('lid_mapping_unavailable')));
  assert.equal(f.received.length, 0);
  assert.deepEqual(f.logs, ['NOC bridge: lid_mapping_unavailable']);
});

test('mapping errors stay isolated and do not expose exception contents or stop later messages', async t => {
  const f = await fixture(t);
  f.sock.signalRepository = { lidMapping: { getPNForLID: async () => { throw new Error('private-mock-token sensitive identity'); } } };
  f.sock.ev.emit('messages.upsert', event([
    incoming('MAPPING_ERROR', { conversation: 'statut' }, { remoteJid: '987654@lid' }),
    incoming('AFTER_MAPPING_ERROR', { conversation: 'statut' })
  ]));
  await until(() => f.received.length === 1);
  assert.equal(f.received[0].headers['x-message-id'], 'AFTER_MAPPING_ERROR');
  assert.deepEqual(f.logs, ['NOC bridge: lid_mapping_failed']);
});

test('supplied conflicting alternate JIDs are rejected instead of using the mapping fallback', async t => {
  const f = await fixture(t, { downloadMediaMessage: async () => assert.fail('conflicting alternate identity') });
  f.sock.signalRepository = { lidMapping: { getPNForLID: async () => assert.fail('must not override supplied alternate identity') } };
  f.sock.ev.emit('messages.upsert', event([
    incoming('CONFLICT_LID', { conversation: 'statut' }, { remoteJid: '987654@lid', remoteJidAlt: '212600000000@s.whatsapp.net' }),
    incoming('EMPTY_ALT_LID', { conversation: 'statut' }, { remoteJid: '987654@lid', remoteJidAlt: '' }),
    incoming('CONFLICT_PHONE', { conversation: 'statut' }, { remoteJidAlt: '212600000000@s.whatsapp.net' }),
    incoming('OTHER_WITH_ADMIN_ALT', { conversation: 'statut' }, { remoteJid: '212600000000@s.whatsapp.net', remoteJidAlt: '212770010264@s.whatsapp.net' })
  ]));
  await sleep(100);
  assert.equal(f.received.length, 0);
  assert.ok(f.logs.every(line => line === 'NOC bridge: unauthorized_sender'));
});

test('one unresolved LID mapping pauses the bounded queue without disturbing gateway listeners', async t => {
  let calls = 0;
  let release;
  let originalCalls = 0;
  const f = await fixture(t, { timeoutMs: 1000, maxQueued: 1, downloadMediaMessage: async () => assert.fail('text lookup must not download') });
  f.sock.signalRepository = { lidMapping: { getPNForLID: async () => {
    calls++;
    if (calls === 1) return new Promise(resolve => { release = resolve; });
    return '212770010264@s.whatsapp.net';
  } } };
  f.sock.ev.on('messages.upsert', () => originalCalls++);
  f.sock.ev.emit('messages.upsert', event(['STALLED_LID', 'AFTER_LID', 'EXCESS_LID'].map(id => incoming(id, { conversation: 'statut' }, { remoteJid: '987654@lid' }))));
  await until(() => f.logs.some(line => line.includes('lid_mapping_timeout')));
  await sleep(100);
  assert.equal(calls, 1, 'no second orphan lookup may start after timeout');
  assert.equal(originalCalls, 1);
  assert.equal(f.received.length, 0);
  assert.ok(f.logs.some(line => line.includes('queue_full')));
  release('212770010264@s.whatsapp.net');
  await until(() => f.received.length === 1);
  assert.equal(calls, 2);
  assert.equal(f.received[0].headers['x-message-id'], 'AFTER_LID', 'late result must not deliver the expired message');
  f.detach();
  assert.equal(f.sock.ev.listenerCount('messages.upsert'), 1);
});

test('text limit matches the backend 1000-character bound before identity work', async t => {
  const f = await fixture(t);
  f.sock.signalRepository = { lidMapping: { getPNForLID: async () => assert.fail('oversized text must not trigger identity lookup') } };
  f.sock.ev.emit('messages.upsert', event([
    incoming('TEXT_TOO_LONG', { conversation: 's'.repeat(1001) }, { remoteJid: '987654@lid' }),
    incoming('TEXT_BOUNDARY', { conversation: 's'.repeat(1000) })
  ]));
  await until(() => f.received.length === 1);
  assert.equal(f.received[0].headers['x-message-id'], 'TEXT_BOUNDARY');
  assert.equal(JSON.parse(f.received[0].body).data.text.length, 1000);
});

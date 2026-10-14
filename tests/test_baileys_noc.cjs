'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { EventEmitter } = require('node:events');
const { Readable } = require('node:stream');
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

test('public plaintext targets and invalid credentials fail before attaching', () => {
  const sock = { ev: new EventEmitter() };
  const config = { adminPhone: '212770010264', webhookToken: 'private-mock-token', downloadMediaMessage: async () => Readable.from([]) };
  assert.throws(() => attachNocWebhook(sock, { ...config, baseUrl: 'http://8.8.8.8:5050' }), /configuration_transport/);
  assert.throws(() => attachNocWebhook(sock, { ...config, webhookToken: 'bad\r\nheader' }), /configuration_identity/);
  assert.equal(sock.ev.listenerCount('messages.upsert'), 0);
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
  assert.equal(f.logs.length, 0);
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

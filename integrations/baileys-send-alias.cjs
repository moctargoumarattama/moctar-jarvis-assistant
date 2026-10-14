'use strict';

// No dependency: reuse the existing gateway's Express app and Baileys sessions.
const crypto = require('node:crypto');
const attached = new WeakMap();
const positiveStates = new Set(['connected', 'connecte', 'open', 'ready', 'active', 'online', 'authenticated', 'loggedin', 'logged_in']);

class SendAliasError extends Error {
  constructor(status, code) { super(code); this.status = status; this.code = code; }
}

function personalJid(value) {
  if (typeof value !== 'string' || value.length > 80) return null;
  const target = value.trim();
  if (target.includes('@')) {
    const match = target.match(/^(\d{8,15})(?::\d{1,10})?@(s\.whatsapp\.net|c\.us)$/);
    return match ? `${match[1]}@s.whatsapp.net` : null;
  }
  if (!/^\+?[\d ()-]+$/.test(target)) return null;
  const phone = target.replace(/[^0-9]/g, '');
  return /^\d{8,15}$/.test(phone) ? `${phone}@s.whatsapp.net` : null;
}

function connectedSocket(record) {
  if (!record || typeof record !== 'object') return null;
  let socket;
  try { socket = record.sock || record.socket || record.client || record; }
  catch (_) { return null; }
  if (!socket || typeof socket.sendMessage !== 'function') return null;
  let confirmed = false;
  let unavailable = false;
  const state = value => {
    if (value === undefined || value === null || value === '') return;
    if (typeof value === 'string') {
      const normalized = value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').trim().toLowerCase();
      if (positiveStates.has(normalized)) confirmed = true;
      else unavailable = true;
    } else if (typeof value === 'boolean') {
      if (value) confirmed = true;
      else unavailable = true;
    } else unavailable = true;
  };
  try {
    for (const subject of record === socket ? [socket] : [record, socket]) {
      state(subject.connected);
      state(subject.status);
      state(subject.connection);
      if (typeof subject.state === 'string') state(subject.state);
      else if (subject.state && typeof subject.state === 'object') state(subject.state.connection);
    }
    const websocket = socket.ws;
    if (websocket) {
      if (typeof websocket.isOpen === 'boolean') state(websocket.isOpen);
      if (typeof websocket.readyState === 'number') state(websocket.readyState === 1);
    }
    // Baileys populates user only after authentication. Explicit closed/offline
    // state still wins: a cached user object alone must not override disconnect.
    if (personalJid(socket.user?.id || record.user?.id)) confirmed = true;
  } catch (_) { return null; }
  return confirmed && !unavailable ? socket : null;
}

function secretFrom(options) {
  if (Object.prototype.hasOwnProperty.call(options, 'gatewaySecret')) return options.gatewaySecret;
  if (Object.prototype.hasOwnProperty.call(process.env, 'NOC_GATEWAY_SECRET')) return process.env.NOC_GATEWAY_SECRET;
  return process.env.NOC_WHATSAPP_WEBHOOK_TOKEN;
}

function authenticated(request, expectedHash) {
  let provided;
  try { provided = typeof request.get === 'function' ? request.get('X-Gateway-Secret') : request.headers?.['x-gateway-secret']; }
  catch (_) { return false; }
  if (typeof provided !== 'string' || !/^[\x21-\x7e]{1,512}$/.test(provided)) return false;
  const actual = crypto.createHash('sha256').update(provided).digest();
  return crypto.timingSafeEqual(actual, expectedHash);
}

async function selectSocket(options, sessionId) {
  // Resolve lazily: registration may precede `const sessions = new Map()`.
  const sessions = typeof options.sessions === 'function' ? await options.sessions() : options.sessions;
  let configured = typeof options.getSession === 'function' ? await options.getSession(sessionId) : undefined;
  if (!configured && sessions && typeof sessions.get === 'function') configured = sessions.get(sessionId);
  let socket = connectedSocket(configured);
  if (socket) return socket;
  if (sessions && typeof sessions.values === 'function') {
    let examined = 0;
    for (const candidate of sessions.values()) {
      if (++examined > 128) break;
      socket = connectedSocket(candidate);
      if (socket) return socket;
    }
  }
  throw new SendAliasError(503, 'whatsapp_session_unavailable');
}

async function bounded(promise, milliseconds) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new SendAliasError(504, 'whatsapp_send_timeout')), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

/** Register once, before the gateway's final 404 handler. Returns the handler.
 * sessions: Map or () => Map; getSession(id): optional sync/async getter.
 * No gateway routes, sessions, connection listeners or machine tokens change.
 */
function attachMessageSendAlias(app, options = {}) {
  if (!app || typeof app.post !== 'function') throw new TypeError('Express app.post requis');
  if (attached.has(app)) return attached.get(app);
  const secret = secretFrom(options);
  const validSecret = typeof secret === 'string' && /^[\x21-\x7e]{1,512}$/.test(secret);
  const expectedHash = validSecret ? crypto.createHash('sha256').update(secret).digest() : null;
  const sessionId = String(options.sessionId ?? process.env.NOC_WHATSAPP_SESSION_ID ?? '1');
  const configuredTimeout = Number(options.timeoutMs);
  const timeoutMs = Number.isFinite(configuredTimeout) && configuredTimeout > 0
    ? Math.min(8000, configuredTimeout) : 8000;
  const sending = new WeakSet();
  const warn = code => {
    try { options.logger?.warn?.(`NOC send alias: ${code}`); } catch (_) { /* logging cannot alter delivery */ }
  };
  const handler = async (request, response) => {
    if (!expectedHash) return response.status(503).json({ ok: false, error: 'gateway_secret_unconfigured' });
    if (!authenticated(request, expectedHash)) return response.status(403).json({ ok: false, error: 'gateway_secret_invalid' });
    const body = request.body;
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return response.status(400).json({ ok: false, error: 'message_body_invalid' });
    }
    const jid = personalJid(body.to);
    const rawText = body.text || body.message;
    const text = typeof rawText === 'string' ? rawText.trim() : '';
    if (!jid || !text || text.length > 1000) {
      return response.status(400).json({ ok: false, error: 'message_recipient_or_text_invalid' });
    }
    const expires = Date.now() + timeoutMs;
    try {
      const operation = Promise.resolve().then(async () => {
        const socket = await selectSocket(options, sessionId);
        if (Date.now() >= expires) throw new SendAliasError(504, 'whatsapp_send_timeout');
        if (sending.has(socket)) throw new SendAliasError(503, 'whatsapp_send_in_progress');
        sending.add(socket);
        const sent = Promise.resolve().then(() => socket.sendMessage(jid, { text }));
        // Baileys has no reliable cancellation API. Keep the single socket busy
        // after a timeout until that actual send settles; never retry it here.
        sent.then(() => sending.delete(socket), () => sending.delete(socket));
        return await sent;
      });
      const result = await bounded(operation, timeoutMs);
      const identifier = result?.key?.id ?? result?.id;
      const reply = { ok: true };
      if (typeof identifier === 'string' && /^[A-Za-z0-9_.:@+-]{1,128}$/.test(identifier)) reply.id = identifier;
      return response.status(200).json(reply);
    } catch (error) {
      const known = error instanceof SendAliasError;
      const code = known ? error.code : 'whatsapp_send_failed';
      warn(code);
      return response.status(known ? error.status : 502).json({ ok: false, error: code });
    }
  };
  app.post('/message/send', handler);
  attached.set(app, handler);
  return handler;
}

module.exports = { attachMessageSendAlias };

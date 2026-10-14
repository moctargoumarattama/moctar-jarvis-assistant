'use strict';

// Node standard library only. Reuse downloadMediaMessage from the gateway's Baileys.
const http = require('node:http');
const https = require('node:https');
const { Transform } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const attachments = new WeakMap();
const MAX_AUDIO_BYTES = 8 * 1024 * 1024;
const quiet = Object.fromEntries(['trace', 'debug', 'info', 'warn', 'error', 'fatal'].map(key => [key, () => {}]));
quiet.child = () => quiet;

class BridgeError extends Error {
  constructor(code, retryable = false) { super(code); this.code = code; this.retryable = retryable; }
}

function phoneOf(jid) {
  if (typeof jid !== 'string') return null;
  const match = jid.match(/^(\d{8,15})(?::\d+)?@(s\.whatsapp\.net|c\.us)$/);
  return match ? match[1] : null;
}

function isLid(jid) {
  return typeof jid === 'string' && /^\d{1,20}(?::\d{1,5})?@lid$/.test(jid);
}

function unwrap(content) {
  for (let depth = 0; content && depth < 4; depth++) {
    const nested = content.ephemeralMessage || content.viewOnceMessage || content.viewOnceMessageV2 || content.viewOnceMessageV2Extension;
    if (!nested) return content;
    content = nested.message;
  }
  return null;
}

function destination(baseUrl) {
  const base = new URL(baseUrl);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash || !['', '/'].includes(base.pathname)) throw new BridgeError('configuration_url');
  if (base.protocol === 'http:') {
    const host = base.hostname.replace(/^\[|\]$/g, '');
    const octets = host.split('.').map(Number);
    const tailscale = octets.length === 4 && octets.every(value => Number.isInteger(value) && value >= 0 && value <= 255) && octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127;
    if (!['127.0.0.1', 'localhost', '::1'].includes(host) && !tailscale) throw new BridgeError('configuration_transport');
  }
  return base;
}

function post(url, headers, body, signal, timeoutMs, maximum) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let incoming;
    const transport = url.protocol === 'https:' ? https : http;
    const request = transport.request(url, { method: 'POST', headers, signal, agent: false }, response => {
      incoming = response;
      let bytes = 0;
      response.on('data', chunk => {
        bytes += chunk.length;
        if (bytes > 65536) request.destroy(new BridgeError('response_limit'));
      });
      response.on('error', fail);
      response.on('end', () => {
        const status = response.statusCode || 0;
        if (status >= 200 && status < 300) finish();
        else fail(new BridgeError(`http_${status}`, status === 429 || status >= 500));
      });
    });
    const timer = setTimeout(() => request.destroy(new BridgeError('post_timeout', true)), timeoutMs);
    function finish(error) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) {
        if (body && typeof body.destroy === 'function') body.destroy();
        if (incoming) incoming.destroy();
        request.destroy();
        reject(error);
      } else resolve();
    }
    function fail(error) {
      finish(error instanceof BridgeError ? error : new BridgeError('post_network', !signal.aborted));
    }
    request.on('error', fail);
    if (Buffer.isBuffer(body)) {
      request.end(body);
    } else {
      let bytes = 0;
      const limit = new Transform({
        transform(chunk, encoding, callback) {
          bytes += chunk.length;
          callback(bytes > maximum ? new BridgeError('audio_limit') : null, bytes > maximum ? undefined : chunk);
        }
      });
      pipeline(body, limit, request, { signal }).catch(fail);
    }
  });
}

/** Attach one isolated messages.upsert listener and return an idempotent detach(). */
function attachNocWebhook(sock, options = {}) {
  if (!sock || !sock.ev || typeof sock.ev.on !== 'function' || typeof options.downloadMediaMessage !== 'function') throw new BridgeError('configuration_socket');
  const adminPhone = String(options.adminPhone || process.env.ADMIN_PHONE_NUMBER || '').replace(/\D/g, '');
  const sessionId = String(options.sessionId || process.env.NOC_WHATSAPP_SESSION_ID || '1');
  const token = options.webhookToken || process.env.NOC_WHATSAPP_WEBHOOK_TOKEN || '';
  if (!/^\d{8,15}$/.test(adminPhone) || !/^[A-Za-z0-9_.:-]{1,64}$/.test(sessionId) || !/^[\x21-\x7e]{1,512}$/.test(token)) throw new BridgeError('configuration_identity');
  const base = destination(options.baseUrl || 'http://127.0.0.1:5050');
  const maximum = Math.min(MAX_AUDIO_BYTES, Math.max(1, Number(options.maxAudioBytes) || MAX_AUDIO_BYTES));
  const timeoutMs = Math.min(30000, Math.max(1000, Number(options.timeoutMs) || 15000));
  const maxQueued = Math.min(16, Math.max(1, Number(options.maxQueued) || 8));
  const queue = [];
  const seen = new Map();
  let active = false;
  let detached = false;
  let stalled = false;
  let currentController = null;
  const notify = code => {
    try { options.logger?.warn?.(`NOC bridge: ${code}`); } catch (_) { /* gateway logger must not affect reception */ }
  };

  async function resolveAdministrator(item) {
    if (!item.lid) return;
    let mapping;
    try { mapping = sock.signalRepository?.lidMapping; }
    catch (_) { throw new BridgeError('lid_mapping_failed'); }
    if (!mapping || typeof mapping.getPNForLID !== 'function') throw new BridgeError('lid_mapping_unavailable');
    let expired = false;
    let timer;
    const promise = Promise.resolve().then(() => mapping.getPNForLID(item.lid));
    // Mapping has no cancellation API. Pause the queue if the single lookup
    // times out; do not create more unresolved jobs or trust its late result.
    const release = () => {
      if (!expired) return;
      stalled = false;
      if (!detached) setImmediate(() => { void pump(); });
    };
    promise.then(release, release);
    let jid;
    try {
      jid = await Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => {
          expired = true;
          stalled = true;
          currentController?.abort();
          reject(new BridgeError('lid_mapping_timeout'));
        }, timeoutMs);
      })]);
    } catch (error) {
      throw error instanceof BridgeError ? error : new BridgeError('lid_mapping_failed');
    } finally { clearTimeout(timer); }
    if (jid === null || jid === undefined) throw new BridgeError('lid_mapping_unknown');
    if (phoneOf(jid) !== adminPhone) throw new BridgeError('lid_mapping_not_admin');
    if (detached || currentController?.signal.aborted) throw new BridgeError('lid_mapping_cancelled');
  }

  async function download(item, signal) {
    let expired = false;
    let timer;
    const promise = Promise.resolve().then(() => options.downloadMediaMessage(item.message, 'stream', { options: { signal, timeout: timeoutMs } }, {
      logger: options.baileysLogger || quiet,
      reuploadRequest: typeof sock.updateMediaMessage === 'function' ? sock.updateMediaMessage.bind(sock) : undefined
    }));
    // Some Baileys versions don't cancel an unresolved download. Stop this bridge's
    // queue until that single promise settles instead of accumulating orphan jobs.
    promise.then(stream => {
      if (expired || detached) {
        stream?.destroy?.();
        stalled = false;
        if (!detached) void pump();
      }
    }, () => {
      if (expired) { stalled = false; if (!detached) void pump(); }
    });
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => {
          expired = true;
          stalled = true;
          currentController?.abort();
          reject(new BridgeError('download_timeout'));
        }, timeoutMs);
      })]);
    } finally { clearTimeout(timer); }
  }

  async function send(item, signal) {
    const headers = { 'X-Gateway-Secret': token, 'X-WhatsApp-From': `${adminPhone}@s.whatsapp.net`, 'X-Message-Id': item.id, 'X-Session-Id': sessionId };
    if (item.audio) {
      const stream = await download(item, signal);
      if (!stream || typeof stream.pipe !== 'function') throw new BridgeError('download_stream');
      headers['Content-Type'] = item.mimetype;
      await post(new URL('/api/voice/process', base), headers, stream, signal, timeoutMs, maximum);
    } else {
      const body = Buffer.from(JSON.stringify({ event: 'messages.upsert', sessionId, data: {
        from: `${adminPhone}@s.whatsapp.net`, fromMe: false, id: item.id,
        messageType: 'conversation', text: item.text, timestamp: item.timestamp
      } }));
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = String(body.length);
      await post(new URL('/api/whatsapp/webhook', base), headers, body, signal, timeoutMs, maximum);
    }
  }

  async function pump() {
    if (active || detached || stalled) return;
    active = true;
    try {
      while (queue.length && !detached && !stalled) {
        const item = queue.shift();
        currentController = new AbortController();
        try { await resolveAdministrator(item); }
        catch (error) {
          notify(error instanceof BridgeError ? error.code : 'lid_mapping_failed');
          currentController = null;
          continue;
        }
        for (let attempt = 0; attempt < 2 && !detached; attempt++) {
          try { await send(item, currentController.signal); break; }
          catch (error) {
            if (attempt === 0 && error instanceof BridgeError && error.retryable && !currentController.signal.aborted) {
              await new Promise(resolve => setTimeout(resolve, 500));
              continue;
            }
            notify(error instanceof BridgeError ? error.code : 'media_failed');
            break;
          }
        }
        currentController = null;
      }
    } finally { active = false; }
  }

  function listener(event) {
    if (detached || !event || event.type !== 'notify' || !Array.isArray(event.messages)) return;
    const suppliedSession = event.sessionId ?? event.session_id;
    if (suppliedSession !== undefined && String(suppliedSession) !== sessionId) return;
    for (const message of event.messages.slice(0, 64)) {
      const key = message?.key;
      if (!key || key.fromMe === true || (key.fromMe !== undefined && typeof key.fromMe !== 'boolean') || typeof key.remoteJid !== 'string' || key.remoteJid.endsWith('@g.us') || key.participant) continue;
      let lid = null;
      if (isLid(key.remoteJid)) {
        if (key.remoteJidAlt === undefined || key.remoteJidAlt === null) lid = key.remoteJid;
        else if (phoneOf(key.remoteJidAlt) !== adminPhone) continue;
      } else {
        if (phoneOf(key.remoteJid) !== adminPhone) continue;
        if (key.remoteJidAlt !== undefined && key.remoteJidAlt !== null && !isLid(key.remoteJidAlt) && phoneOf(key.remoteJidAlt) !== adminPhone) continue;
      }
      if (typeof key.id !== 'string' || !/^[A-Za-z0-9_.:-]{1,128}$/.test(key.id)) continue;
      const content = unwrap(message.message);
      const audio = content?.audioMessage;
      const text = content?.conversation || content?.extendedTextMessage?.text;
      if (!audio && (typeof text !== 'string' || !text.trim() || text.length > 1000)) continue;
      const mimetype = String(audio?.mimetype || 'audio/ogg').split(';', 1)[0].trim().toLowerCase();
      if (audio && (!['audio/ogg', 'audio/opus', 'audio/mpeg', 'audio/wav', 'application/octet-stream'].includes(mimetype) || Number(audio.fileLength) > maximum || Number(audio.seconds) > 60)) continue;
      const dedup = `${sessionId}:${key.id}`;
      if (seen.has(dedup)) continue;
      if (queue.length >= maxQueued) { notify('queue_full'); continue; }
      seen.set(dedup, true);
      while (seen.size > 256) seen.delete(seen.keys().next().value);
      queue.push({ message, lid, id: key.id, audio: Boolean(audio), mimetype, text, timestamp: Number(message.messageTimestamp) || Math.floor(Date.now() / 1000) });
      void pump();
    }
  }

  attachments.get(sock)?.();
  sock.ev.on('messages.upsert', listener);
  function detach() {
    if (detached) return;
    detached = true;
    queue.length = 0;
    currentController?.abort();
    if (typeof sock.ev.off === 'function') sock.ev.off('messages.upsert', listener);
    else sock.ev.removeListener?.('messages.upsert', listener);
    if (attachments.get(sock) === detach) attachments.delete(sock);
  }
  attachments.set(sock, detach);
  return detach;
}

module.exports = { attachNocWebhook };

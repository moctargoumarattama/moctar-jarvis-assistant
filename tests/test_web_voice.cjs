'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { create } = require('../static/voice.js');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function clock() {
  let now = 0;
  let nextId = 1;
  const pending = new Map();
  const timers = {
    setTimeout(callback, delay = 0) {
      const id = nextId++;
      pending.set(id, { at: now + delay, callback });
      return id;
    },
    clearTimeout(id) { pending.delete(id); }
  };
  return {
    timers,
    advance(milliseconds) {
      const target = now + milliseconds;
      let iterations = 0;
      while (true) {
        const next = [...pending.entries()].filter(([, item]) => item.at <= target)
          .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!next) break;
        assert.ok(++iterations < 100, 'recognition must not retry in a tight loop');
        now = next[1].at;
        pending.delete(next[0]);
        next[1].callback();
      }
      now = target;
    },
    get pending() { return pending.size; }
  };
}

function result(text, isFinal = true, resultIndex = 0) {
  const item = [{ transcript: text, confidence: 0.95 }];
  item.isFinal = isFinal;
  const results = Array.from({ length: resultIndex }, () => {
    const previous = [{ transcript: 'déjà entendu', confidence: 0.95 }];
    previous.isFinal = true;
    return previous;
  });
  results.push(item);
  return { resultIndex, results };
}

function fixture(t, overrides = {}) {
  const instances = [];
  const states = [];
  const prompts = [];
  const speech = [];
  const time = clock();
  class Recognition {
    constructor() { this.active = false; this.starts = 0; instances.push(this); }
    start() {
      assert.equal(this.active, false, 'only one recognition start may be active');
      this.active = true;
      this.starts++;
      queueMicrotask(() => { if (this.active) this.onstart?.({}); });
    }
    abort() {
      if (!this.active) return;
      this.active = false;
      queueMicrotask(() => this.onend?.({}));
    }
    stop() { this.abort(); }
    emit(text, isFinal = true, resultIndex = 0) { this.onresult?.(result(text, isFinal, resultIndex)); }
    end() {
      this.active = false;
      this.onend?.({});
    }
    fail(error) {
      this.onerror?.({ error });
      this.end();
    }
  }
  const engine = create({
    Recognition,
    timers: time.timers,
    storage: { getItem: () => null, setItem() {}, removeItem() {} },
    onState: (state, detail) => states.push({ state, detail }),
    speak: async text => { speech.push(text); return true; },
    onPrompt: async text => { prompts.push(text); },
    ...overrides
  });
  t.after(() => engine.destroy());
  return {
    engine, instances, states, prompts, speech, time,
    get rec() { return instances.at(-1); },
    async settle() {
      for (let i = 0; i < 20; i++) { await Promise.resolve(); time.advance(0); }
    },
    get active() { return instances.filter(item => item.active); }
  };
}

test('enabling starts microphone recognition and disabling stops it', async t => {
  const f = fixture(t);
  assert.equal(f.engine.enabled, false);
  await f.engine.enable();
  await f.settle();
  assert.equal(f.engine.enabled, true);
  assert.equal(f.engine.state, 'wake');
  assert.equal(f.active.length, 1);
  f.engine.disable();
  await f.settle();
  f.time.advance(30000);
  await f.settle();
  assert.equal(f.engine.state, 'off');
  assert.equal(f.engine.enabled, false);
  assert.equal(f.active.length, 0);
});

test('interim and final wake results invite a command without sending the wake phrase', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  f.rec.emit('yo moctar', false);
  await f.settle();
  assert.deepEqual(f.speech, []);
  f.rec.emit('yo moctar');
  await f.settle();
  assert.equal(f.speech.length, 1);
  assert.match(f.speech[0], /je t.écoute/i);
  assert.deepEqual(f.prompts, []);
  assert.equal(f.engine.state, 'listening');
  f.rec.emit('donne le statut du système');
  await f.settle();
  assert.deepEqual(f.prompts, ['donne le statut du système']);
});

test('ELMo, el mo, hey elmo and French Moctar wake aliases work', async t => {
  for (const alias of ['ELMo', 'el mo', 'hey elmo', 'hey moctar', 'yo moctar']) {
    const f = fixture(t);
    await f.engine.enable();
    await f.settle();
    f.rec.emit(alias);
    await f.settle();
    assert.equal(f.engine.state, 'listening', alias);
    assert.equal(f.speech.length, 1, alias);
    assert.deepEqual(f.prompts, [], alias);
    f.engine.destroy();
  }
});

test('wake plus command in a single final result sends only the command', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  f.rec.emit('ELMo donne le statut');
  await f.settle();
  assert.deepEqual(f.prompts, ['donne le statut']);
  assert.equal(f.engine.state, 'listening');
});

test('standby ignores ordinary speech without a wake phrase', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  for (const [index, text] of ['ouvre le navigateur', 'un exemple de modèle', 'le nom de ce film est elmore', 'je parle de Moctar'].entries()) {
    f.rec.emit(text, true, index);
    await f.settle();
  }
  assert.deepEqual(f.prompts, []);
  assert.deepEqual(f.speech, []);
  assert.equal(f.engine.state, 'wake');
});

test('recognition is suspended during the spoken invitation and ignores its echo', async t => {
  const greeting = deferred();
  const f = fixture(t, { speak: () => greeting.promise });
  await f.engine.enable();
  await f.settle();
  const previous = f.rec;
  previous.emit('yo moctar');
  await f.settle();
  assert.equal(f.active.length, 0);
  previous.emit('oui moctar je t’écoute que souhaites-tu', true, 1);
  await f.settle();
  assert.deepEqual(f.prompts, []);
  greeting.resolve(true);
  await f.settle();
  assert.equal(f.engine.state, 'listening');
  assert.equal(f.active.length, 1);
});

test('a final command and late duplicate trigger one request while its answer is speaking', async t => {
  const request = deferred();
  const prompts = [];
  const f = fixture(t, { onPrompt: text => { prompts.push(text); return request.promise; } });
  await f.engine.enable();
  await f.settle();
  const previous = f.rec;
  previous.emit('yo moctar état système', false);
  previous.emit('yo moctar état système');
  previous.emit('yo moctar état système');
  await f.settle();
  assert.deepEqual(prompts, ['état système']);
  assert.equal(f.active.length, 0);
  previous.emit('texte provenant de la réponse', true, 1);
  assert.deepEqual(prompts, ['état système']);
  request.resolve();
  await f.settle();
  assert.equal(f.engine.state, 'listening');
  assert.equal(f.active.length, 1);
});

test('followup commands need no wake word and expire after fifteen seconds', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  f.rec.emit('yo moctar état système');
  await f.settle();
  f.rec.emit('et la mémoire disponible');
  await f.settle();
  assert.deepEqual(f.prompts, ['état système', 'et la mémoire disponible']);
  f.time.advance(14999);
  await f.settle();
  assert.equal(f.engine.state, 'listening');
  f.time.advance(1);
  await f.settle();
  assert.equal(f.engine.state, 'wake');
  f.rec.emit('autre conversation');
  await f.settle();
  assert.equal(f.prompts.length, 2);
});

test('a followup command mentioning Moctar stays intact and does not trigger another invitation', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  f.rec.emit('yo moctar état système');
  await f.settle();
  f.rec.emit('quelle est la température Moctar');
  await f.settle();
  assert.deepEqual(f.prompts, ['état système', 'quelle est la température Moctar']);
  assert.deepEqual(f.speech, []);
  assert.equal(f.engine.state, 'listening');
});

test('browser recognition ending naturally restarts the wake listener', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  for (let cycle = 0; cycle < 12; cycle++) {
    const starts = f.rec.starts;
    f.rec.end();
    f.time.advance(249);
    await f.settle();
    assert.equal(f.rec.starts, starts, 'natural endings are debounced');
    assert.equal(f.engine.listeningDesired, true);
    f.time.advance(1);
    await f.settle();
    assert.equal(f.engine.state, 'wake');
    assert.equal(f.active.length, 1);
    assert.equal(f.rec.starts, starts + 1);
  }
  assert.equal(f.instances.length, 1, 'all restarts reuse the same recognizer');
});

for (const error of ['no-speech', 'aborted', 'audio-capture']) {
  test(`repeated ${error} events preserve listening and restart without an error budget`, async t => {
    const f = fixture(t);
    await f.engine.enable();
    await f.settle();
    for (let cycle = 0; cycle < 12; cycle++) {
      const starts = f.rec.starts;
      f.rec.fail(error);
      await f.settle();
      assert.equal(f.engine.listeningDesired, true);
      assert.equal(f.active.length, 0);
      f.time.advance(249);
      await f.settle();
      assert.equal(f.rec.starts, starts);
      f.time.advance(1);
      await f.settle();
      assert.equal(f.active.length, 1);
      assert.equal(f.engine.state, 'wake');
    }
    assert.equal(f.states.some(({ state }) => state === 'error'), false);
    f.rec.emit('elmo donne le statut');
    await f.settle();
    assert.deepEqual(f.prompts, ['donne le statut']);
  });
}

test('network interruptions retry after 1500 ms indefinitely without disabling listening', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  for (let cycle = 0; cycle < 12; cycle++) {
    const starts = f.rec.starts;
    f.rec.fail('network');
    await f.settle();
    assert.equal(f.engine.listeningDesired, true);
    f.time.advance(1499);
    await f.settle();
    assert.equal(f.rec.starts, starts);
    f.time.advance(1);
    await f.settle();
    assert.equal(f.rec.starts, starts + 1);
    assert.equal(f.active.length, 1);
  }
  assert.equal(f.states.some(({ state }) => state === 'error'), false);
});

test('a service restriction preserves listening intent and explains browser permissions', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  for (let cycle = 0; cycle < 6; cycle++) {
    const starts = f.rec.starts;
    f.rec.fail('service-not-allowed');
    await f.settle();
    assert.equal(f.engine.listeningDesired, true);
    f.time.advance(1499);
    await f.settle();
    assert.equal(f.rec.starts, starts);
    f.time.advance(1);
    await f.settle();
    assert.equal(f.rec.starts, starts + 1);
  }
  assert.ok(f.states.some(({ detail }) => detail.error === 'service-not-allowed' && /autorisations/.test(detail.message)));
  assert.equal(f.states.some(({ state }) => state === 'error'), false);
});

test('explicit disabling cancels delayed network and natural-end restarts', async t => {
  for (const error of ['network', 'no-speech', 'aborted', 'audio-capture']) {
    const f = fixture(t);
    await f.engine.enable();
    await f.settle();
    f.rec.fail(error);
    await f.settle();
    f.engine.disable();
    f.time.advance(60000);
    await f.settle();
    assert.equal(f.engine.listeningDesired, false, error);
    assert.equal(f.engine.state, 'off', error);
    assert.equal(f.rec.starts, 1, error);
    assert.equal(f.active.length, 0, error);
    f.engine.destroy();
  }
});

test('disabling during pending greeting prevents automatic microphone reactivation', async t => {
  const greeting = deferred();
  const f = fixture(t, { speak: () => greeting.promise });
  await f.engine.enable();
  await f.settle();
  f.rec.emit('elmo');
  await f.settle();
  f.engine.disable();
  greeting.resolve(true);
  await f.settle();
  f.time.advance(30000);
  await f.settle();
  assert.equal(f.engine.enabled, false);
  assert.equal(f.engine.state, 'off');
  assert.equal(f.active.length, 0);
});

test('suspending during a pending request keeps the microphone paused until visible again', async t => {
  const request = deferred();
  const f = fixture(t, { onPrompt: () => request.promise });
  await f.engine.enable();
  await f.settle();
  f.rec.emit('yo moctar état système');
  await f.settle();
  f.engine.suspend();
  request.resolve();
  await f.settle();
  f.time.advance(30000);
  await f.settle();
  assert.equal(f.active.length, 0);
  assert.equal(f.engine.state, 'paused');
  assert.equal(f.engine.enabled, true);
  f.engine.resume();
  await f.settle();
  assert.equal(f.engine.state, 'wake');
  assert.equal(f.active.length, 1);
});

test('permission denial produces an error and never loops microphone retries', async t => {
  for (const error of ['not-allowed']) {
    const f = fixture(t);
    await f.engine.enable();
    await f.settle();
    f.rec.fail(error);
    await f.settle();
    f.time.advance(60000);
    await f.settle();
    assert.equal(f.engine.state, 'error', error);
    assert.equal(f.active.length, 0, error);
    assert.equal(f.instances.reduce((sum, item) => sum + item.starts, 0), 1, error);
    assert.ok(f.states.some(({ detail }) => detail && /micro|autoris|permission/i.test(detail.message || detail.error || '')), error);
    f.engine.destroy();
  }
});

test('manual microphone enters the same listener without requiring a wake phrase', async t => {
  const f = fixture(t);
  await f.engine.startManual();
  await f.settle();
  assert.equal(f.engine.state, 'listening');
  assert.equal(f.active.length, 1);
  f.rec.emit('donne le statut');
  await f.settle();
  assert.deepEqual(f.prompts, ['donne le statut']);
});

test('storage being unavailable does not prevent explicit listening', async t => {
  const unavailable = () => { throw new Error('SecurityError: storage denied'); };
  const f = fixture(t, { storage: { getItem: unavailable, setItem: unavailable, removeItem: unavailable } });
  await f.engine.enable();
  await f.settle();
  assert.equal(f.engine.state, 'wake');
  f.engine.disable();
  await f.settle();
  assert.equal(f.engine.state, 'off');
});

test('external speech or a typed request pauses listening until busy is cleared', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  const previous = f.rec;
  f.engine.setBusy(true);
  await f.settle();
  assert.equal(f.active.length, 0);
  previous.emit('yo moctar echo');
  assert.deepEqual(f.prompts, []);
  f.engine.setBusy(false);
  await f.settle();
  assert.equal(f.active.length, 1);
});

test('a missing microphone stop confirmation preserves intent without concurrent restarts until confirmed', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  const rec = f.rec;
  rec.abort = () => { rec.active = false; };
  rec.emit('elmo');
  await f.settle();
  assert.deepEqual(f.speech, [], 'do not speak before microphone stop is confirmed');
  f.engine.disable();
  await f.engine.enable();
  await f.settle();
  assert.equal(rec.starts, 1, 'rapid reactivation must wait for the previous onend');
  f.time.advance(2500);
  await f.settle();
  assert.equal(f.engine.state, 'error');
  assert.equal(f.engine.enabled, true, 'a browser stop timeout must not revoke listening intent');
  assert.equal(await f.engine.enable(), false, 'an unconfirmed stop must prevent another start');
  assert.equal(rec.starts, 1);
  assert.deepEqual(f.speech, []);
  assert.deepEqual(f.prompts, []);
  assert.ok(f.states.some(({ detail }) => detail && detail.error === 'stop-timeout'));
  rec.end();
  await f.settle();
  assert.equal(rec.starts, 1, 'a confirmed late onend still respects restart debounce');
  f.time.advance(250);
  await f.settle();
  assert.equal(rec.starts, 2);
  assert.equal(f.engine.state, 'wake');
});

test('a phrase begun near followup expiry keeps its final command after the original deadline', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  f.rec.emit('yo moctar état système');
  await f.settle();
  f.time.advance(14900);
  f.rec.emit('et la mémoire', false);
  await f.settle();
  assert.deepEqual(f.prompts, ['état système'], 'interim words are never sent as commands');
  f.time.advance(1100);
  await f.settle();
  assert.equal(f.engine.state, 'listening');
  f.rec.emit('et la mémoire disponible');
  await f.settle();
  assert.deepEqual(f.prompts, ['état système', 'et la mémoire disponible']);
});

test('manual dictation shares the enabled wake listener and stopping it restores wake mode', async t => {
  const f = fixture(t);
  await f.engine.enable();
  await f.settle();
  const rec = f.rec;
  await f.engine.startManual();
  await f.settle();
  assert.equal(f.engine.state, 'listening');
  assert.equal(f.rec, rec);
  assert.equal(rec.starts, 1, 'manual dictation must reuse active recognition');
  f.engine.stopManual();
  await f.settle();
  assert.equal(f.engine.enabled, true);
  assert.equal(f.engine.state, 'wake');
  assert.equal(f.active.length, 1);
  rec.emit('elmo');
  await f.settle();
  assert.equal(f.speech.length, 1);
  assert.equal(f.engine.state, 'listening');
});

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { create } = require('../static/voice.js');

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
    clearTimeout(id) {
      pending.delete(id);
    },
  };
  const rafQueue = new Map();
  let nextRafId = 1;
  const requestAnimationFrame = callback => {
    const id = nextRafId++;
    rafQueue.set(id, callback);
    return id;
  };
  const cancelAnimationFrame = id => {
    rafQueue.delete(id);
  };
  return {
    timers,
    requestAnimationFrame,
    cancelAnimationFrame,
    stepFrame(ms = 16) {
      this.advance(ms);
      const callbacks = [...rafQueue.entries()];
      rafQueue.clear();
      for (const [, cb] of callbacks) {
        cb(now);
      }
    },
    advance(milliseconds) {
      const target = now + milliseconds;
      let iterations = 0;
      while (true) {
        const next = [...pending.entries()]
          .filter(([, item]) => item.at <= target)
          .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!next) break;
        assert.ok(++iterations < 500, 'timers must not loop infinitely');
        now = next[1].at;
        pending.delete(next[0]);
        next[1].callback();
      }
      now = target;
    },
    get now() {
      return now;
    },
  };
}

function createWebAudioFixture(t, overrides = {}) {
  let currentRms = 0;
  let getUserMediaCalls = [];
  let activeRecorders = [];
  let audioPlayedUrls = [];
  let finishAudioPlayback = null;
  const states = [];
  const prompts = [];
  const speech = [];
  const solarActivityCalls = [];
  const solarStateCalls = [];
  const interactResults = [];
  const time = clock();

  class FakeAnalyser {
    constructor() {
      this.fftSize = 512;
      this.smoothingTimeConstant = 0.25;
    }
    get currentRms() {
      return currentRms;
    }
    getFloatTimeDomainData(buf) {
      buf.fill(currentRms);
    }
  }

  class FakeAudioContext {
    constructor() {
      this.state = 'running';
      this.closed = false;
    }
    resume() {
      this.state = 'running';
      return Promise.resolve();
    }
    createMediaStreamSource() {
      return { connect() {}, disconnect() {} };
    }
    createAnalyser() {
      return new FakeAnalyser();
    }
    close() {
      this.closed = true;
      this.state = 'closed';
      return Promise.resolve();
    }
  }

  class FakeMediaRecorder {
    static isTypeSupported() {
      return true;
    }
    constructor(stream, opts = {}) {
      this.stream = stream;
      this.mimeType = opts.mimeType || 'audio/webm';
      this.state = 'inactive';
      activeRecorders.push(this);
    }
    start() {
      this.state = 'recording';
    }
    stop() {
      if (this.state === 'inactive') return;
      this.state = 'inactive';
      this.ondataavailable?.({ data: { size: 2048, type: this.mimeType } });
      this.onstop?.();
    }
  }

  class FakeAudio {
    constructor(url) {
      this.url = url;
      audioPlayedUrls.push(url);
      finishAudioPlayback = () => this.onended?.();
    }
    play() {
      return Promise.resolve();
    }
    pause() {}
  }

  const fakeSolarEngine = {
    setVoiceActivity(volume, isSpeaking) {
      solarActivityCalls.push({ volume, isSpeaking });
    },
    setVoiceState(state) {
      solarStateCalls.push(state);
    },
  };

  let nextInteractResponse = {
    ok: true,
    ignored: false,
    user_text: 'Yo Moctar quel est le statut CPU',
    reply_text: 'Tous les cœurs CPU sont nominaux, Moctar.',
    audio_url: '/api/voice/tts/cache/abcdef0123456789abcdef.mp3',
    session_active: true,
  };
  let interactCalls = 0;

  const engine = create({
    mediaDevices: {
      getUserMedia: async constraints => {
        getUserMediaCalls.push(constraints);
        return { getTracks: () => [{ stop() {} }] };
      },
    },
    MediaRecorder: FakeMediaRecorder,
    AudioContext: FakeAudioContext,
    Audio: FakeAudio,
    SolarEngine: fakeSolarEngine,
    timers: time.timers,
    requestAnimationFrame: time.requestAnimationFrame,
    cancelAnimationFrame: time.cancelAnimationFrame,
    vadSilenceMs: 800,
    vadThreshold: 0.02,
    storage: { getItem: () => null, setItem() {}, removeItem() {} },
    onState: (state, detail) => states.push({ state, detail }),
    onInteractResult: payload => interactResults.push(payload),
    speak: async text => {
      speech.push(text);
      return true;
    },
    onPrompt: async text => {
      prompts.push(text);
    },
    fetch: async url => {
      assert.equal(url, '/api/voice/interact');
      interactCalls += 1;
      const body = typeof nextInteractResponse === 'function' ? await nextInteractResponse() : nextInteractResponse;
      return {
        ok: true,
        json: async () => body,
      };
    },
    ...overrides,
  });

  t.after(() => engine.destroy());

  return {
    engine,
    time,
    states,
    prompts,
    speech,
    solarActivityCalls,
    solarStateCalls,
    interactResults,
    getUserMediaCalls,
    audioPlayedUrls,
    get activeRecorders() {
      return activeRecorders;
    },
    get latestRecorder() {
      return activeRecorders.at(-1);
    },
    get interactCalls() {
      return interactCalls;
    },
    set rms(value) {
      currentRms = value;
    },
    set interactResponse(value) {
      nextInteractResponse = value;
    },
    finishAudio() {
      finishAudioPlayback?.();
    },
    async settle() {
      for (let i = 0; i < 20; i++) {
        await Promise.resolve();
        time.advance(0);
      }
    },
  };
}

test('zero references to webkitSpeechRecognition or SpeechRecognition exist in static/voice.js and static/index.html', () => {
  const voiceSource = fs.readFileSync(path.join(__dirname, '../static/voice.js'), 'utf8');
  const indexSource = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
  assert.equal(/webkitSpeechRecognition|SpeechRecognition/.test(voiceSource), false, 'voice.js must not reference SpeechRecognition');
  assert.equal(/webkitSpeechRecognition|SpeechRecognition/.test(indexSource), false, 'index.html must not reference SpeechRecognition');
});

test('cache-busting ?v=3.1 and Service Worker skipWaiting are configured', () => {
  const indexSource = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
  const swSource = fs.readFileSync(path.join(__dirname, '../static/service-worker.js'), 'utf8');
  assert.ok(indexSource.includes('/static/voice.js?v=3.1'), 'index.html must load /static/voice.js?v=3.1');
  assert.ok(indexSource.includes('/static/cosmos.js?v=3.1'), 'index.html must load /static/cosmos.js?v=3.1');
  assert.ok(swSource.includes('self.skipWaiting()'), 'service-worker.js must call self.skipWaiting()');
  assert.ok(swSource.includes('.endsWith(\'.js\')'), 'service-worker.js must invalidate cached .js files');
});

test('enabling immediately initializes getUserMedia with echoCancellation & noiseSuppression and displays green listening message', async t => {
  const f = createWebAudioFixture(t);
  assert.equal(f.engine.enabled, false);

  await f.engine.enable();
  await f.settle();

  assert.equal(f.engine.enabled, true);
  assert.equal(f.engine.usingWebAudio, true);
  assert.equal(f.getUserMediaCalls.length, 1);
  assert.deepEqual(f.getUserMediaCalls[0], {
    audio: { echoCancellation: true, noiseSuppression: true },
  });
  assert.equal(f.engine.state, 'wake');
  const wakeStates = f.states.filter(s => s.state === 'wake');
  assert.ok(wakeStates.length >= 1);
  assert.equal(wakeStates.at(-1).detail.message, '🟢 À l\'écoute — Dites « Yo Moctar »');
});

test('60 FPS VU-meter updates SolarEngine.setVoiceActivity on every frame, starts MediaRecorder on RMS > 0.02, and posts after 800ms silence', async t => {
  const f = createWebAudioFixture(t);
  await f.engine.enable();
  await f.settle();

  // Frame 1: silence (RMS = 0.005)
  f.rms = 0.005;
  f.time.stepFrame(16);
  await f.settle();
  assert.ok(f.solarActivityCalls.length >= 1);
  assert.equal(f.solarActivityCalls.at(-1).volume, 0.005);
  assert.equal(f.solarActivityCalls.at(-1).isSpeaking, false);

  // Frame 2: user speaks (RMS = 0.12 > 0.02)
  f.rms = 0.12;
  f.time.stepFrame(16);
  await f.settle();

  assert.equal(f.engine.state, 'listening');
  assert.equal(f.states.at(-1).detail.message, '🎙️ Voix détectée...');
  assert.equal(f.latestRecorder.state, 'recording');
  assert.equal(f.solarActivityCalls.at(-1).volume, 0.12);
  assert.equal(f.solarActivityCalls.at(-1).isSpeaking, true);

  // Silence begins: 30 frames at 60 FPS (500ms < 800ms) -> still recording
  f.rms = 0.001;
  for (let frame = 0; frame < 30; frame += 1) {
    f.time.stepFrame(16);
  }
  await f.settle();
  assert.equal(f.latestRecorder.state, 'recording');
  assert.equal(f.interactCalls, 0);

  // Advance remaining 20 frames (> 800ms total silence at 60 FPS) -> MediaRecorder stops and posts to /api/voice/interact
  for (let frame = 0; frame < 20; frame += 1) {
    f.time.stepFrame(16);
  }
  await f.settle();

  assert.equal(f.latestRecorder.state, 'inactive');
  assert.equal(f.interactCalls, 1);
  assert.equal(f.interactResults.length, 1);
  assert.equal(f.audioPlayedUrls[0], '/api/voice/tts/cache/abcdef0123456789abcdef.mp3');
  assert.equal(f.engine.state, 'speaking');

  // Finish audio playback -> returns to active listening
  f.finishAudio();
  await f.settle();
  assert.equal(f.engine.state, 'listening');
});

test('disabling stops microphone tracks, resets SolarEngine voice activity to 0, and sets state to off', async t => {
  let trackStopped = false;
  const f = createWebAudioFixture(t, {
    mediaDevices: {
      getUserMedia: async () => ({
        getTracks: () => [{ stop() { trackStopped = true; } }],
      }),
    },
  });

  await f.engine.enable();
  await f.settle();
  assert.equal(f.engine.enabled, true);

  f.engine.disable();
  await f.settle();

  assert.equal(f.engine.enabled, false);
  assert.equal(f.engine.state, 'off');
  assert.equal(trackStopped, true);
  assert.deepEqual(f.solarActivityCalls.at(-1), { volume: 0, isSpeaking: false });
});

test('permission denial (NotAllowedError) reports a clear error and disables retry loops', async t => {
  let attempts = 0;
  const f = createWebAudioFixture(t, {
    mediaDevices: {
      getUserMedia: async () => {
        attempts += 1;
        const err = new Error('Permission denied');
        err.name = 'NotAllowedError';
        throw err;
      },
    },
  });

  const ok = await f.engine.enable();
  await f.settle();
  f.time.advance(60000);
  await f.settle();

  assert.equal(ok, false);
  assert.equal(attempts, 1);
  assert.equal(f.engine.enabled, false);
  assert.equal(f.engine.state, 'error');
  assert.ok(f.states.some(({ detail }) => /Autorisez le microphone/i.test(detail?.message || '')));
});

test('suspend and resume pause and restore the WebAudio pipeline cleanly', async t => {
  const f = createWebAudioFixture(t);
  await f.engine.enable();
  await f.settle();
  assert.equal(f.engine.state, 'wake');

  f.engine.suspend();
  await f.settle();
  assert.equal(f.engine.state, 'paused');
  assert.equal(f.engine.enabled, true);

  f.engine.resume();
  await f.settle();
  assert.equal(f.engine.state, 'wake');
  assert.equal(f.getUserMediaCalls.length, 2);
});

test('setBusy pauses capture while busy and restores listening when cleared', async t => {
  const f = createWebAudioFixture(t);
  await f.engine.enable();
  await f.settle();

  f.rms = 0.1;
  f.time.stepFrame(16);
  await f.settle();
  assert.equal(f.latestRecorder.state, 'recording');

  f.engine.setBusy(true);
  await f.settle();
  assert.equal(f.latestRecorder.state, 'inactive');

  f.engine.setBusy(false);
  await f.settle();
  assert.equal(f.engine.state, 'wake');
});

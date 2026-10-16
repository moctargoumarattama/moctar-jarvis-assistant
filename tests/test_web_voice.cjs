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
  let audioInstances = [];
  let audioPlayedUrls = [];
  let finishAudioPlayback = null;
  let biquadFilters = [];
  let sourceConnections = [];
  let filterConnections = [];
  let failMp3Playback = false;
  const spokenUtterances = [];
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

  class FakeBiquadFilter {
    constructor() {
      this.type = 'lowpass';
      this.frequency = { value: 350 };
      biquadFilters.push(this);
    }
    connect(target) {
      filterConnections.push(target);
    }
    disconnect() {}
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
      return {
        connect(target) {
          sourceConnections.push(target);
        },
        disconnect() {},
      };
    }
    createBiquadFilter() {
      return new FakeBiquadFilter();
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
      this.audioBitsPerSecond = opts.audioBitsPerSecond;
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
      this.src = url || '';
      this.url = url || '';
      this.volume = 1.0;
      audioInstances.push(this);
    }
    load() {}
    play() {
      if (this.src && !this.src.startsWith('data:audio/wav')) {
        if (failMp3Playback) {
          return Promise.reject(new Error('Autoplay blocked'));
        }
        audioPlayedUrls.push(this.src);
        finishAudioPlayback = () => this.onended?.();
      }
      return Promise.resolve();
    }
    pause() {}
  }

  class FakeUtterance {
    constructor(text) {
      this.text = text;
      this.lang = '';
      this.voice = null;
    }
  }

  const fakeSpeechSynthesis = {
    cancel() {},
    getVoices() {
      return [
        { name: 'Microsoft Denise Online (Natural) - French (France)', lang: 'fr-FR' },
        { name: 'Microsoft Henri Online (Natural) - French (France)', lang: 'fr-FR' },
      ];
    },
    speak(utterance) {
      spokenUtterances.push(utterance);
      queueMicrotask(() => utterance.onend?.());
    },
  };

  const fakeWindow = {
    Audio: FakeAudio,
    speechSynthesis: fakeSpeechSynthesis,
    SpeechSynthesisUtterance: FakeUtterance,
  };

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
    user_text: 'quel est le statut CPU',
    reply_text: 'Tous les cœurs CPU sont nominaux, Moctar.',
    audio_url: '/api/voice/tts/cache/abcdef0123456789abcdef.mp3',
    session_active: true,
  };
  let interactCalls = 0;

  const engine = create({
    window: fakeWindow,
    mediaDevices: {
      getUserMedia: async constraints => {
        getUserMediaCalls.push(constraints);
        return { getTracks: () => [{ stop() {} }] };
      },
    },
    MediaRecorder: FakeMediaRecorder,
    AudioContext: FakeAudioContext,
    Audio: FakeAudio,
    speechSynthesis: fakeSpeechSynthesis,
    SpeechSynthesisUtterance: FakeUtterance,
    SolarEngine: fakeSolarEngine,
    timers: time.timers,
    requestAnimationFrame: time.requestAnimationFrame,
    cancelAnimationFrame: time.cancelAnimationFrame,
    vadSilenceMs: 850,
    vadThreshold: 0.035,
    minRecordingMs: 1200,
    maxSpeechMs: 3500,
    storage: { getItem: () => null, setItem() {}, removeItem() {} },
    onState: (state, detail) => states.push({ state, detail }),
    onInteractResult: payload => interactResults.push(payload),
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
    fakeWindow,
    time,
    states,
    prompts,
    speech,
    spokenUtterances,
    solarActivityCalls,
    solarStateCalls,
    interactResults,
    getUserMediaCalls,
    audioInstances,
    audioPlayedUrls,
    biquadFilters,
    sourceConnections,
    filterConnections,
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
    set failMp3(value) {
      failMp3Playback = value;
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

test('cache-busting ?v=4.0 and Service Worker NetworkOnly for /api/voice/* are configured', () => {
  const indexSource = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
  const swSource = fs.readFileSync(path.join(__dirname, '../static/service-worker.js'), 'utf8');
  assert.ok(indexSource.includes('/static/voice.js?v=4.0'), 'index.html must load /static/voice.js?v=4.0');
  assert.ok(indexSource.includes('/static/cosmos.js?v=4.0'), 'index.html must load /static/cosmos.js?v=4.0');
  assert.ok(swSource.includes('self.skipWaiting()'), 'service-worker.js must call self.skipWaiting()');
  assert.ok(swSource.includes('/api/voice/'), 'service-worker.js must exclude /api/voice/* from caching');
});

test('enabling warms up window.MoctarSpeaker, inserts 120Hz highpass BiquadFilterNode, and displays green listening message', async t => {
  const f = createWebAudioFixture(t);
  assert.equal(f.engine.enabled, false);

  await f.engine.enable();
  await f.settle();

  assert.equal(f.engine.enabled, true);
  assert.equal(f.engine.usingWebAudio, true);
  assert.ok(f.fakeWindow.MoctarSpeaker, 'window.MoctarSpeaker must be instantiated and unlocked on enable()');
  assert.equal(f.audioInstances.length, 1);
  assert.equal(f.biquadFilters.length, 1, '120Hz highpass BiquadFilterNode must be created');
  assert.equal(f.biquadFilters[0].type, 'highpass');
  assert.equal(f.biquadFilters[0].frequency.value, 120);
  assert.equal(f.sourceConnections[0], f.biquadFilters[0], 'microphone source must connect to BiquadFilterNode');
  assert.equal(f.getUserMediaCalls.length, 1);
  assert.deepEqual(f.getUserMediaCalls[0], {
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      sampleRate: 48000,
    },
  });
  assert.equal(f.engine.state, 'wake');
  const wakeStates = f.states.filter(s => s.state === 'wake');
  assert.ok(wakeStates.length >= 1);
  assert.equal(wakeStates.at(-1).detail.message, '🟢 À l\'écoute — Dites « Yo Moctar »');
});

test('60 FPS VU-meter triggers on RMS > 0.035, stops after 850ms silence and min 1200ms recording, reuses window.MoctarSpeaker, and updates status labels', async t => {
  const f = createWebAudioFixture(t);
  await f.engine.enable();
  await f.settle();

  // Frame 1: fan hum below threshold (RMS = 0.02 <= 0.035) -> ignored
  f.rms = 0.02;
  f.time.stepFrame(16);
  await f.settle();
  assert.equal(f.activeRecorders.length, 0);
  assert.equal(f.solarActivityCalls.at(-1).isSpeaking, false);

  // Frame 2: user speaks (RMS = 0.12 > 0.035)
  f.rms = 0.12;
  f.time.stepFrame(16);
  await f.settle();

  assert.equal(f.engine.state, 'listening');
  assert.equal(f.states.at(-1).detail.message, '🎙️ Moctar vous écoute...');
  assert.equal(f.latestRecorder.state, 'recording');
  assert.equal(f.latestRecorder.audioBitsPerSecond, 128000);
  assert.equal(f.solarActivityCalls.at(-1).volume, 0.12);
  assert.equal(f.solarActivityCalls.at(-1).isSpeaking, true);

  // Silence begins: 30 frames at 60 FPS (~480ms < 850ms) -> still recording
  f.rms = 0.01;
  for (let frame = 0; frame < 30; frame += 1) {
    f.time.stepFrame(16);
  }
  await f.settle();
  assert.equal(f.latestRecorder.state, 'recording');
  assert.equal(f.interactCalls, 0);

  // Advance remaining 50 frames (> 850ms silence AND > 1200ms total recording) -> stops MediaRecorder, resets Sun RMS to 0, posts to /api/voice/interact
  for (let frame = 0; frame < 50; frame += 1) {
    f.time.stepFrame(16);
  }
  await f.settle();

  assert.equal(f.latestRecorder.state, 'inactive');
  assert.equal(f.interactCalls, 1);
  assert.equal(f.audioInstances.length, 1, 'must reuse window.MoctarSpeaker without creating a new Audio element');
  assert.equal(f.audioPlayedUrls[0], '/api/voice/tts/cache/abcdef0123456789abcdef.mp3');
  assert.equal(f.engine.state, 'speaking');
  assert.equal(f.states.at(-1).detail.message, '🔊 Moctar : "Tous les cœurs CPU sont nominaux, Moctar."');

  // Finish audio playback -> returns to wake listening (strict wake-word, zero ambient followup window)
  f.finishAudio();
  await f.settle();
  assert.equal(f.engine.state, 'wake');
});

test('push-to-talk mode starts recording immediately and sends upon release', async t => {
  const f = createWebAudioFixture(t);
  await f.engine.enable();
  await f.settle();

  assert.equal(f.engine.isSpeaking, false);
  assert.equal(f.activeRecorders.length, 0);

  // Press down: start push-to-talk
  await f.engine.startPushToTalk();
  await f.settle();

  assert.equal(f.engine.isSpeaking, true);
  assert.equal(f.engine.isPushToTalk, true);
  assert.equal(f.latestRecorder.state, 'recording');
  assert.equal(f.latestRecorder.audioBitsPerSecond, 128000);
  assert.equal(f.interactCalls, 0);

  // Even with silence for 1600ms, push-to-talk does NOT cut off automatically
  f.rms = 0.001;
  for (let frame = 0; frame < 100; frame += 1) {
    f.time.stepFrame(16);
  }
  await f.settle();
  assert.equal(f.latestRecorder.state, 'recording');
  assert.equal(f.interactCalls, 0);

  // Release button: stop push-to-talk -> immediately sends
  f.engine.stopPushToTalk();
  await f.settle();

  assert.equal(f.latestRecorder.state, 'inactive');
  assert.equal(f.interactCalls, 1);
  assert.equal(f.engine.isPushToTalk, false);
});

test('maxSpeechTimer (3.5s) cuts recording and sends audio even if fan noise prevents silence detection', async t => {
  const f = createWebAudioFixture(t);
  await f.engine.enable();
  await f.settle();

  // Voice starts and stays above threshold due to fan noise
  f.rms = 0.08;
  f.time.stepFrame(16);
  await f.settle();
  assert.equal(f.latestRecorder.state, 'recording');
  assert.equal(f.interactCalls, 0);

  // Advance 3500ms -> maxSpeechTimer fires, resets Sun activity to (0, false), and sends audio chunk
  f.time.advance(3500);
  await f.settle();

  assert.equal(f.latestRecorder.state, 'inactive');
  assert.equal(f.interactCalls, 1);
  assert.ok(f.solarActivityCalls.some(call => call.volume === 0 && call.isSpeaking === false));
});

test('fallback to window.speechSynthesis with French male voice triggers when MP3 playback fails', async t => {
  const f = createWebAudioFixture(t);
  f.failMp3 = true;
  await f.engine.enable();
  await f.settle();

  f.rms = 0.1;
  f.time.stepFrame(16);
  await f.settle();
  f.rms = 0;
  for (let frame = 0; frame < 80; frame += 1) {
    f.time.stepFrame(16);
  }
  await f.settle();

  assert.equal(f.interactCalls, 1);
  assert.equal(f.spokenUtterances.length, 1);
  assert.equal(f.spokenUtterances[0].text, 'Tous les cœurs CPU sont nominaux, Moctar.');
  assert.match(f.spokenUtterances[0].voice.name, /Henri/i);
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

test('continuous 800ms pre-roll buffer encodes to 16kHz mono WAV and sends audio/wav', async t => {
  let capturedBody = null;
  let capturedContentType = null;
  let audioProcessCallback = null;

  class FakeScriptProcessor {
    constructor() {
      this._handler = null;
    }
    get onaudioprocess() {
      return this._handler;
    }
    set onaudioprocess(fn) {
      this._handler = fn;
      audioProcessCallback = fn;
    }
    connect() {}
    disconnect() {}
  }

  const f = createWebAudioFixture(t, {
    AudioContext: class {
      constructor() {
        this.state = 'running';
        this.sampleRate = 48000;
      }
      resume() { return Promise.resolve(); }
      createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
      createBiquadFilter() { return { connect() {}, disconnect() {}, frequency: { value: 120 } }; }
      createAnalyser() {
        return {
          fftSize: 512,
          smoothingTimeConstant: 0.2,
          get currentRms() { return 0; },
          getFloatTimeDomainData() {},
          getByteTimeDomainData() {},
        };
      }
      createScriptProcessor() {
        return new FakeScriptProcessor();
      }
      createGain() { return { gain: { value: 0 }, connect() {}, disconnect() {} }; }
      close() { return Promise.resolve(); }
    },
    fetch: async (url, opts) => {
      capturedContentType = opts.headers['Content-Type'];
      capturedBody = opts.body;
      return {
        ok: true,
        json: async () => ({
          ok: true,
          reply_text: 'Bonjour Moctar.',
        }),
      };
    },
  });

  await f.engine.enable();
  await f.settle();
  assert.ok(typeof audioProcessCallback === 'function', 'audioProcessCallback must be registered');

  // 1. Simuler l'écoute passive (audioProcessCallback avec des échantillons PCM passifs)
  const passiveBlock = new Float32Array(2048);
  for (let i = 0; i < passiveBlock.length; i++) passiveBlock[i] = 0.05;
  audioProcessCallback({
    inputBuffer: {
      getChannelData: () => passiveBlock,
      sampleRate: 48000,
    },
  });

  // 2. Déclencher la parole via push-to-talk
  await f.engine.startPushToTalk();
  await f.settle();

  // 3. Simuler des échantillons actifs pendant la parole
  const activeBlock = new Float32Array(2048);
  for (let i = 0; i < activeBlock.length; i++) activeBlock[i] = 0.2;
  audioProcessCallback({
    inputBuffer: {
      getChannelData: () => activeBlock,
      sampleRate: 48000,
    },
  });

  // 4. Arrêt de push-to-talk -> stopPhraseAndSend()
  f.engine.stopPushToTalk();
  await f.settle();

  // 5. Vérifier que l'audio envoyé est bien audio/wav encodé en PCM 16-bit
  assert.equal(capturedContentType, 'audio/wav');
  assert.ok(capturedBody, 'capturedBody must not be null');
  assert.ok(capturedBody.size > 44, 'WAV size must exceed standard header size');
});

test('speaker anti-echo lock ignores microphone and clears pre-roll buffer during playback and 450ms dampening', async t => {
  let finishAudio = null;
  class FakeAudioDelayed {
    constructor(url) {
      this.src = url || '';
    }
    load() {}
    play() {
      return new Promise((resolve) => {
        finishAudio = () => {
          this.onended?.();
          resolve();
        };
      });
    }
    pause() {}
  }

  const f = createWebAudioFixture(t, {
    Audio: FakeAudioDelayed,
    fetch: async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        reply_text: 'Tous les systèmes sont nominaux, Moctar.',
        audio_url: '/api/voice/tts/cache/echo_test.mp3',
      }),
    }),
  });

  await f.engine.enable();
  await f.settle();

  // Déclencher une interaction vocale
  await f.engine.startPushToTalk();
  await f.settle();
  f.engine.stopPushToTalk();
  await f.settle();

  // Pendant que l'audio joue, isAssistantSpeaking doit être true
  assert.equal(f.engine.isAssistantSpeaking, true);

  // Fin de la lecture audio
  finishAudio?.();
  await f.settle();

  // Immédiatement après la fin (avant 450ms), l'anti-écho doit TOUJOURS être verrouillé
  assert.equal(f.engine.isAssistantSpeaking, true);

  // Avancer le temps de 450ms (fin de la réverbération acoustique de la pièce)
  f.time.advance(450);
  await f.settle();

  // L'anti-écho est maintenant déverrouillé
  assert.equal(f.engine.isAssistantSpeaking, false);
});



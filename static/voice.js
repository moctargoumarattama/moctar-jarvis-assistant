(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MoctarVoice = api;
})(typeof window === 'object' ? window : null, function () {
  'use strict';

  const DEFAULT_WAKE_WORDS = [
    'yo moctar', 'hey moctar', 'moctar', 'ok moctar', 'salut moctar',
    'dis moctar', 'elmo', 'el mo', 'hey elmo', 'hey el mo',
  ];
  const FOLLOWUP_MS = 15000;
  const GREETING = 'Oui Moctar, je t’écoute. Que souhaites-tu ?';
  const WAKE_READY_MESSAGE = "🟢 À l'écoute — Dites « Yo Moctar »";
  const VOICE_DETECTED_MESSAGE = '🎙️ Voix détectée...';
  const normalize = (value) => String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const words = (text) => Array.from(String(text).matchAll(/[\p{L}\p{N}]+/gu), (match) => ({
    value: normalize(match[0]), index: match.index, end: match.index + match[0].length,
  }));
  const phrase = (text) => words(text).map((word) => word.value).join(' ');
  const STOP_PHRASES = new Set(['merci moctar', 'merci elmo', 'tu peux te reposer', 'fin de conversation']);

  function create(options) {
    const opts = options || {};
    const clock = opts.timers || {};
    const delay = clock.setTimeout ? clock.setTimeout.bind(clock) : setTimeout;
    const cancel = clock.clearTimeout ? clock.clearTimeout.bind(clock) : clearTimeout;

    // 60 FPS frame scheduler: uses requestAnimationFrame in browsers, or 16ms timer when deterministic test clock is injected
    const requestFrame = typeof opts.requestAnimationFrame === 'function'
      ? opts.requestAnimationFrame
      : (clock.setTimeout
        ? (cb) => delay(() => cb(Date.now()), 16)
        : (typeof window === 'object' && window && typeof window.requestAnimationFrame === 'function'
          ? window.requestAnimationFrame.bind(window)
          : (cb) => setTimeout(() => cb(Date.now()), 16)));
    const cancelFrame = typeof opts.cancelAnimationFrame === 'function'
      ? opts.cancelAnimationFrame
      : (clock.clearTimeout
        ? (id) => cancel(id)
        : (typeof window === 'object' && window && typeof window.cancelAnimationFrame === 'function'
          ? window.cancelAnimationFrame.bind(window)
          : (id) => clearTimeout(id)));

    const onState = typeof opts.onState === 'function' ? opts.onState : () => {};
    const onPrompt = typeof opts.onPrompt === 'function' ? opts.onPrompt : async () => {};
    const speak = typeof opts.speak === 'function' ? opts.speak : async () => false;
    const onInteractResult = typeof opts.onInteractResult === 'function' ? opts.onInteractResult : null;

    const aliases = (opts.wakeWords || DEFAULT_WAKE_WORDS).map((item) => words(item).map((word) => word.value))
      .filter((item) => item.length).sort((a, b) => b.length - a.length);

    const mediaDevices = opts.mediaDevices || (typeof window === 'object' && window && window.navigator ? window.navigator.mediaDevices : null);
    const MediaRecorderClass = opts.MediaRecorder || (typeof window === 'object' && window ? window.MediaRecorder : null);
    const AudioContextClass = opts.AudioContext || (typeof window === 'object' && window ? (window.AudioContext || window.webkitAudioContext) : null);
    const AudioPlayerClass = opts.Audio || (typeof window === 'object' && window && typeof window.Audio === 'function' ? window.Audio : (typeof Audio === 'function' ? Audio : null));
    const fetchImpl = opts.fetch || (typeof window === 'object' && window && typeof window.fetch === 'function' ? window.fetch.bind(window) : null);
    const interactUrl = opts.interactUrl || '/api/voice/interact';
    const vadSilenceMs = typeof opts.vadSilenceMs === 'number' ? opts.vadSilenceMs : 800;
    const vadThreshold = typeof opts.vadThreshold === 'number' ? opts.vadThreshold : 0.02;
    const frameIntervalMs = typeof opts.frameIntervalMs === 'number' ? opts.frameIntervalMs : (1000 / 60);

    const hasWebAudioSupport = Boolean(
      mediaDevices && typeof mediaDevices.getUserMedia === 'function'
    );

    let enabled = false;
    let manual = false;
    let suspended = false;
    let destroyed = false;
    let busy = false;
    let phase = 'wake';
    let state = hasWebAudioSupport ? 'off' : 'unsupported';
    let fault = null;
    let generation = 0;
    let job = null;
    let status = 'idle';

    let stream = null;
    let audioCtx = null;
    let sourceNode = null;
    let analyser = null;
    let timeDomainData = null;
    let recorder = null;
    let recordedChunks = [];
    let rafId = null;
    let followupTimer = null;
    let isSpeaking = false;
    let silenceElapsedMs = 0;
    let currentRms = 0;
    let processingAudio = false;
    let activeAudioPlayer = null;

    function resolveSolarEngine() {
      if (opts.SolarEngine) return opts.SolarEngine;
      if (typeof window === 'object' && window) {
        return window.SolarEngine || (window.CosmosEngine && window.CosmosEngine.SolarEngine) || null;
      }
      return null;
    }

    function updateSolarActivity(rmsLevel, speakingFlag) {
      const solar = resolveSolarEngine();
      if (solar && typeof solar.setVoiceActivity === 'function') {
        solar.setVoiceActivity(rmsLevel, speakingFlag);
      }
    }

    function emit(next, detail) {
      state = next;
      onState(next, detail || {});
    }

    function clearFollowup() {
      if (followupTimer !== null) {
        cancel(followupTimer);
        followupTimer = null;
      }
    }

    function stopRafLoop() {
      if (rafId !== null) {
        cancelFrame(rafId);
        rafId = null;
      }
    }

    function wanted() {
      return !destroyed && !suspended && !fault && !busy && !job && (enabled || manual);
    }

    function openFollowup() {
      phase = 'listening';
      clearFollowup();
      followupTimer = delay(() => {
        followupTimer = null;
        phase = 'wake';
        if (!enabled) manual = false;
        reconcile();
      }, FOLLOWUP_MS);
    }

    function pickMimeType() {
      let mimeType = 'audio/webm';
      if (MediaRecorderClass && typeof MediaRecorderClass.isTypeSupported === 'function') {
        for (const candidate of ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/wav']) {
          if (MediaRecorderClass.isTypeSupported(candidate)) {
            mimeType = candidate;
            break;
          }
        }
      }
      return mimeType;
    }

    function cleanupWebAudio() {
      stopRafLoop();
      isSpeaking = false;
      silenceElapsedMs = 0;
      currentRms = 0;
      updateSolarActivity(0, false);

      if (recorder) {
        try {
          recorder.ondataavailable = null;
          recorder.onstop = null;
          if (recorder.state && recorder.state !== 'inactive') {
            recorder.stop();
          }
        } catch {}
        recorder = null;
      }
      recordedChunks = [];

      if (sourceNode) {
        try { sourceNode.disconnect?.(); } catch {}
        sourceNode = null;
      }
      analyser = null;
      timeDomainData = null;

      if (audioCtx) {
        try { audioCtx.close?.(); } catch {}
        audioCtx = null;
      }

      if (stream && typeof stream.getTracks === 'function') {
        try {
          stream.getTracks().forEach((track) => track.stop());
        } catch {}
      }
      stream = null;
      status = 'idle';
    }

    function fail(error, message, disableListening = true) {
      fault = { error, message };
      if (disableListening) {
        enabled = false;
        manual = false;
      }
      generation += 1;
      clearFollowup();
      cleanupWebAudio();
      emit('error', fault);
    }

    function computeRmsLevel() {
      if (!analyser) return 0;
      if (typeof analyser.currentRms === 'number') {
        return Math.max(0, Math.min(1, analyser.currentRms));
      }
      if (typeof analyser.getFloatTimeDomainData === 'function' && timeDomainData instanceof Float32Array) {
        analyser.getFloatTimeDomainData(timeDomainData);
        let sumSq = 0;
        for (let i = 0; i < timeDomainData.length; i += 1) {
          const sample = timeDomainData[i];
          sumSq += sample * sample;
        }
        return Math.sqrt(sumSq / timeDomainData.length);
      }
      if (typeof analyser.getByteTimeDomainData === 'function' && timeDomainData) {
        analyser.getByteTimeDomainData(timeDomainData);
        let sumSq = 0;
        for (let i = 0; i < timeDomainData.length; i += 1) {
          const centered = (timeDomainData[i] - 128) / 128;
          sumSq += centered * centered;
        }
        return Math.sqrt(sumSq / timeDomainData.length);
      }
      return 0;
    }

    function playAudioUrl(audioUrl) {
      if (!audioUrl || !AudioPlayerClass) return Promise.resolve(false);
      return new Promise((resolve) => {
        let settled = false;
        const player = new AudioPlayerClass(audioUrl);
        activeAudioPlayer = player;
        const finish = (ok) => {
          if (settled) return;
          settled = true;
          if (activeAudioPlayer === player) activeAudioPlayer = null;
          try { player.pause?.(); } catch {}
          resolve(ok);
        };
        player.onended = () => finish(true);
        player.onerror = () => finish(false);
        try {
          const playPromise = player.play();
          if (playPromise && typeof playPromise.then === 'function') {
            playPromise.catch(() => finish(false));
          }
        } catch {
          finish(false);
        }
      });
    }

    function findWake(text) {
      const tokens = words(text);
      for (const alias of aliases) {
        if (alias.every((word, offset) => tokens[offset] && tokens[offset].value === word)) {
          const end = tokens[alias.length - 1].end;
          return { command: text.slice(end).replace(/^[\s,.:;!?…—–-]+/, '').trim() };
        }
      }
      return null;
    }

    async function handleLocalTextTranscript(text) {
      const trimmed = String(text || '').trim();
      if (!trimmed || !wanted()) return;
      if (STOP_PHRASES.has(phrase(trimmed))) {
        phase = 'wake';
        manual = false;
        clearFollowup();
        reconcile();
        return;
      }
      const wake = findWake(trimmed);
      if (phase === 'wake' && !wake) return;
      const isGreetingOnly = Boolean(wake && !wake.command);
      const commandText = wake ? wake.command : trimmed;
      const current = { generation };
      job = current;
      clearFollowup();
      emit(isGreetingOnly ? 'speaking' : 'processing', {
        transcript: trimmed,
        message: isGreetingOnly ? GREETING : 'M.O.C.T.A.R prépare la réponse…',
      });
      try {
        if (isGreetingOnly) {
          await speak(GREETING);
        } else {
          await onPrompt(commandText);
        }
        if (generation !== current.generation || destroyed || suspended) return;
        if (enabled || (isGreetingOnly && manual)) openFollowup();
        else manual = false;
      } catch (error) {
        if (generation === current.generation && !destroyed && !suspended) {
          emit('error', { error, message: 'La réponse vocale a échoué. Vous pouvez réessayer votre commande.' });
          if (enabled || manual) openFollowup();
        }
      } finally {
        if (job === current) job = null;
        reconcile();
      }
    }

    async function sendAudioChunkToInteract(blobParts, mimeType) {
      if (destroyed || suspended || !blobParts.length) return;
      const blob = (blobParts.length === 1 && blobParts[0] && typeof blobParts[0].size === 'number')
        ? blobParts[0]
        : (typeof Blob === 'function' ? new Blob(blobParts, { type: mimeType || 'audio/webm' }) : blobParts[0]);
      if (!blob || (typeof blob.size === 'number' && blob.size < 64)) return;

      processingAudio = true;
      updateSolarActivity(0, false);

      try {
        if (typeof opts.transcribe === 'function') {
          const text = await opts.transcribe(blob);
          if (text) await handleLocalTextTranscript(text);
          return;
        }
        if (!fetchImpl) return;

        const response = await fetchImpl(interactUrl, {
          method: 'POST',
          headers: {
            'Content-Type': (blob && blob.type) || mimeType || 'audio/webm',
            'X-Voice-Session-Active': (phase === 'listening' || manual) ? 'true' : 'false',
          },
          body: blob,
        });
        if (!response || !response.ok) return;
        const payload = await response.json();
        if (!payload || destroyed || suspended) return;

        if (typeof payload.text === 'string' && payload.reply_text === undefined) {
          await handleLocalTextTranscript(payload.text);
          return;
        }

        if (payload.ignored || !payload.reply_text) {
          if (wanted()) {
            emit(phase, {
              rms: 0,
              isSpeaking: false,
              message: phase === 'wake' ? WAKE_READY_MESSAGE : '🟢 Je vous écoute…',
            });
          }
          return;
        }

        const current = { generation };
        job = current;
        clearFollowup();
        try {
          onInteractResult?.(payload);
          emit('speaking', {
            transcript: payload.user_text || '',
            reply: payload.reply_text || '',
            audioUrl: payload.audio_url || null,
            message: '🔊 M.O.C.T.A.R vous répond…',
          });
          let played = false;
          if (payload.audio_url) {
            played = await playAudioUrl(payload.audio_url);
          }
          if (!played && payload.reply_text) {
            await speak(payload.reply_text);
          }
          if (generation !== current.generation || destroyed || suspended) return;
          openFollowup();
        } finally {
          if (job === current) job = null;
          processingAudio = false;
          reconcile();
        }
      } catch {
        processingAudio = false;
        if (wanted()) {
          emit(phase, {
            rms: 0,
            isSpeaking: false,
            message: phase === 'wake' ? WAKE_READY_MESSAGE : '🟢 Je vous écoute…',
          });
        }
      } finally {
        processingAudio = false;
      }
    }

    function startPhraseRecorder() {
      if (!stream || !MediaRecorderClass) return false;
      if (recorder && recorder.state === 'recording') return true;
      recordedChunks = [];
      const mimeType = pickMimeType();
      try {
        recorder = new MediaRecorderClass(stream, { mimeType });
      } catch {
        try {
          recorder = new MediaRecorderClass(stream);
        } catch {
          return false;
        }
      }
      recorder.ondataavailable = (ev) => {
        if (ev && ev.data && (typeof ev.data.size !== 'number' || ev.data.size > 0)) {
          recordedChunks.push(ev.data);
        }
      };
      recorder.onstop = () => {
        const captured = recordedChunks.splice(0);
        const type = (recorder && recorder.mimeType) || mimeType;
        recorder = null;
        void sendAudioChunkToInteract(captured, type);
      };
      try {
        recorder.start();
        return true;
      } catch {
        return false;
      }
    }

    function stepVuMeterFrame() {
      rafId = null;
      if (destroyed || status !== 'active' || !stream) return;

      if (!processingAudio && !job && !busy && !suspended) {
        const rmsLevel = computeRmsLevel();
        currentRms = rmsLevel;
        const voiceDetected = rmsLevel > vadThreshold;
        updateSolarActivity(rmsLevel, voiceDetected);

        if (voiceDetected) {
          silenceElapsedMs = 0;
          if (!isSpeaking) {
            isSpeaking = true;
            startPhraseRecorder();
          }
          emit('listening', {
            rms: rmsLevel,
            isSpeaking: true,
            message: VOICE_DETECTED_MESSAGE,
          });
        } else if (isSpeaking) {
          silenceElapsedMs += frameIntervalMs;
          if (silenceElapsedMs >= vadSilenceMs) {
            isSpeaking = false;
            silenceElapsedMs = 0;
            updateSolarActivity(0, false);
            emit('processing', {
              rms: 0,
              isSpeaking: false,
              message: '⚡ Analyse vocale M.O.C.T.A.R en cours…',
            });
            if (recorder && recorder.state === 'recording') {
              try { recorder.stop(); } catch {}
            } else {
              processingAudio = false;
              reconcile();
            }
          }
        }
      } else {
        updateSolarActivity(0, false);
      }

      if (!destroyed && status === 'active' && stream) {
        rafId = requestFrame(stepVuMeterFrame);
      }
    }

    let pendingStartPromise = null;

    function startWebAudioCapture() {
      if (!wanted() || status !== 'idle') return pendingStartPromise || Promise.resolve();
      if (!hasWebAudioSupport) {
        emit('unsupported', { message: 'Microphone WebAudio indisponible sur ce navigateur.' });
        return Promise.resolve();
      }

      status = 'starting';
      const startGen = generation;
      emit('wake', {
        rms: 0,
        isSpeaking: false,
        message: WAKE_READY_MESSAGE,
      });

      pendingStartPromise = Promise.resolve()
        .then(() => mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true },
        }))
        .then((mediaStream) => {
          if (destroyed || startGen !== generation || !wanted()) {
            if (mediaStream && typeof mediaStream.getTracks === 'function') {
              mediaStream.getTracks().forEach((track) => track.stop());
            }
            status = 'idle';
            return;
          }
          stream = mediaStream;
          if (AudioContextClass) {
            audioCtx = new AudioContextClass();
            if (audioCtx.state === 'suspended' && typeof audioCtx.resume === 'function') {
              Promise.resolve(audioCtx.resume()).catch(() => {});
            }
            sourceNode = audioCtx.createMediaStreamSource(mediaStream);
            analyser = audioCtx.createAnalyser();
            analyser.fftSize = 512;
            analyser.smoothingTimeConstant = 0.2;
            sourceNode.connect(analyser);
            timeDomainData = new Uint8Array(analyser.fftSize || 512);
          }
          status = 'active';
          emit(phase, {
            rms: 0,
            isSpeaking: false,
            message: phase === 'wake' ? WAKE_READY_MESSAGE : '🟢 Je vous écoute…',
          });
          rafId = requestFrame(stepVuMeterFrame);
        })
        .catch((err) => {
          if (destroyed || startGen !== generation) return;
          status = 'idle';
          const isDenied = err && (
            err.name === 'NotAllowedError' ||
            err.name === 'PermissionDeniedError' ||
            err.name === 'SecurityError' ||
            err.error === 'not-allowed'
          );
          fail(
            isDenied ? 'not-allowed' : 'audio-capture',
            isDenied
              ? 'Autorisez le microphone dans le navigateur, puis réactivez l’écoute.'
              : 'Impossible d’accéder au microphone local.'
          );
        })
        .finally(() => {
          pendingStartPromise = null;
        });
      return pendingStartPromise;
    }

    function reconcile() {
      if (destroyed) return Promise.resolve();
      if (!wanted()) {
        if (status !== 'idle' && !job) {
          cleanupWebAudio();
        }
        if (fault) emit('error', fault);
        else if (!enabled && !manual) emit('off');
        else if (suspended || busy) {
          emit('paused', {
            message: suspended ? 'Écoute suspendue : revenez sur le dashboard.' : 'Réponse en cours…',
          });
        }
        return Promise.resolve();
      }
      if (status === 'idle') {
        return startWebAudioCapture();
      } else if (status === 'active' && !isSpeaking && !processingAudio) {
        emit(phase, {
          rms: currentRms,
          isSpeaking: false,
          message: phase === 'wake' ? WAKE_READY_MESSAGE : '🟢 Je vous écoute…',
        });
      }
      return pendingStartPromise || Promise.resolve();
    }

    function available() {
      if (destroyed) return false;
      if (!hasWebAudioSupport) {
        emit('unsupported', { message: 'Microphone WebAudio indisponible sur ce navigateur.' });
        return false;
      }
      return true;
    }

    return {
      get enabled() { return enabled; },
      get listeningDesired() { return enabled; },
      get usingWebAudio() { return true; },
      get state() { return state; },
      get rms() { return currentRms; },
      get isSpeaking() { return isSpeaking; },
      async enable() {
        if (!available()) return false;
        generation += 1;
        fault = null;
        enabled = true;
        manual = false;
        phase = 'wake';
        clearFollowup();
        await reconcile();
        return enabled && !fault;
      },
      disable() {
        generation += 1;
        enabled = false;
        manual = false;
        phase = 'wake';
        fault = null;
        if (activeAudioPlayer) {
          try { activeAudioPlayer.pause?.(); } catch {}
          activeAudioPlayer = null;
        }
        clearFollowup();
        cleanupWebAudio();
        emit('off');
      },
      async startManual() {
        if (!available()) return false;
        generation += 1;
        fault = null;
        manual = true;
        clearFollowup();
        openFollowup();
        await reconcile();
        return manual && !fault;
      },
      stopManual() {
        generation += 1;
        manual = false;
        phase = 'wake';
        clearFollowup();
        reconcile();
      },
      setBusy(value) {
        busy = Boolean(value);
        if (busy) {
          clearFollowup();
        } else if (phase === 'listening' && !job && (enabled || manual)) {
          openFollowup();
        }
        reconcile();
      },
      suspend() {
        generation += 1;
        suspended = true;
        manual = false;
        phase = 'wake';
        clearFollowup();
        cleanupWebAudio();
        reconcile();
      },
      resume() {
        suspended = false;
        reconcile();
      },
      destroy() {
        generation += 1;
        enabled = false;
        manual = false;
        if (activeAudioPlayer) {
          try { activeAudioPlayer.pause?.(); } catch {}
          activeAudioPlayer = null;
        }
        clearFollowup();
        cleanupWebAudio();
        destroyed = true;
        emit('off');
      },
    };
  }

  return { create };
});

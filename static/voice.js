(function (root, factory) {
  'use strict';
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MoctarVoice = api;
})(typeof window === 'object' ? window : (typeof globalThis === 'object' ? globalThis : null), function (root) {
  'use strict';

  const DEFAULT_WAKE_WORDS = [
    'yo moctar', 'hey moctar', 'allo moctar', 'dis moctar', 'ok moctar', 'salut moctar',
    'moctar', 'moktar', 'mokta', 'mokhtar', 'mouctar', 'mouktar', 'motard',
    "m'occupe tard", 'moccupe tard', "m'occaque", 'moccaque', 'jarvis',
    'yo elmo', 'hey elmo', 'hey el mo', 'el mo', 'elmo',
  ];
  const FOLLOWUP_MS = 45000;
  const GREETING = 'À vos ordres Moctar, je vous écoute.';
  const WAKE_READY_MESSAGE = "🟢 À l'écoute — Dites « Yo Moctar »";
  const VOICE_DETECTED_MESSAGE = '🎙️ Moctar vous écoute...';
  const THINKING_MESSAGE = '⚡ M.O.C.T.A.R calcule la réponse...';

  // Micro-WAV PCM 8kHz mono 16-bit silencieux valide (46 octets) pour déverrouiller l'autoplay sur Brave / iOS / Chrome
  const SILENT_WAV_DATA_URI =
    'data:audio/wav;base64,UklGRiYAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQIAAAAAAA==';

  const normalize = (value) => String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const words = (text) => Array.from(String(text).matchAll(/[\p{L}\p{N}]+/gu), (match) => ({
    value: normalize(match[0]), index: match.index, end: match.index + match[0].length,
  }));
  const phrase = (text) => words(text).map((word) => word.value).join(' ');
  const STOP_PHRASES = new Set(['merci moctar', 'merci elmo', 'tu peux te reposer', 'fin de conversation']);

  function create(options) {
    const opts = options || {};
    const win = opts.window || (typeof window === 'object' && window ? window : root) || {};
    const clock = opts.timers || {};
    const delay = clock.setTimeout ? clock.setTimeout.bind(clock) : setTimeout;
    const cancel = clock.clearTimeout ? clock.clearTimeout.bind(clock) : clearTimeout;

    // 60 FPS frame scheduler
    const requestFrame = typeof opts.requestAnimationFrame === 'function'
      ? opts.requestAnimationFrame
      : (clock.setTimeout
        ? (cb) => delay(() => cb(Date.now()), 16)
        : (typeof win.requestAnimationFrame === 'function'
          ? win.requestAnimationFrame.bind(win)
          : (cb) => setTimeout(() => cb(Date.now()), 16)));
    const cancelFrame = typeof opts.cancelAnimationFrame === 'function'
      ? opts.cancelAnimationFrame
      : (clock.clearTimeout
        ? (id) => cancel(id)
        : (typeof win.cancelAnimationFrame === 'function'
          ? win.cancelAnimationFrame.bind(win)
          : (id) => clearTimeout(id)));

    const onState = typeof opts.onState === 'function' ? opts.onState : () => {};
    const onPrompt = typeof opts.onPrompt === 'function' ? opts.onPrompt : async () => {};
    const speak = typeof opts.speak === 'function' ? opts.speak : null;
    const onInteractResult = typeof opts.onInteractResult === 'function' ? opts.onInteractResult : null;

    const aliases = (opts.wakeWords || DEFAULT_WAKE_WORDS).map((item) => words(item).map((word) => word.value))
      .filter((item) => item.length).sort((a, b) => b.length - a.length);

    const mediaDevices = opts.mediaDevices || (win.navigator ? win.navigator.mediaDevices : null);
    const MediaRecorderClass = opts.MediaRecorder || win.MediaRecorder || null;
    const AudioContextClass = opts.AudioContext || win.AudioContext || win.webkitAudioContext || null;
    const AudioPlayerClass = opts.Audio || (typeof win.Audio === 'function' ? win.Audio : (typeof Audio === 'function' ? Audio : null));
    const fetchImpl = opts.fetch || (typeof win.fetch === 'function' ? win.fetch.bind(win) : (typeof fetch === 'function' ? fetch : null));
    const interactUrl = opts.interactUrl || '/api/voice/interact';
    const vadSilenceMs = typeof opts.vadSilenceMs === 'number' ? opts.vadSilenceMs : 500;
    const vadThreshold = typeof opts.vadThreshold === 'number' ? opts.vadThreshold : 0.04;
    const maxSpeechMs = typeof opts.maxSpeechMs === 'number' ? opts.maxSpeechMs : 3500;
    const frameIntervalMs = typeof opts.frameIntervalMs === 'number' ? opts.frameIntervalMs : (1000 / 60);
    const highpassFrequencyHz = typeof opts.highpassFrequencyHz === 'number' ? opts.highpassFrequencyHz : 120;

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
    let highpassFilter = null;
    let analyser = null;
    let timeDomainData = null;
    let recorder = null;
    let recordedChunks = [];
    let rafId = null;
    let followupTimer = null;
    let maxSpeechTimer = null;
    let isSpeaking = false;
    let silenceElapsedMs = 0;
    let currentRms = 0;
    let processingAudio = false;
    let pendingStartPromise = null;

    function resolveSolarEngine() {
      if (opts.SolarEngine) return opts.SolarEngine;
      if (win) {
        return win.SolarEngine || (win.CosmosEngine && win.CosmosEngine.SolarEngine) || null;
      }
      return null;
    }

    function updateSolarActivity(rmsLevel, speakingFlag) {
      const solar = resolveSolarEngine();
      if (solar && typeof solar.setVoiceActivity === 'function') {
        solar.setVoiceActivity(rmsLevel, speakingFlag);
      }
    }

    function updateSolarVoiceState(voiceState) {
      const solar = resolveSolarEngine();
      if (solar && typeof solar.setVoiceState === 'function') {
        solar.setVoiceState(voiceState);
      }
    }

    /**
     * Bip sonore synthétique discret de confirmation de fin de prise de parole (880Hz -> 1320Hz sur 90 ms).
     */
    function playEndSpeechChime() {
      try {
        const ctx = audioCtx || (AudioContextClass ? new AudioContextClass() : null);
        if (!ctx) return;
        if (ctx.state === 'suspended' && typeof ctx.resume === 'function') {
          ctx.resume().catch(() => {});
        }
        const now = ctx.currentTime;
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, now);
        osc.frequency.exponentialRampToValueAtTime(1320, now + 0.09);

        gain.gain.setValueAtTime(0.08, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.09);

        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.start(now);
        osc.stop(now + 0.095);
      } catch {
        // Fallback muet sécurisé si AudioContext bloqué ou indisponible
      }
    }

    /**
     * Déverrouillage Audio Brave : instancie et préchauffe window.MoctarSpeaker
     * lors du geste utilisateur initial sur le bouton micro.
     */
    function ensureMoctarSpeakerUnlocked(warmup = false) {
      if (!AudioPlayerClass) return null;
      let speaker = win.MoctarSpeaker || null;
      if (!speaker) {
        try {
          speaker = new AudioPlayerClass(SILENT_WAV_DATA_URI);
          speaker.preload = 'auto';
          win.MoctarSpeaker = speaker;
        } catch {
          return null;
        }
      }
      if (warmup) {
        try {
          speaker.src = SILENT_WAV_DATA_URI;
          speaker.volume = 0.01;
          const p = speaker.play();
          if (p && typeof p.then === 'function') {
            p.then(() => {
              try {
                speaker.pause?.();
                speaker.currentTime = 0;
              } catch {}
              speaker.volume = 1.0;
            }).catch(() => {
              speaker.volume = 1.0;
            });
          } else {
            speaker.volume = 1.0;
          }
        } catch {
          speaker.volume = 1.0;
        }
      }
      return speaker;
    }

    /**
     * Fallback vocal de secours via window.speechSynthesis avec voix française masculine
     * si la lecture MP3 échoue ou est bloquée.
     */
    function speakWithNativeSpeechSynthesis(text) {
      const cleanText = String(text || '').trim();
      if (!cleanText) return Promise.resolve(false);
      if (speak) {
        return Promise.resolve()
          .then(() => speak(cleanText))
          .then((ok) => {
            if (ok !== false) return true;
            return runBrowserSpeechSynthesis(cleanText);
          })
          .catch(() => runBrowserSpeechSynthesis(cleanText));
      }
      return runBrowserSpeechSynthesis(cleanText);
    }

    function runBrowserSpeechSynthesis(cleanText) {
      const synth = opts.speechSynthesis || win.speechSynthesis || null;
      const UtteranceClass = opts.SpeechSynthesisUtterance || win.SpeechSynthesisUtterance || (typeof SpeechSynthesisUtterance === 'function' ? SpeechSynthesisUtterance : null);
      if (!synth || typeof synth.speak !== 'function' || !UtteranceClass) {
        return Promise.resolve(false);
      }
      return new Promise((resolve) => {
        let settled = false;
        let safetyTimer = null;
        const finish = (ok) => {
          if (settled) return;
          settled = true;
          if (safetyTimer !== null) cancel(safetyTimer);
          resolve(ok);
        };
        try {
          synth.cancel?.();
          const utterance = new UtteranceClass(cleanText);
          utterance.lang = 'fr-FR';
          utterance.rate = 1.02;
          utterance.pitch = 0.94;
          const voices = typeof synth.getVoices === 'function' ? (synth.getVoices() || []) : [];
          const frVoices = voices.filter((v) => /^fr\b/i.test(v.lang || ''));
          const maleFrVoice = frVoices.find((v) => /henri|remy|paul|thomas|antoine|nicolas|male|homme/i.test(v.name || '')) || frVoices[0];
          if (maleFrVoice) utterance.voice = maleFrVoice;
          utterance.onend = () => finish(true);
          utterance.onerror = () => finish(false);
          safetyTimer = delay(() => finish(true), 8000);
          synth.speak(utterance);
        } catch {
          finish(false);
        }
      });
    }

    function playAudioUrl(audioUrl) {
      if (!audioUrl || !AudioPlayerClass) return Promise.resolve(false);
      return new Promise((resolve) => {
        let settled = false;
        const speaker = ensureMoctarSpeakerUnlocked(false);
        if (!speaker) {
          resolve(false);
          return;
        }
        const finish = (ok) => {
          if (settled) return;
          settled = true;
          speaker.onended = null;
          speaker.onerror = null;
          resolve(ok);
        };
        speaker.onended = () => finish(true);
        speaker.onerror = () => finish(false);
        try {
          speaker.pause?.();
          speaker.volume = 1.0;
          speaker.src = audioUrl;
          speaker.url = audioUrl;
          speaker.load?.();
          const playPromise = speaker.play();
          if (playPromise && typeof playPromise.then === 'function') {
            playPromise.catch(() => finish(false));
          }
        } catch {
          finish(false);
        }
      });
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

    function clearMaxSpeechTimer() {
      if (maxSpeechTimer !== null) {
        cancel(maxSpeechTimer);
        maxSpeechTimer = null;
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
      clearMaxSpeechTimer();
      isSpeaking = false;
      silenceElapsedMs = 0;
      currentRms = 0;
      updateSolarActivity(0, false);
      updateSolarVoiceState('idle');

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
      if (highpassFilter) {
        try { highpassFilter.disconnect?.(); } catch {}
        highpassFilter = null;
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

    function findWake(text) {
      const tokens = words(text);
      for (let startIdx = 0; startIdx < tokens.length; startIdx += 1) {
        for (const alias of aliases) {
          if (alias.every((word, offset) => tokens[startIdx + offset] && tokens[startIdx + offset].value === word)) {
            const matchStart = tokens[startIdx].index;
            const matchEnd = tokens[startIdx + alias.length - 1].end;
            const before = text.slice(0, matchStart).replace(/[\s,.:;!?…—–-]+$/, '').trim();
            const after = text.slice(matchEnd).replace(/^[\s,.:;!?…—–-]+/, '').trim();
            const combined = `${before} ${after}`.trim();
            return { command: combined };
          }
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
        message: isGreetingOnly ? `🔊 Moctar : "${GREETING}"` : THINKING_MESSAGE,
      });
      try {
        if (isGreetingOnly) {
          await speakWithNativeSpeechSynthesis(GREETING);
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
      // Réinitialisation immédiate du niveau RMS du Soleil dès l'envoi pour éviter toute dilatation bloquée
      currentRms = 0;
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
          updateSolarVoiceState('idle');
          if (wanted()) {
            emit(phase, {
              rms: 0,
              isSpeaking: false,
              message: phase === 'wake' ? WAKE_READY_MESSAGE : WAKE_READY_MESSAGE,
            });
          }
          return;
        }

        const current = { generation };
        job = current;
        clearFollowup();
        try {
          onInteractResult?.(payload);
          const replyText = String(payload.reply_text || '').trim();
          updateSolarVoiceState('speaking');
          emit('speaking', {
            transcript: payload.user_text || '',
            reply: replyText,
            audioUrl: payload.audio_url || null,
            message: `🔊 Moctar : "${replyText}"`,
          });
          let played = false;
          if (payload.audio_url) {
            played = await playAudioUrl(payload.audio_url);
          }
          if (!played && replyText) {
            await speakWithNativeSpeechSynthesis(replyText);
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
        updateSolarVoiceState('idle');
        if (wanted()) {
          emit(phase, {
            rms: 0,
            isSpeaking: false,
            message: WAKE_READY_MESSAGE,
          });
        }
      } finally {
        processingAudio = false;
      }
    }

    function stopPhraseAndSend() {
      clearMaxSpeechTimer();
      isSpeaking = false;
      silenceElapsedMs = 0;
      currentRms = 0;
      updateSolarActivity(0, false);
      updateSolarVoiceState('thinking');
      playEndSpeechChime();
      emit('processing', {
        rms: 0,
        isSpeaking: false,
        message: THINKING_MESSAGE,
      });
      if (recorder && recorder.state === 'recording') {
        try {
          recorder.stop();
        } catch {
          processingAudio = false;
          reconcile();
        }
      } else {
        processingAudio = false;
        reconcile();
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
        clearMaxSpeechTimer();
        updateSolarVoiceState('thinking');
        const captured = recordedChunks.splice(0);
        const type = (recorder && recorder.mimeType) || mimeType;
        recorder = null;
        void sendAudioChunkToInteract(captured, type);
      };
      try {
        recorder.start();
        clearMaxSpeechTimer();
        maxSpeechTimer = delay(() => {
          maxSpeechTimer = null;
          if (isSpeaking && recorder && recorder.state === 'recording') {
            stopPhraseAndSend();
          }
        }, maxSpeechMs);
        return true;
      } catch {
        clearMaxSpeechTimer();
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
            stopPhraseAndSend();
          }
        }
      } else {
        updateSolarActivity(0, false);
      }

      if (!destroyed && status === 'active' && stream) {
        rafId = requestFrame(stepVuMeterFrame);
      }
    }

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

            // Filtre passe-haut BiquadFilterNode (120 Hz) pour éliminer le souffle basse fréquence du ventilateur HP
            if (typeof audioCtx.createBiquadFilter === 'function') {
              highpassFilter = audioCtx.createBiquadFilter();
              highpassFilter.type = 'highpass';
              if (highpassFilter.frequency && typeof highpassFilter.frequency.value === 'number') {
                highpassFilter.frequency.value = highpassFrequencyHz;
              } else {
                highpassFilter.frequency = { value: highpassFrequencyHz };
              }
              sourceNode.connect(highpassFilter);
              highpassFilter.connect(analyser);
            } else {
              sourceNode.connect(analyser);
            }

            timeDomainData = new Uint8Array(analyser.fftSize || 512);
          }
          status = 'active';
          emit(phase, {
            rms: 0,
            isSpeaking: false,
            message: WAKE_READY_MESSAGE,
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
          message: WAKE_READY_MESSAGE,
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
        ensureMoctarSpeakerUnlocked(true);
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
        if (win.MoctarSpeaker) {
          try { win.MoctarSpeaker.pause?.(); } catch {}
        }
        clearFollowup();
        cleanupWebAudio();
        emit('off');
      },
      async startManual() {
        if (!available()) return false;
        ensureMoctarSpeakerUnlocked(true);
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
        if (win.MoctarSpeaker) {
          try { win.MoctarSpeaker.pause?.(); } catch {}
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

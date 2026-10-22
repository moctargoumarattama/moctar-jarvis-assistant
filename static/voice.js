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
  const DEFAULT_FOLLOWUP_MS = 0;
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
    const FOLLOWUP_MS = typeof opts.followupMs === 'number' ? opts.followupMs : DEFAULT_FOLLOWUP_MS;
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
    const vadSilenceMs = typeof opts.vadSilenceMs === 'number' ? opts.vadSilenceMs : 900;
    const vadThreshold = typeof opts.vadThreshold === 'number' ? opts.vadThreshold : 0.035;
    const minRecordingMs = typeof opts.minRecordingMs === 'number' ? opts.minRecordingMs : 1200;
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
    let isPushToTalk = false;
    let silenceElapsedMs = 0;
    let recordingElapsedMs = 0;
    let phraseStartTime = 0;
    let currentRms = 0;
    let processingAudio = false;
    let pendingStartPromise = null;

    const TARGET_SAMPLE_RATE = 16000;
    const preRollMs = typeof opts.preRollMs === 'number' ? opts.preRollMs : 1000;
    const PRE_ROLL_SAMPLES = Math.floor(TARGET_SAMPLE_RATE * (preRollMs / 1000)); // 1000 ms = 16000 échantillons à 16 kHz
    const preRollBuffer = new Float32Array(PRE_ROLL_SAMPLES);
    let preRollWriteIndex = 0;
    let preRollCount = 0;
    let activePcmChunks = [];
    let scriptNode = null;
    let silentGainNode = null;
    let isAssistantSpeaking = false;
    let echoDampeningTimer = null;
    let activeAudioPlaybackFinish = null;
    let userBargedIn = false;
    let bargeInStreak = 0;

    function triggerBargeIn() {
      userBargedIn = true;
      isAssistantSpeaking = false;
      bargeInStreak = 0;
      if (activeAudioPlaybackFinish) {
        const finish = activeAudioPlaybackFinish;
        activeAudioPlaybackFinish = null;
        finish(false);
      }
      const speaker = win.MoctarSpeaker || null;
      if (speaker) {
        try {
          speaker.pause?.();
          speaker.currentTime = 0;
        } catch {}
      }
      if (typeof win.speechSynthesis !== 'undefined' && win.speechSynthesis.speaking) {
        try { win.speechSynthesis.cancel(); } catch {}
      }
      if (echoDampeningTimer !== null) {
        cancel(echoDampeningTimer);
        echoDampeningTimer = null;
      }
      clearPreRollBuffer();
      isSpeaking = true;
      silenceElapsedMs = 0;
      recordingElapsedMs = 0;
      phraseStartTime = getNow();
      startPhraseRecorder();
      emit('listening', {
        rms: currentRms,
        isSpeaking: true,
        message: '🎤 Moctar : écoute prioritaire (interruption)...',
      });
    }

    function clearPreRollBuffer() {
      preRollWriteIndex = 0;
      preRollCount = 0;
      preRollBuffer.fill(0);
    }

    function releaseEchoLock() {
      if (echoDampeningTimer !== null) {
        cancel(echoDampeningTimer);
        echoDampeningTimer = null;
      }
      clearPreRollBuffer();
      echoDampeningTimer = delay(() => {
        echoDampeningTimer = null;
        clearPreRollBuffer();
        isAssistantSpeaking = false;
      }, 450);
    }

    function downsampleTo16k(input, sourceRate) {
      if (!input || !input.length) return new Float32Array(0);
      if (sourceRate === TARGET_SAMPLE_RATE) return new Float32Array(input);
      const ratio = sourceRate / TARGET_SAMPLE_RATE;
      const newLength = Math.round(input.length / ratio);
      const result = new Float32Array(newLength);
      let offsetResult = 0;
      let offsetBuffer = 0;
      while (offsetResult < result.length) {
        const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
        let accum = 0;
        let count = 0;
        for (let i = offsetBuffer; i < nextOffsetBuffer && i < input.length; i++) {
          accum += input[i];
          count++;
        }
        result[offsetResult] = count > 0 ? accum / count : 0;
        offsetResult++;
        offsetBuffer = nextOffsetBuffer;
      }
      return result;
    }

    function encodeWavPcm16(samples, sampleRate) {
      const buffer = new ArrayBuffer(44 + samples.length * 2);
      const view = new DataView(buffer);

      function writeString(offset, string) {
        for (let i = 0; i < string.length; i++) {
          view.setUint8(offset + i, string.charCodeAt(i));
        }
      }

      writeString(0, 'RIFF');
      view.setUint32(4, 36 + samples.length * 2, true);
      writeString(8, 'WAVE');
      writeString(12, 'fmt ');
      view.setUint32(16, 16, true);
      view.setUint16(20, 1, true); // PCM format = 1
      view.setUint16(22, 1, true); // mono
      view.setUint32(24, sampleRate, true);
      view.setUint32(28, sampleRate * 2, true); // byte rate (sampleRate * 1 * 2)
      view.setUint16(32, 2, true); // block align (1 * 2)
      view.setUint16(34, 16, true); // 16-bit
      writeString(36, 'data');
      view.setUint32(40, samples.length * 2, true);

      let offset = 44;
      for (let i = 0; i < samples.length; i++, offset += 2) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
      }

      return (typeof Blob === 'function')
        ? new Blob([buffer], { type: 'audio/wav' })
        : { size: buffer.byteLength, type: 'audio/wav', buffer };
    }

    function handleAudioProcess(event) {
      if (destroyed || suspended || status !== 'active') return;
      if (isAssistantSpeaking && !isSpeaking) {
        clearPreRollBuffer();
        return;
      }
      const input = event.inputBuffer.getChannelData(0);
      const srcRate = event.inputBuffer.sampleRate || (audioCtx && audioCtx.sampleRate) || 48000;
      const pcm16k = downsampleTo16k(input, srcRate);

      if (!isSpeaking) {
        // Écoute passive : enregistre en continu dans le tampon circulaire pré-roll (1000 ms)
        for (let i = 0; i < pcm16k.length; i++) {
          preRollBuffer[preRollWriteIndex] = pcm16k[i];
          preRollWriteIndex = (preRollWriteIndex + 1) % PRE_ROLL_SAMPLES;
          if (preRollCount < PRE_ROLL_SAMPLES) preRollCount++;
        }
      } else {
        // Parole active : accumule les échantillons PCM 16kHz
        activePcmChunks.push(pcm16k);
      }
    }

    function getNow() {
      if (clock && typeof clock.now === 'function') return clock.now();
      if (clock && typeof clock.now === 'number') return clock.now;
      if (typeof opts.now === 'function') return opts.now();
      if (typeof win.performance === 'object' && win.performance && typeof win.performance.now === 'function') {
        return win.performance.now();
      }
      return Date.now();
    }

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
     * Bip sonore synthétique discret de début de prise de parole (520Hz -> 780Hz sur 60 ms).
     */
    function playStartSpeechChime() {
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
        osc.frequency.setValueAtTime(520, now);
        osc.frequency.exponentialRampToValueAtTime(780, now + 0.06);

        gain.gain.setValueAtTime(0.04, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.06);

        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.start(now);
        osc.stop(now + 0.065);
      } catch {
        // Fallback muet sécurisé si AudioContext bloqué ou indisponible
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
          activeAudioPlaybackFinish = null;
          speaker.onended = null;
          speaker.onerror = null;
          resolve(ok);
        };
        activeAudioPlaybackFinish = finish;
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
      if (FOLLOWUP_MS <= 0) {
        phase = 'wake';
        manual = false;
        clearFollowup();
        reconcile();
        return;
      }
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
      let mimeType = 'audio/webm;codecs=opus';
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
      isPushToTalk = false;
      silenceElapsedMs = 0;
      recordingElapsedMs = 0;
      phraseStartTime = 0;
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

      if (scriptNode) {
        try {
          scriptNode.onaudioprocess = null;
          scriptNode.disconnect?.();
        } catch {}
        scriptNode = null;
      }
      if (silentGainNode) {
        try { silentGainNode.disconnect?.(); } catch {}
        silentGainNode = null;
      }
      if (echoDampeningTimer !== null) {
        cancel(echoDampeningTimer);
        echoDampeningTimer = null;
      }
      isAssistantSpeaking = false;
      clearPreRollBuffer();
      activePcmChunks = [];

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
      if (isGreetingOnly) {
        isAssistantSpeaking = true;
        clearPreRollBuffer();
      }
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
        if (isGreetingOnly) {
          releaseEchoLock();
        }
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
      isAssistantSpeaking = true;
      if (echoDampeningTimer !== null) {
        cancel(echoDampeningTimer);
        echoDampeningTimer = null;
      }
      clearPreRollBuffer();
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

        const isBargeInSend = userBargedIn;
        userBargedIn = false;
        const response = await fetchImpl(interactUrl, {
          method: 'POST',
          headers: {
            'Content-Type': (blob && blob.type) || mimeType || 'audio/wav',
            'X-Voice-Session-Active': (phase === 'listening' || manual || isBargeInSend) ? 'true' : 'false',
            'X-Voice-Barge-In': isBargeInSend ? 'true' : 'false',
          },
          body: blob,
        });
        if (!response || !response.ok) {
          releaseEchoLock();
          if (FOLLOWUP_MS <= 0) {
            phase = 'wake';
            manual = false;
          }
          return;
        }
        const payload = await response.json();
        if (!payload || destroyed || suspended) {
          releaseEchoLock();
          if (FOLLOWUP_MS <= 0) {
            phase = 'wake';
            manual = false;
          }
          return;
        }

        if (typeof payload.text === 'string' && payload.reply_text === undefined) {
          await handleLocalTextTranscript(payload.text);
          return;
        }

        if (payload.ignored || !payload.reply_text) {
          releaseEchoLock();
          if (FOLLOWUP_MS <= 0) {
            phase = 'wake';
            manual = false;
          }
          updateSolarVoiceState('idle');
          if (wanted()) {
            emit(phase, {
              rms: 0,
              isSpeaking: false,
              message: phase === 'listening' ? VOICE_DETECTED_MESSAGE : WAKE_READY_MESSAGE,
            });
          }
          return;
        }

        processingAudio = false;
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
          const chunksToPlay = (Array.isArray(payload.audio_chunks) && payload.audio_chunks.length > 0)
            ? payload.audio_chunks
            : (payload.first_chunk_audio_url ? [payload.first_chunk_audio_url] : (payload.audio_url ? [payload.audio_url] : []));

          if (chunksToPlay.length > 1) {
            for (const chunkUrl of chunksToPlay) {
              if (generation !== current.generation || destroyed || suspended || userBargedIn) break;
              const ok = await playAudioUrl(chunkUrl);
              if (userBargedIn) break;
              played = ok || played;
            }
          } else if (chunksToPlay.length === 1 && !userBargedIn) {
            played = await playAudioUrl(chunksToPlay[0]);
          } else if (payload.audio_url && !userBargedIn) {
            played = await playAudioUrl(payload.audio_url);
          }
          if (!played && replyText && !userBargedIn) {
            await speakWithNativeSpeechSynthesis(replyText);
          }
          releaseEchoLock();
          if (generation !== current.generation || destroyed || suspended) return;
          openFollowup();
        } finally {
          if (job === current) job = null;
          processingAudio = false;
          reconcile();
        }
      } catch {
        releaseEchoLock();
        processingAudio = false;
        if (FOLLOWUP_MS <= 0) {
          phase = 'wake';
          manual = false;
        }
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
      recordingElapsedMs = 0;
      phraseStartTime = 0;
      currentRms = 0;
      updateSolarActivity(0, false);
      updateSolarVoiceState('thinking');
      playEndSpeechChime();
      emit('processing', {
        rms: 0,
        isSpeaking: false,
        message: THINKING_MESSAGE,
      });

      if (activePcmChunks.length > 0) {
        const chunks = activePcmChunks.splice(0);
        if (recorder && recorder.state === 'recording') {
          try {
            recorder.ondataavailable = null;
            recorder.onstop = null;
            recorder.stop();
          } catch {}
          recorder = null;
        }
        let totalLength = 0;
        for (let i = 0; i < chunks.length; i++) {
          totalLength += chunks[i].length;
        }
        const merged = new Float32Array(totalLength);
        let offset = 0;
        for (let i = 0; i < chunks.length; i++) {
          merged.set(chunks[i], offset);
          offset += chunks[i].length;
        }
        const wavBlob = encodeWavPcm16(merged, TARGET_SAMPLE_RATE);
        void sendAudioChunkToInteract([wavBlob], 'audio/wav');
        return;
      }

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
      if (!stream) return false;
      if (recorder && recorder.state === 'recording') return true;
      phraseStartTime = getNow();
      recordingElapsedMs = 0;
      updateSolarVoiceState('listening');
      playStartSpeechChime();

      // Capture le tampon circulaire pré-roll de 800 ms dans l'ordre chronologique
      if (preRollCount > 0) {
        const preRollChunk = new Float32Array(preRollCount);
        if (preRollCount < PRE_ROLL_SAMPLES) {
          preRollChunk.set(preRollBuffer.subarray(0, preRollCount));
        } else {
          const part1 = preRollBuffer.subarray(preRollWriteIndex, PRE_ROLL_SAMPLES);
          const part2 = preRollBuffer.subarray(0, preRollWriteIndex);
          preRollChunk.set(part1, 0);
          preRollChunk.set(part2, part1.length);
        }
        activePcmChunks = [preRollChunk];
      } else {
        activePcmChunks = [];
      }

      recordedChunks = [];
      const mimeType = pickMimeType();
      if (MediaRecorderClass) {
        try {
          recorder = new MediaRecorderClass(stream, { mimeType, audioBitsPerSecond: 128000 });
        } catch {
          try {
            recorder = new MediaRecorderClass(stream, { mimeType });
          } catch {
            try {
              recorder = new MediaRecorderClass(stream);
            } catch {
              recorder = null;
            }
          }
        }
      }
      if (recorder) {
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
        } catch {
          recorder = null;
        }
      }
      clearMaxSpeechTimer();
      maxSpeechTimer = delay(() => {
        maxSpeechTimer = null;
        if (isSpeaking) {
          stopPhraseAndSend();
        }
      }, isPushToTalk ? 15000 : maxSpeechMs);
      return Boolean(recorder || scriptNode);
    }

    function stepVuMeterFrame(timestamp) {
      rafId = null;
      if (destroyed || status !== 'active' || !stream) return;

      if (!processingAudio && !isAssistantSpeaking && !job && !busy && !suspended) {
        const rmsLevel = computeRmsLevel();
        currentRms = rmsLevel;

        if (isPushToTalk) {
          recordingElapsedMs += frameIntervalMs;
          updateSolarActivity(Math.max(rmsLevel, 0.08), true);
          emit('listening', {
            rms: rmsLevel,
            isSpeaking: true,
            message: VOICE_DETECTED_MESSAGE,
          });
        } else {
          const voiceDetected = rmsLevel > vadThreshold;
          updateSolarActivity(rmsLevel, voiceDetected);

          if (voiceDetected) {
            silenceElapsedMs = 0;
            if (!isSpeaking) {
              isSpeaking = true;
              startPhraseRecorder();
            }
            recordingElapsedMs += frameIntervalMs;
            emit('listening', {
              rms: rmsLevel,
              isSpeaking: true,
              message: VOICE_DETECTED_MESSAGE,
            });
          } else if (isSpeaking) {
            silenceElapsedMs += frameIntervalMs;
            recordingElapsedMs += frameIntervalMs;
            const currentNow = (typeof timestamp === 'number' && timestamp > 0) ? timestamp : getNow();
            const elapsed = Math.max(recordingElapsedMs, phraseStartTime > 0 ? (currentNow - phraseStartTime) : 0);
            if (silenceElapsedMs >= vadSilenceMs && elapsed >= minRecordingMs) {
              stopPhraseAndSend();
            }
          }
        }
      } else if (isAssistantSpeaking && !processingAudio) {
        const rmsLevel = computeRmsLevel();
        currentRms = rmsLevel;
        // Détection Barge-in : l'utilisateur parle par-dessus la voix de M.O.C.T.A.R
        const bargeInThreshold = Math.max(vadThreshold * 1.6, 0.05);
        if (rmsLevel > bargeInThreshold) {
          bargeInStreak++;
          if (bargeInStreak >= 2) {
            triggerBargeIn();
          }
        } else {
          bargeInStreak = 0;
        }
        updateSolarActivity(Math.min(rmsLevel * 0.5, 0.1), false);
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
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            sampleRate: 48000,
          },
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

            // ScriptProcessorNode pour le tampon pré-roll 800ms et l'enregistrement WAV 16kHz
            if (typeof audioCtx.createScriptProcessor === 'function') {
              try {
                scriptNode = audioCtx.createScriptProcessor(2048, 1, 1);
                scriptNode.onaudioprocess = handleAudioProcess;
                const filterOrSource = highpassFilter || sourceNode;
                filterOrSource.connect(scriptNode);
                if (typeof audioCtx.createGain === 'function') {
                  silentGainNode = audioCtx.createGain();
                  silentGainNode.gain.value = 0.0;
                  scriptNode.connect(silentGainNode);
                  silentGainNode.connect(audioCtx.destination);
                } else if (audioCtx.destination) {
                  scriptNode.connect(audioCtx.destination);
                }
              } catch {
                scriptNode = null;
                silentGainNode = null;
              }
            }
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

    async function startPushToTalk() {
      if (!available()) return false;
      ensureMoctarSpeakerUnlocked(true);
      isPushToTalk = true;
      generation += 1;
      fault = null;
      manual = true;
      phase = 'listening';
      clearFollowup();

      if (status !== 'active' || !stream) {
        await startWebAudioCapture();
      }

      if (status === 'active' && stream) {
        isSpeaking = true;
        silenceElapsedMs = 0;
        startPhraseRecorder();
        updateSolarVoiceState('listening');
        emit('listening', {
          rms: 0.1,
          isSpeaking: true,
          message: VOICE_DETECTED_MESSAGE,
        });
        return true;
      }
      return false;
    }

    function stopPushToTalk(options = {}) {
      if (!isPushToTalk) return;
      isPushToTalk = false;
      if (options && options.discard) {
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
        activePcmChunks = [];
        preRollCount = 0;
        preRollWriteIndex = 0;
        isSpeaking = false;
        silenceElapsedMs = 0;
        recordingElapsedMs = 0;
        phraseStartTime = 0;
        currentRms = 0;
        updateSolarActivity(0, false);
        updateSolarVoiceState('idle');
        emit(phase, { rms: 0, isSpeaking: false, message: WAKE_READY_MESSAGE });
        return;
      }
      stopPhraseAndSend();
    }

    return {
      get enabled() { return enabled; },
      get listeningDesired() { return enabled; },
      get usingWebAudio() { return true; },
      get state() { return state; },
      get rms() { return currentRms; },
      get isSpeaking() { return isSpeaking; },
      get isPushToTalk() { return isPushToTalk; },
      get isAssistantSpeaking() { return isAssistantSpeaking; },
      get userBargedIn() { return userBargedIn; },
      triggerBargeIn,
      bargeIn: triggerBargeIn,
      startPushToTalk,
      stopPushToTalk,
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
        phase = 'listening';
        clearFollowup();
        if (FOLLOWUP_MS > 0) openFollowup();
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

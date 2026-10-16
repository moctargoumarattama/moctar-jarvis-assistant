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
  const STOP_TIMEOUT_MS = 2500;
  const GREETING = 'Oui Moctar, je t’écoute. Que souhaites-tu ?';
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
    const onState = typeof opts.onState === 'function' ? opts.onState : () => {};
    const onPrompt = typeof opts.onPrompt === 'function' ? opts.onPrompt : async () => {};
    const speak = typeof opts.speak === 'function' ? opts.speak : async () => false;
    const aliases = (opts.wakeWords || DEFAULT_WAKE_WORDS).map((item) => words(item).map((word) => word.value))
      .filter((item) => item.length).sort((a, b) => b.length - a.length);
    const mediaDevices = opts.mediaDevices || (typeof window === 'object' && window && window.navigator ? window.navigator.mediaDevices : null);
    const MediaRecorderClass = opts.MediaRecorder || (typeof window === 'object' && window ? window.MediaRecorder : null);
    const AudioContextClass = opts.AudioContext || (typeof window === 'object' && window ? (window.AudioContext || window.webkitAudioContext) : null);
    const AudioPlayerClass = opts.Audio || (typeof window === 'object' && window && typeof window.Audio === 'function' ? window.Audio : (typeof Audio === 'function' ? Audio : null));
    const fetchImpl = opts.fetch || (typeof window === 'object' && window && typeof window.fetch === 'function' ? window.fetch.bind(window) : null);
    const transcribeUrl = opts.transcribeUrl || '/api/voice/transcribe';
    const interactUrl = opts.interactUrl || '/api/voice/interact';
    const onInteractResult = typeof opts.onInteractResult === 'function' ? opts.onInteractResult : null;
    const braveWatchdogMs = typeof opts.braveWatchdogMs === 'number' ? opts.braveWatchdogMs : 3000;
    const vadSilenceMs = typeof opts.vadSilenceMs === 'number' ? opts.vadSilenceMs : 800;
    const vadPollMs = typeof opts.vadPollMs === 'number' ? opts.vadPollMs : 50;
    const vadThreshold = typeof opts.vadThreshold === 'number' ? opts.vadThreshold : 0.02;
    const isBraveBrowser = Boolean(
      opts.isBrave || (typeof window === 'object' && window && window.navigator && window.navigator.brave)
    );
    const hasRecorderFallback = Boolean(
      mediaDevices && typeof mediaDevices.getUserMedia === 'function' && MediaRecorderClass
    );
    const preferWebAudio = Boolean((opts.preferWebAudio || isBraveBrowser) && hasRecorderFallback);
    let recognition = null;
    let usingFallback = false;
    let enabled = false;
    let manual = false;
    let suspended = false;
    let destroyed = false;
    let busy = false;
    let phase = 'wake';
    let state = (opts.Recognition || hasRecorderFallback) ? 'off' : 'unsupported';
    let fault = null;
    let generation = 0;
    let job = null;
    let status = 'idle';
    let intentionalStop = false;
    let pendingRetry = null;
    let restartTimer = null;
    let followupTimer = null;
    let stopTimer = null;
    let braveTimer = null;
    let stuck = false;
    let activeAudioPlayer = null;
    const finalResults = new Set();
    const stoppedWaiters = [];

    function emit(next, detail) {
      state = next;
      onState(next, detail || {});
    }

    function clearTimer(name) {
      if (name === 'followup' && followupTimer !== null) {
        cancel(followupTimer);
        followupTimer = null;
      }
      if (name === 'restart' && restartTimer !== null) {
        cancel(restartTimer);
        restartTimer = null;
      }
      if (name === 'stop' && stopTimer !== null) {
        cancel(stopTimer);
        stopTimer = null;
      }
      if (name === 'brave' && braveTimer !== null) {
        cancel(braveTimer);
        braveTimer = null;
      }
    }

    function clearTimers() {
      ['followup', 'restart', 'brave'].forEach(clearTimer);
      pendingRetry = null;
    }

    function settleStopped() {
      stoppedWaiters.splice(0).forEach((resolve) => resolve());
    }

    function wanted() {
      return !destroyed && !suspended && !fault && !busy && !job && (enabled || manual);
    }

    function fail(error, message, disableListening = true) {
      fault = { error, message, recoverOnEnd: !disableListening };
      if (disableListening) {
        enabled = false;
        manual = false;
      }
      generation += 1;
      clearTimers();
      emit('error', fault);
      requestStop();
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

    async function transcribeRecordedBlob(blob) {
      if (typeof opts.transcribe === 'function') {
        return opts.transcribe(blob);
      }
      if (!fetchImpl) return '';
      const mime = (blob && blob.type) ? blob.type : 'audio/webm';
      const targetUrl = (AudioContextClass || opts.interactUrl) ? interactUrl : transcribeUrl;
      const response = await fetchImpl(targetUrl, {
        method: 'POST',
        headers: {
          'Content-Type': mime,
          'X-Voice-Session-Active': (phase === 'listening' || manual) ? 'true' : 'false',
        },
        body: blob,
      });
      if (!response || !response.ok) return '';
      const payload = await response.json();
      if (payload && typeof payload === 'object' && ('ignored' in payload || 'reply_text' in payload)) {
        return payload;
      }
      return String(payload && payload.text || '').trim();
    }

    function createRecorderRecognition() {
      let active = false;
      let stream = null;
      let recorder = null;
      let chunkTimer = null;
      let vadTimer = null;
      let audioCtx = null;
      let analyser = null;
      let sourceNode = null;
      let timeDomainData = null;
      let isSpeechActive = false;
      let silenceAccumMs = 0;
      let processingBlob = false;
      let resultIndex = 0;
      let chunks = [];
      const sliceMs = typeof opts.recorderSliceMs === 'number' ? opts.recorderSliceMs : 2600;

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

      function cleanupStream() {
        if (chunkTimer !== null) {
          cancel(chunkTimer);
          chunkTimer = null;
        }
        if (vadTimer !== null) {
          cancel(vadTimer);
          vadTimer = null;
        }
        isSpeechActive = false;
        silenceAccumMs = 0;
        if (recorder) {
          try {
            if (recorder.state && recorder.state !== 'inactive') recorder.stop();
          } catch {}
          recorder = null;
        }
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
      }

      async function handleInteractPayload(payload) {
        if (!active || destroyed || !payload) return;
        if (payload.ignored || !payload.reply_text) {
          if (active && !destroyed) {
            emit(phase, {
              fallback: 'mediarecorder',
              message: phase === 'wake' ? 'Dites « Yo Moctar » ou « ELMo ».' : 'Je vous écoute…',
            });
          }
          return;
        }
        const current = { generation };
        job = current;
        clearTimer('followup');
        try {
          onInteractResult?.(payload);
          emit('speaking', {
            fallback: 'mediarecorder',
            transcript: payload.user_text || '',
            reply: payload.reply_text || '',
            audioUrl: payload.audio_url || null,
            message: 'M.O.C.T.A.R vous parle…',
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
          if (active && !destroyed && !suspended) {
            emit(phase, {
              fallback: 'mediarecorder',
              message: phase === 'wake' ? 'Dites « Yo Moctar » ou « ELMo ».' : 'Je vous écoute…',
            });
          }
        }
      }

      async function flushPhrase(blobParts, mimeType) {
        if (!active || destroyed || !blobParts.length) return;
        const blob = (blobParts.length === 1 && blobParts[0] && typeof blobParts[0].size === 'number')
          ? blobParts[0]
          : (typeof Blob === 'function' ? new Blob(blobParts, { type: mimeType || 'audio/webm' }) : blobParts[0]);
        if (!blob || (typeof blob.size === 'number' && blob.size < 128)) return;
        processingBlob = true;
        try {
          const outcome = await transcribeRecordedBlob(blob);
          if (!active || destroyed || !outcome) return;
          if (typeof outcome === 'object') {
            await handleInteractPayload(outcome);
            return;
          }
          const transcript = String(outcome).trim();
          if (!transcript) return;
          const idx = resultIndex++;
          const item = [{ transcript, confidence: 0.98 }];
          item.isFinal = true;
          const results = [];
          results[idx] = item;
          results.length = idx + 1;
          adapter.onresult?.({ resultIndex: idx, results });
        } catch {} finally {
          processingBlob = false;
        }
      }

      function computeAnalyserRms() {
        if (!analyser) return 0;
        if (typeof analyser.currentRms === 'number') return analyser.currentRms;
        if (typeof analyser.getByteTimeDomainData === 'function' && timeDomainData) {
          analyser.getByteTimeDomainData(timeDomainData);
          let sumSq = 0;
          for (let i = 0; i < timeDomainData.length; i += 1) {
            const v = (timeDomainData[i] - 128) / 128;
            sumSq += v * v;
          }
          return Math.sqrt(sumSq / timeDomainData.length);
        }
        return 0;
      }

      function startPhraseRecorder() {
        if (!active || destroyed || !stream) return false;
        if (recorder && recorder.state === 'recording') return true;
        chunks = [];
        const mimeType = pickMimeType();
        try {
          recorder = new MediaRecorderClass(stream, { mimeType });
        } catch {
          recorder = new MediaRecorderClass(stream);
        }
        recorder.ondataavailable = (ev) => {
          if (ev && ev.data && (typeof ev.data.size !== 'number' || ev.data.size > 0)) {
            chunks.push(ev.data);
          }
        };
        recorder.onstop = () => {
          const captured = chunks.splice(0);
          const type = (recorder && recorder.mimeType) || mimeType;
          recorder = null;
          void flushPhrase(captured, type);
        };
        try {
          recorder.start();
          return true;
        } catch (err) {
          adapter.onerror?.({ error: err && err.name === 'NotAllowedError' ? 'not-allowed' : 'audio-capture' });
          adapter.abort();
          return false;
        }
      }

      function scheduleVadPoll() {
        if (!active || destroyed || !stream || !analyser) return;
        vadTimer = delay(() => {
          vadTimer = null;
          if (!active || destroyed || !stream || !analyser) return;
          if (!processingBlob && !job && !busy) {
            const rms = computeAnalyserRms();
            if (rms > vadThreshold) {
              silenceAccumMs = 0;
              if (!isSpeechActive) {
                if (startPhraseRecorder()) {
                  isSpeechActive = true;
                  emit('listening', {
                    fallback: 'mediarecorder',
                    vad: true,
                    rms,
                    message: 'Je vous écoute…',
                  });
                  adapter.onspeechstart?.();
                }
              }
            } else if (isSpeechActive) {
              silenceAccumMs += vadPollMs;
              if (silenceAccumMs >= vadSilenceMs) {
                isSpeechActive = false;
                silenceAccumMs = 0;
                emit('processing', {
                  fallback: 'mediarecorder',
                  message: 'M.O.C.T.A.R prépare la réponse…',
                });
                if (recorder && recorder.state === 'recording') {
                  try { recorder.stop(); } catch {}
                }
              }
            }
          }
          if (active && !destroyed && stream) {
            scheduleVadPoll();
          }
        }, vadPollMs);
      }

      function scheduleCycle() {
        if (!active || destroyed || !stream) return;
        chunks = [];
        const mimeType = pickMimeType();
        try {
          recorder = new MediaRecorderClass(stream, { mimeType });
        } catch {
          recorder = new MediaRecorderClass(stream);
        }
        recorder.ondataavailable = (ev) => {
          if (ev && ev.data && (typeof ev.data.size !== 'number' || ev.data.size > 0)) {
            chunks.push(ev.data);
          }
        };
        recorder.onstop = () => {
          const captured = chunks.splice(0);
          const type = (recorder && recorder.mimeType) || mimeType;
          void flushPhrase(captured, type);
          if (active && !destroyed && stream) {
            scheduleCycle();
          }
        };
        try {
          recorder.start();
          chunkTimer = delay(() => {
            chunkTimer = null;
            if (active && recorder && recorder.state === 'recording') {
              try { recorder.stop(); } catch {}
            }
          }, sliceMs);
        } catch (err) {
          adapter.onerror?.({ error: err && err.name === 'NotAllowedError' ? 'not-allowed' : 'audio-capture' });
          adapter.abort();
        }
      }

      const adapter = {
        isFallbackRecorder: true,
        onstart: null,
        onresult: null,
        onspeechstart: null,
        onerror: null,
        onend: null,
        start() {
          if (active) return;
          active = true;
          Promise.resolve()
            .then(() => mediaDevices.getUserMedia({
              audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
            }))
            .then((mediaStream) => {
              if (!active || destroyed) {
                if (mediaStream && typeof mediaStream.getTracks === 'function') {
                  mediaStream.getTracks().forEach((t) => t.stop());
                }
                return;
              }
              stream = mediaStream;
              adapter.onstart?.({});
              if (AudioContextClass) {
                try {
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
                  scheduleVadPoll();
                  return;
                } catch {
                  analyser = null;
                }
              }
              scheduleCycle();
            })
            .catch((err) => {
              if (!active) return;
              active = false;
              cleanupStream();
              const isDenied = err && (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError' || err.name === 'SecurityError');
              adapter.onerror?.({ error: isDenied ? 'not-allowed' : 'audio-capture' });
              adapter.onend?.({});
            });
        },
        abort() {
          if (!active) return;
          active = false;
          cleanupStream();
          Promise.resolve().then(() => adapter.onend?.({}));
        },
        stop() {
          this.abort();
        },
      };
      return adapter;
    }

    function wireRecognition(instance) {
      instance.onstart = () => {
        if (destroyed) return;
        clearTimer('brave');
        if (status === 'stopping') { requestStop(); return; }
        status = 'active';
        reconcile();
      };
      instance.onresult = handleResult;
      instance.onspeechstart = () => {
        if (wanted() && phase === 'listening') openFollowup();
      };
      instance.onerror = (event) => {
        if (destroyed || status === 'idle' || (status === 'stopping' && intentionalStop)) return;
        const error = event.error || 'unknown';
        if (!usingFallback && hasRecorderFallback && ['network', 'service-not-allowed', 'not-allowed'].includes(error)) {
          activateRecorderFallback(error);
          return;
        }
        if (error === 'not-allowed') {
          fail(error, 'Autorisez le microphone et la reconnaissance vocale dans le navigateur, puis réactivez l’écoute.');
        } else if (!['no-speech', 'aborted', 'audio-capture'].includes(error)) {
          retry(error);
        }
      };
      instance.onend = () => {
        if (destroyed || instance !== recognition) return;
        intentionalStop = false;
        status = 'idle';
        clearTimer('stop');
        stuck = false;
        if (fault && fault.recoverOnEnd) fault = null;
        settleStopped();
        if (wanted() && pendingRetry === null) pendingRetry = 250;
        reconcile();
      };
    }

    function activateRecorderFallback(reason) {
      if (destroyed || usingFallback || !hasRecorderFallback) return false;
      clearTimer('brave');
      clearTimer('stop');
      clearTimer('restart');
      pendingRetry = null;
      const previous = recognition;
      if (previous) {
        previous.onstart = previous.onresult = previous.onspeechstart = previous.onerror = previous.onend = null;
        try {
          if (typeof previous.abort === 'function') previous.abort();
          else if (typeof previous.stop === 'function') previous.stop();
        } catch {}
      }
      usingFallback = true;
      fault = null;
      stuck = false;
      intentionalStop = false;
      status = 'idle';
      settleStopped();
      recognition = createRecorderRecognition();
      wireRecognition(recognition);
      emit('starting', {
        fallback: 'mediarecorder',
        reason,
        message: 'Capture vocale directe M.O.C.T.A.R activée (contourneur Brave)…',
      });
      if (wanted()) startRecognition();
      return true;
    }

    function armBraveWatchdog() {
      if (usingFallback || !hasRecorderFallback || braveTimer !== null) return;
      braveTimer = delay(() => {
        braveTimer = null;
        if (destroyed || usingFallback || !wanted()) return;
        if (status === 'starting' || state === 'starting') {
          activateRecorderFallback('starting-timeout');
        }
      }, braveWatchdogMs);
    }

    function requestStop() {
      clearTimer('brave');
      if (!recognition || status === 'idle' || status === 'stopping') return;
      intentionalStop = true;
      status = 'stopping';
      // A missing onend must never start a second microphone session. Preserve
      // the user's listening preference and resume if the browser confirms it later.
      stopTimer = delay(() => {
        stopTimer = null;
        if (destroyed || status !== 'stopping') return;
        stuck = true;
        fail('stop-timeout', 'Le navigateur ne libère pas le microphone. En attente de sa libération ; rechargez la page si le problème persiste.', false);
        settleStopped();
      }, STOP_TIMEOUT_MS);
      try {
        if (typeof recognition.abort === 'function') recognition.abort();
        else recognition.stop();
      } catch (error) {
        // A failed abort cannot safely be followed by a new start.
        status = 'idle';
        stuck = true;
        clearTimer('stop');
        settleStopped();
        fail(error, 'Impossible d’arrêter le microphone. En attente de sa libération ; rechargez la page si le problème persiste.', false);
      }
    }

    function stopRecognition() {
      if (status === 'idle') return Promise.resolve();
      return new Promise((resolve) => {
        stoppedWaiters.push(resolve);
        requestStop();
      });
    }

    function retry(error) {
      pendingRetry = 1500;
      emit('starting', {
        error,
        message: error === 'service-not-allowed'
          ? 'La reconnaissance vocale est bloquée par le navigateur. Vérifiez ses autorisations ; l’écoute reste activée.'
          : 'Connexion au service de reconnaissance vocale…',
      });
      armBraveWatchdog();
    }

    function startRecognition() {
      if (!wanted() || status !== 'idle' || restartTimer !== null) return;
      status = 'starting';
      intentionalStop = false;
      finalResults.clear();
      emit('starting', {
        fallback: usingFallback ? 'mediarecorder' : undefined,
        message: usingFallback ? 'Microphone direct M.O.C.T.A.R actif…' : 'Activation du microphone…',
      });
      armBraveWatchdog();
      try {
        recognition.start();
      } catch (error) {
        status = 'idle';
        if (!usingFallback && hasRecorderFallback) {
          activateRecorderFallback(error.name || 'start-error');
          return;
        }
        if (error.name === 'NotAllowedError' || error.name === 'SecurityError') {
          fail(error, 'Autorisez le microphone dans les réglages du navigateur, puis réactivez l’écoute.');
        } else {
          retry(error);
          reconcile();
        }
      }
    }

    function reconcile() {
      if (destroyed) return;
      if (!wanted()) {
        requestStop();
        if (fault) emit('error', fault);
        else if (!enabled && !manual) emit('off');
        else if (suspended || busy) emit('paused', { message: suspended ? 'Écoute suspendue : revenez sur le dashboard.' : 'Réponse en cours…' });
        return;
      }
      if (status === 'idle') {
        if (pendingRetry !== null && restartTimer === null) {
          const wait = pendingRetry;
          pendingRetry = null;
          restartTimer = delay(() => {
            restartTimer = null;
            startRecognition();
          }, wait);
        } else startRecognition();
      } else if (status === 'active') {
        emit(phase, {
          fallback: usingFallback ? 'mediarecorder' : undefined,
          message: phase === 'wake' ? 'Dites « Yo Moctar » ou « ELMo ».' : 'Je vous écoute…',
        });
      }
    }

    function openFollowup() {
      phase = 'listening';
      clearTimer('followup');
      followupTimer = delay(() => {
        followupTimer = null;
        phase = 'wake';
        if (!enabled) manual = false;
        reconcile();
      }, FOLLOWUP_MS);
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

    async function handleSpeech(text, greeting) {
      const current = { generation };
      job = current;
      clearTimer('followup');
      clearTimer('restart');
      pendingRetry = null;
      emit(greeting ? 'speaking' : 'processing', { transcript: text, message: greeting ? GREETING : 'Commande en cours…' });
      try {
        await stopRecognition();
        if (generation !== current.generation || destroyed || suspended) return;
        if (greeting) await speak(GREETING);
        else await onPrompt(text);
        if (generation !== current.generation || destroyed || suspended) return;
        if (enabled) openFollowup();
        else if (greeting && manual) openFollowup();
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

    function handleResult(event) {
      if (!wanted() || (status !== 'starting' && status !== 'active')) return;
      const results = event.results || [];
      for (let index = event.resultIndex || 0; index < results.length; index += 1) {
        const result = results[index];
        if (!result) continue;
        if (!result.isFinal) {
          // A phrase started near the end of the conversation window must not
          // be discarded just because its final transcription arrives later.
          if (phase === 'listening' && result[0] && String(result[0].transcript || '').trim()) openFollowup();
          continue;
        }
        if (finalResults.has(index)) continue;
        finalResults.add(index);
        const text = String(result[0] && result[0].transcript || '').trim();
        if (!text) continue;
        if (STOP_PHRASES.has(phrase(text))) {
          phase = 'wake';
          manual = false;
          clearTimer('followup');
          reconcile();
          return;
        }
        const wake = findWake(text);
        if (phase === 'wake' && !wake) continue;
        if (wake && !wake.command) void handleSpeech(text, true);
        else void handleSpeech(wake ? wake.command : text, false);
        return;
      }
    }

    if (preferWebAudio) {
      usingFallback = true;
      recognition = createRecorderRecognition();
      wireRecognition(recognition);
    } else if (opts.Recognition) {
      try {
        recognition = new opts.Recognition();
        recognition.lang = opts.language || 'fr-FR';
        recognition.continuous = true;
        recognition.interimResults = true;
        wireRecognition(recognition);
      } catch (error) {
        recognition = null;
        if (hasRecorderFallback) {
          usingFallback = true;
          recognition = createRecorderRecognition();
          wireRecognition(recognition);
        } else {
          fault = { error, message: 'La reconnaissance vocale n’a pas pu être initialisée dans ce navigateur.' };
          state = 'error';
        }
      }
    } else if (hasRecorderFallback) {
      usingFallback = true;
      recognition = createRecorderRecognition();
      wireRecognition(recognition);
    }

    function available() {
      if (destroyed) return false;
      if (!recognition) {
        emit(fault ? 'error' : 'unsupported', fault || { message: 'La reconnaissance vocale est indisponible dans ce navigateur. Essayez Chrome ou Safari.' });
        return false;
      }
      if (stuck) {
        emit('error', fault || { message: 'Le microphone est bloqué. Rechargez la page pour réactiver l’écoute.' });
        return false;
      }
      return true;
    }

    return {
      get enabled() { return enabled; },
      get listeningDesired() { return enabled; },
      get state() { return state; },
      get usingFallback() { return usingFallback; },
      async enable() {
        if (!available()) return false;
        generation += 1;
        fault = null;
        enabled = true;
        manual = false;
        phase = 'wake';
        clearTimers();
        reconcile();
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
        clearTimers();
        requestStop();
        emit('off');
      },
      async startManual() {
        if (!available()) return false;
        generation += 1;
        fault = null;
        manual = true;
        clearTimers();
        openFollowup();
        reconcile();
        return manual && !fault;
      },
      stopManual() {
        generation += 1;
        manual = false;
        phase = 'wake';
        clearTimer('followup');
        reconcile();
      },
      setBusy(value) {
        busy = Boolean(value);
        if (busy) {
          clearTimer('followup');
          clearTimer('restart');
        } else if (phase === 'listening' && !job && (enabled || manual)) openFollowup();
        reconcile();
      },
      suspend() {
        generation += 1;
        suspended = true;
        manual = false;
        phase = 'wake';
        clearTimers();
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
        clearTimers();
        requestStop();
        destroyed = true;
        clearTimer('stop');
        settleStopped();
        if (recognition) {
          recognition.onstart = recognition.onresult = recognition.onspeechstart = recognition.onerror = recognition.onend = null;
        }
        emit('off');
      },
    };
  }

  return { create };
});

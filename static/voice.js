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
    let recognition = null;
    let enabled = false;
    let manual = false;
    let suspended = false;
    let destroyed = false;
    let busy = false;
    let phase = 'wake';
    let state = opts.Recognition ? 'off' : 'unsupported';
    let fault = null;
    let generation = 0;
    let job = null;
    let status = 'idle';
    let intentionalStop = false;
    let retries = 0;
    let pendingRetry = null;
    let restartTimer = null;
    let followupTimer = null;
    let stableTimer = null;
    let stopTimer = null;
    let stuck = false;
    let stable = false;
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
      if (name === 'stable' && stableTimer !== null) {
        cancel(stableTimer);
        stableTimer = null;
      }
      if (name === 'stop' && stopTimer !== null) {
        cancel(stopTimer);
        stopTimer = null;
      }
    }

    function clearTimers() {
      ['followup', 'restart', 'stable'].forEach(clearTimer);
      pendingRetry = null;
    }

    function settleStopped() {
      stoppedWaiters.splice(0).forEach((resolve) => resolve());
    }

    function wanted() {
      return !destroyed && !suspended && !fault && !busy && !job && (enabled || manual);
    }

    function fail(error, message) {
      fault = { error, message };
      enabled = false;
      manual = false;
      generation += 1;
      clearTimers();
      emit('error', fault);
      requestStop();
    }

    function requestStop() {
      if (!recognition || status === 'idle' || status === 'stopping') return;
      intentionalStop = true;
      status = 'stopping';
      clearTimer('stable');
      // Some mobile recognizers never emit onend after abort. Fail closed rather
      // than hang a command forever or start a second microphone session.
      stopTimer = delay(() => {
        stopTimer = null;
        if (destroyed || status !== 'stopping') return;
        stuck = true;
        fail('stop-timeout', 'Le navigateur ne libère pas le microphone. Rechargez la page pour réactiver l’écoute.');
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
        fail(error, 'Impossible d’arrêter le microphone. Rechargez la page avant de réactiver l’écoute.');
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
      retries += 1;
      if (retries > 4) {
        fail(error, 'L’écoute a été interrompue plusieurs fois. Vérifiez le réseau et réactivez le microphone.');
        return;
      }
      pendingRetry = Math.min(250 * (2 ** (retries - 1)), 2000);
      emit('starting', { error, message: 'Connexion au service de reconnaissance vocale…' });
    }

    function startRecognition() {
      if (!wanted() || status !== 'idle' || restartTimer !== null) return;
      status = 'starting';
      intentionalStop = false;
      stable = false;
      finalResults.clear();
      emit('starting', { message: 'Activation du microphone…' });
      try {
        recognition.start();
      } catch (error) {
        status = 'idle';
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
        emit(phase, { message: phase === 'wake' ? 'Dites « Yo Moctar » ou « ELMo ».' : 'Je vous écoute…' });
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
        retries = 0;
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

    if (opts.Recognition) {
      try {
        recognition = new opts.Recognition();
        recognition.lang = opts.language || 'fr-FR';
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.onstart = () => {
          if (destroyed) return;
          if (status === 'stopping') { requestStop(); return; }
          status = 'active';
          stableTimer = delay(() => { stable = true; retries = 0; }, 5000);
          reconcile();
        };
        recognition.onresult = handleResult;
        recognition.onspeechstart = () => {
          if (wanted() && phase === 'listening') openFollowup();
        };
        recognition.onerror = (event) => {
          if (destroyed || status === 'idle' || (status === 'stopping' && intentionalStop)) return;
          const error = event.error || 'unknown';
          if (['not-allowed', 'service-not-allowed', 'audio-capture'].includes(error)) {
            fail(error, error === 'audio-capture'
              ? 'Aucun microphone disponible. Vérifiez sa connexion et réactivez l’écoute.'
              : 'Autorisez le microphone et la reconnaissance vocale dans le navigateur, puis réactivez l’écoute.');
          } else {
            if (error === 'no-speech') pendingRetry = 250;
            else retry(error);
            requestStop();
          }
        };
        recognition.onend = () => {
          if (destroyed) return;
          const stopped = intentionalStop;
          status = 'idle';
          clearTimer('stable');
          clearTimer('stop');
          stuck = false;
          settleStopped();
          if (!stopped && wanted()) {
            if (stable) pendingRetry = 250;
            else retry('interrupted');
          }
          reconcile();
        };
      } catch (error) {
        recognition = null;
        fault = { error, message: 'La reconnaissance vocale n’a pas pu être initialisée dans ce navigateur.' };
        state = 'error';
      }
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
      get state() { return state; },
      async enable() {
        if (!available()) return false;
        generation += 1;
        fault = null;
        retries = 0;
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
        clearTimers();
        requestStop();
        emit('off');
      },
      async startManual() {
        if (!available()) return false;
        generation += 1;
        fault = null;
        retries = 0;
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

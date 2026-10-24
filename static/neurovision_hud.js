/**
 * JARVIS M.O.C.T.A.R // NEUROVISION 3D HOLOGRAPHIC HUD CONTROLLER
 * Architecture adaptative universelle :
 * - Fonctionne sur le PC/mobile de l'utilisateur (Webcam locale via getUserMedia)
 * - Synchronisation continue avec le serveur JARVIS via /api/vision/frame
 * - Rendu 3D temps réel du Digital Twin (tête filaire gyroscopique)
 * - Oscilloscope biométrique EEG/Cardiaque en Canvas 60 FPS
 */

(() => {
  'use strict';

  // Sélecteurs d'éléments
  const dialog = document.getElementById('neurovision-dialog');
  const clientVideo = document.getElementById('nv-client-video');
  const serverImg = document.getElementById('nv-server-feed');
  const toggleBtn = document.getElementById('nv-toggle-btn');
  const switchSrcBtn = document.getElementById('nv-switch-source-btn');
  const resetBtn = document.getElementById('nv-reset-btn');
  const statusChipText = document.getElementById('nv-stream-status-text');
  const chipDot = document.querySelector('.nv-chip-dot');
  const fpsTag = document.getElementById('nv-fps-tag');
  const sourceBadge = document.getElementById('nv-source-badge');

  // Canvas
  const canvas3D = document.getElementById('nv-3d-canvas');
  const canvasWave = document.getElementById('nv-wave-canvas');
  const captureCanvas = document.createElement('canvas');
  captureCanvas.width = 480;
  captureCanvas.height = 360;
  const captureCtx = captureCanvas.getContext('2d');

  // État local
  let clientStream = null;
  let isClientMode = true; // true = Webcam PC Moctar, false = Serveur Linux
  let isCameraActive = false;
  let pollInterval = null;
  let frameSendInterval = null;
  let lastFrameTime = performance.now();
  let clientFps = 0;
  let audioCtx = null;

  // Données biométriques reçues
  let currentMetrics = {
    fatigue_score: 0,
    fatigue_level: 'Normal',
    blink_count: 0,
    blink_rate_per_min: 0,
    yawn_count: 0,
    head_tilt_yaw: 0.0,
    head_roll: 0.0,
    user_present: false,
    session_duration_seconds: 0
  };

  // Cible d'angles pour lissage 3D (lerp)
  let targetYaw = 0.0;
  let targetRoll = 0.0;
  let currentYaw = 0.0;
  let currentRoll = 0.0;

  // =========================================================================
  // 1. GESTION ADAPTATIVE DE LA CAMÉRA (CLIENT / SERVEUR)
  // =========================================================================

  async function startClientCamera() {
    try {
      if (clientStream) stopClientCamera();

      // Vérifie si le navigateur autorise l'API MediaDevices
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        const isRemoteHttp = location.protocol === 'http:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1';
        if (isRemoteHttp) {
          const msg = "Sécurité navigateur : pour utiliser la webcam de ce PC via http://" + location.host + ", ajoutez cette URL dans chrome://flags/#unsafely-treat-insecure-origin-as-secure ou passez en HTTPS.";
          console.warn(msg);
          if (typeof window.toast === 'function') {
            window.toast("Caméra PC : activez l'exception HTTP dans chrome://flags ou utilisez HTTPS.", "warning");
          }
          updateStatusUI('⚠️ HTTPS OU EXCEPTION REQUIS SUR CE PC', '#ff9900');
          startClientSimulation();
          return;
        }
      }

      // Demande d'accès à la webcam locale de l'appareil (PC de Moctar)
      const constraints = {
        video: {
          width: { ideal: 480 },
          height: { ideal: 360 },
          facingMode: 'user'
        },
        audio: false
      };

      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      clientStream = stream;

      if (clientVideo) {
        clientVideo.srcObject = stream;
        clientVideo.style.display = 'block';
        await clientVideo.play();
      }
      if (serverImg) {
        serverImg.style.display = 'none';
        serverImg.src = '';
      }

      isClientMode = true;
      isCameraActive = true;
      updateStatusUI('● CAMÉRA LOCALE (PC MOCTAR)', '#00f3ff');
      if (sourceBadge) sourceBadge.textContent = 'WEBCAM CET ÉCRAN';

      // Démarre l'envoi régulier de frames au serveur Jarvis (3 fois par seconde)
      if (frameSendInterval) clearInterval(frameSendInterval);
      frameSendInterval = setInterval(sendFrameToServer, 330);

      playSciFiTone(880, 'sine', 0.12);
      updateToggleButtonUI(true);
      if (typeof window.toast === 'function') {
        window.toast('Caméra locale de votre écran activée avec succès.', 'success');
      }
    } catch (err) {
      console.warn('Impossible d\'ouvrir la webcam locale du client :', err);
      updateStatusUI('⚠️ WEBCAM NON DISPONIBLE SUR CE PC', '#ff9900');
      startClientSimulation();
    }
  }

  function startClientSimulation() {
    isCameraActive = true;
    updateToggleButtonUI(true);
    if (sourceBadge) sourceBadge.textContent = 'MODE SIMULATION 3D';
    if (fpsTag) fpsTag.textContent = '30 FPS';
    // Mode démo autonome générant une télémétrie fluide pour le HUD 3D
    if (frameSendInterval) clearInterval(frameSendInterval);
    frameSendInterval = setInterval(() => {
      const now = performance.now();
      const yaw = Math.round(Math.sin(now * 0.001) * 15 * 10) / 10;
      const roll = Math.round(Math.cos(now * 0.0008) * 8 * 10) / 10;
      const metrics = {
        active: true,
        user_present: true,
        face_detected: true,
        head_tilt_yaw: yaw,
        head_roll: roll,
        fatigue_score: Math.min(100, Math.floor(Math.abs(yaw) + Math.abs(roll) * 1.5)),
        fatigue_level: Math.abs(yaw) > 10 ? 'Distrait' : 'Normal',
        blink_count: Math.floor(now / 5000),
        blink_rate_per_min: 16,
        yawn_count: 0,
        session_duration_seconds: Math.floor(now / 1000)
      };
      updateMetricsUI(metrics);
      // Synchronisation légère avec le serveur (JSON uniquement, zéro vidéo, zéro CPU)
      fetch('/api/vision/telemetry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(metrics)
      }).catch(() => {});
    }, 400);
  }

  function stopClientCamera() {
    if (clientStream) {
      clientStream.getTracks().forEach(t => t.stop());
      clientStream = null;
    }
    if (clientVideo) {
      clientVideo.srcObject = null;
      clientVideo.style.display = 'none';
    }
    if (frameSendInterval) {
      clearInterval(frameSendInterval);
      frameSendInterval = null;
    }
    isCameraActive = false;
    updateStatusUI('○ VEILLE NEUROVISION', '#8c9eb9');
    updateToggleButtonUI(false);
  }

  function fallbackToServerCamera() {
    stopClientCamera();
    isClientMode = false;
    if (serverImg) {
      serverImg.src = '/api/vision/stream?t=' + Date.now();
      serverImg.style.display = 'block';
    }
    if (clientVideo) clientVideo.style.display = 'none';
    isCameraActive = true;
    updateStatusUI('● FLUX SERVEUR HÔTE', '#00ff88');
    if (sourceBadge) sourceBadge.textContent = 'SERVEUR LINUX';
    updateToggleButtonUI(true);
  }

  async function toggleCamera() {
    if (isCameraActive) {
      stopClientCamera();
      // Notifie aussi le serveur pour passer en veille
      try {
        await fetch('/api/vision/toggle', { method: 'POST' });
      } catch (e) {}
    } else {
      if (isClientMode && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        await startClientCamera();
      } else {
        fallbackToServerCamera();
        try {
          await fetch('/api/vision/toggle', { method: 'POST' });
        } catch (e) {}
      }
    }
  }

  function switchSource() {
    if (isClientMode) {
      fallbackToServerCamera();
      if (typeof window.toast === 'function') window.toast('Basculé sur la caméra du serveur.', 'info');
    } else {
      startClientCamera();
      if (typeof window.toast === 'function') window.toast('Basculé sur la caméra de ce PC.', 'success');
    }
  }

  // Envoi d'une frame locale du navigateur vers l'endpoint /api/vision/frame de Jarvis
  async function sendFrameToServer() {
    if (!clientVideo || clientVideo.readyState < 2 || !isCameraActive) return;

    try {
      captureCtx.drawImage(clientVideo, 0, 0, 480, 360);
      captureCanvas.toBlob(async (blob) => {
        if (!blob) return;
        try {
          const res = await fetch('/api/vision/frame', {
            method: 'POST',
            body: blob,
            headers: { 'Content-Type': 'image/jpeg' }
          });
          if (res.ok) {
            const metrics = await res.json();
            updateMetricsUI(metrics);
          }
        } catch (postErr) {
          console.debug('Frame post error', postErr);
        }
      }, 'image/jpeg', 0.65);

      // Calcul FPS client
      const now = performance.now();
      clientFps = Math.round(1000 / Math.max(1, (now - lastFrameTime)));
      lastFrameTime = now;
      if (fpsTag) fpsTag.textContent = clientFps + ' FPS';
    } catch (e) {
      console.debug('Capture frame error', e);
    }
  }

  async function pollMetrics() {
    try {
      const res = await fetch('/api/vision/metrics');
      if (res.ok) {
        const metrics = await res.json();
        updateMetricsUI(metrics);
      }
    } catch (e) {}
  }

  // =========================================================================
  // 2. MISE À JOUR DE L'INTERFACE BIOMÉTRIQUE & JAUGES
  // =========================================================================

  function updateMetricsUI(m) {
    if (!m) return;
    currentMetrics = m;

    targetYaw = Number(m.head_tilt_yaw) || 0.0;
    targetRoll = Number(m.head_roll) || 0.0;

    // État cognitif
    const statePill = document.getElementById('nv-state-display');
    const level = m.fatigue_level || 'Normal';
    if (statePill) {
      statePill.textContent = level.toUpperCase();
      if (level.includes('Sévère')) {
        statePill.style.color = '#ff3366';
        statePill.style.borderColor = '#ff3366';
        statePill.style.background = 'rgba(255, 51, 102, 0.15)';
      } else if (level.includes('Fatigué')) {
        statePill.style.color = '#ff9900';
        statePill.style.borderColor = '#ff9900';
        statePill.style.background = 'rgba(255, 153, 0, 0.15)';
      } else {
        statePill.style.color = '#00f3ff';
        statePill.style.borderColor = 'rgba(0, 243, 255, 0.4)';
        statePill.style.background = 'rgba(0, 243, 255, 0.08)';
      }
    }

    // Jauge Radiale Fatigue
    const score = Math.min(100, Math.max(0, m.fatigue_score || 0));
    const radialFill = document.getElementById('nv-radial-stroke');
    const radialText = document.getElementById('nv-radial-score-text');
    if (radialText) radialText.textContent = score + '%';
    if (radialFill) {
      // Périmètre du cercle r=30 : 2 * PI * 30 ≈ 188.5
      const offset = 188.5 - (188.5 * score) / 100.0;
      radialFill.style.strokeDashoffset = offset;
      if (score >= 70) {
        radialFill.style.stroke = '#ff3366';
        radialFill.style.filter = 'drop-shadow(0 0 8px #ff3366)';
      } else if (score >= 40) {
        radialFill.style.stroke = '#ff9900';
        radialFill.style.filter = 'drop-shadow(0 0 8px #ff9900)';
      } else {
        radialFill.style.stroke = '#00f3ff';
        radialFill.style.filter = 'drop-shadow(0 0 8px #00f3ff)';
      }
    }

    // Statistiques clignements / bâillements
    const bCount = document.getElementById('nv-blink-count');
    if (bCount) bCount.textContent = m.blink_count || 0;
    const bRate = document.getElementById('nv-blink-rate');
    if (bRate) bRate.textContent = m.blink_rate_per_min || 0;
    const yCount = document.getElementById('nv-yawn-count');
    if (yCount) yCount.textContent = m.yawn_count || 0;

    // Angles Yaw & Roll
    const yawEl = document.getElementById('nv-yaw-angle');
    if (yawEl) yawEl.textContent = (m.head_tilt_yaw > 0 ? '+' : '') + (m.head_tilt_yaw || 0) + '°';
    const rollEl = document.getElementById('nv-roll-angle');
    if (rollEl) rollEl.textContent = (m.head_roll > 0 ? '+' : '') + (m.head_roll || 0) + '°';

    // Présence
    const presEl = document.getElementById('nv-presence-status');
    if (presEl) {
      if (!isCameraActive) {
        presEl.textContent = 'VEILLE';
        presEl.style.color = '#8c9eb9';
      } else if (m.user_present) {
        presEl.textContent = 'PRÉSENT (VERROUILLÉ)';
        presEl.style.color = '#00ff88';
      } else {
        presEl.textContent = 'HORS CHAMP';
        presEl.style.color = '#ff9900';
      }
    }

    // Durée session
    const durEl = document.getElementById('nv-session-duration');
    if (durEl) {
      const s = m.session_duration_seconds || 0;
      durEl.textContent = Math.floor(s / 60) + 'm ' + (s % 60) + 's';
    }
  }

  function updateStatusUI(text, color) {
    if (statusChipText) statusChipText.textContent = text;
    if (chipDot) {
      chipDot.style.background = color;
      chipDot.style.boxShadow = '0 0 8px ' + color;
    }
  }

  function updateToggleButtonUI(active) {
    if (!toggleBtn) return;
    if (active) {
      toggleBtn.textContent = '🛑 DÉSACTIVER LA CAMÉRA';
      toggleBtn.classList.remove('primary');
      toggleBtn.classList.add('danger');
    } else {
      toggleBtn.textContent = '📷 ACTIVER LA CAMÉRA DE CET ÉCRAN';
      toggleBtn.classList.remove('danger');
      toggleBtn.classList.add('primary');
    }
  }

  // =========================================================================
  // 3. MOTEUR 3D DIGITAL TWIN & ANNEAUX GYROSCOPIQUES (CANVAS)
  // =========================================================================

  let is3DInitialized = false;
  let isWaveInitialized = false;

  function resizeCanvases() {
    if (canvas3D && canvas3D.parentElement) {
      const rect = canvas3D.parentElement.getBoundingClientRect();
      if (rect.width > 50) {
        canvas3D.width = Math.round(rect.width);
        canvas3D.height = Math.round(rect.height) || 180;
      }
    }
    if (canvasWave && canvasWave.parentElement) {
      const rect = canvasWave.parentElement.getBoundingClientRect();
      if (rect.width > 50) {
        canvasWave.width = Math.round(rect.width);
        canvasWave.height = Math.round(rect.height) || 48;
      }
    }
  }
  window.addEventListener('resize', resizeCanvases);

  function init3DDigitalTwin() {
    if (!canvas3D) return;
    resizeCanvases();
    if (is3DInitialized) return;
    is3DInitialized = true;
    const ctx = canvas3D.getContext('2d');

    // Définition géométrique d'un visage 3D filaire (Vertices 3D)
    const baseNodes = [
      // Crâne contour
      { x: 0, y: -65, z: 0 },
      { x: -35, y: -45, z: 15 }, { x: 35, y: -45, z: 15 },
      { x: -45, y: -15, z: 20 }, { x: 45, y: -15, z: 20 },
      { x: -40, y: 25, z: 15 }, { x: 40, y: 25, z: 15 },
      { x: -22, y: 55, z: 10 }, { x: 22, y: 55, z: 10 },
      { x: 0, y: 70, z: 5 }, // Menton

      // Yeux & Sourcils
      { x: -18, y: -12, z: 32 }, { x: -6, y: -12, z: 32 },
      { x: 6, y: -12, z: 32 }, { x: 18, y: -12, z: 32 },
      { x: -18, y: -4, z: 30 }, { x: -6, y: -4, z: 30 },
      { x: 6, y: -4, z: 30 }, { x: 18, y: -4, z: 30 },

      // Nez
      { x: 0, y: -8, z: 35 },
      { x: 0, y: 14, z: 42 },
      { x: -7, y: 18, z: 36 }, { x: 7, y: 18, z: 36 },

      // Bouche
      { x: -14, y: 35, z: 28 }, { x: 0, y: 32, z: 32 }, { x: 14, y: 35, z: 28 },
      { x: 0, y: 40, z: 29 }
    ];

    // Arêtes filaires (Edges) reliant les points
    const edges = [
      [0, 1], [0, 2], [1, 3], [2, 4], [3, 5], [4, 6], [5, 7], [6, 8], [7, 9], [8, 9],
      [10, 11], [11, 15], [15, 14], [14, 10], // Œil Gauche
      [12, 13], [13, 17], [17, 16], [16, 12], // Œil Droit
      [18, 19], [19, 20], [19, 21], [20, 21], // Nez
      [22, 23], [23, 24], [24, 25], [25, 22]  // Bouche
    ];

    let idleAngle = 0;

    function render3D() {
      const width = canvas3D.width || 380;
      const height = canvas3D.height || 180;
      const centerX = width / 2;
      const centerY = height / 2;

      // Lissage des angles Yaw et Roll
      currentYaw += (targetYaw - currentYaw) * 0.15;
      currentRoll += (targetRoll - currentRoll) * 0.15;
      idleAngle += 0.015;

      ctx.clearRect(0, 0, width, height);

      // Fond tech & grille radiale
      ctx.strokeStyle = 'rgba(0, 243, 255, 0.05)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(centerX, centerY, 80, 0, Math.PI * 2);
      ctx.arc(centerX, centerY, 50, 0, Math.PI * 2);
      ctx.stroke();

      // Conversion des angles en radians
      const yawRad = (currentYaw * Math.PI) / 180;
      const rollRad = (currentRoll * Math.PI) / 180;
      const pitchRad = Math.sin(idleAngle) * 0.08;

      // Matrice de rotation 3D
      const cosY = Math.cos(yawRad);
      const sinY = Math.sin(yawRad);
      const cosR = Math.cos(rollRad);
      const sinR = Math.sin(rollRad);
      const cosP = Math.cos(pitchRad);
      const sinP = Math.sin(pitchRad);

      function project(x, y, z) {
        // Rotation Yaw (Y-axis)
        let x1 = x * cosY + z * sinY;
        let y1 = y;
        let z1 = -x * sinY + z * cosY;

        // Rotation Pitch (X-axis)
        let x2 = x1;
        let y2 = y1 * cosP - z1 * sinP;
        let z2 = y1 * sinP + z1 * cosP;

        // Rotation Roll (Z-axis)
        let x3 = x2 * cosR - y2 * sinR;
        let y3 = x2 * sinR + y2 * cosR;
        let z3 = z2;

        // Projection perspective
        const distance = 190;
        const scale = distance / (distance + z3);
        return {
          x: centerX + x3 * scale,
          y: centerY + y3 * scale,
          z: z3
        };
      }

      // 1. Dessin des anneaux orbitaux gyroscopiques
      drawGimbalRing(ctx, centerX, centerY, 82, yawRad, 'rgba(0, 243, 255, 0.45)');
      drawGimbalRing(ctx, centerX, centerY, 92, rollRad + Math.PI / 2, 'rgba(0, 102, 255, 0.35)');

      // 2. Projection et tracé des arêtes du visage
      const projected = baseNodes.map(n => project(n.x, n.y, n.z));

      ctx.strokeStyle = '#00f3ff';
      ctx.lineWidth = 1.4;
      ctx.shadowColor = 'rgba(0, 243, 255, 0.6)';
      ctx.shadowBlur = 6;

      ctx.beginPath();
      edges.forEach(([i, j]) => {
        const p1 = projected[i];
        const p2 = projected[j];
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
      });
      ctx.stroke();
      ctx.shadowBlur = 0;

      // 3. Points nodaux lumineux
      projected.forEach((p, idx) => {
        ctx.fillStyle = idx >= 10 && idx <= 17 ? '#ffffff' : '#00f3ff';
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2, 0, Math.PI * 2);
        ctx.fill();
      });

      requestAnimationFrame(render3D);
    }

    function drawGimbalRing(c, cx, cy, radius, rot, color) {
      c.save();
      c.translate(cx, cy);
      c.rotate(rot);
      c.strokeStyle = color;
      c.lineWidth = 1;
      c.beginPath();
      c.ellipse(0, 0, radius, radius * 0.45, 0, 0, Math.PI * 2);
      c.stroke();

      // Graduations
      c.fillStyle = color;
      for (let a = 0; a < Math.PI * 2; a += Math.PI / 6) {
        const gx = Math.cos(a) * radius;
        const gy = Math.sin(a) * (radius * 0.45);
        c.fillRect(gx - 1, gy - 1, 2, 2);
      }
      c.restore();
    }

    render3D();
  }

  // =========================================================================
  // 4. OSCILLOSCOPE BIOMÉTRIQUE EEG / CARDIAQUE (CANVAS)
  // =========================================================================

  function initWaveOscilloscope() {
    if (!canvasWave) return;
    resizeCanvases();
    if (isWaveInitialized) return;
    isWaveInitialized = true;
    const ctx = canvasWave.getContext('2d');
    let offset = 0;

    function renderWave() {
      const width = canvasWave.width || 380;
      const height = canvasWave.height || 48;
      offset += 2.5;
      ctx.clearRect(0, 0, width, height);

      // Grille de fond
      ctx.strokeStyle = 'rgba(0, 243, 255, 0.08)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = 0; x < width; x += 20) {
        ctx.moveTo(x, 0); ctx.lineTo(x, height);
      }
      ctx.stroke();

      // Couleur selon niveau de fatigue
      const score = currentMetrics.fatigue_score || 0;
      let strokeColor = '#00f3ff';
      if (score >= 70) strokeColor = '#ff3366';
      else if (score >= 40) strokeColor = '#ff9900';

      ctx.strokeStyle = strokeColor;
      ctx.lineWidth = 1.6;
      ctx.shadowColor = strokeColor;
      ctx.shadowBlur = 8;
      ctx.beginPath();

      const midY = height / 2;
      for (let x = 0; x < width; x++) {
        const waveX = (x + offset) % 140;
        let y = midY;

        // Motif d'impulsion cardiaque QRS
        if (waveX > 50 && waveX < 56) y -= 14;
        else if (waveX >= 56 && waveX < 64) y += 18;
        else if (waveX >= 64 && waveX < 70) y -= 8;
        else y += Math.sin((x + offset) * 0.08) * 3;

        if (x === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.shadowBlur = 0;

      requestAnimationFrame(renderWave);
    }

    renderWave();
  }

  // =========================================================================
  // 5. EFFET AUDIO SCI-FI (WEB AUDIO API)
  // =========================================================================

  function playSciFiTone(freq, type, duration) {
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = type || 'sine';
      osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
      gain.gain.setValueAtTime(0.08, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + duration);
    } catch (e) {}
  }

  // =========================================================================
  // 6. ÉCOUTEURS D'ÉVÉNEMENTS & INITIALISATION
  // =========================================================================

  if (toggleBtn) {
    toggleBtn.addEventListener('click', toggleCamera);
  }

  if (switchSrcBtn) {
    switchSrcBtn.addEventListener('click', switchSource);
  }

  // Support spécifique du HUD 3D (bouton mode pill et overlay)
  const hudOverlay = document.getElementById('hud-neurovision-overlay');
  const hudCloseBtn = document.getElementById('hud-nv-close-btn');
  const btnNeuroHud = document.getElementById('btn-neurovision-hud');

  if (btnNeuroHud && hudOverlay) {
    btnNeuroHud.addEventListener('click', () => {
      hudOverlay.classList.add('active');
      init3DDigitalTwin();
      initWaveOscilloscope();
      startClientCamera();
      if (!pollInterval) pollInterval = setInterval(pollMetrics, 1000);
    });
  }

  if (hudCloseBtn && hudOverlay) {
    hudCloseBtn.addEventListener('click', () => {
      hudOverlay.classList.remove('active');
      stopClientCamera();
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener('click', async () => {
      try {
        resetBtn.disabled = true;
        await fetch('/api/vision/reset', { method: 'POST' });
        setTimeout(pollMetrics, 200);
        if (typeof window.toast === 'function') window.toast('Compteurs NeuroVision réinitialisés.', 'success');
      } catch (e) {
      } finally {
        resetBtn.disabled = false;
      }
    });
  }

  // Surveillance de l'ouverture du dialog
  if (dialog) {
    const observer = new MutationObserver(() => {
      if (dialog.open) {
        setTimeout(resizeCanvases, 60);
        init3DDigitalTwin();
        initWaveOscilloscope();
        // Démarre la caméra de l'appareil automatiquement par défaut
        startClientCamera();
        if (!pollInterval) pollInterval = setInterval(pollMetrics, 1000);
      } else {
        stopClientCamera();
        if (pollInterval) {
          clearInterval(pollInterval);
          pollInterval = null;
        }
      }
    });
    observer.observe(dialog, { attributes: true, attributeFilter: ['open'] });
  }

  // Lancement initial des animations de rendu 3D
  init3DDigitalTwin();
  initWaveOscilloscope();

  // Export global pour interaction
  window.NeuroVision = {
    start: startClientCamera,
    stop: stopClientCamera,
    toggle: toggleCamera,
    switchSource: switchSource
  };
})();

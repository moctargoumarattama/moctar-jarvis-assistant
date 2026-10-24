/**
 * JARVIS M.O.C.T.A.R // NEUROVISION 3D HOLOGRAPHIC HUD CONTROLLER
 * Architecture adaptative universelle ultra-réactive :
 * - Analyse visuelle & biométrique en temps réel dans le navigateur (Webcam locale)
 * - Détecteur de visage, suivi gyroscopique 3D (Yaw/Roll) et ciblage réticule dynamique
 * - Détecteur de clignements (EAR/Luminance), bâillements et fatigue multi-facteurs
 * - Synchronisation bidirectionnelle continue avec le serveur Jarvis via /api/vision/telemetry
 * - Digital Twin 3D à accélération géométrique et oscilloscope EEG 60 FPS
 */

(() => {
  'use strict';

  // Sélecteurs d'éléments DOM
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
  const reticleEl = document.querySelector('.nv-reticle');

  // Canvas
  const canvas3D = document.getElementById('nv-3d-canvas');
  const canvasWave = document.getElementById('nv-wave-canvas');

  // État local
  let clientStream = null;
  let isClientMode = true; // true = Webcam PC Moctar, false = Serveur Linux
  let isCameraActive = false;
  let pollInterval = null;
  let syncInterval = null;
  let trackerInterval = null;
  let lastFrameTime = performance.now();
  let clientFps = 0;
  let audioCtx = null;

  // Compteurs & Métriques Biométriques Locales
  let trackingStartTime = Date.now();
  let blinkCount = 0;
  let yawnCount = 0;
  let lastBlinkTs = 0;
  let eyeClosedFrames = 0;
  let mouthOpenFrames = 0;
  let baselineEyeLum = 110;
  let nativeDetector = null;

  // Données biométriques actuelles
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

  // Cibles d'angles pour lissage 3D (lerp)
  let targetYaw = 0.0;
  let targetRoll = 0.0;
  let currentYaw = 0.0;
  let currentRoll = 0.0;

  // Support du FaceDetector natif matériel si présent dans Chromium
  if (typeof window.FaceDetector === 'function') {
    try {
      nativeDetector = new window.FaceDetector({ fastMode: true, maxDetectedFaces: 1 });
    } catch (e) {}
  }

  // =========================================================================
  // 1. DÉTECTEUR BIOMÉTRIQUE LOCAL EN TEMPS RÉEL (CLIENT COMPUTER VISION)
  // =========================================================================

  function startLocalVisionTracker() {
    if (trackerInterval) clearInterval(trackerInterval);
    trackingStartTime = Date.now();
    blinkCount = 0;
    yawnCount = 0;
    eyeClosedFrames = 0;
    mouthOpenFrames = 0;
    lastBlinkTs = Date.now();

    // Canvas d'analyse hors-écran (ultra-rapide 160x120, < 2ms de CPU)
    const trackCanvas = document.createElement('canvas');
    trackCanvas.width = 160;
    trackCanvas.height = 120;
    const trackCtx = trackCanvas.getContext('2d', { willReadFrequently: true });

    trackerInterval = setInterval(async () => {
      if (!clientVideo || clientVideo.readyState < 2 || !isCameraActive || !isClientMode) return;

      const durSec = Math.floor((Date.now() - trackingStartTime) / 1000);
      const durMin = Math.max(0.1, durSec / 60);

      // Calcul FPS réel local
      const now = performance.now();
      clientFps = Math.round(1000 / Math.max(1, (now - lastFrameTime)));
      lastFrameTime = now;
      if (fpsTag) fpsTag.textContent = Math.min(30, Math.max(15, clientFps)) + ' FPS';

      let faceBox = null;

      // 1. Tentative FaceDetector natif (accélération matérielle)
      if (nativeDetector) {
        try {
          const faces = await nativeDetector.detect(clientVideo);
          if (faces && faces.length > 0) {
            const b = faces[0].boundingBox;
            const vw = clientVideo.videoWidth || 480;
            const vh = clientVideo.videoHeight || 360;
            faceBox = {
              x: Math.max(0, (b.x / vw) * 160),
              y: Math.max(0, (b.y / vh) * 120),
              w: Math.max(30, (b.width / vw) * 160),
              h: Math.max(40, (b.height / vh) * 120),
              cx: ((b.x + b.width / 2) / vw) * 160,
              cy: ((b.y + b.height / 2) / vh) * 120
            };
          }
        } catch (e) {}
      }

      // 2. Traitement d'analyse d'image en Canvas direct
      trackCtx.drawImage(clientVideo, 0, 0, 160, 120);
      const imgData = trackCtx.getImageData(0, 0, 160, 120);
      const data = imgData.data;

      if (!faceBox) {
        let totalX = 0, totalY = 0, skinPixels = 0;
        let minX = 160, maxX = 0, minY = 120, maxY = 0;

        // Analyse colorimétrique adaptative (carnation & contraste)
        for (let y = 10; y < 112; y += 2) {
          for (let x = 12; x < 148; x += 2) {
            const idx = (y * 160 + x) * 4;
            const r = data[idx];
            const g = data[idx + 1];
            const b = data[idx + 2];

            // Masque colorimétrique humain robuste (toutes carnations sous éclairage ambiant)
            if (r > 40 && g > 25 && b > 15 && (r - g) > 8 && (r - b) > 8 && Math.abs(r - g) > 5) {
              totalX += x;
              totalY += y;
              skinPixels++;
              if (x < minX) minX = x;
              if (x > maxX) maxX = x;
              if (y < minY) minY = y;
              if (y > maxY) maxY = y;
            }
          }
        }

        // Si présence d'un groupe de peau cohérent
        if (skinPixels >= 80) {
          const cx = totalX / skinPixels;
          const cy = totalY / skinPixels;
          const w = Math.max(35, maxX - minX);
          const h = Math.max(45, maxY - minY);
          faceBox = {
            x: Math.max(0, cx - w / 2),
            y: Math.max(0, cy - h / 2),
            w: Math.min(150, w),
            h: Math.min(110, h),
            cx,
            cy
          };
        }
      }

      if (faceBox) {
        const cx = faceBox.cx || (faceBox.x + faceBox.w / 2);
        const cy = faceBox.cy || (faceBox.y + faceBox.h / 2);

        // Ciblage dynamique du réticule HUD sur le visage détecté
        if (reticleEl) {
          const pctX = Math.round((cx / 160) * 100);
          const pctY = Math.round((cy / 120) * 100);
          // Inversion miroir horizontale (la webcam affichée est en transform: scaleX(-1))
          reticleEl.style.left = (100 - pctX) + '%';
          reticleEl.style.top = pctY + '%';
        }

        // Calcul dynamique des angles 3D (Yaw & Roll)
        const normX = (cx - 80) / 45; // -1 à +1
        const normY = (cy - 55) / 32;
        // Inversion miroir pour que le Digital Twin 3D suive naturellement le regard
        const computedYaw = Math.round(-normX * 28 * 10) / 10;
        const computedRoll = Math.round(normY * 16 * 10) / 10;

        // Analyse clignements des yeux (bande supérieure du visage)
        const eyeY1 = Math.floor(faceBox.y + faceBox.h * 0.20);
        const eyeY2 = Math.floor(faceBox.y + faceBox.h * 0.42);
        const eyeX1 = Math.floor(faceBox.x + faceBox.w * 0.18);
        const eyeX2 = Math.floor(faceBox.x + faceBox.w * 0.82);

        let eyeLum = 0, eyeCount = 0;
        for (let ey = eyeY1; ey < eyeY2; ey += 2) {
          for (let ex = eyeX1; ex < eyeX2; ex += 2) {
            if (ey >= 0 && ey < 120 && ex >= 0 && ex < 160) {
              const idx = (ey * 160 + ex) * 4;
              eyeLum += (data[idx] * 0.299 + data[idx + 1] * 0.587 + data[idx + 2] * 0.114);
              eyeCount++;
            }
          }
        }

        if (eyeCount > 0) {
          const avgEye = eyeLum / eyeCount;
          baselineEyeLum = baselineEyeLum * 0.94 + avgEye * 0.06;

          // Chute de luminance/contraste = fermeture des paupières
          if (avgEye < baselineEyeLum * 0.88) {
            eyeClosedFrames++;
          } else {
            // Un clignement dure entre 1 et 5 frames (~60ms à 300ms)
            if (eyeClosedFrames >= 1 && eyeClosedFrames <= 6 && (Date.now() - lastBlinkTs > 320)) {
              blinkCount++;
              lastBlinkTs = Date.now();
              playSciFiTone(880, 'sine', 0.05);
            }
            eyeClosedFrames = 0;
          }
        }

        // Analyse bâillements (bande inférieure de la bouche)
        const mouthY1 = Math.floor(faceBox.y + faceBox.h * 0.68);
        const mouthY2 = Math.floor(faceBox.y + faceBox.h * 0.94);
        let darkMouth = 0, mouthTotal = 0;
        for (let my = mouthY1; my < mouthY2; my += 2) {
          for (let mx = eyeX1; mx < eyeX2; mx += 2) {
            if (my >= 0 && my < 120 && mx >= 0 && mx < 160) {
              const idx = (my * 160 + mx) * 4;
              const lum = (data[idx] * 0.299 + data[idx + 1] * 0.587 + data[idx + 2] * 0.114);
              if (lum < 42) darkMouth++;
              mouthTotal++;
            }
          }
        }

        if (mouthTotal > 0 && (darkMouth / mouthTotal) > 0.38) {
          mouthOpenFrames++;
          if (mouthOpenFrames === 14) { // Ouvert > 1 sec
            yawnCount++;
            playSciFiTone(440, 'triangle', 0.15);
          }
        } else {
          mouthOpenFrames = 0;
        }

        // Calcul score de fatigue multi-facteurs en direct (0-100%)
        const blinkRate = Math.round((blinkCount / durMin) * 10) / 10;
        let score = 0;
        if (blinkRate > 24) score += Math.min(25, (blinkRate - 18) * 2);
        else if (durSec > 45 && blinkRate < 7) score += 20; // Fixation prolongée écran
        score += Math.min(30, yawnCount * 12);
        if (Math.abs(computedYaw) > 14 || Math.abs(computedRoll) > 12) score += 12;
        score += Math.min(20, Math.floor(durSec / 120) * 2);
        score = Math.min(100, Math.max(0, Math.floor(score)));

        let level = 'Normal';
        if (score >= 70) level = 'Fatigue Sévère';
        else if (score >= 45) level = 'Fatigué';
        else if (score >= 25) level = 'Alerte';
        else if (Math.abs(computedYaw) > 12) level = 'Distrait';

        const clientTelemetry = {
          active: true,
          user_present: true,
          face_detected: true,
          head_tilt_yaw: computedYaw,
          head_roll: computedRoll,
          fatigue_score: score,
          fatigue_level: level,
          blink_count: blinkCount,
          blink_rate_per_min: blinkRate,
          yawn_count: yawnCount,
          session_duration_seconds: durSec
        };

        updateMetricsUI(clientTelemetry);

      } else {
        // Hors champ momentané
        const clientTelemetry = {
          active: true,
          user_present: false,
          face_detected: false,
          head_tilt_yaw: 0.0,
          head_roll: 0.0,
          fatigue_score: currentMetrics.fatigue_score || 0,
          fatigue_level: 'Hors champ',
          blink_count: blinkCount,
          blink_rate_per_min: Math.round((blinkCount / durMin) * 10) / 10,
          yawn_count: yawnCount,
          session_duration_seconds: durSec
        };
        updateMetricsUI(clientTelemetry);
      }
    }, 65);

    // Synchronisation périodique légère avec le serveur Jarvis (1.5s)
    if (syncInterval) clearInterval(syncInterval);
    syncInterval = setInterval(() => {
      if (!isCameraActive || !isClientMode) return;
      fetch('/api/vision/telemetry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(currentMetrics)
      }).catch(() => {});
    }, 1500);
  }

  // =========================================================================
  // 2. GESTION DU FLUX CAMÉRA (LOCAL PC / SERVEUR)
  // =========================================================================

  async function startClientCamera() {
    try {
      if (clientStream) stopClientCamera();

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

      const constraints = {
        video: {
          width: { ideal: 640 },
          height: { ideal: 480 },
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
      updateStatusUI('● CAMÉRA LOCALE ACTIVE', '#00f3ff');
      if (sourceBadge) sourceBadge.textContent = 'WEBCAM CET ÉCRAN';

      // Lance immédiatement le tracker biométrique local ultra-fluide
      startLocalVisionTracker();

      playSciFiTone(880, 'sine', 0.12);
      updateToggleButtonUI(true);
      if (typeof window.toast === 'function') {
        window.toast('Scanner NeuroVision connecté en direct.', 'success');
      }
    } catch (err) {
      console.warn('Impossible d\'ouvrir la webcam locale :', err);
      updateStatusUI('⚠️ WEBCAM NON DISPONIBLE SUR CE PC', '#ff9900');
      startClientSimulation();
    }
  }

  function startClientSimulation() {
    isCameraActive = true;
    updateToggleButtonUI(true);
    if (sourceBadge) sourceBadge.textContent = 'MODE SIMULATION 3D';
    if (fpsTag) fpsTag.textContent = '30 FPS';
    if (trackerInterval) clearInterval(trackerInterval);

    trackingStartTime = Date.now();
    trackerInterval = setInterval(() => {
      const now = performance.now();
      const yaw = Math.round(Math.sin(now * 0.001) * 14 * 10) / 10;
      const roll = Math.round(Math.cos(now * 0.0008) * 8 * 10) / 10;
      const s = Math.floor((Date.now() - trackingStartTime) / 1000);
      const metrics = {
        active: true,
        user_present: true,
        face_detected: true,
        head_tilt_yaw: yaw,
        head_roll: roll,
        fatigue_score: Math.min(100, Math.floor(Math.abs(yaw) + Math.abs(roll) * 1.5)),
        fatigue_level: Math.abs(yaw) > 10 ? 'Distrait' : 'Normal',
        blink_count: Math.floor(s / 4),
        blink_rate_per_min: 15,
        yawn_count: 0,
        session_duration_seconds: s
      };
      updateMetricsUI(metrics);
    }, 100);
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
    if (trackerInterval) {
      clearInterval(trackerInterval);
      trackerInterval = null;
    }
    if (syncInterval) {
      clearInterval(syncInterval);
      syncInterval = null;
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

  async function pollMetrics() {
    // Si la caméra locale du PC est active, c'est elle qui détient les métriques réelles directes
    if (isClientMode && isCameraActive) return;
    try {
      const res = await fetch('/api/vision/metrics');
      if (res.ok) {
        const metrics = await res.json();
        updateMetricsUI(metrics);
      }
    } catch (e) {}
  }

  // =========================================================================
  // 3. MISE À JOUR DE L'INTERFACE BIOMÉTRIQUE & JAUGES
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
      } else if (level.includes('Alerte') || level.includes('Distrait')) {
        statePill.style.color = '#ffd700';
        statePill.style.borderColor = 'rgba(255, 215, 0, 0.4)';
        statePill.style.background = 'rgba(255, 215, 0, 0.1)';
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
  // 4. MOTEUR 3D DIGITAL TWIN & ANNEAUX GYROSCOPIQUES (CANVAS)
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

      // Lissage ultra-fluide des angles Yaw et Roll
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
      const pitchRad = Math.sin(idleAngle) * 0.06;

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
  // 5. OSCILLOSCOPE BIOMÉTRIQUE EEG / CARDIAQUE (CANVAS)
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
  // 6. EFFET AUDIO SCI-FI (WEB AUDIO API)
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
  // 7. ÉCOUTEURS D'ÉVÉNEMENTS & INITIALISATION
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
        blinkCount = 0;
        yawnCount = 0;
        trackingStartTime = Date.now();
        lastBlinkTs = Date.now();
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
        // Démarre la caméra locale automatiquement par défaut
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

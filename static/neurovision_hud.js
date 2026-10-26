/**
 * JARVIS M.O.C.T.A.R // NEUROVISION 3D HOLOGRAPHIC HUD CONTROLLER
 * Architecture adaptative universelle ultra-réactive (v5.8) :
 * - Analyse visuelle & biométrique en temps réel dans le navigateur (Webcam locale PC)
 * - Détecteur de visage multi-carnations (YCbCr + Silhouette Avant-Plan + Micro-mouvements)
 * - Suivi gyroscopique 3D temps réel (Yaw/Roll) et ciblage réticule dynamique
 * - Détecteur de clignements (variation temporelle & luminance oculaire) et bâillements
 * - Synchronisation bidirectionnelle continue avec le serveur Jarvis via /api/vision/telemetry (incluant snapshot JPEG pour Gemini Vision)
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
  let cameraSessionToken = 0;
  let isTelemetryUploadInFlight = false;
  let raf3DId = null;
  let rafWaveId = null;
  let pollInterval = null;
  let syncInterval = null;
  let trackerInterval = null;
  let lastFrameTime = performance.now();
  let smoothedFps = 0;
  let audioCtx = null;

  function isNeuroVisionModalOpen() {
    if (dialog && dialog.open) return true;
    const ov = document.getElementById('hud-neurovision-overlay');
    if (ov && ov.classList.contains('active')) return true;
    return false;
  }

  // Compteurs & Métriques Biométriques Locales
  let trackingStartTime = Date.now();
  let blinkCount = 0;
  let yawnCount = 0;
  let lastBlinkTs = 0;
  let lastYawnTs = 0;
  let eyeClosedFrames = 0;
  let mouthOpenFrames = 0;
  let baselineEyeLum = -1;
  let prevEyeLums = null;
  let prevFrameLums = null;
  let lastValidFaceBox = null;
  let lastFaceSeenTs = 0;

  // Données biométriques actuelles
  let currentMetrics = {
    active: false,
    fatigue_score: 0,
    fatigue_level: 'Normal',
    blink_count: 0,
    blink_rate_per_min: 0,
    yawn_count: 0,
    head_tilt_yaw: 0.0,
    head_roll: 0.0,
    user_present: false,
    face_detected: false,
    measured_fps: 25,
    session_duration_seconds: 0
  };

  // Cibles d'angles pour lissage 3D (lerp)
  let targetYaw = 0.0;
  let targetRoll = 0.0;
  let currentYaw = 0.0;
  let currentRoll = 0.0;

  // =========================================================================
  // 1. DÉTECTEUR BIOMÉTRIQUE LOCAL EN TEMPS RÉEL (CLIENT COMPUTER VISION 25 FPS)
  // =========================================================================

  function startLocalVisionTracker() {
    if (trackerInterval) clearInterval(trackerInterval);
    trackingStartTime = Date.now();
    blinkCount = 0;
    yawnCount = 0;
    eyeClosedFrames = 0;
    mouthOpenFrames = 0;
    baselineEyeLum = -1;
    prevEyeLums = null;
    prevFrameLums = new Float32Array(160 * 120);
    lastBlinkTs = Date.now() - 1000;
    lastYawnTs = Date.now() - 3000;
    lastValidFaceBox = null;
    lastFaceSeenTs = Date.now();
    lastFrameTime = performance.now();

    // Canvas d'analyse hors-écran ultra-rapide (160x120, < 1.2ms CPU)
    const trackCanvas = document.createElement('canvas');
    trackCanvas.width = 160;
    trackCanvas.height = 120;
    const trackCtx = trackCanvas.getContext('2d', { willReadFrequently: true });

    // Canvas secondaire pour snapshot JPEG ultra-léger envoyé à Gemini Vision (240x180, ~6 Ko)
    const snapCanvas = document.createElement('canvas');
    snapCanvas.width = 240;
    snapCanvas.height = 180;
    const snapCtx = snapCanvas.getContext('2d');

    function pushTelemetryToServer(includeSnapshot) {
      if (!isCameraActive || !isClientMode || isTelemetryUploadInFlight) return;
      const myToken = cameraSessionToken;
      const payload = Object.assign({}, currentMetrics, {
        measured_fps: smoothedFps
      });
      if (includeSnapshot && clientVideo && clientVideo.readyState >= 2) {
        try {
          snapCtx.drawImage(clientVideo, 0, 0, 240, 180);
          payload.image_b64 = snapCanvas.toDataURL('image/jpeg', 0.62);
        } catch (e) {}
      }
      if (myToken !== cameraSessionToken || !isCameraActive) return;
      isTelemetryUploadInFlight = true;
      fetch('/api/vision/telemetry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
        .catch(() => {})
        .finally(() => {
          isTelemetryUploadInFlight = false;
        });
    }

    trackerInterval = setInterval(() => {
      if (!clientVideo || clientVideo.readyState < 2 || !isCameraActive || !isClientMode) return;

      const nowMs = Date.now();
      const durSec = Math.max(1, Math.floor((nowMs - trackingStartTime) / 1000));
      const durMin = Math.max(0.15, durSec / 60);

      // Calcul FPS réel lissé (sans blocage IPC)
      const nowPerf = performance.now();
      const instantFps = 1000 / Math.max(16, nowPerf - lastFrameTime);
      lastFrameTime = nowPerf;
      smoothedFps = Math.min(30, Math.max(18, Math.round(smoothedFps * 0.8 + instantFps * 0.2)));
      if (fpsTag) fpsTag.textContent = smoothedFps + ' FPS';

      // Capture de la frame 160x120
      try {
        trackCtx.drawImage(clientVideo, 0, 0, 160, 120);
      } catch (e) {
        return;
      }
      const imgData = trackCtx.getImageData(0, 0, 160, 120);
      const data = imgData.data;

      // Estimation de la luminance d'arrière-plan (coins supérieurs gauche et droit)
      let bgLumSum = 0;
      let bgCount = 0;
      for (let by = 2; by < 22; by += 4) {
        for (let bx = 4; bx < 32; bx += 4) {
          const i1 = (by * 160 + bx) * 4;
          const i2 = (by * 160 + (159 - bx)) * 4;
          bgLumSum += (data[i1] * 0.299 + data[i1 + 1] * 0.587 + data[i1 + 2] * 0.114);
          bgLumSum += (data[i2] * 0.299 + data[i2 + 1] * 0.587 + data[i2 + 2] * 0.114);
          bgCount += 2;
        }
      }
      const bgLum = bgCount > 0 ? bgLumSum / bgCount : 175;

      // Détection hybride multi-carnations (YCbCr + contraste avant-plan + mouvement)
      let weightedX = 0;
      let weightedY = 0;
      let totalWeight = 0;
      let leftHalfWeight = 0;
      let rightHalfWeight = 0;
      let leftMeanY = 0;
      let rightMeanY = 0;
      let minX = 160, maxX = 0, minY = 120, maxY = 0;
      let motionEnergy = 0;

      for (let y = 8; y < 114; y += 2) {
        for (let x = 14; x < 146; x += 2) {
          const pIdx = y * 160 + x;
          const idx = pIdx * 4;
          const r = data[idx];
          const g = data[idx + 1];
          const b = data[idx + 2];

          const lum = 0.299 * r + 0.587 * g + 0.114 * b;
          const prevL = prevFrameLums[pIdx] || lum;
          const diffL = Math.abs(lum - prevL);
          prevFrameLums[pIdx] = lum;
          if (diffL > 3.5) motionEnergy += diffL;

          // Conversion YCbCr universelle (toutes carnations : foncée, mate, claire, éclairage chaud/froid)
          const cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b;
          const cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b;

          const isYCbCrSkin = (lum > 14 && lum < 235 && cr >= 122 && cr <= 186 && cb >= 70 && cb <= 142);
          const isWarmTone = (lum > 16 && lum < 225 && r >= g - 6 && g >= b - 12 && (r - b) >= 4);
          // Silhouette centrale contrastant avec le mur d'arrière-plan (y compris en contre-jour)
          const inCenterZone = (x >= 22 && x <= 138 && y >= 10 && y <= 114);
          const isForegroundContrast = inCenterZone && (Math.abs(bgLum - lum) > 16 && lum < 185);

          let w = 0;
          if (isYCbCrSkin) w += 2.2;
          if (isWarmTone) w += 1.5;
          if (isForegroundContrast) w += 1.6;
          if (inCenterZone && diffL > 3.5) w += 1.5;

          if (w >= 1.5) {
            weightedX += x * w;
            weightedY += y * w;
            totalWeight += w;
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
            if (x < 80) {
              leftHalfWeight += w;
              leftMeanY += y * w;
            } else {
              rightHalfWeight += w;
              rightMeanY += y * w;
            }
          }
        }
      }

      let faceBox = null;
      if (totalWeight >= 42) {
        const cx = weightedX / totalWeight;
        const cy = weightedY / totalWeight;
        const rawW = Math.max(44, Math.min(115, (maxX - minX) * 0.85));
        const rawH = Math.max(54, Math.min(102, (maxY - minY) * 0.88));
        faceBox = {
          x: Math.max(6, cx - rawW / 2),
          y: Math.max(4, cy - rawH / 2),
          w: rawW,
          h: rawH,
          cx: cx,
          cy: cy
        };
        lastValidFaceBox = faceBox;
        lastFaceSeenTs = nowMs;
      } else if (lastValidFaceBox && (nowMs - lastFaceSeenTs) < 2500) {
        // Maintien anti-scintillement si l'utilisateur bouge rapidement
        faceBox = lastValidFaceBox;
      }

      if (faceBox) {
        const cx = faceBox.cx;
        const cy = faceBox.cy;

        // 1. Ciblage dynamique du réticule HUD sur le visage détecté
        if (reticleEl) {
          const pctX = Math.min(82, Math.max(18, Math.round((cx / 160) * 100)));
          const pctY = Math.min(78, Math.max(22, Math.round((cy / 120) * 100)));
          // Inversion miroir horizontale (la vidéo locale est en scaleX(-1))
          reticleEl.style.left = (100 - pctX) + '%';
          reticleEl.style.top = pctY + '%';
        }

        // 2. Calcul dynamique et sensible des angles 3D (Yaw & Roll)
        const asymRatio = (rightHalfWeight - leftHalfWeight) / Math.max(1, totalWeight);
        const rawYaw = -((cx - 80) / 34) * 24 - (asymRatio * 14);
        const lY = leftHalfWeight > 0 ? (leftMeanY / leftHalfWeight) : cy;
        const rY = rightHalfWeight > 0 ? (rightMeanY / rightHalfWeight) : cy;
        const eyeSlopeRoll = ((rY - lY) / 28) * 18;
        const verticalPitchRoll = ((cy - 58) / 30) * 12;
        const rawRoll = eyeSlopeRoll + verticalPitchRoll * 0.5;

        const computedYaw = Math.max(-35, Math.min(35, Math.round(rawYaw * 10) / 10));
        const computedRoll = Math.max(-25, Math.min(25, Math.round(rawRoll * 10) / 10));

        // 3. Luminance moyenne du visage pour normaliser yeux et bouche
        let faceLumSum = 0, faceLumCnt = 0;
        const fx1 = Math.max(0, Math.floor(faceBox.x + faceBox.w * 0.2));
        const fx2 = Math.min(159, Math.floor(faceBox.x + faceBox.w * 0.8));
        const fy1 = Math.max(0, Math.floor(faceBox.y + faceBox.h * 0.2));
        const fy2 = Math.min(119, Math.floor(faceBox.y + faceBox.h * 0.8));
        for (let fy = fy1; fy < fy2; fy += 3) {
          for (let fx = fx1; fx < fx2; fx += 3) {
            const idx = (fy * 160 + fx) * 4;
            faceLumSum += (data[idx] * 0.299 + data[idx + 1] * 0.587 + data[idx + 2] * 0.114);
            faceLumCnt++;
          }
        }
        const avgFaceLum = faceLumCnt > 0 ? (faceLumSum / faceLumCnt) : 95;

        // 4. Analyse de clignements des yeux (bande oculaire supérieure + delta temporel)
        const eyeY1 = Math.max(0, Math.floor(faceBox.y + faceBox.h * 0.22));
        const eyeY2 = Math.min(119, Math.floor(faceBox.y + faceBox.h * 0.45));
        const eyeX1 = Math.max(0, Math.floor(faceBox.x + faceBox.w * 0.18));
        const eyeX2 = Math.min(159, Math.floor(faceBox.x + faceBox.w * 0.82));

        let eyeLumSum = 0, eyeCount = 0, eyeDarkPixels = 0;
        for (let ey = eyeY1; ey < eyeY2; ey += 2) {
          for (let ex = eyeX1; ex < eyeX2; ex += 2) {
            const idx = (ey * 160 + ex) * 4;
            const lum = (data[idx] * 0.299 + data[idx + 1] * 0.587 + data[idx + 2] * 0.114);
            eyeLumSum += lum;
            if (lum < avgFaceLum * 0.72) eyeDarkPixels++;
            eyeCount++;
          }
        }

        if (eyeCount > 0) {
          const avgEye = eyeLumSum / eyeCount;
          const darkEyeRatio = eyeDarkPixels / eyeCount;
          if (baselineEyeLum < 0) {
            baselineEyeLum = avgEye;
            prevEyeLums = darkEyeRatio;
          } else {
            const relLumDelta = Math.abs(avgEye - baselineEyeLum) / Math.max(15, baselineEyeLum);
            const darkRatioDelta = Math.abs(darkEyeRatio - (prevEyeLums || darkEyeRatio));
            prevEyeLums = darkEyeRatio * 0.6 + (prevEyeLums || darkEyeRatio) * 0.4;

            // Détecte la fermeture/réouverture rapide des paupières sans faux positif de grand mouvement de tête
            const isEyeTransiting = (relLumDelta > 0.032 || darkRatioDelta > 0.045) && motionEnergy < 4200;
            if (isEyeTransiting) {
              eyeClosedFrames++;
            } else {
              if (eyeClosedFrames >= 1 && eyeClosedFrames <= 7 && (nowMs - lastBlinkTs > 360)) {
                blinkCount++;
                lastBlinkTs = nowMs;
                playSciFiTone(880, 'sine', 0.04);
              }
              eyeClosedFrames = 0;
              baselineEyeLum = baselineEyeLum * 0.92 + avgEye * 0.08;
            }
          }
        }

        // 5. Analyse des bâillements (ouverture prolongée de la cavité buccale)
        const mouthY1 = Math.max(0, Math.floor(faceBox.y + faceBox.h * 0.64));
        const mouthY2 = Math.min(119, Math.floor(faceBox.y + faceBox.h * 0.92));
        const mouthX1 = Math.max(0, Math.floor(faceBox.x + faceBox.w * 0.28));
        const mouthX2 = Math.min(159, Math.floor(faceBox.x + faceBox.w * 0.72));

        let darkMouth = 0, mouthTotal = 0;
        for (let my = mouthY1; my < mouthY2; my += 2) {
          for (let mx = mouthX1; mx < mouthX2; mx += 2) {
            const idx = (my * 160 + mx) * 4;
            const lum = (data[idx] * 0.299 + data[idx + 1] * 0.587 + data[idx + 2] * 0.114);
            // Cavité buccale ouverte = plus sombre que la carnation moyenne du visage
            if (lum < Math.max(38, avgFaceLum * 0.64)) darkMouth++;
            mouthTotal++;
          }
        }

        if (mouthTotal > 0 && (darkMouth / mouthTotal) > 0.26) {
          mouthOpenFrames++;
          if (mouthOpenFrames >= 12 && (nowMs - lastYawnTs > 2800)) {
            yawnCount++;
            lastYawnTs = nowMs;
            mouthOpenFrames = 0;
            playSciFiTone(440, 'triangle', 0.14);
          }
        } else {
          mouthOpenFrames = Math.max(0, mouthOpenFrames - 1);
        }

        // 6. Calcul du score biométrique & charge cognitive en direct (0-100%)
        const blinkRate = Math.round((blinkCount / durMin) * 10) / 10;
        // Charge cognitive de base active (8% à 14% dès que l'utilisateur est concentré devant l'écran)
        let score = 8 + Math.min(8, Math.round((Math.abs(computedYaw) + Math.abs(computedRoll)) * 0.35));
        if (blinkRate > 22) score += Math.min(28, (blinkRate - 18) * 2.2);
        else if (durSec > 35 && blinkCount === 0) score += 14; // Fixation écran intense
        score += Math.min(36, yawnCount * 15);
        if (Math.abs(computedYaw) > 14 || Math.abs(computedRoll) > 11) score += 12;
        score += Math.min(20, Math.floor(durSec / 90) * 3);
        score = Math.min(100, Math.max(6, Math.floor(score)));

        let level = 'Normal';
        if (score >= 70) level = 'Fatigue Sévère';
        else if (score >= 45) level = 'Fatigué';
        else if (score >= 26) level = 'Alerte';
        else if (Math.abs(computedYaw) > 13) level = 'Distrait';

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
          measured_fps: smoothedFps,
          session_duration_seconds: durSec
        };

        updateMetricsUI(clientTelemetry);
      } else {
        // Hors champ réel (aucun visage devant la caméra)
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
          measured_fps: smoothedFps,
          session_duration_seconds: durSec
        };
        updateMetricsUI(clientTelemetry);
      }
    }, 42); // ~24-25 FPS fluides

    // Premier envoi rapide à 450ms (avec snapshot JPEG pour que Gemini Vision voie Moctar immédiatement)
    setTimeout(() => pushTelemetryToServer(true), 450);

    // Synchronisation périodique toutes les 900ms avec le serveur Jarvis + snapshot JPEG léger
    if (syncInterval) clearInterval(syncInterval);
    syncInterval = setInterval(() => {
      pushTelemetryToServer(true);
    }, 900);
  }

  // =========================================================================
  // 2. GESTION DU FLUX CAMÉRA (LOCAL PC / SERVEUR)
  // =========================================================================

  async function startClientCamera() {
    try {
      if (clientStream) stopClientCamera(false);
      const mySessionToken = ++cameraSessionToken;

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

      // Garde-fou strict : si la modale a été fermée ou si un autre cycle a démarré pendant l'invite de permission
      if (mySessionToken !== cameraSessionToken || !isNeuroVisionModalOpen()) {
        try {
          stream.getTracks().forEach(t => t.stop());
        } catch (e) {}
        return;
      }

      clientStream = stream;

      if (clientVideo) {
        clientVideo.srcObject = stream;
        clientVideo.style.display = 'block';
        await clientVideo.play();
      }
      if (mySessionToken !== cameraSessionToken || !isNeuroVisionModalOpen()) {
        stopClientCamera(true);
        return;
      }
      if (serverImg) {
        serverImg.style.display = 'none';
        serverImg.src = '';
        serverImg.removeAttribute('src');
      }

      isClientMode = true;
      isCameraActive = true;
      updateStatusUI('● CAMÉRA LOCALE (PC MOCTAR)', '#00f3ff');
      if (sourceBadge) sourceBadge.textContent = 'WEBCAM CET ÉCRAN';

      // Lance immédiatement le tracker biométrique local 25 FPS
      startLocalVisionTracker();

      playSciFiTone(880, 'sine', 0.12);
      updateToggleButtonUI(true);
      if (typeof window.toast === 'function') {
        window.toast('Scanner NeuroVision 3D verrouillé sur votre caméra.', 'success');
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
    if (fpsTag) fpsTag.textContent = '25 FPS';
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
        fatigue_score: Math.min(100, Math.max(10, Math.floor(Math.abs(yaw) + Math.abs(roll) * 1.5))),
        fatigue_level: Math.abs(yaw) > 10 ? 'Distrait' : 'Normal',
        blink_count: Math.floor(s / 4),
        blink_rate_per_min: 15,
        yawn_count: 0,
        measured_fps: 25,
        session_duration_seconds: s
      };
      updateMetricsUI(metrics);
    }, 100);
  }

  function stopClientCamera(purgeServer = true) {
    cameraSessionToken++;
    isTelemetryUploadInFlight = false;
    if (clientStream) {
      try {
        clientStream.getTracks().forEach(t => t.stop());
      } catch (e) {}
      clientStream = null;
    }
    if (clientVideo) {
      try { clientVideo.pause(); } catch (e) {}
      clientVideo.srcObject = null;
      clientVideo.style.display = 'none';
    }
    if (serverImg) {
      serverImg.src = '';
      serverImg.removeAttribute('src');
      serverImg.style.display = 'none';
    }
    if (trackerInterval) {
      clearInterval(trackerInterval);
      trackerInterval = null;
    }
    if (syncInterval) {
      clearInterval(syncInterval);
      syncInterval = null;
    }
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
    if (raf3DId !== null) {
      cancelAnimationFrame(raf3DId);
      raf3DId = null;
      is3DInitialized = false;
    }
    if (rafWaveId !== null) {
      cancelAnimationFrame(rafWaveId);
      rafWaveId = null;
      isWaveInitialized = false;
    }
    isCameraActive = false;
    smoothedFps = 0;
    if (fpsTag) fpsTag.textContent = '0 FPS';
    updateStatusUI('○ VEILLE NEUROVISION', '#8c9eb9');
    updateToggleButtonUI(false);
    if (purgeServer) {
      fetch('/api/vision/purge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active: false, purge: true })
      }).catch(() => {});
    }
  }

  function fallbackToServerCamera() {
    stopClientCamera(false);
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
      currentYaw += (targetYaw - currentYaw) * 0.18;
      currentRoll += (targetRoll - currentRoll) * 0.18;
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

      raf3DId = requestAnimationFrame(render3D);
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

      rafWaveId = requestAnimationFrame(renderWave);
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
      setTimeout(resizeCanvases, 60);
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
        lastYawnTs = Date.now();
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

  // =========================================================================
  // 8. BANDEAU D'ÉTAT VOCAL & COGNITIF TEMPS RÉEL (DANS NEUROVISION & NOC)
  // =========================================================================
  function initVoiceFeedbackUI() {
    let nvBar = document.getElementById('nv-voice-live-bar');
    const nvGrid = document.querySelector('#neurovision-dialog .nv-grid, #hud-neurovision-overlay .nv-grid');
    if (!nvBar && nvGrid && nvGrid.parentNode) {
      nvBar = document.createElement('div');
      nvBar.id = 'nv-voice-live-bar';
      nvBar.className = 'nv-voice-live-bar';
      nvBar.setAttribute('data-vstate', 'idle');
      nvBar.innerHTML = [
        '<div class="nv-voice-live-left">',
        '  <span class="nv-voice-badge-pill"><span class="nv-voice-spinner" id="nv-voice-spin"></span><span id="nv-voice-badge-label">● CANAL VOCAL ACTIF</span></span>',
        '  <span class="nv-voice-live-text" id="nv-voice-live-msg">Dites « Analyse mon visage » ou posez votre question à M.O.C.T.A.R...</span>',
        '</div>'
      ].join('');
      nvGrid.parentNode.insertBefore(nvBar, nvGrid);
    }

    // Sur le NOC, on déplace la pilule lumineuse verte directement à côté du bouton
    // "Activer le micro / Couper le micro" (#home-voice-status) sans doublon en haut d'écran
    const nocVoicePill = document.getElementById('home-voice-status');
    let globalToast = null;
    if (nocVoicePill) {
      const strayToast = document.getElementById('moctar-global-voice-toast');
      if (strayToast && strayToast !== nocVoicePill) strayToast.remove();
      nocVoicePill.classList.add('moctar-noc-voice-pill');
      if (!nocVoicePill.querySelector('#moctar-gvt-badge')) {
        const currentTxt = nocVoicePill.textContent || 'Mains libres sur cet appareil';
        nocVoicePill.setAttribute('data-vstate', 'off');
        nocVoicePill.innerHTML = '<span id="moctar-gvt-badge">🎙️ MICRO</span><span id="moctar-gvt-text"></span>';
        const txtSpan = nocVoicePill.querySelector('#moctar-gvt-text');
        if (txtSpan) txtSpan.textContent = currentTxt;
      }
      globalToast = nocVoicePill;
    } else if (document.body) {
      globalToast = document.getElementById('moctar-global-voice-toast');
      if (!globalToast) {
        globalToast = document.createElement('div');
        globalToast.id = 'moctar-global-voice-toast';
        globalToast.setAttribute('data-vstate', 'idle');
        globalToast.innerHTML = '<span id="moctar-gvt-badge">🎙️</span><span id="moctar-gvt-text"></span>';
        document.body.appendChild(globalToast);
      }
    }

    let lastHeardAck = '';
    let hideToastTimer = null;

    window.addEventListener('moctar:voice-state', (ev) => {
      const d = (ev && ev.detail) || {};
      const st = d.state || 'idle';
      const badgeLbl = document.getElementById('nv-voice-badge-label');
      const liveMsg = document.getElementById('nv-voice-live-msg');
      const gvtBadge = document.getElementById('moctar-gvt-badge');
      const gvtText = document.getElementById('moctar-gvt-text');
      const isNoc = Boolean(nocVoicePill);
      const micActive = Boolean(
        window.jarvisVoice && (window.jarvisVoice.listeningDesired || window.jarvisVoice.enabled)
      );

      if (hideToastTimer) {
        clearTimeout(hideToastTimer);
        hideToastTimer = null;
      }

      if (st === 'listening' && (d.isSpeaking || isNoc)) {
        if (nvBar) nvBar.setAttribute('data-vstate', 'listening');
        if (badgeLbl) badgeLbl.textContent = '🎙️ ÉCOUTE EN DIRECT';
        if (liveMsg) liveMsg.textContent = 'Vous parlez — M.O.C.T.A.R vous écoute attentivement...';
        if (globalToast) {
          globalToast.setAttribute('data-vstate', 'listening');
          if (gvtBadge) gvtBadge.textContent = '🎙️ ÉCOUTE';
          if (gvtText) gvtText.textContent = 'Parlez, je vous écoute...';
        }
      } else if (isNoc && (st === 'wake' || st === 'starting')) {
        if (globalToast) {
          globalToast.setAttribute('data-vstate', 'listening');
          if (gvtBadge) gvtBadge.textContent = '🎙️ ÉCOUTE';
          if (gvtText) gvtText.textContent = 'Parlez, je vous écoute...';
        }
      } else if (st === 'processing') {
        if (nvBar) nvBar.setAttribute('data-vstate', 'processing');
        if (d.transcript) {
          if (d.transcript !== lastHeardAck) {
            lastHeardAck = d.transcript;
            playSciFiTone(660, 'sine', 0.06);
            setTimeout(() => playSciFiTone(990, 'sine', 0.07), 65);
          }
          if (badgeLbl) badgeLbl.textContent = '🧠 RÉFLEXION & ANALYSE IA';
          if (liveMsg) liveMsg.textContent = '✅ Entendu : « ' + d.transcript + ' » — Analyse en cours...';
          if (globalToast) {
            globalToast.setAttribute('data-vstate', 'processing');
            if (gvtBadge) gvtBadge.textContent = '🧠 ANALYSE IA';
            if (gvtText) gvtText.textContent = 'Entendu : « ' + d.transcript + ' »';
          }
        } else {
          lastHeardAck = '';
          if (badgeLbl) badgeLbl.textContent = '⏳ TRANSCRIPTION AUDIO';
          if (liveMsg) liveMsg.textContent = 'Réception audio terminée — Transcription et analyse en cours...';
          if (globalToast) {
            globalToast.setAttribute('data-vstate', 'processing');
            if (gvtBadge) gvtBadge.textContent = '⏳ AUDIO REÇU';
            if (gvtText) gvtText.textContent = 'Transcription & réflexion en cours...';
          }
        }
      } else if (st === 'speaking') {
        if (nvBar) nvBar.setAttribute('data-vstate', 'speaking');
        if (badgeLbl) badgeLbl.textContent = '🔊 RÉPONSE M.O.C.T.A.R';
        const rep = d.reply || d.message || '';
        const prefix = d.transcript ? ('« ' + d.transcript + ' » ➔ ') : '';
        if (liveMsg) liveMsg.textContent = prefix + rep;
        if (globalToast) {
          globalToast.setAttribute('data-vstate', 'speaking');
          if (gvtBadge) gvtBadge.textContent = '🔊 M.O.C.T.A.R';
          if (gvtText) gvtText.textContent = rep.slice(0, 110) + (rep.length > 110 ? '…' : '');
          hideToastTimer = setTimeout(() => {
            if (!globalToast) return;
            if (isNoc) {
              const stillActive = Boolean(
                window.jarvisVoice && (window.jarvisVoice.listeningDesired || window.jarvisVoice.enabled)
              );
              globalToast.setAttribute('data-vstate', stillActive ? 'listening' : 'off');
              if (gvtBadge) gvtBadge.textContent = stillActive ? '🎙️ ÉCOUTE' : '🎙️ MICRO';
              if (gvtText) gvtText.textContent = stillActive ? 'Parlez, je vous écoute...' : 'Mains libres sur cet appareil';
            } else {
              globalToast.setAttribute('data-vstate', 'idle');
            }
          }, 5500);
        }
      } else if (isNoc && (st === 'error' || st === 'unsupported')) {
        if (globalToast) {
          globalToast.setAttribute('data-vstate', 'error');
          if (gvtBadge) gvtBadge.textContent = '⚠️ MICRO';
          if (gvtText) gvtText.textContent = d.message || 'Écoute interrompue. Réactivez le micro.';
        }
      } else {
        if (nvBar && nvBar.getAttribute('data-vstate') !== 'speaking') {
          nvBar.setAttribute('data-vstate', 'idle');
          if (badgeLbl) badgeLbl.textContent = '● CANAL VOCAL ACTIF';
        }
        if (globalToast) {
          if (isNoc) {
            if (micActive && st !== 'off' && st !== 'paused') {
              globalToast.setAttribute('data-vstate', 'listening');
              if (gvtBadge) gvtBadge.textContent = '🎙️ ÉCOUTE';
              if (gvtText) gvtText.textContent = 'Parlez, je vous écoute...';
            } else {
              globalToast.setAttribute('data-vstate', 'off');
              if (gvtBadge) gvtBadge.textContent = '🎙️ MICRO';
              if (gvtText) gvtText.textContent = 'Mains libres sur cet appareil';
            }
          } else {
            globalToast.setAttribute('data-vstate', 'idle');
          }
        }
      }
    });
  }

  initVoiceFeedbackUI();

  // Export global pour interaction
  window.NeuroVision = {
    start: startClientCamera,
    stop: stopClientCamera,
    toggle: toggleCamera,
    switchSource: switchSource
  };
})();

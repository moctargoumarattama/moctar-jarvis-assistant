/**
 * M.O.C.T.A.R — Moteur Graphique HUD 3D Cybernétique & Assistant Vocal Intégré
 * 
 * 1. MOTEUR 3D THREE.JS (Référence Cinématique TikTok @techenclair) :
 *    - Mode Arc Plasma (Image 5) : Tore circulaire frontal face caméra, fluide plasma électrique cyan/bleu, anneaux néon concentriques.
 *    - Mode Quantum Core (Image 4) : Cœur solaire blanc-chaud, anneaux gyroscopiques imbriqués, nébuleuse quantique rayonnante.
 *    - Mode Globe 3D (Image 3) : Terre satellite NASA réaliste, nuages dynamiques, anneau orbital incliné, radar vert pulsant.
 * 
 * 2. MOTEUR AUDIO VOCAL BIDIRECTIONNEL (MoctarVoice) :
 *    - Capture micro réelle (MediaDevices + AudioContext + WAV 16k PCM).
 *    - Détection d'activité vocale (VAD) et mot de réveil ("Yo Moctar", "Moctar").
 *    - Dialogue direct avec /api/voice/interact (LLM + actions locales 0 ms).
 *    - Synthèse vocale edge-TTS haute fidélité avec synchronisation visuelle.
 */

'use strict';

(function () {
  // --- Paramètres & Constantes 3D ---
  const GLOBE_RADIUS = 90;
  const CLOUD_RADIUS = 91.6;
  const ATMOS_RADIUS = 98;
  const PLASMA_PARTICLE_COUNT = 3600;
  const QUANTUM_PARTICLE_COUNT = 3000;
  const STAR_COUNT = 1600;

  // Coordonnées de référence Moctar (Dell Precision M4800 / Rabat)
  const DEFAULT_COORDS = { lat: 34.020882, lon: -6.841650, label: "Rabat · Dell Precision M4800" };

  // Couleurs Three.js
  const COLOR_CYAN = new THREE.Color(0x00f3ff);
  const COLOR_ELECTRIC = new THREE.Color(0x00aaff);
  const COLOR_DEEP_BLUE = new THREE.Color(0x0033cc);
  const COLOR_WHITE = new THREE.Color(0xffffff);
  const COLOR_MAGENTA = new THREE.Color(0xd900ff);
  const COLOR_GOLD = new THREE.Color(0xffb700);
  const COLOR_GREEN = new THREE.Color(0x00ff88);

  // Éléments du DOM
  const container = document.getElementById('hud-viewport');
  const canvas = document.getElementById('hud-canvas');
  const btnPlasmaView = document.getElementById('btn-view-plasma');
  const btnQuantumView = document.getElementById('btn-view-quantum');
  const btnGlobeView = document.getElementById('btn-view-globe');
  const btnLocate = document.getElementById('btn-action-locate');
  const btnMic = document.getElementById('hud-mic-btn');
  const btnStop = document.getElementById('hud-stop-btn');
  const voiceStateBadge = document.getElementById('voice-state-badge');
  const userTextEl = document.getElementById('hud-user-transcript');
  const assistantTextEl = document.getElementById('hud-assistant-reply');
  const gpsCoordsEl = document.getElementById('hud-gps-coords');
  const cpuValEl = document.getElementById('hud-cpu-val');
  const ramValEl = document.getElementById('hud-ram-val');
  const hostValEl = document.getElementById('hud-host-val');
  const clockTimeEl = document.getElementById('hud-clock-time');
  const clockDateEl = document.getElementById('hud-clock-date');
  const cmdForm = document.getElementById('hud-cmd-form');
  const cmdInput = document.getElementById('hud-cmd-input');

  let renderer, scene, camera;
  let textureLoader;
  let starField;

  // 1. Groupes de Scène 3D
  let globeGroup, earthMesh, cloudsMesh, atmosMesh, orbitalRingMesh, radarMarker, radarPulseRing;
  let plasmaGroup, plasmaParticles, plasmaInnerRing, plasmaOuterRing;
  let quantumGroup, quantumCoreMesh, quantumHaloMesh, quantumFilamentRings = [], quantumParticles;

  // Tableaux de particules
  let plasmaAngles, plasmaRadii, plasmaSpeeds, plasmaZOffsets;
  let quantumPositions, quantumOriginals, quantumSpeeds;

  // Variables de contrôle 3D
  let currentView = 'plasma'; // 'plasma' | 'quantum' | 'globe'
  let voicePhase = 'idle';    // 'idle' | 'listening' | 'processing' | 'speaking'
  let clock = new THREE.Clock();
  let userCoords = { ...DEFAULT_COORDS };
  let isDragging = false;
  let previousMousePos = { x: 0, y: 0 };
  let rotationTargets = {
    plasma: { x: 0.12, y: 0 },
    quantum: { x: 0.18, y: 0 },
    globe: { x: 0.15, y: -0.6 },
  };
  let currentCameraZ = 290;
  let targetCameraZ = 290;

  // Voice Engine Handle
  let jarvisVoice = null;
  let activeAudioElement = null;

  // --- 1. INITIALISATION DU MOTEUR 3D ---
  function initThree() {
    if (!container || !canvas) return;

    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || window.innerHeight;

    scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x02060e, 0.0008);

    camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 4000);
    camera.position.set(0, 0, currentCameraZ);

    renderer = new THREE.WebGLRenderer({
      canvas: canvas,
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance',
    });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    textureLoader = new THREE.TextureLoader();

    // Lumières
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.95);
    scene.add(ambientLight);

    const sunLight = new THREE.DirectionalLight(0xe8f5ff, 1.8);
    sunLight.position.set(400, 300, 500);
    scene.add(sunLight);

    const rimLight = new THREE.DirectionalLight(0x0055ff, 1.2);
    rimLight.position.set(-400, -200, -400);
    scene.add(rimLight);

    // Création des 3 composants
    createStarfield();
    createCircularPlasmaReactor();
    createRadiantQuantumCore();
    createRealisticEarthGlobe();

    // Interaction souris / touch
    setup3DControls();

    // Synchronisation de la vue initiale
    applyView(currentView);

    // Lancement boucle de rendu
    animate();
  }

  // --- CHAMP D'ÉTOILES ---
  function createStarfield() {
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(STAR_COUNT * 3);
    const colors = new Float32Array(STAR_COUNT * 3);

    for (let i = 0; i < STAR_COUNT; i++) {
      const idx = i * 3;
      positions[idx] = (Math.random() - 0.5) * 2800;
      positions[idx + 1] = (Math.random() - 0.5) * 2800;
      positions[idx + 2] = (Math.random() - 0.5) * 2800;

      const c = Math.random() > 0.4 ? COLOR_CYAN : COLOR_WHITE;
      colors[idx] = c.r;
      colors[idx + 1] = c.g;
      colors[idx + 2] = c.b;
    }

    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const mat = new THREE.PointsMaterial({
      size: 1.8,
      vertexColors: true,
      transparent: true,
      opacity: 0.8,
    });

    starField = new THREE.Points(geo, mat);
    scene.add(starField);
  }

  // --- MODE 1 : RÉACTEUR À ARC PLASMA CIRCULAIRE (Image 5 TikTok) ---
  function createCircularPlasmaReactor() {
    plasmaGroup = new THREE.Group();

    // 1. Anneau néon intérieur (face à la caméra dans le plan XY)
    const innerGeo = new THREE.TorusGeometry(74, 1.6, 16, 120);
    const innerMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      transparent: true,
      opacity: 0.85,
      blending: THREE.AdditiveBlending,
    });
    plasmaInnerRing = new THREE.Mesh(innerGeo, innerMat);
    plasmaGroup.add(plasmaInnerRing);

    // 2. Anneau néon extérieur
    const outerGeo = new THREE.TorusGeometry(94, 1.2, 16, 140);
    const outerMat = new THREE.MeshBasicMaterial({
      color: COLOR_DEEP_BLUE,
      transparent: true,
      opacity: 0.55,
      blending: THREE.AdditiveBlending,
    });
    plasmaOuterRing = new THREE.Mesh(outerGeo, outerMat);
    plasmaGroup.add(plasmaOuterRing);

    // 3. Particules de plasma en flux circulaire
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(PLASMA_PARTICLE_COUNT * 3);
    const colors = new Float32Array(PLASMA_PARTICLE_COUNT * 3);

    plasmaAngles = new Float32Array(PLASMA_PARTICLE_COUNT);
    plasmaRadii = new Float32Array(PLASMA_PARTICLE_COUNT);
    plasmaSpeeds = new Float32Array(PLASMA_PARTICLE_COUNT);
    plasmaZOffsets = new Float32Array(PLASMA_PARTICLE_COUNT);

    const baseRadius = 84;

    for (let i = 0; i < PLASMA_PARTICLE_COUNT; i++) {
      const theta = Math.random() * Math.PI * 2;
      const rOffset = (Math.random() - 0.5) * 18;
      const r = baseRadius + rOffset;
      const z = (Math.random() - 0.5) * 16;

      const idx = i * 3;
      positions[idx] = r * Math.cos(theta);
      positions[idx + 1] = r * Math.sin(theta);
      positions[idx + 2] = z;

      plasmaAngles[i] = theta;
      plasmaRadii[i] = r;
      plasmaSpeeds[i] = 1.0 + Math.random() * 2.2;
      plasmaZOffsets[i] = z;

      // Dégradé cyan éclatant, bleu électrique et blanc étincelant
      const rand = Math.random();
      const c = rand > 0.8 ? COLOR_WHITE : rand > 0.35 ? COLOR_CYAN : COLOR_ELECTRIC;
      colors[idx] = c.r;
      colors[idx + 1] = c.g;
      colors[idx + 2] = c.b;
    }

    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const pMat = new THREE.PointsMaterial({
      size: 2.8,
      vertexColors: true,
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
    });

    plasmaParticles = new THREE.Points(geo, pMat);
    plasmaGroup.add(plasmaParticles);

    // Orientation initiale douce face à l'utilisateur
    plasmaGroup.rotation.x = rotationTargets.plasma.x;
    plasmaGroup.rotation.y = rotationTargets.plasma.y;

    scene.add(plasmaGroup);
  }

  // --- MODE 2 : CŒUR QUANTIQUE NEURONAL RAYONNANT (Image 4 TikTok) ---
  function createRadiantQuantumCore() {
    quantumGroup = new THREE.Group();

    // 1. Cœur sphérique incandescent blanc-chaud
    const coreGeo = new THREE.SphereGeometry(22, 32, 32);
    const coreMat = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
    });
    quantumCoreMesh = new THREE.Mesh(coreGeo, coreMat);
    quantumGroup.add(quantumCoreMesh);

    // 2. Halo coronale cyan
    const haloGeo = new THREE.SphereGeometry(36, 32, 32);
    const haloMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      transparent: true,
      opacity: 0.32,
      blending: THREE.AdditiveBlending,
    });
    quantumHaloMesh = new THREE.Mesh(haloGeo, haloMat);
    quantumGroup.add(quantumHaloMesh);

    // 3. Filaments gyroscopiques imbriqués à 0°, 45°, 90°, 135°
    const angles = [
      { x: 0, y: 0, z: 0, r: 70, c: COLOR_CYAN },
      { x: Math.PI * 0.25, y: Math.PI * 0.15, z: 0, r: 80, c: COLOR_MAGENTA },
      { x: Math.PI * 0.5, y: 0, z: Math.PI * 0.25, r: 90, c: COLOR_CYAN },
      { x: -Math.PI * 0.25, y: Math.PI * 0.3, z: 0, r: 100, c: COLOR_ELECTRIC },
    ];

    quantumFilamentRings = [];
    angles.forEach((cfg) => {
      const rGeo = new THREE.TorusGeometry(cfg.r, 1.1, 16, 120);
      const rMat = new THREE.MeshBasicMaterial({
        color: cfg.c,
        transparent: true,
        opacity: 0.5,
        blending: THREE.AdditiveBlending,
      });
      const ring = new THREE.Mesh(rGeo, rMat);
      ring.rotation.set(cfg.x, cfg.y, cfg.z);
      quantumFilamentRings.push(ring);
      quantumGroup.add(ring);
    });

    // 4. Nébuleuse de particules quantiques sphériques
    const geo = new THREE.BufferGeometry();
    quantumPositions = new Float32Array(QUANTUM_PARTICLE_COUNT * 3);
    quantumOriginals = new Float32Array(QUANTUM_PARTICLE_COUNT * 3);
    quantumSpeeds = new Float32Array(QUANTUM_PARTICLE_COUNT);
    const colors = new Float32Array(QUANTUM_PARTICLE_COUNT * 3);

    for (let i = 0; i < QUANTUM_PARTICLE_COUNT; i++) {
      const u = Math.random();
      const v = Math.random();
      const theta = u * 2.0 * Math.PI;
      const phi = Math.acos(2.0 * v - 1.0);
      const r = 35 + Math.random() * 85;

      const x = r * Math.sin(phi) * Math.cos(theta);
      const y = r * Math.sin(phi) * Math.sin(theta);
      const z = r * Math.cos(phi);

      const idx = i * 3;
      quantumPositions[idx] = x;
      quantumPositions[idx + 1] = y;
      quantumPositions[idx + 2] = z;

      quantumOriginals[idx] = x;
      quantumOriginals[idx + 1] = y;
      quantumOriginals[idx + 2] = z;

      quantumSpeeds[i] = 0.5 + Math.random() * 1.5;

      const c = Math.random() > 0.45 ? COLOR_CYAN : COLOR_MAGENTA;
      colors[idx] = c.r;
      colors[idx + 1] = c.g;
      colors[idx + 2] = c.b;
    }

    geo.setAttribute('position', new THREE.BufferAttribute(quantumPositions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const pMat = new THREE.PointsMaterial({
      size: 2.5,
      vertexColors: true,
      transparent: true,
      opacity: 0.85,
      blending: THREE.AdditiveBlending,
    });

    quantumParticles = new THREE.Points(geo, pMat);
    quantumGroup.add(quantumParticles);

    quantumGroup.rotation.x = rotationTargets.quantum.x;
    quantumGroup.rotation.y = rotationTargets.quantum.y;

    scene.add(quantumGroup);
  }

  // --- MODE 3 : GLOBE 3D RÉALISTE NASA (Image 3 TikTok) ---
  function createRealisticEarthGlobe() {
    globeGroup = new THREE.Group();

    // 1. Sphère terrestre avec texture satellite haute résolution
    const earthGeo = new THREE.SphereGeometry(GLOBE_RADIUS, 64, 64);
    const earthTex = textureLoader.load('/static/textures/earth_atmos_2048.jpg');
    earthTex.anisotropy = 4;

    const earthMat = new THREE.MeshPhongMaterial({
      map: earthTex,
      bumpScale: 0.06,
      specular: new THREE.Color(0x1a3355),
      shininess: 22,
    });

    earthMesh = new THREE.Mesh(earthGeo, earthMat);
    globeGroup.add(earthMesh);

    // 2. Couche de nuages dynamiques semi-transparente
    const cloudsGeo = new THREE.SphereGeometry(CLOUD_RADIUS, 64, 64);
    const cloudsTex = textureLoader.load('/static/textures/earth_clouds_1024.png');

    const cloudsMat = new THREE.MeshLambertMaterial({
      map: cloudsTex,
      transparent: true,
      opacity: 0.78,
      blending: THREE.AdditiveBlending,
    });

    cloudsMesh = new THREE.Mesh(cloudsGeo, cloudsMat);
    globeGroup.add(cloudsMesh);

    // 3. Halo atmosphérique cyan
    const atmosGeo = new THREE.SphereGeometry(ATMOS_RADIUS, 64, 64);
    const atmosMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      side: THREE.BackSide,
      transparent: true,
      opacity: 0.28,
      blending: THREE.AdditiveBlending,
    });
    atmosMesh = new THREE.Mesh(atmosGeo, atmosMat);
    globeGroup.add(atmosMesh);

    // 4. Anneau orbital incliné en harmonie avec le globe
    const ringGeo = new THREE.TorusGeometry(GLOBE_RADIUS * 1.42, 0.75, 16, 140);
    const ringMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      transparent: true,
      opacity: 0.65,
      blending: THREE.AdditiveBlending,
    });
    orbitalRingMesh = new THREE.Mesh(ringGeo, ringMat);
    orbitalRingMesh.rotation.x = Math.PI * 0.32;
    orbitalRingMesh.rotation.y = Math.PI * 0.12;
    globeGroup.add(orbitalRingMesh);

    // 5. Radar vert pulsant sur Moctar
    const markerGeo = new THREE.SphereGeometry(2.4, 16, 16);
    const markerMat = new THREE.MeshBasicMaterial({ color: COLOR_GREEN });
    radarMarker = new THREE.Mesh(markerGeo, markerMat);

    const pulseGeo = new THREE.RingGeometry(3.2, 5.0, 32);
    const pulseMat = new THREE.MeshBasicMaterial({
      color: COLOR_GREEN,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.85,
      blending: THREE.AdditiveBlending,
    });
    radarPulseRing = new THREE.Mesh(pulseGeo, pulseMat);
    radarMarker.add(radarPulseRing);

    globeGroup.add(radarMarker);
    setRadarGpsCoordinates(userCoords.lat, userCoords.lon);

    globeGroup.rotation.x = rotationTargets.globe.x;
    globeGroup.rotation.y = rotationTargets.globe.y;

    scene.add(globeGroup);
  }

  function setRadarGpsCoordinates(lat, lon) {
    if (!radarMarker) return;
    const phi = (90 - lat) * (Math.PI / 180);
    const theta = (lon + 180) * (Math.PI / 180);

    const r = GLOBE_RADIUS + 1.2;
    const x = -(r * Math.sin(phi) * Math.cos(theta));
    const z = r * Math.sin(phi) * Math.sin(theta);
    const y = r * Math.cos(phi);

    const pos = new THREE.Vector3(x, y, z);
    radarMarker.position.copy(pos);
    radarMarker.lookAt(pos.clone().multiplyScalar(2));
  }

  // --- BASCULEMENT DE VUE & AFFICHAGE ---
  function applyView(viewName) {
    const v = (viewName || '').toLowerCase().trim();
    if (['globe', 'earth', 'terre'].includes(v)) {
      currentView = 'globe';
    } else if (['quantum', 'core', 'neural'].includes(v)) {
      currentView = 'quantum';
    } else {
      currentView = 'plasma';
    }

    if (globeGroup) globeGroup.visible = currentView === 'globe';
    if (quantumGroup) quantumGroup.visible = currentView === 'quantum';
    if (plasmaGroup) plasmaGroup.visible = currentView === 'plasma';

    // Boutons actifs
    [btnPlasmaView, btnQuantumView, btnGlobeView].forEach((b) => b && b.classList.remove('active'));
    if (currentView === 'plasma' && btnPlasmaView) btnPlasmaView.classList.add('active');
    if (currentView === 'quantum' && btnQuantumView) btnQuantumView.classList.add('active');
    if (currentView === 'globe' && btnGlobeView) btnGlobeView.classList.add('active');

    targetCameraZ = currentView === 'globe' ? 280 : 290;
  }

  // --- CONTRÔLES SOURIS, TOUCH & ZOOM ---
  function setup3DControls() {
    function onDown(e) {
      isDragging = true;
      const x = e.touches ? e.touches[0].clientX : e.clientX;
      const y = e.touches ? e.touches[0].clientY : e.clientY;
      previousMousePos = { x, y };
    }

    function onMove(e) {
      if (!isDragging) return;
      const x = e.touches ? e.touches[0].clientX : e.clientX;
      const y = e.touches ? e.touches[0].clientY : e.clientY;

      const dx = x - previousMousePos.x;
      const dy = y - previousMousePos.y;

      const target = rotationTargets[currentView];
      if (target) {
        target.y += dx * 0.007;
        target.x += dy * 0.007;
        target.x = Math.max(-Math.PI * 0.45, Math.min(Math.PI * 0.45, target.x));
      }

      previousMousePos = { x, y };
    }

    function onUp() {
      isDragging = false;
    }

    canvas.addEventListener('mousedown', onDown);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);

    canvas.addEventListener('touchstart', onDown, { passive: true });
    window.addEventListener('touchmove', onMove, { passive: true });
    window.addEventListener('touchend', onUp);

    // Zoom molette
    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      targetCameraZ += e.deltaY * 0.25;
      targetCameraZ = Math.max(160, Math.min(650, targetCameraZ));
    }, { passive: false });
  }

  // --- 2. INTÉGRATION COMPLÈTE DU MOTEUR VOCAL (MoctarVoice) ---
  function initVoiceEngine() {
    if (!window.MoctarVoice) {
      console.warn("MoctarVoice non disponible dans la fenêtre.");
      return;
    }

    jarvisVoice = window.MoctarVoice.create({
      followupMs: 30000,
      mediaDevices: window.navigator ? window.navigator.mediaDevices : null,
      MediaRecorder: window.MediaRecorder,
      AudioContext: window.AudioContext || window.webkitAudioContext,
      Audio: window.Audio,
      interactUrl: '/api/voice/interact',
      onState: handleVoiceStateChange,
      onInteractResult: handleVoiceInteractResult,
      language: 'fr-FR',
    });

    // Mettre à jour l'interface
    handleVoiceStateChange(jarvisVoice.state);

    // Activer l'écoute automatique au premier clic utilisateur (déverrouillage navigateur)
    function unlockVoice() {
      if (jarvisVoice && !jarvisVoice.enabled) {
        jarvisVoice.enable().catch(() => {});
      }
      window.removeEventListener('click', unlockVoice);
      window.removeEventListener('touchstart', unlockVoice);
    }
    window.addEventListener('click', unlockVoice);
    window.addEventListener('touchstart', unlockVoice);
  }

  function handleVoiceStateChange(phase, detail = {}) {
    voicePhase = phase;

    const messages = {
      off: '● VEILLE ACTIVE',
      starting: '🟢 INITIALISATION...',
      wake: '🟢 À L\'ÉCOUTE (Dites « Yo Moctar »)',
      listening: '🎙️ ÉCOUTE EN COURS...',
      processing: '⚡ ANALYSE...',
      speaking: '🔊 SYNTHÈSE...',
      paused: '⏸️ VEILLE',
      error: '⚠️ ERREUR MICRO',
      unsupported: '❌ NON SUPPORTÉ',
    };

    if (voiceStateBadge) {
      voiceStateBadge.setAttribute('data-state', phase);
      voiceStateBadge.textContent = messages[phase] || '● EN LIGNE';
    }

    if (btnMic) {
      const isListening = ['starting', 'wake', 'listening'].includes(phase);
      btnMic.classList.toggle('active', isListening);
      btnMic.textContent = phase === 'listening' ? '🎙️ ENREGISTREMENT...' : '🎙️ MICRO';
    }
  }

  function handleVoiceInteractResult(data) {
    if (!data || data.ignored) return;

    if (data.user_text && userTextEl) {
      userTextEl.textContent = `« ${data.user_text} »`;
    }

    if (data.reply_text && assistantTextEl) {
      assistantTextEl.textContent = data.reply_text;
    }

    // Basculement automatique de vue 3D si la commande le demande
    if (data.action === 'open_app' && data.url === '/hud') {
      if (data.title && data.title.includes('Globe')) {
        applyView('globe');
      } else if (data.title && data.title.includes('Plasma')) {
        applyView('plasma');
      } else if (data.title && data.title.includes('Quantique')) {
        applyView('quantum');
      }
    }
  }

  // Action Manuelle Micro
  function toggleMicrophone() {
    if (!jarvisVoice) {
      initVoiceEngine();
    }
    if (!jarvisVoice) return;

    if (window.isSecureContext === false) {
      alert("L'accès micro nécessite une connexion HTTPS.");
      return;
    }

    if (jarvisVoice.state === 'listening') {
      jarvisVoice.stopManual();
    } else {
      if (!jarvisVoice.enabled) {
        jarvisVoice.enable().then(() => jarvisVoice.startManual());
      } else {
        jarvisVoice.startManual();
      }
    }
  }

  // Interruption audio immédiate
  function stopAllSpeech() {
    if (jarvisVoice) {
      jarvisVoice.stopManual();
    }
    if (window.speechSynthesis && window.speechSynthesis.speaking) {
      window.speechSynthesis.cancel();
    }
    const audios = document.querySelectorAll('audio');
    audios.forEach((a) => {
      try { a.pause(); a.currentTime = 0; } catch (e) {}
    });
    handleVoiceStateChange('wake');
    if (assistantTextEl) {
      assistantTextEl.textContent = "Interrompu, Moctar.";
    }
  }

  // Envoi de commande textuelle (Alternative sans micro)
  async function submitTextCommand(text) {
    const query = (text || '').trim();
    if (!query) return;

    if (userTextEl) userTextEl.textContent = `« ${query} »`;
    if (assistantTextEl) assistantTextEl.textContent = "Traitement en cours...";
    handleVoiceStateChange('processing');

    try {
      const res = await fetch('/api/voice/interact', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Voice-Session-Active': 'true',
        },
        body: JSON.stringify({ text: query }),
      });

      if (res.ok) {
        const data = await res.json();
        handleVoiceInteractResult(data);
        handleVoiceStateChange('speaking');

        // Jouer la synthèse audio si reçue
        if (data.audio_url) {
          playAudioUrl(data.audio_url);
        } else if (data.reply_text && window.speechSynthesis) {
          const utter = new SpeechSynthesisUtterance(data.reply_text);
          utter.lang = 'fr-FR';
          utter.onend = () => handleVoiceStateChange('wake');
          window.speechSynthesis.speak(utter);
        }
      } else {
        handleVoiceStateChange('wake');
      }
    } catch (e) {
      handleVoiceStateChange('wake');
    }
  }

  function playAudioUrl(url) {
    try {
      const audio = new Audio(url);
      activeAudioElement = audio;
      audio.onended = () => {
        handleVoiceStateChange('wake');
        activeAudioElement = null;
      };
      audio.play().catch(() => {
        handleVoiceStateChange('wake');
      });
    } catch (e) {
      handleVoiceStateChange('wake');
    }
  }

  // --- ACTIONS DES BOUTONS ---
  function setupUIEventListeners() {
    if (btnPlasmaView) btnPlasmaView.addEventListener('click', () => applyView('plasma'));
    if (btnQuantumView) btnQuantumView.addEventListener('click', () => applyView('quantum'));
    if (btnGlobeView) btnGlobeView.addEventListener('click', () => applyView('globe'));

    if (btnLocate) {
      btnLocate.addEventListener('click', () => {
        applyView('globe');
        // Animation fluide de rotation vers la position de Rabat
        const phi = (90 - userCoords.lat) * (Math.PI / 180);
        const theta = (userCoords.lon + 180) * (Math.PI / 180);
        rotationTargets.globe.x = 0.25;
        rotationTargets.globe.y = -(theta - Math.PI / 2);

        if (gpsCoordsEl) {
          gpsCoordsEl.textContent = `GPS : ${userCoords.lat.toFixed(4)}°N, ${userCoords.lon.toFixed(4)}°E (Position Moctar Fixée)`;
        }
        if (assistantTextEl) {
          assistantTextEl.textContent = "Position géographique verrouillée sur le Dell Precision M4800, Moctar.";
        }
      });
    }

    if (btnMic) {
      btnMic.addEventListener('click', (e) => {
        e.preventDefault();
        toggleMicrophone();
      });
    }

    if (btnStop) {
      btnStop.addEventListener('click', (e) => {
        e.preventDefault();
        stopAllSpeech();
      });
    }

    if (cmdForm && cmdInput) {
      cmdForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const val = cmdInput.value;
        cmdInput.value = '';
        submitTextCommand(val);
      });
    }

    window.addEventListener('resize', onResize);
  }

  function onResize() {
    if (!container || !renderer || !camera) return;
    const w = container.clientWidth || window.innerWidth;
    const h = container.clientHeight || window.innerHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  }

  // --- HORLOGE NUMÉRIQUE CYBERNÉTIQUE ---
  function startClock() {
    function tick() {
      const now = new Date();
      const hh = String(now.getHours()).padStart(2, '0');
      const mm = String(now.getMinutes()).padStart(2, '0');
      const ss = String(now.getSeconds()).padStart(2, '0');
      if (clockTimeEl) clockTimeEl.textContent = `${hh}:${mm}:${ss}`;

      if (clockDateEl) {
        const opts = { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' };
        clockDateEl.textContent = now.toLocaleDateString('fr-FR', opts).toUpperCase();
      }
    }
    setInterval(tick, 1000);
    tick();
  }

  // --- SYNCHRONISATION TÉLÉMÉTRIE MATÉRIELLE EN DIRECT ---
  function startTelemetrySync() {
    async function sync() {
      try {
        const res = await fetch('/api/hud/state');
        if (res.ok) {
          const data = await res.json();
          if (cpuValEl && data.cpu_percent !== undefined) {
            cpuValEl.textContent = `${data.cpu_percent}%`;
          }
          if (ramValEl && data.ram_percent !== undefined) {
            ramValEl.textContent = `${data.ram_percent}%`;
          }
          if (hostValEl && data.hostname) {
            hostValEl.textContent = data.hostname;
          }

          // Bascule de vue à distance
          if (data.view && data.view !== currentView) {
            applyView(data.view);
          }
        }
      } catch (e) {}
    }
    setInterval(sync, 1400);
    sync();
  }

  // --- BOUCLE DE RENDU CINÉMATIQUE 60 FPS ---
  function animate() {
    requestAnimationFrame(animate);

    const delta = clock.getDelta();
    const elapsed = clock.getElapsedTime();

    // 1. Zoom caméra doux (Lerp)
    currentCameraZ += (targetCameraZ - currentCameraZ) * 0.08;
    camera.position.z = currentCameraZ;

    // 2. Rotation douce des groupes
    const activeGrp = currentView === 'globe' ? globeGroup : currentView === 'quantum' ? quantumGroup : plasmaGroup;
    const tgt = rotationTargets[currentView];

    if (activeGrp && tgt) {
      activeGrp.rotation.y += (tgt.y - activeGrp.rotation.y) * 0.08;
      activeGrp.rotation.x += (tgt.x - activeGrp.rotation.x) * 0.08;

      // Dérive naturelle si non manipulé
      if (!isDragging) {
        if (currentView === 'globe') {
          tgt.y += delta * 0.1; // Terre tourne
        } else {
          // Balancement subtil et stable pour Plasma et Quantum (reste centré !)
          tgt.y = Math.sin(elapsed * 0.4) * 0.08;
        }
      }
    }

    if (starField) {
      starField.rotation.y += delta * 0.012;
    }

    // --- ANIMATION MODE 1 : PLASMA ARC REACTOR (Image 5) ---
    if (currentView === 'plasma' && plasmaParticles) {
      const positions = plasmaParticles.geometry.attributes.position.array;
      const speedMult = voicePhase === 'speaking' ? 2.6 : voicePhase === 'listening' ? 1.8 : 1.0;

      for (let i = 0; i < PLASMA_PARTICLE_COUNT; i++) {
        plasmaAngles[i] += delta * plasmaSpeeds[i] * speedMult;
        const u = plasmaAngles[i];
        const r = plasmaRadii[i] + Math.sin(elapsed * 5 + i) * 1.8;

        const idx = i * 3;
        positions[idx] = r * Math.cos(u);
        positions[idx + 1] = r * Math.sin(u);
        positions[idx + 2] = plasmaZOffsets[i] + Math.sin(u * 3 + elapsed) * 3;
      }
      plasmaParticles.geometry.attributes.position.needsUpdate = true;

      if (plasmaInnerRing) {
        const pulse = 1.0 + Math.sin(elapsed * 4) * 0.025;
        plasmaInnerRing.scale.set(pulse, pulse, pulse);
      }
      if (plasmaOuterRing) {
        plasmaOuterRing.rotation.z -= delta * 0.5;
      }
    }

    // --- ANIMATION MODE 2 : QUANTUM CORE (Image 4) ---
    if (currentView === 'quantum' && quantumParticles) {
      const positions = quantumParticles.geometry.attributes.position.array;
      const corePulse = 1.0 + Math.sin(elapsed * 6) * 0.08;

      if (quantumCoreMesh) quantumCoreMesh.scale.set(corePulse, corePulse, corePulse);
      if (quantumHaloMesh) quantumHaloMesh.scale.set(corePulse * 1.05, corePulse * 1.05, corePulse * 1.05);

      quantumFilamentRings.forEach((ring, idx) => {
        ring.rotation.z += delta * (0.35 + idx * 0.2);
      });

      for (let i = 0; i < QUANTUM_PARTICLE_COUNT; i++) {
        const idx = i * 3;
        const ox = quantumOriginals[idx];
        const oy = quantumOriginals[idx + 1];
        const oz = quantumOriginals[idx + 2];

        // Respiration ondulatoire vers l'extérieur
        const breath = 1.0 + Math.sin(elapsed * 3 + i) * 0.12;
        positions[idx] = ox * breath;
        positions[idx + 1] = oy * breath;
        positions[idx + 2] = oz * breath;
      }
      quantumParticles.geometry.attributes.position.needsUpdate = true;
    }

    // --- ANIMATION MODE 3 : GLOBE 3D (Image 3) ---
    if (currentView === 'globe') {
      if (cloudsMesh) cloudsMesh.rotation.y += delta * 0.035;
      if (orbitalRingMesh) orbitalRingMesh.rotation.z += delta * 0.18;
      if (radarPulseRing) {
        const pScale = 1.0 + ((elapsed * 2.8) % 2.5);
        radarPulseRing.scale.set(pScale, pScale, pScale);
        radarPulseRing.material.opacity = Math.max(0, 1.0 - (pScale - 1.0) / 2.5);
      }
    }

    renderer.render(scene, camera);
  }

  // --- INITIALISATION GLOBALE AU CHARGEMENT ---
  window.addEventListener('DOMContentLoaded', () => {
    initThree();
    initVoiceEngine();
    setupUIEventListeners();
    startClock();
    startTelemetrySync();
  });

  // Interface externe globale
  window.MocHUD = {
    applyView,
    toggleMicrophone,
    stopAllSpeech,
    submitTextCommand,
  };
})();

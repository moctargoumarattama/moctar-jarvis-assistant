/**
 * M.O.C.T.A.R — Moteur Graphique HUD 3D Cybernétique & Assistant Vocal Haute Fidélité
 * 
 * Architecture Unifiée & Résiliente :
 * 1. Cycle de vie découplé : L'horloge, la télémétrie matérielle et les contrôles UI
 *    démarrent immédiatement sans dépendre de WebGL (zéro écran noir / zéro blocage).
 * 2. Moteur 3D Cinématique Three.js (Arc Plasma, Cœur Quantique, Globe 3D Réaliste NASA) :
 *    - Positionnement rehaussé (Y = +38) pour dégager 100% de visibilité au-dessus du ruban inférieur.
 *    - Correction stricte de toutes les références de maillage (aucun ReferenceError).
 * 3. Persistance des Vues 3D :
 *    - La télémétrie n'écrase JAMAIS la vue choisie par l'utilisateur.
 *    - Mémorisation locale (localStorage) + synchronisation API backend.
 * 4. Moteur Vocal Bidirectionnel Unifié (MoctarVoice) :
 *    - Même moteur éprouvé que le Dashboard NOC.
 *    - Prise en charge push-to-talk et mains libres (Wake-Word « Yo Moctar »).
 *    - VU-mètre audio en direct animé par RMS réel.
 *    - Bouton STOP d'interruption immédiate 0 ms.
 */

'use strict';

(function () {
  // --- Paramètres & Constantes 3D ---
  const SCENE_Y_OFFSET = 38; // Rehaussé largement pour ne jamais entrer en collision avec le bandeau inférieur
  const GLOBE_RADIUS = 86;
  const CLOUD_RADIUS = 87.8;
  const ATMOS_RADIUS = 94;
  const PLASMA_PARTICLE_COUNT = 3400;
  const QUANTUM_PARTICLE_COUNT = 2800;
  const STAR_COUNT = 1500;

  // Coordonnées de Moctar (Dell Precision M4800 / Rabat)
  const DEFAULT_COORDS = { lat: 34.020882, lon: -6.841650, label: "Rabat · Dell Precision M4800" };

  // Hexagones de couleurs (sécurisés, utilisables sans THREE immédiat)
  const HEX_CYAN = 0x00f3ff;
  const HEX_ELECTRIC = 0x00aaff;
  const HEX_DEEP_BLUE = 0x002db3;
  const HEX_WHITE = 0xffffff;
  const HEX_MAGENTA = 0xd900ff;
  const HEX_GOLD = 0xffb700;
  const HEX_GREEN = 0x00ff88;

  // Couleurs THREE.Color instanciées dynamiquement
  let COLOR_CYAN, COLOR_ELECTRIC, COLOR_DEEP_BLUE, COLOR_WHITE, COLOR_MAGENTA, COLOR_GOLD, COLOR_GREEN;

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
  const vumeterEl = document.getElementById('hud-vumeter');

  let renderer, scene, camera;
  let textureLoader;
  let starField;

  // Groupes 3D
  let globeGroup, earthMesh, cloudsMesh, atmosMesh, orbitalRingMesh, radarMarker, radarPulseRing;
  let plasmaGroup, plasmaParticles, plasmaInnerRing, plasmaOuterRing;
  let quantumGroup, quantumCoreMesh, quantumHaloMesh, quantumFilamentRings = [], quantumParticles;

  // Tableaux de particules
  let plasmaAngles, plasmaRadii, plasmaSpeeds, plasmaZOffsets;
  let quantumPositions, quantumOriginals, quantumSpeeds;

  // États du système
  let currentView = 'plasma';
  let clock = null;
  let userCoords = { ...DEFAULT_COORDS };
  let isDragging = false;
  let previousMousePos = { x: 0, y: 0 };
  let rotationTargets = {
    plasma: { x: 0.08, y: 0 },
    quantum: { x: 0.12, y: 0 },
    globe: { x: 0.18, y: -0.6 },
  };
  let currentCameraZ = 285;
  let targetCameraZ = 285;

  // Moteur Audio & Voix
  let jarvisVoice = null;
  let isRecordingManual = false;
  let activeAudioElement = null;

  // Liste noire des hallucinations Whisper pour ne jamais polluer l'interface
  const HALLUCINATION_BLACK_LIST = [
    'sous-titrage',
    'sous-titres',
    'radio-canada',
    'amara.org',
    'merci d\'avoir regardé',
    'merci d avoir regarde',
    'merci de votre attention',
    'abonnez-vous',
  ];

  // --- INITIALISATION DES COULEURS THREE.JS ---
  function initColors() {
    if (typeof THREE === 'undefined' || !THREE.Color) return;
    COLOR_CYAN = new THREE.Color(HEX_CYAN);
    COLOR_ELECTRIC = new THREE.Color(HEX_ELECTRIC);
    COLOR_DEEP_BLUE = new THREE.Color(HEX_DEEP_BLUE);
    COLOR_WHITE = new THREE.Color(HEX_WHITE);
    COLOR_MAGENTA = new THREE.Color(HEX_MAGENTA);
    COLOR_GOLD = new THREE.Color(HEX_GOLD);
    COLOR_GREEN = new THREE.Color(HEX_GREEN);
  }

  // --- 1. INITIALISATION DU MOTEUR 3D ---
  function initThree() {
    if (typeof THREE === 'undefined') {
      console.warn("Three.js non chargé.");
      return;
    }
    if (!container || !canvas) return;

    initColors();
    clock = new THREE.Clock();

    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || window.innerHeight;

    scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x02060e, 0.0008);

    camera = new THREE.PerspectiveCamera(48, width / height, 0.1, 4000);
    camera.position.set(0, 10, currentCameraZ);
    camera.lookAt(0, 26, 0);

    try {
      renderer = new THREE.WebGLRenderer({
        canvas: canvas,
        antialias: true,
        alpha: true,
        powerPreference: 'high-performance',
      });
      renderer.setSize(width, height);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    } catch (e) {
      console.error("Échec création WebGLRenderer:", e);
      return;
    }

    textureLoader = new THREE.TextureLoader();

    // Éclairage spatial réaliste
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.95);
    scene.add(ambientLight);

    const sunLight = new THREE.DirectionalLight(0xe8f5ff, 1.8);
    sunLight.position.set(400, 300, 500);
    scene.add(sunLight);

    const rimLight = new THREE.DirectionalLight(0x0055ff, 1.2);
    rimLight.position.set(-400, -200, -400);
    scene.add(rimLight);

    // Création des composants 3D
    try { createStarfield(); } catch (err) { console.warn("Erreur createStarfield:", err); }
    try { createCircularPlasmaReactor(); } catch (err) { console.warn("Erreur createCircularPlasmaReactor:", err); }
    try { createRadiantQuantumCore(); } catch (err) { console.warn("Erreur createRadiantQuantumCore:", err); }
    try { createRealisticEarthGlobe(); } catch (err) { console.warn("Erreur createRealisticEarthGlobe:", err); }

    // Contrôles 3D
    setup3DControls();

    // Récupérer la vue préférée mémorisée
    let savedView = 'plasma';
    try {
      savedView = localStorage.getItem('moctar_hud_view') || 'plasma';
    } catch (_) {}

    applyView(savedView, false);

    // Démarrer la boucle de rendu 60 FPS
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

  // --- MODE 1 : RÉACTEUR À ARC PLASMA CIRCULAIRE (Image 5) ---
  function createCircularPlasmaReactor() {
    plasmaGroup = new THREE.Group();
    plasmaGroup.position.y = SCENE_Y_OFFSET;

    // Anneau néon intérieur parfaitement circulaire
    const innerGeo = new THREE.TorusGeometry(72, 1.5, 16, 120);
    const innerMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      transparent: true,
      opacity: 0.85,
      blending: THREE.AdditiveBlending,
    });
    plasmaInnerRing = new THREE.Mesh(innerGeo, innerMat);
    plasmaGroup.add(plasmaInnerRing);

    // Anneau néon extérieur
    const outerGeo = new THREE.TorusGeometry(92, 1.2, 16, 140);
    const outerMat = new THREE.MeshBasicMaterial({
      color: COLOR_DEEP_BLUE,
      transparent: true,
      opacity: 0.6,
      blending: THREE.AdditiveBlending,
    });
    plasmaOuterRing = new THREE.Mesh(outerGeo, outerMat);
    plasmaGroup.add(plasmaOuterRing);

    // Particules de plasma en flux circulaire stable
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(PLASMA_PARTICLE_COUNT * 3);
    const colors = new Float32Array(PLASMA_PARTICLE_COUNT * 3);

    plasmaAngles = new Float32Array(PLASMA_PARTICLE_COUNT);
    plasmaRadii = new Float32Array(PLASMA_PARTICLE_COUNT);
    plasmaSpeeds = new Float32Array(PLASMA_PARTICLE_COUNT);
    plasmaZOffsets = new Float32Array(PLASMA_PARTICLE_COUNT);

    for (let i = 0; i < PLASMA_PARTICLE_COUNT; i++) {
      plasmaAngles[i] = Math.random() * Math.PI * 2.0;
      plasmaRadii[i] = 68 + Math.random() * 26;
      plasmaSpeeds[i] = (Math.random() > 0.5 ? 1 : -1) * (0.8 + Math.random() * 1.6);
      plasmaZOffsets[i] = (Math.random() - 0.5) * 8.0;

      const u = plasmaAngles[i];
      const r = plasmaRadii[i];

      const idx = i * 3;
      positions[idx] = r * Math.cos(u);
      positions[idx + 1] = r * Math.sin(u);
      positions[idx + 2] = plasmaZOffsets[i];

      const isCyan = Math.random() > 0.35;
      const c = isCyan ? COLOR_CYAN : COLOR_ELECTRIC;
      colors[idx] = c.r;
      colors[idx + 1] = c.g;
      colors[idx + 2] = c.b;
    }

    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const pMat = new THREE.PointsMaterial({
      size: 2.2,
      vertexColors: true,
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
    });

    plasmaParticles = new THREE.Points(geo, pMat);
    plasmaGroup.add(plasmaParticles);

    plasmaGroup.rotation.x = rotationTargets.plasma.x;
    plasmaGroup.rotation.y = rotationTargets.plasma.y;

    scene.add(plasmaGroup);
  }

  // --- MODE 2 : CŒUR QUANTIQUE RAYONNANT (Image 4) ---
  function createRadiantQuantumCore() {
    quantumGroup = new THREE.Group();
    quantumGroup.position.y = SCENE_Y_OFFSET;

    // Cœur central sphérique incandescent blanc-chaud
    const coreGeo = new THREE.SphereGeometry(22, 32, 32);
    const coreMat = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
    });
    quantumCoreMesh = new THREE.Mesh(coreGeo, coreMat);
    quantumGroup.add(quantumCoreMesh);

    // Halo d'énergie coronale cyan
    const haloGeo = new THREE.SphereGeometry(35, 32, 32);
    const haloMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      transparent: true,
      opacity: 0.35,
      blending: THREE.AdditiveBlending,
    });
    quantumHaloMesh = new THREE.Mesh(haloGeo, haloMat);
    quantumGroup.add(quantumHaloMesh);

    // 4 anneaux gyroscopiques à 0°, 45°, 90°, 135°
    const configs = [
      { x: 0, y: 0, z: 0, r: 68, c: COLOR_CYAN },
      { x: Math.PI * 0.25, y: Math.PI * 0.15, z: 0, r: 78, c: COLOR_MAGENTA },
      { x: Math.PI * 0.5, y: 0, z: Math.PI * 0.2, r: 88, c: COLOR_CYAN },
      { x: -Math.PI * 0.25, y: Math.PI * 0.25, z: 0, r: 96, c: COLOR_ELECTRIC },
    ];

    quantumFilamentRings = [];
    configs.forEach((cfg) => {
      const rGeo = new THREE.TorusGeometry(cfg.r, 1.1, 16, 120);
      const rMat = new THREE.MeshBasicMaterial({
        color: cfg.c,
        transparent: true,
        opacity: 0.52,
        blending: THREE.AdditiveBlending,
      });
      const ring = new THREE.Mesh(rGeo, rMat);
      ring.rotation.set(cfg.x, cfg.y, cfg.z);
      quantumFilamentRings.push(ring);
      quantumGroup.add(ring);
    });

    // Nébuleuse de particules sphériques
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
      const r = 32 + Math.random() * 80;

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

  // --- MODE 3 : GLOBE 3D RÉALISTE NASA (Image 3) ---
  function createRealisticEarthGlobe() {
    globeGroup = new THREE.Group();
    globeGroup.position.y = SCENE_Y_OFFSET;

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

    // 2. Couche de nuages dynamiques semi-transparents
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

    // 4. Anneau orbital incliné
    const ringGeo = new THREE.TorusGeometry(GLOBE_RADIUS * 1.38, 0.75, 16, 140);
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

    // 5. Radar vert pulsant sur Moctar (Rabat)
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

  // --- BASCULEMENT DE VUE ROBUSTE & PERSISTANT ---
  function applyView(viewName, isUserAction = false) {
    const v = (viewName || '').toLowerCase().trim();
    if (['globe', 'earth', 'terre'].includes(v)) {
      currentView = 'globe';
    } else if (['quantum', 'core', 'neural', 'quantique'].includes(v)) {
      currentView = 'quantum';
    } else {
      currentView = 'plasma';
    }

    if (globeGroup) globeGroup.visible = currentView === 'globe';
    if (quantumGroup) quantumGroup.visible = currentView === 'quantum';
    if (plasmaGroup) plasmaGroup.visible = currentView === 'plasma';

    // Mise à jour visuelle des boutons de vue
    [btnPlasmaView, btnQuantumView, btnGlobeView].forEach((b) => b && b.classList.remove('active'));
    if (currentView === 'plasma' && btnPlasmaView) btnPlasmaView.classList.add('active');
    if (currentView === 'quantum' && btnQuantumView) btnQuantumView.classList.add('active');
    if (currentView === 'globe' && btnGlobeView) btnGlobeView.classList.add('active');

    targetCameraZ = currentView === 'globe' ? 275 : 285;

    // Si action utilisateur : persister dans localStorage et synchroniser avec l'API backend
    if (isUserAction) {
      try {
        localStorage.setItem('moctar_hud_view', currentView);
      } catch (_) {}

      fetch('/api/hud/view', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ view: currentView }),
      }).catch(() => {});
    }
  }

  // --- CONTRÔLES SOURIS, TOUCH & ZOOM ---
  function setup3DControls() {
    if (!canvas) return;

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

  // --- 2. GESTION DU MICROPHONE & MOTEUR VOCAL UNIFIÉ (MoctarVoice) ---
  function initVoiceEngine() {
    if (!window.MoctarVoice) {
      console.warn("MoctarVoice non disponible.");
      return;
    }

    jarvisVoice = window.MoctarVoice.create({
      followupMs: 30000, // 30s d'écoute active pour les enchaînements fluides
      mediaDevices: window.navigator ? window.navigator.mediaDevices : null,
      MediaRecorder: window.MediaRecorder,
      AudioContext: window.AudioContext || window.webkitAudioContext,
      Audio: window.Audio,
      interactUrl: '/api/voice/interact',
      onState: handleVoiceStateChange,
      onInteractResult: handleVoiceInteractResult,
      speak: speakJarvisText,
      language: 'fr-FR',
    });

    handleVoiceStateChange('off');

    // Réactiver l'écoute si l'utilisateur l'avait activée précédemment
    let savedWakeword = false;
    try {
      savedWakeword = localStorage.getItem('jarvis_wakeword_active') === 'true';
    } catch (_) {}

    if (savedWakeword && !document.hidden && jarvisVoice) {
      jarvisVoice.enable().catch(() => {});
    }
  }

  function handleVoiceStateChange(phase, detail = {}) {
    const isMicActive = jarvisVoice && jarvisVoice.enabled;
    const messages = {
      off: '● MICRO COUPÉ',
      starting: '🟢 INITIALISATION...',
      wake: '🟢 EN VEILLE — Dites « Yo Moctar »',
      listening: '🎙️ EN ÉCOUTE... (PARLEZ)',
      processing: '⚡ ANALYSE M.O.C.T.A.R...',
      speaking: '🔊 M.O.C.T.A.R RÉPOND...',
      paused: '⏸️ VEILLE SUSPENDUE',
      error: '⚠️ ERREUR MICRO',
      unsupported: '❌ MICRO NON SUPPORTÉ',
    };

    if (voiceStateBadge) {
      voiceStateBadge.setAttribute('data-state', phase);
      voiceStateBadge.textContent = messages[phase] || (isMicActive ? '🟢 MICRO ACTIF' : '● MICRO COUPÉ');
    }

    if (btnMic) {
      if (isMicActive) {
        btnMic.classList.add('active');
        if (phase === 'listening') {
          btnMic.textContent = '🔴 ENREGISTREMENT...';
        } else {
          btnMic.textContent = '🔴 COUPER LE MICRO';
        }
      } else {
        btnMic.classList.remove('active');
        btnMic.textContent = '🎙️ ACTIVER MICRO';
      }
    }

    // VU-mètre audio en direct
    if (vumeterEl) {
      const bars = vumeterEl.querySelectorAll('.vbar');
      const rms = detail.rms || (jarvisVoice ? jarvisVoice.rms : 0) || 0;
      if ((phase === 'listening' || phase === 'wake') && rms > 0.005) {
        bars.forEach((bar) => {
          const height = Math.min(24, Math.max(4, rms * 180 * (0.8 + Math.random() * 0.5)));
          bar.style.height = `${height}px`;
          bar.style.background = '#00ff88';
        });
      } else {
        bars.forEach((bar) => {
          bar.style.height = '4px';
          bar.style.background = isMicActive ? 'rgba(0, 255, 136, 0.4)' : 'rgba(0, 243, 255, 0.2)';
        });
      }
    }
  }

  function handleVoiceInteractResult(data) {
    if (!data || data.ignored) return;

    // Filtrer les hallucinations Whisper résiduelles
    const rawUser = String(data.user_text || '').trim();
    const isHallucination = HALLUCINATION_BLACK_LIST.some((h) => rawUser.toLowerCase().includes(h));

    if (rawUser && !isHallucination && userTextEl) {
      userTextEl.textContent = `« ${rawUser} »`;
    }

    const reply = data.reply_text || data.reply || '';
    if (reply && assistantTextEl) {
      assistantTextEl.textContent = reply;
    }

    // Basculement 3D automatique si demandé vocalement
    const textToCheck = (rawUser + ' ' + reply + ' ' + (data.title || '')).toLowerCase();
    if (textToCheck.includes('globe') || textToCheck.includes('terre') || textToCheck.includes('earth') || textToCheck.includes('satellite')) {
      applyView('globe', true);
    } else if (textToCheck.includes('quantique') || textToCheck.includes('quantum') || textToCheck.includes('core') || textToCheck.includes('noyau')) {
      applyView('quantum', true);
    } else if (textToCheck.includes('plasma') || textToCheck.includes('réacteur') || textToCheck.includes('arc') || textToCheck.includes('reactor')) {
      applyView('plasma', true);
    }

    if (textToCheck.includes('localise') || textToCheck.includes('rabat') || textToCheck.includes('position')) {
      applyView('globe', true);
      const phi = (90 - userCoords.lat) * (Math.PI / 180);
      const theta = (userCoords.lon + 180) * (Math.PI / 180);
      rotationTargets.globe.x = 0.22;
      rotationTargets.globe.y = -(theta - Math.PI / 2);
    }

    // Gestion de l'audio si présent
    if (data.audio_url) {
      playAudioUrl(data.audio_url);
    } else if (reply) {
      speakJarvisText(reply);
    }
  }

  // Bascule Marche / Arrêt explicite du micro
  async function toggleMicrophone() {
    if (!jarvisVoice) {
      initVoiceEngine();
    }
    if (!jarvisVoice) return;

    if (window.isSecureContext === false) {
      alert("L'accès micro nécessite une connexion sécurisée (HTTPS ou localhost).");
      return;
    }

    try {
      if (jarvisVoice.enabled) {
        // Couper le micro
        jarvisVoice.disable();
        stopAllSpeech();
        try { localStorage.setItem('jarvis_wakeword_active', 'false'); } catch (_) {}
      } else {
        // Allumer le micro et démarrer l'écoute active
        try { window.speechSynthesis?.resume(); } catch (_) {}
        await jarvisVoice.enable();
        await jarvisVoice.startManual();
        try { localStorage.setItem('jarvis_wakeword_active', 'true'); } catch (_) {}
      }
    } catch (err) {
      console.warn("Erreur toggleMicrophone:", err);
      handleVoiceStateChange('error');
    }
  }

  // Interruption totale 0 ms
  function stopAllSpeech() {
    isRecordingManual = false;
    if (jarvisVoice) {
      try { jarvisVoice.stopManual(); } catch (_) {}
      try { jarvisVoice.disable(); } catch (_) {}
    }
    if (window.speechSynthesis) {
      try { window.speechSynthesis.cancel(); } catch (_) {}
    }
    if (activeAudioElement) {
      try {
        activeAudioElement.pause();
        activeAudioElement.currentTime = 0;
      } catch (_) {}
      activeAudioElement = null;
    }
    const audios = document.querySelectorAll('audio');
    audios.forEach((a) => {
      try { a.pause(); a.currentTime = 0; } catch (_) {}
    });

    handleVoiceStateChange('off');
    if (assistantTextEl) {
      assistantTextEl.textContent = "Interrompu et micro désactivé, Moctar.";
    }
  }

  // Synthèse vocale de secours (Web Speech)
  function speakJarvisText(text) {
    const clean = String(text || '')
      .replace(/```[\s\S]*?```/g, '')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/[*_#>[\]()]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

    if (!clean || !window.speechSynthesis) return Promise.resolve(false);
    try { window.speechSynthesis.cancel(); } catch (_) {}

    return new Promise((resolve) => {
      const utter = new SpeechSynthesisUtterance(clean);
      utter.lang = 'fr-FR';
      utter.rate = 1.05;
      utter.pitch = 0.95;
      utter.onend = () => {
        handleVoiceStateChange(jarvisVoice && jarvisVoice.enabled ? 'wake' : 'off');
        resolve(true);
      };
      utter.onerror = () => {
        handleVoiceStateChange(jarvisVoice && jarvisVoice.enabled ? 'wake' : 'off');
        resolve(false);
      };
      window.speechSynthesis.speak(utter);
    });
  }

  // Lecture d'URL audio MP3/WAV
  function playAudioUrl(url) {
    if (!url) return;
    try {
      const audio = new Audio(url);
      activeAudioElement = audio;
      handleVoiceStateChange('speaking');
      audio.onended = () => {
        activeAudioElement = null;
        handleVoiceStateChange(jarvisVoice && jarvisVoice.enabled ? 'wake' : 'off');
      };
      audio.onerror = () => {
        activeAudioElement = null;
        handleVoiceStateChange(jarvisVoice && jarvisVoice.enabled ? 'wake' : 'off');
      };
      audio.play().catch(() => {
        activeAudioElement = null;
        handleVoiceStateChange(jarvisVoice && jarvisVoice.enabled ? 'wake' : 'off');
      });
    } catch (_) {
      handleVoiceStateChange(jarvisVoice && jarvisVoice.enabled ? 'wake' : 'off');
    }
  }

  // Envoi d'ordre texte hybride
  async function submitTextCommand(text) {
    const query = (text || '').trim();
    if (!query) return;

    if (userTextEl) userTextEl.textContent = `« ${query} »`;
    if (assistantTextEl) assistantTextEl.textContent = "Calcul en cours...";
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
      } else {
        if (assistantTextEl) assistantTextEl.textContent = "Erreur de communication avec le serveur, Moctar.";
        handleVoiceStateChange(jarvisVoice && jarvisVoice.enabled ? 'wake' : 'off');
      }
    } catch (e) {
      if (assistantTextEl) assistantTextEl.textContent = "Réseau indisponible, Moctar.";
      handleVoiceStateChange(jarvisVoice && jarvisVoice.enabled ? 'wake' : 'off');
    }
  }

  // --- ÉCOUTEURS D'ÉVÉNEMENTS INTERFACE ---
  function setupUIEventListeners() {
    if (btnPlasmaView) {
      btnPlasmaView.addEventListener('click', (e) => {
        e.preventDefault();
        applyView('plasma', true);
      });
    }
    if (btnQuantumView) {
      btnQuantumView.addEventListener('click', (e) => {
        e.preventDefault();
        applyView('quantum', true);
      });
    }
    if (btnGlobeView) {
      btnGlobeView.addEventListener('click', (e) => {
        e.preventDefault();
        applyView('globe', true);
      });
    }

    if (btnLocate) {
      btnLocate.addEventListener('click', (e) => {
        e.preventDefault();
        applyView('globe', true);

        // Rotation fluide pour orienter Rabat vers l'utilisateur
        const phi = (90 - userCoords.lat) * (Math.PI / 180);
        const theta = (userCoords.lon + 180) * (Math.PI / 180);
        rotationTargets.globe.x = 0.22;
        rotationTargets.globe.y = -(theta - Math.PI / 2);

        if (gpsCoordsEl) {
          gpsCoordsEl.textContent = `GPS : ${userCoords.lat.toFixed(4)}°N, ${userCoords.lon.toFixed(4)}°E (Position Moctar Verrouillée)`;
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

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (jarvisVoice && jarvisVoice.enabled) jarvisVoice.suspend();
        stopAllSpeech();
      } else {
        if (jarvisVoice && jarvisVoice.listeningDesired) jarvisVoice.resume();
      }
    });
  }

  function onResize() {
    if (!container || !renderer || !camera) return;
    const w = container.clientWidth || window.innerWidth;
    const h = container.clientHeight || window.innerHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  }

  // --- HORLOGE NUMÉRIQUE HAUTE PRÉCISION ---
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

  // --- TÉLÉMÉTRIE MATÉRIELLE EN DIRECT SANS RÉVERSION DE VUE ---
  function startTelemetrySync() {
    async function sync() {
      try {
        const res = await fetch('/api/hud/state', { cache: 'no-store' });
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
          return;
        }
      } catch (_) {}

      // Fallback sur /api/snapshot utilisé par le Dashboard NOC
      try {
        const snapRes = await fetch('/api/snapshot', { cache: 'no-store' });
        if (snapRes.ok) {
          const snap = await snapRes.json();
          if (cpuValEl && snap.cpu && snap.cpu.total !== undefined) {
            cpuValEl.textContent = `${snap.cpu.total}%`;
          }
          if (ramValEl && snap.memory && snap.memory.percent !== undefined) {
            ramValEl.textContent = `${snap.memory.percent}%`;
          }
          if (hostValEl && snap.host && snap.host.hostname) {
            hostValEl.textContent = snap.host.hostname;
          }
        }
      } catch (_) {}
    }

    setInterval(sync, 2000);
    sync();
  }

  // --- BOUCLE D'ANIMATION 60 FPS ---
  function animate() {
    requestAnimationFrame(animate);

    if (!renderer || !scene || !camera) return;

    const delta = clock ? clock.getDelta() : 0.016;
    const elapsed = clock ? clock.getElapsedTime() : Date.now() * 0.001;

    // 1. Zoom caméra doux
    currentCameraZ += (targetCameraZ - currentCameraZ) * 0.08;
    camera.position.z = currentCameraZ;
    camera.lookAt(0, 26, 0);

    // 2. Rotation douce des groupes
    const activeGrp = currentView === 'globe' ? globeGroup : currentView === 'quantum' ? quantumGroup : plasmaGroup;
    const tgt = rotationTargets[currentView];

    if (activeGrp && tgt) {
      activeGrp.rotation.y += (tgt.y - activeGrp.rotation.y) * 0.08;
      activeGrp.rotation.x += (tgt.x - activeGrp.rotation.x) * 0.08;

      if (!isDragging) {
        if (currentView === 'globe') {
          tgt.y += delta * 0.09;
        } else {
          tgt.y = Math.sin(elapsed * 0.35) * 0.06;
        }
      }
    }

    if (starField) {
      starField.rotation.y += delta * 0.012;
    }

    // --- ANIMATION MODE 1 : PLASMA ARC REACTOR ---
    if (currentView === 'plasma' && plasmaParticles) {
      const positions = plasmaParticles.geometry.attributes.position.array;
      const speedMult = isRecordingManual ? 2.2 : 1.0;

      for (let i = 0; i < PLASMA_PARTICLE_COUNT; i++) {
        plasmaAngles[i] += delta * plasmaSpeeds[i] * speedMult;
        const u = plasmaAngles[i];
        const r = plasmaRadii[i] + Math.sin(elapsed * 5 + i) * 1.6;

        const idx = i * 3;
        positions[idx] = r * Math.cos(u);
        positions[idx + 1] = r * Math.sin(u);
        positions[idx + 2] = plasmaZOffsets[i] + Math.sin(u * 3 + elapsed) * 2.5;
      }
      plasmaParticles.geometry.attributes.position.needsUpdate = true;

      if (plasmaInnerRing) {
        const pulse = 1.0 + Math.sin(elapsed * 4) * 0.02;
        plasmaInnerRing.scale.set(pulse, pulse, pulse);
      }
      if (plasmaOuterRing) {
        plasmaOuterRing.rotation.z -= delta * 0.45;
      }
    }

    // --- ANIMATION MODE 2 : QUANTUM CORE ---
    if (currentView === 'quantum' && quantumParticles) {
      const positions = quantumParticles.geometry.attributes.position.array;
      const corePulse = 1.0 + Math.sin(elapsed * 5) * 0.06;

      if (quantumCoreMesh) quantumCoreMesh.scale.set(corePulse, corePulse, corePulse);

      quantumFilamentRings.forEach((ring, idx) => {
        ring.rotation.z += delta * (0.35 + idx * 0.18);
      });

      for (let i = 0; i < QUANTUM_PARTICLE_COUNT; i++) {
        const idx = i * 3;
        const ox = quantumOriginals[idx];
        const oy = quantumOriginals[idx + 1];
        const oz = quantumOriginals[idx + 2];

        const breath = 1.0 + Math.sin(elapsed * 3 + i) * 0.1;
        positions[idx] = ox * breath;
        positions[idx + 1] = oy * breath;
        positions[idx + 2] = oz * breath;
      }
      quantumParticles.geometry.attributes.position.needsUpdate = true;
    }

    // --- ANIMATION MODE 3 : GLOBE 3D ---
    if (currentView === 'globe') {
      if (cloudsMesh) cloudsMesh.rotation.y += delta * 0.035;
      if (orbitalRingMesh) orbitalRingMesh.rotation.z += delta * 0.16;
      if (radarPulseRing) {
        const pScale = 1.0 + ((elapsed * 2.6) % 2.5);
        radarPulseRing.scale.set(pScale, pScale, pScale);
        radarPulseRing.material.opacity = Math.max(0, 1.0 - (pScale - 1.0) / 2.5);
      }
    }

    renderer.render(scene, camera);
  }

  // --- INITIALISATION SÉCURISÉE AU CHARGEMENT ---
  function boot() {
    // 1. Fonctions vitales de l'interface : Toujours actives quoi qu'il arrive
    startClock();
    startTelemetrySync();
    setupUIEventListeners();

    // 2. Moteur vocal unifié
    try {
      initVoiceEngine();
    } catch (err) {
      console.warn("Erreur démarrage initVoiceEngine:", err);
    }

    // 3. Moteur 3D WebGL isolé dans un try-catch
    try {
      initThree();
    } catch (err) {
      console.error("Erreur démarrage Three.js WebGL:", err);
    }
  }

  if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // Interface externe
  window.MocHUD = {
    applyView,
    toggleMicrophone,
    stopAllSpeech,
    submitTextCommand,
  };
})();

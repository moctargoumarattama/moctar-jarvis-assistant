/**
 * M.O.C.T.A.R — Moteur Graphique HUD 3D Cybernétique & Assistant Vocal Haute Fidélité
 *
 * Architecture Unifiée sur le Modèle du Dashboard NOC :
 * 1. Protection Anti-Cache & Anti-Collision (window.__MOCTAR_HUD_BOOTED__).
 * 2. Démarrage Synchrone 0 ms : Horloge, Télémétrie (/api/snapshot + /api/events SSE + /api/hud/state)
 *    et Contrôles UI actifs instantanément avant même le chargement 3D.
 * 3. Double Moteur Graphique 60 FPS (Three.js WebGL avec Sprites Radiaux + Fallback Holographique Canvas 2D) :
 *    - Cadrage centré (SCENE_Y_OFFSET = +8, Camera Z = 375, Rayon = 66) : Zéro chevauchement avec les boutons du haut ou le ruban du bas.
 *    - Texture procédurale Terre + Satellite NASA haute résolution.
 * 4. Moteur Vocal Unifié (MoctarVoice + Pont SolarEngine 60 FPS) :
 *    - VU-mètre 5 barres et pulsation 3D pilotés à 60 FPS par le signal RMS réel.
 *    - Zéro duplication audio (voice.js gère le flux TTS nativement).
 */

'use strict';

(function () {
  if (window.__MOCTAR_HUD_BOOTED__) return;
  window.__MOCTAR_HUD_BOOTED__ = true;

  // --- Paramètres & Constantes 3D Cadrés au Millimètre ---
  const SCENE_Y_OFFSET = 18; // Rehaussé pour un dégagement total au-dessus du ruban de sous-titres bas
  const GLOBE_RADIUS = 64;
  const CLOUD_RADIUS = 65.4;
  const ATMOS_RADIUS = 70;
  const PLASMA_PARTICLE_COUNT = 2800;
  const QUANTUM_PARTICLE_COUNT = 2400;
  const STAR_COUNT = 1400;

  // Coordonnées de Moctar (Dell Precision M4800 / Rabat)
  const DEFAULT_COORDS = { lat: 34.020882, lon: -6.841650, label: "Rabat · Dell Precision M4800" };

  const HEX_CYAN = 0x00f3ff;
  const HEX_ELECTRIC = 0x00aaff;
  const HEX_DEEP_BLUE = 0x002db3;
  const HEX_WHITE = 0xffffff;
  const HEX_MAGENTA = 0xd900ff;
  const HEX_GOLD = 0xffb700;
  const HEX_GREEN = 0x00ff88;

  let COLOR_CYAN, COLOR_ELECTRIC, COLOR_DEEP_BLUE, COLOR_WHITE, COLOR_MAGENTA, COLOR_GOLD, COLOR_GREEN;

  // Éléments du DOM
  const $ = (id) => document.getElementById(id);
  const container = $('hud-viewport');
  const canvas = $('hud-canvas');
  const btnPlasmaView = $('btn-view-plasma');
  const btnQuantumView = $('btn-view-quantum');
  const btnGlobeView = $('btn-view-globe');
  const btnLocate = $('btn-action-locate');
  const btnMic = $('hud-mic-btn');
  const btnStop = $('hud-stop-btn');
  const voiceStateBadge = $('voice-state-badge');
  const userTextEl = $('hud-user-transcript');
  const assistantTextEl = $('hud-assistant-reply');
  const gpsCoordsEl = $('hud-gps-coords');
  const cpuValEl = $('hud-cpu-val');
  const ramValEl = $('hud-ram-val');
  const hostValEl = $('hud-host-val');
  const clockTimeEl = $('hud-clock-time');
  const clockDateEl = $('hud-clock-date');
  const cmdForm = $('hud-cmd-form');
  const cmdInput = $('hud-cmd-input');
  const vumeterEl = $('hud-vumeter');

  let renderer, scene, camera;
  let textureLoader;
  let starField;
  let particleGlowTex = null;

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
    quantum: { x: 0.14, y: 0 },
    globe: { x: 0.18, y: -0.6 },
  };
  let currentCameraZ = 370;
  let targetCameraZ = 370;

  // État Audio & Voix en temps réel (Pont SolarEngine 60 FPS)
  let jarvisVoice = null;
  let liveVoiceRms = 0;
  let liveVoiceSpeaking = false;
  let currentVoicePhase = 'off';
  let activeAudioElement = null;

  // Liste noire des hallucinations Whisper
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

  // =========================================================================
  // 0. PONT SOLARENGINE 60 FPS (Identique au Dashboard NOC)
  // =========================================================================
  const HudSolarBridge = {
    setVoiceActivity(rms = 0, isSpeaking = false) {
      const clamped = Number.isFinite(rms) ? Math.max(0, Math.min(1, Number(rms))) : 0;
      liveVoiceRms = clamped > 0.004 ? clamped : liveVoiceRms * 0.6;
      if (liveVoiceRms < 0.002) liveVoiceRms = 0;
      liveVoiceSpeaking = Boolean(isSpeaking || liveVoiceRms > 0.025);
      renderVumeter(liveVoiceRms, liveVoiceSpeaking);
    },
    setVoiceState(nextState) {
      if (nextState === 'idle' && jarvisVoice && jarvisVoice.enabled) {
        handleVoiceStateChange('wake');
      } else if (nextState === 'listening' || nextState === 'speaking' || nextState === 'thinking') {
        handleVoiceStateChange(nextState === 'thinking' ? 'processing' : nextState);
      }
    },
  };
  window.SolarEngine = window.SolarEngine || HudSolarBridge;

  function renderVumeter(rms, speaking) {
    if (!vumeterEl) return;
    const bars = vumeterEl.querySelectorAll('.vbar');
    const isMicOn = Boolean(jarvisVoice && jarvisVoice.enabled);
    if ((speaking || currentVoicePhase === 'listening' || currentVoicePhase === 'speaking') && rms > 0.004) {
      bars.forEach((bar, idx) => {
        const wave = 0.65 + 0.55 * Math.sin(Date.now() * 0.02 + idx * 1.1);
        const h = Math.min(22, Math.max(5, Math.round(rms * 240 * wave)));
        bar.style.height = `${h}px`;
        bar.style.background = '#00ff88';
        bar.style.boxShadow = '0 0 6px #00ff88';
      });
    } else if (currentVoicePhase === 'speaking') {
      bars.forEach((bar, idx) => {
        const h = Math.round(5 + 10 * Math.abs(Math.sin(Date.now() * 0.012 + idx * 0.9)));
        bar.style.height = `${h}px`;
        bar.style.background = '#00f3ff';
        bar.style.boxShadow = '0 0 6px #00f3ff';
      });
    } else {
      bars.forEach((bar) => {
        bar.style.height = '4px';
        bar.style.background = isMicOn ? 'rgba(0, 255, 136, 0.45)' : 'rgba(0, 243, 255, 0.25)';
        bar.style.boxShadow = 'none';
      });
    }
  }

  // =========================================================================
  // 1. TEXTURES PROCÉDURALES HAUTE FIDÉLITÉ (Zéro Écran Noir)
  // =========================================================================
  function createGlowSpriteTexture() {
    const c = document.createElement('canvas');
    c.width = 64;
    c.height = 64;
    const ctx = c.getContext('2d');
    const grad = ctx.createRadialGradient(32, 32, 2, 32, 32, 30);
    grad.addColorStop(0, 'rgba(255, 255, 255, 1)');
    grad.addColorStop(0.35, 'rgba(0, 243, 255, 0.85)');
    grad.addColorStop(0.7, 'rgba(0, 120, 255, 0.25)');
    grad.addColorStop(1, 'rgba(0, 0, 0, 0)');
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(c);
  }

  function createProceduralEarthTexture() {
    const c = document.createElement('canvas');
    c.width = 1024;
    c.height = 512;
    const ctx = c.getContext('2d');

    // Fond océanique profond
    const oceanGrad = ctx.createLinearGradient(0, 0, 0, 512);
    oceanGrad.addColorStop(0, '#04142c');
    oceanGrad.addColorStop(0.5, '#07264e');
    oceanGrad.addColorStop(1, '#04142c');
    ctx.fillStyle = oceanGrad;
    ctx.fillRect(0, 0, 1024, 512);

    // Grille holographique latitude / longitude
    ctx.strokeStyle = 'rgba(0, 243, 255, 0.18)';
    ctx.lineWidth = 1;
    for (let x = 0; x <= 1024; x += 64) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, 512);
      ctx.stroke();
    }
    for (let y = 0; y <= 512; y += 64) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(1024, y);
      ctx.stroke();
    }

    // Silhouettes continentales stylisées (Afrique, Europe, Asie, Amériques)
    ctx.fillStyle = 'rgba(16, 118, 168, 0.72)';
    ctx.strokeStyle = 'rgba(0, 243, 255, 0.65)';
    ctx.lineWidth = 1.5;

    const continents = [
      // Afrique
      [[480, 195], [535, 190], [585, 225], [600, 275], [565, 365], [525, 385], [500, 320], [465, 255]],
      // Europe
      [[475, 110], [545, 95], [595, 115], [580, 175], [520, 180], [470, 165]],
      // Asie
      [[595, 105], [760, 95], [855, 150], [820, 255], [725, 285], [645, 235], [595, 175]],
      // Amérique du Nord
      [[145, 95], [285, 85], [320, 165], [255, 235], [185, 210]],
      // Amérique du Sud
      [[275, 255], [355, 280], [340, 395], [295, 435], [265, 345]],
      // Australie
      [[770, 330], [855, 325], [865, 395], [785, 405]],
    ];

    continents.forEach((poly) => {
      ctx.beginPath();
      poly.forEach(([px, py], idx) => {
        if (idx === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    });

    return new THREE.CanvasTexture(c);
  }

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

  // =========================================================================
  // 2. MOTEUR 3D THREE.JS + FALLBACK HOLOGRAPHIQUE 2D
  // =========================================================================
  function initThree() {
    if (!container || !canvas) return;
    if (typeof THREE === 'undefined') {
      initFallback2DCanvas();
      return;
    }

    initColors();
    clock = new THREE.Clock();
    particleGlowTex = createGlowSpriteTexture();

    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || window.innerHeight;

    scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x02060e, 0.0007);

    camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 4000);
    camera.position.set(0, 0, currentCameraZ);
    camera.lookAt(0, 0, 0);

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
      console.warn("WebGL indisponible, bascule sur le moteur holographique 2D:", e);
      initFallback2DCanvas();
      return;
    }

    textureLoader = new THREE.TextureLoader();

    const ambientLight = new THREE.AmbientLight(0xffffff, 1.15);
    scene.add(ambientLight);

    const sunLight = new THREE.DirectionalLight(0xe8f5ff, 1.9);
    sunLight.position.set(400, 300, 500);
    scene.add(sunLight);

    const rimLight = new THREE.DirectionalLight(0x0088ff, 1.3);
    rimLight.position.set(-400, -200, -400);
    scene.add(rimLight);

    try { createStarfield(); } catch (err) { console.warn(err); }
    try { createCircularPlasmaReactor(); } catch (err) { console.warn(err); }
    try { createRadiantQuantumCore(); } catch (err) { console.warn(err); }
    try { createRealisticEarthGlobe(); } catch (err) { console.warn(err); }

    setup3DControls();

    let savedView = 'plasma';
    try {
      savedView = localStorage.getItem('moctar_hud_view') || 'plasma';
    } catch (_) {}
    applyView(savedView, false);

    animate();
  }

  function createStarfield() {
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(STAR_COUNT * 3);
    const colors = new Float32Array(STAR_COUNT * 3);

    for (let i = 0; i < STAR_COUNT; i++) {
      const idx = i * 3;
      positions[idx] = (Math.random() - 0.5) * 2600;
      positions[idx + 1] = (Math.random() - 0.5) * 2600;
      positions[idx + 2] = (Math.random() - 0.5) * 2600;

      const c = Math.random() > 0.4 ? COLOR_CYAN : COLOR_WHITE;
      colors[idx] = c.r;
      colors[idx + 1] = c.g;
      colors[idx + 2] = c.b;
    }

    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const mat = new THREE.PointsMaterial({
      size: 4.2,
      map: particleGlowTex,
      vertexColors: true,
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    starField = new THREE.Points(geo, mat);
    scene.add(starField);
  }

  function createCircularPlasmaReactor() {
    plasmaGroup = new THREE.Group();
    plasmaGroup.position.y = SCENE_Y_OFFSET;

    // Anneau néon intérieur
    const innerGeo = new THREE.TorusGeometry(54, 1.3, 16, 120);
    const innerMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
    });
    plasmaInnerRing = new THREE.Mesh(innerGeo, innerMat);
    plasmaGroup.add(plasmaInnerRing);

    // Anneau néon extérieur
    const outerGeo = new THREE.TorusGeometry(69, 1.0, 16, 140);
    const outerMat = new THREE.MeshBasicMaterial({
      color: COLOR_ELECTRIC,
      transparent: true,
      opacity: 0.65,
      blending: THREE.AdditiveBlending,
    });
    plasmaOuterRing = new THREE.Mesh(outerGeo, outerMat);
    plasmaGroup.add(plasmaOuterRing);

    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(PLASMA_PARTICLE_COUNT * 3);
    const colors = new Float32Array(PLASMA_PARTICLE_COUNT * 3);

    plasmaAngles = new Float32Array(PLASMA_PARTICLE_COUNT);
    plasmaRadii = new Float32Array(PLASMA_PARTICLE_COUNT);
    plasmaSpeeds = new Float32Array(PLASMA_PARTICLE_COUNT);
    plasmaZOffsets = new Float32Array(PLASMA_PARTICLE_COUNT);

    for (let i = 0; i < PLASMA_PARTICLE_COUNT; i++) {
      plasmaAngles[i] = Math.random() * Math.PI * 2.0;
      plasmaRadii[i] = 50 + Math.random() * 22;
      plasmaSpeeds[i] = (Math.random() > 0.5 ? 1 : -1) * (0.8 + Math.random() * 1.5);
      plasmaZOffsets[i] = (Math.random() - 0.5) * 6.0;

      const u = plasmaAngles[i];
      const r = plasmaRadii[i];

      const idx = i * 3;
      positions[idx] = r * Math.cos(u);
      positions[idx + 1] = r * Math.sin(u);
      positions[idx + 2] = plasmaZOffsets[i];

      const c = Math.random() > 0.35 ? COLOR_CYAN : COLOR_ELECTRIC;
      colors[idx] = c.r;
      colors[idx + 1] = c.g;
      colors[idx + 2] = c.b;
    }

    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const pMat = new THREE.PointsMaterial({
      size: 4.5,
      map: particleGlowTex,
      vertexColors: true,
      transparent: true,
      opacity: 0.92,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    plasmaParticles = new THREE.Points(geo, pMat);
    plasmaGroup.add(plasmaParticles);

    plasmaGroup.rotation.x = rotationTargets.plasma.x;
    plasmaGroup.rotation.y = rotationTargets.plasma.y;

    scene.add(plasmaGroup);
  }

  function createRadiantQuantumCore() {
    quantumGroup = new THREE.Group();
    quantumGroup.position.y = SCENE_Y_OFFSET;

    const coreGeo = new THREE.SphereGeometry(16, 32, 32);
    const coreMat = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.96,
      blending: THREE.AdditiveBlending,
    });
    quantumCoreMesh = new THREE.Mesh(coreGeo, coreMat);
    quantumGroup.add(quantumCoreMesh);

    const haloGeo = new THREE.SphereGeometry(26, 32, 32);
    const haloMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      transparent: true,
      opacity: 0.38,
      blending: THREE.AdditiveBlending,
    });
    quantumHaloMesh = new THREE.Mesh(haloGeo, haloMat);
    quantumGroup.add(quantumHaloMesh);

    const configs = [
      { x: 0, y: 0, z: 0, r: 50, c: COLOR_CYAN },
      { x: Math.PI * 0.25, y: Math.PI * 0.15, z: 0, r: 58, c: COLOR_MAGENTA },
      { x: Math.PI * 0.5, y: 0, z: Math.PI * 0.2, r: 66, c: COLOR_CYAN },
      { x: -Math.PI * 0.25, y: Math.PI * 0.25, z: 0, r: 72, c: COLOR_ELECTRIC },
    ];

    quantumFilamentRings = [];
    configs.forEach((cfg) => {
      const rGeo = new THREE.TorusGeometry(cfg.r, 0.95, 16, 120);
      const rMat = new THREE.MeshBasicMaterial({
        color: cfg.c,
        transparent: true,
        opacity: 0.58,
        blending: THREE.AdditiveBlending,
      });
      const ring = new THREE.Mesh(rGeo, rMat);
      ring.rotation.set(cfg.x, cfg.y, cfg.z);
      quantumFilamentRings.push(ring);
      quantumGroup.add(ring);
    });

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
      const r = 24 + Math.random() * 54;

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
      size: 4.8,
      map: particleGlowTex,
      vertexColors: true,
      transparent: true,
      opacity: 0.88,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });

    quantumParticles = new THREE.Points(geo, pMat);
    quantumGroup.add(quantumParticles);

    quantumGroup.rotation.x = rotationTargets.quantum.x;
    quantumGroup.rotation.y = rotationTargets.quantum.y;

    scene.add(quantumGroup);
  }

  function createRealisticEarthGlobe() {
    globeGroup = new THREE.Group();
    globeGroup.position.y = SCENE_Y_OFFSET;

    const earthGeo = new THREE.SphereGeometry(GLOBE_RADIUS, 64, 64);
    const fallbackTex = createProceduralEarthTexture();

    const earthMat = new THREE.MeshPhongMaterial({
      map: fallbackTex,
      emissive: new THREE.Color(0x041226),
      emissiveIntensity: 0.45,
      specular: new THREE.Color(0x1a3b66),
      shininess: 24,
    });

    // Chargement asynchrone de la vraie texture satellite NASA
    if (textureLoader) {
      textureLoader.load(
        '/static/textures/earth_atmos_2048.jpg',
        (nasaTex) => {
          nasaTex.anisotropy = 4;
          earthMat.map = nasaTex;
          earthMat.needsUpdate = true;
        },
        undefined,
        () => {}
      );
    }

    earthMesh = new THREE.Mesh(earthGeo, earthMat);
    globeGroup.add(earthMesh);

    const cloudsGeo = new THREE.SphereGeometry(CLOUD_RADIUS, 48, 48);
    const cloudsMat = new THREE.MeshLambertMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.0,
      blending: THREE.AdditiveBlending,
    });

    if (textureLoader) {
      textureLoader.load(
        '/static/textures/earth_clouds_1024.png',
        (cTex) => {
          cloudsMat.map = cTex;
          cloudsMat.opacity = 0.72;
          cloudsMat.needsUpdate = true;
        },
        undefined,
        () => {}
      );
    }

    cloudsMesh = new THREE.Mesh(cloudsGeo, cloudsMat);
    globeGroup.add(cloudsMesh);

    const atmosGeo = new THREE.SphereGeometry(ATMOS_RADIUS, 48, 48);
    const atmosMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      side: THREE.BackSide,
      transparent: true,
      opacity: 0.26,
      blending: THREE.AdditiveBlending,
    });
    atmosMesh = new THREE.Mesh(atmosGeo, atmosMat);
    globeGroup.add(atmosMesh);

    const ringGeo = new THREE.TorusGeometry(GLOBE_RADIUS * 1.28, 0.65, 16, 140);
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

    const markerGeo = new THREE.SphereGeometry(2.0, 16, 16);
    const markerMat = new THREE.MeshBasicMaterial({ color: COLOR_GREEN });
    radarMarker = new THREE.Mesh(markerGeo, markerMat);

    const pulseGeo = new THREE.RingGeometry(2.6, 4.2, 32);
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
    if (!radarMarker || typeof THREE === 'undefined') return;
    const phi = (90 - lat) * (Math.PI / 180);
    const theta = (lon + 180) * (Math.PI / 180);

    const r = GLOBE_RADIUS + 1.0;
    const x = -(r * Math.sin(phi) * Math.cos(theta));
    const z = r * Math.sin(phi) * Math.sin(theta);
    const y = r * Math.cos(phi);

    const pos = new THREE.Vector3(x, y, z);
    radarMarker.position.copy(pos);
    radarMarker.lookAt(pos.clone().multiplyScalar(2));
  }

  // --- FALLBACK HOLOGRAPHIQUE 2D (Si Three.js / WebGL absent) ---
  function initFallback2DCanvas() {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    function render2D() {
      requestAnimationFrame(render2D);
      const w = (canvas.width = window.innerWidth);
      const h = (canvas.height = window.innerHeight);
      const cx = w / 2;
      const cy = h * 0.46;
      const t = Date.now() * 0.001;
      const baseR = Math.min(w, h) * 0.22 * (1 + liveVoiceRms * 0.5);

      ctx.clearRect(0, 0, w, h);

      // Halo central
      const grad = ctx.createRadialGradient(cx, cy, baseR * 0.1, cx, cy, baseR * 1.5);
      grad.addColorStop(0, currentView === 'quantum' ? 'rgba(217, 0, 255, 0.35)' : 'rgba(0, 243, 255, 0.35)');
      grad.addColorStop(1, 'rgba(2, 6, 14, 0)');
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, baseR * 1.5, 0, Math.PI * 2);
      ctx.fill();

      // Anneaux concentriques
      ctx.strokeStyle = '#00f3ff';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(cx, cy, baseR, 0, Math.PI * 2);
      ctx.stroke();

      ctx.strokeStyle = 'rgba(0, 170, 255, 0.55)';
      ctx.beginPath();
      ctx.arc(cx, cy, baseR * 1.22, t % (Math.PI * 2), (t % (Math.PI * 2)) + Math.PI * 1.3);
      ctx.stroke();
    }
    render2D();
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

    [btnPlasmaView, btnQuantumView, btnGlobeView].forEach((b) => b && b.classList.remove('active'));
    if (currentView === 'plasma' && btnPlasmaView) btnPlasmaView.classList.add('active');
    if (currentView === 'quantum' && btnQuantumView) btnQuantumView.classList.add('active');
    if (currentView === 'globe' && btnGlobeView) btnGlobeView.classList.add('active');

    targetCameraZ = currentView === 'globe' ? 355 : 370;

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

    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      targetCameraZ += e.deltaY * 0.25;
      targetCameraZ = Math.max(220, Math.min(620, targetCameraZ));
    }, { passive: false });
  }

  // =========================================================================
  // 3. MOTEUR VOCAL UNIFIÉ (Identique au Dashboard NOC)
  // =========================================================================
  function initVoiceEngine() {
    if (!window.MoctarVoice) {
      handleVoiceStateChange('off');
      return;
    }

    jarvisVoice = window.MoctarVoice.create({
      followupMs: 45000,
      mediaDevices: window.navigator ? window.navigator.mediaDevices : null,
      MediaRecorder: window.MediaRecorder,
      AudioContext: window.AudioContext || window.webkitAudioContext,
      Audio: window.Audio,
      SolarEngine: HudSolarBridge,
      interactUrl: '/api/voice/interact',
      onState: handleVoiceStateChange,
      onInteractResult: (data) => handleVoiceInteractResult(data, false),
      speak: speakJarvisText,
      language: 'fr-FR',
    });

    window.jarvisVoice = jarvisVoice;
    handleVoiceStateChange(jarvisVoice.state || 'off');

    let savedWakeword = false;
    try {
      savedWakeword = localStorage.getItem('jarvis_wakeword_active') === 'true';
    } catch (_) {}

    if (savedWakeword && !document.hidden) {
      jarvisVoice.enable().catch(() => {});
    }
  }

  function handleVoiceStateChange(phase, detail = {}) {
    currentVoicePhase = phase || 'off';
    const isMicActive = Boolean(jarvisVoice && (jarvisVoice.listeningDesired ?? jarvisVoice.enabled));

    const messages = {
      off: '● MICRO COUPÉ',
      starting: '🟢 INITIALISATION MICRO...',
      wake: '🟢 À L\'ÉCOUTE — Dites « Yo Moctar »',
      listening: '🎙️ MOCTAR VOUS ÉCOUTE... (PARLEZ)',
      processing: '⚡ ANALYSE M.O.C.T.A.R...',
      speaking: '🔊 M.O.C.T.A.R VOUS PARLE...',
      paused: '⏸️ VEILLE SUSPENDUE',
      error: detail.message || '⚠️ ERREUR MICRO (Vérifiez HTTPS/Permissions)',
      unsupported: '❌ MICRO NON SUPPORTÉ',
    };

    if (voiceStateBadge) {
      voiceStateBadge.setAttribute('data-state', phase);
      voiceStateBadge.textContent = messages[phase] || (isMicActive ? '🟢 MICRO ACTIF' : '● MICRO COUPÉ');
    }

    if (btnMic) {
      if (isMicActive || phase === 'listening' || phase === 'wake' || phase === 'starting') {
        btnMic.classList.add('active');
        btnMic.textContent = phase === 'listening' ? '🔴 PARLEZ MAINTENANT' : '🔴 COUPER LE MICRO';
      } else {
        btnMic.classList.remove('active');
        btnMic.textContent = '🎙️ ACTIVER MICRO';
      }
    }

    if (typeof detail.rms === 'number') {
      HudSolarBridge.setVoiceActivity(detail.rms, Boolean(detail.isSpeaking));
    } else {
      renderVumeter(liveVoiceRms, liveVoiceSpeaking);
    }
  }

  function handleVoiceInteractResult(data, playAudioManually = false) {
    if (!data || data.ignored) return;

    const rawUser = String(data.user_text || '').trim();
    const isHallucination = HALLUCINATION_BLACK_LIST.some((h) => rawUser.toLowerCase().includes(h));

    if (rawUser && !isHallucination && userTextEl) {
      userTextEl.textContent = `« ${rawUser} »`;
    }

    const reply = String(data.reply_text || data.reply || '').trim();
    if (reply && assistantTextEl) {
      assistantTextEl.textContent = reply;
    }

    // Ouverture d'URL externe si demandée (ex: YouTube, Google)
    let targetUrl = data.url;
    if (!targetUrl && Array.isArray(data.tools_used)) {
      for (const tu of data.tools_used) {
        if (tu.data && tu.data.action === 'open_browser' && tu.data.url) {
          targetUrl = tu.data.url;
          break;
        }
      }
    }
    if (targetUrl && (targetUrl.startsWith('http://') || targetUrl.startsWith('https://'))) {
      try { window.open(targetUrl, '_blank', 'noopener,noreferrer'); } catch (_) {}
    }

    // Basculement 3D automatique si demandé vocalement ou par texte
    const textToCheck = (rawUser + ' ' + reply + ' ' + (data.title || '')).toLowerCase();
    if (textToCheck.includes('localise') || textToCheck.includes('rabat') || textToCheck.includes('ma position')) {
      lockOnMoctarPosition();
    } else if (textToCheck.includes('globe') || textToCheck.includes('terre') || textToCheck.includes('earth') || textToCheck.includes('satellite')) {
      applyView('globe', true);
    } else if (textToCheck.includes('quantique') || textToCheck.includes('quantum') || textToCheck.includes('core') || textToCheck.includes('noyau')) {
      applyView('quantum', true);
    } else if (textToCheck.includes('plasma') || textToCheck.includes('réacteur') || textToCheck.includes('arc') || textToCheck.includes('orbe')) {
      applyView('plasma', true);
    }

    // Lecture audio uniquement pour les commandes texte (car voice.js lit déjà l'audio des commandes vocales)
    if (playAudioManually) {
      if (data.audio_url) {
        playAudioUrl(data.audio_url);
      } else if (reply) {
        speakJarvisText(reply);
      }
    }
  }

  function lockOnMoctarPosition() {
    applyView('globe', true);
    const theta = (userCoords.lon + 180) * (Math.PI / 180);
    rotationTargets.globe.x = 0.22;
    rotationTargets.globe.y = -(theta - Math.PI / 2);

    if (gpsCoordsEl) {
      gpsCoordsEl.textContent = `GPS : ${userCoords.lat.toFixed(4)}°N, ${userCoords.lon.toFixed(4)}°E (Position Moctar Verrouillée)`;
    }
    if (assistantTextEl) {
      assistantTextEl.textContent = "Position géographique verrouillée sur le Dell Precision M4800 à Rabat, Moctar.";
    }
  }

  async function toggleMicrophone() {
    if (!jarvisVoice) initVoiceEngine();
    if (!jarvisVoice) {
      handleVoiceStateChange('unsupported');
      return;
    }

    if (window.isSecureContext === false) {
      handleVoiceStateChange('error', { message: '⚠️ HTTPS requis pour autoriser le microphone' });
      return;
    }

    try {
      const isCurrentlyActive = Boolean(jarvisVoice.enabled || jarvisVoice.state === 'listening');
      if (isCurrentlyActive) {
        jarvisVoice.disable();
        stopAllSpeech();
        try { localStorage.setItem('jarvis_wakeword_active', 'false'); } catch (_) {}
      } else {
        try { window.speechSynthesis?.resume(); } catch (_) {}
        if (userTextEl) userTextEl.textContent = '« Micro activé — Parlez maintenant ou dites Yo Moctar... »';
        await jarvisVoice.enable();
        await jarvisVoice.startManual();
        try { localStorage.setItem('jarvis_wakeword_active', 'true'); } catch (_) {}
      }
    } catch (err) {
      console.warn("Erreur toggleMicrophone:", err);
      handleVoiceStateChange('error');
    }
  }

  function stopAllSpeech() {
    if (jarvisVoice) {
      try { jarvisVoice.stopManual(); } catch (_) {}
      try { jarvisVoice.disable(); } catch (_) {}
    }
    if (window.MoctarSpeaker) {
      try {
        window.MoctarSpeaker.pause?.();
        window.MoctarSpeaker.currentTime = 0;
      } catch (_) {}
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
    document.querySelectorAll('audio').forEach((a) => {
      try { a.pause(); a.currentTime = 0; } catch (_) {}
    });

    liveVoiceRms = 0;
    liveVoiceSpeaking = false;
    handleVoiceStateChange('off');
    if (assistantTextEl) {
      assistantTextEl.textContent = "Système vocal interrompu et micro coupé, Moctar.";
    }
  }

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

  async function submitTextCommand(text) {
    const query = (text || '').trim();
    if (!query) return;

    if (userTextEl) userTextEl.textContent = `« ${query} »`;
    if (assistantTextEl) assistantTextEl.textContent = "Exécution en cours...";
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
        handleVoiceInteractResult(data, true);
        return;
      }

      // Fallback sur /api/jarvis/query (endpoint NOC)
      const fallbackRes = await fetch('/api/jarvis/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: query, source: 'hud_web' }),
      });
      if (fallbackRes.ok) {
        const fbData = await fallbackRes.json();
        handleVoiceInteractResult({ user_text: query, reply_text: fbData.reply || '' }, true);
        return;
      }
      if (assistantTextEl) assistantTextEl.textContent = "Erreur de communication avec le serveur, Moctar.";
      handleVoiceStateChange(jarvisVoice && jarvisVoice.enabled ? 'wake' : 'off');
    } catch (_) {
      if (assistantTextEl) assistantTextEl.textContent = "Réseau indisponible, Moctar.";
      handleVoiceStateChange(jarvisVoice && jarvisVoice.enabled ? 'wake' : 'off');
    }
  }

  // =========================================================================
  // 4. ÉCOUTEURS D'ÉVÉNEMENTS, HORLOGE & TÉLÉMÉTRIE TEMPS RÉEL (NOC UNIFIÉ)
  // =========================================================================
  function setupUIEventListeners() {
    if (btnPlasmaView) {
      btnPlasmaView.addEventListener('click', (e) => {
        e.preventDefault();
        applyView('plasma', true);
        if (assistantTextEl) assistantTextEl.textContent = "Mode Réacteur à Arc Plasma activé, Moctar.";
      });
    }
    if (btnQuantumView) {
      btnQuantumView.addEventListener('click', (e) => {
        e.preventDefault();
        applyView('quantum', true);
        if (assistantTextEl) assistantTextEl.textContent = "Mode Cœur Quantique Neuronal activé, Moctar.";
      });
    }
    if (btnGlobeView) {
      btnGlobeView.addEventListener('click', (e) => {
        e.preventDefault();
        applyView('globe', true);
        if (assistantTextEl) assistantTextEl.textContent = "Initialisation du globe terrestre 3D NASA, Moctar.";
      });
    }
    if (btnLocate) {
      btnLocate.addEventListener('click', (e) => {
        e.preventDefault();
        lockOnMoctarPosition();
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

    if (typeof document.addEventListener === 'function') {
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
          if (jarvisVoice && jarvisVoice.enabled) jarvisVoice.suspend();
        } else {
          if (jarvisVoice && jarvisVoice.listeningDesired) jarvisVoice.resume();
        }
      });
    }
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
    tick();
    setInterval(tick, 1000);
  }

  function startTelemetrySync() {
    function applyMetrics(cpu, ram, host) {
      if (cpuValEl && cpu !== undefined && cpu !== null) {
        cpuValEl.textContent = `${Number(cpu).toFixed(1)}%`;
      }
      if (ramValEl && ram !== undefined && ram !== null) {
        ramValEl.textContent = `${Number(ram).toFixed(1)}%`;
      }
      if (hostValEl && host) {
        hostValEl.textContent = host;
      }
    }

    async function sync() {
      // 1. Endpoint NOC /api/snapshot (prioritaire et identique au Dashboard NOC)
      try {
        const snapRes = await fetch('/api/snapshot', { cache: 'no-store', credentials: 'same-origin' });
        if (snapRes.ok) {
          const snap = await snapRes.json();
          const cpu = snap.cpu?.total ?? snap.cpu_percent;
          const ram = snap.memory?.percent ?? snap.ram_percent;
          const host = snap.host?.hostname ?? snap.hostname;
          if (cpu !== undefined || ram !== undefined) {
            applyMetrics(cpu, ram, host);
            return;
          }
        }
      } catch (_) {}

      // 2. Endpoint /api/hud/state
      try {
        const res = await fetch('/api/hud/state', { cache: 'no-store', credentials: 'same-origin' });
        if (res.ok) {
          const data = await res.json();
          applyMetrics(data.cpu_percent, data.ram_percent, data.hostname);
        }
      } catch (_) {}
    }

    sync();
    setInterval(sync, 2000);
  }

  // =========================================================================
  // 5. BOUCLE D'ANIMATION 60 FPS RÉACTIVE À LA VOIX
  // =========================================================================
  function animate() {
    requestAnimationFrame(animate);
    if (!renderer || !scene || !camera) return;

    const delta = clock ? clock.getDelta() : 0.016;
    const elapsed = clock ? clock.getElapsedTime() : Date.now() * 0.001;
    const voiceBoost = 1.0 + liveVoiceRms * 3.2;

    currentCameraZ += (targetCameraZ - currentCameraZ) * 0.08;
    camera.position.z = currentCameraZ;
    camera.lookAt(0, 0, 0);

    const activeGrp = currentView === 'globe' ? globeGroup : currentView === 'quantum' ? quantumGroup : plasmaGroup;
    const tgt = rotationTargets[currentView];

    if (activeGrp && tgt) {
      activeGrp.rotation.y += (tgt.y - activeGrp.rotation.y) * 0.08;
      activeGrp.rotation.x += (tgt.x - activeGrp.rotation.x) * 0.08;

      const phasePulse = currentVoicePhase === 'listening'
        ? Math.sin(elapsed * 4.5) * 0.04
        : currentVoicePhase === 'processing'
        ? Math.sin(elapsed * 7.0) * 0.025
        : 0;
      const breathScale = 1.0 + liveVoiceRms * 0.45 + phasePulse;
      activeGrp.scale.set(breathScale, breathScale, breathScale);

      if (!isDragging) {
        if (currentView === 'globe') {
          const spinMultiplier = currentVoicePhase === 'processing' ? 2.5 : 1.0;
          tgt.y += delta * 0.11 * spinMultiplier;
        } else {
          tgt.y = Math.sin(elapsed * 0.35) * 0.06;
        }
      }
    }

    if (starField) {
      starField.rotation.y += delta * 0.012;
    }

    if (currentView === 'plasma' && plasmaParticles) {
      const positions = plasmaParticles.geometry.attributes.position.array;
      for (let i = 0; i < PLASMA_PARTICLE_COUNT; i++) {
        plasmaAngles[i] += delta * plasmaSpeeds[i] * voiceBoost;
        const u = plasmaAngles[i];
        const r = plasmaRadii[i] + Math.sin(elapsed * 5 + i) * (1.5 + liveVoiceRms * 8);

        const idx = i * 3;
        positions[idx] = r * Math.cos(u);
        positions[idx + 1] = r * Math.sin(u);
        positions[idx + 2] = plasmaZOffsets[i] + Math.sin(u * 3 + elapsed) * 2.2;
      }
      plasmaParticles.geometry.attributes.position.needsUpdate = true;

      if (plasmaInnerRing) {
        const pulse = 1.0 + Math.sin(elapsed * 4) * 0.025 + liveVoiceRms * 0.25;
        plasmaInnerRing.scale.set(pulse, pulse, pulse);
      }
      if (plasmaOuterRing) {
        const pBoost = currentVoicePhase === 'processing' ? 2.2 : 1.0;
        plasmaOuterRing.rotation.z -= delta * 0.45 * voiceBoost * pBoost;
      }
    }

    if (currentView === 'quantum' && quantumParticles) {
      const positions = quantumParticles.geometry.attributes.position.array;
      const phaseCorePulse = currentVoicePhase === 'listening'
        ? Math.sin(elapsed * 6) * 0.08
        : currentVoicePhase === 'processing'
        ? Math.sin(elapsed * 10) * 0.06
        : 0;
      const corePulse = 1.0 + Math.sin(elapsed * 5) * 0.06 + liveVoiceRms * 0.4 + phaseCorePulse;

      if (quantumCoreMesh) quantumCoreMesh.scale.set(corePulse, corePulse, corePulse);

      const qBoost = currentVoicePhase === 'processing' ? 2.5 : 1.0;
      quantumFilamentRings.forEach((ring, idx) => {
        ring.rotation.z += delta * (0.35 + idx * 0.18) * voiceBoost * qBoost;
      });

      for (let i = 0; i < QUANTUM_PARTICLE_COUNT; i++) {
        const idx = i * 3;
        const ox = quantumOriginals[idx];
        const oy = quantumOriginals[idx + 1];
        const oz = quantumOriginals[idx + 2];

        const breath = 1.0 + Math.sin(elapsed * 3 + i) * 0.08 + liveVoiceRms * 0.35;
        positions[idx] = ox * breath;
        positions[idx + 1] = oy * breath;
        positions[idx + 2] = oz * breath;
      }
      quantumParticles.geometry.attributes.position.needsUpdate = true;
    }

    if (currentView === 'globe') {
      if (cloudsMesh) cloudsMesh.rotation.y += delta * 0.035;
      const spinBoost = currentVoicePhase === 'processing' ? 2.8 : 1.0;
      if (orbitalRingMesh) orbitalRingMesh.rotation.z += delta * 0.18 * voiceBoost * spinBoost;
      if (radarPulseRing) {
        const pulseRate = currentVoicePhase === 'listening' ? 4.2 : currentVoicePhase === 'processing' ? 6.0 : 2.6;
        const pScale = 1.0 + ((elapsed * pulseRate) % 2.4);
        radarPulseRing.scale.set(pScale, pScale, pScale);
        radarPulseRing.material.opacity = Math.max(0, 1.0 - (pScale - 1.0) / 2.4);
      }
    }

    renderer.render(scene, camera);
  }

  // =========================================================================
  // 6. DÉMARRAGE IMMÉDIAT & INFAILLIBLE
  // =========================================================================
  function boot() {
    try { startClock(); } catch (e) { console.error(e); }
    try { setupUIEventListeners(); } catch (e) { console.error(e); }
    try { startTelemetrySync(); } catch (e) { console.error(e); }
    try { initVoiceEngine(); } catch (e) { console.error(e); }
    try { initThree(); } catch (e) { console.error(e); initFallback2DCanvas(); }
  }

  if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  window.MocHUD = {
    applyView,
    toggleMicrophone,
    stopAllSpeech,
    submitTextCommand,
    lockOnMoctarPosition,
  };
})();

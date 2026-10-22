/**
 * M.O.C.T.A.R — Moteur Graphique HUD 3D Cybernétique Ultra-Moderne (Three.js)
 * 
 * Basé sur les modèles cinématiques TikTok (@techenclair) :
 * 1. Globe 3D NASA Réaliste (Texture satellite haute résolution, nuages dynamiques, anneau orbital incliné, radar vert).
 * 2. Réacteur à Arc Plasma Fluide (Torus de particules à dynamique turbulente, anneaux néon cyan, réactif à la voix).
 * 3. Cœur Neuronal Quantique (Noyau incandescent blanc-chaud, filaments énergétiques tourbillonnants, nébuleuse cosmique).
 * 
 * Télémétrie système Dell Precision M4800 en direct (CPU, RAM, Host) & Contrôles tactiles / souris complets.
 */

'use strict';

(function () {
  // --- Paramètres & Constantes ---
  const GLOBE_RADIUS = 95;
  const CLOUD_RADIUS = 96.8;
  const ATMOS_RADIUS = 104;
  const TORUS_PARTICLE_COUNT = 3800;
  const QUANTUM_PARTICLE_COUNT = 3200;
  const STAR_COUNT = 1800;

  const DEFAULT_COORDS = { lat: 34.020882, lon: -6.841650, label: "Rabat / Dell Precision M4800" };

  // Palettes cybernétiques Three.js
  const COLOR_CYAN = new THREE.Color(0x00f3ff);
  const COLOR_DEEP_BLUE = new THREE.Color(0x0044ff);
  const COLOR_ELECTRIC = new THREE.Color(0x00d4ff);
  const COLOR_GOLD = new THREE.Color(0xffb700);
  const COLOR_MAGENTA = new THREE.Color(0xd900ff);
  const COLOR_GREEN = new THREE.Color(0x00ff88);
  const COLOR_WHITE = new THREE.Color(0xffffff);

  // Éléments DOM
  const container = document.getElementById('hud-viewport');
  const canvas = document.getElementById('hud-canvas');
  const btnOrbView = document.getElementById('btn-view-orb');
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

  let renderer, scene, camera;
  let textureLoader;
  let starField;

  // 1. Groupes 3D
  let globeGroup, earthMesh, cloudsMesh, atmosMesh, orbitalRingMesh, radarMarker, radarPulseRing;
  let plasmaGroup, plasmaParticles, plasmaTorusInnerRing, plasmaTorusOuterRing;
  let quantumGroup, quantumParticles, quantumCoreMesh, quantumFilamentRings = [];

  // Tableaux de particules
  let plasmaPosArray, plasmaAngleArray, plasmaRadiusArray, plasmaSpeedArray, plasmaColors;
  let quantumPosArray, quantumOrigArray, quantumVelArray, quantumColors;

  // États
  let currentView = 'plasma'; // 'plasma' | 'quantum' | 'globe'
  let voiceState = 'idle';    // 'idle' | 'listening' | 'thinking' | 'speaking'
  let clock = new THREE.Clock();
  let userCoords = { ...DEFAULT_COORDS };
  let isDragging = false;
  let previousMousePos = { x: 0, y: 0 };
  let targetRotation = { x: 0.15, y: -0.4 };
  let cameraTargetZ = 300;
  let currentCameraZ = 300;
  let sseSource = null;
  let speechSynth = window.speechSynthesis || null;

  // --- Initialisation Three.js ---
  function initThree() {
    if (!container || !canvas) return;

    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || window.innerHeight;

    scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x02060e, 0.0009);

    camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 4000);
    camera.position.set(0, 0, cameraTargetZ);

    renderer = new THREE.WebGLRenderer({
      canvas: canvas,
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance',
    });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    textureLoader = new THREE.TextureLoader();

    // Éclairage spatial réaliste
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.85);
    scene.add(ambientLight);

    const sunLight = new THREE.DirectionalLight(0xdff2ff, 1.6);
    sunLight.position.set(350, 250, 400);
    scene.add(sunLight);

    const blueBackLight = new THREE.DirectionalLight(0x0044bb, 0.9);
    blueBackLight.position.set(-300, -100, -300);
    scene.add(blueBackLight);

    // 1. Champ d'étoiles cosmiques
    createStarfield();

    // 2. Vue Réacteur Plasma Arc (Image 5)
    createPlasmaReactor();

    // 3. Vue Cœur Quantique Neuronal (Image 4)
    createQuantumCore();

    // 4. Vue Globe 3D Réaliste NASA (Image 3)
    createRealisticEarthGlobe();

    // Configuration des écouteurs
    setupInteractions();
    setupEventListeners();

    // Appliquer la vue initiale
    applyViewVisibility(currentView);

    // Démarrage de la boucle de rendu
    animate();
  }

  // --- 1. Champ d'étoiles spatiales ---
  function createStarfield() {
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(STAR_COUNT * 3);
    const colors = new Float32Array(STAR_COUNT * 3);

    for (let i = 0; i < STAR_COUNT; i++) {
      const idx = i * 3;
      positions[idx] = (Math.random() - 0.5) * 2600;
      positions[idx + 1] = (Math.random() - 0.5) * 2600;
      positions[idx + 2] = (Math.random() - 0.5) * 2600;

      const c = Math.random() > 0.3 ? COLOR_CYAN : COLOR_WHITE;
      colors[idx] = c.r;
      colors[idx + 1] = c.g;
      colors[idx + 2] = c.b;
    }

    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    const mat = new THREE.PointsMaterial({
      size: 1.6,
      vertexColors: true,
      transparent: true,
      opacity: 0.75,
    });

    starField = new THREE.Points(geo, mat);
    scene.add(starField);
  }

  // --- 2. Réacteur à Arc Plasma Fluide (Image 5) ---
  function createPlasmaReactor() {
    plasmaGroup = new THREE.Group();

    // Anneaux néon intérieurs et extérieurs
    const innerRingGeo = new THREE.TorusGeometry(72, 1.2, 16, 100);
    const ringMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      transparent: true,
      opacity: 0.65,
      blending: THREE.AdditiveBlending,
    });
    plasmaTorusInnerRing = new THREE.Mesh(innerRingGeo, ringMat);
    plasmaGroup.add(plasmaTorusInnerRing);

    const outerRingGeo = new THREE.TorusGeometry(105, 0.8, 16, 120);
    const outerRingMat = new THREE.MeshBasicMaterial({
      color: COLOR_DEEP_BLUE,
      transparent: true,
      opacity: 0.45,
      blending: THREE.AdditiveBlending,
    });
    plasmaTorusOuterRing = new THREE.Mesh(outerRingGeo, outerRingMat);
    plasmaGroup.add(plasmaTorusOuterRing);

    // Nuage toroidal de particules de plasma tourbillonnantes
    const geo = new THREE.BufferGeometry();
    plasmaPosArray = new Float32Array(TORUS_PARTICLE_COUNT * 3);
    plasmaAngleArray = new Float32Array(TORUS_PARTICLE_COUNT);
    plasmaRadiusArray = new Float32Array(TORUS_PARTICLE_COUNT);
    plasmaSpeedArray = new Float32Array(TORUS_PARTICLE_COUNT);
    plasmaColors = new Float32Array(TORUS_PARTICLE_COUNT * 3);

    const majorR = 86; // Rayon majeur du tore
    const minorR = 19; // Rayon du tube

    for (let i = 0; i < TORUS_PARTICLE_COUNT; i++) {
      const u = Math.random() * Math.PI * 2;
      const v = Math.random() * Math.PI * 2;
      const r = minorR * (0.3 + Math.random() * 0.7);

      const x = (majorR + r * Math.cos(v)) * Math.cos(u);
      const y = (majorR + r * Math.cos(v)) * Math.sin(u);
      const z = r * Math.sin(v) + (Math.random() - 0.5) * 8;

      const idx = i * 3;
      plasmaPosArray[idx] = x;
      plasmaPosArray[idx + 1] = y;
      plasmaPosArray[idx + 2] = z;

      plasmaAngleArray[i] = u;
      plasmaRadiusArray[i] = r;
      plasmaSpeedArray[i] = 0.8 + Math.random() * 1.5;

      // Dégradé électrique cyan <-> bleu profond avec pointes de blanc
      const mix = Math.random();
      const col = mix > 0.85 ? COLOR_WHITE : mix > 0.4 ? COLOR_CYAN : COLOR_ELECTRIC;
      plasmaColors[idx] = col.r;
      plasmaColors[idx + 1] = col.g;
      plasmaColors[idx + 2] = col.b;
    }

    geo.setAttribute('position', new THREE.BufferAttribute(plasmaPosArray, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(plasmaColors, 3));

    const pMat = new THREE.PointsMaterial({
      size: 2.8,
      vertexColors: true,
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
    });

    plasmaParticles = new THREE.Points(geo, pMat);
    plasmaGroup.add(plasmaParticles);

    scene.add(plasmaGroup);
  }

  // --- 3. Cœur Neuronal Quantique (Image 4) ---
  function createQuantumCore() {
    quantumGroup = new THREE.Group();

    // Sphère centrale blanche ultra-luminescente
    const coreGeo = new THREE.SphereGeometry(18, 32, 32);
    const coreMat = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.95,
      blending: THREE.AdditiveBlending,
    });
    quantumCoreMesh = new THREE.Mesh(coreGeo, coreMat);
    quantumGroup.add(quantumCoreMesh);

    // Halo d'énergie coronale
    const haloGeo = new THREE.SphereGeometry(30, 32, 32);
    const haloMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      transparent: true,
      opacity: 0.35,
      blending: THREE.AdditiveBlending,
    });
    const haloMesh = new THREE.Mesh(haloGeo, haloMat);
    quantumGroup.add(haloMesh);

    // Filaments et orbites d'énergie elliptiques inclinées
    for (let r = 0; r < 4; r++) {
      const curveGeo = new THREE.TorusGeometry(60 + r * 22, 0.9, 12, 100);
      const curveMat = new THREE.MeshBasicMaterial({
        color: r % 2 === 0 ? COLOR_CYAN : COLOR_MAGENTA,
        transparent: true,
        opacity: 0.45,
        blending: THREE.AdditiveBlending,
      });
      const ring = new THREE.Mesh(curveGeo, curveMat);
      ring.rotation.x = Math.PI * (0.2 + r * 0.18);
      ring.rotation.y = Math.PI * (0.15 * r);
      quantumFilamentRings.push(ring);
      quantumGroup.add(ring);
    }

    // Nébuleuse de particules quantiques rayonnantes
    const geo = new THREE.BufferGeometry();
    quantumPosArray = new Float32Array(QUANTUM_PARTICLE_COUNT * 3);
    quantumOrigArray = new Float32Array(QUANTUM_PARTICLE_COUNT * 3);
    quantumVelArray = new Float32Array(QUANTUM_PARTICLE_COUNT * 3);
    quantumColors = new Float32Array(QUANTUM_PARTICLE_COUNT * 3);

    for (let i = 0; i < QUANTUM_PARTICLE_COUNT; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const rad = 25 + Math.random() * 95;

      const x = rad * Math.sin(phi) * Math.cos(theta);
      const y = rad * Math.sin(phi) * Math.sin(theta);
      const z = rad * Math.cos(phi);

      const idx = i * 3;
      quantumPosArray[idx] = x;
      quantumPosArray[idx + 1] = y;
      quantumPosArray[idx + 2] = z;

      quantumOrigArray[idx] = x;
      quantumOrigArray[idx + 1] = y;
      quantumOrigArray[idx + 2] = z;

      quantumVelArray[idx] = (Math.random() - 0.5) * 0.4;
      quantumVelArray[idx + 1] = (Math.random() - 0.5) * 0.4;
      quantumVelArray[idx + 2] = (Math.random() - 0.5) * 0.4;

      const c = Math.random() > 0.4 ? COLOR_CYAN : COLOR_MAGENTA;
      quantumColors[idx] = c.r;
      quantumColors[idx + 1] = c.g;
      quantumColors[idx + 2] = c.b;
    }

    geo.setAttribute('position', new THREE.BufferAttribute(quantumPosArray, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(quantumColors, 3));

    const pMat = new THREE.PointsMaterial({
      size: 2.4,
      vertexColors: true,
      transparent: true,
      opacity: 0.85,
      blending: THREE.AdditiveBlending,
    });

    quantumParticles = new THREE.Points(geo, pMat);
    quantumGroup.add(quantumParticles);

    scene.add(quantumGroup);
  }

  // --- 4. Globe Terrestre Réaliste NASA (Image 3) ---
  function createRealisticEarthGlobe() {
    globeGroup = new THREE.Group();

    // 1. Texture Satellite NASA Terre
    const earthGeo = new THREE.SphereGeometry(GLOBE_RADIUS, 64, 64);
    
    // Chargement de la texture réelle avec repli procédural élégant
    const earthTex = textureLoader.load(
      '/static/textures/earth_atmos_2048.jpg',
      function (tex) {
        tex.anisotropy = 4;
        earthMesh.material.map = tex;
        earthMesh.material.needsUpdate = true;
      },
      undefined,
      function () {
        // Repli élégant en cas de non disponibilité réseau
        const fallbackCanvas = createCyberEarthTexture();
        earthMesh.material.map = new THREE.CanvasTexture(fallbackCanvas);
        earthMesh.material.needsUpdate = true;
      }
    );

    const earthMat = new THREE.MeshPhongMaterial({
      map: earthTex,
      bumpScale: 0.08,
      specular: new THREE.Color(0x192b45),
      shininess: 18,
    });

    earthMesh = new THREE.Mesh(earthGeo, earthMat);
    globeGroup.add(earthMesh);

    // 2. Couche de Nuages Dynamiques Indépendante
    const cloudsGeo = new THREE.SphereGeometry(CLOUD_RADIUS, 64, 64);
    const cloudsTex = textureLoader.load(
      '/static/textures/earth_clouds_1024.png',
      function (tex) {
        cloudsMesh.material.map = tex;
        cloudsMesh.material.needsUpdate = true;
      },
      undefined,
      function () {}
    );

    const cloudsMat = new THREE.MeshLambertMaterial({
      map: cloudsTex,
      transparent: true,
      opacity: 0.72,
      blending: THREE.AdditiveBlending,
    });

    cloudsMesh = new THREE.Mesh(cloudsGeo, cloudsMat);
    globeGroup.add(cloudsMesh);

    // 3. Halo Atmosphérique Lumineux Cyan
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

    // 4. Anneau Trajectoire Orbitale Incliné Luminescent (Exactement comme Image 3)
    const ringGeo = new THREE.TorusGeometry(GLOBE_RADIUS * 1.55, 0.8, 16, 140);
    const ringMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      transparent: true,
      opacity: 0.6,
      blending: THREE.AdditiveBlending,
    });
    orbitalRingMesh = new THREE.Mesh(ringGeo, ringMat);
    orbitalRingMesh.rotation.x = Math.PI * 0.38;
    orbitalRingMesh.rotation.y = Math.PI * 0.12;
    globeGroup.add(orbitalRingMesh);

    // 5. Marqueur Radar Vert Géolocalisé (Pointe sur Moctar / Dell Precision M4800)
    const markerGeo = new THREE.SphereGeometry(2.6, 16, 16);
    const markerMat = new THREE.MeshBasicMaterial({ color: COLOR_GREEN });
    radarMarker = new THREE.Mesh(markerGeo, markerMat);

    const pulseGeo = new THREE.RingGeometry(3.6, 5.8, 32);
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
    updateRadarCoordinates(userCoords.lat, userCoords.lon);

    scene.add(globeGroup);
  }

  // Texture de repli procédurale au cas où le fichier JPEG est en cours de téléchargement
  function createCyberEarthTexture() {
    const c = document.createElement('canvas');
    c.width = 1024;
    c.height = 512;
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#020b18';
    ctx.fillRect(0, 0, c.width, c.height);

    ctx.fillStyle = '#082846';
    ctx.beginPath();
    ctx.ellipse(c.width * 0.52, c.height * 0.55, 80, 120, 0, 0, Math.PI * 2);
    ctx.ellipse(c.width * 0.28, c.height * 0.45, 90, 110, 0, 0, Math.PI * 2);
    ctx.ellipse(c.width * 0.75, c.height * 0.40, 120, 90, 0, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = '#00f3ff22';
    for (let x = 0; x <= c.width; x += 64) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, c.height);
      ctx.stroke();
    }
    return c;
  }

  // Conversion GPS (Lat, Lon) -> Coordonnées 3D sur la sphère
  function latLonToVector3(lat, lon, radius) {
    const phi = (90 - lat) * (Math.PI / 180);
    const theta = (lon + 180) * (Math.PI / 180);

    const x = -(radius * Math.sin(phi) * Math.cos(theta));
    const z = radius * Math.sin(phi) * Math.sin(theta);
    const y = radius * Math.cos(phi);

    return new THREE.Vector3(x, y, z);
  }

  function updateRadarCoordinates(lat, lon) {
    if (!radarMarker) return;
    const pos = latLonToVector3(lat, lon, GLOBE_RADIUS + 1.2);
    radarMarker.position.copy(pos);
    radarMarker.lookAt(pos.clone().multiplyScalar(2));
  }

  // --- Gestion du Changement de Vue 3D ---
  function setView(viewName, locate = false) {
    const cleanView = (viewName || '').toLowerCase().trim();
    if (['globe', 'earth', 'terre'].includes(cleanView)) {
      currentView = 'globe';
    } else if (['quantum', 'core', 'neural'].includes(cleanView)) {
      currentView = 'quantum';
    } else {
      currentView = 'plasma';
    }

    applyViewVisibility(currentView);

    // Mettre à jour les boutons de vue
    [btnOrbView, btnPlasmaView, btnQuantumView, btnGlobeView].forEach((btn) => {
      if (btn) btn.classList.remove('active');
    });

    if (currentView === 'globe' && btnGlobeView) btnGlobeView.classList.add('active');
    if (currentView === 'plasma' && (btnPlasmaView || btnOrbView)) {
      if (btnPlasmaView) btnPlasmaView.classList.add('active');
      if (btnOrbView) btnOrbView.classList.add('active');
    }
    if (currentView === 'quantum' && btnQuantumView) btnQuantumView.classList.add('active');

    // Géolocalisation ciblée
    if (locate && currentView === 'globe') {
      triggerGeolocation();
    }
  }

  function applyViewVisibility(view) {
    if (globeGroup) globeGroup.visible = view === 'globe';
    if (plasmaGroup) plasmaGroup.visible = view === 'plasma';
    if (quantumGroup) quantumGroup.visible = view === 'quantum';

    if (view === 'globe') {
      cameraTargetZ = 280;
    } else if (view === 'quantum') {
      cameraTargetZ = 290;
    } else {
      cameraTargetZ = 300;
    }
  }

  // --- Gestion des États Vocaux ---
  function setVoiceState(state, userText = null, assistantText = null) {
    voiceState = state;
    if (voiceStateBadge) {
      voiceStateBadge.setAttribute('data-state', state);
      const labels = {
        idle: '● VEILLE ACTIVE',
        listening: '🎙️ ÉCOUTE...',
        thinking: '⚡ ANALYSE...',
        speaking: '🔊 SYNTHÈSE...',
      };
      voiceStateBadge.textContent = labels[state] || '● EN LIGNE';
    }

    if (userText !== null && userTextEl) {
      userTextEl.textContent = userText ? `« ${userText} »` : '';
    }
    if (assistantText !== null && assistantTextEl) {
      assistantTextEl.textContent = assistantText || '';
    }

    // Réactivité visuelle : si réflexion lourde, basculer temporairement sur le cœur quantique
    if (state === 'thinking' && currentView === 'plasma') {
      // Énergie accrue
    }
  }

  // --- Géolocalisation GPS ---
  function triggerGeolocation() {
    if (gpsCoordsEl) gpsCoordsEl.textContent = 'Acquisition GPS en cours...';

    if ('geolocation' in navigator) {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          userCoords.lat = pos.coords.latitude;
          userCoords.lon = pos.coords.longitude;
          userCoords.label = 'Position Fixée (Navigateur)';
          updateRadarCoordinates(userCoords.lat, userCoords.lon);
          if (gpsCoordsEl) {
            gpsCoordsEl.textContent = `GPS : ${userCoords.lat.toFixed(4)}°N, ${userCoords.lon.toFixed(4)}°E (Précision ±${Math.round(pos.coords.accuracy)}m)`;
          }
        },
        () => {
          // Repli sur coordonnées configurées
          updateRadarCoordinates(DEFAULT_COORDS.lat, DEFAULT_COORDS.lon);
          if (gpsCoordsEl) {
            gpsCoordsEl.textContent = `GPS : ${DEFAULT_COORDS.lat.toFixed(4)}°N, ${DEFAULT_COORDS.lon.toFixed(4)}°E (${DEFAULT_COORDS.label})`;
          }
        },
        { timeout: 7000 }
      );
    } else {
      updateRadarCoordinates(DEFAULT_COORDS.lat, DEFAULT_COORDS.lon);
      if (gpsCoordsEl) {
        gpsCoordsEl.textContent = `GPS : ${DEFAULT_COORDS.lat.toFixed(4)}°N, ${DEFAULT_COORDS.lon.toFixed(4)}°E (${DEFAULT_COORDS.label})`;
      }
    }
  }

  // --- Interactions Souris, Touch & Zoom ---
  function setupInteractions() {
    // Rotation par glissement (Souris & Doigt)
    function onPointerDown(e) {
      isDragging = true;
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;
      previousMousePos = { x: clientX, y: clientY };
    }

    function onPointerMove(e) {
      if (!isDragging) return;
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;

      const deltaX = clientX - previousMousePos.x;
      const deltaY = clientY - previousMousePos.y;

      targetRotation.y += deltaX * 0.007;
      targetRotation.x += deltaY * 0.007;

      // Limiter le pitch vertical
      targetRotation.x = Math.max(-Math.PI * 0.45, Math.min(Math.PI * 0.45, targetRotation.x));

      previousMousePos = { x: clientX, y: clientY };
    }

    function onPointerUp() {
      isDragging = false;
    }

    canvas.addEventListener('mousedown', onPointerDown);
    window.addEventListener('mousemove', onPointerMove);
    window.addEventListener('mouseup', onPointerUp);

    canvas.addEventListener('touchstart', onPointerDown, { passive: true });
    window.addEventListener('touchmove', onPointerMove, { passive: true });
    window.addEventListener('touchend', onPointerUp);

    // Zoom à la molette (Wheel Zoom)
    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      cameraTargetZ += e.deltaY * 0.25;
      cameraTargetZ = Math.max(160, Math.min(750, cameraTargetZ));
    }, { passive: false });
  }

  // --- Écouteurs d'Événements UI ---
  function setupEventListeners() {
    if (btnOrbView) btnOrbView.addEventListener('click', () => setView('plasma'));
    if (btnPlasmaView) btnPlasmaView.addEventListener('click', () => setView('plasma'));
    if (btnQuantumView) btnQuantumView.addEventListener('click', () => setView('quantum'));
    if (btnGlobeView) btnGlobeView.addEventListener('click', () => setView('globe'));
    if (btnLocate) btnLocate.addEventListener('click', () => {
      setView('globe', true);
    });

    if (btnStop) {
      btnStop.addEventListener('click', () => {
        if (speechSynth && speechSynth.speaking) {
          speechSynth.cancel();
        }
        setVoiceState('idle', null, 'Lecture audio interrompue, Moctar.');
      });
    }

    if (btnMic) {
      btnMic.addEventListener('click', () => {
        if (voiceState === 'listening') {
          setVoiceState('idle');
        } else {
          setVoiceState('listening', 'Écoute active en cours...', 'Parlez, Moctar...');
        }
      });
    }

    window.addEventListener('resize', onWindowResize);
  }

  function onWindowResize() {
    if (!container || !renderer || !camera) return;
    const w = container.clientWidth || window.innerWidth;
    const h = container.clientHeight || window.innerHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  }

  // --- Horloge Numérique en Direct ---
  function updateClock() {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    const ss = String(now.getSeconds()).padStart(2, '0');
    if (clockTimeEl) clockTimeEl.textContent = `${hh}:${mm}:${ss}`;

    if (clockDateEl) {
      const options = { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' };
      clockDateEl.textContent = now.toLocaleDateString('fr-FR', options).toUpperCase();
    }
  }
  setInterval(updateClock, 1000);
  updateClock();

  // --- Synchronisation de l'État & Télémétrie Matérielle ---
  function setupStateSync() {
    async function fetchHudMetrics() {
      try {
        const res = await fetch('/api/hud/state');
        if (res.ok) {
          const data = await res.json();
          // 1. Télémétrie CPU & RAM réelles
          if (cpuValEl && data.cpu_percent !== undefined) {
            cpuValEl.textContent = `${data.cpu_percent}%`;
          }
          if (ramValEl && data.ram_percent !== undefined) {
            ramValEl.textContent = `${data.ram_percent}%`;
          }
          if (hostValEl && data.hostname) {
            hostValEl.textContent = data.hostname;
          }

          // 2. Vue demandée
          if (data.view && data.view !== currentView) {
            setView(data.view, Boolean(data.locate));
          } else if (data.locate && currentView === 'globe') {
            triggerGeolocation();
          }

          // 3. Statut vocal
          if (data.status && data.status !== voiceState) {
            setVoiceState(data.status, data.user_transcript, data.assistant_reply);
          }
        }
      } catch (err) {}
    }

    // Polling toutes les 1 200 ms pour assurer des métriques toujours fraîches
    setInterval(fetchHudMetrics, 1200);
    fetchHudMetrics();

    // Connexion SSE en continu pour les réactions à 0 ms
    try {
      if (window.EventSource) {
        sseSource = new EventSource('/stream');
        sseSource.onmessage = function (event) {
          try {
            const msg = JSON.parse(event.data);
            if (msg.hud_view && msg.hud_view !== currentView) {
              setView(msg.hud_view, Boolean(msg.hud_locate));
            }
            if (msg.voice_state) {
              setVoiceState(msg.voice_state, msg.user_text, msg.reply_text);
            }
          } catch (e) {}
        };
      }
    } catch (e) {}
  }

  // --- Boucle d'Animation & Moteur Cinématique ---
  function animate() {
    requestAnimationFrame(animate);

    const delta = clock.getDelta();
    const elapsedTime = clock.getElapsedTime();

    // 1. Zoom caméra doux (Lerp)
    currentCameraZ += (cameraTargetZ - currentCameraZ) * 0.08;
    camera.position.z = currentCameraZ;

    // 2. Rotation douce des groupes (Lerp)
    const currentGroup = currentView === 'globe' ? globeGroup : currentView === 'quantum' ? quantumGroup : plasmaGroup;
    if (currentGroup) {
      currentGroup.rotation.y += (targetRotation.y - currentGroup.rotation.y) * 0.08;
      currentGroup.rotation.x += (targetRotation.x - currentGroup.rotation.x) * 0.08;

      // Dérive continue naturelle si l'utilisateur ne manipule pas
      if (!isDragging) {
        targetRotation.y += delta * 0.12;
      }
    }

    // Rotation du champ d'étoiles
    if (starField) {
      starField.rotation.y += delta * 0.015;
    }

    // --- ANIMATION MODE 1 : GLOBE 3D ---
    if (currentView === 'globe') {
      if (cloudsMesh) {
        // Défilement atmosphérique indépendant des nuages
        cloudsMesh.rotation.y += delta * 0.04;
      }
      if (orbitalRingMesh) {
        orbitalRingMesh.rotation.z += delta * 0.2;
      }
      if (radarPulseRing) {
        // Onde pulsante du radar
        const pulseScale = 1.0 + ((elapsedTime * 2.5) % 2.5);
        radarPulseRing.scale.set(pulseScale, pulseScale, pulseScale);
        radarPulseRing.material.opacity = Math.max(0, 1.0 - (pulseScale - 1.0) / 2.5);
      }
    }

    // --- ANIMATION MODE 2 : RÉACTEUR PLASMA ARC ---
    if (currentView === 'plasma' && plasmaParticles) {
      const positions = plasmaParticles.geometry.attributes.position.array;
      const speedMult = voiceState === 'speaking' ? 2.8 : voiceState === 'thinking' ? 2.0 : 1.0;

      for (let i = 0; i < TORUS_PARTICLE_COUNT; i++) {
        plasmaAngleArray[i] += delta * plasmaSpeedArray[i] * speedMult;
        const u = plasmaAngleArray[i];
        const r = plasmaRadiusArray[i] + Math.sin(elapsedTime * 4 + i) * 1.5;
        const v = u * 4.0; // Spirale le long du tore

        const majorR = 86;
        const x = (majorR + r * Math.cos(v)) * Math.cos(u);
        const y = (majorR + r * Math.cos(v)) * Math.sin(u);
        const z = r * Math.sin(v);

        const idx = i * 3;
        positions[idx] = x;
        positions[idx + 1] = y;
        positions[idx + 2] = z;
      }
      plasmaParticles.geometry.attributes.position.needsUpdate = true;

      if (plasmaTorusInnerRing) {
        const pScale = 1.0 + Math.sin(elapsedTime * 3) * 0.02;
        plasmaTorusInnerRing.scale.set(pScale, pScale, pScale);
      }
      if (plasmaTorusOuterRing) {
        plasmaTorusOuterRing.rotation.z -= delta * 0.4;
      }
    }

    // --- ANIMATION MODE 3 : CŒUR QUANTIQUE NEURONAL ---
    if (currentView === 'quantum' && quantumParticles) {
      const positions = quantumParticles.geometry.attributes.position.array;
      const pulse = 1.0 + Math.sin(elapsedTime * 5) * 0.08;

      if (quantumCoreMesh) {
        quantumCoreMesh.scale.set(pulse, pulse, pulse);
      }

      quantumFilamentRings.forEach((ring, idx) => {
        ring.rotation.z += delta * (0.3 + idx * 0.15);
      });

      for (let i = 0; i < QUANTUM_PARTICLE_COUNT; i++) {
        const idx = i * 3;
        positions[idx] += quantumVelArray[idx];
        positions[idx + 1] += quantumVelArray[idx + 1];
        positions[idx + 2] += quantumVelArray[idx + 2];

        // Rétablir vers l'orbite d'origine
        const dist = Math.sqrt(
          positions[idx] ** 2 + positions[idx + 1] ** 2 + positions[idx + 2] ** 2
        );
        if (dist > 130 || dist < 20) {
          positions[idx] = quantumOrigArray[idx];
          positions[idx + 1] = quantumOrigArray[idx + 1];
          positions[idx + 2] = quantumOrigArray[idx + 2];
        }
      }
      quantumParticles.geometry.attributes.position.needsUpdate = true;
    }

    renderer.render(scene, camera);
  }

  // Lancement dès que le DOM est disponible
  window.addEventListener('DOMContentLoaded', () => {
    initThree();
    setupStateSync();
  });

  // Interface globale exposée
  window.MocHUD = {
    setView,
    setVoiceState,
    triggerGeolocation,
  };
})();

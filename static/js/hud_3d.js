/**
 * M.O.C.T.A.R — Moteur Graphique HUD 3D Cybernétique (Three.js)
 * 
 * Pilote en temps réel :
 * 1. Vue Orbe : Sphère de 4 000 particules réactive aux 4 états vocaux (IDLE, LISTENING, THINKING, SPEAKING).
 * 2. Vue Globe : Globe terrestre 3D interactif avec atmosphère lumineuse, géolocalisation GPS et radar pulsant.
 * 3. Synchronisation 0 ms avec le runtime M.O.C.T.A.R via SSE et API.
 */

'use strict';

(function () {
  // --- Configuration & Constantes ---
  const PARTICLE_COUNT = 4000;
  const GLOBE_RADIUS = 100;
  const DEFAULT_COORDS = { lat: 34.020882, lon: -6.841650, label: "Rabat / Poste Dell M4800" };

  // Couleurs cybernétiques
  const COLOR_CYAN = new THREE.Color(0x00f3ff);
  const COLOR_BLUE = new THREE.Color(0x0066ff);
  const COLOR_GOLD = new THREE.Color(0xffb700);
  const COLOR_MAGENTA = new THREE.Color(0xd900ff);
  const COLOR_GREEN = new THREE.Color(0x00ff88);

  // Éléments du DOM
  const container = document.getElementById('hud-viewport');
  const canvas = document.getElementById('hud-canvas');
  const btnNoc = document.getElementById('hud-return-noc');
  const btnOrbView = document.getElementById('btn-view-orb');
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
  const clockTimeEl = document.getElementById('hud-clock-time');
  const clockDateEl = document.getElementById('hud-clock-date');

  let renderer, scene, camera;
  let starField, orbGroup, orbParticles, orbPulseRing;
  let globeGroup, globeMesh, atmosphereMesh, radarMarker, radarPulseRing;
  let orbPositions, orbOriginals, orbVelocities, orbColors;

  let currentView = 'orb'; // 'orb' | 'globe'
  let voiceState = 'idle'; // 'idle' | 'listening' | 'thinking' | 'speaking'
  let targetRotation = { x: 0, y: 0 };
  let isDragging = false;
  let previousMousePosition = { x: 0, y: 0 };
  let userCoords = { ...DEFAULT_COORDS };
  let sseSource = null;

  // --- Initialisation Three.js ---
  function initThree() {
    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || window.innerHeight;

    scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x02060d, 0.001);

    camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 3000);
    camera.position.set(0, 0, 320);

    renderer = new THREE.WebGLRenderer({
      canvas: canvas,
      antialias: true,
      alpha: true,
      powerPreference: 'high-performance',
    });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    // Lumières
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.8);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0x00f3ff, 1.2);
    dirLight.position.set(200, 300, 400);
    scene.add(dirLight);

    // 1. Champ d'étoiles spatiales
    createStarfield();

    // 2. Vue Orbe de Particules
    createParticleOrb();

    // 3. Vue Globe Terrestre
    createInteractiveGlobe();

    // Événements
    setupEventListeners();

    // Redimensionnement
    window.addEventListener('resize', onWindowResize);

    // Démarrage de la boucle d'animation
    animate();
  }

  // --- 1. Champ d'étoiles ---
  function createStarfield() {
    const starCount = 1500;
    const starGeometry = new THREE.BufferGeometry();
    const starPositions = new Float32Array(starCount * 3);

    for (let i = 0; i < starCount * 3; i += 3) {
      starPositions[i] = (Math.random() - 0.5) * 2000;
      starPositions[i + 1] = (Math.random() - 0.5) * 2000;
      starPositions[i + 2] = (Math.random() - 0.5) * 2000;
    }

    starGeometry.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
    const starMaterial = new THREE.PointsMaterial({
      color: 0x88ccff,
      size: 1.5,
      transparent: true,
      opacity: 0.6,
    });

    starField = new THREE.Points(starGeometry, starMaterial);
    scene.add(starField);
  }

  // --- 2. Orbe de Particules 3D (4 000 particules) ---
  function createParticleOrb() {
    orbGroup = new THREE.Group();

    const geometry = new THREE.BufferGeometry();
    orbPositions = new Float32Array(PARTICLE_COUNT * 3);
    orbOriginals = new Float32Array(PARTICLE_COUNT * 3);
    orbVelocities = new Float32Array(PARTICLE_COUNT * 3);
    orbColors = new Float32Array(PARTICLE_COUNT * 3);

    const radius = 75;

    // Distribution sphérique de Fibonacci
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const phi = Math.acos(-1 + (2 * i) / PARTICLE_COUNT);
      const theta = Math.sqrt(PARTICLE_COUNT * Math.PI) * phi;

      const x = radius * Math.cos(theta) * Math.sin(phi);
      const y = radius * Math.sin(theta) * Math.sin(phi);
      const z = radius * Math.cos(phi);

      const idx = i * 3;
      orbPositions[idx] = x;
      orbPositions[idx + 1] = y;
      orbPositions[idx + 2] = z;

      orbOriginals[idx] = x;
      orbOriginals[idx + 1] = y;
      orbOriginals[idx + 2] = z;

      orbVelocities[idx] = (Math.random() - 0.5) * 0.4;
      orbVelocities[idx + 1] = (Math.random() - 0.5) * 0.4;
      orbVelocities[idx + 2] = (Math.random() - 0.5) * 0.4;

      // Dégradé cyan / bleu par défaut
      orbColors[idx] = COLOR_CYAN.r;
      orbColors[idx + 1] = COLOR_CYAN.g;
      orbColors[idx + 2] = COLOR_CYAN.b;
    }

    geometry.setAttribute('position', new THREE.BufferAttribute(orbPositions, 3));
    geometry.setAttribute('color', new THREE.BufferAttribute(orbColors, 3));

    const material = new THREE.PointsMaterial({
      size: 2.2,
      vertexColors: true,
      transparent: true,
      opacity: 0.85,
      blending: THREE.AdditiveBlending,
    });

    orbParticles = new THREE.Points(geometry, material);
    orbGroup.add(orbParticles);

    // Anneau d'onde énergétique extérieur (pulse pendant SPEAKING)
    const ringGeo = new THREE.RingGeometry(85, 87, 64);
    const ringMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.35,
      blending: THREE.AdditiveBlending,
    });
    orbPulseRing = new THREE.Mesh(ringGeo, ringMat);
    orbGroup.add(orbPulseRing);

    scene.add(orbGroup);
  }

  // --- 3. Globe Terrestre 3D Interactif & Radar GPS ---
  function createInteractiveGlobe() {
    globeGroup = new THREE.Group();

    // Sphère principale avec texture procédurale cybernétique
    const globeGeo = new THREE.SphereGeometry(GLOBE_RADIUS, 64, 64);
    const globeCanvas = generateProceduralEarthCanvas();
    const globeTexture = new THREE.CanvasTexture(globeCanvas);

    const globeMat = new THREE.MeshPhongMaterial({
      map: globeTexture,
      bumpScale: 0.05,
      specular: new THREE.Color(0x112244),
      shininess: 15,
      transparent: true,
      opacity: 0.95,
    });

    globeMesh = new THREE.Mesh(globeGeo, globeMat);
    globeGroup.add(globeMesh);

    // Atmosphère lumineuse cyan
    const atmosGeo = new THREE.SphereGeometry(GLOBE_RADIUS * 1.08, 64, 64);
    const atmosMat = new THREE.MeshBasicMaterial({
      color: COLOR_CYAN,
      side: THREE.BackSide,
      transparent: true,
      opacity: 0.22,
      blending: THREE.AdditiveBlending,
    });
    atmosphereMesh = new THREE.Mesh(atmosGeo, atmosMat);
    globeGroup.add(atmosphereMesh);

    // Marqueur radar vert pour géolocalisation
    const markerGeo = new THREE.SphereGeometry(2.5, 16, 16);
    const markerMat = new THREE.MeshBasicMaterial({ color: COLOR_GREEN });
    radarMarker = new THREE.Mesh(markerGeo, markerMat);

    const pulseGeo = new THREE.RingGeometry(3.5, 5.0, 32);
    const pulseMat = new THREE.MeshBasicMaterial({
      color: COLOR_GREEN,
      side: THREE.DoubleSide,
      transparent: true,
      opacity: 0.8,
      blending: THREE.AdditiveBlending,
    });
    radarPulseRing = new THREE.Mesh(pulseGeo, pulseMat);

    radarMarker.add(radarPulseRing);
    globeGroup.add(radarMarker);

    // Positionner initialement le radar sur Rabat / Moctar
    updateRadarPosition(DEFAULT_COORDS.lat, DEFAULT_COORDS.lon);

    // Le globe est initialement masqué (mode orbe par défaut)
    globeGroup.visible = false;
    scene.add(globeGroup);
  }

  // Génération procédurale de la carte du globe (continents + méridiens cybernétiques)
  function generateProceduralEarthCanvas() {
    const c = document.createElement('canvas');
    c.width = 2048;
    c.height = 1024;
    const ctx = c.getContext('2d');

    // Fond océan spatial profond
    ctx.fillStyle = '#030b17';
    ctx.fillRect(0, 0, c.width, c.height);

    // Grille de méridiens et parallèles (lignes de coordonnées cyan subtiles)
    ctx.strokeStyle = '#00f3ff18';
    ctx.lineWidth = 1.2;

    for (let x = 0; x <= c.width; x += c.width / 24) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, c.height);
      ctx.stroke();
    }
    for (let y = 0; y <= c.height; y += c.height / 12) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(c.width, y);
      ctx.stroke();
    }

    // Équateur en surbrillance
    ctx.strokeStyle = '#00f3ff44';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, c.height / 2);
    ctx.lineTo(c.width, c.height / 2);
    ctx.stroke();

    // Rendu des masses continentales simplifiées en cyan luminescent
    ctx.fillStyle = '#08233d';
    ctx.strokeStyle = '#00f3ff';
    ctx.lineWidth = 2.5;

    // Afrique & Europe
    ctx.beginPath();
    ctx.ellipse(c.width * 0.52, c.height * 0.55, 140, 220, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    // Europe
    ctx.beginPath();
    ctx.ellipse(c.width * 0.53, c.height * 0.30, 110, 80, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    // Amériques
    ctx.beginPath();
    ctx.ellipse(c.width * 0.28, c.height * 0.35, 130, 120, -0.2, 0, Math.PI * 2);
    ctx.ellipse(c.width * 0.33, c.height * 0.68, 100, 160, 0.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    // Asie
    ctx.beginPath();
    ctx.ellipse(c.width * 0.72, c.height * 0.33, 230, 140, 0.1, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    // Australie
    ctx.beginPath();
    ctx.ellipse(c.width * 0.85, c.height * 0.75, 80, 60, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    return c;
  }

  // Conversion Coordonnées GPS (Lat, Lon) -> Coordonnées 3D (X, Y, Z)
  function latLonToVector3(lat, lon, radius) {
    const phi = (90 - lat) * (Math.PI / 180);
    const theta = (lon + 180) * (Math.PI / 180);

    const x = -(radius * Math.sin(phi) * Math.cos(theta));
    const z = radius * Math.sin(phi) * Math.sin(theta);
    const y = radius * Math.cos(phi);

    return new THREE.Vector3(x, y, z);
  }

  function updateRadarPosition(lat, lon) {
    const pos = latLonToVector3(lat, lon, GLOBE_RADIUS + 1.5);
    radarMarker.position.copy(pos);
    radarMarker.lookAt(0, 0, 0);

    if (gpsCoordsEl) {
      const latDir = lat >= 0 ? 'N' : 'S';
      const lonDir = lon >= 0 ? 'E' : 'W';
      gpsCoordsEl.textContent = `LAT: ${Math.abs(lat).toFixed(4)}° ${latDir} | LON: ${Math.abs(lon).toFixed(4)}° ${lonDir} | GPS_LOCK`;
    }
  }

  // Oriente la caméra du globe vers une latitude/longitude cible
  function centerGlobeOnCoordinates(lat, lon) {
    const phi = (lat * Math.PI) / 180;
    const theta = ((lon + 90) * Math.PI) / 180;

    targetRotation.x = phi;
    targetRotation.y = -theta;
  }

  // --- Gestion du Basculement de Vue (Orbe <-> Globe) ---
  function setView(viewName, locate = false) {
    currentView = viewName === 'globe' ? 'globe' : 'orb';

    if (currentView === 'globe') {
      orbGroup.visible = false;
      globeGroup.visible = true;
      if (btnOrbView) btnOrbView.classList.remove('active');
      if (btnGlobeView) btnGlobeView.classList.add('active');

      if (locate) {
        triggerGeolocation();
      }
    } else {
      orbGroup.visible = true;
      globeGroup.visible = false;
      if (btnOrbView) btnOrbView.classList.add('active');
      if (btnGlobeView) btnGlobeView.classList.remove('active');
    }
  }

  // Géolocalisation via l'API du navigateur avec repli automatique
  function triggerGeolocation() {
    if (gpsCoordsEl) {
      gpsCoordsEl.textContent = "ACQUISITION GPS EN COURS...";
    }

    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const lat = position.coords.latitude;
          const lon = position.coords.longitude;
          userCoords = { lat, lon, label: "Position Détectée" };
          updateRadarPosition(lat, lon);
          centerGlobeOnCoordinates(lat, lon);
        },
        (error) => {
          console.warn("Géolocalisation refusée ou indisponible, repli sur coordonnées par défaut :", error);
          userCoords = { ...DEFAULT_COORDS };
          updateRadarPosition(DEFAULT_COORDS.lat, DEFAULT_COORDS.lon);
          centerGlobeOnCoordinates(DEFAULT_COORDS.lat, DEFAULT_COORDS.lon);
        },
        { timeout: 6000, enableHighAccuracy: true }
      );
    } else {
      updateRadarPosition(DEFAULT_COORDS.lat, DEFAULT_COORDS.lon);
      centerGlobeOnCoordinates(DEFAULT_COORDS.lat, DEFAULT_COORDS.lon);
    }
  }

  // --- Gestion des États Vocaux de l'Orbe ---
  function setVoiceState(newState, userText = null, assistantText = null) {
    voiceState = newState;

    if (voiceStateBadge) {
      voiceStateBadge.setAttribute('data-state', newState);
      const labels = {
        idle: 'SYSTÈME NOMINAL',
        listening: 'ÉCOUTE ACTIVE...',
        thinking: 'RÉFLEXION / ANALYSE...',
        speaking: 'M.O.C.T.A.R TRANSMISSION...',
      };
      voiceStateBadge.textContent = labels[newState] || newState.toUpperCase();
    }

    if (userText && userTextEl) {
      userTextEl.textContent = `VOUS > ${userText}`;
    }
    if (assistantText && assistantTextEl) {
      assistantTextEl.textContent = `M.O.C.T.A.R > ${assistantText}`;
    }
  }

  // --- Boucle d'Animation Principale ---
  let time = 0;
  function animate() {
    requestAnimationFrame(animate);
    time += 0.016;

    // Étoiles de fond
    if (starField) {
      starField.rotation.y += 0.0003;
    }

    // A. Animation Orbe de Particules
    if (orbGroup && orbGroup.visible) {
      const posAttr = orbParticles.geometry.attributes.position;
      const colAttr = orbParticles.geometry.attributes.color;

      let speedMult = 1.0;
      let targetColor = COLOR_CYAN;

      if (voiceState === 'listening') {
        speedMult = 1.8;
        targetColor = COLOR_GOLD;
      } else if (voiceState === 'thinking') {
        speedMult = 3.5;
        targetColor = COLOR_MAGENTA;
      } else if (voiceState === 'speaking') {
        speedMult = 2.2;
        targetColor = COLOR_CYAN;
      }

      orbGroup.rotation.y += 0.005 * speedMult;
      orbGroup.rotation.x += 0.002 * speedMult;

      // Déformation organique des particules selon l'état
      for (let i = 0; i < PARTICLE_COUNT; i++) {
        const idx = i * 3;
        const ox = orbOriginals[idx];
        const oy = orbOriginals[idx + 1];
        const oz = orbOriginals[idx + 2];

        let offset = 0;
        if (voiceState === 'listening') {
          offset = Math.sin(time * 5 + i * 0.1) * 8.0;
        } else if (voiceState === 'thinking') {
          offset = Math.cos(time * 8 + i * 0.2) * 12.0;
        } else if (voiceState === 'speaking') {
          offset = Math.sin(time * 6 + (ox + oy) * 0.05) * 10.0;
        } else {
          offset = Math.sin(time * 1.5 + i * 0.05) * 2.5;
        }

        const scale = 1.0 + offset / 75.0;
        posAttr.array[idx] = ox * scale;
        posAttr.array[idx + 1] = oy * scale;
        posAttr.array[idx + 2] = oz * scale;

        // Transition progressive de couleur
        colAttr.array[idx] += (targetColor.r - colAttr.array[idx]) * 0.08;
        colAttr.array[idx + 1] += (targetColor.g - colAttr.array[idx + 1]) * 0.08;
        colAttr.array[idx + 2] += (targetColor.b - colAttr.array[idx + 2]) * 0.08;
      }

      posAttr.needsUpdate = true;
      colAttr.needsUpdate = true;

      // Pulsation de l'anneau externe
      if (orbPulseRing) {
        if (voiceState === 'speaking') {
          const s = 1.0 + Math.sin(time * 8) * 0.15;
          orbPulseRing.scale.set(s, s, s);
          orbPulseRing.material.opacity = 0.6;
        } else {
          orbPulseRing.scale.set(1.0, 1.0, 1.0);
          orbPulseRing.material.opacity = 0.2;
        }
      }
    }

    // B. Animation Globe Terrestre
    if (globeGroup && globeGroup.visible) {
      if (!isDragging) {
        globeGroup.rotation.y += (targetRotation.y - globeGroup.rotation.y) * 0.05;
        globeGroup.rotation.x += (targetRotation.x - globeGroup.rotation.x) * 0.05;

        // Rotation lente naturelle quand aucune interaction
        targetRotation.y += 0.001;
      }

      // Pulsation du radar vert
      if (radarPulseRing) {
        const pulseScale = 1.0 + (Math.sin(time * 6) + 1.0) * 0.7;
        radarPulseRing.scale.set(pulseScale, pulseScale, pulseScale);
        radarPulseRing.material.opacity = 0.9 - (pulseScale - 1.0) * 0.5;
      }
    }

    renderer.render(scene, camera);
  }

  // --- Gestion du Redimensionnement ---
  function onWindowResize() {
    if (!camera || !renderer) return;
    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || window.innerHeight;

    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);
  }

  // --- Écouteurs d'Événements & Contrôles Souris ---
  function setupEventListeners() {
    // Boutons de navigation
    if (btnNoc) {
      btnNoc.addEventListener('click', () => { window.location.href = '/'; });
    }
    if (btnOrbView) {
      btnOrbView.addEventListener('click', () => { setView('orb'); notifyViewChange('orb'); });
    }
    if (btnGlobeView) {
      btnGlobeView.addEventListener('click', () => { setView('globe'); notifyViewChange('globe'); });
    }
    if (btnLocate) {
      btnLocate.addEventListener('click', () => { setView('globe', true); notifyViewChange('globe', true); });
    }

    // Commandes vocales rapides
    if (btnMic) {
      btnMic.addEventListener('click', () => {
        setVoiceState('listening');
        // Déclenche l'enregistrement audio si disponible sur la fenêtre parente
        if (window.MocVoice && typeof window.MocVoice.startRecording === 'function') {
          window.MocVoice.startRecording();
        }
      });
    }
    if (btnStop) {
      btnStop.addEventListener('click', () => {
        setVoiceState('idle');
        fetch('/api/voice/stop', { method: 'POST' }).catch(() => {});
      });
    }

    // Drag-to-rotate pour le Globe terrestre
    canvas.addEventListener('mousedown', (e) => {
      if (currentView !== 'globe') return;
      isDragging = true;
      previousMousePosition = { x: e.clientX, y: e.clientY };
    });

    window.addEventListener('mouseup', () => { isDragging = false; });

    window.addEventListener('mousemove', (e) => {
      if (!isDragging || currentView !== 'globe') return;
      const deltaX = e.clientX - previousMousePosition.x;
      const deltaY = e.clientY - previousMousePosition.y;

      globeGroup.rotation.y += deltaX * 0.005;
      globeGroup.rotation.x += deltaY * 0.005;

      targetRotation.y = globeGroup.rotation.y;
      targetRotation.x = globeGroup.rotation.x;

      previousMousePosition = { x: e.clientX, y: e.clientY };
    });

    // Zoom molette
    canvas.addEventListener('wheel', (e) => {
      camera.position.z = Math.max(180, Math.min(600, camera.position.z + e.deltaY * 0.3));
    }, { passive: true });
  }

  function notifyViewChange(viewName, locate = false) {
    fetch('/api/hud/view', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ view: viewName, locate: locate }),
    }).catch(() => {});
  }

  // --- Horloge Numérique & Données Télémétriques ---
  function updateHeaderClock() {
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, '0');
    const mm = String(now.getMinutes()).padStart(2, '0');
    const ss = String(now.getSeconds()).padStart(2, '0');
    if (clockTimeEl) clockTimeEl.textContent = `${hh}:${mm}:${ss}`;

    const dd = String(now.getDate()).padStart(2, '0');
    const mo = String(now.getMonth() + 1).padStart(2, '0');
    const yyyy = now.getFullYear();
    if (clockDateEl) clockDateEl.textContent = `${dd}/${mo}/${yyyy}`;
  }
  setInterval(updateHeaderClock, 1000);
  updateHeaderClock();

  // --- Synchronisation SSE et Polling d'état ---
  function setupStateSync() {
    // 1. Polling régulier de l'état HUD (/api/hud/state)
    async function checkHudState() {
      try {
        const res = await fetch('/api/hud/state');
        if (res.ok) {
          const data = await res.json();
          if (data.view && data.view !== currentView) {
            setView(data.view, Boolean(data.locate));
          } else if (data.locate && currentView === 'globe') {
            triggerGeolocation();
          }

          if (data.status && data.status !== voiceState) {
            setVoiceState(data.status, data.user_transcript, data.assistant_reply);
          }
        }
      } catch (e) {
        // Mode déconnecté gracieux
      }
    }
    setInterval(checkHudState, 800);

    // 2. Récupération CPU / RAM en direct
    async function updateSystemMetrics() {
      try {
        const res = await fetch('/api/status');
        if (res.ok) {
          const status = await res.json();
          if (cpuValEl && status.cpu_percent !== undefined) {
            cpuValEl.textContent = `${status.cpu_percent}%`;
          }
          if (ramValEl && status.ram_percent !== undefined) {
            ramValEl.textContent = `${status.ram_percent}%`;
          }
        }
      } catch (e) {}
    }
    setInterval(updateSystemMetrics, 2000);
    updateSystemMetrics();

    // 3. Écoute SSE si disponible
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
          } catch (err) {}
        };
      }
    } catch (e) {}
  }

  // Initialisation au chargement du DOM
  window.addEventListener('DOMContentLoaded', () => {
    initThree();
    setupStateSync();
  });

  // Export pour les contrôles externes
  window.MocHUD = {
    setView,
    setVoiceState,
    triggerGeolocation,
  };
})();

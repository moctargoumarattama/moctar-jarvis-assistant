/**
 * M.O.C.T.A.R · COSMOS ENGINE
 * Système solaire 3D cinématique en fond d'écran + Moteur de modales orbitales douces.
 * Performance 60 FPS, zéro défilement, réactivité instantanée.
 */
(() => {
  'use strict';

  /* ==========================================================================
     1. CONFIGURATION DU SYSTÈME SOLAIRE & DES MODULES ORBITAUX
     ========================================================================== */
  const PLANET_DEFS = [
    {
      id: 0,
      name: 'Mercure',
      modalId: 'hardware-modal',
      color: '#c9b8a6',
      glow: 'rgba(201, 184, 166, 0.6)',
      baseRadius: 0.16,
      orbitTilt: 0.08,
      speed: 0.85,
      size: 5,
      orbitEcc: 0.06,
      desc: 'Ressources matérielles'
    },
    {
      id: 1,
      name: 'Vénus',
      modalId: 'services-modal',
      color: '#e8c27a',
      glow: 'rgba(232, 194, 122, 0.65)',
      baseRadius: 0.23,
      orbitTilt: 0.05,
      speed: 0.62,
      size: 7.5,
      orbitEcc: 0.02,
      desc: 'Applications & Services'
    },
    {
      id: 2,
      name: 'Terre',
      modalId: 'network-modal',
      color: '#4f9de8',
      glow: 'rgba(79, 157, 232, 0.7)',
      baseRadius: 0.31,
      orbitTilt: 0.03,
      speed: 0.48,
      size: 8.5,
      orbitEcc: 0.03,
      hasAtmosphere: true,
      hasMoon: true,
      desc: 'Infrastructure & Réseau'
    },
    {
      id: 3,
      name: 'Mars',
      modalId: 'sentinel-modal',
      color: '#e0714f',
      glow: 'rgba(224, 113, 79, 0.7)',
      baseRadius: 0.38,
      orbitTilt: 0.07,
      speed: 0.39,
      size: 6.5,
      orbitEcc: 0.07,
      desc: 'Radar Sentinel & VPS'
    },
    {
      id: 4,
      name: 'Jupiter',
      modalId: 'live-metrics-dialog',
      color: '#d9a877',
      glow: 'rgba(217, 168, 119, 0.6)',
      baseRadius: 0.47,
      orbitTilt: 0.04,
      speed: 0.26,
      size: 15,
      orbitEcc: 0.04,
      isBanded: true,
      desc: 'Courbes en direct'
    },
    {
      id: 5,
      name: 'Saturne',
      modalId: 'scheduler-modal',
      color: '#e6d08f',
      glow: 'rgba(230, 208, 143, 0.6)',
      baseRadius: 0.58,
      orbitTilt: 0.06,
      speed: 0.18,
      size: 12,
      orbitEcc: 0.05,
      hasRings: true,
      desc: 'Planificateur'
    },
    {
      id: 6,
      name: 'Uranus',
      modalId: 'cyberdefense-modal',
      color: '#8fe3e8',
      glow: 'rgba(143, 227, 232, 0.65)',
      baseRadius: 0.69,
      orbitTilt: 0.05,
      speed: 0.12,
      size: 9.5,
      orbitEcc: 0.03,
      desc: 'Cyberdéfense'
    },
    {
      id: 7,
      name: 'Neptune',
      modalId: 'journal-modal',
      color: '#6f8cff',
      glow: 'rgba(111, 140, 255, 0.7)',
      baseRadius: 0.81,
      orbitTilt: 0.04,
      speed: 0.09,
      size: 9,
      orbitEcc: 0.02,
      desc: 'Journal d’événements'
    }
  ];

  /* ==========================================================================
     2. GESTIONNAIRE UNIFIÉ DES MODALES (OUVERTURE & FERMETURE DOUCE)
     ========================================================================== */
  const ModalManager = {
    activeModal: null,
    isClosing: false,

    init() {
      // Interception des déclencheurs data-open
      document.addEventListener('click', (e) => {
        const trigger = e.target.closest('[data-open]');
        if (trigger) {
          e.preventDefault();
          const targetId = trigger.dataset.open;
          this.open(targetId, trigger);
        }
      });

      // Boutons de fermeture dans les modales
      document.addEventListener('click', (e) => {
        const closeBtn = e.target.closest('[data-close]');
        if (closeBtn) {
          e.preventDefault();
          const modal = closeBtn.closest('dialog');
          if (modal) this.close(modal);
        }
      });

      // Fermeture douce par clic sur le backdrop
      document.querySelectorAll('dialog').forEach((dialog) => {
        dialog.addEventListener('click', (e) => {
          if (e.target !== dialog) return;
          const rect = dialog.getBoundingClientRect();
          const clickedOutside = (
            e.clientX < rect.left ||
            e.clientX > rect.right ||
            e.clientY < rect.top ||
            e.clientY > rect.bottom
          );
          if (clickedOutside) {
            this.close(dialog);
          }
        });

        // Interception de la touche Échap pour jouer l'animation de sortie
        dialog.addEventListener('cancel', (e) => {
          e.preventDefault();
          this.close(dialog);
        });
      });

      // Raccourcis clavier (1 à 8 pour ouvrir les planètes, Échap pour fermer)
      window.addEventListener('keydown', (e) => {
        const isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName);
        if (isInput) return;

        if (e.key >= '1' && e.key <= '8' && !e.ctrlKey && !e.altKey && !e.metaKey) {
          const planetIdx = parseInt(e.key, 10) - 1;
          const pDef = PLANET_DEFS[planetIdx];
          if (pDef) {
            e.preventDefault();
            this.open(pDef.modalId, document.querySelector(`[data-planet="${planetIdx}"]`));
          }
        }
      });
    },

    open(modalId, triggerEl = null) {
      const modal = document.getElementById(modalId);
      if (!modal) return;
      if (modal.open && !modal.hasAttribute('data-closing')) return;

      // Détermine l'origine de l'animation d'ouverture
      let ox = '50%';
      let oy = '50%';
      let bx = '50%';
      let by = '50%';

      if (triggerEl && triggerEl.getBoundingClientRect) {
        const rect = triggerEl.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        ox = `${Math.round(cx)}px`;
        oy = `${Math.round(cy)}px`;
        bx = `${Math.round((cx / window.innerWidth) * 100)}%`;
        by = `${Math.round((cy / window.innerHeight) * 100)}%`;
      }

      modal.style.setProperty('--ox', ox);
      modal.style.setProperty('--oy', oy);
      modal.style.setProperty('--bx', bx);
      modal.style.setProperty('--by', by);
      modal.removeAttribute('data-closing');

      try {
        if (!modal.open) modal.showModal();
      } catch (err) {
        // En cas de modal déjà ouverte
      }

      this.activeModal = modal;
      SolarEngine.pauseForModal(true);

      // Animation spéciale pour les cartes intérieures
      const cards = modal.querySelectorAll('.card, .metric-card, .service-card');
      cards.forEach((c, idx) => {
        c.style.animationDelay = `${0.04 * idx}s`;
      });
    },

    close(modal = null) {
      const target = modal || this.activeModal;
      if (!target || !target.open || target.hasAttribute('data-closing')) return;

      target.setAttribute('data-closing', 'true');
      this.isClosing = true;

      // Attendre la fin de la transition douce (200ms) avant de fermer
      setTimeout(() => {
        target.removeAttribute('data-closing');
        try {
          target.close();
        } catch (e) { }
        this.isClosing = false;
        if (this.activeModal === target) this.activeModal = null;

        // Si aucune autre modal n'est ouverte, relancer le cosmos à 60 FPS
        const anyOpen = Array.from(document.querySelectorAll('dialog')).some((d) => d.open);
        if (!anyOpen) {
          SolarEngine.pauseForModal(false);
        }
      }, 210);
    }
  };

  /* ==========================================================================
     3. MOTEUR 3D CANVAS : SYSTÈME SOLAIRE CINÉMATIQUE HOLOGRAPHIQUE
     ========================================================================== */
  const SolarEngine = {
    canvas: null,
    ctx: null,
    width: 0,
    height: 0,
    dpr: 1,
    centerX: 0,
    centerY: 0,
    maxRadius: 0,
    isPaused: false,
    hoveredPlanet: null,
    hoveredTile: null,
    stars: [],
    shootingStars: [],
    planets: [],
    sunRays: [],
    sunPulse: 1.0,
    sunPulseDir: 1,
    voiceState: 'idle',
    shockwaves: [],
    lastShockwaveTime: 0,
    orbitTiltAngle: 0.32,   // Inclinaison de vue perspective (cosinus d'orbite)
    orbitRotation: -0.22,   // Rotation dans le plan
    timeScale: 0.0016,
    frameCount: 0,
    lastFrameTime: performance.now(),
    fps: 60,

    setVoiceState(nextState) {
      const normalized = ['idle', 'listening', 'speaking'].includes(nextState) ? nextState : 'idle';
      this.voiceState = normalized;
      if (typeof document !== 'undefined' && document.body) {
        document.body.dataset.voiceState = normalized;
      }
      if (normalized === 'speaking') {
        this.shockwaves.push({ radius: 1.0, alpha: 0.9, color: 'rgba(94, 217, 236,' });
      }
    },

    getVoiceState() {
      if (this.voiceState && this.voiceState !== 'idle') return this.voiceState;
      if (typeof document !== 'undefined') {
        const reactor = document.getElementById('jarvis-reactor');
        const rState = reactor?.dataset?.reactorState;
        if (rState === 'listening' || rState === 'speaking') return rState;
        const bState = document.body?.dataset?.voiceState;
        if (bState === 'listening' || bState === 'speaking') return bState;
      }
      return 'idle';
    },

    init() {
      this.canvas = document.getElementById('cosmos-canvas');
      if (!this.canvas) return;
      this.ctx = this.canvas.getContext('2d', { alpha: false });

      this.resize();
      window.addEventListener('resize', () => this.resize(), { passive: true });

      this.createStars();
      this.initPlanets();
      this.initSunRays();
      this.bindInteractions();

      requestAnimationFrame((t) => this.loop(t));
    },

    resize() {
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.width = window.innerWidth;
      this.height = window.innerHeight;
      this.canvas.width = Math.round(this.width * this.dpr);
      this.canvas.height = Math.round(this.height * this.dpr);
      this.canvas.style.width = `${this.width}px`;
      this.canvas.style.height = `${this.height}px`;
      this.ctx.scale(this.dpr, this.dpr);

      // Centrage du soleil sur le bouton MOCTAR central
      const coreBtn = document.querySelector('.core-btn');
      if (coreBtn) {
        const rect = coreBtn.getBoundingClientRect();
        this.centerX = rect.left + rect.width / 2;
        this.centerY = rect.top + rect.height / 2;
      } else {
        this.centerX = this.width / 2;
        this.centerY = this.height / 2;
      }

      // Rayon maximal disponible entre le centre et les colonnes
      const leftCol = document.querySelector('.cosmos-left');
      const leftEdge = leftCol ? leftCol.getBoundingClientRect().right : 0;
      const availableW = Math.max(140, this.centerX - leftEdge);
      const availableH = this.centerY - 60;
      this.maxRadius = Math.max(availableW, availableH) * 1.45;

      this.updatePlanetRadii();
    },

    createStars() {
      this.stars = [];
      const count = Math.min(Math.floor((this.width * this.height) / 7500), 220);
      for (let i = 0; i < count; i++) {
        this.stars.push({
          x: Math.random() * this.width,
          y: Math.random() * this.height,
          size: Math.random() < 0.85 ? Math.random() * 1.2 + 0.3 : Math.random() * 2.2 + 1.2,
          brightness: Math.random() * 0.7 + 0.3,
          twinkleSpeed: Math.random() * 0.03 + 0.008,
          phase: Math.random() * Math.PI * 2,
          color: ['#e1efff', '#cce6ff', '#ffdcb3', '#ffffff', '#a8d8ff'][Math.floor(Math.random() * 5)]
        });
      }
    },

    initSunRays() {
      this.sunRays = [];
      for (let i = 0; i < 28; i++) {
        this.sunRays.push({
          angle: (i / 28) * Math.PI * 2,
          length: Math.random() * 35 + 20,
          speed: (Math.random() - 0.5) * 0.005,
          opacity: Math.random() * 0.4 + 0.2
        });
      }
    },

    initPlanets() {
      this.planets = PLANET_DEFS.map((def, idx) => {
        return {
          ...def,
          angle: (idx * 0.78) + Math.PI * 0.15,
          x: 0,
          y: 0,
          scale: 1,
          depth: 0, // -1 (arrière) à +1 (avant)
          screenRadius: 0,
          trail: []
        };
      });
      this.updatePlanetRadii();
    },

    updatePlanetRadii() {
      if (!this.planets.length) return;
      const minR = 60;
      const maxR = Math.max(this.maxRadius, 260);
      this.planets.forEach((p) => {
        p.actualRadius = minR + p.baseRadius * (maxR - minR);
      });
    },

    bindInteractions() {
      // Détection de survol sur le canvas pour les planètes
      this.canvas.addEventListener('mousemove', (e) => {
        const mouseX = e.clientX;
        const mouseY = e.clientY;
        let found = null;

        for (const p of this.planets) {
          const dx = mouseX - p.x;
          const dy = mouseY - p.y;
          const hitDist = p.size * p.scale + 14;
          if (dx * dx + dy * dy <= hitDist * hitDist) {
            found = p;
            break;
          }
        }

        if (this.hoveredPlanet !== found) {
          this.hoveredPlanet = found;
          this.canvas.style.cursor = found ? 'pointer' : 'default';
          this.syncTileHighlight(found ? found.id : null);
        }
      });

      // Clic sur une planète 3D -> ouvre la modale associée
      this.canvas.addEventListener('click', (e) => {
        if (this.hoveredPlanet) {
          const trigger = document.querySelector(`[data-planet="${this.hoveredPlanet.id}"]`);
          ModalManager.open(this.hoveredPlanet.modalId, trigger);
        } else {
          // Si on clique près du soleil central -> ouvrir MOCTAR
          const dx = e.clientX - this.centerX;
          const dy = e.clientY - this.centerY;
          if (dx * dx + dy * dy <= 70 * 70) {
            const coreBtn = document.querySelector('.core-btn');
            ModalManager.open('jarvis-modal', coreBtn);
          }
        }
      });

      // Survol des tuiles de l'accueil -> met en valeur la planète correspondante
      document.querySelectorAll('.orb-tile[data-planet]').forEach((tile) => {
        const pId = parseInt(tile.dataset.planet, 10);
        tile.addEventListener('mouseenter', () => {
          this.hoveredTile = pId;
          tile.classList.add('is-linked');
        });
        tile.addEventListener('mouseleave', () => {
          if (this.hoveredTile === pId) this.hoveredTile = null;
          tile.classList.remove('is-linked');
        });
      });
    },

    syncTileHighlight(planetId) {
      document.querySelectorAll('.orb-tile[data-planet]').forEach((t) => {
        const id = parseInt(t.dataset.planet, 10);
        if (id === planetId) {
          t.classList.add('is-linked');
        } else if (this.hoveredTile !== id) {
          t.classList.remove('is-linked');
        }
      });
    },

    pauseForModal(paused) {
      this.isPaused = paused;
    },

    /* ========================================================================
       CYCLE DE RENDU 60 FPS
       ======================================================================== */
    loop(time) {
      requestAnimationFrame((t) => this.loop(t));

      // Calcul FPS et gestion du mode allégé si GPU en difficulté
      const delta = time - this.lastFrameTime;
      this.lastFrameTime = time;
      this.frameCount++;
      if (this.frameCount % 45 === 0 && delta > 0) {
        this.fps = 1000 / delta;
        if (this.fps < 35 && !document.body.classList.contains('cosmos-lite')) {
          document.body.classList.add('cosmos-lite');
        }
      }

      // Si une modale est ouverte, économiser 100% du GPU en ne redessinant pas
      if (this.isPaused) {
        return;
      }

      this.ctx.fillStyle = '#02050b';
      this.ctx.fillRect(0, 0, this.width, this.height);

      this.drawNebulaGlow();
      this.drawStars(time);
      this.drawShootingStars();
      this.drawOrbits();
      this.updatePlanets(time);
      this.drawBeacons();
      this.drawSun(time);
      this.drawPlanets();
      this.drawHoloHUD();
    },

    drawNebulaGlow() {
      const grad = this.ctx.createRadialGradient(
        this.centerX, this.centerY, 10,
        this.centerX, this.centerY, Math.max(this.width, this.height) * 0.7
      );
      grad.addColorStop(0, 'rgba(255, 175, 60, 0.12)');
      grad.addColorStop(0.2, 'rgba(94, 217, 236, 0.05)');
      grad.addColorStop(0.5, 'rgba(15, 30, 60, 0.25)');
      grad.addColorStop(1, 'rgba(2, 5, 11, 0.95)');
      this.ctx.fillStyle = grad;
      this.ctx.fillRect(0, 0, this.width, this.height);
    },

    drawStars(time) {
      for (const s of this.stars) {
        const twinkle = Math.sin(time * s.twinkleSpeed + s.phase) * 0.35 + 0.65;
        this.ctx.fillStyle = s.color;
        this.ctx.globalAlpha = s.brightness * twinkle;
        this.ctx.beginPath();
        this.ctx.arc(s.x, s.y, s.size, 0, Math.PI * 2);
        this.ctx.fill();
      }
      this.ctx.globalAlpha = 1.0;
    },

    drawShootingStars() {
      // Apparition aléatoire d'étoiles filantes
      if (Math.random() < 0.012 && this.shootingStars.length < 3) {
        this.shootingStars.push({
          x: Math.random() * this.width,
          y: Math.random() * (this.height * 0.5),
          len: Math.random() * 80 + 40,
          speed: Math.random() * 12 + 10,
          angle: (Math.random() * 0.3 + 0.6) * Math.PI,
          alpha: 1.0
        });
      }

      for (let i = this.shootingStars.length - 1; i >= 0; i--) {
        const st = this.shootingStars[i];
        st.x += Math.cos(st.angle) * st.speed;
        st.y += Math.sin(st.angle) * st.speed;
        st.alpha -= 0.022;

        if (st.alpha <= 0 || st.x < 0 || st.x > this.width || st.y > this.height) {
          this.shootingStars.splice(i, 1);
          continue;
        }

        this.ctx.strokeStyle = `rgba(140, 220, 255, ${st.alpha})`;
        this.ctx.lineWidth = 1.6;
        this.ctx.beginPath();
        this.ctx.moveTo(st.x, st.y);
        this.ctx.lineTo(st.x - Math.cos(st.angle) * st.len, st.y - Math.sin(st.angle) * st.len);
        this.ctx.stroke();
      }
    },

    drawOrbits() {
      this.ctx.save();
      this.ctx.translate(this.centerX, this.centerY);
      this.ctx.rotate(this.orbitRotation);

      for (const p of this.planets) {
        const isTarget = (this.hoveredPlanet === p || this.hoveredTile === p.id);
        const radiusX = p.actualRadius;
        const radiusY = p.actualRadius * this.orbitTiltAngle;

        this.ctx.beginPath();
        this.ctx.ellipse(0, 0, radiusX, radiusY, 0, 0, Math.PI * 2);

        if (isTarget) {
          this.ctx.strokeStyle = p.color;
          this.ctx.lineWidth = 1.8;
          this.ctx.shadowColor = p.color;
          this.ctx.shadowBlur = 12;
          this.ctx.setLineDash([6, 6]);
        } else {
          this.ctx.strokeStyle = 'rgba(140, 200, 255, 0.08)';
          this.ctx.lineWidth = 1.0;
          this.ctx.shadowBlur = 0;
          this.ctx.setLineDash([3, 7]);
        }
        this.ctx.stroke();
      }

      this.ctx.restore();
    },

    updatePlanets(time) {
      for (const p of this.planets) {
        p.angle += p.speed * this.timeScale;

        // Position 3D avec perspective elliptique inclinée
        const a = p.actualRadius;
        const b = p.actualRadius * this.orbitTiltAngle;

        // Rotation d'orbite
        const cosR = Math.cos(this.orbitRotation);
        const sinR = Math.sin(this.orbitRotation);

        const localX = Math.cos(p.angle) * a;
        const localY = Math.sin(p.angle) * b;

        p.x = this.centerX + (localX * cosR - localY * sinR);
        p.y = this.centerY + (localX * sinR + localY * cosR);

        // Profondeur (sinus d'angle : de -1 en arrière à +1 en avant)
        p.depth = Math.sin(p.angle);
        // Échelle perspective : les planètes en premier plan sont 35% plus grandes
        p.scale = 0.8 + 0.45 * ((p.depth + 1) / 2);

        // Enregistrement de la traînée de poussière orbitale
        if (this.frameCount % 2 === 0) {
          p.trail.unshift({ x: p.x, y: p.y, alpha: 0.5 });
          if (p.trail.length > 14) p.trail.pop();
        }
      }
    },

    drawSun(time) {
      this.ctx.save();
      const cx = this.centerX;
      const cy = this.centerY;
      const now = typeof time === 'number' ? time : performance.now();
      const state = this.getVoiceState();

      // Modulation dynamique selon l'état vocal ('idle' | 'listening' | 'speaking')
      if (state === 'speaking') {
        // Ondulation cinétique rapide au rythme de la parole (style sphère Jarvis)
        this.sunPulse = 1.04 + Math.sin(now * 0.018) * 0.13 + Math.abs(Math.cos(now * 0.031)) * 0.08;
        if (now - this.lastShockwaveTime > 220) {
          this.lastShockwaveTime = now;
          this.shockwaves.push({
            radius: 1.05,
            alpha: 0.85,
            color: this.shockwaves.length % 2 === 0 ? 'rgba(94, 217, 236,' : 'rgba(255, 209, 102,'
          });
        }
      } else if (state === 'listening') {
        // Pulsation dynamique cyan/or d'écoute active
        this.sunPulse = 1.02 + Math.sin(now * 0.008) * 0.11 + Math.cos(now * 0.016) * 0.04;
      } else {
        // Respiration lente normale ('idle')
        this.sunPulse += 0.003 * this.sunPulseDir;
        if (this.sunPulse > 1.08) this.sunPulseDir = -1;
        if (this.sunPulse < 0.95) this.sunPulseDir = 1;
      }

      const baseR = Math.max(34, Math.min(this.width * 0.05, 52)) * this.sunPulse;

      // 1. Rayons coronaux tournants
      this.ctx.save();
      const raySpeedMult = state === 'speaking' ? 3.8 : state === 'listening' ? 2.4 : 1.0;
      const rayBoost = state === 'speaking' ? 1.45 : state === 'listening' ? 1.25 : 1.0;
      for (let i = 0; i < this.sunRays.length; i++) {
        const ray = this.sunRays[i];
        ray.angle += ray.speed * raySpeedMult;
        const rLen = baseR + ray.length * rayBoost;
        const rx = cx + Math.cos(ray.angle) * rLen;
        const ry = cy + Math.sin(ray.angle) * rLen;

        if (state === 'listening' && i % 2 === 0) {
          this.ctx.strokeStyle = `rgba(94, 217, 236, ${Math.min(0.9, ray.opacity * 0.65)})`;
        } else if (state === 'speaking' && i % 3 === 0) {
          this.ctx.strokeStyle = `rgba(94, 217, 236, ${Math.min(0.95, ray.opacity * 0.75)})`;
        } else {
          this.ctx.strokeStyle = `rgba(255, 200, 100, ${ray.opacity * (state === 'idle' ? 0.35 : 0.6)})`;
        }
        this.ctx.lineWidth = state === 'speaking' ? 2.6 : state === 'listening' ? 2.2 : 2;
        this.ctx.beginPath();
        this.ctx.moveTo(cx + Math.cos(ray.angle) * baseR, cy + Math.sin(ray.angle) * baseR);
        this.ctx.lineTo(rx, ry);
        this.ctx.stroke();
      }
      this.ctx.restore();

      // 2. Halo extérieur réactif
      const haloRadius = baseR * (state === 'speaking' ? 4.2 : state === 'listening' ? 3.9 : 3.4);
      const haloGrad = this.ctx.createRadialGradient(cx, cy, baseR * 0.4, cx, cy, haloRadius);
      if (state === 'listening') {
        haloGrad.addColorStop(0, 'rgba(94, 217, 236, 0.58)');
        haloGrad.addColorStop(0.32, 'rgba(255, 205, 85, 0.36)');
        haloGrad.addColorStop(0.68, 'rgba(94, 217, 236, 0.14)');
        haloGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
      } else if (state === 'speaking') {
        haloGrad.addColorStop(0, 'rgba(255, 225, 130, 0.65)');
        haloGrad.addColorStop(0.35, 'rgba(94, 217, 236, 0.38)');
        haloGrad.addColorStop(0.72, 'rgba(255, 120, 30, 0.14)');
        haloGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
      } else {
        haloGrad.addColorStop(0, 'rgba(255, 190, 80, 0.45)');
        haloGrad.addColorStop(0.3, 'rgba(255, 120, 30, 0.22)');
        haloGrad.addColorStop(0.7, 'rgba(255, 70, 10, 0.06)');
        haloGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
      }
      this.ctx.fillStyle = haloGrad;
      this.ctx.beginPath();
      this.ctx.arc(cx, cy, haloRadius, 0, Math.PI * 2);
      this.ctx.fill();

      // 2b. Ondes de choc radiales (shockwaves) pendant la parole ('speaking')
      if (this.shockwaves.length > 0) {
        for (let i = this.shockwaves.length - 1; i >= 0; i--) {
          const wave = this.shockwaves[i];
          wave.radius += 0.065;
          wave.alpha -= 0.022;
          if (wave.alpha <= 0.01 || wave.radius > 4.4) {
            this.shockwaves.splice(i, 1);
            continue;
          }
          this.ctx.save();
          this.ctx.strokeStyle = `${wave.color} ${wave.alpha.toFixed(3)})`;
          this.ctx.lineWidth = Math.max(1.2, 3.2 * wave.alpha);
          this.ctx.beginPath();
          this.ctx.arc(cx, cy, baseR * wave.radius, 0, Math.PI * 2);
          this.ctx.stroke();
          this.ctx.restore();
        }
      }

      // 3. Sphère solaire incandescente avec texture d'énergie
      const sunGrad = this.ctx.createRadialGradient(
        cx - baseR * 0.25, cy - baseR * 0.25, baseR * 0.1,
        cx, cy, baseR
      );
      if (state === 'listening') {
        sunGrad.addColorStop(0, '#ffffff');
        sunGrad.addColorStop(0.25, '#bff6ff');
        sunGrad.addColorStop(0.6, '#ffd166');
        sunGrad.addColorStop(0.88, '#1ca3de');
        sunGrad.addColorStop(1, '#0b5278');
        this.ctx.shadowColor = '#5ed9ec';
        this.ctx.shadowBlur = 42;
      } else if (state === 'speaking') {
        sunGrad.addColorStop(0, '#ffffff');
        sunGrad.addColorStop(0.22, '#fff6bf');
        sunGrad.addColorStop(0.52, '#5ed9ec');
        sunGrad.addColorStop(0.82, '#ff9f1c');
        sunGrad.addColorStop(1, '#c83e00');
        this.ctx.shadowColor = '#ffd166';
        this.ctx.shadowBlur = 48;
      } else {
        sunGrad.addColorStop(0, '#ffffff');
        sunGrad.addColorStop(0.2, '#fff1a8');
        sunGrad.addColorStop(0.55, '#ffad33');
        sunGrad.addColorStop(0.85, '#e65c00');
        sunGrad.addColorStop(1, '#b33600');
        this.ctx.shadowColor = '#ffaa33';
        this.ctx.shadowBlur = 32;
      }

      this.ctx.fillStyle = sunGrad;
      this.ctx.beginPath();
      this.ctx.arc(cx, cy, baseR, 0, Math.PI * 2);
      this.ctx.fill();
      this.ctx.shadowBlur = 0;

      // 4. Anneaux holographiques autour du soleil MOCTAR
      this.ctx.strokeStyle = state === 'listening'
        ? 'rgba(94, 217, 236, 0.85)'
        : state === 'speaking'
          ? 'rgba(255, 209, 102, 0.9)'
          : 'rgba(94, 217, 236, 0.4)';
      this.ctx.lineWidth = state === 'idle' ? 1.4 : 2.0;
      this.ctx.setLineDash([4, 8]);
      this.ctx.beginPath();
      this.ctx.arc(cx, cy, baseR * 1.35, 0, Math.PI * 2);
      this.ctx.stroke();

      if (state === 'listening' || state === 'speaking') {
        this.ctx.strokeStyle = state === 'speaking'
          ? 'rgba(94, 217, 236, 0.78)'
          : 'rgba(255, 209, 102, 0.72)';
        this.ctx.lineWidth = 1.5;
        this.ctx.setLineDash([8, 6]);
        this.ctx.beginPath();
        this.ctx.arc(cx, cy, baseR * (1.62 + Math.sin(now * 0.012) * 0.08), 0, Math.PI * 2);
        this.ctx.stroke();
      }
      this.ctx.setLineDash([]);

      this.ctx.restore();
    },

    drawPlanets() {
      // Tri par profondeur pour occlusion réaliste (planètes en arrière d'abord)
      const sorted = [...this.planets].sort((a, b) => a.depth - b.depth);

      for (const p of sorted) {
        this.drawSinglePlanet(p);
      }
    },

    drawSinglePlanet(p) {
      const ctx = this.ctx;
      const r = p.size * p.scale;
      const isTarget = (this.hoveredPlanet === p || this.hoveredTile === p.id);

      // 1. Traînée de poussière
      if (p.trail.length > 1) {
        for (let i = 0; i < p.trail.length - 1; i++) {
          const pt = p.trail[i];
          const next = p.trail[i + 1];
          const fade = (1 - (i / p.trail.length)) * 0.3;
          ctx.strokeStyle = p.color;
          ctx.globalAlpha = fade;
          ctx.lineWidth = r * 0.45 * (1 - (i / p.trail.length));
          ctx.beginPath();
          ctx.moveTo(pt.x, pt.y);
          ctx.lineTo(next.x, next.y);
          ctx.stroke();
        }
        ctx.globalAlpha = 1.0;
      }

      ctx.save();
      ctx.translate(p.x, p.y);

      // 2. Halo de surbrillance si ciblée
      if (isTarget) {
        ctx.shadowColor = p.color;
        ctx.shadowBlur = 24;
        ctx.strokeStyle = p.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(0, 0, r + 7, 0, Math.PI * 2);
        ctx.stroke();
      }

      // 3. Anneaux arrière de Saturne (tracés derrière la sphère)
      if (p.hasRings) {
        ctx.save();
        ctx.rotate(0.45);
        ctx.beginPath();
        ctx.ellipse(0, 0, r * 2.2, r * 0.65, 0, Math.PI, Math.PI * 2);
        ctx.strokeStyle = 'rgba(230, 208, 143, 0.45)';
        ctx.lineWidth = r * 0.55;
        ctx.stroke();
        ctx.restore();
      }

      // 4. Sphère planétaire 3D éclairée par le soleil central
      // Direction de la lumière venant du centre (soleil)
      const lightAngle = Math.atan2(this.centerY - p.y, this.centerX - p.x);
      const lightOffX = Math.cos(lightAngle) * (r * 0.35);
      const lightOffY = Math.sin(lightAngle) * (r * 0.35);

      const pGrad = ctx.createRadialGradient(
        lightOffX, lightOffY, r * 0.1,
        0, 0, r
      );
      pGrad.addColorStop(0, '#ffffff');
      pGrad.addColorStop(0.35, p.color);
      pGrad.addColorStop(0.85, this.darkenColor(p.color, 0.65));
      pGrad.addColorStop(1, '#05070d');

      ctx.fillStyle = pGrad;
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.fill();

      // Bandes atmosphériques pour Jupiter
      if (p.isBanded) {
        ctx.save();
        ctx.clip();
        ctx.strokeStyle = 'rgba(120, 70, 30, 0.35)';
        ctx.lineWidth = r * 0.22;
        ctx.beginPath();
        ctx.moveTo(-r, -r * 0.2);
        ctx.lineTo(r, -r * 0.2);
        ctx.moveTo(-r, r * 0.25);
        ctx.lineTo(r, r * 0.25);
        ctx.stroke();
        ctx.restore();
      }

      // 5. Anneaux avant de Saturne (tracés par-dessus la sphère)
      if (p.hasRings) {
        ctx.save();
        ctx.rotate(0.45);
        ctx.beginPath();
        ctx.ellipse(0, 0, r * 2.2, r * 0.65, 0, 0, Math.PI);
        ctx.strokeStyle = 'rgba(230, 208, 143, 0.75)';
        ctx.lineWidth = r * 0.55;
        ctx.stroke();
        ctx.restore();
      }

      // 6. Atmosphère terrestre bleutée
      if (p.hasAtmosphere) {
        ctx.strokeStyle = 'rgba(100, 210, 255, 0.45)';
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        ctx.arc(0, 0, r + 1.5, 0, Math.PI * 2);
        ctx.stroke();
      }

      ctx.restore();
    },

    drawBeacons() {
      // Rayon laser/hologramme entre la planète ciblée et sa tuile sur l'interface
      const targetId = (this.hoveredPlanet ? this.hoveredPlanet.id : this.hoveredTile);
      if (targetId === null || targetId === undefined) return;

      const p = this.planets[targetId];
      const tile = document.querySelector(`.orb-tile[data-planet="${targetId}"]`);
      if (!p || !tile) return;

      const rect = tile.getBoundingClientRect();
      // Point d'accroche sur le bord de la tuile le plus proche
      const targetX = rect.left < this.centerX ? rect.right : rect.left;
      const targetY = rect.top + rect.height / 2;

      this.ctx.save();
      const grad = this.ctx.createLinearGradient(p.x, p.y, targetX, targetY);
      grad.addColorStop(0, p.color);
      grad.addColorStop(0.5, 'rgba(94, 217, 236, 0.35)');
      grad.addColorStop(1, 'rgba(94, 217, 236, 0.05)');

      this.ctx.strokeStyle = grad;
      this.ctx.lineWidth = 1.4;
      this.ctx.setLineDash([4, 6]);
      this.ctx.beginPath();
      this.ctx.moveTo(p.x, p.y);
      this.ctx.lineTo(targetX, targetY);
      this.ctx.stroke();

      // Anneau pulsant au point d'impact sur la tuile
      this.ctx.fillStyle = p.color;
      this.ctx.beginPath();
      this.ctx.arc(targetX, targetY, 3, 0, Math.PI * 2);
      this.ctx.fill();

      this.ctx.restore();
    },

    drawHoloHUD() {
      if (!this.hoveredPlanet) return;
      const p = this.hoveredPlanet;
      const ctx = this.ctx;
      const r = p.size * p.scale;

      ctx.save();
      const hudX = p.x + r + 14;
      const hudY = p.y - 10;

      // Boîte HUD holographique
      ctx.fillStyle = 'rgba(10, 18, 32, 0.82)';
      ctx.strokeStyle = p.color;
      ctx.lineWidth = 1;

      const text = `${p.name} · ${p.desc}`;
      ctx.font = '600 11px Inter, sans-serif';
      const textW = ctx.measureText(text).width;

      ctx.beginPath();
      ctx.roundRect(hudX, hudY - 14, textW + 18, 26, 6);
      ctx.fill();
      ctx.stroke();

      // Texte
      ctx.fillStyle = '#ffffff';
      ctx.fillText(text, hudX + 9, hudY + 3);

      // Trait d'amorce vers la planète
      ctx.strokeStyle = p.color;
      ctx.beginPath();
      ctx.moveTo(p.x + r + 2, p.y);
      ctx.lineTo(hudX, hudY);
      ctx.stroke();

      ctx.restore();
    },

    darkenColor(hex, factor = 0.5) {
      if (!hex.startsWith('#')) return '#000000';
      const c = parseInt(hex.slice(1), 16);
      const r = Math.floor(((c >> 16) & 255) * factor);
      const g = Math.floor(((c >> 8) & 255) * factor);
      const b = Math.floor((c & 255) * factor);
      return `rgb(${r}, ${g}, ${b})`;
    }
  };

  /* ==========================================================================
     4. SYNCHRONISATION EN TEMPS RÉEL DU DASHBOARD D'ACCUEIL (TUILES & SPARKLINE)
     ========================================================================== */
  const HomeSync = {
    sparkCanvas: null,
    sparkCtx: null,
    history: [],

    init() {
      this.sparkCanvas = document.getElementById('home-spark');
      if (this.sparkCanvas) {
        this.sparkCtx = this.sparkCanvas.getContext('2d');
        this.resizeSpark();
        window.addEventListener('resize', () => this.resizeSpark(), { passive: true });
      }

      // Écoute de l'événement snapshot diffusé par l'index.html
      document.addEventListener('noc:snapshot', (e) => {
        this.update(e.detail);
      });

      // Polling de mise à jour des automatisations (Planificateur)
      this.fetchSchedulerSummary();
      setInterval(() => this.fetchSchedulerSummary(), 20000);
    },

    resizeSpark() {
      if (!this.sparkCanvas) return;
      const rect = this.sparkCanvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      this.sparkCanvas.width = Math.round(rect.width * dpr);
      this.sparkCanvas.height = Math.round(rect.height * dpr);
      this.sparkCtx?.scale(dpr, dpr);
      this.drawSpark();
    },

    update(snapshot) {
      if (!snapshot) return;

      // 1. Ressources matérielles
      const hw = snapshot.hardware || {};
      const cpuPct = hw.cpu?.percent;
      const memPct = hw.memory?.percent;
      const rootPct = hw.disks?.root?.percent;
      const tempC = hw.temperature_c;

      if (typeof cpuPct === 'number') {
        const cpuEl = document.getElementById('home-cpu');
        if (cpuEl) cpuEl.textContent = cpuPct.toFixed(1);
        this.history.push(cpuPct);
        if (this.history.length > 60) this.history.shift();
        this.drawSpark();

        const sparkVal = document.getElementById('home-spark-value');
        if (sparkVal) sparkVal.textContent = cpuPct.toFixed(1);
      }

      if (typeof memPct === 'number') {
        const ramFill = document.getElementById('home-ram-fill');
        const ramVal = document.getElementById('home-ram');
        if (ramFill) ramFill.style.width = `${Math.min(100, Math.max(0, memPct))}%`;
        if (ramVal) ramVal.textContent = `${memPct.toFixed(0)}%`;
      }

      if (typeof rootPct === 'number') {
        const diskFill = document.getElementById('home-disk-fill');
        const diskVal = document.getElementById('home-disk');
        if (diskFill) diskFill.style.width = `${Math.min(100, Math.max(0, rootPct))}%`;
        if (diskVal) diskVal.textContent = `${rootPct.toFixed(0)}%`;
      }

      if (typeof tempC === 'number') {
        const tempFill = document.getElementById('home-temp-fill');
        const tempVal = document.getElementById('home-temp');
        if (tempFill) tempFill.style.width = `${Math.min(100, Math.max(0, (tempC / 95) * 100))}%`;
        if (tempVal) tempVal.textContent = `${tempC.toFixed(0)}°`;
      }

      // 2. Services & Applications
      const services = Array.isArray(snapshot.services) ? snapshot.services : [];
      let upCount = 0;
      services.forEach((s) => {
        if (s && s.healthy) upCount++;
        const dot = document.querySelector(`.orb-dot[data-service="${s?.name}"]`);
        if (dot) {
          dot.dataset.tone = s?.healthy ? 'success' : 'error';
        }
      });
      const homeSvc = document.getElementById('home-svc');
      if (homeSvc) homeSvc.textContent = `${upCount}`;

      // 3. Infrastructure & Réseau
      const infra = snapshot.infrastructure || {};
      const cf = infra.cloudflared;
      const wa = infra.whatsapp;

      const cfLed = document.getElementById('home-cf-led');
      const cfVal = document.getElementById('home-cf');
      if (cfLed && cfVal) {
        const active = cf && cf.healthy;
        cfLed.dataset.tone = active ? 'success' : 'error';
        cfVal.textContent = active ? 'Actif' : 'Arrêté';
      }

      const waLed = document.getElementById('home-wa-led');
      const waVal = document.getElementById('home-wa');
      if (waLed && waVal) {
        const connected = wa && wa.connected;
        waLed.dataset.tone = connected ? 'success' : 'error';
        waVal.textContent = connected ? 'Connecté' : 'Attente';
      }

      // 4. VPS Distants & Cyberdéfense
      const vpsList = Array.isArray(snapshot.vps) ? snapshot.vps : [];
      const homeVps = document.getElementById('home-vps');
      const homeVpsSub = document.getElementById('home-vps-sub');
      if (homeVps) homeVps.textContent = `${vpsList.length}`;
      if (homeVpsSub) {
        const activeCount = vpsList.filter((v) => v && !v.stale && !v.restored && !v.archived).length;
        const archivedCount = vpsList.filter((v) => v && v.archived).length;
        homeVpsSub.textContent = vpsList.length === 0 ? 'Aucun serveur distant' : `${activeCount} actif(s) en ligne` +
          (archivedCount ? ` · ${archivedCount} archivé(s)` : '');
      }

      // Cyberdéfense (Sentinel)
      const sentinel = snapshot.sentinel || {};
      const cyber = snapshot.cyber || {};
      const blocked = sentinel.blocked_today ?? 0;
      const homeBlocked = document.getElementById('home-blocked');
      const homeSentinel = document.getElementById('home-sentinel');
      if (homeBlocked) homeBlocked.textContent = `${blocked}`;
      if (homeSentinel) {
        const sActive = sentinel.agent_status === 'active';
        const localDefenseActive = cyber.mode === 'application' || cyber.firewall_active === true ||
          cyber.rules_loaded === true || cyber.rule_count > 0;
        homeSentinel.textContent = sActive ? 'Sentinel actif' :
          localDefenseActive ? 'Surveillance locale active' : 'En attente de protection';
      }

      // 5. Journal d'événements (2 derniers logs sur l'accueil)
      const events = Array.isArray(snapshot.events) ? snapshot.events : [];
      if (events.length > 0) {
        const container = document.getElementById('home-events');
        if (container) {
          const recent = events.slice(-2).reverse();
          container.innerHTML = recent.map((ev) => {
            const timeStr = ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
            return `<span class="orb-event" data-level="${ev.level || 'info'}"><time>${timeStr}</time>${this.escapeHtml(ev.message || '')}</span>`;
          }).join('');
        }
      }
    },

    drawSpark() {
      if (!this.sparkCtx || !this.sparkCanvas || this.history.length < 2) return;
      const ctx = this.sparkCtx;
      const w = this.sparkCanvas.getBoundingClientRect().width;
      const h = this.sparkCanvas.getBoundingClientRect().height;

      ctx.clearRect(0, 0, w, h);

      const max = 100;
      const step = w / (this.history.length - 1);

      // Trajet de la courbe
      ctx.beginPath();
      this.history.forEach((val, idx) => {
        const x = idx * step;
        const y = h - (val / max) * (h - 6) - 3;
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });

      ctx.strokeStyle = '#5ed9ec';
      ctx.lineWidth = 1.8;
      ctx.shadowColor = '#5ed9ec';
      ctx.shadowBlur = 8;
      ctx.stroke();
      ctx.shadowBlur = 0;

      // Remplissage dégradé sous la courbe
      ctx.lineTo(w, h);
      ctx.lineTo(0, h);
      ctx.closePath();
      const fillGrad = ctx.createLinearGradient(0, 0, 0, h);
      fillGrad.addColorStop(0, 'rgba(94, 217, 236, 0.28)');
      fillGrad.addColorStop(1, 'rgba(94, 217, 236, 0)');
      ctx.fillStyle = fillGrad;
      ctx.fill();
    },

    async fetchSchedulerSummary() {
      try {
        const res = await fetch('/api/scheduler/tasks');
        if (!res.ok) return;
        const data = await res.json();
        const tasks = data.tasks || [];
        const homeSched = document.getElementById('home-sched');
        const homeSchedNext = document.getElementById('home-sched-next');
        if (homeSched) homeSched.textContent = `${tasks.length}`;
        if (homeSchedNext) {
          const active = tasks.filter((t) => t.status === 'active');
          if (active.length > 0) {
            homeSchedNext.textContent = `Prochaine à ${active[0].send_time || '09:00'}`;
          } else {
            homeSchedNext.textContent = tasks.length === 0 ? 'Aucune tâche' : 'En pause';
          }
        }
      } catch (e) {
        // En cas de non disponibilité temporaire
      }
    },

    escapeHtml(str) {
      return String(str).replace(/[&<>"']/g, (m) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[m]));
    }
  };

  /* ==========================================================================
     5. QUANTUM NEXUS 3D : CŒUR QUANTIQUE & MATRICE CYBER-ESPACE (MOCTAR IA)
     ========================================================================== */
  const QuantumNexus = {
    isRunning: false,
    animFrame: null,
    bgCanvas: null,
    bgCtx: null,
    coreCanvas: null,
    coreCtx: null,
    reactorEl: null,
    modalEl: null,
    bgWidth: 0,
    bgHeight: 0,
    dpr: 1,

    // 3D Parallax & Camera
    camParallaxX: 0,
    camParallaxY: 0,
    targetParallaxX: 0,
    targetParallaxY: 0,
    gridOffsetZ: 0,

    // Cyber-Space Neural Nodes
    nodes: [],
    dataStreams: [],

    // 3D Quantum Core
    coreRotX: 0.25,
    coreRotY: 0.35,
    coreRotZ: 0.1,
    dragVelX: 0,
    dragVelY: 0,
    isDragging: false,
    dragLastX: 0,
    dragLastY: 0,
    energyParticles: [],
    corePulse: 1.0,
    corePulseDir: 1,
    lastTime: performance.now(),

    init() {
      this.modalEl = document.getElementById('jarvis-modal');
      this.bgCanvas = document.getElementById('jarvis-bg-canvas');
      this.coreCanvas = document.getElementById('jarvis-core-canvas');
      this.reactorEl = document.getElementById('jarvis-reactor');

      if (this.bgCanvas) {
        this.bgCtx = this.bgCanvas.getContext('2d');
      }
      if (this.coreCanvas) {
        this.coreCtx = this.coreCanvas.getContext('2d');
      }

      this.initNodes();
      this.initStreams();
      this.initParticles();
      this.bindEvents();

      // MutationObserver pour surveiller l'ouverture de jarvis-modal
      if (this.modalEl) {
        const observer = new MutationObserver(() => {
          if (this.modalEl.open && !this.modalEl.hasAttribute('data-closing')) {
            this.start();
          } else {
            this.stop();
          }
        });
        observer.observe(this.modalEl, { attributes: true, attributeFilter: ['open'] });
        if (this.modalEl.open) this.start();
      }
    },

    bindEvents() {
      // Parallaxe souris sur la modal IA
      if (this.modalEl) {
        this.modalEl.addEventListener('mousemove', (e) => {
          const rect = this.modalEl.getBoundingClientRect();
          const mx = (e.clientX - rect.left) / rect.width - 0.5;
          const my = (e.clientY - rect.top) / rect.height - 0.5;
          this.targetParallaxX = mx * 35;
          this.targetParallaxY = my * 22;
        }, { passive: true });

        this.modalEl.addEventListener('mouseleave', () => {
          this.targetParallaxX = 0;
          this.targetParallaxY = 0;
        });
      }

      // Drag 3D interactif sur le Cœur Quantique
      if (this.coreCanvas) {
        this.coreCanvas.addEventListener('mousedown', (e) => {
          this.isDragging = true;
          this.dragLastX = e.clientX;
          this.dragLastY = e.clientY;
          this.dragVelX = 0;
          this.dragVelY = 0;
        });

        window.addEventListener('mousemove', (e) => {
          if (!this.isDragging) return;
          const dx = e.clientX - this.dragLastX;
          const dy = e.clientY - this.dragLastY;
          this.dragLastX = e.clientX;
          this.dragLastY = e.clientY;
          this.dragVelX = dx * 0.015;
          this.dragVelY = dy * 0.015;
          this.coreRotY += this.dragVelX;
          this.coreRotX -= this.dragVelY;
        }, { passive: true });

        window.addEventListener('mouseup', () => {
          this.isDragging = false;
        });

        // Touch support
        this.coreCanvas.addEventListener('touchstart', (e) => {
          if (e.touches.length === 1) {
            this.isDragging = true;
            this.dragLastX = e.touches[0].clientX;
            this.dragLastY = e.touches[0].clientY;
          }
        }, { passive: true });

        window.addEventListener('touchmove', (e) => {
          if (!this.isDragging || e.touches.length !== 1) return;
          const dx = e.touches[0].clientX - this.dragLastX;
          const dy = e.touches[0].clientY - this.dragLastY;
          this.dragLastX = e.touches[0].clientX;
          this.dragLastY = e.touches[0].clientY;
          this.coreRotY += dx * 0.015;
          this.coreRotX -= dy * 0.015;
        }, { passive: true });

        window.addEventListener('touchend', () => {
          this.isDragging = false;
        });
      }
    },

    initNodes() {
      this.nodes = [];
      for (let i = 0; i < 40; i++) {
        this.nodes.push({
          x: (Math.random() - 0.5) * 750,
          y: (Math.random() - 0.5) * 480,
          z: Math.random() * 550 + 80,
          vx: (Math.random() - 0.5) * 0.35,
          vy: (Math.random() - 0.5) * 0.35,
          vz: (Math.random() - 0.5) * 0.25,
          size: Math.random() * 2.2 + 1.2,
          pulse: Math.random() * Math.PI * 2,
          color: Math.random() < 0.6 ? '#5ed9ec' : '#c084fc'
        });
      }
    },

    initStreams() {
      this.dataStreams = [];
      for (let i = 0; i < 20; i++) {
        this.dataStreams.push({
          x: (Math.random() - 0.5) * 850,
          y: -280 - Math.random() * 220,
          z: Math.random() * 480 + 90,
          len: Math.random() * 70 + 40,
          speed: Math.random() * 3.8 + 2.2,
          alpha: Math.random() * 0.6 + 0.3
        });
      }
    },

    initParticles() {
      this.energyParticles = [];
      for (let i = 0; i < 50; i++) {
        this.energyParticles.push({
          theta: Math.random() * Math.PI * 2,
          phi: (Math.random() - 0.5) * Math.PI,
          rad: Math.random() * 26 + 32,
          speed: (Math.random() * 0.02 + 0.012) * (Math.random() < 0.5 ? 1 : -1),
          size: Math.random() * 2.2 + 0.8,
          alpha: Math.random() * 0.7 + 0.3,
          color: Math.random() < 0.45 ? '#5ed9ec' : Math.random() < 0.75 ? '#c084fc' : '#ffd166'
        });
      }
    },

    resize() {
      if (!this.modalEl || !this.bgCanvas) return;
      const rect = this.modalEl.getBoundingClientRect();
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.bgWidth = rect.width;
      this.bgHeight = rect.height;

      this.bgCanvas.width = Math.round(this.bgWidth * this.dpr);
      this.bgCanvas.height = Math.round(this.bgHeight * this.dpr);
      this.bgCtx.setTransform(1, 0, 0, 1, 0, 0);
      this.bgCtx.scale(this.dpr, this.dpr);

      if (this.coreCanvas) {
        this.coreCanvas.width = 300;
        this.coreCanvas.height = 300;
        this.coreCtx.setTransform(1, 0, 0, 1, 0, 0);
        this.coreCtx.scale(2, 2); // 150x150 css -> 300x300 retina
      }
    },

    start() {
      if (this.isRunning) return;
      this.isRunning = true;
      this.resize();
      this.lastTime = performance.now();
      this.loop(this.lastTime);
    },

    stop() {
      this.isRunning = false;
      if (this.animFrame) {
        cancelAnimationFrame(this.animFrame);
        this.animFrame = null;
      }
    },

    loop(time) {
      if (!this.isRunning) return;
      this.animFrame = requestAnimationFrame((t) => this.loop(t));

      const dt = Math.min((time - this.lastTime) * 0.001, 0.1);
      this.lastTime = time;

      // Lecture de l'état actuel de l'IA (idle, thinking, listening, speaking)
      const state = this.reactorEl?.dataset?.reactorState || 'idle';

      // 1. Rendu Cyber-Space 3D (Arrière-plan de la modal)
      this.renderCyberSpace(dt, state, time);

      // 2. Rendu Cœur Quantique 3D (HUD Reactor)
      this.renderQuantumCore(dt, state, time);
    },

    renderCyberSpace(dt, state, time) {
      if (!this.bgCtx || !this.bgCanvas) return;
      const ctx = this.bgCtx;
      const w = this.bgWidth;
      const h = this.bgHeight;

      // Dégradé spatial profond
      ctx.fillStyle = '#050a14';
      ctx.fillRect(0, 0, w, h);

      // Parallaxe douce
      this.camParallaxX += (this.targetParallaxX - this.camParallaxX) * 0.06;
      this.camParallaxY += (this.targetParallaxY - this.camParallaxY) * 0.06;

      const fov = 340;
      const vpx = w * 0.5 + this.camParallaxX;
      const vpy = h * 0.52 + this.camParallaxY;

      // Vitesse de défilement selon l'état de réflexion
      const speedMult = state === 'thinking' ? 2.8 : 1.0;
      this.gridOffsetZ = (this.gridOffsetZ + dt * 68 * speedMult) % 40;

      // 1. Grille cybernétique en perspective (sol)
      ctx.save();
      const gridY = 170;
      const maxZ = 680;
      const minZ = 70;

      ctx.strokeStyle = 'rgba(94, 217, 236, 0.12)';
      ctx.lineWidth = 1;

      // Lignes longitudinales
      for (let x = -640; x <= 640; x += 60) {
        const s1 = fov / (fov + minZ);
        const s2 = fov / (fov + maxZ);
        ctx.beginPath();
        ctx.moveTo(vpx + x * s1, vpy + gridY * s1);
        ctx.lineTo(vpx + x * s2, vpy + gridY * s2);
        ctx.stroke();
      }

      // Lignes transversales défilantes
      for (let z = minZ; z <= maxZ; z += 40) {
        const effZ = z - this.gridOffsetZ;
        if (effZ < minZ) continue;
        const s = fov / (fov + effZ);
        const alpha = Math.max(0, 1 - (effZ / maxZ)) * 0.22;
        ctx.strokeStyle = `rgba(94, 217, 236, ${alpha})`;
        ctx.beginPath();
        ctx.moveTo(vpx - 640 * s, vpy + gridY * s);
        ctx.lineTo(vpx + 640 * s, vpy + gridY * s);
        ctx.stroke();
      }
      ctx.restore();

      // 2. Flux de données verticaux (Cyber Rain 3D)
      for (const st of this.dataStreams) {
        st.y += st.speed * 60 * dt * speedMult;
        if (st.y > 320) {
          st.y = -300 - Math.random() * 150;
          st.x = (Math.random() - 0.5) * 850;
          st.z = Math.random() * 480 + 90;
        }

        const s = fov / (fov + st.z);
        const px = vpx + st.x * s;
        const py = vpy + st.y * s;
        const pLen = st.len * s;

        const grad = ctx.createLinearGradient(px, py, px, py + pLen);
        grad.addColorStop(0, 'rgba(94, 217, 236, 0)');
        grad.addColorStop(0.7, `rgba(94, 217, 236, ${st.alpha * 0.4})`);
        grad.addColorStop(1, `rgba(192, 132, 252, ${st.alpha * 0.85})`);

        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.4 * s;
        ctx.beginPath();
        ctx.moveTo(px, py);
        ctx.lineTo(px, py + pLen);
        ctx.stroke();
      }

      // 3. Constellation neuronale 3D (Synaptic Links)
      for (let i = 0; i < this.nodes.length; i++) {
        const n = this.nodes[i];
        n.x += n.vx * 60 * dt;
        n.y += n.vy * 60 * dt;
        n.z += n.vz * 60 * dt;

        if (n.x < -380 || n.x > 380) n.vx *= -1;
        if (n.y < -240 || n.y > 240) n.vy *= -1;
        if (n.z < 60 || n.z > 600) n.vz *= -1;

        // Connexions synaptiques entre nœuds proches
        for (let j = i + 1; j < this.nodes.length; j++) {
          const n2 = this.nodes[j];
          const dx = n.x - n2.x;
          const dy = n.y - n2.y;
          const dz = n.z - n2.z;
          const distSq = dx * dx + dy * dy + dz * dz;
          if (distSq < 130 * 130) {
            const dist = Math.sqrt(distSq);
            const linkAlpha = (1 - dist / 130) * 0.28;
            const s1 = fov / (fov + n.z);
            const s2 = fov / (fov + n2.z);

            ctx.strokeStyle = `rgba(94, 217, 236, ${linkAlpha})`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(vpx + n.x * s1, vpy + n.y * s1);
            ctx.lineTo(vpx + n2.x * s2, vpy + n2.y * s2);
            ctx.stroke();
          }
        }

        // Rendu du nœud sphérique
        const s = fov / (fov + n.z);
        const px = vpx + n.x * s;
        const py = vpy + n.y * s;
        const pr = n.size * s;

        ctx.fillStyle = n.color;
        ctx.shadowColor = n.color;
        ctx.shadowBlur = 8 * s;
        ctx.beginPath();
        ctx.arc(px, py, pr, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;
      }
    },

    renderQuantumCore(dt, state, time) {
      if (!this.coreCtx || !this.coreCanvas) return;
      const ctx = this.coreCtx;
      const cx = 75; // Centre logique 150x150
      const cy = 75;

      ctx.clearRect(0, 0, 150, 150);

      // Inertie de rotation manuelle et automatique
      if (!this.isDragging) {
        const baseSpeed = state === 'thinking' ? 3.6 : state === 'listening' ? 1.4 : 1.0;
        this.coreRotY += 0.85 * dt * baseSpeed;
        this.coreRotX += 0.5 * dt * baseSpeed;
        this.coreRotZ += 0.32 * dt * baseSpeed;
      }

      // Pulsation cardiaque quantique
      const pulseSpeed = state === 'thinking' ? 8.5 : 2.5;
      this.corePulse = Math.sin(time * 0.001 * pulseSpeed) * 0.12 + 1.0;

      // Projection 3D perspective
      const project = (x, y, z, fov = 180) => {
        // Rotation autour de X
        const cosX = Math.cos(this.coreRotX);
        const sinX = Math.sin(this.coreRotX);
        const y1 = y * cosX - z * sinX;
        const z1 = y * sinX + z * cosX;

        // Rotation autour de Y
        const cosY = Math.cos(this.coreRotY);
        const sinY = Math.sin(this.coreRotY);
        const x2 = x * cosY + z1 * sinY;
        const z2 = -x * sinY + z1 * cosY;

        // Rotation autour de Z
        const cosZ = Math.cos(this.coreRotZ);
        const sinZ = Math.sin(this.coreRotZ);
        const x3 = x2 * cosZ - y1 * sinZ;
        const y3 = x2 * sinZ + y1 * cosZ;

        const s = fov / (fov + z2);
        return {
          px: cx + x3 * s,
          py: cy + y3 * s,
          scale: s,
          z: z2
        };
      };

      // 1. Anneaux gyroscopiques 3D en rotation indépendante
      const drawGimbalRing = (radius, tiltX, tiltY, color, segments = 36, speedFactor = 1) => {
        const points = [];
        for (let i = 0; i <= segments; i++) {
          const theta = (i / segments) * Math.PI * 2;
          const rx = Math.cos(theta) * radius;
          const ry = Math.sin(theta) * radius;

          // Inclinaison de l'anneau
          const lx = rx * Math.cos(tiltY) - ry * Math.sin(tiltY);
          const ly = rx * Math.sin(tiltX) + ry * Math.cos(tiltX);
          const lz = rx * Math.sin(tiltY);

          points.push(project(lx, ly, lz));
        }

        // Tracé avec découpage avant / arrière selon z
        for (let i = 0; i < points.length - 1; i++) {
          const pt1 = points[i];
          const pt2 = points[i + 1];
          const avgZ = (pt1.z + pt2.z) / 2;
          const alpha = avgZ > 0 ? 0.35 : 0.85;

          ctx.strokeStyle = color;
          ctx.globalAlpha = alpha;
          ctx.lineWidth = (avgZ > 0 ? 1.0 : 1.8) * pt1.scale;
          ctx.beginPath();
          ctx.moveTo(pt1.px, pt1.py);
          ctx.lineTo(pt2.px, pt2.py);
          ctx.stroke();
        }
        ctx.globalAlpha = 1.0;

        // Photon orbitant le long de l'anneau
        const photonIdx = Math.floor(((time * 0.002 * speedFactor) % 1) * segments);
        const photon = points[photonIdx];
        if (photon) {
          ctx.fillStyle = '#ffffff';
          ctx.shadowColor = color;
          ctx.shadowBlur = 10;
          ctx.beginPath();
          ctx.arc(photon.px, photon.py, 2.5 * photon.scale, 0, Math.PI * 2);
          ctx.fill();
          ctx.shadowBlur = 0;
        }
      };

      const ringColor1 = state === 'thinking' ? '#ffd166' : state === 'listening' ? '#f43f5e' : '#5ed9ec';
      const ringColor2 = state === 'thinking' ? '#f59e0b' : '#c084fc';
      const ringColor3 = state === 'thinking' ? '#38bdf8' : '#2dd4bf';

      drawGimbalRing(55, 0.4, 0.6, ringColor1, 36, 1.4);
      drawGimbalRing(43, -0.6, 0.3, ringColor2, 32, -1.8);
      drawGimbalRing(32, 0.2, -0.8, ringColor3, 28, 2.2);

      // 2. Particules d'énergie quantique en vortex
      for (const p of this.energyParticles) {
        p.theta += p.speed * (state === 'thinking' ? 3.0 : 1.0);
        const px = Math.cos(p.theta) * p.rad;
        const py = Math.sin(p.theta) * (p.rad * 0.45);
        const pz = Math.sin(p.phi + p.theta) * p.rad;

        const prj = project(px, py, pz);
        ctx.fillStyle = p.color;
        ctx.globalAlpha = p.alpha * prj.scale;
        ctx.beginPath();
        ctx.arc(prj.px, prj.py, p.size * prj.scale, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1.0;

      // 3. Polyèdre 3D / Cristal Octaédrique Stellaire Central
      const s = 19 * this.corePulse;
      const s2 = s * 0.55;

      const rawVertices = [
        { x: 0, y: -s, z: 0 }, // 0 Haut
        { x: 0, y: s, z: 0 },  // 1 Bas
        { x: -s, y: 0, z: 0 }, // 2 Gauche
        { x: s, y: 0, z: 0 },  // 3 Droite
        { x: 0, y: 0, z: -s }, // 4 Avant
        { x: 0, y: 0, z: s },  // 5 Arrière
        // Points stellés interstitiels
        { x: s2, y: -s2, z: s2 },
        { x: -s2, y: -s2, z: s2 },
        { x: -s2, y: -s2, z: -s2 },
        { x: s2, y: -s2, z: -s2 }
      ];

      const projectedVerts = rawVertices.map(v => project(v.x, v.y, v.z));

      // Faces triangulaires de l'octaèdre
      const faces = [
        [0, 2, 4], [0, 4, 3], [0, 3, 5], [0, 5, 2],
        [1, 4, 2], [1, 3, 4], [1, 5, 3], [1, 2, 5]
      ];

      // Tri des faces par profondeur pour transparence propre
      const sortedFaces = faces.map(f => {
        const zAvg = (projectedVerts[f[0]].z + projectedVerts[f[1]].z + projectedVerts[f[2]].z) / 3;
        return { face: f, z: zAvg };
      }).sort((a, b) => b.z - a.z);

      for (const item of sortedFaces) {
        const f = item.face;
        const p0 = projectedVerts[f[0]];
        const p1 = projectedVerts[f[1]];
        const p2 = projectedVerts[f[2]];

        ctx.beginPath();
        ctx.moveTo(p0.px, p0.py);
        ctx.lineTo(p1.px, p1.py);
        ctx.lineTo(p2.px, p2.py);
        ctx.closePath();

        const faceGrad = ctx.createLinearGradient(p0.px, p0.py, p2.px, p2.py);
        if (state === 'thinking') {
          faceGrad.addColorStop(0, 'rgba(255, 170, 51, 0.45)');
          faceGrad.addColorStop(1, 'rgba(94, 217, 236, 0.2)');
        } else {
          faceGrad.addColorStop(0, 'rgba(94, 217, 236, 0.4)');
          faceGrad.addColorStop(1, 'rgba(168, 85, 247, 0.18)');
        }

        ctx.fillStyle = faceGrad;
        ctx.fill();

        ctx.strokeStyle = state === 'thinking' ? 'rgba(255, 209, 102, 0.7)' : 'rgba(94, 217, 236, 0.65)';
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }

      // 4. Cœur de singularité centrale blanche / incandescente
      const corePrj = project(0, 0, 0);
      const coreR = 7 * this.corePulse;

      const singGrad = ctx.createRadialGradient(
        corePrj.px, corePrj.py, 1,
        corePrj.px, corePrj.py, coreR * 2.8
      );
      if (state === 'thinking') {
        singGrad.addColorStop(0, '#ffffff');
        singGrad.addColorStop(0.3, '#ffd166');
        singGrad.addColorStop(0.7, 'rgba(255, 170, 51, 0.4)');
        singGrad.addColorStop(1, 'rgba(255, 170, 51, 0)');
      } else {
        singGrad.addColorStop(0, '#ffffff');
        singGrad.addColorStop(0.35, '#5ed9ec');
        singGrad.addColorStop(0.7, 'rgba(192, 132, 252, 0.35)');
        singGrad.addColorStop(1, 'rgba(94, 217, 236, 0)');
      }

      ctx.fillStyle = singGrad;
      ctx.beginPath();
      ctx.arc(corePrj.px, corePrj.py, coreR * 2.8, 0, Math.PI * 2);
      ctx.fill();
    }
  };

  /* ==========================================================================
     6. INITIALISATION AU CHARGEMENT DU DOM
     ========================================================================== */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      ModalManager.init();
      SolarEngine.init();
      HomeSync.init();
      QuantumNexus.init();
    });
  } else {
    ModalManager.init();
    SolarEngine.init();
    HomeSync.init();
    QuantumNexus.init();
  }

  // Exposition globale pour tests et compatibilité
  // Browsers may suspend requestAnimationFrame in background tabs. Reset clocks
  // on return so the next frame resumes smoothly without a large delta jump.
  document.addEventListener('visibilitychange', () => {
    const now = performance.now();
    SolarEngine.lastFrameTime = now;
    QuantumNexus.lastTime = now;
  });

  window.CosmosEngine = {
    ModalManager,
    SolarEngine,
    HomeSync,
    QuantumNexus
  };
})();


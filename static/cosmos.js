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
    orbitTiltAngle: 0.32,   // Inclinaison de vue perspective (cosinus d'orbite)
    orbitRotation: -0.22,   // Rotation dans le plan
    timeScale: 0.0016,
    frameCount: 0,
    lastFrameTime: performance.now(),
    fps: 60,

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

      // Pulsation douce du cœur
      this.sunPulse += 0.003 * this.sunPulseDir;
      if (this.sunPulse > 1.08) this.sunPulseDir = -1;
      if (this.sunPulse < 0.95) this.sunPulseDir = 1;

      const baseR = Math.max(34, Math.min(this.width * 0.05, 52)) * this.sunPulse;

      // 1. Rayons coronaux tournants
      this.ctx.save();
      for (const ray of this.sunRays) {
        ray.angle += ray.speed;
        const rLen = baseR + ray.length;
        const rx = cx + Math.cos(ray.angle) * rLen;
        const ry = cy + Math.sin(ray.angle) * rLen;

        this.ctx.strokeStyle = `rgba(255, 200, 100, ${ray.opacity * 0.35})`;
        this.ctx.lineWidth = 2;
        this.ctx.beginPath();
        this.ctx.moveTo(cx + Math.cos(ray.angle) * baseR, cy + Math.sin(ray.angle) * baseR);
        this.ctx.lineTo(rx, ry);
        this.ctx.stroke();
      }
      this.ctx.restore();

      // 2. Halo extérieur chaud
      const haloGrad = this.ctx.createRadialGradient(cx, cy, baseR * 0.4, cx, cy, baseR * 3.4);
      haloGrad.addColorStop(0, 'rgba(255, 190, 80, 0.45)');
      haloGrad.addColorStop(0.3, 'rgba(255, 120, 30, 0.22)');
      haloGrad.addColorStop(0.7, 'rgba(255, 70, 10, 0.06)');
      haloGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
      this.ctx.fillStyle = haloGrad;
      this.ctx.beginPath();
      this.ctx.arc(cx, cy, baseR * 3.4, 0, Math.PI * 2);
      this.ctx.fill();

      // 3. Sphère solaire incandescente avec texture d'énergie
      const sunGrad = this.ctx.createRadialGradient(
        cx - baseR * 0.25, cy - baseR * 0.25, baseR * 0.1,
        cx, cy, baseR
      );
      sunGrad.addColorStop(0, '#ffffff');
      sunGrad.addColorStop(0.2, '#fff1a8');
      sunGrad.addColorStop(0.55, '#ffad33');
      sunGrad.addColorStop(0.85, '#e65c00');
      sunGrad.addColorStop(1, '#b33600');

      this.ctx.shadowColor = '#ffaa33';
      this.ctx.shadowBlur = 32;
      this.ctx.fillStyle = sunGrad;
      this.ctx.beginPath();
      this.ctx.arc(cx, cy, baseR, 0, Math.PI * 2);
      this.ctx.fill();
      this.ctx.shadowBlur = 0;

      // 4. Anneau holographique autour du soleil MOCTAR
      this.ctx.strokeStyle = 'rgba(94, 217, 236, 0.4)';
      this.ctx.lineWidth = 1.4;
      this.ctx.setLineDash([4, 8]);
      this.ctx.beginPath();
      this.ctx.arc(cx, cy, baseR * 1.35, 0, Math.PI * 2);
      this.ctx.stroke();
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
        const activeCount = vpsList.filter((v) => v && !v.stale).length;
        homeVpsSub.textContent = vpsList.length === 0 ? 'Aucun serveur distant' : `${activeCount} actif(s) en ligne`;
      }

      // Cyberdéfense (Sentinel)
      const sentinel = snapshot.sentinel || {};
      const blocked = sentinel.blocked_today ?? 0;
      const homeBlocked = document.getElementById('home-blocked');
      const homeSentinel = document.getElementById('home-sentinel');
      if (homeBlocked) homeBlocked.textContent = `${blocked}`;
      if (homeSentinel) {
        const sActive = sentinel.agent_status === 'active';
        homeSentinel.textContent = sActive ? 'Sentinel actif' : 'En veille';
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
     5. INITIALISATION AU CHARGEMENT DU DOM
     ========================================================================== */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      ModalManager.init();
      SolarEngine.init();
      HomeSync.init();
    });
  } else {
    ModalManager.init();
    SolarEngine.init();
    HomeSync.init();
  }

  // Exposition globale pour tests et compatibilité
  window.CosmosEngine = {
    ModalManager,
    SolarEngine,
    HomeSync
  };
})();

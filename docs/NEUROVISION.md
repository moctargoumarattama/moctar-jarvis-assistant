# 🧠 NEUROVISION - Documentation d'Intégration & Spécifications Algorithmiques

Ce document archive et synthétise les spécifications techniques du projet **NeuroVision v2.2**, désormais intégré nativement dans **JARVIS / M.O.C.T.A.R** via `vision_engine.py`.

---

## 1. Objectifs & Rôle dans JARVIS

NeuroVision confère à JARVIS une conscience visuelle et biométrique de l'utilisateur :
- **Détection de présence réelle** : Savoir si Moctar est physiquement présent devant le Dell Precision M4800.
- **Surveillance de la fatigue cognitive** : Clignements, bâillements, affaissement de tête et micro-sommeil.
- **Accompagnement bienveillant proactif** : Déclenchement naturel de pauses et de rappels via `proactive_companion.py`.
- **Adaptation du HUD 3D** : Synchronisation des couleurs d'ambiance et de pulsation de l'orbe en fonction de la charge cognitive.

---

## 2. Détecteurs & Formules Mathématiques

### 👁️ Clignements & Ouverture des Yeux (EAR Rapide)
* **Algorithme** : Analyse de la région supérieure du visage ($y$ à $y + 0.4 \times h$).
* **Méthode** : Seuillage binaire inversé à 50 pour isoler les pixels sombres (pupilles) par rapport à la surface totale de l'œil.
* **Ratio d'ouverture** :
  $$\text{Ouverture} = 1.0 - \frac{\text{Pixels sombres}}{\text{Pixels totaux de la ROI}}$$
* **Détection de clignement** :
  * Si $\text{Ouverture} > 0.70$ pendant au moins 3 frames consécutives, puis réouverture $\rightarrow$ $+1$ clignement comptabilisé.
  * Taux de clignement : Calculé en clignements par minute. Un taux $> 24/\text{min}$ traduit de la fatigue oculaire.

### 🥱 Bâillements (MAR Rapide)
* **Algorithme** : Analyse de la région buccale ($y + 0.65 \times h$ à $y + h$).
* **Méthode** : Seuillage binaire inversé pour détecter la formation de la cavité buccale sombre.
* **Ratio buccal** :
  $$\text{MAR} = \frac{\text{Pixels sombres}}{\text{Pixels totaux de la ROI}}$$
* **Seuil** : $\text{MAR} > 0.25$ maintenu pendant 5 frames consécutives $\rightarrow$ $+1$ bâillement comptabilisé.

### 📐 Pose de Tête 3D (Yaw & Roll)
* **Yaw (Inclinaison horizontale)** :
  $$\text{Yaw} = \frac{\text{Center}_X - \text{FrameCenter}_X}{\frac{W}{2}} \times 30^\circ$$
* **Roll (Affaissement / Roulis vertical)** :
  $$\text{Roll} = \frac{\text{Center}_Y - \text{FrameCenter}_Y}{\frac{W}{2}} \times 20^\circ$$
* **Compensation par aspect ratio** : Si le ratio $\frac{h}{w} > 1.3$, correction $+10^\circ$ (penché en arrière) ; si $< 0.7$, correction $-10^\circ$ (penché en avant).

### ⚡ Score Global de Fatigue (0 à 100%)
Le score synthétise 5 composantes physiologiques :
1. **Taux de clignements** : Jusqu'à $+30$ points si le taux dépasse $24/\text{min}$.
2. **Nombre de bâillements** : $+10$ points par bâillement (plafonné à $+30$).
3. **Mouvements erratiques ou tête affaissée** : Jusqu'à $+20$ points si l'angle cumulé dépasse $6^\circ$.
4. **Yeux mi-clos prolongés** : $+15$ points si la moyenne mobile d'ouverture chute sous $0.35$.
5. **Durée de travail ininterrompu** : $+10$ points après 30 min, $+20$ points après 60 min.

---

## 3. Optimisations Matérielles & Benchmarks (Dell Precision M4800)

| Paramètre | Version Standard initiale | Version Optimisée v2.2 (Intégrée) |
| :--- | :--- | :--- |
| **Résolution** | 640x480 (307 200 px) | **480x360 (172 800 px)** (-44% pixels) |
| **Cadence (FPS)** | 15 FPS saccadé | **25-30 FPS fluide** |
| **Latence moyenne** | 67 ms / frame | **~18 ms / frame** (-73%) |
| **Consommation CPU** | ~45% | **~8-12%** (-60%) |
| **Qualité JPEG stream** | 85 (lourd) | **75 (léger, bande passante minime)** |
| **Traitement frame skip** | Chaque frame | **1 frame sur 2 pour l'analyse lourde** |

---

## 4. Endpoints & API JARVIS

L'architecture Flask sur le port 5001 a été entièrement supprimée et unifiée dans le serveur FastAPI principal de JARVIS :

* `GET /api/vision/metrics` : Télémétrie JSON temps réel.
* `GET /api/vision/stream` : Flux vidéo MJPEG (`multipart/x-mixed-replace`) direct.
* `POST /api/vision/toggle` : Démarrage ou coupure à chaud du capteur.
* `POST /api/vision/reset` : Remise à zéro des compteurs de session.


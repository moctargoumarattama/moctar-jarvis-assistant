# VPS conservés et courbes en direct

Le tableau de bord conservait les rapports VPS uniquement en mémoire. Un redémarrage ou une mise à jour du processus vidait donc le radar, même si le serveur distant fonctionnait encore.

Les derniers rapports authentifiés sont désormais enregistrés atomiquement. Au redémarrage, les VPS connus réapparaissent avec leur heure de dernière réception et la mention **Reconnexion en attente**. Un nouveau rapport confirme leur présence et actualise immédiatement le flux du navigateur. Le seuil de rapport ancien reste à 20 secondes et la rétention à 24 heures par défaut : conserver une carte ne signifie pas que le serveur est connecté.

La conservation commence au prochain rapport reçu après l'installation de ce correctif. Les rapports perdus avant cette installation ne peuvent pas être reconstruits.

## Fenêtre de courbes

Cliquez sur **Courbes en direct** près des ressources matérielles, ou sur **Courbes** dans le radar. Choisissez l'hôte central ou un VPS, puis une fenêtre de 5 minutes, 15 minutes ou 1 heure.

- CPU, RAM, disque et température si le capteur existe.
- Sur l'hôte central : swap, disque de sauvegarde et latence maximale des services sondés.
- Dernier état connu des services, valeurs minimale, moyenne et maximale.
- Coupures visibles, mesures absentes signalées et rapports VPS répétés ignorés.

L'historique se construit dans l'onglet ouvert, même lorsque la fenêtre est fermée. Il reste borné à une heure et disparaît au rechargement de la page ; les derniers rapports VPS sauvegardés côté serveur restent indépendants de cet historique de courbes. La fenêtre réutilise le flux SSE existant, sans connexion supplémentaire. Échap, la croix et le clic hors de la fenêtre ferment le dialogue.

## Installation existante

Déployer ensemble les sources modifiées, notamment `main.py`, `config.py`, `vps_registry.py`, `static/index.html`, `static/live_metrics.js` et `static/live_metrics.css`. Redémarrer uniquement le service du tableau de bord, puis recharger la page. Copier seulement le HTML ne suffit pas pour les nouvelles routes et la conservation des VPS.

La destination du registre est `NOC_VPS_STATE_PATH` si configurée, sinon `$STATE_DIRECTORY/vps-registry.json` sous systemd, sinon `data/vps-registry.json`. Le compte du service doit pouvoir écrire dans ce répertoire. Aucun jeton n'est enregistré dans ce fichier. Une erreur d'écriture est journalisée ; la réception en mémoire continue. Une valeur vide de `NOC_VPS_STATE_PATH` désactive la conservation.

Si aucun nouveau rapport n'arrive après le redémarrage, vérifier **sur le VPS** `systemctl status vps-agent.service` et `journalctl -u vps-agent.service -n 50 --no-pager`. Les métriques VPS et les incidents Sentinel utilisent des agents distincts : un agent Sentinel actif ne fournit pas à lui seul les mesures CPU/RAM/disque.

## Vérification locale

```powershell
& .\venv\Scripts\python.exe -m pytest tests/test_vps_registry.py tests/test_api.py tests/test_config_env.py tests/test_vps_agent.py -q
node --test tests/test_live_metrics.cjs
```

Les tests de registre couvrent le redémarrage, les dates réelles, les données invalides, la capacité, les écritures atomiques et leur annulation concurrente. Les tests des courbes couvrent les doublons, les mesures absentes, les reconnexions et le bornage de l'historique. La validation Chromium utilise exclusivement un serveur local et des données de test.

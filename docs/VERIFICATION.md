# Vérifications de livraison — 13 octobre 2026

Projet développé dans un dossier vide, sous Windows avec Python 3.11.9. Aucun projet existant ni service applicatif de production n'a été modifié.

## Vérifications initiales avant suppression du jeton de consultation

Les contrôles ci-dessous décrivent la livraison initiale, qui comportait une connexion par jeton et des cookies de session. Ce parcours a ensuite été supprimé à la demande de l'utilisateur ; il ne décrit plus l'accès au dashboard actuel.

- 37 tests automatisés `unittest` réussis : droits d'accès, cookies signés, expiration des sessions SSE, validation et limites des rapports VPS, états périmés, limite des connexions, redémarrages limités aux quatre unités, gestion des erreurs, nettoyage des processus et reprise du collecteur.
- Instance Uvicorn réelle : `/api/health` et `/api/snapshot` répondent ; `/api/stream` transmet des snapshots JSON successifs toutes les deux secondes. Le premier snapshot est transmis immédiatement, donc le délai avant le suivant dépend de la phase de collecte.
- Chrome réel : affichage à 1440 × 1100 et 390 × 844, sans débordement horizontal ni erreur JavaScript. Voir `preview.png` et `preview-mobile.png` : mesures réelles du PC Windows, services Linux absents et signalés indisponibles.
- Parcours navigateur vérifié sur une instance isolée : mauvais jeton refusé, connexion réussie, cookie `HttpOnly` et `SameSite=Strict`, actions désactivées sur Windows, réception SSE d'un rapport VPS de test, pause du journal, déconnexion et effacement des données affichées.
- Syntaxe Bash de l'installateur, compilation Python, JavaScript et cohérence des dépendances vérifiées. Les fichiers utilisent UTF-8 et des fins de ligne LF.
- Agent VPS : cinq contrôles avec `/proc` et Linux simulés, portant sur le delta CPU, le format RAM/disque, les services, la validation de la configuration et le refus des redirections.

## Vérifications restantes sur le Dell

L'installation réelle systemd, sudoers, les capteurs matériels et la passerelle WhatsApp doivent être contrôlés sur le Dell Ubuntu avec les commandes du README. Aucun accès SSH au Dell n'était fourni ; aucune installation distante n'a été effectuée.

Le RSS observé du backend sous Windows est d'environ **60 MiB**. La contrainte **inférieure à 50 Mo** n'est donc pas certifiée : elle doit être mesurée sur Ubuntu avec les VPS et navigateurs habituels. Le code expose son RSS et systemd permet de mesurer l'ensemble du groupe du service. Aucun plafond artificiel susceptible de tuer le collecteur n'est imposé.

## Reproduire les tests dans le dossier source

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe tests\check_runtime.py
```

La dernière commande suppose une instance locale démarrée. La consultation actuelle est directe : `/api/snapshot` et `/api/stream` ne demandent ni jeton ni cookie.

## Ajustement de la disposition — 13 octobre 2026

Le grand titre et le slogan ont été remplacés par une ligne de supervision. Le matériel et les services restent sur quatre colonnes sur ordinateur ; réseau, VPS et journal se répartissent ensuite sur trois colonnes. Les listes VPS et événements défilent dans leurs propres panneaux.

Comparaison dans Chrome à 1536 × 680, avec les mêmes mesures réelles du PC et les services signalés indisponibles : hauteur du document de 1277 à 744 pixels ; défilement vertical de 597 à 64 pixels. En 1440 × 900 et 1366 × 768, aucun défilement de la page. Le rendu 1280 × 720 et le mobile 390 × 844 ont également été vérifiés, sans débordement horizontal ni exception JavaScript.

Le JavaScript du dashboard et les fichiers du backend sont conservés. Pour appliquer cette mise en page à une installation existante, transférez seulement `static/index.html` vers le Dell, installez ce fichier dans `/opt/noc-dashboard/static/index.html`, puis rechargez le navigateur avec Ctrl+F5. FastAPI sert le HTML à chaque requête ; l'application n'a pas besoin d'être réinstallée.

## Suppression de la connexion — 13 octobre 2026

L'écran de connexion et le jeton de consultation sont supprimés. Le tableau, les snapshots et le flux SSE sont accessibles directement ; une ancienne variable `NOC_VIEW_TOKEN` dans la configuration est ignorée. L'installateur ne génère plus ce jeton ni de paramètre de cookie de consultation. Les jetons administrateur et VPS ainsi que le secret WhatsApp sont conservés.

Les tests de la version actuelle vérifient désormais la consultation sans authentification et le maintien des protections des rapports VPS et des redémarrages. Les 37 tests et le parcours de connexion consignés plus haut restent l'historique de la version initiale.

Vérification de la version actuelle : **37 tests réussis** après adaptation. Une instance Uvicorn réelle, démarrée avec une ancienne variable `NOC_VIEW_TOKEN` non vide, a transmis un snapshot et quatre événements SSE successifs sans jeton dans les requêtes. La syntaxe JavaScript et celle de l'installateur Bash passent également.

Le README fournit les commandes pour activer immédiatement la consultation sur un ancien serveur, puis mettre à jour seulement `config.py`, `main.py` et `static/index.html` et redémarrer `noc-dashboard.service`. Aucun redémarrage applicatif ni déploiement sur le Dell n'a été exécuté depuis cet environnement Windows.

## WhatsApp vocal et Sentinel — 13 octobre 2026

La version actuelle ajoute les routes WhatsApp et Sentinel, une transcription CPU dans un processus temporaire, un script de sauvegarde et un agent distant TELNET. Le guide courant de déploiement est `MODULES.md` ; une copie limitée aux trois fichiers de l'ancienne version ne suffit plus.

- **96 tests automatisés réussis** : anciens services, filtrage téléphone/session/secret, grammaire sans ambiguïté, files bornées, SSRF, sous-processus nettoyés, AES-GCM et altérations refusées, incidents et heartbeats, compteur quotidien, buffer 50, GeoIP en cache, pare-feu simulé et SQLite en WAL sauvegardé réellement dans un dossier temporaire.
- Commande texte `statut` envoyée à l'API locale de test : état terminé, réponse au format `sessionId/to/text` reçue par une fausse passerelle. Aucun message réel envoyé.
- Note OGG française synthétique transcrite réellement par `faster-whisper` tiny CPU int8. L'homophone reconnu « Statue des services » est normalisé uniquement en lecture de statut. La grammaire des redémarrages demeure exacte.
- Webhook accepté en **31 ms** sur ce PC. Le worker a atteint environ **232 MiB** temporairement, puis s'est terminé. Le serveur est resté autour de **64 MiB** au total, à comparer aux environ 60 MiB observés avant ces modules. Cette mesure Windows ne certifie pas une limite de mémoire Ubuntu.
- Pendant l'inférence, les deux intervalles de collecte observés sont de **2,0 secondes**. Le moteur Whisper est installé dans un venv séparé et n'est jamais importé par FastAPI.
- Chrome : compteur, incidents et dernière commande ont changé dans la page déjà ouverte, **sans navigation ni rechargement**. Une enveloppe chiffrée construite indépendamment en JavaScript a été acceptée par Python. Des identifiants contenant du HTML ont été affichés comme texte.
- Radar et journal restent à **146 px** ; hauteur du document **744 px** à 1536 × 680, identique à la disposition précédente. Mobile 390 px sans débordement ni exception JavaScript.
- Les aperçus `preview.png` et `preview-mobile.png` présentent désormais un scénario de validation : métriques réelles du PC Windows, commandes et incidents de test. Ils ne représentent pas un bannissement de production.

Les commandes de pare-feu et les unités systemd ont été examinées mais ne sont pas exécutables sur ce poste Windows. Aucun pare-feu, script existant ou service applicatif du Dell/VPS n'a été modifié. L'installation Linux et la passerelle réelle restent à appliquer avec le guide.

## Correctif de disponibilité vocale — 14 octobre 2026

L'état `voice.enabled` représente désormais la disponibilité de la transcription, indépendamment du secret WhatsApp. Le diagnostic exécute `voice_transcribe.py --check` dans un processus temporaire : import réel de faster-whisper et ouverture des actifs locaux sous les droits du service, sans chargement des poids ni téléchargement. Le backend conserve un état explicite `checking`, `ready`, `missing_model` ou `disabled`, son motif et le modèle réellement trouvé. Le canal WhatsApp expose séparément `webhook_configured`.

- **131 tests Python réussis**, incluant le diagnostic, les chemins anciens et caches, les permissions, le choix du Python, les uploads authentifiés et bornés, leur nettoyage, les doublons et la simulation sans effets.
- **8 tests Node réussis** pour le bridge Baileys : flux HTTP réel vers un serveur de test, filtres d'émetteur/session, reprise avec le même ID, limite audio, file bornée, JID alternatifs et téléchargement bloqué sans accumulation.
- Syntaxe des trois scripts Bash validée avec Git Bash ; dépendances des deux venvs cohérentes avec `pip check`.
- Le vrai worker tiny CPU/int8 a transcrit la même phrase française depuis **OGG, Opus, MP3 et WAV**. L'API `POST /api/voice/process` a accepté l'OGG brut en **16 ms**, puis publié son résultat après **2,72 secondes**. La réponse est arrivée dans une fausse passerelle locale, sans message WhatsApp réel.
- L'alias SSE `/events` a transmis les mises à jour pendant l'inférence. Les intervalles de collecte observés sont **2,11 / 1,91 / 1,98 secondes**.
- Chrome a affiché **PRÊT en vert**, puis le résultat d'une simulation `restart babamarket` soumise depuis le dialog, sans navigation ni rechargement. Le jeton saisi est effacé et aucun redémarrage n'est effectué. Aucun nouveau débordement : document de **744 px** en 1536 × 680, radar et journal de **146 px**, mobile 390 px sans débordement horizontal ni erreur JavaScript.
- L'installation vocale prépare le chemin standard `/opt/noc-dashboard/models/whisper`, réutilise un modèle local valide avant téléchargement, conserve les anciens actifs et vérifie la lecture sous le véritable utilisateur systemd. Le modèle appartient à `moctar:moctar` si ce compte existe ; les ACL donnent également la lecture au compte du NOC. L'installateur principal préserve un User/Group non-root existant et adapte la règle sudoers.

Les captures actuelles montrent le scénario local de validation. Les tests Windows et les vérifications Bash ne constituent pas un déploiement Ubuntu. La passerelle Baileys réelle, les ACL Linux et les unités du Dell restent à appliquer puis vérifier avec `MODULES.md`. Aucun service ni pare-feu de production n'a été modifié.

## Journal agrandi en modale — 14 octobre 2026

Le bouton « Agrandir » ouvre le journal dans un dialog natif, avec messages complets, dates et heures détaillées, identifiants et niveaux. Le rendu réutilise le flux existant et conserve au plus 200 lignes ; les copies de la modale sont libérées à la fermeture. Le panneau compact garde ses dimensions.

Vérification dans Chrome avec une instance locale et une route d'injection présente uniquement dans `.qa`, exclue de l'archive : 201 événements ajoutés, limite de 200 respectée dans les deux vues, nouveaux événements reçus par SSE sans navigation, absence de doublons, messages multiligne et texte HTML affichés intégralement comme texte. La pause ne force pas le défilement ; la reprise rejoint les derniers événements. Ouverture au clavier, fermeture par Échap ou bouton, retour du focus sur « Agrandir » et réouverture avec l'historique courant vérifiés.

La modale reste dans les limites du viewport en 1536 × 680, 1366 × 768, 390 × 844 et 375 × 667. Son contenu ne déborde pas horizontalement et le bouton de fermeture reste visible. Le document compact conserve sa hauteur de 744 px et le journal de 146 px en 1536 × 680. Aucune exception JavaScript ; syntaxe JS et `git diff --check` validés. `events-modal.png` montre les événements synthétiques utilisés pour ces vérifications. Aucun déploiement de production effectué.

## Contrat passerelle et réparation terminal — 14 octobre 2026

Le diagnostic communiqué par l'utilisateur confirme que le payload `{sender,message}` reçoit `403` avec « Émetteur non autorisé ». Le parseur lisait `from/key.remoteJid`, sans consulter `sender`. Le bloc Node fourni envoyait le numéro administrateur en dur et aucun ID, n'ignorait pas systématiquement `fromMe` et référençait `audioMsg` sans déclaration dans ce bloc. Les journaux de démarrage et de synchronisation ne démontrent aucun échec Whisper ; le refus de réception précède la transcription. Le bloc ne lisant pas `res.json()`, aucun conflit de retour `reply/text` n'est établi.

Les variantes `sender/message/messageId`, `X-Sender` et les JID PN avec appareil sont maintenant acceptées, tout en refusant les identités contradictoires et les LID sans PN administrateur associé. Un ID stable reste obligatoire. `status`, `aide` et `help` sont des commandes exactes de lecture seule. Le bridge peut utiliser le mapping LID local de Baileys avant téléchargement ; un lookup qui reste bloqué ne crée pas d'autres tâches orphelines.

Validation : suite Python de **158 tests réussie**, puis ajout et réussite d'un test d'exécution du code Node injecté (**159 tests Python couverts au total**). Les **16 tests Node** du bridge passent. Les tests du script vérifient que seul le handler reconnu est remplacé, les fonctions et configurations étrangères sont conservées, les patches sont idempotents et les sources ambiguës refusées. Le wrapper injecté a réellement transmis un message vers un serveur HTTP de test en modes CommonJS et ESM. La vérification de disponibilité exige le marqueur de relais attaché dans les logs de la nouvelle invocation systemd, pas un ancien log ou seulement un service actif.

Le script `tools/repair_whatsapp_contract.py` utilise uniquement la bibliothèque standard Python et le Node existant. Ses écritures root, sauvegardes et redémarrages systemd n'ont pas été exécutés sur ce poste Windows. Les contrôles de patch et les flux HTTP sont locaux ; aucun message WhatsApp réel ni service de production n'a été modifié. `--probe` sur le Dell envoie explicitement un seul `statut` au backend et affiche réception, traitement et livraison séparément ; la réception depuis WhatsApp doit ensuite être vérifiée depuis le téléphone administrateur.

## Livraison sortante et clics directs — 14 octobre 2026

Le NOC envoie maintenant exactement `to/text` sans `sessionId`, avec son secret machine et un délai de dix secondes. Le module `baileys-send-alias.cjs` enregistre `/message/send` dans la passerelle existante, sélectionne une session connectée et attend la confirmation de `sendMessage`. Le script de mise à jour conserve `/session/:ecoleId/send`, les comptes systemd et les autres routes, puis vérifie l'alias authentifié sans envoyer de message. Ses tests exécutent l'injection en CommonJS et ESM, y compris avec une déclaration `sessions` après les routes et un handler 404 final.

Le navigateur et les routes de redémarrage/simulation n'exigent plus de jeton. Le panneau de confirmation du redémarrage est supprimé ; le clic lance le POST directement et les boutons sont verrouillés pendant la demande et le délai de 30 secondes. Les contrôles de services autorisés, systemd/sudoers, Origin et Fetch Metadata restent vérifiés. L'indicateur `restart_enabled` ne dépend plus de `NOC_ADMIN_TOKEN`.

Vérification réelle dans Chrome avec un backend de test et le véritable module d'alias : un clic a produit une seule demande de redémarrage simulée, sans champ secret ni modale de confirmation ; le délai et la simulation directe sont visibles. Une commande `statut` a suivi le chemin HTTP **FastAPI → /message/send → sendMessage simulé**, avec `completed` et `reply_sent=true`, le bon destinataire et une seule réponse. Aucun message WhatsApp réel ni commande systemd exécutés. Le flux SSE et les modales d'information restent fonctionnels, sans exception JavaScript. Les tests Node de l'alias couvrent les secrets, états de session, erreur de livraison, délai, absence de reprise ambiguë et enregistrement idempotent.

Les écritures privilégiées et redémarrages de `deploy_dashboard_update.py` restent à effectuer sur Ubuntu. Le checkout local contient les fichiers testés ; la disponibilité sur le Dell dépend de l'exécution de la commande de mise à jour documentée dans `MODULES.md`.

Validation finale de cette version : **167 tests Python et 33 tests Node réussis**. Syntaxe Bash/Python/JavaScript et absence d'erreurs de patch vérifiées. Aucun nouveau paquet Python ou Node requis pour cette mise à jour.

## Numéro administrateur et rapports VPS — 14 octobre 2026

La configuration Python et le bridge Baileys suppriment tous les caractères non numériques du numéro administrateur configuré. Les tests HTTP du bridge acceptent texte et audio avec une option `+212 770-010-264` ou la variable `ADMIN_PHONE_NUMBER=+212770010264`, et refusent les autres expéditeurs, groupes, messages sortants et LID non associés. Le texte utilise `/api/whatsapp/webhook`, l'audio conserve `/api/voice/process`. Le déploiement copie aussi le bridge réellement importé dans `/usr/local/lib/noc-integrations` et synchronise le numéro normalisé et le secret entrant configuré dans l'environnement privé de la passerelle. Les tests couvrent la création de ce fichier, la préservation de ses autres valeurs et un numéro explicitement vide. Si le relais installé est configuré, sa disponibilité est vérifiée dans les logs de la nouvelle invocation systemd avant d'annoncer la réussite.

Les tests API partent de `Settings.from_env()` : `NOC_VPS_TOKEN` autorise le JSON de supervision sur `/api/vps/report` et l'enveloppe JSON chiffrée Sentinel sur `/api/sentinel/incident` avec `x-vps-token`. Un jeton vide, absent ou incorrect ne stocke aucun rapport. Les routes existantes respectaient déjà ce contrat ; elles n'ont pas été modifiées.

L'agent VPS accepte directement la cible HTTP Tailscale du Dell et garde HTTPS ou une autorisation HTTP explicite pour les autres adresses distantes. Les 22 tests de l'agent vérifient notamment les métriques Linux contre le schéma backend, un envoi HTTP local réel avec le bon header, le refus des redirections, les reprises limitées et le mode `--once`. Un `/proc/stat` vide produit désormais une erreur contrôlée. Le README documente les configurations systemd et cron.

Validation finale : **197 tests Python et 36 tests Node réussis**, compilation des fichiers Python modifiés et `git diff --check` réussis. Aucune dépendance supplémentaire. Les déploiements et redémarrages systemd sur le Dell/VPS restent à effectuer ; aucun message WhatsApp réel ni service de production n'a été exécuté pendant ces tests.

## Console Honeypot locale — 14 octobre 2026

La modale `cyberdefense-modal` s'ouvre avec `>_` ou le compteur Sentinel. Quatre boutons sélectionnent la route d'un exercice ; le serveur attribue une IP réservée à la documentation et la marque TEST. Les routes pièges GET/POST bannissent les véritables IP publiques appelantes dans le middleware du NOC. Les adresses locales, Tailscale et le VPS de gestion restent protégés. Le filtre ne fait confiance à aucun header X-Forwarded-For reçu. Un flux SSE déjà ouvert est également fermé après un bannissement, avec libération de sa place.

Le JSON est conservé dans `/var/lib/noc-dashboard/blocked_ips.json`, compatible avec `StateDirectory` et les permissions actuelles du service. Les écritures atomiques sont sérialisées, synchronisées et attendues même après annulation d'une requête ; les liens symboliques, fichiers spéciaux et fichiers trop volumineux sont refusés. Les tests couvrent redémarrage, IPv6, capacité, corruptions, échec de disque, corps JSON bornés, origine des actions et absence de nouvelles sondes pour publier les événements.

Le tableau des bans locaux, l'audit heuristique et le graphique des 60 dernières minutes utilisent le même SSE. Les compteurs et l'analyse des exercices sont séparés des scans réels ; les incidents Sentinel distants restent en lecture seule. Les règles proposées ne sont pas installées dans un pare-feu. Aucun nouveau paquet Python/Node, CDN ou modèle IA n'est requis.

Validation navigateur dans Chrome sur backend QA loopback : sélection et POST du simulateur, deux incidents de test publics injectés dans le manager sans réseau distant, tableau trié du plus récent au plus ancien, analyse dynamique des motifs, DELETE de débannissement et export JSON réussis. Échap restaure le focus sur le bouton d'ouverture, le second déclencheur et la modale de journal existante fonctionnent. Aucune exception JavaScript ni rechargement de page. La hauteur du document fermé reste identique avant/après la modale. Le contenu reste dans le viewport sans débordement horizontal en 1536 × 850, 1366 × 768, 390 × 844 et 375 × 667. Aperçus : `cyberdefense-modal.png` et `cyberdefense-mobile.png`, données QA uniquement.

Validation finale : **232 tests Python et 36 tests Node réussis**, syntaxe Bash et compilation Python/JavaScript vérifiées. Aucun service de production, règle de pare-feu ou message WhatsApp réel exécuté. Le guide `CYBERDEFENSE.md` fournit les fichiers complets et une mise à jour ciblée qui redémarre uniquement le NOC sur le Dell.

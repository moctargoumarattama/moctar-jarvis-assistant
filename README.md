# M.O.C.T.A.R

Assistant personnel et centre de supervision Ubuntu/Windows : FastAPI headless, NOC,
HUD 3D, voix, WhatsApp et interface desktop PyQt5.

## Etat actuel

Les actions autonomes restent locales ; les questions utilisent Gemini, puis le secours Groq
si configuré, puis le moteur d'intentions locales. Aucun serveur IA local n'est requis.
- `main.py` est le point d'entrée du service FastAPI headless (port 5050)
- `main_jarvis.py` lance le mode desktop PyQt5
- `assistant_core.py` orchestre les actions via un registre de plugins
- `intent_engine.py` detecte les commandes localement avec regles explicites + `rapidfuzz`
- `actions/` regroupe la logique metier par domaine
- `wake_word_simple.py` gere le wake word moderne local
- Gemini assure le tool calling ; Groq peut fournir le secours conversationnel cloud
- `ai_brain.py` peut maintenant produire des briefs quotidiens, des priorites et des suggestions locales sans cloud

Les anciens modules `JarvisGUI/`, `Gesture Control/` et autres scripts legacy ont ete regroupes sous `legacy/`. Ils sont conserves pour reference, mais ils ne pilotent plus le coeur moderne.
Le fichier `wake_word.py` est maintenant un chemin deprecated de compatibilite. Le runtime moderne utilise `wake_word_simple.py`.

## Arborescence utile

```text
Jarvis/
├── main_jarvis.py
├── assistant_core.py
├── config.py
├── wake_word_simple.py
├── interface.py
├── speech2text.py
├── text2speech.py
├── intent_engine.py
├── ai_brain.py
├── actions/
│   ├── system_actions.py
│   ├── web_actions.py
│   ├── music_actions.py
│   ├── project_actions.py
│   ├── personal_actions.py
│   ├── energy_actions.py
│   └── iot_actions.py
├── tests/
├── logs/
├── notes/
├── data/
└── screenshots/
```

## Installation

```bash
cd C:\Users\hp\Jarvis
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration

1. Edite le fichier `.env`.
2. Definis `GEMINI_API_KEY` et `AI_PROVIDER=gemini`. Le modele par defaut est `gemini-3.5-flash-lite`.
3. La couche agentique utilise le tool calling natif de Gemini et reste bornee a cinq etapes par defaut. Le delai Cloud vaut 15 secondes, avec un minimum strict de 10 secondes.
4. Ajuste les chemins projets et dossiers dans [config.py](config.py) si besoin.

Variables utiles :
- `AI_PROVIDER=gemini` (les anciennes valeurs sont ignorees)
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `GEMINI_TIMEOUT_SECONDS` (15 par defaut, minimum 10)
- `MOCTAR_AGENT_ENABLED`
- `MOCTAR_AGENT_MAX_STEPS`
- `MOCTAR_AGENT_THINKING` (`auto`, `always` ou `never`)
- `ENABLE_SOFT_WAKE_WORD`
- `SOFT_WAKE_LANGUAGE`
- `SOFT_WAKE_TIMEOUT`
- `SOFT_WAKE_PHRASE_LIMIT`
- `SOFT_WAKE_COOLDOWN_SECONDS`
- `SOFT_WAKE_DUPLICATE_SECONDS`
- `SOFT_WAKE_MIN_CONFIDENCE`
- `VOICE_COMMAND_DEBOUNCE_SECONDS`
- `JARVIS_MIC_INDEX`
- `JARVIS_MIC_TIMEOUT`
- `JARVIS_MIC_PHRASE_LIMIT`
- `JARVIS_MIC_ATTEMPTS`
- `JARVIS_MIC_AMBIENT_DURATION`
- `JARVIS_MIC_CALIBRATION_INTERVAL`
- `JARVIS_MIC_ENERGY_RATIO`
- `JARVIS_MIC_PAUSE_THRESHOLD`
- `JARVIS_MIC_PHRASE_THRESHOLD`
- `JARVIS_MIC_NON_SPEAKING_DURATION`
- `MOCTAR_IOT_BASE_URL`
- `MOCTAR_IOT_TIMEOUT`
- `MOCTAR_IOT_TEMP_THRESHOLD`
- `MOCTAR_REQUIRE_CONFIRMATION`
- `MOCTAR_ALLOW_SYSTEM_ACTIONS`
- `MOCTAR_ALLOW_WEB_ACTIONS`
- `MOCTAR_ALLOW_MUSIC_ACTIONS`
- `MOCTAR_ALLOW_PROJECT_ACTIONS`
- `MOCTAR_ALLOW_PERSONAL_ACTIONS`
- `MOCTAR_ALLOW_ENERGY_ACTIONS`
- `MOCTAR_ALLOW_IOT_ACTIONS`
- `MOCTAR_ALLOW_LOCAL_AI`

## Lancement

```bash
cd C:\Users\hp\Jarvis
venv\Scripts\activate
python main_jarvis.py
```

Le clic sur le coeur lance l'ecoute. Les modes `idle`, `listening` et `speaking` restent geres par l'UI PyQt5 actuelle.

Modes d'activation disponibles :
- clic sur le coeur : mode principal fiable
- `hey moctar` : activation vocale prioritaire et naturelle
- `yo moctar` : activation vocale prioritaire et rapide
- `yo` : activation courte, volontairement plus stricte pour limiter les faux positifs

Si l'activation vocale echoue ou si le micro est indisponible, le clic continue de fonctionner normalement.
Tu peux aussi enchaîner directement wake word + commande, par exemple `hey moctar ouvre youtube` ou `yo mets ninho`.
Si la transcription courte mange le wake word mais capte une commande claire comme `active youtube` ou `mets la musique`, M.O.C.T.A.R peut maintenant la traiter directement sans clic.

La capture de commande utilise le microphone `2` par defaut sur cette installation. Elle calibre le bruit periodiquement, retente une transcription une fois et distingue les erreurs de micro, de delai et de transcription. Les reglages `JARVIS_MIC_ENERGY_RATIO`, `JARVIS_MIC_PAUSE_THRESHOLD` et `JARVIS_MIC_PHRASE_THRESHOLD` permettent d'ajuster la sensibilite lorsque de la musique joue dans la piece. Pour forcer un autre peripherique, definis `JARVIS_MIC_INDEX` dans `.env`.

Le coeur reste autonome pour les commandes, la memoire, les briefs et les priorites.
Les réponses conversationnelles et les outils IA passent par le cloud, avec repli sur les
intentions locales lorsque nécessaire ; aucun serveur IA local n'est sondé ou utilisé.

## Commandes vocales supportees

Systeme et web :
- `ouvre brave`
- `ouvre edge`
- `ouvre github`
- `ouvre pythonanywhere`
- `ouvre telechargements`
- `ouvre documents`
- `quelle heure est-il`
- `niveau batterie`
- `fais une capture`
- `augmente volume`
- `baisse volume`
- `coupe le son`

Musique et recherche :
- `mets du ninho`
- `playlist afrobeat`
- `cherche sur google formation python`
- `cherche sur youtube energie solaire`

Projets et travail :
- `ouvre projet noor express`
- `ouvre projet baba market`
- `ouvre projet edumanage`
- `ouvre projet noor express dans vscode`
- `lance serveur flask`
- `prepare git status`
- `prepare git pull`
- `prepare git log`
- `prepare commit`
- `prepare push`

Personnel local :
- `cree une note idee audit solaire`
- `ajoute todo appeler client demain`
- `liste mes todos`
- `resume ma journee`
- `quoi faire maintenant`
- `priorise mes taches`
- `mode focus projet noor express`
- `routine du matin`
- `routine du soir`
- `que peux tu faire`
- `resume le fichier notes/audit.txt`
- `rappelle moi appeler le client a 18h30`

Le module `energy_actions.py` est deja pret et teste. Les integrations vocales energie/IoT peuvent etre branchees dans la phase suivante sans re-casser le coeur.

## Choix techniques

- `assistant_core.py` evite le spaghetti via un registre de plugins et des handlers par domaine.
- `config.py` sert de source unique pour chemins, URLs, projets et parametres runtime.
- `actions/` separe la logique par responsabilite, ce qui facilite le nettoyage et la reutilisation.
- `assistant_memory.py` ajoute une memoire de session + memoire utilisateur locale enrichie (`data/user_memory.json`) avec insights d'usage.
- `ai_brain.py` transforme cette memoire locale + les todos + les rappels en brief du jour, priorisation intelligente, routines matin/soir et mode focus projet offline.
- Les rappels tournent localement pendant que l'application reste ouverte.
- `assistant_security.py` impose confirmations sur actions sensibles et permissions par domaine.

## Securite

- Aucune cle IA externe en dur dans le code.
- `.env` est ignore par Git.
- Les mots de passe GUI restent hashes via `auth_security.py`.
- Les commandes Git sensibles ne sont pas executees automatiquement.
- Les actions locales retournent toujours une chaine exploitable par l'UI et la voix.

## Verification

Commandes de verification recommandees :

```bash
venv\Scripts\python.exe -m unittest discover -s tests -v
venv\Scripts\python.exe -m py_compile main_jarvis.py interface.py speech2text.py intent_engine.py ai_brain.py config.py
```

## Exploitation du service Ubuntu — Phase 9

Sur le Dell Precision M4800, le checkout de référence est `/opt/jarvis`.
`jarvis.service` lance `main.py` en Python 3.11 sur le port 5050 ; le mode desktop
`python main_jarvis.py` reste disponible pour une session graphique. Le service
détecte les périphériques audio/vidéo disponibles et continue en mode web lorsque
le poste ne possède pas de session graphique. Conserver un seul worker Uvicorn :
les files WhatsApp, la mémoire de session et les notifications SSE sont partagées
dans ce processus. Le planificateur démarre et s'arrête avec le service.

Interfaces : `/` pour le NOC compact (SSE toutes les deux secondes), `/hud` pour le
HUD 3D ; la modale NeuroVision 3D du HUD utilise la caméra et la télémétrie visuelle
existantes. L'authentification et les permissions de la caméra restent applicables.
Le contrôleur vocal est seul responsable de la proactivité : il informe le HUD via
`jarvis:proactive-message` sans second polling ni seconde lecture audio.

Configuration privée dans `/opt/jarvis/.env` (ne jamais commiter ses secrets) :

- `GEMINI_API_KEY`, éventuellement `GROQ_API_KEY` pour le secours cloud.
- `WHATSAPP_ADMIN_NUMBERS` ou `ADMIN_PHONE_NUMBER` pour les administrateurs autorisés.
- `NOC_WHATSAPP_WEBHOOK_TOKEN` pour les webhooks entrants ; `NOC_GATEWAY_SECRET`
  pour les réponses sortantes, identique au secret configuré dans la passerelle.
- `NOC_WHATSAPP_SEND_URL=http://127.0.0.1:3001/message/send` et
  `NOC_WHATSAPP_SESSION_ID=1` pour l'alias Baileys.
- `NOC_VPS_TOKEN` et la configuration Sentinel existante pour les rapports distants.

WhatsApp : `POST /api/whatsapp/webhook` reçoit le texte avec un ID Baileys stable,
`POST /api/voice/process` reçoit l'audio brut authentifié. Le webhook reste un ACK
rapide : la transcription, l'action puis la livraison se font dans le worker.
Une note vocale ou la demande « réponds en vocal » ajoute à la réponse texte les
champs JSON `reply`, `voice_reply_b64` et `voice_mime_type: "audio/mpeg"`.
L'audio Remy est généré par le TTS existant, limité à 1 Mio et transmis à l'alias
`integrations/baileys-send-alias.cjs`, qui envoie le texte puis une note vocale
Baileys (`ptt: true`). Le parseur JSON de la passerelle doit accepter 2 Mio pour
cette route. Un échec TTS conserve la réponse texte et ne rejoue jamais l'action.
Les autres routes et sessions Baileys restent disponibles. Une mise à jour de
Python seule ne suffit pas : copier aussi l'alias utilisé par `server.js`.

Sentinel VPS reçoit les incidents authentifiés/chiffrés du VPS et les publie en
SSE. La console Cyberdéfense conserve ses pièges, exercices, historique et audit
heuristique ; ses exercices sont distincts des scans réels. Aucun nouveau modèle
ni nouvelle dépendance n'est ajouté en Phase 9.

Diagnostic en lecture seule : « diagnostique le service jarvis.service »,
« pourquoi le service klasora-whatsapp.service plante » ou « vérifie les logs de
jarvis.service ». L'outil valide le nom, lit `systemctl is-active` et les 25
dernières lignes `journalctl` avec un délai de quatre secondes par commande et
signale l'état réellement mesuré. Il ne redémarre aucun service. Sous Windows, il
indique que la mesure doit être exécutée sur Ubuntu.

États d'exécution exposés aux interfaces :

| État | Signification |
| --- | --- |
| `success` / `completed` | Traitement terminé ; `verification_status` indique si un résultat a été vérifié. |
| `pending_confirmation` | Action vocale suspendue, liée à sa session, valable 180 s ; « confirme l'action » ou « annule l'action ». |
| `awaiting_confirmation` | Exécution planifiée interrompue : vérifier le destinataire avant de la marquer envoyée ; aucun renvoi aveugle. |
| `blocked` | Permission ou politique de sécurité refusée, `ok: false`. |
| `error` / `failed` / `degraded_fallback` | Échec réel ou indisponibilité, `ok: false` ; le texte explique la situation. |

La réponse `/api/voice/interact` inclut aussi `security_status`,
`requires_confirmation`, `pending_action_id` et `verification_status`.
Les confirmations sont consommées une seule fois. Le planificateur conserve dans
SQLite `occurrence_key` et `execution_id` ainsi qu'un journal des exécutions pour
éviter le rejeu d'un même créneau après redémarrage.

Administration sur le Dell (consulter les journaux avant un redémarrage) :

```bash
sudo systemctl status jarvis.service klasora-whatsapp.service --no-pager
sudo journalctl -u jarvis.service -n 80 --no-pager
sudo journalctl -u klasora-whatsapp.service -n 80 --no-pager
cd /opt/jarvis && git pull --ff-only && sudo systemctl restart jarvis.service
```

Avant une mise à jour touchant le connecteur, sauvegarder `server.js` et vérifier
le chemin importé par la passerelle ; mettre à jour l'alias puis redémarrer aussi
`klasora-whatsapp.service`. Préserver `.env`, `data/`, les sessions Baileys et les
bases SQLite. Les migrations ajoutent des colonnes et un journal sans supprimer
les anciennes tâches. Sauvegarder les bases via l'API backup SQLite ou service
arrêté, jamais une copie partielle pendant une écriture.

Validation locale (aucun message WhatsApp réel requis) :

```bash
python -m py_compile main.py headless_bridge.py voice_semantic_engine.py actions/system_actions.py intent_engine.py scheduler/scheduler_store.py scheduler/scheduler_core.py
node --check static/js/hud_3d.js
node --check static/voice.js
python -m pytest -q
node --test tests/*.cjs
```

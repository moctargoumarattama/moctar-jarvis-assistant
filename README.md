# M.O.C.T.A.R

Assistant personnel Windows local, modulaire, fiable et extensible, avec interface PyQt5 style Iron Man.

## Etat actuel

Le coeur actif est offline-first :
- `main_jarvis.py` est l'unique point d'entree reel
- `assistant_core.py` orchestre les actions via un registre de plugins
- `intent_engine.py` detecte les commandes localement avec regles explicites + `rapidfuzz`
- `actions/` regroupe la logique metier par domaine
- `wake_word_simple.py` gere le wake word moderne local
- la reponse generique est locale par defaut, sans dependance obligatoire a ChatGPT
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

1. Cree un fichier `.env` a partir de `.env.example`.
2. `OPENAI_API_KEY` est optionnel et n'est pas requis pour le runtime principal local.
3. Ajuste les chemins projets et dossiers dans [config.py](config.py) si besoin.

Variables utiles :
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `ENABLE_SOFT_WAKE_WORD`
- `SOFT_WAKE_LANGUAGE`
- `SOFT_WAKE_TIMEOUT`
- `SOFT_WAKE_PHRASE_LIMIT`
- `SOFT_WAKE_COOLDOWN_SECONDS`
- `SOFT_WAKE_MIN_CONFIDENCE`
- `JARVIS_MIC_INDEX`
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

- Aucune cle OpenAI en dur dans le code.
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

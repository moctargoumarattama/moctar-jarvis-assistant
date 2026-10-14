# Qwen et tests hors ligne sur Windows

Le modèle local de M.O.C.T.A.R est `qwen2.5:1.5b`. Les valeurs de référence sont définies dans `config.py` et réutilisées par le moteur et les interfaces.

Sur le Dell, conserver l'endpoint local :

```dotenv
OLLAMA_MODEL=qwen2.5:1.5b
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_TIMEOUT_SECONDS=120
```

Pour le développement Windows vers le Dell, remplacer uniquement l'adresse dans le `.env` local :

```dotenv
OLLAMA_BASE_URL=http://192.168.11.103:11434
```

Le délai de connexion ne garantit pas un temps de génération. L'inférence ReAct utilise une température basse, un maximum de 512 tokens et les arrêts `Observation:`, `<|im_end|>` et `\n\nUser:`. Le prompt demande un appel d'outil direct si nécessaire, puis `[État Opérationnel]` et `[Recommandations M.O.C.T.A.R]`, sans phrase d'attente.

Les chemins `/opt/jarvis`, `/opt/jarvis/venv/bin/python`, les bases de données et les services existants restent inchangés. Le `.env` local est ignoré par Git ; le fichier `.env.example` documente les valeurs à adapter sur chaque hôte.

## Tests sans Ollama

```powershell
& .\venv\Scripts\python.exe -m pytest -v
```

`tests/conftest.py` remplace les transports HTTP Ollama pour les adresses locales et celle du Dell. Les tests reçoivent du JSON Ollama et passent par la sérialisation et le parsing habituels. Les scénarios de panne continuent à injecter explicitement leurs erreurs. Les autres serveurs HTTP de test, les sondes et les transports de l'application restent indépendants de ces mocks.

Ces mocks sont chargés uniquement par pytest. Ils ne modifient pas le comportement de l'application en production et ne nécessitent aucune dépendance supplémentaire.

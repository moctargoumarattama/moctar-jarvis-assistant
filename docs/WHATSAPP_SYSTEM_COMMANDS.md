# Commandes système WhatsApp

Les commandes opérateur sont exécutées directement, sans Ollama. Le webhook reste protégé par son secret de passerelle et l'identité de session ; le numéro de téléphone ne remplace pas cette authentification.

## Administrateurs

Dans le `.env` réellement chargé par le service du tableau de bord **et** celui de la passerelle Baileys, définir :

```dotenv
WHATSAPP_ADMIN_NUMBERS=+212600000001,+22790000000
```

Remplacer ces exemples par vos numéros internationaux autorisés. Les espaces, parenthèses, tirets et points sont normalisés ; groupes et identifiants LID sans association vérifiée sont refusés. Une liste vide désactive les commandes. Si cette variable est absente, `ADMIN_PHONE_NUMBER` reste accepté pour les installations existantes. Une liste explicite remplace cet ancien réglage et ne l'élargit pas.

Un expéditeur non autorisé reçoit un refus HTTP 403 avec une alerte ; une trace est ajoutée au journal de supervision. Le relais Baileys filtre également les expéditeurs et conserve l'identité réelle de chaque administrateur, y compris pour la réponse. Aucun ordre rejeté ne déclenche un outil ou une inférence.

Le contenu des conversations WhatsApp libres et leurs identifiants restent masqués dans les snapshots publics. Les sessions WhatsApp sont exclues de l'historique public du tableau de bord ; les bilans des commandes opérateur restent visibles.

## Commandes

| Message | Comportement |
| --- | --- |
| `statut`, `status`, `etat`, `état` | Dernière télémétrie Dell, états systemd et HTTP des quatre applications, rapports VPS connus. |
| `restart klasora`, `redémarre klasora` | Redémarrage puis vérification `is-active`. Le succès n'est annoncé que si le service est actif. |
| `stop klasora`, `arrête klasora` | Arrêt puis vérification de l'état inactif. |
| `reboot` | Demande de confirmation explicite, valable 60 secondes pour ce numéro et cette session. |
| `CONFIRMER REBOOT` | Redémarrage de l'hôte uniquement si une demande correspondante est encore en attente. La confirmation est consommée avant exécution. |
| Autre question | Traitement par le moteur ReAct M.O.C.T.A.R ; les fragments de commandes ambiguës ou interdits sont refusés. |

Les cinq noms de service acceptés sont exactement `merca-market`, `klasora`, `babamarket`, `moc-mal` et `jarvis`. Pas de chemin, suffixe fourni par le message, joker, option ou fragment shell. Le gestionnaire ajoute lui-même `.service` et lance un tableau d'arguments avec `shell=False`. La simulation web n'exécute jamais les actions et n'arme jamais une confirmation réelle de reboot.

La demande de reboot et sa confirmation doivent être envoyées par messages écrits : une transcription vocale ne peut pas les déclencher. Le réglage existant `NOC_ENABLE_RESTART=false` bloque aussi les nouveaux arrêts et redémarrages de services ou de l'hôte, sans armer de confirmation.

Le statut est produit à partir des sondes partagées déjà en mémoire, sans HTTP, systemctl ou modèle supplémentaire. Sa génération vise moins de 0,5 seconde ; la livraison WhatsApp dépend ensuite du réseau et de la passerelle. L'heure du dernier relevé et les mesures périmées sont signalées. Un VPS restauré après redémarrage reste en attente de reconnexion tant qu'un nouveau rapport n'arrive pas. Les historiques de rapports ne sont pas présentés comme une nouvelle mesure.

Le statut dispose d'une file indépendante des questions ReAct : une inférence lente ne bloque pas sa génération. Les réponses sont bornées à 4096 caractères ; un grand parc VPS est résumé explicitement.

## Sudoers

Les chemins utilisés sont `/usr/bin/systemctl` et `/sbin/reboot`. Vérifier ces chemins sur l'hôte. Identifier l'utilisateur réel avec `systemctl show jarvis.service -p User` ou l'unité qui héberge votre passerelle. Ne pas changer cet utilisateur pour appliquer le correctif.

Éditer un fichier dédié avec `sudo visudo -f /etc/sudoers.d/moctar-system-commands`, puis adapter **uniquement** `noc-dashboard` au compte réel :

```sudoers
Cmnd_Alias MOCTAR_SERVICES = /usr/bin/systemctl restart merca-market.service, /usr/bin/systemctl stop merca-market.service, /usr/bin/systemctl restart klasora.service, /usr/bin/systemctl stop klasora.service, /usr/bin/systemctl restart babamarket.service, /usr/bin/systemctl stop babamarket.service, /usr/bin/systemctl restart moc-mal.service, /usr/bin/systemctl stop moc-mal.service, /usr/bin/systemctl restart jarvis.service, /usr/bin/systemctl stop jarvis.service
noc-dashboard ALL=(root) NOPASSWD: MOCTAR_SERVICES
noc-dashboard ALL=(root) NOPASSWD: /sbin/reboot
```

Le code utilise `sudo -n` pour échouer immédiatement si une règle manque, sans attendre de mot de passe. `systemctl is-active` s'exécute sans élévation. Le binaire `sudo` doit être installé. Conserver les autres règles existantes, notamment celles utilisées par les boutons du tableau de bord ; ne pas remplacer leur fichier par ce bloc.

Valider avec `sudo visudo -cf /etc/sudoers.d/moctar-system-commands`, puis vérifier les droits du compte avec `sudo -l -U noc-dashboard`. Ces vérifications ne redémarrent aucun service. Éviter les autorisations générales `systemctl *` ou `NOPASSWD: ALL`.

Si `jarvis.service` héberge aussi la passerelle, son arrêt ou son redémarrage peut interrompre la livraison de sa propre réponse. Vérifier le résultat après reconnexion ; le code ne doit pas annoncer un état actif non observé.

## Ollama et installation

Le modèle est choisi selon le backend : `OLLAMA_MODEL` pour Ollama, `OPENAI_MODEL` pour l'API OpenAI. Un réglage OpenAI ne doit plus faire appeler `gpt-4o-mini` sur le serveur local. Le statut distingue le serveur joignable du modèle effectivement installé.

Une question qui nécessite l'IA reçoit désormais une erreur explicite si le moteur est indisponible ; le repli ne répond plus par une salutation sans rapport avec la question. Les salutations et commandes locales connues restent disponibles.

Un serveur Ollama absent ou inaccessible doit être rétabli sur l'hôte qui l'héberge. Vérifier son service et `ollama list` sur cet hôte ; ne pas déduire une saturation CPU d'un simple refus de connexion. Le dispatcher opérateur continue de fonctionner lorsque le moteur ReAct est indisponible.

Déployer ensemble `system_manager.py`, les fichiers Python modifiés, les modules Baileys modifiés et la configuration des deux services. Les fichiers `.env` locaux ne sont pas poussés sur Git. Les règles sudoers ne sont ni installées ni exécutées automatiquement par ces modifications.

## Tests

```powershell
& .\venv\Scripts\python.exe -m pytest tests/test_system_commands.py tests/test_whatsapp_dispatcher.py tests/test_whatsapp.py tests/test_voice_api.py tests/test_config_env.py tests/test_ai_brain.py -q
node --test tests/test_baileys_noc.cjs tests/test_baileys_send_alias.cjs
```

Les commandes système sont simulées dans les tests. Aucun service réel n'est arrêté et aucun reboot n'est déclenché pendant la validation.

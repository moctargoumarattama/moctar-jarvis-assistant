# HTTPS natif et installation sur l'ecran d'accueil

Le service `jarvis.service` lance deja `/opt/jarvis/venv/bin/python /opt/jarvis/main.py`.
Ce lancement utilise HTTPS sur le port 5050 quand **les deux fichiers** existent :

```dotenv
NOC_SSL_KEYFILE=/opt/jarvis/certs/server.key
NOC_SSL_CERTFILE=/opt/jarvis/certs/server.crt
```

Sans paire complete, il conserve HTTP. Aucun certificat n'est genere automatiquement.
Un certificat present mais invalide doit etre corrige ; il ne declenche pas un repli silencieux.
Proteger la cle privee et la rendre lisible uniquement par l'utilisateur du service.
Le certificat doit couvrir le nom ou l'IP utilisee, notamment `192.168.11.103` dans ses SAN.

Avec les certificats installes, ouvrir `https://192.168.11.103:5050/#overview`.
Pour un certificat auto-signe, installer sa CA ou son certificat de confiance sur chaque
appareil : un simple contournement de l'avertissement TLS ne garantit pas le micro ni la PWA.
Le navigateur doit indiquer une connexion fiable ; autoriser ensuite le microphone.
La reconnaissance vocale depend aussi du support Web Speech du navigateur.

## Conversation mains libres sur chaque appareil

Sur le telephone ou le PC, ouvrir le dashboard en HTTPS, toucher **Activer le micro**
sur l'accueil ou dans le chat, puis autoriser le microphone dans le navigateur.
Cette activation et sa preference sont propres a chaque appareil/navigateur.
Dire **Yo Moctar** ou **ELMo** : M.O.C.T.A.R demande ce que vous souhaitez,
ecoute la commande et repond oralement si la synthese vocale est activee.
Apres chaque reponse, une fenetre de 15 secondes permet de continuer sans
repeter le mot de reveil. **Fin de conversation** revient a l'attente du mot-cle.
**Couper le micro** arrete l'ecoute.

Une seule reconnaissance gere reveil et commande; elle s'arrete pendant la
reponse pour ne pas entendre la voix de l'assistant. Le statut affiche les erreurs
de permission, de microphone et de compatibilite du navigateur.
Le mode vise un dashboard ouvert et actif : il se met en pause quand la page est
masquee et reprend au retour. Il ne garantit pas l'ecoute ecran verrouille.
Un navigateur sans Web Speech peut continuer a utiliser le chat texte.

Le silence, les fins de session automatiques et les transitions de peripherique
audio conservent l'activation : reprise apres 250 ms, ou 1,5 s si le service de
reconnaissance signale un probleme reseau. Retour sur l'onglet ou retour du focus
relancent l'ecoute demandee sans interrompre une reponse vocale en cours.

## Retirer un VPS obsolete

Dans **VPS distants**, un noeud deconnecte propose **Retirer du suivi**.
Confirmer avec le token VPS ou le secret passerelle ; la cle n'est pas memorisee.
Le retrait met a jour le registre JSON et la liste en memoire. En cas d'erreur
de sauvegarde, le noeud est conserve et l'interface permet de reessayer.
Le noeud reapparait s'il emet un nouveau rapport authentifie.

L'API equivalente est `DELETE /api/vps/{hostname}` avec `x-vps-token`
(`NOC_VPS_TOKEN`, ou ses alias) ou `x-gateway-secret` (`NOC_GATEWAY_SECRET`).
Un noeud absent depuis plus de 24 h ne declenche plus l'incident global :
il est archive/deconnecte si sa retention le conserve, sinon expire du registre.
Les incidents de l'hote principal et des VPS recents restent pris en compte.

## Passerelle et autres clients

Dans l'environnement du service `klasora-whatsapp.service`, configurer :

```dotenv
NOC_API_BASE_URL=https://127.0.0.1:5050
```

Recharger le relais Baileys modifie et sa configuration lors du deploiement.
Les helpers de synchronisation recopient cette adresse ou detectent la paire
de certificats afin de choisir le meme protocole que le serveur natif.
Le relais accepte un certificat auto-signe uniquement sur une IP locale, privee ou Tailscale.
Les destinations HTTPS publiques conservent la verification TLS.
Il n'utilise pas `NODE_TLS_REJECT_UNAUTHORIZED=0`.
Sans HTTPS, conserver `http://127.0.0.1:5050`.
Mettre aussi a jour l'URL des agents VPS et autres clients qui utilisent ce port :
HTTP et HTTPS ne sont pas servis simultanement. Ces clients doivent faire confiance
au certificat serveur ; aucune verification TLS globale n'est desactivee.

## Installation et donnees fraiches

Sur Chrome/Edge/Android, utiliser **Installer M.O.C.T.A.R** quand le navigateur propose
l'installation, ou son menu d'installation. Sur iPhone/iPad, ouvrir dans Safari puis
**Partager > Sur l'ecran d'accueil**. L'application s'ouvre dans une fenetre autonome.

Les pages, scripts et donnees API sont servis avec `Cache-Control: no-store`.
Le service worker fait uniquement des requetes reseau avec `cache: no-store` et
ne cree aucun cache hors ligne. Il nettoie seulement les anciens caches identifies
comme appartenant a NOC/M.O.C.T.A.R, sans effacer les autres applications.
Sans connexion au serveur, l'application ne fournit pas d'ancienne copie des donnees.
Le navigateur conserve necessairement les metadonnees d'installation et le service worker ;
les preferences existantes du tableau de bord sont conservees.

## Verification sans certificats physiques

```powershell
& .\venv\Scripts\python.exe -m pytest tests/test_https.py tests/test_pwa_api.py -v
node --test tests/test_baileys_noc.cjs tests/test_pwa.cjs
```

Les tests de lancement simulent Uvicorn et les fichiers SSL ; les tests HTTP n'exigent
aucun serveur Ollama ni certificat installe sur Windows.

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

## Passerelle et autres clients

Dans l'environnement du service `klasora-whatsapp.service`, configurer :

```dotenv
NOC_API_BASE_URL=https://127.0.0.1:5050
```

Recharger le relais Baileys modifie et sa configuration lors du deploiement.
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

(function (window) {
  'use strict';
  const document = window.document;
  if (!document) return;

  const installed = () => Boolean(window.navigator.standalone ||
    window.matchMedia('(display-mode: standalone)').matches ||
    window.matchMedia('(display-mode: fullscreen)').matches);
  const ios = /iPad|iPhone|iPod/.test(window.navigator.userAgent) ||
    (window.navigator.platform === 'MacIntel' && window.navigator.maxTouchPoints > 1);
  let promptEvent = null;

  const button = document.createElement('button');
  button.id = 'pwa-install';
  button.className = 'pwa-install';
  button.type = 'button';
  button.hidden = !window.isSecureContext || installed();
  button.title = "Installer M.O.C.T.A.R sur l'écran d'accueil";
  button.setAttribute('aria-label', button.title);
  button.setAttribute('aria-haspopup', 'dialog');
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M4 15v5h16v-5"/></svg><span>Installer</span>';
  (document.querySelector('.cosmos-top-right, .topbar-right') || document.body).appendChild(button);

  const help = document.createElement('dialog');
  help.id = 'pwa-install-help';
  help.className = 'pwa-help';
  help.setAttribute('aria-labelledby', 'pwa-help-title');
  button.setAttribute('aria-controls', help.id);
  const title = document.createElement('h2');
  title.id = 'pwa-help-title';
  title.textContent = "M.O.C.T.A.R sur votre écran";
  const instructions = document.createElement('p');
  instructions.textContent = ios
    ? "Dans Safari, ouvrez Partager, puis « Sur l'écran d'accueil » et « Ajouter »."
    : "Dans le menu du navigateur, choisissez « Installer M.O.C.T.A.R » ou « Ajouter à l'écran d'accueil », si cette option est disponible.";
  const online = document.createElement('p');
  online.textContent = "Connexion requise : les pages et les données sont chargées en direct, sans cache hors ligne.";
  const close = document.createElement('button');
  close.type = 'button';
  close.textContent = 'Compris';
  close.addEventListener('click', () => help.close());
  help.append(title, instructions, online, close);
  document.body.appendChild(help);

  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    promptEvent = event;
    button.hidden = !window.isSecureContext || installed();
  });
  window.addEventListener('appinstalled', () => {
    promptEvent = null;
    button.hidden = true;
    if (help.open) help.close();
  });
  button.addEventListener('click', async () => {
    if (!promptEvent) {
      if (typeof help.showModal === 'function') help.showModal();
      return;
    }
    const pending = promptEvent;
    promptEvent = null;
    try {
      await pending.prompt();
      const choice = await pending.userChoice;
      if (choice.outcome === 'accepted') button.hidden = true;
    } catch (_) {
      // Keep the manual installation instructions available after a dismissal.
      if (typeof help.showModal === 'function') help.showModal();
    }
  });

  // Never register on an insecure LAN origin; browsers require trusted HTTPS.
  if (window.isSecureContext && 'serviceWorker' in window.navigator) {
    window.navigator.serviceWorker.register('/service-worker.js', {
      scope: '/', updateViaCache: 'none',
    }).catch(() => {
      // Installation support must not interrupt telemetry or the assistant.
    });
  }
})(typeof window === 'undefined' ? globalThis : window);

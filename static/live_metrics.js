/* Session history uses collection timestamps, never repeated SSE delivery times. */
(function (root) {
  'use strict';
  const RETENTION_MS = 60 * 60 * 1000;
  const MAX_POINTS = 2400;
  const MAX_HOSTS = 129;
  const CENTRAL = 'central';
  const METRICS = [
    {key:'cpu', label:'Processeur', detail:'Charge CPU', unit:'%', percent:true},
    {key:'memory', label:'Mémoire', detail:'RAM utilisée', unit:'%', percent:true},
    {key:'disk', label:'Disque racine', detail:'Espace utilisé', unit:'%', percent:true},
    {key:'temperature', label:'Température', detail:'Capteur CPU', unit:'°C', percent:false},
    {key:'swap', label:'Mémoire swap', detail:'Espace d’échange utilisé', unit:'%', percent:true, centralOnly:true},
    {key:'backup', label:'Disque de sauvegarde', detail:'Espace utilisé · montage requis', unit:'%', percent:true, centralOnly:true},
    {key:'serviceLatency', label:'Réponse des services', detail:'Latence maximale HTTP et WhatsApp', unit:'ms', percent:false, centralOnly:true}
  ];
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const percent = value => finite(value) && value >= 0 && value <= 100 ? value : null;
  const timestamp = value => typeof value === 'string' || finite(value) ? new Date(value).getTime() : NaN;

  function createMetricsStore(options = {}) {
    const hosts = new Map();
    const retention = options.retentionMs || RETENTION_MS;
    const limit = options.maxPoints || MAX_POINTS;
    const hostLimit = options.maxHosts || MAX_HOSTS;
    let connected = false;
    let lastReceipt = 0;

    function obtain(id, label, now) {
      let host = hosts.get(id);
      if (!host) {
        if (hosts.size >= hostLimit) {
          // Evict the least recently received remote host, preserving the central host.
          const oldest = Array.from(hosts.values()).filter(item => item.id !== CENTRAL).sort((a,b) => a.receivedAt - b.receivedAt)[0];
          if (oldest) hosts.delete(oldest.id);
        }
        host = {id, label, samples:[], present:true, stale:false, restored:false, gapPending:false, lastSeen:NaN, receivedAt:now, services:[]};
        hosts.set(id, host);
      }
      host.label = label;
      return host;
    }
    function append(host, at, values, now) {
      if (!Number.isFinite(at)) return false;
      if (Number.isFinite(host.lastSeen) && at <= host.lastSeen) return false;
      host.samples.push({at, values, breakBefore:host.gapPending});
      host.gapPending = false;
      host.lastSeen = at;
      prune(host, now);
      return true;
    }
    function prune(host, now) {
      const minimum = now - retention;
      let remove = 0;
      while (remove < host.samples.length && host.samples[remove].at < minimum) remove++;
      if (remove) host.samples.splice(0, remove);
      if (host.samples.length > limit) host.samples.splice(0, host.samples.length - limit);
    }
    function ingest(snapshot, now = Date.now()) {
      if (!snapshot || typeof snapshot !== 'object' || Array.isArray(snapshot)) return;
      lastReceipt = now;
      hosts.forEach(host => { host.wasPresent = host.present; host.present = false; });
      const hardware = snapshot.hardware || {};
      const central = obtain(CENTRAL, snapshot.hostname || 'Serveur central', now);
      central.present = true;
      central.receivedAt = now;
      central.stale = Boolean(snapshot.collector?.error);
      central.services = Array.isArray(snapshot.services) ? snapshot.services : [];
      central.gapPending = central.gapPending || central.wasPresent === false;
      const measuredLatencies = central.services.map(service => service?.latency_ms).concat(snapshot.infrastructure?.whatsapp?.latency_ms).filter(value => finite(value) && value >= 0);
      append(central, timestamp(snapshot.timestamp), {
        cpu:percent(hardware.cpu?.percent), memory:percent(hardware.memory?.percent),
        disk:!hardware.disks?.root?.error && hardware.disks?.root?.available !== false ? percent(hardware.disks?.root?.percent) : null,
        temperature:finite(hardware.temperature_c) ? hardware.temperature_c : null,
        swap:percent(hardware.swap?.percent),
        backup:hardware.disks?.backup?.mounted === true && hardware.disks?.backup?.available !== false && !hardware.disks?.backup?.error ? percent(hardware.disks?.backup?.percent) : null,
        serviceLatency:measuredLatencies.length ? Math.max(...measuredLatencies) : null
      }, now);
      (Array.isArray(snapshot.vps) ? snapshot.vps : []).forEach(server => {
        if (!server || typeof server.hostname !== 'string' || !server.hostname) return;
        const host = obtain('vps:' + server.hostname, server.hostname, now);
        host.gapPending = host.gapPending || host.wasPresent === false || host.stale || host.restored;
        host.present = true;
        host.stale = server.stale === true;
        host.restored = server.restored === true;
        host.receivedAt = now;
        host.services = Array.isArray(server.services) ? server.services : [];
        const at = timestamp(server.last_seen);
        // Reports remain available while disconnected. Their timestamps must not advance.
        append(host, at, {cpu:percent(server.cpu_percent), memory:percent(server.memory?.percent), disk:percent(server.disk?.percent), temperature:finite(server.temperature_c) ? server.temperature_c : null}, now);
        if (Number.isFinite(at)) host.lastSeen = Math.max(Number.isFinite(host.lastSeen) ? host.lastSeen : at, at);
      });
      hosts.forEach(host => {
        if (!host.present) { host.stale = true; host.gapPending = true; }
        prune(host, now);
      });
    }
    function setConnection(value) {
      if (connected && !value) hosts.forEach(host => { host.gapPending = true; });
      connected = Boolean(value);
    }
    return {hosts, ingest, setConnection, prune, get connected() { return connected; }, get lastReceipt() { return lastReceipt; }};
  }

  function gapThreshold(samples, remote = false) {
    const intervals = [];
    for (let index = Math.max(1, samples.length - 30); index < samples.length; index++) {
      if (!samples[index].breakBefore) intervals.push(samples[index].at - samples[index - 1].at);
    }
    intervals.sort((a,b) => a - b);
    const typical = intervals.length ? intervals[Math.floor((intervals.length - 1) / 2)] : remote ? 10000 : 2000;
    return Math.max(remote ? 30000 : 15000, typical * 3);
  }

  function segmentSeries(samples, key, start, end, threshold) {
    const segments = [];
    let current = [];
    let previous = null;
    samples.forEach(sample => {
      if (sample.at < start || sample.at > end) return;
      const value = sample.values[key];
      if (!finite(value)) {
        if (current.length) segments.push(current);
        current = []; previous = null; return;
      }
      if (previous && (sample.breakBefore || sample.at - previous.at > threshold)) {
        if (current.length) segments.push(current);
        current = [];
      }
      current.push({at:sample.at, value});
      previous = sample;
    });
    if (current.length) segments.push(current);
    return segments;
  }

  const exported = {createMetricsStore, segmentSeries, gapThreshold, METRICS, RETENTION_MS, MAX_POINTS, MAX_HOSTS};
  if (typeof module === 'object' && module.exports) module.exports = exported;
  if (!root.document) return;

  const document = root.document;
  const store = createMetricsStore();
  const format = value => finite(value) ? value.toLocaleString('fr-FR', {maximumFractionDigits:1}) : '—';
  const timeFormat = new Intl.DateTimeFormat('fr-FR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  const clock = value => Number.isFinite(value) ? timeFormat.format(new Date(value)) : '—';
  const age = milliseconds => milliseconds < 60000 ? Math.max(0, Math.floor(milliseconds / 1000)) + ' s' : milliseconds < 3600000 ? Math.floor(milliseconds / 60000) + ' min' : Math.floor(milliseconds / 3600000) + ' h';
  const $ = id => document.getElementById(id);
  const namespace = 'http://www.w3.org/2000/svg';
  let selected = CENTRAL;
  let period = 5 * 60 * 1000;
  let opener = null;
  let initialized = false;
  const chartNodes = new Map();

  function svgElement(name, attributes) {
    const node = document.createElementNS(namespace, name);
    Object.entries(attributes || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }
  function createChart(metric) {
    const card = document.createElement('article');
    card.className = 'live-metrics-chart'; card.dataset.metric = metric.key;
    const head = document.createElement('div'); head.className = 'live-metrics-chart-head';
    const heading = document.createElement('div'), title = document.createElement('h3'), detail = document.createElement('small');
    title.textContent = metric.label; detail.textContent = metric.detail; heading.append(title, detail);
    const value = document.createElement('div'); value.className = 'live-metrics-value';
    const amount = document.createElement('span'), unit = document.createElement('small'); amount.textContent = '—'; unit.textContent = metric.unit; value.append(amount, unit); head.append(heading, value);
    const plot = document.createElement('div'); plot.className = 'live-metrics-plot';
    const svg = svgElement('svg', {viewBox:'0 0 500 150', role:'img', 'aria-label':metric.label + ' : en attente de mesures', preserveAspectRatio:'none'}); svg.classList.add('live-metrics-svg');
    const grid = svgElement('g'), lines = svgElement('g'); svg.append(grid, lines);
    const empty = document.createElement('div'); empty.className = 'live-metrics-empty'; plot.append(svg, empty);
    const axis = document.createElement('div'); axis.className = 'live-metrics-axis';
    const start = document.createElement('span'), middle = document.createElement('span'), end = document.createElement('span'); axis.append(start,middle,end);
    const stats = document.createElement('div'); stats.className = 'live-metrics-stats';
    const statsNodes = {};
    [['min','Min.'],['mean','Moy.'],['max','Pic']].forEach(([key,label]) => {
      const item = document.createElement('span'), text = document.createElement('strong'); text.textContent = '—'; item.append(label + ' ',text); stats.append(item); statsNodes[key] = text;
    });
    card.append(head,plot,axis,stats); $('live-metrics-charts').append(card);
    chartNodes.set(metric.key, {card, svg, grid, lines, amount, empty, start, middle, end, stats:statsNodes});
  }
  function updateHosts() {
    const select = $('live-metrics-host');
    const entries = [[CENTRAL, 'Hôte central' + (store.hosts.has(CENTRAL) ? ' · ' + store.hosts.get(CENTRAL).label : '')]];
    store.hosts.forEach(host => {
      if (host.id !== CENTRAL) entries.push([host.id, 'VPS · ' + host.label + (!host.present || host.stale || host.restored ? ' · ancien' : '')]);
    });
    const signature = JSON.stringify(entries);
    if (select.dataset.signature === signature) return;
    const fragment = document.createDocumentFragment();
    entries.forEach(([id,label]) => { const option = document.createElement('option'); option.value = id; option.textContent = label; fragment.append(option); });
    select.replaceChildren(fragment); select.dataset.signature = signature;
    if (!entries.some(([id]) => id === selected)) selected = CENTRAL;
    select.value = selected;
  }
  function drawChart(metric, host, now) {
    const nodes = chartNodes.get(metric.key);
    nodes.card.hidden = Boolean(metric.centralOnly && selected !== CENTRAL);
    if (nodes.card.hidden) return;
    const start = now - period;
    const samples = host?.samples || [];
    const segments = segmentSeries(samples, metric.key, start, now, gapThreshold(samples, selected !== CENTRAL));
    const values = segments.flat().map(point => point.value);
    const latest = samples.at(-1);
    const current = latest?.values[metric.key];
    nodes.amount.textContent = format(current);
    nodes.card.dataset.points = String(values.length);
    const low = metric.percent ? 0 : Math.min(0, Math.floor(Math.min(...values, 0) / 20) * 20);
    const high = metric.percent ? 100 : metric.key === 'serviceLatency' ? Math.max(50, Math.ceil(Math.max(...values, 0) / 50) * 50) : Math.max(100, Math.ceil(Math.max(...values, 0) / 20) * 20);
    const x = at => 34 + (at - start) / period * 462;
    const y = value => 137 - (value - low) / (high - low) * 126;
    nodes.grid.replaceChildren();
    [0,.25,.5,.75,1].forEach(ratio => {
      const height = 11 + ratio * 126;
      nodes.grid.append(svgElement('line', {x1:34,x2:496,y1:height,y2:height,class:'live-metrics-grid-line'}));
      const label = svgElement('text', {x:27,y:height + 3,'text-anchor':'end',class:'live-metrics-y-label'}); label.textContent = format(high - ratio * (high - low)); nodes.grid.append(label);
    });
    nodes.lines.replaceChildren();
    segments.forEach(segment => {
      if (segment.length > 1) nodes.lines.append(svgElement('path', {d:segment.map((point,index) => (index ? 'L' : 'M') + x(point.at).toFixed(2) + ' ' + y(point.value).toFixed(2)).join(' '),class:'live-metrics-path'}));
      const point = segment.at(-1);
      nodes.lines.append(svgElement('circle', {cx:x(point.at).toFixed(2),cy:y(point.value).toFixed(2),r:segment.length === 1 ? 3 : 2.3,class:'live-metrics-dot'}));
    });
    nodes.empty.hidden = values.length > 0;
    nodes.empty.textContent = metric.key === 'temperature' && samples.length && !samples.some(sample => finite(sample.values.temperature)) ? 'Capteur non disponible sur cet hôte' : metric.key === 'backup' && samples.length && !samples.some(sample => finite(sample.values.backup)) ? 'Disque de sauvegarde non monté ou indisponible' : metric.key === 'serviceLatency' && samples.length && !samples.some(sample => finite(sample.values.serviceLatency)) ? 'Aucune latence de service mesurée' : samples.length ? 'Aucun relevé sur cette période' : 'En attente du premier relevé';
    nodes.start.textContent = clock(start); nodes.middle.textContent = clock(start + period / 2); nodes.end.textContent = clock(now);
    const minimum = values.length ? Math.min(...values) : null, maximum = values.length ? Math.max(...values) : null;
    const mean = values.length ? values.reduce((sum,value) => sum + value, 0) / values.length : null;
    nodes.stats.min.textContent = format(minimum) + (values.length ? ' ' + metric.unit : '');
    nodes.stats.mean.textContent = format(mean) + (values.length ? ' ' + metric.unit : '');
    nodes.stats.max.textContent = format(maximum) + (values.length ? ' ' + metric.unit : '');
    nodes.svg.setAttribute('aria-label', metric.label + ' : dernière valeur ' + format(current) + ' ' + metric.unit + ', ' + values.length + ' mesure(s) sur ' + period / 60000 + ' minutes. Les interruptions apparaissent en coupures.');
  }
  function renderServices(host) {
    const services = (host?.services || []).slice(0,16);
    $('live-metrics-services').hidden = !services.length;
    const fragment = document.createDocumentFragment();
    services.forEach(service => {
      const card = document.createElement('div'); card.className = 'live-metrics-service';
      const name = document.createElement('strong'); name.textContent = service.label || service.name || 'Service';
      const state = document.createElement('span');
      if (typeof service.healthy === 'boolean') {
        card.dataset.tone = service.healthy ? 'success' : 'error';
        state.textContent = (service.healthy ? 'En ligne' : 'Incident') + ' · ' + format(service.latency_ms) + ' ms';
      } else {
        card.dataset.tone = service.status === 'active' ? 'success' : service.status === 'failed' || service.status === 'inactive' ? 'error' : 'neutral';
        state.textContent = service.status || 'État inconnu';
      }
      card.append(name,state); fragment.append(card);
    });
    $('live-metrics-service-list').replaceChildren(fragment);
  }
  function render(now = Date.now()) {
    if (!initialized || !$('live-metrics-dialog').open) return;
    updateHosts();
    const host = store.hosts.get(selected);
    if (host) store.prune(host, now);
    const oldStream = !store.connected || !store.lastReceipt || now - store.lastReceipt > 7500;
    const oldHost = Boolean(host && (!host.present || host.stale || host.restored || !Number.isFinite(host.lastSeen) || now - host.lastSeen > gapThreshold(host.samples, selected !== CENTRAL)));
    const status = $('live-metrics-status');
    status.dataset.tone = oldStream || oldHost || !host ? 'warning' : 'success';
    $('live-metrics-status-text').textContent = !host ? 'EN ATTENTE' : oldStream ? 'FLUX INTERROMPU' : host.restored ? 'RECONNEXION EN ATTENTE' : oldHost ? 'MESURES ANCIENNES' : 'FLUX EN DIRECT';
    $('live-metrics-host-title').textContent = host ? (selected === CENTRAL ? 'Hôte central · ' : 'VPS · ') + host.label : 'Hôte central · en attente';
    $('live-metrics-last-seen').textContent = host && Number.isFinite(host.lastSeen) ? 'Dernière mesure : ' + clock(host.lastSeen) + ' · il y a ' + age(now - host.lastSeen) : 'Les mesures apparaîtront dès leur première réception.';
    const samples = host?.samples.filter(sample => sample.at >= now - period && sample.at <= now) || [];
    $('live-metrics-count').textContent = samples.length + ' relevé(s) · ' + period / 60000 + ' min';
    const notice = $('live-metrics-notice');
    notice.hidden = Boolean(host && !oldStream && !oldHost);
    notice.textContent = !host ? 'En attente du collecteur. L’historique se construit pendant cette session, même lorsque cette fenêtre est fermée.' : oldStream ? 'Connexion au collecteur interrompue. Les dernières valeurs sont conservées ; les courbes reprendront à la réception de nouvelles mesures.' : host.restored ? 'Dernier rapport restauré après redémarrage du tableau de bord. En attente d’un nouveau rapport de cet agent VPS ; ces valeurs sont anciennes.' : !host.present ? 'Cet hôte ne figure plus dans le dernier relevé. Son historique de session est conservé ; aucune mesure n’est inventée.' : 'Dernières valeurs connues : le rapport de cet hôte est ancien. En attente de nouvelles mesures.';
    METRICS.forEach(metric => drawChart(metric,host,now));
    renderServices(host);
  }
  function initialize() {
    if (initialized || !$('live-metrics-dialog')) return;
    initialized = true;
    METRICS.forEach(createChart);
    document.querySelectorAll('[data-live-metrics-open]').forEach(button => button.addEventListener('click', () => {
      opener = button;
      if (button.dataset.liveMetricsOpen === 'vps') selected = Array.from(store.hosts.keys()).find(id => id !== CENTRAL) || CENTRAL;
      else selected = CENTRAL;
      $('live-metrics-dialog').showModal(); render();
    }));
    $('live-metrics-close').addEventListener('click', () => $('live-metrics-dialog').close());
    $('live-metrics-dialog').addEventListener('close', () => { if (opener?.isConnected) opener.focus({preventScroll:true}); });
    $('live-metrics-dialog').addEventListener('click', event => {
      const dialog = $('live-metrics-dialog');
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
    $('live-metrics-host').addEventListener('change', event => { selected = event.target.value; render(); });
    document.querySelectorAll('[data-live-metrics-minutes]').forEach(button => button.addEventListener('click', () => {
      period = Number(button.dataset.liveMetricsMinutes) * 60000;
      document.querySelectorAll('[data-live-metrics-minutes]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      render();
    }));
  }
  root.NocLiveMetrics = {
    ingest(snapshot) { store.ingest(snapshot); render(); },
    setConnection(connected) { store.setConnection(connected); render(); },
    tick(now) { render(now); }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once:true});
  else initialize();
})(typeof window !== 'undefined' ? window : globalThis);

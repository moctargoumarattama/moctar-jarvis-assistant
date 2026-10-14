const test = require('node:test');
const assert = require('node:assert/strict');
const {createMetricsStore, segmentSeries} = require('../static/live_metrics.js');

const BASE = Date.parse('2026-10-14T18:00:00Z');
function snapshot(offset = 0, options = {}) {
  return {
    hostname: 'central-test', timestamp: new Date(BASE + offset).toISOString(),
    hardware: {
      cpu: {percent: 27}, memory: {percent: 43}, swap: {percent: 2}, temperature_c: null,
      disks: {root: {percent: 61, available: true}, backup: {percent: null, available: false}},
    },
    collector: {error: null}, services: [],
    vps: [{hostname: 'remote-test', last_seen: new Date(BASE + offset).toISOString(),
      cpu_percent: 15, memory: {percent: 35}, disk: {percent: 55}, stale: false, ...options}],
  };
}

test('repeated SSE snapshots never invent host or VPS samples', () => {
  const store = createMetricsStore();
  const first = snapshot();
  store.ingest(first, BASE);
  store.ingest(first, BASE + 1000);
  const second = snapshot(2000);
  second.vps = first.vps;
  store.ingest(second, BASE + 2000);
  assert.equal(store.hosts.get('central').samples.length, 2);
  assert.equal(store.hosts.get('vps:remote-test').samples.length, 1);
});

test('missing measurements remain gaps instead of zero values', () => {
  const store = createMetricsStore();
  const input = snapshot();
  input.hardware.cpu.percent = null;
  input.hardware.disks.root = {percent: 42, available: false};
  store.ingest(input, BASE);
  const values = store.hosts.get('central').samples[0].values;
  assert.equal(values.cpu, null);
  assert.equal(values.disk, null);
  assert.equal(values.temperature, null);
});

test('stream interruption breaks the next line without erasing known data', () => {
  const store = createMetricsStore();
  store.setConnection(true);
  store.ingest(snapshot(), BASE);
  store.setConnection(false);
  store.setConnection(true);
  store.ingest(snapshot(2000), BASE + 2000);
  const samples = store.hosts.get('central').samples;
  assert.equal(samples.length, 2);
  assert.equal(segmentSeries(samples, 'cpu', BASE, BASE + 4000, 15000).length, 2);
});

test('VPS stale or restored reports stay labelled old and do not advance history', () => {
  const store = createMetricsStore();
  store.ingest(snapshot(), BASE);
  const stale = snapshot(5000, {last_seen: new Date(BASE).toISOString(), stale: true, restored: true});
  store.ingest(stale, BASE + 5000);
  const host = store.hosts.get('vps:remote-test');
  assert.equal(host.samples.length, 1);
  assert.equal(host.restored, true);
  store.ingest(snapshot(10000), BASE + 10000);
  assert.equal(host.samples.length, 2);
  assert.equal(host.samples[1].breakBefore, true);
});

test('VPS removed from a snapshot retains its session history and recovers with a gap', () => {
  const store = createMetricsStore();
  store.ingest(snapshot(), BASE);
  const absent = snapshot(2000); absent.vps = [];
  store.ingest(absent, BASE + 2000);
  const host = store.hosts.get('vps:remote-test');
  assert.equal(host.present, false);
  assert.equal(host.samples.length, 1);
  store.ingest(snapshot(5000), BASE + 5000);
  assert.equal(host.present, true);
  assert.equal(host.samples[1].breakBefore, true);
});

test('history is bounded by both elapsed time and point count', () => {
  const store = createMetricsStore({retentionMs: 10000, maxPoints: 3});
  for (let offset = 0; offset <= 8000; offset += 2000) store.ingest(snapshot(offset), BASE + offset);
  assert.deepEqual(store.hosts.get('central').samples.map(item => item.at - BASE), [4000, 6000, 8000]);
  store.ingest(snapshot(30000), BASE + 30000);
  assert.equal(store.hosts.get('central').samples.length, 1);
});

test('a cached VPS report cannot reappear as new after its history is purged', () => {
  const store = createMetricsStore({retentionMs: 10000});
  const first = snapshot();
  store.ingest(first, BASE);
  const later = snapshot(20000); later.vps = first.vps;
  store.ingest(later, BASE + 20000);
  const host = store.hosts.get('vps:remote-test');
  assert.equal(host.samples.length, 0);
  store.ingest(later, BASE + 21000);
  assert.equal(host.samples.length, 0);
  assert.equal(host.lastSeen, BASE);
});

test('central-only series use measured swap, mounted backup and maximum service latency', () => {
  const store = createMetricsStore();
  const input = snapshot();
  input.hardware.disks.backup = {percent: 75, available: true, mounted: true};
  input.services = [{latency_ms: 8}, {latency_ms: null}, {latency_ms: 22}];
  input.infrastructure = {whatsapp: {latency_ms: 15}};
  store.ingest(input, BASE);
  const values = store.hosts.get('central').samples[0].values;
  assert.equal(values.swap, 2);
  assert.equal(values.backup, 75);
  assert.equal(values.serviceLatency, 22);
  const next = snapshot(2000);
  next.hardware.disks.backup = {percent: 75, available: true, mounted: false};
  store.ingest(next, BASE + 2000);
  assert.equal(store.hosts.get('central').samples[1].values.backup, null);
  assert.equal(store.hosts.get('central').samples[1].values.serviceLatency, null);
});

test('tracking new remote hosts stays bounded and preserves the central host', () => {
  const store = createMetricsStore({maxHosts: 3});
  for (let index = 0; index < 8; index++) {
    const input = snapshot(index * 2000);
    input.vps[0].hostname = 'host-' + index;
    store.ingest(input, BASE + index * 2000);
  }
  assert.equal(store.hosts.size, 3);
  assert.equal(store.hosts.has('central'), true);
  assert.equal(store.hosts.has('vps:host-7'), true);
});

test('plot segmentation uses real timestamps and breaks on missing values or large gaps', () => {
  const samples = [
    {at: 0, values: {cpu: 10}}, {at: 2000, values: {cpu: 20}},
    {at: 4000, values: {cpu: null}}, {at: 6000, values: {cpu: 30}},
    {at: 50000, values: {cpu: 40}},
  ];
  const segments = segmentSeries(samples, 'cpu', 0, 60000, 15000);
  assert.deepEqual(segments.map(segment => segment.map(point => point.at)), [[0, 2000], [6000], [50000]]);
});

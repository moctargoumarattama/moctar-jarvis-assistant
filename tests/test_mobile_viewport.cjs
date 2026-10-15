'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/mobile.js'), 'utf8');

function frontend({mobile = true, visualViewport = true} = {}) {
  const properties = new Map();
  const attributes = new Set();
  const events = {};
  const frames = [];
  const media = {matches: mobile, addEventListener: (name, handler) => {events['media:' + name] = handler;}};
  const viewport = visualViewport ? {
    height: 844, offsetTop: 0,
    addEventListener: (name, handler) => {events['viewport:' + name] = handler;},
  } : undefined;
  const window = {
    document: {documentElement: {
      style: {
        setProperty: (name, value) => properties.set(name, value),
        removeProperty: name => properties.delete(name),
      },
      toggleAttribute: (name, force) => force ? attributes.add(name) : attributes.delete(name),
      removeAttribute: name => attributes.delete(name),
    }},
    visualViewport: viewport,
    innerHeight: 900,
    matchMedia: query => {assert.equal(query, '(max-width: 767px)'); return media;},
    addEventListener: (name, handler) => {events['window:' + name] = handler;},
    requestAnimationFrame: handler => {frames.push(handler);},
  };
  vm.runInNewContext(source, {window});
  return {properties, attributes, events, frames, media, viewport, window,
    flush: () => {while (frames.length) frames.shift()();}};
}

test('mobile viewport follows keyboard height and viewport panning in one frame', () => {
  const ui = frontend();
  assert.equal(ui.properties.get('--mobile-viewport-height'), '844px');
  assert.equal(ui.properties.get('--mobile-viewport-top'), '0px');

  ui.viewport.height = 392;
  ui.viewport.offsetTop = 46;
  ui.events['viewport:resize']();
  ui.events['viewport:scroll']();
  ui.events['window:resize']();
  assert.equal(ui.frames.length, 1);
  ui.flush();
  assert.equal(ui.properties.get('--mobile-viewport-height'), '392px');
  assert.equal(ui.properties.get('--mobile-viewport-top'), '46px');
});

test('desktop has no mobile viewport overrides and clears them after resizing', () => {
  const ui = frontend({mobile: false});
  assert.equal(ui.properties.size, 0);
  ui.media.matches = true;
  ui.events['media:change']();
  ui.flush();
  assert.equal(ui.properties.get('--mobile-viewport-height'), '844px');

  ui.media.matches = false;
  ui.events['media:change']();
  ui.flush();
  assert.equal(ui.properties.size, 0);
  ui.events['viewport:scroll']();
  ui.flush();
  assert.equal(ui.properties.size, 0);
});

test('browsers without VisualViewport use current window height', () => {
  const ui = frontend({visualViewport: false});
  assert.equal(ui.properties.get('--mobile-viewport-height'), '900px');
  assert.equal(ui.properties.get('--mobile-viewport-top'), '0px');
  ui.window.innerHeight = 480;
  ui.events['window:resize']();
  ui.flush();
  assert.equal(ui.properties.get('--mobile-viewport-height'), '480px');
});

test('a temporarily empty visual viewport falls back to window height', () => {
  const ui = frontend();
  ui.viewport.height = 0;
  ui.viewport.offsetTop = -8;
  ui.events['viewport:resize']();
  ui.flush();
  assert.equal(ui.properties.get('--mobile-viewport-height'), '900px');
  assert.equal(ui.properties.get('--mobile-viewport-top'), '0px');
});

test('compact mobile layout follows keyboard space and resets on desktop', () => {
  const ui = frontend();
  assert.equal(ui.attributes.has('data-mobile-compact'), false);
  ui.viewport.height = 480;
  ui.events['viewport:resize']();
  ui.flush();
  assert.equal(ui.attributes.has('data-mobile-compact'), true);

  ui.viewport.height = 481;
  ui.events['viewport:resize']();
  ui.flush();
  assert.equal(ui.attributes.has('data-mobile-compact'), false);

  ui.viewport.height = 340;
  ui.events['viewport:resize']();
  ui.flush();
  assert.equal(ui.attributes.has('data-mobile-compact'), true);
  ui.media.matches = false;
  ui.events['media:change']();
  ui.flush();
  assert.equal(ui.attributes.has('data-mobile-compact'), false);
});

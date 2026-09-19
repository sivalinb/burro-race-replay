/* Portable behavioral checks for the Grafana panel. All workout data is fictional.
 * The fake DOM records SVG image URLs without fetching them; no browser or network is used.
 * Run from any working directory: node /path/to/repo/tests/test_replay.cjs
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const panelPath = path.join(__dirname, '..', 'panels', 'race', 'on-init.js');
const panelCode = fs.readFileSync(panelPath, 'utf8');

class FakeNode {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.textContent = '';
    this.value = 0;
    this.listeners = new Map();
    this.selected = new Map();
    this.style = {setProperty: (key, value) => { this.style[key] = value; }};
    const classes = new Set();
    this.classList = {
      add: key => classes.add(key),
      remove: key => classes.delete(key),
      toggle: (key, enabled) => enabled ? classes.add(key) : classes.delete(key),
      contains: key => classes.has(key),
    };
  }
  addEventListener(event, fn, options = {}) {
    if (!this.listeners.has(event)) this.listeners.set(event, []);
    this.listeners.get(event).push({fn, once: Boolean(options.once)});
  }
  removeEventListener(event, fn) {
    const remaining = (this.listeners.get(event) || []).filter(item => item.fn !== fn);
    if (remaining.length) this.listeners.set(event, remaining);
    else this.listeners.delete(event);
  }
  emit(event, detail = {}) {
    for (const item of [...(this.listeners.get(event) || [])]) {
      item.fn(detail);
      if (item.once) this.removeEventListener(event, item.fn);
    }
  }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this.children = nodes; }
  querySelector(selector) {
    if (!this.selected.has(selector)) this.selected.set(selector, new FakeNode(selector));
    return this.selected.get(selector);
  }
  querySelectorAll(tag) { return this.children.filter(node => node.tag === tag); }
}

function frames(tables, vector = false) {
  return {
    state: 'Done',
    series: Object.entries(tables).map(([refId, rows]) => ({
      refId,
      length: rows.length,
      fields: Object.keys(rows[0] || {}).map(name => ({
        name,
        values: vector ? {length: rows.length, get: i => rows[i][name]} : rows.map(row => row[name]),
      })),
    })),
  };
}

function fixture({vector = false, synthetic = 1, reducedMotion = true, raceChanges = {}, points: customPoints} = {}) {
  const nodes = {};
  const root = new FakeNode();
  const html = new FakeNode();
  html.getElementById = id => nodes[id] || (nodes[id] = new FakeNode());
  html.querySelector = () => root;
  const race = {
    title: 'Fictional fixture', start_time: '2026-01-01T00:00:00Z', source: 'Fixture Watch',
    synthetic, point_count: 5, hr_count: 2, gps_distance_m: 1000, elapsed_s: 120,
    pauses_json: '[{"start_s":40,"end_s":60}]', ...raceChanges,
  };
  const points = customPoints || [
    {elapsed_s: 10, distance_m: 0, lat: 40, lon: -105, elevation_m: 100, pace_s_km: null, segment: 0},
    {elapsed_s: 30, distance_m: 200, lat: 40, lon: -104.998, elevation_m: 105, pace_s_km: 500, segment: 0},
    {elapsed_s: 50, distance_m: 200, lat: 40, lon: -104.997, elevation_m: 107, pace_s_km: null, segment: 1},
    {elapsed_s: 70, distance_m: 200, lat: 40, lon: -104.996, elevation_m: 110, pace_s_km: null, segment: 2},
    {elapsed_s: 100, distance_m: 1000, lat: 40, lon: -104.990, elevation_m: 120, pace_s_km: 600, segment: 2},
  ];
  const tables = {A: [race], B: points, C: [], D: [{elapsed_s: 20, bpm: 150}, {elapsed_s: 70, bpm: 160}]};
  const data = frames(tables, vector);
  const media = new FakeNode();
  media.matches = reducedMotion;
  let nextFrame = 0, now = 0;
  const queuedFrames = new Map(), createdImages = [];
  const document = {
    hidden: false,
    createElementNS: (namespace, tag) => {
      assert.equal(namespace, 'http://www.w3.org/2000/svg');
      const node = new FakeNode(tag);
      if (tag === 'image') createdImages.push(node);
      return node;
    },
    createElement: tag => new FakeNode(tag),
  };
  const context = {
    htmlNode: html, htmlGraphics: {data, customProperties: {}},
    window: {matchMedia: () => media}, performance: {now: () => now}, document,
    requestAnimationFrame: fn => { const id = ++nextFrame; queuedFrames.set(id, fn); return id; },
    cancelAnimationFrame: id => queuedFrames.delete(id),
    fetch: () => { throw new Error('The replay test must not make network requests'); },
    XMLHttpRequest: class { constructor() { throw new Error('Network access is not available in this test'); } },
  };
  vm.runInNewContext(panelCode, context, {filename: panelPath});
  return {
    nodes, root, html, media, document, data, tables, createdImages, queuedFrames,
    seek: seconds => { nodes.seek.value = seconds / race.elapsed_s * 1000; nodes.seek.emit('input'); },
    click: id => nodes[id].emit('click'),
    update: changes => html.__burroUpdate(frames({...tables, ...changes}, vector)),
    step: timestamp => {
      now = timestamp;
      const callbacks = [...queuedFrames.values()];
      queuedFrames.clear();
      for (const fn of callbacks) fn(timestamp);
    },
  };
}

let count = 0;
function test(name, run) {
  try { run(); count += 1; console.log(`PASS ${name}`); }
  catch (error) { console.error(`FAIL ${name}`); throw error; }
}

function assertFiniteProjection(f) {
  const route = f.nodes['route-base'].attributes.d;
  assert(route && !/NaN|Infinity/.test(route), 'Route must have finite projected coordinates');
  const points = [...route.matchAll(/[ML](-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)/g)];
  assert.equal(points.length, f.tables.B.length);
  for (const point of points) assert(Number.isFinite(Number(point[1])) && Number.isFinite(Number(point[2])));
  const marker = f.nodes['runner-marker'].attributes.transform;
  if (marker) assert.match(marker, /^translate\(-?\d+(?:\.\d+)? -?\d+(?:\.\d+)?\)$/);
}

function assertVisibleTiles(f) {
  const tiles = f.nodes['map-tiles'].children;
  assert(tiles.length > 0 && tiles.length <= 20, 'Only a bounded current viewport should have tiles');
  const seen = new Set();
  for (const tile of tiles) {
    assert.equal(tile.tag, 'image');
    const attrs = tile.attributes, url = new URL(attrs.href);
    assert.equal(url.origin, 'https://tile.openstreetmap.org');
    assert.equal(url.search, '');
    assert.equal(url.hash, '');
    const xyz = url.pathname.match(/^\/(\d+)\/(\d+)\/(\d+)\.png$/);
    assert(xyz, 'Tiles must use the standard z/x/y PNG endpoint');
    const [zoom, x, y] = xyz.slice(1).map(Number);
    assert(zoom >= 2 && zoom <= 19 && x >= 0 && x < 2 ** zoom && y >= 0 && y < 2 ** zoom);
    assert.equal(Number(attrs.width), 256);
    assert.equal(Number(attrs.height), 256);
    assert(Number(attrs.x) <= 920 && Number(attrs.x) + 256 > 0);
    assert(Number(attrs.y) <= 420 && Number(attrs.y) + 256 > 0);
    assert(!seen.has(attrs.href));
    seen.add(attrs.href);
  }
}

for (const vector of [false, true]) {
  test(`${vector ? 'Vector' : 'Array'} frames preserve GPS bounds, pause holds, gaps, and HR expiry`, () => {
    const f = fixture({vector}), n = f.nodes;
    f.seek(0);
    assert.equal(n['current-distance'].textContent, 'No GPS');
    assert.equal(n['current-pace'].textContent, '—');
    assert.equal(n['current-elevation'].textContent, '—');
    assert.equal(n['runner-marker'].style.display, 'none');
    assert.equal(n['replay-label'].textContent, 'AWAITING GPS');
    assert.equal(n['current-hr'].textContent, '—');
    f.seek(40); const held = n['runner-marker'].attributes.transform;
    f.seek(55);
    assert.equal(n['runner-marker'].attributes.transform, held);
    assert.equal(n['replay-label'].textContent, 'WATCH PAUSED');
    assert.equal(n['current-pace'].textContent, '—');
    assert.equal(n['current-hr'].textContent, '—');
    assert(f.root.classList.contains('gap'));
    f.seek(60); assert.equal(n['replay-label'].textContent, 'GPS GAP');
    f.seek(100);
    assert.equal(n['current-distance'].textContent, '1.00 km');
    assert.equal(n['current-pace'].textContent, '10:00');
    assert.equal(n['current-elevation'].textContent, '120');
    assert.equal(n['current-hr'].textContent, '160');
    f.seek(110);
    assert.equal(n['current-distance'].textContent, '1.00 km');
    assert.equal(n['current-pace'].textContent, '—');
    assert.equal(n['current-elevation'].textContent, '—');
    assert.equal(n['replay-label'].textContent, 'GPS ENDED');
    assert.equal(n['current-hr'].textContent, '—');
    f.html.__burroCleanup();
  });
}

test('Fictional previews never create tile images, including after zoom and refresh', () => {
  const f = fixture();
  for (const action of ['map-in', 'map-out', 'map-fit']) { f.click(action); f.seek(80); }
  f.update({});
  assert.equal(f.createdImages.length, 0);
  assert.equal(f.nodes['map-tiles'].children.length, 0);
  assert.equal(f.nodes['map-attribution'].hidden, true);
  assert.match(f.nodes['map-caption'].textContent, /FICTIONAL COURSE/);
  f.html.__burroCleanup();
});

test('Recorded mode creates only current-view HTTPS OSM tiles and reuses an unchanged view', () => {
  const f = fixture({synthetic: 0});
  assertVisibleTiles(f);
  assert.equal(f.nodes['map-attribution'].hidden, false);
  assert(f.root.classList.contains('real-map'));
  const created = f.createdImages.length;
  f.seek(80); f.update({});
  assert.equal(f.createdImages.length, created, 'Replay and unchanged query results must not recreate tile requests');
  assertFiniteProjection(f);
  f.html.__burroCleanup();
});

test('Zoom controls keep projections finite and Fit restores the original route', () => {
  const f = fixture({synthetic: 0});
  f.seek(80);
  const fittedRoute = f.nodes['route-base'].attributes.d;
  const fittedMarker = f.nodes['runner-marker'].attributes.transform;
  f.click('map-in'); assertVisibleTiles(f); assertFiniteProjection(f);
  assert.notEqual(f.nodes['route-base'].attributes.d, fittedRoute);
  f.click('map-out'); assertVisibleTiles(f); assertFiniteProjection(f);
  assert.equal(f.nodes['route-base'].attributes.d, fittedRoute);
  f.click('map-out'); assertVisibleTiles(f); assertFiniteProjection(f);
  assert.notEqual(f.nodes['route-base'].attributes.d, fittedRoute);
  f.click('map-fit'); assertVisibleTiles(f); assertFiniteProjection(f);
  assert.equal(f.nodes['route-base'].attributes.d, fittedRoute);
  assert.equal(f.nodes['runner-marker'].attributes.transform, fittedMarker);
  f.html.__burroCleanup();
});

test('User device label and different nested exported metadata retain separate provenance', () => {
  const exported = {model: 'Fixture exported model', hardware: 'FixtureHardware1,1', software: '1.2.3'};
  const f = fixture({raceChanges: {
    device_label: 'Fixture user-described device',
    device_metadata_json: JSON.stringify({exported_device: exported, user_label: 'Fixture user-described device'}),
    energy_kcal: 123.4,
  }});
  assert.equal(f.nodes['device-model'].textContent, 'Fixture user-described device');
  assert.equal(f.nodes['device-exported'].textContent, exported.model);
  assert.equal(f.nodes['device-hardware'].textContent, exported.hardware);
  assert.equal(f.nodes['device-software'].textContent, exported.software);
  assert.match(f.nodes['device-provenance'].textContent, /supplied by you.*separately/);
  assert.equal(f.nodes['device-source'].textContent, 'Fixture Watch');
  assert.equal(f.nodes['device-energy'].textContent, '123 kcal');
  assert.equal(JSON.parse(f.tables.A[0].device_metadata_json).exported_device.model, exported.model);
  f.html.__burroCleanup();
});

test('Query errors and missing workout rows pause playback without erasing the last trace', () => {
  const f = fixture();
  f.seek(80); f.click('play');
  const route = f.nodes['route-base'].attributes.d;
  f.html.__burroUpdate({state: 'Error', error: {message: 'Fictional database error'}});
  assert(f.root.classList.contains('paused'));
  assert.equal(f.nodes.play.attributes['aria-pressed'], 'false');
  assert.match(f.nodes['data-notice'].textContent, /query failed/);
  assert.equal(f.nodes['route-base'].attributes.d, route);
  f.html.__burroUpdate({state: 'Done', series: []});
  assert.match(f.nodes['data-notice'].textContent, /^No workout row/);
  assert(f.root.classList.contains('paused'));
  f.html.__burroCleanup();
});

test('Reduced motion starts paused and background tabs suspend the replay clock', () => {
  const f = fixture();
  assert(f.root.classList.contains('paused'));
  f.click('play');
  f.document.hidden = true;
  f.step(1000);
  assert.equal(f.nodes['replay-clock'].textContent, '0:00');
  f.document.hidden = false;
  f.step(1250);
  assert.equal(f.nodes['replay-clock'].textContent, '0:15');
  f.media.emit('change', {matches: true});
  assert(f.root.classList.contains('paused'));
  f.html.__burroCleanup();
});

test('Failed map tiles retain the GPS trace and unmount cleans replay callbacks', () => {
  const f = fixture({synthetic: 0});
  const route = f.nodes['route-base'].attributes.d, tile = f.nodes['map-tiles'].children[0];
  tile.emit('error');
  assert.match(f.nodes['route-kind'].textContent, /unavailable.*GPS trace retained/);
  assert.equal(f.nodes['route-base'].attributes.d, route);
  const update = f.html.__burroUpdate;
  f.html.emit('panelwillunmount');
  assert.equal(f.html.__burroCleanup, undefined);
  assert.equal(f.html.__burroUpdate, undefined);
  assert.equal(f.queuedFrames.size, 0);
  assert.equal(f.html.listeners.size, 0);
  assert.equal(f.media.listeners.size, 0);
  for (const id of ['play', 'restart', 'seek', 'speed', 'map-in', 'map-out', 'map-fit']) {
    assert.equal(f.nodes[id].listeners.size, 0, `${id} listeners must be removed on unmount`);
  }
  const before = f.nodes['data-notice'].textContent;
  update({state: 'Done', series: []});
  assert.equal(f.nodes['data-notice'].textContent, before, 'Disposed callbacks must not mutate the panel');
});

console.log(`${count} replay behavior tests passed; fictional fixtures, fake DOM, no network requests.`);

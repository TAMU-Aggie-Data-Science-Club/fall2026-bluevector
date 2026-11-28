"""Standalone deck.gl page for the Blue Vector globe.

Why this exists
---------------
The map used to come from ``pydeck.Deck.to_html()``. Two things broke there:

1. pydeck 0.9.3's bundled frontend (deck.gl 9.3) has no ``GlobeView`` class
   registered, so ``View(type="GlobeView")`` was silently dropped and the page
   fell back to a flat MapView whose mapbox-gl canvas stayed at its 400x300
   default inside the Streamlit iframe.
2. The overlay was an f-string, and an f-string turns ``\\'`` into ``'``, so the
   generated popup buttons contained ``bvRemoveEdge('' + id + '')``. That is a
   JavaScript syntax error, which killed the whole overlay script: no KPIs, no
   click handling, no iframe resize.

This module emits one self-contained page instead: deck.gl 9.3 from jsDelivr
with ``GlobeView``, land outlines from Natural Earth land-110m via TopoJSON,
and the same panels/copy as before. Placeholders use ``@@NAME@@`` tokens and
plain ``str.replace`` so nothing in the JS or CSS has to be escaped.
"""

GLOBE_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blue Vector</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap');

html, body {
  width: 100% !important; height: 100% !important;
  max-width: 100% !important; overflow: hidden !important;
  margin: 0; background: #020817 !important;
}
* { box-sizing: border-box; font-family: 'Inter', system-ui, sans-serif; }
#map { position: absolute; inset: 0; width: 100%; height: 100%; }

/* ── Panel base ─────────────────────────────────────────────────────────── */
.bv-panel {
  position: fixed;
  background: rgba(2, 8, 23, 0.86);
  -webkit-backdrop-filter: blur(20px) saturate(1.4);
  backdrop-filter: blur(20px) saturate(1.4);
  border: 1px solid rgba(56, 189, 248, 0.15);
  border-radius: 16px;
  color: #e2e8f0;
  padding: 15px 14px;
  z-index: 999;
  box-shadow: 0 8px 40px rgba(0,0,0,0.6);
}
.bv-panel h2 {
  font-size: 10px; font-weight: 900; letter-spacing: .12em;
  text-transform: uppercase; color: #38bdf8; margin: 0 0 13px;
  display: flex; align-items: center; gap: 7px;
}
.bv-panel h3 {
  font-size: 9px; font-weight: 800; letter-spacing: .1em;
  text-transform: uppercase; color: #334155; margin: 13px 0 7px;
}

/* ── Top bar ─────────────────────────────────────────────────────────────── */
#bv-topbar {
  top: 14px; left: 50%; transform: translateX(-50%);
  display: flex; align-items: center; gap: 10px;
  padding: 10px 20px; border-radius: 40px; white-space: nowrap;
  max-width: calc(100vw - 28px); overflow: hidden;
}
.bv-brand { font-size: 15px; font-weight: 900; letter-spacing: .06em; color: #f8fafc; }
.bv-brand em { color: #38bdf8; font-style: normal; }
.bv-sep { width: 1px; height: 18px; background: rgba(56,189,248,.2); flex-shrink: 0; }
.bv-badge {
  font-size: 10px; font-weight: 800; letter-spacing: .04em;
  border-radius: 20px; padding: 3px 11px; white-space: nowrap;
}
.bv-badge-blue   { background:rgba(56,189,248,.12); border:1px solid rgba(56,189,248,.25); color:#38bdf8; }
.bv-badge-purple { background:rgba(167,139,250,.12); border:1px solid rgba(167,139,250,.25); color:#a78bfa; }
.bv-hz-btn {
  padding: 5px 13px; border-radius: 20px;
  border: 1px solid rgba(239,68,68,.35); background: rgba(239,68,68,.07);
  color: #ef4444; font-size: 10px; font-weight: 800;
  cursor: pointer; transition: all .15s; letter-spacing: .04em; font-family: inherit;
}
.bv-hz-btn.active { background:rgba(239,68,68,.22); border-color:rgba(239,68,68,.6); }
.bv-hz-btn:disabled { border-color: rgba(100,116,139,.25); background: rgba(100,116,139,.06);
  color: #475569; cursor: not-allowed; }
.bv-scn-btn {
  padding: 5px 13px; border-radius: 20px;
  border: 1px solid rgba(56,189,248,.35); background: rgba(56,189,248,.07);
  color: #38bdf8; font-size: 10px; font-weight: 800;
  cursor: pointer; transition: all .15s; letter-spacing: .04em; font-family: inherit;
}
.bv-scn-btn.active { background:rgba(56,189,248,.22); border-color:rgba(56,189,248,.6); }

/* ── Left panel ───────────────────────────────────────────────────────────── */
#bv-left {
  top: 70px; left: 14px; width: 236px;
  max-height: calc(100vh - 90px); overflow-y: auto; overflow-x: hidden;
}
#bv-left::-webkit-scrollbar { width: 3px; }
#bv-left::-webkit-scrollbar-thumb { background:rgba(56,189,248,.3); border-radius:2px; }

/* ── Right panel ──────────────────────────────────────────────────────────── */
#bv-right {
  top: 70px; right: 14px; width: 220px;
  max-height: calc(100vh - 90px); overflow-y: auto; overflow-x: hidden;
}
#bv-right::-webkit-scrollbar { width: 3px; }
#bv-right::-webkit-scrollbar-thumb { background:rgba(56,189,248,.3); border-radius:2px; }

/* ── Model toggle ─────────────────────────────────────────────────────────── */
.bv-toggle {
  display: flex; border: 1px solid rgba(56,189,248,.18);
  border-radius: 9px; overflow: hidden; margin-bottom: 6px;
  background: rgba(0,0,0,.3);
}
.bv-toggle button {
  flex: 1; padding: 7px 4px; font-size: 10px; font-weight: 800;
  letter-spacing: .04em; border: none; background: transparent;
  color: #334155; cursor: pointer; transition: all .15s; font-family: inherit;
}
.bv-toggle button.active     { background:rgba(56,189,248,.18); color:#38bdf8; }
.bv-toggle button.active-gnn { background:rgba(167,139,250,.18); color:#a78bfa; }
.bv-toggle button:disabled { cursor: not-allowed; color: #1e293b; }
.bv-note {
  font-size: 9px; line-height: 1.5; color: #f59e0b;
  background: rgba(245,158,11,.07); border: 1px solid rgba(245,158,11,.2);
  border-radius: 7px; padding: 7px 8px; margin-bottom: 4px;
}

/* ── KPI cards ────────────────────────────────────────────────────────────── */
.kpi {
  background: rgba(56,189,248,.04); border: 1px solid rgba(56,189,248,.1);
  border-radius: 10px; padding: 9px 11px; margin-bottom: 7px;
}
.kpi-label { font-size:9px; font-weight:700; color:#475569; letter-spacing:.08em; text-transform:uppercase; }
.kpi-value { font-size:20px; font-weight:900; color:#f1f5f9; margin-top:2px; line-height:1; }
.kpi-sub   { font-size:10px; color:#64748b; margin-top:2px; }
.kpi-warn  { color:#f97316 !important; }
.kpi-crit  { color:#ef4444 !important; }
.brent-box {
  background:rgba(250,204,21,.06); border:1px solid rgba(250,204,21,.18);
  border-radius:10px; padding:10px 11px; margin-top:6px;
}
.brent-label { font-size:9px; font-weight:700; color:#64748b; letter-spacing:.08em; text-transform:uppercase; }
.brent-value { font-size:19px; font-weight:900; color:#facc15; margin-top:2px; }
.brent-ci    { font-size:10px; color:#64748b; margin-top:2px; }

/* ── Popup ────────────────────────────────────────────────────────────────── */
#bv-popup {
  position: fixed; display: none;
  background: rgba(2,8,23,.97); -webkit-backdrop-filter: blur(24px); backdrop-filter: blur(24px);
  border: 1px solid rgba(56,189,248,.3); border-radius: 14px;
  color: #e2e8f0; padding: 16px 17px;
  min-width: 220px; max-width: 280px; z-index: 1000;
  box-shadow: 0 20px 56px rgba(0,0,0,.7);
}
#bv-popup h4 {
  font-size: 13px; font-weight: 800; color: #38bdf8;
  margin: 0 0 11px; padding-bottom: 10px;
  border-bottom: 1px solid rgba(56,189,248,.14);
}
.pop-row  { display:flex; justify-content:space-between; align-items:center; margin:5px 0; font-size:11px; }
.pop-label { color:#64748b; }
.pop-val  { color:#f1f5f9; font-weight:700; }
.pop-badge {
  display:inline-block; font-size:9px; font-weight:800; letter-spacing:.06em;
  padding:2px 9px; border-radius:20px; margin-bottom:9px;
}
.pb-choke { background:rgba(239,68,68,.18);   color:#ef4444; border:1px solid rgba(239,68,68,.3); }
.pb-lane  { background:rgba(56,189,248,.18);  color:#38bdf8; border:1px solid rgba(56,189,248,.3); }
.pb-conn  { background:rgba(100,116,139,.18); color:#94a3b8; border:1px solid rgba(100,116,139,.3); }
.pb-node  { background:rgba(45,212,191,.18);  color:#2dd4bf; border:1px solid rgba(45,212,191,.3); }
.bv-thr { margin-top:13px; padding-top:11px; border-top:1px solid rgba(56,189,248,.14); }
.bv-thr-head {
  display:flex; justify-content:space-between; align-items:center;
  font-size:9px; font-weight:800; color:#64748b; letter-spacing:.08em;
  text-transform:uppercase; margin-bottom:8px;
}
.bv-thr-head span:last-child { color:#38bdf8; font-size:12px; font-weight:800; letter-spacing:0; }
.bv-thr input[type="range"] {
  -webkit-appearance:none; appearance:none; width:100%; height:3px;
  border-radius:2px; background:rgba(56,189,248,.25); outline:none; cursor:pointer;
}
.bv-thr input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance:none; appearance:none; width:13px; height:13px;
  border-radius:50%; background:#38bdf8; border:none;
  box-shadow:0 0 0 3px rgba(56,189,248,.18); cursor:pointer;
}
.bv-thr input[type="range"]::-moz-range-thumb {
  width:13px; height:13px; border-radius:50%; background:#38bdf8; border:none;
  box-shadow:0 0 0 3px rgba(56,189,248,.18); cursor:pointer;
}
.bv-thr input[type="range"]:disabled { opacity:.35; cursor:not-allowed; }
.bv-thr-hint { font-size:9px; line-height:1.5; color:#334155; margin-top:7px; }
.bv-alt {
  margin-top:9px; padding-top:9px; border-top:1px solid rgba(56,189,248,.14);
  font-size:9px; color:#475569; line-height:1.7;
}
.bv-alt button {
  margin:3px 4px 0 0; padding:2px 8px; border-radius:20px;
  border:1px solid rgba(56,189,248,.3); background:rgba(56,189,248,.1);
  color:#38bdf8; font-size:9px; font-weight:800; cursor:pointer; font-family:inherit;
}
.bv-alt button:hover { background:rgba(56,189,248,.22); }
.pop-close {
  position:absolute; top:12px; right:14px; background:none; border:none;
  color:#475569; font-size:16px; cursor:pointer; line-height:1; font-family:inherit;
}
.pop-close:hover { color:#e2e8f0; }

/* ── Hormuz banner ────────────────────────────────────────────────────────── */
#bv-hz-banner {
  display:none; position:fixed;
  bottom:70px; left:50%; transform:translateX(-50%);
  background:rgba(239,68,68,.14); border:1px solid rgba(239,68,68,.4);
  border-radius:10px; padding:9px 22px; color:#ef4444;
  font-size:11px; font-weight:800; letter-spacing:.05em; z-index:999;
  align-items:center; gap:8px; white-space:nowrap;
  -webkit-backdrop-filter:blur(12px); backdrop-filter:blur(12px);
}

/* ── Hormuz crisis panel ──────────────────────────────────────────────────── */
#bv-hz-panel {
  display:none; position:fixed;
  bottom:100px; right:14px; width:220px;
  background:rgba(2,8,23,.9); -webkit-backdrop-filter:blur(20px); backdrop-filter:blur(20px);
  border:1px solid rgba(239,68,68,.3); border-radius:14px;
  padding:13px 14px; z-index:999; box-shadow:0 8px 32px rgba(0,0,0,.5);
}
#bv-hz-panel h4 {
  font-size:10px; font-weight:900; letter-spacing:.1em;
  text-transform:uppercase; color:#ef4444; margin:0 0 10px;
}
.hz-row  { display:flex; align-items:center; gap:4px; margin-bottom:5px; font-size:10px; }
.hz-week { color:#475569; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.hz-val  { color:#f1f5f9; font-weight:700; }
.hz-val small { color:#64748b; font-weight:400; }
.hz-pred { color:#a78bfa !important; }

/* ── Legend ───────────────────────────────────────────────────────────────── */
#bv-legend {
  position:fixed; bottom:14px; left:14px;
  background:rgba(2,8,23,.86); -webkit-backdrop-filter:blur(18px); backdrop-filter:blur(18px);
  border:1px solid rgba(56,189,248,.14); border-radius:13px;
  padding:12px 15px; z-index:999;
}
#bv-legend h3 {
  font-size:9px; font-weight:800; color:#334155;
  letter-spacing:.1em; text-transform:uppercase; margin:0 0 8px;
}

/* ── Removed list ─────────────────────────────────────────────────────────── */
#bv-removed-list { min-height:16px; }
.rm-item {
  display:flex; justify-content:space-between; align-items:center;
  margin-bottom:4px; font-size:11px;
}
.rm-item span { color:#ef4444; }
.rm-item button {
  background:none; border:none; color:#334155; cursor:pointer;
  font-size:12px; line-height:1; padding:0 2px; font-family:inherit;
}
.rm-item button:hover { color:#e2e8f0; }
#bv-clear-btn {
  margin-top:7px; width:100%; padding:6px; border-radius:8px;
  border:1px solid rgba(100,116,139,.3); background:rgba(100,116,139,.1);
  color:#64748b; font-size:10px; font-weight:700; cursor:pointer; font-family:inherit;
}
.bv-hint { font-size:10px; color:#334155; line-height:1.7; }
.bv-hint b { color:#38bdf8; font-weight:700; }
.bv-hint b.nd { color:#2dd4bf; }

/* ── Narrow viewports (laptops in a split window, phones) ─────────────────── */
/* Four scenario pills plus the brand need more room than the original two, so
   the brand gives way first and the pills tighten — clipping the Hormuz or a
   scenario control would leave a mode unreachable. */
@media (max-width: 1200px) {
  .bv-brand, .bv-sep { display: none; }
  #bv-topbar { padding: 8px 12px; gap: 7px; }
  .bv-scn-btn, .bv-hz-btn { font-size: 9px; padding: 4px 10px; }
}
@media (max-width: 900px) {
  .bv-brand, .bv-sep { display: none; }
  #bv-topbar { padding: 8px 12px; gap: 7px; }
  .bv-badge { font-size: 9px; padding: 2px 8px; }
  #bv-left  { width: 196px; top: 60px; padding: 12px 12px; }
  #bv-right { width: 186px; top: 60px; padding: 12px 12px; }
  .kpi-value { font-size: 17px; }
  #bv-legend { display: none; }
}
@media (max-width: 700px) {
  #bv-topbar { padding: 6px 8px; gap: 5px; }
  .bv-scn-btn, .bv-hz-btn { font-size: 8px; padding: 3px 7px; letter-spacing: 0; }
}
</style>
</head>
<body>

<div id="map"></div>

<!-- Top bar -->
<div class="bv-panel" id="bv-topbar">
  <div class="bv-brand"><em>BLUE</em>VECTOR&nbsp;🌐</div>
  <div class="bv-sep"></div>
  <button class="bv-scn-btn @@RED_SEA_CLS@@" id="bv-redsea-btn" onclick="bvToggleScenario('red_sea_2024')"
          title="Calibrated 2023–24 Red Sea / Houthi crisis scenario">2023–24 Red Sea Crisis</button>
  <button class="bv-scn-btn @@PANAMA_CLS@@" id="bv-panama-btn" onclick="bvToggleScenario('panama_drought_2023')"
          title="Calibrated 2023–24 Panama Canal drought scenario">2023–24 Panama Drought</button>
  <button class="bv-scn-btn @@EVER_GIVEN_CLS@@" id="bv-evergiven-btn" onclick="bvToggleScenario('ever_given_2021')"
          title="Calibrated 2021 Ever Given Suez blockage scenario">2021 Ever Given Blockage</button>
  <button class="bv-hz-btn @@HZ_CLS@@" id="bv-hz-btn" onclick="bvToggleHormuz()" @@HZ_DISABLED@@>🛢&nbsp;Hormuz Crisis 2026</button>
</div>

<!-- Left panel -->
<div class="bv-panel" id="bv-left">
  <h2>⚙&nbsp; Model Controls</h2>
  <div class="bv-toggle">
    <button id="bv-btn-flow" class="@@FLOW_CLS@@" onclick="bvSetModel('flow')">Flow Model</button>
    <button id="bv-btn-gnn"  class="@@GNN_CLS@@"  onclick="bvSetModel('gnn')" @@GNN_DISABLED@@>GNN</button>
  </div>
  @@GNN_NOTE@@
  <h3>Congestion Scale</h3>
  @@LEGEND@@
  <h3>Removed Edges</h3>
  <div id="bv-removed-list"></div>
  <button id="bv-clear-btn" onclick="bvClearRemoved()" style="display:none">↺ Restore All</button>
  <h3>Interaction</h3>
  <div class="bv-hint">
    <b>Click edge</b> → inspect &amp; throttle<br>
    <b class="nd">Click node</b> → port attributes<br>
    Drag → rotate globe&nbsp;·&nbsp;Scroll → zoom
  </div>
</div>

<!-- Right panel -->
<div class="bv-panel" id="bv-right">
  <h2>📊&nbsp; Live Metrics</h2>
  <div id="bv-kpis"></div>
  <div id="bv-brent"></div>
</div>

<!-- Pop-up -->
<div id="bv-popup">
  <button class="pop-close" onclick="bvClosePopup()">✕</button>
  <div id="bv-popup-content"></div>
</div>

<!-- Hormuz banner -->
<div id="bv-hz-banner">🚨&nbsp; HORMUZ CRISIS MODE ACTIVE — 2026 Transfer Analysis</div>

<!-- Hormuz panel -->
<div id="bv-hz-panel">
  <h4>🛢 Hormuz Weekly Predictions</h4>
  @@HZ_ROWS@@
</div>

<!-- Legend -->
<div id="bv-legend">
  <h3>Edge Utilisation</h3>
  @@LEGEND@@
</div>

<script src="https://cdn.jsdelivr.net/npm/topojson-client@3/dist/topojson-client.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/deck.gl@9.3.12/dist.min.js"></script>
<script>
// ── State ────────────────────────────────────────────────────────────────────
var PATHS     = @@PATHS@@;
var PORTS     = @@PORTS@@;
var JUNCTIONS = @@JUNCTIONS@@;
var KPI       = @@KPI@@;
var BRENT     = @@BRENT@@;
var REMOVED   = @@REMOVED@@;
var MODEL     = "@@MODEL@@";
var SCENARIO  = "@@SCENARIO_KEY@@";
var THROTTLES = @@THROTTLES@@;
// Edges sharing the pixel last clicked, primary first (filled by bvPickAt).
var bvCandidates = [];
var bvPopupAt = { x: 0, y: 0 };
var HAS_GNN   = @@HAS_GNN@@;
var HAS_HZ    = @@HAS_HZ@@;
var HZ_ON     = @@HZ_ON@@;

function bvSetEl(id, html) {
  var el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

// ── Iframe self-resize ───────────────────────────────────────────────────────
// Streamlit gives the component iframe a fixed pixel height. This expands it to
// the parent viewport so the globe is genuinely full-bleed.
(function bvResize() {
  function apply() {
    try {
      var h = window.parent.innerHeight;
      if (!h || h < 200) return;
      var frame = window.frameElement;
      if (!frame) {
        var frames = window.parent.document.querySelectorAll('iframe');
        for (var i = 0; i < frames.length; i++) {
          try { if (frames[i].contentWindow === window) { frame = frames[i]; break; } } catch (e) {}
        }
      }
      if (!frame) return;
      // Trim any residual Streamlit chrome above the frame, not just innerHeight.
      var top = 0;
      try { top = Math.round(frame.getBoundingClientRect().top); } catch (e) {}
      frame.style.height  = Math.max(h - top, 320) + 'px';
      frame.style.width   = '100%';
      frame.style.display = 'block';
      frame.style.marginTop = '0';
    } catch (e) {}
  }
  apply();
  try { window.parent.addEventListener('resize', apply); } catch (e) {}
  window.addEventListener('resize', apply);
  setTimeout(apply, 300);
  setTimeout(apply, 1200);
})();

// ── Layers ───────────────────────────────────────────────────────────────────
// Layer instances must be created afresh for every Deck. deck.gl layer objects
// are single-use: `bvBuildDeck()` finalizes the previous Deck, which tears down
// each layer's state, and handing the same instances to the next Deck makes
// them fail to initialize ("deck: initialization of PathLayer(...): assertion
// failed") — the globe then draws with no edges and no nodes at all.
function bvMakeLayers() {
  var layers = [];

  if (LAND) {
    layers.push(new deck.GeoJsonLayer({
      id: 'land',
      data: LAND,
      filled: true,
      stroked: true,
      getFillColor: [10, 30, 58, 255],
      getLineColor: [36, 64, 100, 255],
      lineWidthMinPixels: 1,
      pickable: false
    }));
  }

  layers.push(new deck.PathLayer({
    id: 'edges',
    data: PATHS,
    getPath: function (d) { return d.path; },
    getColor: function (d) { return d.color; },
    getWidth: function (d) { return d.width; },
    widthUnits: 'pixels',
    widthMinPixels: 1,
    widthMaxPixels: 14,
    capRounded: true,
    jointRounded: true,
    pickable: true,
    autoHighlight: true,
    highlightColor: [255, 255, 255, 60]
  }));

  layers.push(new deck.ScatterplotLayer({
    id: 'ports',
    data: PORTS,
    getPosition: function (d) { return [d.lon, d.lat]; },
    getFillColor: [45, 212, 191, 220],
    radiusUnits: 'pixels',
    getRadius: 5,
    radiusMinPixels: 4,
    radiusMaxPixels: 11,
    pickable: true,
    autoHighlight: true,
    highlightColor: [45, 212, 191, 80]
  }));

  layers.push(new deck.ScatterplotLayer({
    id: 'junctions',
    data: JUNCTIONS,
    getPosition: function (d) { return [d.lon, d.lat]; },
    getFillColor: [250, 204, 21, 190],
    radiusUnits: 'pixels',
    getRadius: 3.5,
    radiusMinPixels: 3,
    radiusMaxPixels: 8,
    pickable: true,
    autoHighlight: true,
    highlightColor: [250, 204, 21, 60]
  }));

  return layers;
}

// deck.gl 9.3 exports the globe view as the experimental `_GlobeView` in the
// UMD bundle; fall back to `GlobeView` if a later build promotes it.
var GlobeViewClass = deck.GlobeView || deck._GlobeView;

// GlobeView has no fitBounds, and its zoom is not Web-Mercator zoom: measured on
// this build the globe's on-screen radius is about 76 * 2^zoom pixels. Pick the
// zoom that makes the globe span ~70% of the shorter side of the viewport.
var mapEl = document.getElementById('map');
var LAND = null;
var deckgl = null;

function bvSize() {
  return [
    mapEl.clientWidth  || window.innerWidth  || 800,
    mapEl.clientHeight || window.innerHeight || 600
  ];
}

function bvFitZoom() {
  var size = bvSize();
  var target = 0.35 * Math.min(size[0], size[1]);
  var z = Math.log2(Math.max(target, 40) / 76);
  return Math.max(0.1, Math.min(3.5, Math.round(z * 20) / 20));
}

// Deck reads its parent's size once, at construction, and inside a Streamlit
// iframe that parent can still be 0-sized at that moment (leaving a 300x150
// canvas). The size is therefore passed explicitly and the whole Deck is
// rebuilt when the viewport changes. Rebuilding — instead of mutating a live
// Deck with setProps — keeps the layer manager and the picking framebuffer in
// step with the canvas that is actually on screen.
function bvBuildDeck() {
  if (deckgl) {
    try { deckgl.finalize(); } catch (e) {}
    deckgl = null;
  }
  while (mapEl.firstChild) mapEl.removeChild(mapEl.firstChild);
  var size = bvSize();
  deckgl = new deck.Deck({
    parent: mapEl,
    width: size[0],
    height: size[1],
    views: [new GlobeViewClass({ controller: true })],
    initialViewState: { longitude: 48, latitude: 18, zoom: bvFitZoom() },
    controller: { dragRotate: true, touchRotate: true },
    layers: bvMakeLayers(),
    parameters: { clearColor: [0.008, 0.031, 0.09, 1] }
  });
}

bvBuildDeck();

// ── Hit testing ──────────────────────────────────────────────────────────────
// deck.gl 9.3's picking framebuffer is not dependable from this page (it returns
// nothing even while the globe is drawn), so clicks are resolved here instead:
// project the geometry with the live viewport and take the nearest thing under
// the cursor. It is deterministic, and it only ever hits the near hemisphere.
function bvAngleDeg(lon1, lat1, lon2, lat2) {
  var rad = Math.PI / 180;
  var dLat = (lat2 - lat1) * rad;
  var dLon = (lon2 - lon1) * rad;
  var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
          Math.cos(lat1 * rad) * Math.cos(lat2 * rad) *
          Math.sin(dLon / 2) * Math.sin(dLon / 2);
  return 2 * Math.asin(Math.min(1, Math.sqrt(a))) / rad;
}

function bvFrontFacing(lon, lat) {
  var vs = deckgl && deckgl.viewState;
  if (!vs || vs.longitude === undefined) return true;
  // A point is on the far side of the globe once it is 90 degrees from the
  // point the camera looks at; keep a small margin for the limb.
  return bvAngleDeg(lon, lat, vs.longitude, vs.latitude) < 88;
}

function bvPointSegmentDistance(px, py, ax, ay, bx, by) {
  var dx = bx - ax, dy = by - ay;
  var len2 = dx * dx + dy * dy;
  var t = len2 > 0 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
  t = Math.max(0, Math.min(1, t));
  var cx = ax + t * dx, cy = ay + t * dy;
  return Math.sqrt((px - cx) * (px - cx) + (py - cy) * (py - cy));
}

function bvPickAt(px, py) {
  if (!deckgl) return null;
  var vp = deckgl.viewManager.getViewports()[0];
  if (!vp) return null;
  var bestNode = null;

  [PORTS, JUNCTIONS].forEach(function (list) {
    list.forEach(function (n) {
      if (!bvFrontFacing(n.lon, n.lat)) return;
      var p = vp.project([n.lon, n.lat]);
      var d = Math.hypot(p[0] - px, p[1] - py);
      if (d <= 9 && (!bestNode || d < bestNode.d)) bestNode = {d: d, obj: n};
    });
  });

  // Several graph edges can follow one polyline over open water — around the
  // Cape of Good Hope the chokepoint E007 and several 30-point lanes share the
  // same route — so their strokes hit at exactly the same distance. Comparing
  // with a strict < meant the first row in PATHS always won and the others could
  // never be opened. Every edge whose stroke covers the pixel is now kept as a
  // candidate, resolved by: genuinely closer, then the thicker stroke (it is the
  // one covering the pixel), then the stroke drawn last. The rest stay reachable
  // from the popup, so no coincident edge is unclickable.
  var hits = [];
  PATHS.forEach(function (row, idx) {
    var pts = row.path;
    var threshold = Math.max(5, row.width * 0.6 + 4);
    var prev = null, prevVisible = false, best = Infinity;
    for (var i = 0; i < pts.length; i++) {
      var visible = bvFrontFacing(pts[i][0], pts[i][1]);
      var p = visible ? vp.project(pts[i]) : null;
      if (prev && prevVisible && visible) {
        var d = bvPointSegmentDistance(px, py, prev[0], prev[1], p[0], p[1]);
        if (d <= threshold && d < best) best = d;
      }
      prev = p;
      prevVisible = visible;
    }
    if (best < Infinity) hits.push({ d: best, row: row, i: idx });
  });

  bvCandidates = [];
  var bestEdge = null, bestEdgeD = Infinity;
  if (hits.length) {
    // Connectors are the port-to-junction plumbing: thin 0.8 px lines, and in
    // this graph many are hundreds of nautical miles long (the median is 512 nm,
    // the longest 1,479 nm), so they lie across whole seas and often pass closer
    // to the cursor than the corridor the user is aiming at. A click must land on
    // the lane or chokepoint; a connector is only picked when nothing else is
    // under the cursor.
    var pool = hits.filter(function (h) { return h.row.edge_type !== 'connector'; });
    if (!pool.length) pool = hits;
    var closest = pool[0].d;
    pool.forEach(function (h) { if (h.d < closest) closest = h.d; });
    var near = pool.filter(function (h) { return h.d <= closest + 1.0; });
    near.sort(function (a, b) {
      if (Math.abs(a.d - b.d) > 0.05) return a.d - b.d;
      if (Math.abs(a.row.width - b.row.width) > 0.05) return b.row.width - a.row.width;
      return b.i - a.i;
    });
    bvCandidates = near.map(function (h) { return h.row; });
    bestEdge = near[0].row;
    bestEdgeD = near[0].d;
  }

  // A marker sitting on a corridor (Fujairah on Hormuz, for example) should not
  // swallow clicks on the corridor itself, so a node wins only when it is
  // clearly closer to the cursor than the nearest edge.
  if (bestNode && (!bestEdge || bestNode.d + 2 < bestEdgeD)) return bestNode.obj;
  if (bestEdge) return bestEdge;
  return bestNode ? bestNode.obj : null;
}

(function bvWireClicks() {
  function handle(e) {
    var rect = mapEl.getBoundingClientRect();
    var x = e.clientX - rect.left;
    var y = e.clientY - rect.top;
    var obj = bvPickAt(x, y);
    if (!obj) { bvClosePopup(); return; }
    if (obj.edge_id) {
      bvShowEdgePopup(obj, x, y);
    } else {
      bvShowNodePopup(obj, x, y);
    }
  }
  mapEl.addEventListener('click', handle, true);
})();

// Rebuild on window resize so the globe stays fitted (debounced).
(function bvAutoFit() {
  var timer = null;
  window.addEventListener('resize', function () {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { bvBuildDeck(); }, 250);
  });
})();

// The iframe is stretched after load, so re-measure once layout has settled.
setTimeout(function () {
  var size = bvSize();
  if (deckgl && (deckgl.width !== size[0] || deckgl.height !== size[1])) bvBuildDeck();
}, 400);

// Land outlines: Natural Earth 110m, converted from TopoJSON client-side.
// A basemap is deliberately not used — deck.gl's GlobeView does not take a
// MapLibre/Mapbox basemap, and the earlier attempt left a 400x300 stranded
// mapbox-gl canvas behind the globe.
(function bvLoadLand() {
  if (typeof topojson === 'undefined' || !window.fetch) return;
  fetch('https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json')
    .then(function (r) { return r.json(); })
    .then(function (topo) {
      LAND = topojson.feature(topo, topo.objects.land);
      bvBuildDeck();
    })
    .catch(function () {});
})();

// ── KPI rendering ────────────────────────────────────────────────────────────
function bvRenderKPIs() {
  var warn = function (v) { return v > 3 ? 'kpi-crit' : v > 1 ? 'kpi-warn' : ''; };
  bvSetEl('bv-kpis',
    '<div class="kpi"><div class="kpi-label">Chokepoint Crossings</div>' +
    '<div class="kpi-value">' + KPI.cp_transits.toLocaleString() + '</div>' +
    '<div class="kpi-sub">tanker transits / week</div></div>' +

    '<div class="kpi"><div class="kpi-label">Rerouted Flow</div>' +
    '<div class="kpi-value">' + KPI.rerouted_mbd + '</div>' +
    '<div class="kpi-sub">mb/d added corridor traffic</div></div>' +

    '<div class="kpi"><div class="kpi-label">Extra Delay</div>' +
    '<div class="kpi-value ' + warn(KPI.extra_days) + '">' +
    (KPI.extra_days >= 0 ? '+' : '') +
    '<span style="font-size:14px">' + KPI.extra_days + '</span> d</div>' +
    '<div class="kpi-sub">Cape detour + BPR queue</div></div>' +

    '<div class="kpi"><div class="kpi-label">Suez / (Suez+Cape) Share</div>' +
    '<div class="kpi-value">' + KPI.suez_share_pct +
    '<span style="font-size:12px">%</span></div>' +
    '<div class="kpi-sub">Europe–Asia routing split</div></div>' +

    '<div class="kpi"><div class="kpi-label">Stranded Volume</div>' +
    '<div class="kpi-value ' + (KPI.stranded_mbd > 0.5 ? 'kpi-crit' : '') + '">' +
    KPI.stranded_mbd + '</div>' +
    '<div class="kpi-sub">mb/d unserved demand</div></div>');

  if (BRENT && BRENT.pred_brent_mean != null) {
    var mean = parseFloat(BRENT.pred_brent_mean).toFixed(1);
    var lo   = parseFloat(BRENT.pred_brent_lower_90).toFixed(1);
    var hi   = parseFloat(BRENT.pred_brent_upper_90).toFixed(1);
    var d    = BRENT.delta_brent_mean;
    var ds   = (d !== null && d !== undefined)
      ? (d >= 0 ? '+' : '') + parseFloat(d).toFixed(1) : null;
    bvSetEl('bv-brent', '<h3>Brent Signal</h3><div class="brent-box">' +
      '<div class="brent-label">Conditional Estimate</div>' +
      '<div class="brent-value">$' + mean + ' / bbl</div>' +
      '<div class="brent-ci">90% CI: $' + lo + ' – $' + hi + '</div>' +
      (ds ? '<div class="brent-ci" style="color:#f97316;margin-top:3px">Δ baseline: ' + ds + ' $/bbl</div>' : '') +
      '<div class="brent-ci" style="color:#334155;font-size:9px;margin-top:4px">Ridge regression · conditional assoc.</div>' +
      '</div>');
  } else {
    bvSetEl('bv-brent', '<h3>Brent Signal</h3>' +
      '<div class="bv-hint">No conditional estimate saved for this scenario.</div>');
  }
}

// ── Removed edge list ────────────────────────────────────────────────────────
function bvRenderRemoved() {
  var el  = document.getElementById('bv-removed-list');
  var btn = document.getElementById('bv-clear-btn');
  if (!el) return;
  if (!REMOVED.length) {
    el.innerHTML = '<span class="bv-hint" style="font-style:italic">None</span>';
    if (btn) btn.style.display = 'none';
  } else {
    el.innerHTML = REMOVED.map(function (e) {
      return '<div class="rm-item"><span>' + e + '</span>' +
             '<button onclick="bvRestoreEdge(\'' + e + '\')" title="Restore">✕</button></div>';
    }).join('');
    if (btn) btn.style.display = 'block';
  }
}

// ── Hormuz UI ────────────────────────────────────────────────────────────────
function bvApplyHormuzUI() {
  var btn = document.getElementById('bv-hz-btn');
  if (btn) btn.className = 'bv-hz-btn' + (HZ_ON ? ' active' : '');
  var banner = document.getElementById('bv-hz-banner');
  if (banner) banner.style.display = HZ_ON ? 'flex' : 'none';
  var panel = document.getElementById('bv-hz-panel');
  if (panel) panel.style.display = HZ_ON ? 'block' : 'none';
}

// ── Popup ────────────────────────────────────────────────────────────────────
function bvClosePopup() {
  var p = document.getElementById('bv-popup');
  if (p) p.style.display = 'none';
}

function bvShowAlt(i) {
  var row = bvCandidates[i];
  if (row) bvShowEdgePopup(row, bvPopupAt.x, bvPopupAt.y);
}

function bvShowEdgePopup(obj, px, py) {
  bvPopupAt = { x: px, y: py };
  var bc = obj.edge_type === 'chokepoint' ? 'pb-choke' :
           obj.edge_type === 'lane' ? 'pb-lane' : 'pb-conn';
  var uc = obj.util_pct > 95 ? '#ef4444' : obj.util_pct > 70 ? '#f97316' :
           obj.util_pct > 40 ? '#facc15' : '#38bdf8';
  var html =
    '<h4>' + obj.edge_id + '</h4>' +
    '<span class="pop-badge ' + bc + '">' + String(obj.edge_type || 'edge').toUpperCase() + '</span>' +
    (obj.passage ? ' <span class="pop-badge pb-lane" style="background:rgba(167,139,250,.15);color:#a78bfa;border-color:rgba(167,139,250,.3)">' + obj.passage + '</span>' : '') +
    '<div class="pop-row"><span class="pop-label">Utilization</span><span class="pop-val" style="color:' + uc + '">' + obj.util_pct + '%</span></div>' +
    '<div class="pop-row"><span class="pop-label">Transits / wk</span><span class="pop-val">' + obj.transits + '</span></div>' +
    '<div class="pop-row"><span class="pop-label">Corridor Delay</span><span class="pop-val">+' + obj.delay_days + ' d</span></div>' +
    '<div class="pop-row"><span class="pop-label">Volume</span><span class="pop-val">' + obj.volume_mbd + ' mb/d</span></div>' +
    '<div class="pop-row"><span class="pop-label">Length</span><span class="pop-val">' + (obj.length_nm || 0).toLocaleString() + ' nm</span></div>' +
    (obj.cost_per_bbl ? '<div class="pop-row"><span class="pop-label">Cost / bbl</span><span class="pop-val">$' + obj.cost_per_bbl + '</span></div>' : '') +
    (obj.anomaly_z ? '<div class="pop-row"><span class="pop-label">Anomaly Z</span><span class="pop-val">' + obj.anomaly_z + '</span></div>' : '') +
    '<div class="bv-thr">' +
      '<div class="bv-thr-head"><span>Edge throttle</span>' +
        '<span id="bv-thr-val">' + obj.throughput_pct + '%</span></div>' +
      // The slider sets the throughput fraction the flow solver routes on this
      // edge (1.0 = open, 0.0 = closed). `throughput_pct` is that same fraction,
      // so the control starts where the current solve left it.
      '<input type="range" id="bv-thr" min="0" max="100" step="5" value="' + obj.throughput_pct + '"' +
      (MODEL === 'gnn' ? ' disabled' : '') +
      ' oninput="bvThrottleInput(this.value)"' +
      ' onchange="bvSetEdgeThrottle(\'' + obj.edge_id + '\', this.value)">' +
      '<div class="bv-thr-hint">' +
        (MODEL === 'gnn'
          ? 'GNN predictions are fixed per scenario — switch to the flow model to throttle this edge.'
          : 'Share of pre-event throughput the model still carries here. 0% closes the edge and the crude re-routes.') +
      '</div>' +
    '</div>' +
    (bvCandidates.length > 1
      ? '<div class="bv-alt">Also at this point: ' +
        bvCandidates.map(function (c, i) {
          return c.edge_id === obj.edge_id ? '' :
            '<button onclick="bvShowAlt(' + i + ')">' + c.edge_id + '</button>';
        }).join('') + '</div>'
      : '');
  bvSetEl('bv-popup-content', html);
  bvPositionPopup(px, py);
}

function bvShowNodePopup(obj, px, py) {
  var html =
    '<h4>' + (obj.name || obj.node_id) + '</h4>' +
    '<span class="pop-badge pb-node">' + String(obj.node_type || 'node').toUpperCase() + '</span>' +
    '<div class="pop-row"><span class="pop-label">Node ID</span><span class="pop-val">' + obj.node_id + '</span></div>' +
    '<div class="pop-row"><span class="pop-label">Country</span><span class="pop-val">' + (obj.country || '—') + '</span></div>' +
    '<div class="pop-row"><span class="pop-label">Latitude</span><span class="pop-val">' + parseFloat(obj.lat).toFixed(2) + '°</span></div>' +
    '<div class="pop-row"><span class="pop-label">Longitude</span><span class="pop-val">' + parseFloat(obj.lon).toFixed(2) + '°</span></div>';
  bvSetEl('bv-popup-content', html);
  bvPositionPopup(px, py);
}

function bvPositionPopup(px, py) {
  var popup = document.getElementById('bv-popup');
  if (!popup) return;
  popup.style.display = 'block';
  requestAnimationFrame(function () {
    var pw = popup.offsetWidth  || 260;
    var ph = popup.offsetHeight || 220;
    var vw = window.innerWidth;
    var vh = window.innerHeight;
    var x = px + 16, y = py - 24;
    if (x + pw > vw - 10) x = px - pw - 16;
    if (y + ph > vh - 10) y = vh - ph - 10;
    if (y < 10) y = 10;
    if (x < 10) x = 10;
    popup.style.left = x + 'px';
    popup.style.top  = y + 'px';
  });
}

// ── Actions → Streamlit query params ─────────────────────────────────────────
// Every control in this page has to make the Streamlit app rerun, and the only
// channel that survives the embed is a query param on the app's own URL.
// Writing `window.parent.location` from here is silently dropped: `st.iframe`
// sandboxes the embed without `allow-top-navigation`, so the write is refused
// and the button appears dead. The embed is same-origin (`allow-same-origin`
// plus a `srcdoc` document), so the parent's DOM is reachable — the resize
// helper above already edits the frame element. Navigate by handing the URL to
// a script element that executes in the Streamlit page itself, where the
// iframe sandbox does not apply.
function bvNavigate(params, keepThrottles) {
  var url;
  try {
    url = new URL(window.parent.location.href);
  } catch (e) {
    return;
  }
  Object.keys(params).forEach(function (k) { url.searchParams.set(k, params[k]); });
  // A click reloads the app, which starts a fresh Streamlit session, so the
  // rest of the globe's state has to travel with the action or the controls
  // would undo each other (a Hormuz press would drop the GNN selection, a
  // model press would reset the scenario and the edge throttles).
  url.searchParams.set('bv_scenario', SCENARIO);
  url.searchParams.set('bv_model', MODEL);
  url.searchParams.set('bv_hormuz', HZ_ON ? 'on' : 'off');
  if (keepThrottles !== false) {
    var thr = Object.keys(THROTTLES).map(function (k) {
      return k + ':' + THROTTLES[k];
    });
    if (thr.length) url.searchParams.set('bv_throttles', thr.join(','));
  }
  var target = url.toString();

  try {
    var doc = window.parent.document;
    var script = doc.createElement('script');
    script.textContent = 'window.location.href = ' + JSON.stringify(target) + ';';
    (doc.body || doc.documentElement).appendChild(script);
    script.remove();
    return;
  } catch (e) {}

  // Last resort for embeds that block inline scripts: try the direct write.
  try { window.parent.location.href = target; } catch (e) {}
}

function bvThrottleInput(pct) {
  var el = document.getElementById('bv-thr-val');
  if (el) el.textContent = pct + '%';
}

// The slider is the edge's throttle in the flow model, not a UI flag: the app
// reruns run_scenario({edge_id: fraction}), so the popup, the KPIs and the
// colours all come from that solve.
function bvSetEdgeThrottle(eid, pct) {
  var frac = Math.round(Number(pct)) / 100;
  bvClosePopup();
  bvNavigate({ bv_action: 'set_throttle', bv_value: eid,
               bv_throttle: frac.toFixed(2) });
}

function bvRestoreEdge(eid) {
  REMOVED = REMOVED.filter(function (e) { return e !== eid; });
  bvClosePopup();
  bvRenderRemoved();
  bvNavigate({ bv_action: 'restore_edge', bv_value: eid });
}

function bvClearRemoved() {
  var ids = REMOVED.join(',');
  REMOVED = [];
  bvRenderRemoved();
  bvNavigate({ bv_action: 'clear_removed', bv_value: ids });
}

function bvSetModel(mode) {
  if (mode === 'gnn' && !HAS_GNN) return;
  MODEL = mode;
  document.getElementById('bv-btn-flow').className = mode === 'flow' ? 'active' : '';
  document.getElementById('bv-btn-gnn').className  = mode === 'gnn'  ? 'active-gnn' : '';
  bvNavigate({ bv_action: 'set_model', bv_value: mode });
}

function bvToggleHormuz() {
  if (!HAS_HZ) return;
  HZ_ON = !HZ_ON;
  bvApplyHormuzUI();
  bvNavigate({ bv_action: 'hormuz_mode', bv_value: HZ_ON ? 'on' : 'off' });
}

function bvToggleScenario(key) {
  // Switches between a calibrated crisis scenario and the pre-crisis baseline.
  // The sidebar's Scenario selectbox carries the same state, so it follows.
  var next = SCENARIO === key ? 'baseline' : key;
  bvNavigate({ bv_action: 'set_scenario', bv_value: next }, false);
}

// ── Init ─────────────────────────────────────────────────────────────────────
bvRenderKPIs();
bvRenderRemoved();
bvApplyHormuzUI();

document.addEventListener('click', function (e) {
  if (e.target.closest && !e.target.closest('#bv-popup') && !e.target.closest('#map')) {
    bvClosePopup();
  }
});
</script>
</body>
</html>
'''


def render_globe_html(
    *,
    paths: list[dict],
    ports: list[dict],
    junctions: list[dict],
    kpis: dict,
    brent_data: dict | None,
    removed_edges: list[str],
    model_mode: str,
    scenario_key: str,
    throttles: dict[str, float],
    hormuz_mode: bool,
    has_hormuz: bool,
    gnn_available: bool,
    legend_html: str,
    hormuz_rows_html: str,
    gnn_note_html: str,
) -> str:
    """Fill the template. Tokens are replaced literally, so JS/CSS need no escaping."""
    import json

    tokens = {
        "@@PATHS@@": json.dumps(paths),
        "@@PORTS@@": json.dumps(ports),
        "@@JUNCTIONS@@": json.dumps(junctions),
        "@@KPI@@": json.dumps(kpis),
        "@@BRENT@@": json.dumps(brent_data or {}),
        "@@REMOVED@@": json.dumps(list(removed_edges)),
        "@@MODEL@@": model_mode,
        "@@SCENARIO_KEY@@": scenario_key,
        "@@THROTTLES@@": json.dumps({k: round(float(v), 2) for k, v in throttles.items()}),
        "@@RED_SEA_CLS@@": "active" if scenario_key == "red_sea_2024" else "",
        "@@PANAMA_CLS@@": "active" if scenario_key == "panama_drought_2023" else "",
        "@@EVER_GIVEN_CLS@@": "active" if scenario_key == "ever_given_2021" else "",
        "@@HAS_GNN@@": "true" if gnn_available else "false",
        "@@HAS_HZ@@": "true" if has_hormuz else "false",
        "@@HZ_ON@@": "true" if (hormuz_mode and has_hormuz) else "false",
        "@@FLOW_CLS@@": "active" if model_mode == "flow" else "",
        "@@GNN_CLS@@": "active-gnn" if model_mode == "gnn" else "",
        "@@HZ_CLS@@": "active" if (hormuz_mode and has_hormuz) else "",
        "@@HZ_DISABLED@@": "" if has_hormuz else 'disabled title="Weekly Hormuz transfer predictions are not in data/processed"',
        "@@GNN_DISABLED@@": "" if gnn_available else 'disabled title="No GNN predictions on disk"',
        "@@GNN_NOTE@@": gnn_note_html,
        "@@LEGEND@@": legend_html,
        "@@HZ_ROWS@@": hormuz_rows_html,
    }
    html = GLOBE_HTML
    for token, value in tokens.items():
        html = html.replace(token, value)
    return html

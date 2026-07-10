/* Charlottesville historical zoning map browser.
 *
 * Single-map mode: one edition visible, timeline picks the year.
 * Compare mode: two synced maps split by a draggable divider.
 * Vector district overlays (2003 code / 2024 code) toggle on top.
 * State (year, compare year, camera) lives in the URL hash.
 */

const BASE_STYLE = "https://tiles.openfreemap.org/styles/positron";
const START = { center: [-78.478, 38.031], zoom: 13.2 };
const ATTRIB = 'Maps © City of Charlottesville, via <a href="https://www.cvillepedia.org/Zoning">cvillepedia</a>';
// Editions ship as one .pmtiles archive per year, read via HTTP range
// requests — no tile server. Override TILES_BASE (absolute URL, trailing
// slash) to host the archives elsewhere, e.g. an R2 bucket.
const TILES_BASE = window.TILES_BASE || "";

const pmProtocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", pmProtocol.tile);

const SOURCE_URLS = {}; // year -> original scan/PDF, filled from manifest below
fetch("sources.json").then(r => r.ok ? r.json() : {}).then(d => Object.assign(SOURCE_URLS, d));

let editions = {};   // index.json: year -> {path, minzoom, maxzoom, bounds}
let years = [];
let yearA, yearB;
let comparing = false;

const $ = id => document.getElementById(id);

async function init() {
  editions = await (await fetch("tiles/index.json")).json();
  years = Object.keys(editions).sort();
  yearA = years[years.length - 1];
  yearB = years[0];

  const hash = parseHash();
  if (hash.year && years.includes(hash.year)) yearA = hash.year;
  if (hash.compare && years.includes(hash.compare)) { yearB = hash.compare; comparing = true; }

  const mapA = mkMap("mapA");
  const mapB = mkMap("mapB");
  window._maps = { A: mapA, B: mapB };

  syncMaps(mapA, mapB);
  buildTimeline();
  buildCompareSelects();
  wireControls();
  initLegend();

  mapA.on("load", () => { addEditions(mapA); showYear("A", yearA); });
  mapB.on("load", () => { addEditions(mapB); showYear("B", yearB); });
  if (comparing) enterCompare(true);
  updateChrome();
}

function mkMap(container) {
  const m = new maplibregl.Map({
    container,
    style: BASE_STYLE,
    center: START.center,
    zoom: START.zoom,
    hash: container === "mapA" ? "map" : false,
    attributionControl: false,
  });
  m.addControl(new maplibregl.AttributionControl({ customAttribution: ATTRIB }), "bottom-right");
  if (container === "mapA") {
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new maplibregl.FullscreenControl({ container: document.body }), "top-right");
  }
  return m;
}

function addEditions(map) {
  for (const y of years) {
    const e = editions[y];
    const archive = new URL(TILES_BASE + e.path + ".pmtiles",
                            location.href).href;
    map.addSource("zon-" + y, {
      type: "raster",
      url: "pmtiles://" + archive,
      tileSize: 256,
      bounds: e.bounds,
    });
    map.addLayer({
      id: "zon-" + y,
      type: "raster",
      source: "zon-" + y,
      layout: { visibility: "none" },
      paint: {
        "raster-opacity": Number($("opacity").value) / 100,
        "raster-fade-duration": 0,
      },
    });
  }
}

function showYear(side, year) {
  const map = window._maps[side];
  if (side === "A") yearA = year; else yearB = year;
  if (map.getLayer("zon-" + year)) {
    for (const y of years) {
      map.setLayoutProperty("zon-" + y, "visibility",
        y === year ? "visible" : "none");
    }
  } // else: not added yet; the load handler calls us again
  updateChrome();
}

/* ---- sync two maps without feedback loops ---- */
function syncMaps(a, b) {
  let busy = false;
  const follow = (src, dst) => () => {
    if (busy || !comparing) return;
    busy = true;
    dst.jumpTo({
      center: src.getCenter(), zoom: src.getZoom(),
      bearing: src.getBearing(), pitch: src.getPitch(),
    });
    busy = false;
  };
  a.on("move", follow(a, b));
  b.on("move", follow(b, a));
}

/* ---- timeline ---- */
function buildTimeline() {
  const tl = $("timeline");
  tl.innerHTML = "";
  const y0 = +years[0], y1 = +years[years.length - 1];
  for (const y of years) {
    const dot = document.createElement("button");
    dot.className = "tl-dot";
    dot.dataset.year = y;
    dot.style.left = (3 + 94 * (+y - y0) / (y1 - y0)) + "%";
    dot.title = y;
    const label = document.createElement("span");
    label.textContent = y;
    dot.appendChild(label);
    dot.onclick = () => showYear("A", y);
    tl.appendChild(dot);
  }
}

function buildCompareSelects() {
  for (const [sel, side] of [[$("yearA"), "A"], [$("yearB"), "B"]]) {
    sel.innerHTML = years.map(y => `<option>${y}</option>`).join("");
    sel.onchange = () => showYear(side, sel.value);
  }
}

/* ---- compare mode ---- */
function enterCompare(on) {
  comparing = on;
  $("wrapB").classList.toggle("hidden", !on);
  $("divider").classList.toggle("hidden", !on);
  $("compare-years").classList.toggle("hidden", !on);
  $("compare-btn").classList.toggle("active", on);
  $("timeline").classList.toggle("hidden", on);
  if (on) {
    setSplit(0.5);
    const b = window._maps.B;
    b.resize();
    b.jumpTo({
      center: window._maps.A.getCenter(), zoom: window._maps.A.getZoom(),
      bearing: window._maps.A.getBearing(), pitch: window._maps.A.getPitch(),
    });
  } else {
    $("wrapA").style.clipPath = "";
  }
  updateChrome();
}

function setSplit(f) {
  f = Math.min(0.95, Math.max(0.05, f));
  const px = f * window.innerWidth;
  $("wrapA").style.clipPath = `inset(0 ${window.innerWidth - px}px 0 0)`;
  $("wrapB").style.clipPath = `inset(0 0 0 ${px}px)`;
  $("divider").style.left = px + "px";
}

function wireDivider() {
  let dragging = false;
  $("divider").addEventListener("pointerdown", e => {
    dragging = true;
    $("divider").setPointerCapture(e.pointerId);
  });
  window.addEventListener("pointermove", e => {
    if (dragging) setSplit(e.clientX / window.innerWidth);
  });
  window.addEventListener("pointerup", () => (dragging = false));
}

/* ---- chrome ---- */
function wireControls() {
  $("opacity").oninput = () => {
    const v = Number($("opacity").value) / 100;
    for (const m of Object.values(window._maps)) {
      if (!m.isStyleLoaded()) continue;
      for (const y of years) m.setPaintProperty("zon-" + y, "raster-opacity", v);
    }
  };
  $("compare-btn").onclick = () => enterCompare(!comparing);
  wireDivider();
  window.addEventListener("keydown", e => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    if (e.target.tagName === "SELECT" || e.target.tagName === "INPUT") return;
    const i = years.indexOf(yearA) + (e.key === "ArrowRight" ? 1 : -1);
    if (i >= 0 && i < years.length) showYear("A", years[i]);
  });
}

function updateChrome() {
  $("title-year").textContent = comparing ? `${yearA} vs ${yearB}` : yearA;
  document.querySelectorAll(".tl-dot").forEach(d =>
    d.classList.toggle("active", d.dataset.year === yearA));
  $("yearA").value = yearA;
  $("yearB").value = yearB;
  const src = SOURCE_URLS[yearA];
  $("source-sep").style.display = src ? "" : "none";
  if (src) $("source-link").href = src;
  renderLegend();
  writeHash();
}

/* ---- legend panel ----
 * Each edition's printed legend, cropped from the sheet, ships as
 * docs/legends/{year}.png (built by scripts/prep.py). */
function initLegend() {
  $("legend-head").onclick = () => {
    $("legend").classList.toggle("collapsed");
    $("legend-arrow").textContent =
      $("legend").classList.contains("collapsed") ? "▸" : "▾";
  };
  if (window.innerWidth < 700) $("legend-head").onclick();
  renderLegend();
}

function legendBlock(year) {
  return `<div class="lg-block"><div class="lg-year">${year}</div>` +
         `<img class="lg-img" src="legends/${year}.png" alt="${year} legend"` +
         ` title="Click to enlarge" onclick="showLightbox(this.src)"></div>`;
}

function showLightbox(src) {
  const box = document.createElement("div");
  box.id = "lightbox";
  box.innerHTML = `<img src="${src}">`;
  box.onclick = () => box.remove();
  document.body.appendChild(box);
}

function renderLegend() {
  $("legend-body").innerHTML =
    comparing ? legendBlock(yearA) + legendBlock(yearB) : legendBlock(yearA);
}

/* ---- hash state (alongside maplibre's #map=z/lat/lng) ---- */
function parseHash() {
  const m = location.hash.match(/year=(\d{4})/);
  const c = location.hash.match(/compare=(\d{4})/);
  return { year: m && m[1], compare: c && c[1] };
}

function writeHash() {
  let h = location.hash.replace(/&?(year|compare)=\d{4}/g, "");
  if (!h.startsWith("#")) h = "#" + h;
  h += (h.length > 1 ? "&" : "") + "year=" + yearA;
  if (comparing) h += "&compare=" + yearB;
  history.replaceState(null, "", h);
}

init();

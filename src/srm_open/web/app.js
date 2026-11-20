/* Srm-Open web front end.
 *
 * No build step and no external assets: plain DOM + inline SVG, so the same
 * files work from `srm serve`, from `uvicorn`, and from a frozen binary whose
 * data directory is read-only.
 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = { families: [], propellants: [], mode: "forward" };

/* ---------------------------------------------------------------- helpers */

function fmt(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (typeof value !== "number") return String(value);
  const abs = Math.abs(value);
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e6)) return value.toExponential(2);
  return value.toFixed(digits).replace(/\.?0+$/, "");
}

const METRICS = [
  ["average_thrust", "平均推力", "N", 1],
  ["peak_thrust", "峰值推力", "N", 1],
  ["burn_time", "工作时间", "s", 3],
  ["total_impulse", "总冲", "N·s", 1],
  ["specific_impulse", "比冲", "s", 1],
  ["propellant_mass", "装药量", "kg", 3],
  ["peak_pressure", "峰值压强", "MPa", null],
  ["loading_fraction", "装载分数", "—", 3],
  ["sliver_fraction", "余药分数", "—", 3],
];

function metricValue(key, perf) {
  if (!perf) return null;
  const raw = perf[key];
  if (raw === null || raw === undefined) return null;
  return key === "peak_pressure" ? raw / 1e6 : raw;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function htmlEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ------------------------------------------------------------------ charts */

function svgRoot(width, height) {
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, role: "img" });
  svg.style.height = "auto";
  return svg;
}

/** Port cross-section: grain in brown, port cavity cut out. */
function drawSketch(payload, size = 320) {
  const segments = payload && payload.segments ? payload.segments : [];
  if (!segments.length) return null;
  const seg = segments[0];
  const pad = 16;
  const svg = svgRoot(size, size);
  const d = seg.outer && seg.outer.length ? seg.outer : null;
  if (!d) return svg;

  // fit the outer circle into the viewbox
  let r = 0;
  for (const [, y] of d) r = Math.max(r, Math.abs(y));
  if (r <= 0) return svg;
  const scale = (size / 2 - pad) / r;
  const cx = size / 2;
  const cy = size / 2;

  const toPath = (ring) =>
    ring.map(([x, y], i) =>
      `${i === 0 ? "M" : "L"}${(cx + x * scale).toFixed(2)} ${(cy - y * scale).toFixed(2)}`
    ).join(" ") + " Z";

  const grainPath = svgEl("path", { d: toPath(d), class: "grain-fill" });
  svg.appendChild(grainPath);

  for (const ring of seg.port_rings || []) {
    if (!ring || ring.length < 3) continue;
    svg.appendChild(svgEl("path", { d: toPath(ring), class: "port-fill" }));
  }

  svg.appendChild(svgEl("circle", {
    cx, cy, r: r * scale, class: "outline",
  }));
  return svg;
}

/** Simple line chart with axes and a light grid. */
function drawCurve(series, opts = {}) {
  const { width = 520, height = 200, xLabel = "", yLabel = "", colors = [] } = opts;
  const keys = Object.keys(series).filter((k) => k !== "x");
  if (!keys.length) return null;

  const x = series.x || series[Object.keys(series)[0]].map((_, i) => i);
  const all = keys.flatMap((k) => series[k]).filter((v) => Number.isFinite(v));
  if (!all.length) return null;

  const padL = 54, padR = 10, padT = 12, padB = 30;
  const w = width - padL - padR;
  const h = height - padT - padB;
  const xMin = Math.min(...x), xMax = Math.max(...x);
  const yMin = Math.min(0, ...all), yMax = Math.max(...all);
  const sx = (v) => padL + ((v - xMin) / (xMax - xMin || 1)) * w;
  const sy = (v) => padT + h - ((v - yMin) / (yMax - yMin || 1)) * h;

  const svg = svgRoot(width, height);

  for (let i = 0; i <= 4; i++) {
    const gy = padT + (h * i) / 4;
    svg.appendChild(svgEl("line", { x1: padL, y1: gy, x2: padL + w, y2: gy, class: "grid" }));
    const value = yMax - ((yMax - yMin) * i) / 4;
    svg.appendChild(svgEl("text", { x: padL - 6, y: gy + 3, "text-anchor": "end" }, fmt(value, 3)));
  }

  svg.appendChild(svgEl("line", { x1: padL, y1: padT + h, x2: padL + w, y2: padT + h, class: "axis" }));
  svg.appendChild(svgEl("line", { x1: padL, y1: padT, x2: padL, y2: padT + h, class: "axis" }));

  svg.appendChild(svgEl("text", { x: padL + w / 2, y: height - 6, "text-anchor": "middle" }, xLabel));
  svg.appendChild(svgEl("text", {
    x: 10, y: padT + h / 2, "text-anchor": "middle",
    transform: `rotate(-90 10 ${padT + h / 2})`,
  }, yLabel));

  keys.forEach((key, idx) => {
    const pts = series[key].map((v, i) => `${sx(x[i]).toFixed(2)},${sy(v).toFixed(2)}`).join(" ");
    svg.appendChild(svgEl("polyline", {
      points: pts, fill: "none", stroke: colors[idx % colors.length],
      "stroke-width": 1.6, "stroke-linejoin": "round",
    }));
  });

  return svg;
}

/* ------------------------------------------------------------------- views */

function renderMetrics(perf) {
  const grid = document.createElement("div");
  grid.className = "metrics";
  for (const [key, label, unit, digits] of METRICS) {
    const value = metricValue(key, perf);
    if (value === null) continue;
    const box = document.createElement("div");
    box.className = "metric";
    box.innerHTML =
      `<div class="k">${label}</div>` +
      `<div class="v">${fmt(value, digits ?? 3)} <span class="u">${unit}</span></div>`;
    grid.appendChild(box);
  }
  return grid;
}

function renderChecks(verification) {
  const wrap = document.createElement("div");
  wrap.className = "checks";
  const total = verification.total || 0;
  const passed = verification.passed || 0;
  const summary = document.createElement("div");
  summary.className = "check " + (passed === total ? "pass" : "fail");
  summary.innerHTML = `<span class="mark">${passed === total ? "✓" : "!"}</span>` +
    `<span class="name">校核 ${passed}/${total} 通过</span>`;
  wrap.appendChild(summary);
  for (const name of verification.failed || []) {
    const row = document.createElement("div");
    row.className = "check fail";
    row.innerHTML = `<span class="mark">✗</span><span class="name">${name}</span>`;
    wrap.appendChild(row);
  }
  return wrap;
}

function renderSpec(recovered) {
  const pre = document.createElement("div");
  pre.className = "spec";
  const params = Object.entries(recovered.params || {})
    .map(([k, v]) => `  ${k.padEnd(18)} ${fmt(v, 5)}`)
    .join("\n");
  pre.textContent =
    `family      ${recovered.family}\n` +
    `length      ${fmt(recovered.length, 5)} m\n` +
    `outer dia.  ${fmt(recovered.outer_diameter, 5)} m\n` +
    `burn mode   ${recovered.burn_mode}\n` +
    `params\n${params}`;
  return pre;
}

function figure(caption, svg) {
  if (!svg) return null;
  const fig = document.createElement("figure");
  const cap = document.createElement("figcaption");
  cap.textContent = caption;
  fig.appendChild(cap);
  fig.appendChild(svg);
  return fig;
}

function render(body, mode) {
  const host = $("#results-body");
  host.className = "";
  host.textContent = "";

  const head = document.createElement("h2");
  head.textContent = mode === "inverse" ? "逆向设计结果" : "正向设计结果";
  host.appendChild(head);

  if (body.recovered) host.appendChild(renderSpec(body.recovered));
  if (body.performance) host.appendChild(renderMetrics(body.performance));

  const curves = body.curves || {};
  if (curves.time && curves.thrust) {
    host.appendChild(figure(
      "推力 / 压强 — 时间",
      drawCurve(
        { x: curves.time, thrust: curves.thrust, pressure: (curves.chamber_pressure || []).map((p) => p / 1e6) },
        { xLabel: "t (s)", yLabel: "F (N) / p (MPa)", colors: ["var(--curve-a)", "var(--curve-b)"] }
      )
    ));
  }
  if (curves.burn_distance && curves.burn_area) {
    host.appendChild(figure(
      "燃面积 — 燃去厚度",
      drawCurve(
        { x: curves.burn_distance, burn_area: curves.burn_area },
        { xLabel: "e (m)", yLabel: "A_b (m²)", colors: ["var(--curve-c)"] }
      )
    ));
  }

  const sketchSvg = body.sketch ? drawSketch(body.sketch) : null;
  if (sketchSvg) host.appendChild(figure("药型横截面（棕色为装药，深色为通道）", sketchSvg));

  if (body.verification) host.appendChild(renderChecks(body.verification));

  if (body.input_hash) {
    const note = document.createElement("p");
    note.className = "hint";
    note.textContent = `provenance hash: ${body.input_hash.slice(0, 16)}…`;
    host.appendChild(note);
  }
}

/* ------------------------------------------------------------------- forms */

function showError(message) {
  const box = $("#error");
  box.textContent = message;
  box.hidden = false;
}

function clearError() {
  const box = $("#error");
  box.hidden = true;
  box.textContent = "";
}

async function post(path, payload) {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
    throw new Error(`${resp.status}: ${detail}`);
  }
  return body;
}

function numberOrNull(form, name) {
  const field = form.elements[name];
  if (!field || field.value === "") return null;
  const value = Number(field.value);
  return Number.isFinite(value) ? value : null;
}

async function runForward(event) {
  event.preventDefault();
  clearError();
  const form = event.target;
  const button = $("#run-forward");
  button.disabled = true;
  button.textContent = "计算中…";
  try {
    const payload = {
      thrust: Number(form.elements.thrust.value),
      burn_time: Number(form.elements.burn_time.value),
      propellant: form.elements.propellant.value,
    };
    const family = form.elements.family.value;
    if (family) payload.family = family;
    const outer = numberOrNull(form, "outer_diameter");
    if (outer) payload.outer_diameter = outer;
    const pmax = numberOrNull(form, "max_pressure_mpa");
    if (pmax) payload.max_chamber_pressure = pmax * 1e6;

    const body = await post("/design?summary=true&curves=true&sketch=true", payload);
    render(body, "forward");
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = false;
    button.textContent = "生成药型方案";
  }
}

async function runInverse(event) {
  event.preventDefault();
  clearError();
  const form = event.target;
  const button = $("#run-inverse");
  button.disabled = true;
  button.textContent = "优化中…";
  try {
    const payload = {
      family: form.elements.family.value,
      outer_diameter: Number(form.elements.outer_diameter.value),
      propellant: form.elements.propellant.value,
      thrust: numberOrNull(form, "thrust"),
      burn_time: numberOrNull(form, "burn_time"),
      max_iter: Number(form.elements.max_iter.value) || 120,
    };
    const length = numberOrNull(form, "length");
    if (length) payload.length = length;

    const body = await post("/inverse?curves=true&sketch=true", payload);
    render(body, "inverse");
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = false;
    button.textContent = "反推设计参数";
  }
}

/* -------------------------------------------------------------------- init */

function fillSelect(select, options, placeholder) {
  select.textContent = "";
  if (placeholder) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = placeholder;
    select.appendChild(opt);
  }
  for (const { value, label } of options) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    select.appendChild(opt);
  }
}

function wireTabs() {
  const forward = $("#tab-forward");
  const inverse = $("#tab-inverse");
  const select = (mode) => {
    state.mode = mode;
    forward.setAttribute("aria-selected", String(mode === "forward"));
    inverse.setAttribute("aria-selected", String(mode === "inverse"));
    $("#pane-forward").hidden = mode !== "forward";
    $("#pane-inverse").hidden = mode !== "inverse";
    clearError();
  };
  forward.addEventListener("click", () => select("forward"));
  inverse.addEventListener("click", () => select("inverse"));
}

async function init() {
  wireTabs();
  $("#pane-forward").addEventListener("submit", runForward);
  $("#pane-inverse").addEventListener("submit", runInverse);

  try {
    const [families, propellants] = await Promise.all([
      fetch("/families").then((r) => r.json()),
      fetch("/propellants").then((r) => r.json()),
    ]);
    state.families = families.families || [];
    state.propellants = propellants.propellants || [];

    const familyOptions = state.families.map((f) => ({
      value: f.family,
      label: f.params.length ? f.family : `${f.family}（无参数空间）`,
    }));
    fillSelect($("#family-forward"), familyOptions, "自动选择");
    fillSelect($("#family-inverse"),
      state.families.filter((f) => f.params.length).map((f) => ({ value: f.family, label: f.family })));
    if ($("#family-inverse").options.length) $("#family-inverse").selectedIndex = 3;

    const propOptions = state.propellants.map((p) => ({ value: p.id, label: `${p.name} (${p.id})` }));
    for (const id of ["#propellant-forward", "#propellant-inverse"]) {
      fillSelect($(id), propOptions);
      const preferred = propOptions.findIndex((o) => o.value === "ap_htpb_80");
      if (preferred >= 0) $(id).selectedIndex = preferred;
    }
  } catch (err) {
    showError(`无法加载后端数据：${err.message}`);
  }
}

document.addEventListener("DOMContentLoaded", init);

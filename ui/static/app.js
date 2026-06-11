/* ============================================================
   Zulrah RL Observatory — frontend
   Consumes:
     WS  /ws/live              per-tick game state
     GET /api/metrics/live     latest trainer rollout metrics (poll ~2s)
     GET /api/metrics/history  cross-version training_history.csv rows
   Renders a top-down arena canvas + a grid of live uPlot charts.
   ============================================================ */
"use strict";

/* -------------------------------------------------------------
   Constants — MUST match eval/watch.py & env/state.py
   ------------------------------------------------------------- */
const X0 = 2256, X1 = 2277, Y0 = 3062, Y1 = 3080;   // arena template bounds (north up)
const COLS = X1 - X0 + 1;                            // 22
const ROWS = Y1 - Y0 + 1;                            // 19

const FORM_COLOR = {
  range: "#3ddc84", mage: "#5b9dff", melee: "#ff6b6b", unknown: "#9aa3b5",
};
const OVERHEAD_COLOR = {
  magic: "#5b9dff", missiles: "#3ddc84", melee: "#ff6b6b", none: "#5c6577",
};
const STYLE_DOT = { mage: "#5b9dff", range: "#3ddc84" };
const NEEDED_PRAY = { range: "missiles", mage: "magic" };   // form -> required overhead
const ZHP_MAX = 500.0;

const PALETTE = {
  accent: "#6ea8ff", accent2: "#8b7bff", good: "#3ddc84", bad: "#ff6b6b",
  warn: "#f0a637", hist: "#5c6577", grid: "#1e2433", text: "#8a93a6",
  venom: "#a96bff", mage: "#5b9dff",
};

/* -------------------------------------------------------------
   DOM helpers
   ------------------------------------------------------------- */
const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v == null || Number.isNaN(v)) ? "—" : Number(v).toFixed(d);
const pct = (v) => (v == null || Number.isNaN(v)) ? "—" : (v * 100).toFixed(1) + "%";
function compactInt(v) {
  if (v == null || Number.isNaN(v)) return "—";
  v = Number(v);
  if (v >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(0) + "k";
  return String(Math.round(v));
}

/* -------------------------------------------------------------
   Arena renderer (canvas)
   ------------------------------------------------------------- */
const Arena = (() => {
  const canvas = $("arena");
  const ctx = canvas.getContext("2d");
  let arena = null;          // {x0,y0,w,h,blocked:[[..]]}
  let dpr = 1, tile = 26;

  function resize() {
    const cssW = canvas.clientWidth || 780;
    const cssH = cssW * (ROWS / COLS);
    dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    canvas.style.height = cssH + "px";
    // draw entirely in device pixels (tile is sized in device px) for crisp rendering.
    tile = canvas.width / COLS;
  }

  // tile (x,y absolute) + per-episode offset -> canvas pixel center
  function toPx(x, y, off) {
    const cx = (x - off[0] - X0 + 0.5) * tile;
    const cy = (Y1 - (y - off[1]) + 0.5) * tile;
    return [cx, cy];
  }

  function setArena(a) { if (a && a.blocked) arena = a; }

  function clear() {
    ctx.fillStyle = "#0a1322";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  function drawTiles() {
    if (!arena || !arena.blocked) { drawGridOnly(); return; }
    const rows = arena.blocked;
    for (let r = 0; r < rows.length; r++) {
      const ty = Y0 + r;
      const row = rows[r];
      for (let c = 0; c < row.length; c++) {
        const b = row[c];
        const px = c * tile;
        const py = (Y1 - ty) * tile;
        ctx.fillStyle = b ? "#142844" : "#39432f";
        ctx.fillRect(px, py, tile + 0.5, tile + 0.5);
        if (b) {
          // subtle water shimmer
          ctx.fillStyle = "rgba(90,150,230,0.05)";
          ctx.fillRect(px, py, tile + 0.5, tile / 2);
        }
      }
    }
    drawGrid();
  }

  function drawGridOnly() {
    ctx.fillStyle = "#0e1a2e";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    drawGrid();
  }

  function drawGrid() {
    ctx.strokeStyle = "rgba(110,168,255,0.06)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let gx = 0; gx <= COLS; gx++) {
      ctx.moveTo(Math.round(gx * tile) + 0.5, 0);
      ctx.lineTo(Math.round(gx * tile) + 0.5, ROWS * tile);
    }
    for (let gy = 0; gy <= ROWS; gy++) {
      ctx.moveTo(0, Math.round(gy * tile) + 0.5);
      ctx.lineTo(COLS * tile, Math.round(gy * tile) + 0.5);
    }
    ctx.stroke();
  }

  function render(state) {
    clear();
    drawTiles();
    const off = state.offset || [0, 0];
    const p = state.player || {};
    const z = state.zulrah || {};

    // venom clouds
    for (const [cx, cy] of state.clouds || []) {
      const [px, py] = toPx(cx, cy, off);
      ctx.fillStyle = "rgba(169,107,255,0.34)";
      ctx.fillRect(px - tile / 2, py - tile / 2, tile, tile);
      ctx.strokeStyle = "rgba(169,107,255,0.5)";
      ctx.lineWidth = 1;
      ctx.strokeRect(px - tile / 2 + 0.5, py - tile / 2 + 0.5, tile - 1, tile - 1);
    }

    // snakelings
    for (const [sx, sy] of state.snakelings || []) {
      const [px, py] = toPx(sx, sy, off);
      ctx.beginPath();
      ctx.arc(px, py, tile * 0.22, 0, Math.PI * 2);
      ctx.fillStyle = "#5fd07a";
      ctx.fill();
      ctx.strokeStyle = "rgba(10,15,22,0.8)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // Zulrah
    if (z.present) {
      const [zx, zy] = toPx(z.x, z.y, off);
      const col = FORM_COLOR[z.form] || FORM_COLOR.unknown;
      const R = tile * 0.92;
      // glow
      const g = ctx.createRadialGradient(zx, zy, R * 0.3, zx, zy, R * 1.7);
      g.addColorStop(0, hexA(col, 0.35));
      g.addColorStop(1, hexA(col, 0));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(zx, zy, R * 1.7, 0, Math.PI * 2); ctx.fill();
      // body
      ctx.beginPath(); ctx.arc(zx, zy, R, 0, Math.PI * 2);
      ctx.fillStyle = col; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = "#e9edf5"; ctx.stroke();
      // hp bar
      const frac = (z.hp || 0) / Math.max(1, z.maxHp || 1);
      const bw = R * 2, bh = 5, bx = zx - R, by = zy - R - 11;
      ctx.fillStyle = "rgba(10,15,22,0.85)";
      ctx.fillRect(bx, by, bw, bh);
      ctx.fillStyle = col;
      ctx.fillRect(bx, by, bw * Math.max(0, Math.min(1, frac)), bh);
    }

    // player
    const [px, py] = toPx(p.x || 0, p.y || 0, off);
    const ohCol = OVERHEAD_COLOR[p.overhead] || OVERHEAD_COLOR.none;
    ctx.beginPath();
    ctx.arc(px, py, tile * 0.42, 0, Math.PI * 2);
    ctx.lineWidth = 3.2;
    ctx.strokeStyle = ohCol;
    ctx.stroke();
    // inner attack-style dot
    ctx.beginPath();
    ctx.arc(px, py, tile * 0.2, 0, Math.PI * 2);
    ctx.fillStyle = STYLE_DOT[p.attack_style] || "#c8cedb";
    ctx.fill();
    // venom indicator ring
    if (p.venomed) {
      ctx.beginPath();
      ctx.arc(px, py, tile * 0.54, 0, Math.PI * 2);
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = PALETTE.venom;
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  window.addEventListener("resize", () => { resize(); });
  resize();
  return { render, setArena, resize };
})();

/* -------------------------------------------------------------
   HUD update
   ------------------------------------------------------------- */
function updateHUD(s) {
  const p = s.player || {};
  const z = s.zulrah || {};

  // header
  $("hs-model").textContent = s.model_name || "—";
  $("hs-episode").textContent = s.episode ?? "—";
  $("hs-kills").textContent = s.kills ?? "—";
  $("hs-tick").textContent = s.tick ?? "—";
  $("footer-run").textContent = s.model_name || "—";

  // bars
  const hp = p.hp ?? 0, maxHp = p.maxHp ?? 99;
  $("hp-fill").style.width = (100 * hp / Math.max(1, maxHp)) + "%";
  $("hp-text").textContent = `${hp}/${maxHp}`;
  const pr = p.prayer ?? 0;
  $("pray-fill").style.width = (100 * pr / 99) + "%";
  $("pray-text").textContent = `${pr}`;

  // action + reward chips
  $("action-name").textContent = s.action_name || (s.action != null ? `#${s.action}` : "—");
  const r = s.reward ?? 0;
  const rc = $("reward-chip");
  $("reward-val").textContent = (r >= 0 ? "+" : "") + fmt(r, 2);
  rc.classList.toggle("pos", r > 0);
  rc.classList.toggle("neg", r < 0);

  // tags
  const form = z.form || "—";
  const formVal = $("form-val");
  formVal.textContent = form;
  formVal.style.color = FORM_COLOR[z.form] || "var(--text)";

  $("style-val").textContent = p.attack_style || "—";
  const lastHit = p.last_atk && p.last_atk !== "none"
    ? `${p.last_atk} ${p.last_atk_ago != null ? p.last_atk_ago + "t" : ""}`.trim()
    : "—";
  $("lasthit-val").textContent = lastHit;
  $("pool-val").textContent = p.pool != null ? p.pool : "—";
  $("tag-venom").hidden = !p.venomed;

  // pray badge
  const need = NEEDED_PRAY[z.form];
  const prayable = !!z.present && need != null;
  const correct = prayable && p.overhead === need;
  const badge = $("pray-badge");
  badge.classList.toggle("ok", correct);
  badge.classList.toggle("wrong", prayable && !correct);
  $("pray-badge-val").textContent = correct ? "OK" : (prayable ? "WRONG" : (p.overhead || "none"));
}

/* -------------------------------------------------------------
   Inventory panel
   ------------------------------------------------------------- */
const ITEM_LABEL = {
  385: "shark", 6685: "brew", 12913: "anti-ven", 5952: "antidote", 2434: "pray pot",
  3024: "restore", 3026: "restore", 3028: "restore", 3030: "restore",
  9185: "c'bow", 11905: "trident", 9144: "r. bolts", 9244: "bolts", 11212: "d. arrow",
};
const itemName = (id) => ITEM_LABEL[id] || ("#" + id);
// Actual OSRS item sprites by id (RuneLite's icon cache). Falls back to a text label if a sprite 404s.
const ITEM_ICON = (id) => `https://static.runelite.net/cache/item/icon/${id}.png`;
function renderInventory(s) {
  const grid = $("inv-grid");
  if (!grid) return;
  const inv = s.inv || [];
  let html = "";
  for (let i = 0; i < 28; i++) {
    const it = inv[i];
    if (it) {
      const [id, amt] = it;
      html += `<div class="inv-slot filled" title="${itemName(id)} (#${id})">` +
              `<img class="inv-img" src="${ITEM_ICON(id)}" alt="${itemName(id)}" loading="lazy" ` +
              `onerror="this.classList.add('broken')">` +
              `<span class="nm">${itemName(id)}</span>` +
              (amt > 1 ? `<span class="qty">${amt >= 1000 ? Math.floor(amt / 1000) + "K" : amt}</span>` : "") +
              `</div>`;
    } else {
      html += '<div class="inv-slot empty"></div>';
    }
  }
  grid.innerHTML = html;
  const w = $("inv-weapon");
  if (w) {
    w.innerHTML = s.weapon
      ? `<img class="inv-img sm" src="${ITEM_ICON(s.weapon)}" onerror="this.style.display='none'"> ${itemName(s.weapon)}`
      : "";
  }
}

/* -------------------------------------------------------------
   Outcome banner
   ------------------------------------------------------------- */
let bannerTimer = null;
function showBanner(outcome) {
  if (!outcome || outcome === "ongoing") return;
  const b = $("banner");
  b.className = "banner " + outcome;
  $("banner-text").textContent = outcome.toUpperCase();
  b.hidden = false;
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => { b.hidden = true; }, 2200);
}

/* -------------------------------------------------------------
   Live websocket
   ------------------------------------------------------------- */
const Live = (() => {
  let ws = null, gotFirst = false, retry = 0;

  function setConn(state) {
    const c = $("conn"), d = $("conn-label");
    c.classList.remove("live", "down");
    if (state === "live") { c.classList.add("live"); d.textContent = "live"; }
    else if (state === "down") { c.classList.add("down"); d.textContent = "disconnected"; }
    else d.textContent = "connecting…";
  }

  function connect() {
    setConn("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/live`);

    ws.onopen = () => { retry = 0; setConn("live"); };
    ws.onmessage = (ev) => {
      let s;
      try { s = JSON.parse(ev.data); } catch (e) { return; }
      if (!gotFirst) { $("arena-empty").style.display = "none"; gotFirst = true; }
      if (s.arena) Arena.setArena(s.arena);
      Arena.render(s);
      updateHUD(s);
      renderInventory(s);
      if (s.outcome && s.outcome !== "ongoing") showBanner(s.outcome);
    };
    ws.onclose = () => {
      setConn("down");
      retry = Math.min(retry + 1, 6);
      const wait = Math.min(1000 * 2 ** retry, 15000);
      setTimeout(connect, wait);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }

  return { connect };
})();

/* -------------------------------------------------------------
   uPlot charts
   ------------------------------------------------------------- */
const Charts = (() => {
  const charts = {};
  const HIST_MAX = 800;     // history point cap per series
  const LIVE_MAX = 240;     // rolling live points

  function axisStroke() {
    return {
      stroke: PALETTE.text,
      grid: { stroke: PALETTE.grid, width: 1 },
      ticks: { stroke: PALETTE.grid, width: 1 },
      font: "11px ui-monospace, monospace",
      size: 42,
    };
  }
  function xAxis() {
    return { ...axisStroke(), space: 60, size: 30 };
  }

  function make(el, opts) {
    const node = $(el);
    node.innerHTML = "";
    const w = node.clientWidth || 320;
    const h = opts.height || 150;
    const base = {
      width: w,
      height: h,
      cursor: { y: false, points: { size: 5 } },
      legend: { live: true },
      scales: { x: { time: false }, y: opts.y || {} },
      axes: [xAxis(), axisStroke()],
      series: opts.series,
    };
    // uPlot needs data length === series length (x + each y), even when empty.
    const emptyData = opts.series.map(() => []);
    const u = new uPlot(base, opts.data || emptyData, node);
    u._h = h;
    charts[el] = u;
    return u;
  }

  function lineSeries(label, color, opts = {}) {
    return {
      label, stroke: color, width: opts.width || 1.8,
      fill: opts.fill || null, points: { show: false },
      value: opts.value || ((u, v) => v == null ? "—" : v.toFixed(opts.dec ?? 3)),
      ...(opts.dash ? { dash: opts.dash } : {}),
    };
  }

  function fillGrad(u, color) {
    try {
      const g = u.ctx.createLinearGradient(0, 0, 0, u.bbox.height);
      const n = parseInt(color.slice(1), 16);
      g.addColorStop(0, `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},0.22)`);
      g.addColorStop(1, `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},0)`);
      return g;
    } catch (e) { return null; }
  }

  function init() {
    // kill rate — history (x = timesteps) + live overlay handled via second chart series
    make("chart-killrate", {
      height: 104,
      y: { range: (u, lo, hi) => [0, Math.max(0.05, hi * 1.15)] },
      series: [
        {},
        lineSeries("history", PALETTE.hist, { dec: 3, fill: (u) => fillGrad(u, PALETTE.hist) }),
        lineSeries("live", PALETTE.accent, { width: 2, dec: 3 }),
      ],
    });

    make("chart-rew", {
      height: 104,
      series: [
        {},
        lineSeries("history", PALETTE.hist, { dec: 2, fill: (u) => fillGrad(u, PALETTE.hist) }),
        lineSeries("live", PALETTE.good, { width: 2, dec: 2 }),
      ],
    });

    make("chart-hill", {
      height: 104,
      y: { range: [0, ZHP_MAX] },
      series: [
        {},
        lineSeries("history", PALETTE.hist, { dec: 0, fill: (u) => fillGrad(u, PALETTE.hist) }),
        lineSeries("live", PALETTE.mage, { width: 2, dec: 0 }),
      ],
    });

    make("chart-entropy", {
      height: 104,
      series: [
        {},
        lineSeries("entropy", PALETTE.warn, { dec: 3, fill: (u) => fillGrad(u, PALETTE.warn) }),
        lineSeries("ent_coef", PALETTE.accent2, { width: 1.6, dec: 4, dash: [4, 3] }),
      ],
    });

    // Reward is condensed to TWO terms (env/reward.py): dense damage-to-Zulrah + a speed-scaled kill bonus.
    make("chart-rewcomp", {
      height: 118,
      series: [
        {},
        lineSeries("dmg_dealt", PALETTE.good, { dec: 3, width: 2 }),
        lineSeries("kill", PALETTE.accent, { dec: 3, width: 2 }),
      ],
    });

    $("rewcomp-legend").innerHTML =
      '<i style="background:var(--good)"></i>dmg_dealt' +
      '<i style="background:var(--accent)"></i>kill';

    window.addEventListener("resize", debounce(() => {
      for (const k in charts) {
        const node = $(k);
        charts[k].setSize({ width: node.clientWidth || 320, height: charts[k]._h });
      }
    }, 150));
  }

  // ----- history (from CSV rows) -----
  // Live-only: loading the v2–v5 CSV history set the shared x-axis to ~1.7M steps, so the current run's
  // low step numbers fell *inside* that range, never exceeded lastHx, and were dropped → "not updating".
  // The live run renders on its own growing x-axis; cross-version history lives in metrics/training_history.csv.
  // Plot the whole run from the sidecar's per-rollout series (ui/static/train_history.json), refreshed each
  // poll, so the charts show the full curve on load instead of filling one point every ~40s. This is a v8-only
  // series on its own growing x-axis, so the old "live points fell inside the v2-v5 CSV range" drop can't happen.
  function setHistory(series) {
    if (!Array.isArray(series) || !series.length) return;
    const xs = series.map((r) => r.total_timesteps);
    const col = (k) => series.map((r) => (r[k] == null ? null : r[k]));
    const apply = (chart, k1, k2) => {
      const u = charts[chart];
      if (!u) return;
      const data = [xs, col(k1)];
      if (u.series.length > 2) data[2] = k2 ? col(k2) : xs.map(() => null);
      u.setData(data);
    };
    apply("chart-killrate", "kill_rate", null);
    apply("chart-rew", "ep_rew_mean", null);
    apply("chart-hill", "zulrah_min_hp_mean", null);
    apply("chart-entropy", "entropy_loss", "ent_coef_now");
    apply("chart-rewcomp", "dmg_dealt", "kill");
  }

  // replace a single non-x series, keeping x shared with the chart's existing x band.
  function setSeries(chart, idx, xs, ys) {
    const u = charts[chart];
    if (!u) return;
    const data = u.data && u.data.length ? u.data.map((a) => a.slice()) : [];
    // history charts always own the x-axis from the CSV; live overlay is sparse on the same axis.
    data[0] = xs;
    data[idx] = ys;
    // ensure live series array exists & matches length (filled with nulls)
    for (let i = 1; i < u.series.length; i++) {
      if (!data[i]) data[i] = xs.map(() => null);
    }
    u.setData(data);
  }

  // ----- live overlay (poll, last value appended at current step) -----
  const liveBuf = {
    "chart-killrate": { x: [], y: [] },
    "chart-rew": { x: [], y: [] },
    "chart-hill": { x: [], y: [] },
    "chart-entropy": { x: [], y: [] },   // entropy live
    "chart-entropy-coef": { x: [], y: [] },
  };

  const rcBuf = { x: [], dmg: [], kill: [] };
  function pushRewComp(step, dmg, kill) {
    const u = charts["chart-rewcomp"];
    if (!u) return;
    if (rcBuf.x.length && rcBuf.x[rcBuf.x.length - 1] === step) {
      if (dmg != null) rcBuf.dmg[rcBuf.dmg.length - 1] = dmg;
      if (kill != null) rcBuf.kill[rcBuf.kill.length - 1] = kill;
    } else {
      rcBuf.x.push(step); rcBuf.dmg.push(dmg); rcBuf.kill.push(kill);
      if (rcBuf.x.length > LIVE_MAX) { rcBuf.x.shift(); rcBuf.dmg.shift(); rcBuf.kill.shift(); }
    }
    u.setData([rcBuf.x.slice(), rcBuf.dmg.slice(), rcBuf.kill.slice()]);
  }

  function pushLive(metrics) {
    const step = num(metrics.total_timesteps, null);
    if (step == null) return;
    pushOne("chart-killrate", 2, step, num(metrics.kill_rate, null));
    pushOne("chart-rew", 2, step, num(metrics.ep_rew_mean, null));
    pushOne("chart-hill", 2, step, num(metrics.zulrah_min_hp_mean, null));
    // entropy chart: series 1 = entropy_loss live, series 2 = ent_coef_now (optional)
    pushEntropy(step, num(metrics.entropy_loss, null), num(metrics.ent_coef_now, null));
    pushRewComp(step, num(metrics.dmg_dealt, null), num(metrics.kill, null));
  }

  function pushOne(chart, idx, step, val) {
    const u = charts[chart];
    if (!u || val == null) return;
    const buf = liveBuf[chart];
    if (buf.x.length && buf.x[buf.x.length - 1] === step) {
      buf.y[buf.y.length - 1] = val;            // same rollout, update in place
    } else {
      buf.x.push(step); buf.y.push(val);
      if (buf.x.length > LIVE_MAX) { buf.x.shift(); buf.y.shift(); }
    }
    mergeLive(u, chart, idx, buf);
  }

  function pushEntropy(step, ent, coef) {
    const u = charts["chart-entropy"];
    if (!u) return;
    if (ent != null) {
      const b = liveBuf["chart-entropy"];
      if (b.x.length && b.x[b.x.length - 1] === step) b.y[b.y.length - 1] = ent;
      else { b.x.push(step); b.y.push(ent); if (b.x.length > LIVE_MAX) { b.x.shift(); b.y.shift(); } }
    }
    if (coef != null) {
      const b = liveBuf["chart-entropy-coef"];
      if (b.x.length && b.x[b.x.length - 1] === step) b.y[b.y.length - 1] = coef;
      else { b.x.push(step); b.y.push(coef); if (b.x.length > LIVE_MAX) { b.x.shift(); b.y.shift(); } }
      $("entcoef-tag").textContent = "· ent_coef " + coef.toFixed(4);
    }
    // entropy chart shows live-only x: combine entropy(series1) + coef(series2) on entropy buffer's x
    const be = liveBuf["chart-entropy"], bc = liveBuf["chart-entropy-coef"];
    if (!be.x.length) return;
    const coefAligned = be.x.map((sx) => {
      const j = bc.x.indexOf(sx);
      return j >= 0 ? bc.y[j] : null;
    });
    u.setData([be.x.slice(), be.y.slice(), coefAligned]);
  }

  // merge a live buffer onto a history chart that owns its own x-axis:
  // we extend x with any live steps beyond the history range so the live tail draws.
  function mergeLive(u, chart, idx, buf) {
    if (!u.data || !u.data[0] || !u.data[0].length) {
      // no history yet: render live-only, nulling the other (history) series.
      const cols = u.series.map((_, i) => i === 0 ? buf.x.slice()
        : i === idx ? buf.y.slice() : buf.x.map(() => null));
      u.setData(cols);
      return;
    }
    const hx = u.data[0].slice();
    // start from current history arrays
    const cols = u.data.map((a) => a ? a.slice() : []);
    // extend x for any live step beyond the last history x
    const lastHx = hx.length ? hx[hx.length - 1] : -Infinity;
    const liveSeries = new Array(cols[0].length).fill(null);
    // map live points onto existing x where they match, append where newer
    for (let i = 0; i < buf.x.length; i++) {
      const sx = buf.x[i], sy = buf.y[i];
      const at = cols[0].indexOf(sx);
      if (at >= 0) {
        liveSeries[at] = sy;
      } else if (sx > lastHx) {
        cols[0].push(sx);
        for (let c = 1; c < cols.length; c++) cols[c].push(null);
        liveSeries.push(sy);
      }
    }
    cols[idx] = liveSeries;
    u.setData(cols);
  }

  function num(v, dflt) {
    if (v == null || v === "") return dflt;
    const n = typeof v === "number" ? v : parseFloat(v);
    return Number.isNaN(n) ? dflt : n;
  }

  function debounce(fn, ms) {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  return { init, setHistory, pushLive };
})();

/* -------------------------------------------------------------
   Stat tiles (from live metrics)
   ------------------------------------------------------------- */
function updateTiles(m) {
  if (!m || !Object.keys(m).length) return;
  const kr = m.kill_rate;
  $("t-killrate").textContent = pct(kr);
  $("t-killrate").style.color = (kr > 0) ? "var(--good)" : "var(--text)";
  $("t-steps").textContent = compactInt(m.total_timesteps);
  $("t-fps").textContent = m.fps != null ? Math.round(m.fps) : "—";
  $("t-eplen").textContent = m.ep_len_mean != null ? Number(m.ep_len_mean).toFixed(1) : "—";
  $("t-entropy").textContent = m.entropy_loss != null ? Number(m.entropy_loss).toFixed(2) : "—";
  if (m.ent_coef_now != null) $("t-entropy-sub").textContent = "coef " + Number(m.ent_coef_now).toFixed(4);
  const rew = m.ep_rew_mean;
  $("t-rew").textContent = rew != null ? (rew >= 0 ? "+" : "") + Number(rew).toFixed(1) : "—";
  $("t-rew").style.color = rew != null ? (rew >= 0 ? "var(--good)" : "var(--bad)") : "var(--text)";
}

/* -------------------------------------------------------------
   Pollers
   ------------------------------------------------------------- */
async function pollLive() {
  try {
    const r = await fetch("/api/metrics/live", { cache: "no-store" });
    if (r.ok) updateTiles(await r.json());   // tiles only; the charts are driven by the full series below
  } catch (e) { /* trainer not up yet */ }
  setTimeout(pollLive, 2000);
}

async function loadHistory() {
  try {
    const r = await fetch("/static/train_history.json", { cache: "no-store" });
    if (r.ok) Charts.setHistory(await r.json());
  } catch (e) { /* series absent */ }
  setTimeout(loadHistory, 4000);   // the sidecar rewrites the series every ~3s
}

/* -------------------------------------------------------------
   3D client frame poller — shows the spectated headless client.
   Gracefully degrades to an "offline" state when no frame exists.
   ------------------------------------------------------------- */
const Client3D = (() => {
  const PERIOD_MS = 1500;
  let everLoaded = false;

  // The container's crop occasionally writes a half-finished frame, so a single probe error is normal and must
  // NOT blank the panel. Once any frame has loaded we keep showing the last good one and only dim the status dot.
  function setStatus(up) {
    if (up) everLoaded = true;
    const dot = $("client3d-dot");
    const label = $("client3d-label");
    const empty = $("client3d-empty");
    const img = $("client3d-img");
    if (dot) dot.classList.toggle("live", up);
    if (label) label.textContent = up ? "live" : (everLoaded ? "stale" : "offline");
    if (empty) empty.hidden = everLoaded;          // hide the "no client frame yet" placeholder for good once a frame arrives
    if (img) img.classList.toggle("show", everLoaded);
  }

  function tick() {
    const img = $("client3d-img");
    if (!img) { setTimeout(tick, PERIOD_MS); return; }
    const probe = new Image();
    probe.onload = () => {
      img.src = probe.src;
      setStatus(true);
      setTimeout(tick, PERIOD_MS);
    };
    probe.onerror = () => {
      setStatus(false);
      setTimeout(tick, PERIOD_MS);
    };
    probe.src = "/api/client-frame?t=" + Date.now();
  }

  return { start: tick };
})();

/* -------------------------------------------------------------
   Boot
   ------------------------------------------------------------- */
window.addEventListener("DOMContentLoaded", () => {
  Arena.resize();
  Charts.init();
  Live.connect();
  loadHistory();
  pollLive();
  Client3D.start();
});

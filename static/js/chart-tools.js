/* chart-tools.js — ابزارهای ترسیم پیشرفته (به سبک TradingView) برای KLineChart
   Hand-built custom overlays registered on the vendored KLineChart v9 engine —
   NO TradingView library involved. Implements the tool families shown in the
   reference (Capture2.png): Pitchforks, Gann, and the Fibonacci set.

   Each tool is a klinecharts.registerOverlay({...}) whose createPointFigures()
   returns line/polygon/circle/arc/text figures computed from the user's click
   points (given as pixel coordinates). Geometry helpers live at the top.

   Loaded BEFORE chart.js; chart.js references these overlay names in its
   left-rail flyout groups. */
(function () {
  if (!window.klinecharts || !klinecharts.registerOverlay) return;

  // ---- palette (kept in the chart's blue/structure family) ----
  const STRUCT = "#2f6db3";       // structural lines (median, base, handle)
  const GUIDE = "#868fa3";        // helper / handle lines
  // fibonacci ratios → distinct colors, TradingView-ish
  const FIB = [
    { r: 0, c: "#787b86" }, { r: 0.236, c: "#f23645" }, { r: 0.382, c: "#ff9800" },
    { r: 0.5, c: "#4caf50" }, { r: 0.618, c: "#089981" }, { r: 0.786, c: "#00bcd4" },
    { r: 1, c: "#2f6db3" }, { r: 1.618, c: "#9c27b0" }, { r: 2.618, c: "#e91e63" },
    { r: 4.236, c: "#795548" },
  ];
  const FA = "۰۱۲۳۴۵۶۷۸۹";
  const faNum = (s) => String(s).replace(/[0-9]/g, (d) => FA[d]);

  // ---- vector helpers (all in pixel space) ----
  const sub = (a, b) => ({ x: a.x - b.x, y: a.y - b.y });
  const add = (a, d) => ({ x: a.x + d.x, y: a.y + d.y });
  const mul = (d, s) => ({ x: d.x * s, y: d.y * s });
  const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  const lerp = (a, b, t) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
  const len = (d) => Math.hypot(d.x, d.y) || 1;
  // point far along the a→b direction, so a segment reads as a ray off-canvas
  const ray = (a, b, L) => {
    const d = sub(b, a), m = len(d);
    return { x: a.x + (d.x / m) * (L || 8000), y: a.y + (d.y / m) * (L || 8000) };
  };

  // ---- figure builders ----
  const line = (coords, color, size, dashed) => ({
    type: "line",
    attrs: { coordinates: coords },
    styles: { color: color || STRUCT, size: size || 1,
      style: dashed ? "dashed" : "solid", dashedValue: [4, 4] },
    ignoreEvent: true,
  });
  const poly = (coords, color, fill) => ({
    type: "polygon",
    attrs: { coordinates: coords },
    styles: fill
      ? { style: "fill", color }
      : { style: "stroke", color: color || STRUCT, size: 1 },
    ignoreEvent: true,
  });
  const circle = (c, r, color, size) => ({
    type: "circle",
    attrs: { x: c.x, y: c.y, r },
    styles: { style: "stroke", color: color || STRUCT, size: size || 1 },
    ignoreEvent: true,
  });
  const arc = (c, r, a0, a1, color, size) => ({
    type: "arc",
    attrs: { x: c.x, y: c.y, r, startAngle: a0, endAngle: a1 },
    styles: { color: color || STRUCT, size: size || 1 },
    ignoreEvent: true,
  });
  const label = (p, text, color) => ({
    type: "text",
    attrs: { x: p.x, y: p.y, text, align: "left", baseline: "middle" },
    styles: { color: color || GUIDE, size: 11, family: "Tahoma", weight: "bold",
      backgroundColor: "rgba(255,255,255,.75)", paddingLeft: 3, paddingRight: 3,
      borderRadius: 2 },
    ignoreEvent: true,
  });

  // register with sensible defaults; `pts` = number of click points
  function reg(name, pts, draw) {
    klinecharts.registerOverlay({
      name,
      totalStep: pts + 1,
      needDefaultPointFigure: true,
      needDefaultXAxisFigure: true,
      needDefaultYAxisFigure: true,
      createPointFigures: (arg) => {
        const c = arg.coordinates || [];
        if (c.length < 2) return c.length === 1 ? [] : [];
        try { return draw(c, arg.bounding || { width: 9999, height: 9999 }) || []; }
        catch (e) { return []; }
      },
    });
  }

  // =========================================================================
  // PITCHFORK FAMILY  (3 points: A handle, B & C the two pivots)
  // =========================================================================
  function pitchfork(c, originFn, dashHandle) {
    const [A, B] = c;
    if (c.length < 3) return [line([A, B], GUIDE, 1, true)];
    const C = c[2];
    const M = mid(B, C);
    const S = originFn(A, B, C, M);       // where the median line starts
    const dir = sub(M, S);
    const medEnd = add(S, mul(dir, 40));
    const figs = [
      line([B, C], STRUCT, 1.5),                 // base
      line([S, medEnd], STRUCT, 1.5),            // median
      line([B, add(B, mul(dir, 40))], STRUCT, 1),// upper tine
      line([C, add(C, mul(dir, 40))], STRUCT, 1),// lower tine
    ];
    if (dashHandle) figs.push(line([A, S], GUIDE, 1, true)); // handle
    return figs;
  }
  reg("andrewsPitchfork", 3, (c) => pitchfork(c, (A) => A, false));
  reg("schiffPitchfork", 3, (c) => pitchfork(c, (A, B, C, M) => mid(A, M), true));
  reg("modifiedSchiffPitchfork", 3, (c) => pitchfork(c, (A, B) => mid(A, B), true));
  // inside pitchfork: median from A, tines drawn dashed (variant look)
  reg("insidePitchfork", 3, (c) => {
    const [A, B] = c;
    if (c.length < 3) return [line([A, B], GUIDE, 1, true)];
    const C = c[2], M = mid(B, C), dir = sub(M, A);
    return [
      line([B, C], STRUCT, 1.5),
      line([A, add(A, mul(dir, 40))], STRUCT, 1.5),
      line([B, add(B, mul(dir, 40))], STRUCT, 1, true),
      line([C, add(C, mul(dir, 40))], STRUCT, 1, true),
    ];
  });
  // pitchfan: fan of fib-spaced lines from A across the B→C base
  reg("pitchfan", 3, (c) => {
    const [A, B] = c;
    if (c.length < 3) return [line([A, B], GUIDE, 1, true)];
    const C = c[2];
    const figs = [line([B, C], STRUCT, 1.5)];
    [0, 0.25, 0.382, 0.5, 0.618, 0.75, 1].forEach((t, i) => {
      const P = lerp(B, C, t);
      figs.push(line([A, ray(A, P)], FIB[i % FIB.length].c, 1));
    });
    return figs;
  });

  // =========================================================================
  // GANN FAMILY
  // =========================================================================
  // Gann Fan: rays from A at Gann angle ratios; A→B sets the 1×1 unit.
  reg("gannFan", 2, (c) => {
    const [A, B] = c;
    const dx = B.x - A.x, dy = B.y - A.y;
    const ratios = [1 / 8, 1 / 4, 1 / 3, 1 / 2, 1, 2, 3, 4, 8];
    return ratios.map((rt, i) => {
      const target = { x: A.x + dx, y: A.y + dy * rt };
      return line([A, ray(A, target)], i === 4 ? "#f23645" : STRUCT, i === 4 ? 1.5 : 1);
    });
  });
  // Gann Box: rectangle A→C with internal fib grid + diagonals.
  function gannBox(c, square) {
    const [A, C] = c;
    let x0 = Math.min(A.x, C.x), x1 = Math.max(A.x, C.x);
    let y0 = Math.min(A.y, C.y), y1 = Math.max(A.y, C.y);
    if (square) { const s = Math.max(x1 - x0, y1 - y0); x1 = x0 + s; y1 = y0 + s; }
    const levels = [0, 0.25, 0.382, 0.5, 0.618, 0.75, 1];
    const figs = [poly([{ x: x0, y: y0 }, { x: x1, y: y0 },
      { x: x1, y: y1 }, { x: x0, y: y1 }], STRUCT)];
    levels.forEach((t, i) => {
      const col = FIB[i % FIB.length].c;
      const yy = y0 + (y1 - y0) * t, xx = x0 + (x1 - x0) * t;
      figs.push(line([{ x: x0, y: yy }, { x: x1, y: yy }], col, 1));   // horizontal
      figs.push(line([{ x: xx, y: y0 }, { x: xx, y: y1 }], col, 1));   // vertical
    });
    figs.push(line([{ x: x0, y: y1 }, { x: x1, y: y0 }], "#f23645", 1.5));
    figs.push(line([{ x: x0, y: y0 }, { x: x1, y: y1 }], "#f23645", 1.5));
    return figs;
  }
  reg("gannBox", 2, (c) => gannBox(c, false));
  reg("gannSquare", 2, (c) => gannBox(c, true));

  // =========================================================================
  // FIBONACCI FAMILY
  // =========================================================================
  // Trend-Based Fib Extension (3 pts): measure A→B, project levels from C.
  reg("fibExtension", 3, (c, b) => {
    const [A, B] = c;
    if (c.length < 3) return [line([A, B], GUIDE, 1, true)];
    const C = c[2], amp = B.y - A.y;
    const x0 = C.x, x1 = b.width;
    const figs = [line([A, B], GUIDE, 1, true), line([B, C], GUIDE, 1, true)];
    FIB.forEach((f) => {
      const y = C.y + amp * f.r;
      figs.push(line([{ x: x0, y }, { x: x1, y }], f.c, 1));
      figs.push(label({ x: x0 + 4, y }, faNum(f.r.toFixed(3)), f.c));
    });
    return figs;
  });
  // Fib Speed/Resistance Fan (2 pts): fan through fib points of the bounding box.
  reg("fibSpeedResistanceFan", 2, (c) => {
    const [A, B] = c;
    const figs = [poly([A, { x: B.x, y: A.y }, B, { x: A.x, y: B.y }], GUIDE)];
    FIB.filter((f) => f.r > 0 && f.r <= 1).forEach((f) => {
      figs.push(line([A, ray(A, { x: B.x, y: A.y + (B.y - A.y) * f.r })], f.c, 1));
      figs.push(line([A, ray(A, { x: A.x + (B.x - A.x) * f.r, y: B.y })], f.c, 1));
    });
    return figs;
  });
  // Fib Speed/Resistance Arcs (2 pts): concentric semicircle arcs at fib radii.
  reg("fibSpeedResistanceArcs", 2, (c) => {
    const [A, B] = c;
    const R = len(sub(B, A));
    const up = B.y < A.y;
    const a0 = up ? Math.PI : 0, a1 = up ? 2 * Math.PI : Math.PI;
    const figs = [line([A, B], GUIDE, 1, true)];
    FIB.filter((f) => f.r > 0 && f.r <= 1).forEach((f) =>
      figs.push(arc(A, R * f.r, a0, a1, f.c, 1)));
    return figs;
  });
  // Fib Circles (2 pts): full concentric circles at fib radii.
  reg("fibCircles", 2, (c) => {
    const [A, B] = c;
    const R = len(sub(B, A));
    const figs = [line([A, B], GUIDE, 1, true)];
    FIB.filter((f) => f.r > 0).forEach((f) => figs.push(circle(A, R * f.r, f.c, 1)));
    return figs;
  });
  // Fib Time Zone (2 pts): vertical lines at fibonacci-count time steps.
  reg("fibTimeZone", 2, (c, b) => {
    const [A, B] = c;
    const step = B.x - A.x;
    const figs = [line([A, B], GUIDE, 1, true)];
    [1, 2, 3, 5, 8, 13, 21, 34].forEach((n, i) => {
      const x = A.x + step * n;
      figs.push(line([{ x, y: 0 }, { x, y: b.height }], FIB[i % FIB.length].c, 1));
    });
    return figs;
  });
  // Trend-Based Fib Time (3 pts): fib time steps sized by the A→B interval, from C.
  reg("fibTrendTime", 3, (c, b) => {
    const [A, B] = c;
    if (c.length < 3) return [line([A, B], GUIDE, 1, true)];
    const C = c[2], unit = B.x - A.x;
    const figs = [line([A, B], GUIDE, 1, true)];
    [0, 1, 1.618, 2.618, 4.236].forEach((n, i) => {
      const x = C.x + unit * n;
      figs.push(line([{ x, y: 0 }, { x, y: b.height }], FIB[i % FIB.length].c, 1));
    });
    return figs;
  });
  // Fib Spiral (2 pts): golden spiral of quarter-turn arcs from A.
  reg("fibSpiral", 2, (c) => {
    const [A, B] = c;
    let r = len(sub(B, A)) / 6 || 6;
    const phi = 1.618033988749;
    // start angle from A→B direction, quadrant-snapped
    let ang = Math.atan2(B.y - A.y, B.x - A.x);
    // spiral centers step along the turning direction
    const figs = [];
    let center = { x: A.x, y: A.y };
    for (let k = 0; k < 8; k++) {
      const a0 = ang, a1 = ang + Math.PI / 2;
      figs.push(arc(center, r, a0, a1, STRUCT, 1.4));
      // next center is the arc's end point minus next radius along new axis
      const end = { x: center.x + r * Math.cos(a1), y: center.y + r * Math.sin(a1) };
      const nr = r * phi;
      const na = a1;
      center = { x: end.x - nr * Math.cos(na), y: end.y - nr * Math.sin(na) };
      r = nr; ang = a1;
    }
    return figs;
  });
  // Fib Channel (3 pts): base line A→B, parallel lines offset by fib × (base→C width).
  reg("fibChannel", 3, (c) => {
    const [A, B] = c;
    if (c.length < 3) return [line([A, ray(A, B)], STRUCT, 1.5),
      line([B, ray(B, A)], STRUCT, 1.5)];
    const C = c[2];
    const d = sub(B, A), m = len(d);
    const nrm = { x: -d.y / m, y: d.x / m };          // unit normal to the base line
    const width = (C.x - A.x) * nrm.x + (C.y - A.y) * nrm.y; // signed distance to C
    const figs = [];
    FIB.forEach((f) => {
      const off = mul(nrm, width * f.r);
      const p0 = add(A, off), p1 = add(B, off);
      figs.push(line([ray(p1, p0), ray(p0, p1)], f.c, f.r === 0 || f.r === 1 ? 1.5 : 1));
      figs.push(label(add(p0, mul(sub(p1, p0), 0.04)), faNum(f.r.toFixed(3)), f.c));
    });
    return figs;
  });
  // Fib Wedge (3 pts): fan of fib rays between two bounding rays A→B and A→C.
  reg("fibWedge", 3, (c) => {
    const [A, B] = c;
    if (c.length < 3) return [line([A, ray(A, B)], STRUCT, 1.5)];
    const C = c[2];
    const R = (len(sub(B, A)) + len(sub(C, A))) / 2;
    const aB = Math.atan2(B.y - A.y, B.x - A.x);
    const aC = Math.atan2(C.y - A.y, C.x - A.x);
    const figs = [line([A, ray(A, B)], STRUCT, 1.5), line([A, ray(A, C)], STRUCT, 1.5)];
    FIB.filter((f) => f.r > 0 && f.r < 1.7).forEach((f) => {
      const a = aB + (aC - aB) * (f.r > 1 ? 1 : f.r);
      figs.push(line([A, ray(A, { x: A.x + Math.cos(a), y: A.y + Math.sin(a) })], f.c, 1));
      figs.push(arc(A, R * (f.r > 1 ? 1 : f.r), Math.min(aB, aC), Math.max(aB, aC), f.c, 1));
    });
    return figs;
  });

  // expose the list so chart.js can verify names exist
  window.BN_CUSTOM_OVERLAYS = [
    "andrewsPitchfork", "schiffPitchfork", "modifiedSchiffPitchfork",
    "insidePitchfork", "pitchfan", "gannFan", "gannBox", "gannSquare",
    "fibExtension", "fibSpeedResistanceFan", "fibSpeedResistanceArcs",
    "fibCircles", "fibTimeZone", "fibTrendTime", "fibSpiral",
    "fibChannel", "fibWedge",
  ];
})();

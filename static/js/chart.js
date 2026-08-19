/* chart.js — نمودار حرفه‌ای نماد (کندل/خطی + اندیکاتورها + ابزار ترسیم فیبوناچی…)
   Professional per-security chart built on the vendored KLineChart engine
   (static/js/vendor/klinecharts.min.js). Provides:
     • candlestick / OHLC bar / area(line) chart types
     • built-in indicators: MA, EMA, BOLL, SAR (main) + VOL, MACD, RSI, KDJ (sub)
     • a left drawing toolbar (Fibonacci retracement, trend line, ray, horizontal
       / vertical line, price channel, rectangle) + clear-all
     • range presets (۱م…کل) and Jalali (شمسی) axis / tooltip labels
   Data comes from /api/ohlc/<kind>/<id>. No external network at runtime. */
(function () {
  const FA = "۰۱۲۳۴۵۶۷۸۹";
  const faDigits = (s) => String(s).replace(/[0-9]/g, (d) => FA[d]);
  const faNum = (n, dp = 0) =>
    n == null || isNaN(n) ? "—" : faDigits(Number(n).toLocaleString("en-US", { maximumFractionDigits: dp }));
  const faCompact = (n) => {
    if (n == null || isNaN(n)) return "—";
    const a = Math.abs(n);
    if (a >= 1e12) return faDigits((n / 1e12).toFixed(2)) + " هزار میلیارد";
    if (a >= 1e9) return faDigits((n / 1e9).toFixed(2)) + " میلیارد";
    if (a >= 1e6) return faDigits((n / 1e6).toFixed(2)) + " میلیون";
    return faNum(n);
  };

  // ---- Jalali label: we ship jdate per candle, keyed by timestamp ----
  const tsToJ = new Map();
  function jLabel(ts) {
    const j = tsToJ.get(ts);
    if (j) return faDigits(j);
    const d = new Date(ts);
    return faDigits(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`);
  }

  // ---- TradingView design tokens ----
  // Two sets, because the chart is drawn on a <canvas>: it cannot inherit the
  // page's CSS variables the way the rest of the app does, so a dark theme
  // would otherwise leave one blazing white rectangle in the middle of an
  // otherwise dark page — and grid lines nobody can see.
  //
  // The candle colours are deliberately the SAME in both. They are the only
  // thing on screen carrying meaning rather than decoration, and a trader who
  // switches theme must not have to re-learn which colour is a gain.
  const C_LIGHT = {
    up: "#26a69a", down: "#ef5350", brand: "#2962ff",
    ink: "#131722", muted: "#787b86", line: "#e0e3eb", bg: "#ffffff",
    cross: "#9598a1", crossBg: "#131722", axisBg: "#f8f9fd",
  };
  const C_DARK = {
    up: "#26a69a", down: "#ef5350", brand: "#5b8dff",
    ink: "#e7eef3", muted: "#8f9bab", line: "#2a3946", bg: "#16232c",
    cross: "#8f9bab", crossBg: "#e7eef3", axisBg: "#1b2932",
  };
  const DARK_THEMES = { dark: 1, midnight: 1, graphite: 1 };
  const C = Object.assign({}, C_LIGHT);

  function readTheme() {
    const id = document.documentElement.getAttribute("data-theme") || "light";
    Object.assign(C, DARK_THEMES[id] ? C_DARK : C_LIGHT);
    // The crosshair label is a filled box: its text has to invert with the box.
    C.crossInk = DARK_THEMES[id] ? "#16232c" : "#ffffff";
  }
  readTheme();

  function chartStyles() {
    readTheme();
    return {
      grid: {
        horizontal: { color: C.line, style: "solid", size: 1 },
        vertical: { color: C.line, style: "solid", size: 1 },
      },
      candle: {
        type: "candle_solid",
        bar: {
          upColor: C.up, downColor: C.down, noChangeColor: C.muted,
          upBorderColor: C.up, downBorderColor: C.down, noChangeBorderColor: C.muted,
          upWickColor: C.up, downWickColor: C.down, noChangeWickColor: C.muted,
        },
        priceMark: {
          high: { color: C.muted }, low: { color: C.muted },
          last: {
            upColor: C.up, downColor: C.down, noChangeColor: C.muted,
            line: { show: true, style: "dashed", dashedValue: [4, 4], size: 1 },
            text: { color: C.crossInk, size: 12, paddingLeft: 4, paddingRight: 4, borderRadius: 2 },
          },
        },
        tooltip: {
          showRule: "always", showType: "standard", custom: candleTooltip,
          text: { size: 12, family: "Tahoma", color: C.ink, marginTop: 8, marginLeft: 10 },
        },
      },
      indicator: {
        tooltip: { showRule: "always", text: { size: 12, family: "Tahoma", color: C.muted } },
        lastValueMark: { show: false },
      },
      xAxis: {
        axisLine: { color: C.line },
        tickLine: { color: C.line },
        tickText: { color: C.muted, size: 12, family: "Tahoma" },
      },
      yAxis: {
        type: "normal", position: "right",
        axisLine: { color: C.line },
        tickLine: { color: C.line },
        tickText: { color: C.muted, size: 12, family: "Tahoma" },
      },
      crosshair: {
        horizontal: {
          line: { color: C.cross, style: "dashed", dashedValue: [4, 2], size: 1 },
          text: { backgroundColor: C.crossBg, color: C.crossInk, size: 12, borderRadius: 2 },
        },
        vertical: {
          line: { color: C.cross, style: "dashed", dashedValue: [4, 2], size: 1 },
          text: { backgroundColor: C.crossBg, color: C.crossInk, size: 12, borderRadius: 2 },
        },
      },
    };
  }

  // localized candle legend (O/L/H + last trade + final + change% + volume/value)
  //
  // Naming, which the rest of the app follows: `close` (adj_close) is «آخرین
  // معامله» — the last trade of the session — and `final` (adj_final) is
  // «پایانی», the volume-weighted close. They are different numbers on the TSE
  // and this legend used to label `close` as «پایانی».
  function candleTooltip(data) {
    const c = data.current, p = data.prev || {};
    // Change is measured on «پایانی», the same basis as every return in this
    // app (db.py computes all of them from adj_final). Falls back to the last
    // trade only when a row carries no final at all.
    const cf = c.final != null ? c.final : c.close;
    const pf = p.final != null ? p.final : p.close;
    const chg = pf ? ((cf - pf) / pf) * 100 : null;
    const chgTxt = chg == null ? "—" : (chg >= 0 ? "+" : "") + faDigits(chg.toFixed(2)) + "٪";
    const color = chg == null ? C.muted : chg >= 0 ? C.up : C.down;
    return [
      { title: "باز", value: faNum(c.open) },
      { title: "کمترین", value: faNum(c.low) },
      { title: "بیشترین", value: faNum(c.high) },
      { title: "آخرین معامله", value: faNum(c.close) },
      { title: "پایانی", value: faNum(cf) },
      { title: "تغییر", value: { text: chgTxt, color } },
      { title: "حجم", value: faCompact(c.volume) },
      { title: "ارزش", value: faCompact(c.turnover) },
    ];
  }

  // ---- drawing tools (left rail) ----
  // Grouped TradingView-style: each rail icon opens a flyout submenu of related
  // tools; picking one activates it and becomes that group's shown icon.
  const TOOL_GROUPS = [
    {
      key: "cursor", label: "نشانگر",
      tools: [{ key: "cursor", overlay: null, label: "نشانگر", icon: "cursor" }],
    },
    {
      key: "trend", label: "خطوط روند",
      tools: [
        { key: "segment", overlay: "segment", label: "پاره‌خط", icon: "segment" },
        { key: "straightLine", overlay: "straightLine", label: "خط مستقیم", icon: "straightLine" },
        { key: "rayLine", overlay: "rayLine", label: "پرتو", icon: "rayLine" },
        { key: "priceLine", overlay: "priceLine", label: "خط قیمت", icon: "priceLine" },
      ],
    },
    {
      key: "hv", label: "خطوط افقی و عمودی",
      tools: [
        { key: "horizontalStraightLine", overlay: "horizontalStraightLine", label: "خط افقی", icon: "horizontalStraightLine" },
        { key: "horizontalRayLine", overlay: "horizontalRayLine", label: "پرتو افقی", icon: "horizontalRayLine" },
        { key: "horizontalSegment", overlay: "horizontalSegment", label: "پاره‌خط افقی", icon: "horizontalSegment" },
        { key: "verticalStraightLine", overlay: "verticalStraightLine", label: "خط عمودی", icon: "verticalStraightLine" },
        { key: "verticalRayLine", overlay: "verticalRayLine", label: "پرتو عمودی", icon: "verticalRayLine" },
        { key: "verticalSegment", overlay: "verticalSegment", label: "پاره‌خط عمودی", icon: "verticalSegment" },
      ],
    },
    {
      key: "channel", label: "کانال‌ها",
      tools: [
        { key: "parallelStraightLine", overlay: "parallelStraightLine", label: "خطوط موازی", icon: "parallelStraightLine" },
        { key: "priceChannelLine", overlay: "priceChannelLine", label: "کانال قیمت", icon: "priceChannelLine" },
      ],
    },
    {
      key: "pitchfork", label: "پیچ‌فورک",
      tools: [
        { key: "andrewsPitchfork", overlay: "andrewsPitchfork", label: "پیچ‌فورک", icon: "andrewsPitchfork" },
        { key: "schiffPitchfork", overlay: "schiffPitchfork", label: "پیچ‌فورک شیف", icon: "schiffPitchfork" },
        { key: "modifiedSchiffPitchfork", overlay: "modifiedSchiffPitchfork", label: "پیچ‌فورک شیف اصلاح‌شده", icon: "modifiedSchiffPitchfork" },
        { key: "insidePitchfork", overlay: "insidePitchfork", label: "پیچ‌فورک داخلی", icon: "insidePitchfork" },
        { key: "pitchfan", overlay: "pitchfan", label: "پیچ‌فن", icon: "pitchfan" },
      ],
    },
    {
      key: "gann", label: "گان",
      tools: [
        { key: "gannBox", overlay: "gannBox", label: "جعبهٔ گان", icon: "gannBox" },
        { key: "gannSquare", overlay: "gannSquare", label: "مربع گان", icon: "gannSquare" },
        { key: "gannFan", overlay: "gannFan", label: "بادبزن گان", icon: "gannFan" },
      ],
    },
    {
      key: "fib", label: "فیبوناچی",
      tools: [
        { key: "fibonacciLine", overlay: "fibonacciLine", label: "اصلاحی فیبوناچی", icon: "fibonacciLine" },
        { key: "fibExtension", overlay: "fibExtension", label: "گسترش فیبوناچی (روند)", icon: "fibExtension" },
        { key: "fibSpeedResistanceFan", overlay: "fibSpeedResistanceFan", label: "بادبزن مقاومت فیبوناچی", icon: "fibSpeedResistanceFan" },
        { key: "fibTimeZone", overlay: "fibTimeZone", label: "منطقهٔ زمانی فیبوناچی", icon: "fibTimeZone" },
        { key: "fibTrendTime", overlay: "fibTrendTime", label: "زمان فیبوناچی (روند)", icon: "fibTrendTime" },
        { key: "fibCircles", overlay: "fibCircles", label: "دایره‌های فیبوناچی", icon: "fibCircles" },
        { key: "fibSpiral", overlay: "fibSpiral", label: "مارپیچ فیبوناچی", icon: "fibSpiral" },
        { key: "fibSpeedResistanceArcs", overlay: "fibSpeedResistanceArcs", label: "کمان‌های مقاومت فیبوناچی", icon: "fibSpeedResistanceArcs" },
        { key: "fibWedge", overlay: "fibWedge", label: "گوه فیبوناچی", icon: "fibWedge" },
        { key: "fibChannel", overlay: "fibChannel", label: "کانال فیبوناچی", icon: "fibChannel" },
      ],
    },
    {
      key: "shape", label: "اشکال",
      tools: [
        { key: "circle", overlay: "circle", label: "دایره", icon: "circle" },
        { key: "rect", overlay: "rect", label: "مستطیل", icon: "rect" },
      ],
    },
  ];

  // TradingView-style monochrome line icons (24×24, stroke = currentColor)
  const ICON_PATHS = {
    cursor: '<circle cx="12" cy="12" r="2.2"/><path d="M12 3v4M12 17v4M3 12h4M17 12h4"/>',
    fibonacciLine: '<path d="M3 5h18M3 10h18M3 14h18M3 19h18"/><path d="M4 19L20 5"/>',
    segment: '<path d="M6 18L18 6"/><circle cx="6" cy="18" r="1.7"/><circle cx="18" cy="6" r="1.7"/>',
    straightLine: '<path d="M4 20L20 4"/><circle cx="9" cy="15" r="1.5"/><circle cx="15" cy="9" r="1.5"/>',
    rayLine: '<path d="M6 18L20 4"/><circle cx="6" cy="18" r="1.7"/>',
    parallelStraightLine: '<path d="M4 15L15 4M9 20L20 9"/>',
    priceChannelLine: '<path d="M4 15L15 4M9 20L20 9"/><path d="M6.5 17.5L17.5 6.5" stroke-dasharray="2 2"/>',
    horizontalStraightLine: '<path d="M3 12h18"/>',
    horizontalRayLine: '<path d="M6 12h15"/><circle cx="6" cy="12" r="1.7"/>',
    horizontalSegment: '<path d="M6 12h12"/><circle cx="6" cy="12" r="1.7"/><circle cx="18" cy="12" r="1.7"/>',
    verticalStraightLine: '<path d="M12 3v18"/>',
    verticalRayLine: '<path d="M12 3v15"/><circle cx="12" cy="18" r="1.7"/>',
    verticalSegment: '<path d="M12 6v12"/><circle cx="12" cy="6" r="1.7"/><circle cx="12" cy="18" r="1.7"/>',
    priceLine: '<path d="M3 12h18" stroke-dasharray="3 3"/>',
    circle: '<circle cx="12" cy="12" r="8"/>',
    rect: '<rect x="4" y="6" width="16" height="12" rx="1"/>',
    clear: '<path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/>',
    // pitchfork family
    andrewsPitchfork: '<path d="M4 20L14 6M9 13l7 3M9 13l3 7M20 3l-6 3"/>',
    schiffPitchfork: '<path d="M4 20L14 8M9 14l7 2M10 12l3 7M20 4l-6 4"/>',
    modifiedSchiffPitchfork: '<path d="M5 20L14 9M9 15l7 1M11 12l2 7M20 5l-6 4"/>',
    insidePitchfork: '<path d="M4 20L14 6M10 13l6 2M10 13l2 6" stroke-dasharray="2 2"/><path d="M4 20L14 6"/>',
    pitchfan: '<path d="M4 20L20 4M4 20L20 9M4 20L20 14M4 20L18 18"/>',
    // gann family
    gannFan: '<path d="M4 20L20 4M4 20h16M4 20L14 4M4 20L20 12"/>',
    gannBox: '<rect x="4" y="4" width="16" height="16"/><path d="M4 12h16M12 4v16M4 20L20 4"/>',
    gannSquare: '<rect x="5" y="5" width="14" height="14"/><path d="M5 19L19 5M5 5l14 14"/>',
    // fibonacci family
    fibExtension: '<path d="M3 5h18M3 12h18M3 19h18"/><path d="M6 5v14"/>',
    fibSpeedResistanceFan: '<path d="M4 20V4M4 20h16M4 20L20 6M4 20L16 4M4 20L20 12"/>',
    fibSpeedResistanceArcs: '<path d="M4 20a5 5 0 0 1 5-5M4 20a10 10 0 0 1 10-10M4 20a15 15 0 0 1 15-15"/>',
    fibCircles: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="10.5"/>',
    fibTimeZone: '<path d="M4 4v16M8 4v16M14 4v16M21 4v16"/>',
    fibTrendTime: '<path d="M4 20L10 12" stroke-dasharray="2 2"/><path d="M10 4v16M15 4v16M21 4v16"/>',
    fibSpiral: '<path d="M13 12a3 3 0 1 0-3 3 5 5 0 0 0 5-5 7 7 0 0 0-7-7"/>',
    fibChannel: '<path d="M3 18L21 8M3 21L21 11M3 15L21 5"/>',
    fibWedge: '<path d="M4 20L20 6M4 20L20 16M4 20a12 12 0 0 1 12-6" /><path d="M4 20a16 16 0 0 1 14-3"/>',
  };

  function svgIcon(name) {
    return `<svg viewBox="0 0 24 24" class="tvi" fill="none" stroke="currentColor" ` +
      `stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name] || ""}</svg>`;
  }

  // main-pane vs sub-pane indicators (full KLineChart built-in set)
  const MAIN_IND = ["MA", "EMA", "SMA", "BOLL", "BBI", "SAR"];
  const SUB_IND = ["VOL", "MACD", "RSI", "KDJ", "BIAS", "BRAR", "CCI", "DMI",
    "CR", "PSY", "DMA", "TRIX", "OBV", "VR", "WR", "MTM", "EMV", "ROC", "PVT", "AO"];
  const IND_LABEL = {
    MA: "میانگین متحرک", EMA: "EMA", SMA: "SMA", BOLL: "بولینگر",
    BBI: "BBI", SAR: "سار",
    VOL: "حجم", MACD: "MACD", RSI: "RSI", KDJ: "KDJ", BIAS: "بایاس",
    BRAR: "BRAR", CCI: "CCI", DMI: "DMI", CR: "CR", PSY: "PSY", DMA: "DMA",
    TRIX: "TRIX", OBV: "OBV", VR: "VR", WR: "ویلیامز", MTM: "مومنتوم",
    EMV: "EMV", ROC: "ROC", PVT: "PVT", AO: "AO",
  };

  const RANGES = [
    { n: 22, label: "۱م" }, { n: 66, label: "۳م" }, { n: 132, label: "۶م" },
    { n: 250, label: "۱س" }, { n: 750, label: "۳س" }, { n: 0, label: "کل" },
  ];

  function button(label, title, cls) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cbtn" + (cls ? " " + cls : "");
    b.textContent = label;
    if (title) b.title = title;
    return b;
  }

  async function mount(rootId, kind, entityId, historyId) {
    const root = document.getElementById(rootId);
    if (!root || !window.klinecharts) return;

    // ---- scaffold DOM ----
    root.classList.add("bnchart");
    root.innerHTML = `
      <div class="bnc-top">
        <div class="bnc-group" data-role="types"></div>
        <div class="bnc-group" data-role="ranges"></div>
        <div class="bnc-group bnc-dd" data-role="inds"></div>
        <div class="bnc-group bnc-right" data-role="actions"></div>
      </div>
      <div class="bnc-body">
        <div class="bnc-tools" data-role="tools"></div>
        <div class="bnc-canvas" id="${rootId}-canvas"></div>
      </div>
      <div class="bnc-status" data-role="status">در حال بارگذاری داده…</div>`;

    const status = root.querySelector('[data-role="status"]');
    let data;
    try {
      const res = await fetch(`/api/ohlc/${kind}/${entityId}`);
      data = await res.json();
    } catch (e) {
      status.textContent = "خطا در دریافت داده."; return;
    }
    const candles = data.candles || [];
    if (!candles.length) { status.textContent = "دادهٔ قیمتی کافی موجود نیست."; return; }
    candles.forEach((c) => tsToJ.set(c.timestamp, c.jdate));
    status.remove();

    const chart = klinecharts.init(`${rootId}-canvas`);
    chart.setStyles(chartStyles());
    chart.setCustomApi({ formatDate: (_f, ts) => jLabel(ts) });

    const state = { all: candles, activeInds: {}, activeTool: "cursor" };
    applyRange(chart, state, 250);

    buildTypeButtons(root, chart);
    buildRangeButtons(root, chart, state);
    buildIndicatorMenu(root, chart, state);
    buildDrawTools(root, chart, state);
    buildActions(root, chart);

    // volume shown by default (professional default)
    toggleIndicator(chart, state, "VOL", true);
    syncIndButtons(root, state);

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(root.querySelector(".bnc-canvas"));
    window.BNChartInstance = chart;

    // Repaint the canvas when the theme changes. Without this the chart keeps
    // the palette it was built with until the next navigation, which is exactly
    // the moment a user is looking at it — they pressed the theme button while
    // reading this page.
    document.addEventListener("bn:prefs", () => {
      try { chart.setStyles(chartStyles()); } catch (e) { /* chart disposed */ }
    });

    if (historyId) renderHistory(historyId, data);
  }

  // ---- raw data-history table (newest first) + CSV export ----
  function renderHistory(historyId, data) {
    const host = document.getElementById(historyId);
    if (!host) return;
    const rows = (data.candles || []).slice();
    if (!rows.length) { host.innerHTML = '<p class="muted">دادهٔ تاریخی موجود نیست.</p>'; return; }

    // «پایانی» (adj_final) per row, falling back to the last trade when a row
    // carries no final — _fin is what both the change column and the CSV use.
    for (let i = 0; i < rows.length; i++) {
      rows[i]._fin = rows[i].final != null ? rows[i].final : rows[i].close;
    }
    // precompute day-over-day change on the chronological series. It is measured
    // on «پایانی», not on the last trade, so it agrees with every return the
    // rest of the app reports (db.py computes all of those from adj_final).
    for (let i = 0; i < rows.length; i++) {
      const prev = i > 0 ? rows[i - 1]._fin : null;
      rows[i]._chg = prev ? ((rows[i]._fin - prev) / prev) * 100 : null;
    }
    const desc = rows.slice().reverse(); // newest first for display

    const head = `<tr>
        <th>تاریخ</th><th>باز</th><th>کمترین</th><th>بیشترین</th>
        <th>آخرین معامله</th><th>پایانی</th><th>تغییر</th>
        <th>حجم</th><th>ارزش</th></tr>`;

    const render = (limit) => desc.slice(0, limit).map((r) => {
      const chg = r._chg;
      const cls = chg == null ? "" : chg >= 0 ? "up" : "down";
      const chgTxt = chg == null ? "—" : (chg >= 0 ? "+" : "") + faDigits(chg.toFixed(2)) + "٪";
      return `<tr>
        <td class="jd">${faDigits(r.jdate)}</td>
        <td class="num">${faNum(r.open)}</td>
        <td class="num">${faNum(r.low)}</td>
        <td class="num">${faNum(r.high)}</td>
        <td class="num">${faNum(r.close)}</td>
        <td class="num strong">${faNum(r._fin)}</td>
        <td class="num ${cls}">${chgTxt}</td>
        <td class="num">${faCompact(r.volume)}</td>
        <td class="num">${faCompact(r.turnover)}</td>
      </tr>`;
    }).join("");

    // Page size comes from the user's «تعداد ردیف در هر صفحه» setting; the 120
    // that used to be hard-coded here is the fallback for a page rendered
    // before theme.js has published the preferences (and for the anonymous
    // case, which keeps the historical behaviour).
    const pageSize = (window.BN_PREFS && parseInt(window.BN_PREFS.rows_per_page, 10)) || 120;
    let shown = Math.min(pageSize, desc.length);
    host.innerHTML = `
      <div class="hist-bar">
        <span class="muted small">${faDigits(rows.length)} روز معاملاتی</span>
        <button type="button" class="cbtn" data-role="csv">دریافت CSV</button>
      </div>
      <div class="hist-scroll"><table class="grid hist-table">
        <thead>${head}</thead><tbody data-role="hbody">${render(shown)}</tbody>
      </table></div>
      ${desc.length > shown ? '<div class="hist-more"><button type="button" class="cbtn" data-role="more">نمایش بیشتر</button></div>' : ""}`;

    const more = host.querySelector('[data-role="more"]');
    if (more) more.onclick = () => {
      shown = Math.min(shown + pageSize, desc.length);
      host.querySelector('[data-role="hbody"]').innerHTML = render(shown);
      if (shown >= desc.length) more.parentElement.remove();
    };
    host.querySelector('[data-role="csv"]').onclick = () => exportCsv(data.ticker, desc);
  }

  function exportCsv(ticker, rows) {
    // Same column order as the table on screen. change_pct is final-based.
    const head = ["j_date", "open", "low", "high", "close", "final", "change_pct", "volume", "value"];
    const lines = [head.join(",")];
    rows.forEach((r) => {
      lines.push([r.jdate, r.open, r.low, r.high, r.close, r._fin,
        r._chg == null ? "" : r._chg.toFixed(2), r.volume, r.turnover].join(","));
    });
    const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `${ticker}_history.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }

  function applyRange(chart, state, n) {
    const d = n && n < state.all.length ? state.all.slice(-n) : state.all;
    chart.applyNewData(d);
  }

  function buildTypeButtons(root, chart) {
    const wrap = root.querySelector('[data-role="types"]');
    const types = [
      { key: "candle_solid", label: "کندل" },
      { key: "ohlc", label: "میله‌ای" },
      { key: "area", label: "خطی" },
    ];
    types.forEach((t, i) => {
      const b = button(t.label, "نوع نمودار: " + t.label);
      if (i === 0) b.classList.add("on");
      b.onclick = () => {
        wrap.querySelectorAll(".cbtn").forEach((x) => x.classList.remove("on"));
        b.classList.add("on");
        chart.setStyles({ candle: { type: t.key } });
      };
      wrap.appendChild(b);
    });
  }

  function buildRangeButtons(root, chart, state) {
    const wrap = root.querySelector('[data-role="ranges"]');
    RANGES.forEach((r) => {
      const b = button(r.label, "بازهٔ زمانی");
      if (r.n === 250) b.classList.add("on");
      b.onclick = () => {
        wrap.querySelectorAll(".cbtn").forEach((x) => x.classList.remove("on"));
        b.classList.add("on");
        applyRange(chart, state, r.n);
      };
      wrap.appendChild(b);
    });
  }

  // TradingView-style indicator picker: a dropdown grouped into main-pane
  // (drawn over the candles) and sub-pane (separate panel) indicators.
  function buildIndicatorMenu(root, chart, state) {
    const host = root.querySelector('[data-role="inds"]');
    host.innerHTML = `
      <button type="button" class="cbtn" data-role="inds-btn">اندیکاتورها ▾</button>
      <div class="bnc-menu" data-role="inds-menu" hidden></div>`;
    const btn = host.querySelector('[data-role="inds-btn"]');
    const menu = host.querySelector('[data-role="inds-menu"]');

    const section = (title, names) =>
      `<div class="bnc-menu-sec">${title}</div>` + names.map((name) => `
        <button type="button" class="bnc-mi" data-ind="${name}">
          <span class="bnc-mi-check">✓</span>
          <span class="bnc-mi-label">${IND_LABEL[name] || name}</span>
          <span class="bnc-mi-code">${name}</span>
        </button>`).join("");
    menu.innerHTML = section("روی نمودار اصلی", MAIN_IND) + section("پنل جداگانه", SUB_IND);

    menu.querySelectorAll("[data-ind]").forEach((mi) => {
      mi.onclick = () => {
        const name = mi.dataset.ind;
        toggleIndicator(chart, state, name, !state.activeInds[name]);
        mi.classList.toggle("on", !!state.activeInds[name]);
        updateIndBadge(root, state);
      };
    });

    btn.onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; };
    document.addEventListener("click", (e) => { if (!host.contains(e.target)) menu.hidden = true; });
  }

  function updateIndBadge(root, state) {
    const btn = root.querySelector('[data-role="inds-btn"]');
    if (!btn) return;
    const n = Object.keys(state.activeInds).length;
    btn.textContent = n ? `اندیکاتورها (${faDigits(n)}) ▾` : "اندیکاتورها ▾";
    btn.classList.toggle("on", n > 0);
  }

  function toggleIndicator(chart, state, name, on) {
    if (on) {
      const main = MAIN_IND.includes(name);
      const paneId = chart.createIndicator(name, true, main ? { id: "candle_pane" } : undefined);
      state.activeInds[name] = main ? "candle_pane" : paneId;
    } else if (state.activeInds[name]) {
      chart.removeIndicator(state.activeInds[name], name);
      delete state.activeInds[name];
    }
  }

  function syncIndButtons(root, state) {
    root.querySelectorAll("[data-ind]").forEach((b) => {
      b.classList.toggle("on", !!state.activeInds[b.dataset.ind]);
    });
    updateIndBadge(root, state);
  }

  // right-aligned actions: true full-screen (Fullscreen API) toggle
  function buildActions(root, chart) {
    const host = root.querySelector('[data-role="actions"]');
    const fs = button("⤢ تمام‌صفحه", "نمایش تمام‌صفحه");
    fs.onclick = () => {
      try {
        if (!document.fullscreenElement) root.requestFullscreen();
        else document.exitFullscreen();
      } catch (e) { /* ignore */ }
    };
    host.appendChild(fs);
    document.addEventListener("fullscreenchange", () => {
      const on = document.fullscreenElement === root;
      root.classList.toggle("is-fs", on);
      fs.textContent = on ? "⤡ خروج از تمام‌صفحه" : "⤢ تمام‌صفحه";
      fs.classList.toggle("on", on);
      setTimeout(() => { try { chart.resize(); } catch (e) { /* ignore */ } }, 80);
    });
  }

  function divider(wrap) {
    const d = document.createElement("div");
    d.className = "tool-div";
    wrap.appendChild(d);
  }

  // icon-only left rail (TradingView style): every multi-tool group icon opens a
  // flyout submenu; single-tool groups (cursor) activate directly on click.
  function buildDrawTools(root, chart, state) {
    const wrap = root.querySelector('[data-role="tools"]');
    wrap.innerHTML = "";

    let openFlyout = null;
    const closeFlyout = () => { if (openFlyout) { openFlyout.remove(); openFlyout = null; } };
    document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) closeFlyout(); });

    const clearActive = () => wrap.querySelectorAll(".toolbtn").forEach((x) => x.classList.remove("on"));

    TOOL_GROUPS.forEach((g, gi) => {
      const multi = g.tools.length > 1;
      const cell = document.createElement("div");
      cell.className = "toolcell";

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "toolbtn" + (g.key === "cursor" ? " on" : "");
      let current = g.tools[0];

      const render = () => {
        btn.innerHTML = svgIcon(current.icon) + (multi ? '<span class="tool-caret"></span>' : "");
        btn.title = current.label;
      };
      render();

      const activate = (tool) => {
        clearActive();
        btn.classList.add("on");
        current = tool;
        render();
        state.activeTool = tool.key;
        if (tool.overlay) chart.createOverlay({ name: tool.overlay });
      };

      const openGroupFlyout = () => {
        const fly = document.createElement("div");
        fly.className = "tool-flyout";
        fly._group = g.key;
        const title = document.createElement("div");
        title.className = "tool-flyout-title";
        title.textContent = g.label;
        fly.appendChild(title);
        g.tools.forEach((t) => {
          const item = document.createElement("button");
          item.type = "button";
          item.className = "tool-fly-item" + (t.key === current.key ? " on" : "");
          item.innerHTML = svgIcon(t.icon) + `<span class="tfl-label">${t.label}</span>`;
          item.onclick = (e) => { e.stopPropagation(); activate(t); closeFlyout(); };
          fly.appendChild(item);
        });
        cell.appendChild(fly);
        openFlyout = fly;
      };

      btn.onclick = (e) => {
        e.stopPropagation();
        if (!multi) { closeFlyout(); activate(current); return; }
        const wasOpen = openFlyout && openFlyout._group === g.key;
        closeFlyout();
        if (!wasOpen) openGroupFlyout();
      };

      cell.appendChild(btn);
      wrap.appendChild(cell);
      if (gi === 0) divider(wrap); // split the pointer from the drawing tools
    });

    divider(wrap);
    // clear-all
    const cell = document.createElement("div");
    cell.className = "toolcell";
    const clr = document.createElement("button");
    clr.type = "button";
    clr.className = "toolbtn danger";
    clr.innerHTML = svgIcon("clear");
    clr.title = "حذف همهٔ ترسیم‌ها";
    clr.onclick = () => {
      closeFlyout();
      chart.removeOverlay();
      clearActive();
      wrap.querySelector(".toolbtn").classList.add("on"); // back to cursor
      state.activeTool = "cursor";
    };
    cell.appendChild(clr);
    wrap.appendChild(cell);
  }

  // history-only entry: renders the OHLCV table + CSV without a chart engine
  // (the technical tab is served by the TradingView UDF datafeed in tv.py)
  async function history(historyId, kind, entityId) {
    const host = document.getElementById(historyId);
    if (!host) return;
    try {
      const res = await fetch(`/api/ohlc/${kind}/${entityId}`);
      renderHistory(historyId, await res.json());
    } catch (e) {
      host.innerHTML = '<p class="muted">خطا در دریافت داده.</p>';
    }
  }

  window.BNChart = { mount, history, faNum, faCompact, faDigits };
})();

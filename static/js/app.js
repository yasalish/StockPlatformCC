/* app.js — بورس‌نگار front-end: global search, sortable/filterable gainer table,
   and a self-contained SVG price chart (no external libraries). */
const BN = (function () {
  const FA = "۰۱۲۳۴۵۶۷۸۹";
  const faDigits = (s) => String(s).replace(/[0-9]/g, (d) => FA[d]);
  const fmt = (n) => faDigits(Math.round(n || 0).toLocaleString("en-US"));
  const SVGNS = "http://www.w3.org/2000/svg";

  function el(tag, attrs, txt) {
    const n = document.createElementNS(SVGNS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (txt != null) n.textContent = txt;
    return n;
  }

  /* ---- global symbol search (stocks + ETFs) ---- */
  function initSearch() {
    const input = document.getElementById("search");
    const box = document.getElementById("search-results");
    if (!input) return;
    let t;
    input.addEventListener("input", () => {
      clearTimeout(t);
      const q = input.value.trim();
      if (!q) { box.style.display = "none"; return; }
      t = setTimeout(async () => {
        const res = await fetch("/api/search?q=" + encodeURIComponent(q));
        const rows = await res.json();
        box.innerHTML = rows.map((r) => {
          const href = (r.kind === "stock" ? "/stock/" : "/etf/") + r.id;
          const k = r.kind === "stock" ? "سهم" : "صندوق";
          return `<a href="${href}">
              <span><span class="sr-kind ${r.kind}">${k}</span>
              <span class="sr-sym">${r.ticker}</span></span>
              <span class="sr-sec">${r.sub || ""}</span></a>`;
        }).join("") || `<a class="sr-sec" style="justify-content:center">نتیجه‌ای یافت نشد</a>`;
        box.style.display = "block";
      }, 180);
    });
    document.addEventListener("click", (e) => {
      if (!e.target.closest(".search-wrap")) box.style.display = "none";
    });
  }

  /* ---- sortable + text-filterable table ---- */
  function initTable(tableId, filterInputId) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const tbody = table.tBodies[0];
    const rows = Array.from(tbody.rows);

    // text filter (by ticker)
    const fi = filterInputId && document.getElementById(filterInputId);
    if (fi) {
      fi.addEventListener("input", () => {
        const q = fi.value.trim();
        rows.forEach((r) => {
          const tk = r.dataset.ticker || "";
          r.style.display = (!q || tk.includes(q)) ? "" : "none";
        });
      });
    }

    // click-to-sort. A <thead> may be more than one row and its cells may span
    // rows/columns — the performance grid stacks a period name over its
    // سقف/کف pair — so walk the whole head as a grid and record the body column
    // each header cell STARTS at. That makes every header sortable: a spanning
    // one sorts by the first column it covers, a stacked one by its own column.
    const hrows = Array.from(table.tHead.rows);
    const taken = hrows.map(() => []);      // taken[row][col], filled in by spans
    const heads = [];                       // {th, col} in document order
    hrows.forEach((tr, ri) => {
      let ci = 0;
      Array.from(tr.cells).forEach((th) => {
        while (taken[ri][ci]) ci++;         // skip slots a cell above spilled into
        const cs = th.colSpan || 1, rs = th.rowSpan || 1;
        for (let r = ri; r < Math.min(ri + rs, hrows.length); r++)
          for (let c = ci; c < ci + cs; c++) taken[r][c] = true;
        heads.push({ th, col: ci });
        ci += cs;
      });
    });

    // «—» cells carry the templates' no-data sentinel; they belong at the bottom
    // in BOTH directions, not stacked on top as a fake −99999٪ record low.
    const MISSING = -99999;
    const numOf = (cell) => {
      const v = parseFloat(cell.dataset.v ?? cell.textContent);
      return (Number.isNaN(v) || v === MISSING) ? null : v;
    };

    heads.forEach(({ th, col }) => {
      const type = th.dataset.sort;
      if (!type) return;
      let dir = 0;
      th.addEventListener("click", () => {
        heads.forEach((h) => h.th.classList.remove("sorted-asc", "sorted-desc"));
        dir = dir === 1 ? -1 : 1;
        th.classList.add(dir === 1 ? "sorted-asc" : "sorted-desc");
        const visible = rows.slice();
        visible.sort((a, b) => {
          const ca = a.cells[col], cb = b.cells[col];
          if (type === "num") {
            const va = numOf(ca), vb = numOf(cb);
            if (va === null || vb === null)
              return va === vb ? 0 : (va === null ? 1 : -1);
            return (va - vb) * dir;
          }
          return ca.textContent.trim().localeCompare(cb.textContent.trim(), "fa") * dir;
        });
        visible.forEach((r) => tbody.appendChild(r));
      });
    });
  }

  /* ---- self-contained SVG price line chart (RTL: oldest on the right) ---- */
  function priceChart(containerId, labels, values) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = "";
    const n = values.length;
    if (!n) { container.textContent = "بدون داده"; return; }
    const w = container.clientWidth || 820, h = 300;
    const padL = 60, padR = 14, padT = 14, padB = 30;
    const plotW = w - padL - padR, plotH = h - padT - padB;
    let max = Math.max(...values), min = Math.min(...values);
    if (max === min) { max += 1; min -= 1; }
    const x = (i) => padL + plotW - (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const yy = (v) => padT + plotH - ((v - min) / (max - min)) * plotH;
    const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, width: "100%", height: h });

    for (let g = 0; g <= 4; g++) {
      const gy = padT + (g / 4) * plotH;
      svg.appendChild(el("line", { x1: padL, y1: gy, x2: w - padR, y2: gy, class: "axis", "stroke-width": .5 }));
      svg.appendChild(el("text", { x: padL - 6, y: gy + 4, "text-anchor": "end" }, fmt(max - (g / 4) * (max - min))));
    }
    // ~6 date labels
    const step = Math.max(1, Math.ceil(n / 6));
    labels.forEach((lb, i) => {
      if (i % step !== 0 && i !== n - 1) return;
      svg.appendChild(el("text", { x: x(i), y: h - 10, "text-anchor": "middle" }, faDigits(lb)));
    });

    // area + line
    const linePts = values.map((v, i) => `${x(i)},${yy(v)}`).join(" ");
    const areaPts = `${x(0)},${padT + plotH} ${linePts} ${x(n - 1)},${padT + plotH}`;
    svg.appendChild(el("polygon", { points: areaPts, fill: "rgba(47,109,179,.10)" }));
    svg.appendChild(el("polyline", { points: linePts, fill: "none", stroke: "#2f6db3", "stroke-width": 2, "stroke-linejoin": "round" }));
    container.appendChild(svg);
  }

  /* ---- security-detail tabs (technical / returns / history) ---- */
  function initTabs() {
    document.querySelectorAll('[data-role="sec-tabs"]').forEach((tabs) => {
      const btns = Array.from(tabs.querySelectorAll(".tab-btn"));
      const panes = Array.from(tabs.querySelectorAll(".tab-pane"));
      if (!btns.length) return;

      const activate = (key, push) => {
        if (!btns.some((b) => b.dataset.tab === key)) return;
        btns.forEach((b) => {
          const on = b.dataset.tab === key;
          b.classList.toggle("on", on);
          b.setAttribute("aria-selected", on ? "true" : "false");
        });
        panes.forEach((p) => p.classList.toggle("on", p.dataset.pane === key));
        // the pro chart is measured lazily; resize when its pane becomes visible
        if (window.BNChartInstance) {
          try { window.BNChartInstance.resize(); } catch (e) {}
        }
        if (push && history.replaceState) history.replaceState(null, "", "#" + key);
      };

      btns.forEach((b) => b.addEventListener("click", () => activate(b.dataset.tab, true)));

      const fromHash = (location.hash || "").replace(/^#/, "");
      if (fromHash) activate(fromHash, false);
    });
  }

  // ---------- Watchlist (دیده‌بان) ----------
  const FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
  function toFa(n) {
    return String(n).replace(/\d/g, (d) => FA_DIGITS[d]);
  }

  function updateWatchBadge(count) {
    const link = document.querySelector('.topnav a[href$="/watchlist"]');
    if (!link) return;
    let badge = link.querySelector(".nav-badge");
    if (count > 0) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "nav-badge";
        link.appendChild(document.createTextNode(" "));
        link.appendChild(badge);
      }
      badge.textContent = toFa(count);
    } else if (badge) {
      badge.remove();
    }
  }

  async function toggleWatch(btn) {
    const kind = btn.dataset.kind;
    const ticker = btn.dataset.ticker;
    const entity_id = btn.dataset.id || null;
    if (btn.dataset.busy) return;
    btn.dataset.busy = "1";
    try {
      const res = await fetch("/api/watchlist/toggle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, ticker, entity_id }),
      });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.ok) return;
      // Reflect the new state on every star for this symbol on the page.
      document
        .querySelectorAll(
          '.watch-star[data-kind="' + kind + '"][data-ticker="' + (window.CSS && CSS.escape ? CSS.escape(ticker) : ticker) + '"]'
        )
        .forEach((b) => b.classList.toggle("on", data.watched));
      updateWatchBadge(data.count);
      // On the watchlist page itself, an un-starred row disappears.
      if (!data.watched) {
        const row = btn.closest("tr");
        if (row && row.closest('[id^="wl-"]')) {
          row.remove();
          if (!document.querySelector('[id^="wl-"] tbody tr')) location.reload();
        }
      }
    } finally {
      delete btn.dataset.busy;
    }
  }

  /* ---- global navigation loader ----
     Pages are server-rendered, so submitting a filter form or clicking a link
     is a full navigation that can take a while (large group / long date range).
     Show a full-screen overlay the moment navigation starts so the user gets
     immediate "working…" feedback, and hide it if the page is restored from
     the back/forward cache. */
  function initNavLoader() {
    const overlay = document.getElementById("nav-loader");
    if (!overlay) return;
    let shown = false;
    const show = () => {
      if (shown) return;
      shown = true;
      overlay.classList.add("on");
    };
    const hide = () => {
      shown = false;
      overlay.classList.remove("on");
    };

    // form submits (filter bars, compare, calculate…)
    document.addEventListener("submit", (e) => {
      const f = e.target;
      if (f instanceof HTMLFormElement && !f.hasAttribute("data-no-loader")) show();
    });

    // selects that auto-submit via `this.form.submit()` don't fire the submit
    // event, so cover programmatic submits too
    const nativeSubmit = HTMLFormElement.prototype.submit;
    HTMLFormElement.prototype.submit = function () {
      if (!this.hasAttribute("data-no-loader")) show();
      return nativeSubmit.apply(this, arguments);
    };

    // real navigations via links (skip new-tab / modified clicks & anchors)
    document.addEventListener("click", (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey ||
          e.shiftKey || e.altKey) return;
      const a = e.target.closest && e.target.closest("a[href]");
      if (!a) return;
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || a.target === "_blank" ||
          a.hasAttribute("download") || /^(mailto:|tel:|javascript:)/i.test(href)) return;
      if (a.origin && a.origin !== location.origin) return;
      show();
    });

    // reset when the page is shown (incl. bfcache back/forward)
    window.addEventListener("pageshow", hide);
    window.addEventListener("pagehide", hide);
  }

  document.addEventListener("DOMContentLoaded", initSearch);
  document.addEventListener("DOMContentLoaded", initTabs);
  document.addEventListener("DOMContentLoaded", initNavLoader);
  return { initSearch, initTable, initTabs, priceChart, fmt, toggleWatch };
})();

/* app.js — بورس‌نگار front-end: global search, sortable/filterable gainer table,
   and a self-contained SVG price chart (no external libraries). */
const BN = (function () {
  // M-2. Persian formatting has ONE implementation now:
  // frontend/src/format.ts, published on window.BN_FORMAT by the
  // legacy-format bundle that base.html loads just above this file.
  //
  // The local fallbacks are not duplication for its own sake — they are what
  // keeps the nav, the search box and the chart working if that bundle fails
  // to load, on a page where a missing thousands separator is a cosmetic
  // problem and a thrown TypeError is a dead page. They are deliberately the
  // simplest possible version and are never the one that runs when the bundle
  // is present.
  const FA = "۰۱۲۳۴۵۶۷۸۹";
  const faDigits = (s) =>
    window.BN_FORMAT
      ? window.BN_FORMAT.toFaDigits(String(s))
      : String(s).replace(/[0-9]/g, (d) => FA[d]);
  const fmt = (n) =>
    window.BN_FORMAT
      ? window.BN_FORMAT.fa(Math.round(n || 0))
      : faDigits(Math.round(n || 0).toLocaleString("en-US"));
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

  /* ---- a table row opens its security in a NEW TAB ----

     The Jinja-rendered tables (watchlist, the dashboard's top lists) used to
     carry onclick="location.href=…", which replaced the list the user was
     reading. The Vue grids open a new tab now; these do the same, through one
     delegated handler rather than an inline attribute per row, so the rule
     lives in exactly one place.

     `data-href` on the <tr> is what marks a row as navigable. Clicks that
     something inside the row already owns — the watchlist star, and the ticker
     and name anchors, which are real links to the same place — are left alone,
     or the click would open the page twice. */
  function openInNewTab(href) {
    if (!href) return;
    // Not `window.open(href, "_blank", "noopener")`: that returns null even on
    // success, so a blocked popup could not be told from an opened tab and the
    // fallback below would fire on every click, navigating this tab as well.
    const win = window.open(href, "_blank");
    if (win) win.opener = null;
    else location.href = href;
  }

  function initRowLinks() {
    document.addEventListener("click", (e) => {
      if (e.defaultPrevented || e.button !== 0) return;
      const tr = e.target.closest && e.target.closest("tr.clickable[data-href]");
      if (!tr || e.target.closest("a, button, .watch-star")) return;
      openInNewTab(tr.dataset.href);
    });
  }

  /* ---- sortable + text-filterable table ---- */
  function initTable(tableId, filterInputId) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const tbody = table.tBodies[0];
    /*  Rows marked data-nosort are not records — they are attached to one, like
        the order-book drawer under each row of «تابلوی زنده». They are kept out
        of the sort and out of the filter, and re-parked under their anchor row
        afterwards (see reattach() below); sorting them as if they were records
        would scatter five order books across the table.

        Each such row names its anchor with data-for="<ticker>", matched against
        the anchor row's data-ticker.  */
    /*  hasAttribute, NOT `r.dataset.nosort`. A valueless boolean attribute —
        `<tr data-nosort>` — reads back as the EMPTY STRING, which is falsy, so
        the truthiness test excluded nothing: every drawer stayed in the sort,
        the comparator reached for a column its single colspan cell does not
        have, and the resulting TypeError aborted the click handler. The table
        then looked correct, because a sort that throws before it moves anything
        leaves the rows where they were.  */
    const isPinned = (r) => r.hasAttribute("data-nosort");
    const rows = Array.from(tbody.rows).filter((r) => !isPinned(r));
    const pinned = Array.from(tbody.rows).filter(isPinned);
    const reattach = () => {
      pinned.forEach((p) => {
        const anchor = rows.find((r) => r.dataset.ticker === p.dataset.for);
        if (anchor) anchor.after(p);
      });
    };

    // text filter (by ticker)
    const fi = filterInputId && document.getElementById(filterInputId);
    if (fi) {
      fi.addEventListener("input", () => {
        const q = fi.value.trim();
        rows.forEach((r) => {
          const tk = r.dataset.ticker || "";
          r.style.display = (!q || tk.includes(q)) ? "" : "none";
        });
        //  A hidden row's attachment must hide with it, or a filtered-out
        //  symbol leaves its open drawer stranded among the matches.
        pinned.forEach((p) => {
          const anchor = rows.find((r) => r.dataset.ticker === p.dataset.for);
          p.style.display = (anchor && anchor.style.display !== "none") ? "" : "none";
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
      // A row shorter than the header has no cell in this column. Treated as
      // missing rather than allowed to throw: one such row used to take the
      // whole sort down with it, and a click that silently does nothing is the
      // hardest kind of broken to notice.
      if (!cell) return null;
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
          // Same guard as numOf(): a short row sorts as empty, not as a crash.
          const ta = ca ? ca.textContent.trim() : "";
          const tb = cb ? cb.textContent.trim() : "";
          return ta.localeCompare(tb, "fa") * dir;
        });
        visible.forEach((r) => tbody.appendChild(r));
        reattach();
      });
    });
  }

  /* ---- self-contained SVG price line chart (RTL: oldest on the right) ---- */
  /* -------------------------------------------------------------------------
     priceChart — the index / price line chart, with a hover readout
     -------------------------------------------------------------------------
     Used by /indices. Rebuilt for two reasons.

     1. IT IGNORED THE THEME. The line was hard-coded #2f6db3 and the fill
        rgba(47,109,179,.10), so on the seven dark themes the chart stayed a
        pale daylight blue on a near-black panel, and the «رنگ صعود و نزول»
        setting never reached it. Every colour now comes from a CSS class, so
        the chart follows the palette like the tables do — including the
        فیروزه‌ای / سرخابی scheme.

     2. IT WAS NOT INTERACTIVE. A finance chart whose values you cannot read is
        a picture of data. There is now a crosshair, a dot on the series and a
        floating readout that follows the pointer, plus keyboard stepping so
        the same information is reachable without a mouse.

     Still no charting library, deliberately: this is ~90 lines against 40-130
     KB of vendor JavaScript for an audience on slow mobile connections, and
     the app already ships KLineChart for the one screen that needs candles.
     ------------------------------------------------------------------------- */
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

    /*  RTL: index 0 sits at the RIGHT edge and i grows leftwards, which is how
        a Persian reader scans a time axis. Everything below — including the
        pointer-to-index maths — respects that, and getting the direction
        backwards is the classic bug here. */
    const x = (i) => padL + plotW - (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const yy = (v) => padT + plotH - ((v - min) / (max - min)) * plotH;

    //  Direction over the WHOLE window colours the series, matching the
    //  server-rendered sparklines in app.spark(). Not the last bar: a series
    //  that rose all year and dipped on the final session is still rising.
    const rising = values[n - 1] >= values[0];
    const svg = el("svg", {
      viewBox: "0 0 " + w + " " + h, width: "100%", height: h,
      class: "pc " + (rising ? "up" : "down"),
      role: "img",
      "aria-label": "نمودار در " + faDigits(String(n)) + " روز معاملاتی"
    });

    //  A gradient needs a document-unique id, or two charts on one page share
    //  the first one's stops.
    const gid = "pcg-" + containerId + "-" + Math.random().toString(36).slice(2, 7);
    const defs = el("defs", {});
    const grad = el("linearGradient", { id: gid, x1: "0", y1: "0", x2: "0", y2: "1" });
    grad.appendChild(el("stop", { offset: "0%", class: "pc-stop-a" }));
    grad.appendChild(el("stop", { offset: "100%", class: "pc-stop-b" }));
    defs.appendChild(grad);
    svg.appendChild(defs);

    for (let g = 0; g <= 4; g++) {
      const gy = padT + (g / 4) * plotH;
      svg.appendChild(el("line", { x1: padL, y1: gy, x2: w - padR, y2: gy, class: "pc-grid" }));
      svg.appendChild(el("text", { x: padL - 6, y: gy + 4, "text-anchor": "end", class: "pc-tick" },
                                  fmt(max - (g / 4) * (max - min))));
    }
    const step = Math.max(1, Math.ceil(n / 6));
    labels.forEach((lb, i) => {
      if (i % step !== 0 && i !== n - 1) return;
      svg.appendChild(el("text", { x: x(i), y: h - 10, "text-anchor": "middle", class: "pc-tick" },
                                  faDigits(lb)));
    });

    const linePts = values.map((v, i) => x(i) + "," + yy(v)).join(" ");
    svg.appendChild(el("polygon", {
      points: x(0) + "," + (padT + plotH) + " " + linePts + " " + x(n - 1) + "," + (padT + plotH),
      fill: "url(#" + gid + ")", class: "pc-area"
    }));
    svg.appendChild(el("polyline", { points: linePts, class: "pc-line" }));

    //  ---- the hover readout ------------------------------------------------
    const cross = el("line", { class: "pc-cross", y1: padT, y2: padT + plotH, x1: 0, x2: 0 });
    const dot = el("circle", { class: "pc-dot", r: 4, cx: 0, cy: 0 });
    cross.style.opacity = dot.style.opacity = "0";
    svg.appendChild(cross);
    svg.appendChild(dot);

    if (!container.style.position) container.style.position = "relative";
    const tip = document.createElement("div");
    tip.className = "pc-tip";
    tip.hidden = true;
    container.appendChild(svg);
    container.appendChild(tip);

    let active = -1;
    function showAt(i) {
      if (i < 0 || i >= n) return;
      active = i;
      const px = x(i), py = yy(values[i]);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px);
      dot.setAttribute("cx", px); dot.setAttribute("cy", py);
      cross.style.opacity = dot.style.opacity = "1";
      tip.textContent = "";
      const b = document.createElement("b");
      b.textContent = fmt(values[i]);
      const sp = document.createElement("span");
      sp.textContent = faDigits(labels[i] || "");
      tip.appendChild(b); tip.appendChild(sp);
      tip.hidden = false;
      //  px/py are viewBox units and the tip is positioned in CSS pixels, so
      //  scale by the rendered width. Clamped to the container so it never
      //  hangs off the edge on the first or last point.
      const k = container.clientWidth / w;
      const tw = tip.offsetWidth || 90;
      let left = px * k - tw / 2;
      left = Math.max(2, Math.min(container.clientWidth - tw - 2, left));
      tip.style.left = left + "px";
      tip.style.top = Math.max(0, py * k - tip.offsetHeight - 10) + "px";
    }
    function hide() {
      active = -1;
      cross.style.opacity = dot.style.opacity = "0";
      tip.hidden = true;
    }
    function indexFromClientX(clientX) {
      const r = svg.getBoundingClientRect();
      if (!r.width || n <= 1) return 0;
      const vx = ((clientX - r.left) / r.width) * w;          // to viewBox units
      //  Inverse of x(): i grows as vx DECREASES, because of RTL.
      const t = (padL + plotW - vx) / plotW;
      return Math.max(0, Math.min(n - 1, Math.round(t * (n - 1))));
    }

    svg.addEventListener("pointermove", function (ev) { showAt(indexFromClientX(ev.clientX)); });
    svg.addEventListener("pointerleave", hide);
    //  A tap should read a value rather than do nothing.
    svg.addEventListener("pointerdown", function (ev) {
      if (ev.pointerType === "touch") showAt(indexFromClientX(ev.clientX));
    });

    //  Keyboard: the same readout without a pointer. In RTL the visually-next
    //  point is to the LEFT, so ArrowLeft steps forward in time.
    container.tabIndex = 0;
    container.addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowLeft") { ev.preventDefault(); showAt((active < 0 ? -1 : active) + 1); }
      else if (ev.key === "ArrowRight") { ev.preventDefault(); showAt((active < 0 ? 1 : active) - 1); }
      else if (ev.key === "Home") { ev.preventDefault(); showAt(n - 1); }
      else if (ev.key === "End") { ev.preventDefault(); showAt(0); }
      else if (ev.key === "Escape") { hide(); }
    });
    container.addEventListener("blur", hide);
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
  // The SECOND copy of the digit map that used to live in this file, 246 lines
  // below the first. Now the same delegation as faDigits above.
  function toFa(n) {
    return faDigits(n);
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

    /*  DON'T SHOW IT STRAIGHT AWAY.

        The overlay is a full-screen blurred backdrop with a box in the middle
        of the screen. It used to go up the instant a link was clicked or a form
        was submitted — but a page on this app answers in 50–200 ms, so on
        almost every click it appeared and vanished again inside a tenth of a
        second. A box that flashes on and off on every click does not read as
        "working…", it reads as the app glitching, and it was doing it on every
        navigation in the whole platform («all time in app i see a window come
        in screen and closed fast»).

        So the overlay now waits. A navigation that finishes before the delay
        shows nothing at all; only one still going after it — the reason this
        exists, a big group over a long date range — puts the box up, and by
        then it is answering a question the user has started to ask.

        Nothing else changes: hide() cancels a pending show, and it is already
        wired to `pagehide`/`pageshow`. pagehide fires when the new document is
        ready to replace this one, i.e. exactly when the waiting is over, so a
        navigation that beats the timer never flashes at the very end either. */
    const SHOW_AFTER_MS = 400;
    let shown = false;
    let pending = null;
    const show = () => {
      if (shown || pending) return;
      pending = setTimeout(() => {
        pending = null;
        shown = true;
        overlay.classList.add("on");
      }, SHOW_AFTER_MS);
    };
    const hide = () => {
      if (pending) { clearTimeout(pending); pending = null; }
      shown = false;
      overlay.classList.remove("on");
    };

    // form submits (filter bars, compare, calculate…)
    //
    // The overlay is hidden by `pageshow`, so it may only be shown for a submit
    // that really NAVIGATES. A Vue island's form calls preventDefault() and
    // fetches instead — «مقایسه» on /performance, and the filter bars on the
    // market, calculator, scan and screener islands — so no navigation ever
    // followed, no pageshow ever fired, and «در حال محاسبه… لطفاً صبر کنید» sat
    // there until the user reloaded the page by hand. The island's handler runs
    // on the form and this one on the document, so by the time it is reached
    // defaultPrevented already says which kind of submit this is. Same guard the
    // link handler below has always had.
    document.addEventListener("submit", (e) => {
      if (e.defaultPrevented) return;
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
  initRowLinks();          // delegated: no DOM to wait for
  return { initSearch, initTable, initTabs, priceChart, fmt, toggleWatch,
           openInNewTab };
})();

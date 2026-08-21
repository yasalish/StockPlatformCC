/* heatmap.js — نقشهٔ بازار
   ---------------------------------------------------------------------------
   Draws /api/heatmap/<kind> as one screen of tiles: a box per گروه (صنعت for
   stocks, نوع for ETFs), and inside it a tile per symbol, sized by traded value
   and coloured by return.

   Why a heat map at all, when the app already has sortable tables: a table
   answers "which symbol rose most" and this answers "where did the money go" —
   the sector rotation that a ranked list actively hides, because the top of the
   list is always the smallest, most volatile symbols.

   Three decisions worth keeping:

   * **Size follows traded value, not symbol count.** A ۵٪ move in a symbol
     nobody traded is not the same event as a ۵٪ move in the most-traded symbol
     of the session; a grid of equal tiles says they are.
   * **The colour comes from the live CSS variables**, read once per render with
     getComputedStyle. That is what makes the map follow the theme, and — more
     importantly — the کوررنگی setting: a hard-coded green/red scale would be
     the one part of the app that ignores it, and it is the part that is
     entirely colour.
   * **~۷۸۰ tiles are built as one HTML string** and assigned once. Creating
     them node by node is ~۱۰× slower here, and the map is redrawn on every
     keystroke in the filter box.
*/
(function () {
  "use strict";

  var root = document.getElementById("hm-root");
  if (!root) return;

  var FA = "۰۱۲۳۴۵۶۷۸۹";
  function faDigits(s) { return String(s).replace(/[0-9]/g, function (d) { return FA[+d]; }); }
  function pct(v) {
    if (v == null) return "—";
    return (v >= 0 ? "+" : "−") + faDigits(Math.abs(v).toFixed(2)) + "٪";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---- colour ----
     The scale saturates at ±۵٪, which is one trading band on this market: past
     that everything is "as extreme as it gets" and finer gradations would only
     make the map harder to read. Below ±۰٫۲٪ the tile stays neutral, so a flat
     symbol does not read as a faint gain. */
  var CAP = 5;

  function rgb(varName, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    return parse(v) || parse(fallback);
  }

  function parse(color) {
    if (!color) return null;
    var m = color.match(/^#([0-9a-f]{6})$/i);
    if (m) {
      var n = parseInt(m[1], 16);
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }
    m = color.match(/rgba?\(([^)]+)\)/);
    if (m) {
      var parts = m[1].split(",").map(function (x) { return parseInt(x, 10); });
      return [parts[0], parts[1], parts[2]];
    }
    return null;
  }

  function tint(chg, palette) {
    if (chg == null) return { bg: palette.flat, ink: palette.flatInk };
    var t = Math.min(Math.abs(chg) / CAP, 1);
    if (Math.abs(chg) < 0.2) return { bg: palette.flat, ink: palette.flatInk };
    var target = chg >= 0 ? palette.up : palette.down;
    var base = palette.flat;
    var mix = [
      Math.round(base[0] + (target[0] - base[0]) * (0.25 + 0.75 * t)),
      Math.round(base[1] + (target[1] - base[1]) * (0.25 + 0.75 * t)),
      Math.round(base[2] + (target[2] - base[2]) * (0.25 + 0.75 * t))
    ];
    return {
      bg: mix,
      // Contrast is decided from the tile's own luminance rather than from the
      // theme: the same tile can be pale at ۰٫۵٪ and saturated at ۵٪, and one
      // fixed text colour is unreadable at one end or the other.
      ink: luminance(mix) > 150 ? [20, 25, 30] : [255, 255, 255]
    };
  }

  function luminance(c) { return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]; }
  function css(c) { return "rgb(" + c[0] + "," + c[1] + "," + c[2] + ")"; }

  function palette() {
    var flat = rgb("--mute-fill", "#cfd6de");
    return {
      up: rgb("--up", "#14874f"),
      down: rgb("--down", "#d92d20"),
      flat: flat,
      flatInk: luminance(flat) > 150 ? [20, 25, 30] : [255, 255, 255]
    };
  }

  /* ---- rendering ---- */
  var state = { rows: [], groups: [], base: root.getAttribute("data-detail-base") || "/stock/" };

  function render(filter) {
    var pal = palette();
    var q = (filter || "").trim();
    var byGroup = {};
    state.rows.forEach(function (r) {
      if (q && r.t.indexOf(q) === -1 && (r.n || "").indexOf(q) === -1 && r.g.indexOf(q) === -1) return;
      (byGroup[r.g] = byGroup[r.g] || []).push(r);
    });

    var html = "";
    state.groups.forEach(function (g) {
      var rows = byGroup[g.group];
      if (!rows || !rows.length) return;
      // Biggest first inside the group, so the eye lands on what actually moved
      // the group's average.
      rows.sort(function (a, b) { return (b.v || 0) - (a.v || 0); });
      var maxValue = rows[0].v || 1;
      var avgCls = g.avg == null ? "muted" : (g.avg >= 0 ? "up-title" : "down-title");

      html += '<section class="hm-group"><div class="hm-group-head">' +
        "<h3>" + esc(g.group) + "</h3>" +
        '<span class="g-avg ' + avgCls + '">' + pct(g.avg) + "</span>" +
        '<span class="g-meta">' + faDigits(rows.length) + " نماد · " +
        faDigits(g.up) + " مثبت / " + faDigits(g.down) + " منفی</span></div><div class=\"hm-tiles\">";

      rows.forEach(function (r) {
        var t = tint(r.c, pal);
        // flex-grow carries the weighting: a symbol with ۱۰× the turnover of its
        // neighbour claims ~۱۰× the width of the row it lands in. Clamped to 12
        // so one enormous symbol cannot squeeze the rest of its group to a
        // sliver, which happens on days ملی sells one block.
        var grow = Math.max(1, Math.min(12, (r.v || 0) / maxValue * 12));
        html += '<a class="hm-tile' + (grow > 6 ? " big" : "") + '" href="' +
          esc(state.base + r.id) + '" style="flex-grow:' + grow.toFixed(2) +
          ";--tint:" + css(t.bg) + ";--tint-ink:" + css(t.ink) + '" title="' +
          esc(r.t + " — " + (r.n || "") + " · قیمت " + faDigits(Math.round(r.p || 0).toLocaleString("en-US"))) +
          '"><span class="t-sym">' + esc(r.t) + '</span><span class="t-chg">' + pct(r.c) + "</span></a>";
      });
      html += "</div></section>";
    });

    root.innerHTML = html || '<p class="hm-empty">نمادی با این فیلتر پیدا نشد.</p>';
  }

  function load() {
    var kind = root.getAttribute("data-kind") || "stock";
    var period = root.getAttribute("data-period") || "p20";
    fetch("/api/heatmap/" + kind + "?period=" + encodeURIComponent(period))
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      })
      .then(function (d) {
        state.rows = d.rows || [];
        state.groups = d.groups || [];
        var asof = document.querySelector('[data-role="hm-asof"]');
        if (asof) {
          asof.textContent = "تا تاریخ " + faDigits(d.as_of || "") + " · " +
            faDigits(state.rows.length) + " نماد در " + faDigits(state.groups.length) + " گروه";
        }
        render("");
      })
      .catch(function () {
        root.innerHTML = '<p class="hm-empty">نقشه بارگذاری نشد. صفحه را دوباره باز کنید.</p>';
      });
  }

  var box = document.getElementById("hm-filter");
  if (box) {
    var t = null;
    box.addEventListener("input", function () {
      clearTimeout(t);
      t = setTimeout(function () { render(box.value); }, 120);
    });
  }
  // A theme or کوررنگی change has to repaint the tiles: their colours were
  // computed from the CSS variables at render time, not linked to them.
  document.addEventListener("bn:prefs", function () { render(box ? box.value : ""); });

  load();
})();

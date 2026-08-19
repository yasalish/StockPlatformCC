/* tables.js — نوار پیمایش بالای جدول‌ها و ابزار جدول
   ---------------------------------------------------------------------------
   Every wide table in the app gets a second, mirrored horizontal scrollbar
   ABOVE it, plus a small toolbar. The problem it solves is concrete: the only
   horizontal scrollbar used to be the browser's own, at the BOTTOM of the
   scroller. /performance is ۷۸۲ rows tall and ۲۰ columns wide, so the control
   that moves the table sideways sat two screens below the part of the table you
   were reading. Mirroring it above — and making it sticky under the site header
   — puts it where the hand already is.

   Three things make this harder than "insert a div":

   1. **The islands.** /stocks, /etfs, /performance, /screener, /filters and
      /strategies render their tables from Vue AFTER the page loads, and the
      virtualized ones replace their rows on every scroll. A one-shot pass at
      DOMContentLoaded would decorate none of them, so a MutationObserver keeps
      watching and a ResizeObserver re-measures when a column width settles.
   2. **The mirror must not be a fake.** It is a real overflow container with a
      spacer inside, so the browser draws a real scrollbar: wheel-tilt, drag,
      shift+wheel, keyboard and assistive technology all keep working. A
      div-with-a-thumb would have to reimplement every one of them, badly.
   3. **It must disappear when it is not needed.** A table that fits its panel
      shows no mirror at all — an inert scrollbar over a narrow table is noise,
      and it would push the first row down for nothing.

   The toolbar (تمام‌صفحه / CSV / چاپ) works on what is already in the DOM. That
   is why CSV and print are offered only on the NON-virtualized tables: a
   virtualized grid holds ~۶۰ rows at a time, so exporting it from the DOM would
   silently produce a file with sixty of the seven hundred rows in it — a wrong
   answer that looks like a right one. Those pages keep their server-side Excel
   export, which reads the database.
*/
const BNTables = (function () {
  "use strict";

  var MIRROR_CLASS = "table-scroll-top";
  var FA = "۰۱۲۳۴۵۶۷۸۹";

  function prefs() { return (window.BN_PREFS || {}); }

  /* ---- the header height, as a CSS variable ----
     The mirror bar and the sticky table header both sit below the site header,
     which wraps to two rows on a narrow window. Measuring it beats hard-coding
     it: a wrong constant either floats the bar in mid-air or hides it behind
     the header, and only on the window sizes nobody tested. */
  function measureHeader() {
    var bar = document.querySelector(".topbar");
    if (!bar) return;
    var h = Math.round(bar.getBoundingClientRect().height);
    if (h > 0) document.documentElement.style.setProperty("--topbar-h", h + "px");
  }

  /* ---- one scroller ---- */
  function decorate(scroller) {
    if (!scroller || scroller.dataset.bnBar === "1") return;
    // A scroller inside another decorated scroller (the fullscreen clone, say)
    // would get a second bar that scrolls the wrong element.
    if (scroller.closest("." + MIRROR_CLASS)) return;
    scroller.dataset.bnBar = "1";

    var bar = document.createElement("div");
    bar.className = "tbl-bar";

    var mirror = document.createElement("div");
    mirror.className = MIRROR_CLASS;
    mirror.setAttribute("aria-hidden", "true");   // it duplicates a real scrollbar
    mirror.appendChild(document.createElement("div"));
    bar.appendChild(mirror);
    bar.appendChild(buildTools(scroller));

    scroller.parentNode.insertBefore(bar, scroller);

    // scrollLeft is negative in RTL on some engines and positive on others.
    // Copying the value straight across is correct in both, because the two
    // elements have the same direction — which is exactly why nothing here
    // tries to compute a position from scrollWidth.
    var lock = false;
    mirror.addEventListener("scroll", function () {
      if (lock) return;
      lock = true; scroller.scrollLeft = mirror.scrollLeft; lock = false;
    });
    scroller.addEventListener("scroll", function () {
      if (lock) return;
      lock = true; mirror.scrollLeft = scroller.scrollLeft; lock = false;
    });

    var sync = function () { measure(scroller, mirror); };
    sync();
    // The table's own width settles a frame or two after it mounts (fonts, then
    // the virtualizer's column measurement), so re-measure rather than trusting
    // the first reading.
    requestAnimationFrame(sync);
    setTimeout(sync, 250);

    if (window.ResizeObserver) {
      var ro = new ResizeObserver(sync);
      ro.observe(scroller);
      var table = scroller.querySelector("table");
      if (table) ro.observe(table);
    }
    window.addEventListener("resize", sync);
  }

  function measure(scroller, mirror) {
    var spacer = mirror.firstElementChild;
    var w = scroller.scrollWidth;
    if (spacer) spacer.style.width = w + "px";
    // 2px of slack: sub-pixel layout leaves scrollWidth a hair above
    // clientWidth on tables that visibly fit, and a 1px scrollbar over every
    // narrow table is worse than none.
    var overflows = w > scroller.clientWidth + 2;
    mirror.hidden = !overflows || prefs().top_scrollbar === false;
    mirror.scrollLeft = scroller.scrollLeft;
  }

  /* ---- the toolbar ---- */
  function buildTools(scroller) {
    var tools = document.createElement("div");
    tools.className = "tbl-tools";
    var table = scroller.querySelector("table");
    var virtual = table && table.classList.contains("grid-virtual");

    tools.appendChild(button("⛶", "تمام‌صفحه", function () { toggleFullscreen(scroller); }));
    if (!virtual) {
      tools.appendChild(button("⤓", "دریافت CSV", function () { exportCsv(scroller); }));
      tools.appendChild(button("🖨", "چاپ جدول", function () { printTable(scroller); }));
    }
    return tools;
  }

  function button(glyph, title, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "tbl-btn";
    b.title = title;
    b.setAttribute("aria-label", title);
    b.textContent = glyph;
    b.addEventListener("click", onClick);
    return b;
  }

  /* ---- تمام‌صفحه ----
     The panel is promoted, not the bare scroller, so the heading and the filter
     bar come with it. The Fullscreen API is deliberately not used: it detaches
     the element, which loses the sticky header, the page font-size and (in
     Chrome) the custom scrollbar styling this file exists to provide. */
  var fsHost = null;

  function toggleFullscreen(scroller) {
    if (fsHost) return exitFullscreen();
    var host = scroller.closest(".panel") || scroller.parentNode;
    host.classList.add("tbl-fs");
    document.body.classList.add("tbl-fs-on");
    fsHost = host;

    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn tbl-fs-close";
    close.textContent = "بستن (Esc)";
    close.addEventListener("click", exitFullscreen);
    host.appendChild(close);
    close.focus();
    window.dispatchEvent(new Event("resize"));   // let the virtualizer re-measure
  }

  function exitFullscreen() {
    if (!fsHost) return;
    var close = fsHost.querySelector(".tbl-fs-close");
    if (close) close.remove();
    fsHost.classList.remove("tbl-fs");
    document.body.classList.remove("tbl-fs-on");
    fsHost = null;
    window.dispatchEvent(new Event("resize"));
  }

  /* ---- CSV ----
     Digits are converted back to Latin and the ٪ / ‎−‎ signs normalised, because
     the file is opened in Excel: a Persian «۱۲٫۳٪» is text there, while 12.3 is
     a number you can sort. The BOM is what makes Excel read it as UTF-8 rather
     than mangling every Persian name in the first column. */
  function exportCsv(scroller) {
    var table = scroller.querySelector("table");
    if (!table) return;
    var lines = [];
    var head = table.tHead ? Array.prototype.slice.call(table.tHead.rows) : [];
    // Only the LAST header row: a two-row header's first row is group labels
    // spanning pairs of columns, which do not line up with the data cells.
    if (head.length) lines.push(cells(head[head.length - 1]));
    var body = table.tBodies[0];
    if (body) {
      Array.prototype.forEach.call(body.rows, function (tr) {
        if (tr.classList.contains("vpad")) return;   // virtualizer spacer rows
        lines.push(cells(tr));
      });
    }
    var csv = "﻿" + lines.map(function (r) {
      return r.map(function (v) { return '"' + String(v).replace(/"/g, '""') + '"'; }).join(",");
    }).join("\r\n");
    download(csv, (document.title || "table").replace(/[^\p{L}\p{N}]+/gu, "-") + ".csv");
  }

  function cells(tr) {
    return Array.prototype.map.call(tr.cells, function (td) {
      var v = (td.dataset && td.dataset.v != null) ? td.dataset.v : td.textContent;
      return String(v).replace(/[۰-۹]/g, function (d) { return String(FA.indexOf(d)); })
        .replace(/−/g, "-").replace(/\s+/g, " ").trim();
    });
  }

  function download(text, name) {
    var blob = new Blob([text], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  /* ---- چاپ ----
     Printing the page prints the nav, the filter bar and whatever else is on
     screen; printing one table into a blank document prints the table. */
  function printTable(scroller) {
    var table = scroller.querySelector("table");
    if (!table) return;
    var w = window.open("", "_blank", "width=900,height=650");
    if (!w) return;                                  // pop-up blocked
    var css = "body{font-family:Vazirmatn,Tahoma,sans-serif;direction:rtl;padding:16px}" +
      "table{width:100%;border-collapse:collapse;font-size:12px}" +
      "th,td{border:1px solid #ddd;padding:5px 7px;text-align:right}" +
      "thead th{background:#f2f2f2}";
    w.document.write('<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8">' +
      "<title>" + document.title + "</title><style>" + css + "</style></head><body>" +
      "<h3>" + document.title + "</h3>" + table.outerHTML + "</body></html>");
    w.document.close();
    w.focus();
    w.print();
  }

  /* ---- discovery ---- */
  function scan(root) {
    var nodes = (root || document).querySelectorAll(".table-scroll");
    Array.prototype.forEach.call(nodes, decorate);
  }

  function init() {
    measureHeader();
    window.addEventListener("resize", measureHeader);
    scan(document);

    // The islands mount after this file runs, and a table that re-renders can
    // replace its scroller entirely. Watching the document costs one callback
    // per DOM batch and is the only thing that catches them all.
    if (window.MutationObserver) {
      new MutationObserver(function (records) {
        for (var i = 0; i < records.length; i++) {
          var added = records[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var n = added[j];
            if (n.nodeType !== 1) continue;
            if (n.classList && n.classList.contains("table-scroll")) decorate(n);
            else if (n.querySelectorAll) scan(n);
          }
        }
        // A bar whose scroller was removed (an island re-rendered, a filter
        // emptied the table) is left pointing at nothing — drop it, or the page
        // slowly fills with orphaned scrollbars.
        var bars = document.querySelectorAll(".tbl-bar");
        Array.prototype.forEach.call(bars, function (bar) {
          var next = bar.nextElementSibling;
          if (!next || !next.classList.contains("table-scroll")) bar.remove();
        });
      }).observe(document.body, { childList: true, subtree: true });
    }

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && fsHost) exitFullscreen();
    });

    // A settings change must reach the tables already on screen — re-measuring
    // is what shows or hides every mirror when «نوار پیمایش» is switched.
    document.addEventListener("bn:prefs", function () {
      Array.prototype.forEach.call(document.querySelectorAll(".tbl-bar"), function (bar) {
        var scroller = bar.nextElementSibling;
        var mirror = bar.querySelector("." + MIRROR_CLASS);
        if (scroller && mirror) measure(scroller, mirror);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", init);

  var api = { scan: scan, decorate: decorate, exportCsv: exportCsv };
  try { if (typeof BN !== "undefined" && BN) BN.tables = api; } catch (e) {}
  window.BNTables = api;
  return api;
})();

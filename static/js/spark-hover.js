/* ===========================================================================
   spark-hover.js — a value readout for the server-rendered SVG charts
   ===========================================================================
   The dashboard's charts are drawn in Python (app.spark) and arrive as static
   SVG, which is why the page needs no charting library and works with scripts
   off. The cost of that choice was that the numbers could not be read: you
   could see the shape of the index over 250 sessions and not one value on it.

   This adds the readout without giving up the choice. It attaches to any
   element carrying `data-spark-points`, so the markup stays static and this
   file is pure enhancement — if it never loads, the chart is exactly the chart
   the server sent.

   THE CONTRACT

     <div class="spark-host" data-spark-points="x:value:label|x:value:label|…"
          data-spark-w="1000">
       <svg viewBox="0 0 1000 190">…</svg>
     </div>

   `x` is in viewBox units, already projected by the server — deliberately not
   recomputed here, because two copies of that projection would drift the first
   time anyone changed the padding.

   Deferred and idempotent: it can run before or after the dashboard's own
   fetch injects the markup, and initSparkHover() is safe to call again for
   nodes that arrive later.
   --------------------------------------------------------------------------- */
(function () {
  "use strict";

  var FA = "۰۱۲۳۴۵۶۷۸۹";
  function faDigits(s) {
    // window.BN_FORMAT is the shared formatter (M-2); the fallback is here for
    // the same reason app.js keeps one — a missing separator is cosmetic, a
    // thrown error is a dead chart.
    if (window.BN_FORMAT) return window.BN_FORMAT.toFaDigits(String(s));
    return String(s).replace(/[0-9]/g, function (d) { return FA[d]; });
  }
  function group(n) {
    // ROUNDED to a whole number on purpose. BN_FORMAT.fa() keeps two decimals
    // for a non-integral value, which is right for a percentage and wrong
    // here: the readout would say ۶,۵۸۳,۹۳۲.۳۰ while the headline beside it
    // says ۶,۵۸۳,۹۳۲, and two different numbers for the same figure on one
    // card reads as a bug. The full precision is still carried in the data —
    // this is only how it is shown.
    var r = Math.round(n);
    if (window.BN_FORMAT) return window.BN_FORMAT.fa(r);
    return faDigits(r.toLocaleString("en-US"));
  }

  function attach(host) {
    if (host.dataset.sparkReady === "1") return;
    var raw = host.dataset.sparkPoints;
    var svg = host.querySelector("svg");
    if (!raw || !svg) return;

    var pts = [];
    raw.split("|").forEach(function (rec) {
      var f = rec.split(":");
      if (f.length < 2) return;
      pts.push({ x: parseFloat(f[0]), v: parseFloat(f[1]), label: f[2] || "" });
    });
    if (pts.length < 2) return;
    host.dataset.sparkReady = "1";

    var vbW = parseFloat(host.dataset.sparkW || 0) ||
              (svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width) || 1000;
    var vbH = (svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.height) || 190;

    // The crosshair and dot are added to the SVG rather than drawn server-side,
    // so a chart nobody hovers costs nothing extra in the HTML.
    var NS = "http://www.w3.org/2000/svg";
    var cross = document.createElementNS(NS, "line");
    cross.setAttribute("class", "sh-cross");
    cross.setAttribute("y1", 0);
    cross.setAttribute("y2", vbH);
    var dot = document.createElementNS(NS, "circle");
    dot.setAttribute("class", "sh-dot");
    dot.setAttribute("r", 3.5);
    cross.style.opacity = dot.style.opacity = "0";
    svg.appendChild(cross);
    svg.appendChild(dot);

    var tip = document.createElement("div");
    tip.className = "sh-tip";
    tip.hidden = true;
    host.appendChild(tip);

    // y for a value, from the same normalisation the server used: the path was
    // scaled so min sits at the bottom of the inner box and max at the top.
    var lo = pts[0].v, hi = pts[0].v;
    pts.forEach(function (p) { if (p.v < lo) lo = p.v; if (p.v > hi) hi = p.v; });
    var span = (hi - lo) || 1;
    var pad = 6;                                  // matches spark()'s pad
    var inner = vbH - 2 * pad;
    function yOf(v) { return pad + inner - ((v - lo) / span) * inner; }

    var active = -1;
    function show(i) {
      if (i < 0 || i >= pts.length) return;
      active = i;
      var p = pts[i], py = yOf(p.v);
      cross.setAttribute("x1", p.x); cross.setAttribute("x2", p.x);
      dot.setAttribute("cx", p.x); dot.setAttribute("cy", py);
      cross.style.opacity = dot.style.opacity = "1";

      tip.textContent = "";
      var b = document.createElement("b");
      b.textContent = group(p.v);
      tip.appendChild(b);
      if (p.label) {
        var sp = document.createElement("span");
        sp.textContent = faDigits(p.label);
        tip.appendChild(sp);
      }
      tip.hidden = false;

      // viewBox units -> CSS pixels, then clamped so the readout never hangs
      // off either edge on the first or last sample.
      var k = host.clientWidth / vbW;
      var tw = tip.offsetWidth || 90;
      var left = p.x * k - tw / 2;
      left = Math.max(2, Math.min(host.clientWidth - tw - 2, left));
      tip.style.left = left + "px";
      tip.style.top = Math.max(0, py * k - tip.offsetHeight - 8) + "px";
    }
    function hide() {
      active = -1;
      cross.style.opacity = dot.style.opacity = "0";
      tip.hidden = true;
    }
    function nearest(clientX) {
      var r = svg.getBoundingClientRect();
      if (!r.width) return 0;
      var vx = ((clientX - r.left) / r.width) * vbW;
      // Nearest by x rather than by index arithmetic: the server may have
      // emitted an uneven series, and a linear guess would drift on one.
      var best = 0, bestD = Infinity;
      for (var i = 0; i < pts.length; i++) {
        var d = Math.abs(pts[i].x - vx);
        if (d < bestD) { bestD = d; best = i; }
      }
      return best;
    }

    svg.addEventListener("pointermove", function (ev) { show(nearest(ev.clientX)); });
    svg.addEventListener("pointerleave", hide);
    svg.addEventListener("pointerdown", function (ev) {
      if (ev.pointerType === "touch") show(nearest(ev.clientX));
    });

    // The same values without a pointer. In RTL the visually-next sample is to
    // the LEFT, and spark() emits x ascending with time, so ArrowLeft steps
    // forward only if the chart is drawn left-to-right — which spark() is.
    host.tabIndex = 0;
    host.setAttribute("role", "application");
    host.addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowRight") { ev.preventDefault(); show((active < 0 ? -1 : active) + 1); }
      else if (ev.key === "ArrowLeft") { ev.preventDefault(); show((active < 0 ? pts.length : active) - 1); }
      else if (ev.key === "Home") { ev.preventDefault(); show(0); }
      else if (ev.key === "End") { ev.preventDefault(); show(pts.length - 1); }
      else if (ev.key === "Escape") { hide(); }
    });
    host.addEventListener("blur", hide);
  }

  function initSparkHover(root) {
    (root || document).querySelectorAll("[data-spark-points]").forEach(attach);
  }

  // The dashboard injects its markup after a fetch, so run on both.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { initSparkHover(); });
  } else {
    initSparkHover();
  }
  window.BNSparkHover = { init: initSparkHover };
})();

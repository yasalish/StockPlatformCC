/* theme.js — پوسته و تنظیمات نمایش
   ---------------------------------------------------------------------------
   Turns the saved preferences into attributes on <html>, and writes changes
   back. Every visual preference in the app is applied here and nowhere else, so
   there is one place to look when a setting does not take effect.

   Where the values come from, in order of authority:

   1. The server, for a signed-in account. base.html renders the attributes onto
      <html> and embeds the same values as window.BN_PREFS. That is what makes a
      theme follow the account to another browser.
   2. localStorage, for everyone else. The inline pre-paint script in base.html
      applies it BEFORE the first frame; this file only keeps it in step
      afterwards. Applying the theme from an external file alone is what causes
      the white flash on every navigation that dark-mode users complain about.

   A signed-in user's localStorage is deliberately NOT allowed to override the
   server: two browsers would otherwise fight over the account, each restoring
   its own theme on the next page load.
*/
const BNTheme = (function () {
  "use strict";

  var THEME_KEY = "boursenegar-theme";     // the pre-paint script reads this one
  var PREFS_KEY = "boursenegar-prefs";     // everything else, for anonymous users
  var FA = "۰۱۲۳۴۵۶۷۸۹";

  // Server-rendered current values. Present on every page (base.html renders
  // prefs.client_payload for anonymous visitors too, so this is never empty and
  // no default has to be repeated in JavaScript).
  function current() {
    // A copy: callers merge into the result, and mutating window.BN_PREFS in
    // place would let a read leak into the page-wide state.
    var src = window.BN_PREFS || {};
    var p = {};
    for (var s0 in src) if (Object.prototype.hasOwnProperty.call(src, s0)) p[s0] = src[s0];
    if (!signedIn()) {
      var local = read(PREFS_KEY, {});
      for (var k in local) if (Object.prototype.hasOwnProperty.call(local, k)) p[k] = local[k];
      var t = get(THEME_KEY);
      if (t) p.theme = t;
    }
    return p;
  }

  function signedIn() {
    return document.documentElement.getAttribute("data-prefs") === "server";
  }

  function get(key) {
    // localStorage throws outright in private mode in some browsers, so every
    // access is guarded — a settings screen that cannot save is survivable, a
    // page that will not render is not.
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }

  function set(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  function read(key, fallback) {
    try { return JSON.parse(get(key) || "null") || fallback; } catch (e) { return fallback; }
  }

  /* ---- applying ---- */
  // One preference → one attribute. The same map is mirrored, in miniature, by
  // the inline pre-paint script in base.html; keep them in step, and note that
  // the CSS reads ONLY these attributes (never a class), so the pre-paint script
  // can set them all in one pass with nothing to undo afterwards.
  var ATTRS = {
    theme:          function (v) { return ["data-theme", v]; },
    density:        function (v) { return ["data-density", v]; },
    font_scale:     function (v) { return ["data-font", v]; },
    scrollbar_size: function (v) { return ["data-sbar", v]; },
    updown_scheme:  function (v) { return ["data-updown", v]; },
    digits:         function (v) { return ["data-digits", v]; },
    zebra:          function (v) { return ["data-zebra", v ? "on" : "off"]; },
    sticky_head:    function (v) { return ["data-stickyhead", v ? "on" : "off"]; },
    reduce_motion:  function (v) { return ["data-motion", v ? "reduce" : "full"]; },
    wide:           function (v) { return ["data-wide", v ? "on" : "off"]; }
  };

  function apply(p) {
    var d = document.documentElement;
    for (var key in ATTRS) {
      if (!Object.prototype.hasOwnProperty.call(p, key)) continue;
      var pair = ATTRS[key](p[key]);
      d.setAttribute(pair[0], pair[1]);
    }
    if (p.digits) digits(p.digits);
  }

  /* ---- Persian ⇄ Latin digits ----
     The server renders Persian digits (db.to_persian), which is right for the
     Persian UI but wrong for anyone pasting figures into Excel or reading them
     next to a Latin-numeral broker statement. Rewriting the text nodes is the
     only way to reach the numbers the server already rendered — and it reaches
     the Vue islands too, which is why the observer stays running.

     Terminates: after a pass there are no Persian digits left, so the mutations
     this makes produce a second pass that changes nothing. */
  var digitObserver = null;

  function toLatin(text) {
    return text.replace(/[۰-۹]/g, function (d) { return String(FA.indexOf(d)); });
  }

  function convert(root) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        var p = n.parentNode;
        if (!p) return NodeFilter.FILTER_REJECT;
        var tag = p.nodeName;
        // Script and style text is code, not copy; an input's value is not a
        // text node at all, so typed digits are never touched.
        if (tag === "SCRIPT" || tag === "STYLE" || tag === "TEXTAREA") return NodeFilter.FILTER_REJECT;
        return /[۰-۹]/.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    var n, batch = [];
    while ((n = walker.nextNode())) batch.push(n);
    for (var i = 0; i < batch.length; i++) batch[i].nodeValue = toLatin(batch[i].nodeValue);
  }

  // True while the page is showing Latin digits that were rewritten from the
  // server's Persian ones — save() consults it to know whether going back needs
  // a reload (the original text is gone; only the server can produce it again).
  function converted() { return !!digitObserver; }

  function digits(mode) {
    if (mode !== "en") {
      if (digitObserver) { digitObserver.disconnect(); digitObserver = null; }
      return;
    }
    if (digitObserver) return;
    convert(document.body);
    digitObserver = new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        if (r.type === "characterData") {
          if (/[۰-۹]/.test(r.target.nodeValue)) r.target.nodeValue = toLatin(r.target.nodeValue);
        } else {
          for (var j = 0; j < r.addedNodes.length; j++) {
            var node = r.addedNodes[j];
            if (node.nodeType === 3) {
              if (/[۰-۹]/.test(node.nodeValue)) node.nodeValue = toLatin(node.nodeValue);
            } else if (node.nodeType === 1) {
              convert(node);
            }
          }
        }
      }
    });
    digitObserver.observe(document.body, {
      childList: true, subtree: true, characterData: true
    });
  }

  /* ---- saving ---- */
  function save(patch) {
    // Switching back to Persian digits cannot be undone in the DOM — the
    // Persian text was overwritten — so the page has to come back from the
    // server. The reload waits until the PATCH has actually been sent, because
    // navigating away mid-request cancels it and the setting would not stick.
    var needsReload = patch.digits === "fa" && converted();
    var merged = current();
    for (var k in patch) if (Object.prototype.hasOwnProperty.call(patch, k)) merged[k] = patch[k];
    apply(merged);
    window.BN_PREFS = merged;

    // The theme is mirrored into localStorage even for a signed-in user: the
    // pre-paint script cannot ask the server, and without this a signed-in user
    // on a slow connection still gets the right theme on the first frame.
    if (patch.theme) set(THEME_KEY, patch.theme);

    if (signedIn()) {
      // Fire and forget. A failed sync must not revert the click — the user
      // asked for a dark page and got one; what they lose is only the sync to
      // their other browser, and the next successful save fixes that.
      fetch("/api/me/prefs", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch)
      }).catch(function () { /* offline: the local application already happened */ })
        .then(function () { if (needsReload) window.location.reload(); });
    } else {
      var local = read(PREFS_KEY, {});
      for (var k2 in patch) if (Object.prototype.hasOwnProperty.call(patch, k2)) local[k2] = patch[k2];
      set(PREFS_KEY, JSON.stringify(local));
      if (needsReload) window.location.reload();
    }
    document.dispatchEvent(new CustomEvent("bn:prefs", { detail: merged }));
    return merged;
  }

  function setTheme(id) { return save({ theme: id }); }

  // Crosses to the OTHER family's default — never to whichever alternate was
  // last picked over there. A control whose destination depends on history is a
  // control nobody can predict; prefs.toggle_target() says the same in Python.
  var DARK = { dark: 1, midnight: 1, graphite: 1 };
  function toggle() {
    var now = current().theme || "light";
    return setTheme(DARK[now] ? "light" : "dark");
  }

  function init() {
    // The pre-paint script has already set the attributes; this re-applies from
    // the authoritative object so a server value and a stale localStorage entry
    // cannot disagree after the first frame, and starts the digit observer.
    apply(current());
    var btn = document.querySelector('[data-role="theme-toggle"]');
    if (btn) {
      btn.addEventListener("click", function () {
        var p = toggle();
        btn.textContent = DARK[p.theme] ? "☀" : "☾";
        btn.setAttribute("aria-label", DARK[p.theme] ? "پوستهٔ روشن" : "پوستهٔ تاریک");
      });
      btn.textContent = DARK[current().theme] ? "☀" : "☾";
    }
  }

  document.addEventListener("DOMContentLoaded", init);

  var api = { apply: apply, save: save, setTheme: setTheme, toggle: toggle, current: current };
  // app.js exposes `BN` as a const binding rather than a window property, so it
  // is extended here only if it actually loaded — a missing app.js must not take
  // the theme with it.
  try { if (typeof BN !== "undefined" && BN) BN.theme = api; } catch (e) {}
  window.BNTheme = api;
  return api;
})();

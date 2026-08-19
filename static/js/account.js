/* account.js — صفحهٔ تنظیمات: پوسته، ترجیح‌ها و غربالگرهای ذخیره‌شده
   ---------------------------------------------------------------------------
   Wires the تنظیمات screen to BNTheme (which applies a preference) and to
   /api/me/prefs (which stores it). Nothing here is behind a «ذخیره» button:
   every control writes on change and the page repaints immediately, because a
   theme picker you have to confirm is a theme picker you cannot preview.

   The optimistic order matters — apply first, persist second. A save that fails
   (offline, session expired) leaves the user looking at the theme they asked
   for rather than at a control that snapped back for reasons they cannot see;
   the next successful save reconciles it. Losing a preference silently is a
   small cost, and undoing a click the user just made is a large one.
*/
(function () {
  "use strict";

  function theme() { return window.BNTheme; }

  function flash() {
    var el = document.querySelector('[data-role="saved"]');
    if (!el) return;
    el.classList.add("on");
    clearTimeout(el._t);
    el._t = setTimeout(function () { el.classList.remove("on"); }, 1400);
  }

  /* ---- انتخاب پوسته ---- */
  function themePicker() {
    var row = document.querySelector('[data-role="theme-row"]');
    if (!row) return;
    row.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest("[data-theme-id]") : null;
      if (!btn) return;
      var id = btn.getAttribute("data-theme-id");
      if (theme()) theme().setTheme(id);
      var all = row.querySelectorAll("[data-theme-id]");
      for (var i = 0; i < all.length; i++) {
        var on = all[i] === btn;
        all[i].classList.toggle("active", on);
        all[i].setAttribute("aria-pressed", on ? "true" : "false");
      }
      flash();
    });
  }

  /* ---- بقیهٔ ترجیح‌ها ----
     One listener on the document rather than one per control: the settings page
     has ~۱۵ of them and they are all the same three shapes (checkbox, radio,
     select). `data-pref` names the preference, and the element type decides how
     to read it. */
  function controls() {
    document.addEventListener("change", function (e) {
      var el = e.target;
      if (!el || !el.getAttribute) return;
      var key = el.getAttribute("data-pref");
      if (!key) return;

      var value;
      if (el.type === "checkbox") value = el.checked;
      else if (el.type === "radio") { if (!el.checked) return; value = el.value; }
      else value = el.value;

      // Numbers arrive from the DOM as strings; prefs.normalize() coerces them
      // server-side too, but sending the right type keeps window.BN_PREFS —
      // which tables.js and ui.js read directly — honest in the meantime.
      if (key === "rows_per_page" || key === "auto_refresh") value = parseInt(value, 10);

      if (theme()) theme().save(makePatch(key, value));
      flash();
    });

    var reset = document.querySelector('[data-role="reset-prefs"]');
    if (reset) {
      reset.addEventListener("click", function () {
        if (!window.confirm("همهٔ تنظیمات نمایش به حالت اولیه برگردد؟")) return;
        fetch("/api/me/prefs/reset", { method: "POST" })
          .then(function (r) { return r.json(); })
          .then(function () { window.location.reload(); })
          .catch(function () { window.location.reload(); });
      });
    }
  }

  function makePatch(key, value) {
    var patch = {};
    patch[key] = value;
    return patch;
  }

  /* ---- غربالگرهای ذخیره‌شده ---- */
  function screens() {
    var host = document.querySelector('[data-role="screens"]');
    if (!host) return;
    var empty = document.querySelector('[data-role="screens-empty"]');

    var render = function (rows) {
      if (empty) empty.hidden = !!rows.length;
      host.innerHTML = rows.map(function (r) {
        var href = pageUrl(r.page) + (r.query ? "?" + r.query : "");
        return '<div class="screen-item"><div><a class="s-name" href="' + esc(href) + '">' +
          esc(r.name) + '</a><div class="s-meta">' + esc(labelFor(r)) + "</div></div>" +
          '<button type="button" class="screen-del" data-id="' + r.id +
          '" title="حذف" aria-label="حذف">✕</button></div>';
      }).join("");
    };

    fetch("/api/me/screens")
      .then(function (r) { return r.json(); })
      .then(function (d) { render(d.screens || []); })
      .catch(function () { /* the list is a convenience; a failure shows nothing */ });

    host.addEventListener("click", function (e) {
      var btn = e.target.closest ? e.target.closest(".screen-del") : null;
      if (!btn) return;
      fetch("/api/me/screens/" + btn.getAttribute("data-id"), { method: "DELETE" })
        .then(function () { btn.closest(".screen-item").remove(); if (empty) empty.hidden = !!host.children.length; });
    });
  }

  var PAGES = {
    market: "/stocks", screener: "/screener", performance: "/performance",
    strategies: "/strategies", filters: "/filters", heatmap: "/heatmap"
  };
  function pageUrl(page) { return PAGES[page] || "/dashboard"; }
  function labelFor(r) {
    return (r.kind === "etf" ? "صندوق‌ها" : "سهام") + " · " + (r.query || "بدون فیلتر");
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    themePicker();
    controls();
    screens();
  });
})();

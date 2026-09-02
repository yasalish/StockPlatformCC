/* ui.js — میان‌برها، بازدیدهای اخیر، بازگشت به بالا، به‌روزرسانی خودکار
   ---------------------------------------------------------------------------
   The small conveniences every serious market site has and this one did not.
   All of them are additive: nothing here is required for a page to work, and
   each guards on the element it needs actually existing, so a page that does
   not have it simply gets nothing.
*/
const BNUi = (function () {
  "use strict";

  var RECENTS_KEY = "boursenegar-recents";
  var RECENTS_MAX = 8;

  function prefs() { return (window.BN_PREFS || {}); }
  function read(key, fallback) {
    try { return JSON.parse(window.localStorage.getItem(key) || "null") || fallback; }
    catch (e) { return fallback; }
  }
  function write(key, value) {
    try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) {}
  }

  function typing(e) {
    var t = e.target;
    if (!t) return false;
    var tag = t.nodeName;
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t.isContentEditable;
  }

  /* ---- بازدیدهای اخیر ----
     Kept in localStorage rather than on the server on purpose: it is a browsing
     trail, not a saved preference. It should not follow the account to a shared
     machine, and it must work for a visitor who never signs in. */
  function recordVisit() {
    var host = document.querySelector("[data-symbol]");
    if (!host) return;
    var item = {
      kind: host.getAttribute("data-symbol-kind"),
      id: host.getAttribute("data-symbol-id"),
      ticker: host.getAttribute("data-symbol")
    };
    if (!item.ticker || !item.id) return;
    var list = read(RECENTS_KEY, []).filter(function (r) {
      return !(r.kind === item.kind && String(r.id) === String(item.id));
    });
    list.unshift(item);
    write(RECENTS_KEY, list.slice(0, RECENTS_MAX));
  }

  function renderRecents() {
    var host = document.getElementById("bn-recents");
    if (!host) return;
    var list = read(RECENTS_KEY, []);
    if (!list.length) return;
    var html = '<span class="r-label">بازدیدهای اخیر:</span>';
    list.forEach(function (r) {
      var href = (r.kind === "stock" ? "/stock/" : "/etf/") + encodeURIComponent(r.id);
      html += '<a href="' + href + '">' + escapeHtml(r.ticker) +
        '<span class="r-kind">' + (r.kind === "stock" ? "سهم" : "صندوق") + "</span></a>";
    });
    host.innerHTML = html;
    host.hidden = false;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---- بازگشت به بالا ----
     The tables run to hundreds of rows and the filter bar is at the top, so the
     scroll back up is a long one on every page of this app. */
  function backToTop() {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "to-top";
    btn.title = "بازگشت به بالا";
    btn.setAttribute("aria-label", "بازگشت به بالا");
    btn.textContent = "↑";
    btn.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: prefs().reduce_motion ? "auto" : "smooth" });
    });
    document.body.appendChild(btn);
    var onScroll = function () { btn.classList.toggle("on", window.scrollY > 500); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* ---- میان‌برهای صفحه‌کلید ----
     The set is deliberately small and single-key, matching what a market site's
     users already have in their fingers from other terminals: / to search, t to
     flip the theme, f for full-screen table, ? for this list. Anything typed
     into a field is left alone. */
  var SHORTCUTS = [
    ["/", "جستجوی نماد"],
    ["t", "تغییر پوستهٔ روشن/تاریک"],
    ["f", "نمایش تمام‌صفحهٔ جدول"],
    ["g", "رفتن به داشبورد"],
    ["m", "نقشهٔ بازار"],
    ["s", "تنظیمات"],
    ["?", "همین راهنما"],
    ["Esc", "بستن پنجره یا خروج از تمام‌صفحه"]
  ];

  function sheet() {
    var el = document.createElement("div");
    el.className = "kbd-sheet";
    el.innerHTML = '<div class="kbd-card" role="dialog" aria-modal="true" aria-label="میان‌برهای صفحه‌کلید">' +
      "<h2>میان‌برهای صفحه‌کلید</h2><div class=\"kbd-list\">" +
      SHORTCUTS.map(function (s) {
        return "<div><span>" + s[1] + "</span><kbd>" + escapeHtml(s[0]) + "</kbd></div>";
      }).join("") +
      '</div><p class="muted small note">با کلید Esc بسته می‌شود.</p></div>';
    el.addEventListener("click", function (e) { if (e.target === el) el.classList.remove("on"); });
    document.body.appendChild(el);
    return el;
  }

  function shortcuts() {
    var panel = null;
    document.addEventListener("keydown", function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "Escape") {
        if (panel) panel.classList.remove("on");
        return;
      }
      if (typing(e)) return;
      var k = e.key;
      if (k === "/" ) {
        var box = document.getElementById("search");
        if (box) { e.preventDefault(); box.focus(); box.select(); }
      } else if (k === "?" ) {
        e.preventDefault();
        if (!panel) panel = sheet();
        panel.classList.toggle("on");
      } else if (k === "t") {
        if (window.BNTheme) { e.preventDefault(); window.BNTheme.toggle(); }
      } else if (k === "f") {
        var first = document.querySelector(".tbl-tools .tbl-btn");
        if (first) { e.preventDefault(); first.click(); }
      } else if (k === "g") {
        e.preventDefault(); window.location.href = "/dashboard";
      } else if (k === "m") {
        e.preventDefault(); window.location.href = "/heatmap";
      } else if (k === "s") {
        e.preventDefault(); window.location.href = "/settings";
      }
    });
  }

  /* ---- پیمایش نتایج جستجو با صفحه‌کلید ----
     app.js fills the results box; moving through it with the arrow keys is the
     part that was missing, and it is the difference between a search box and a
     symbol picker. */
  function searchKeys() {
    var input = document.getElementById("search");
    var box = document.getElementById("search-results");
    if (!input || !box) return;
    var idx = -1;
    var items = function () { return box.querySelectorAll("a[href]"); };
    var mark = function () {
      var all = items();
      for (var i = 0; i < all.length; i++) all[i].style.background = (i === idx) ? "var(--hover)" : "";
      if (idx >= 0 && all[idx]) all[idx].scrollIntoView({ block: "nearest" });
    };
    input.addEventListener("input", function () { idx = -1; });
    input.addEventListener("keydown", function (e) {
      var all = items();
      if (!all.length) return;
      if (e.key === "ArrowDown") { e.preventDefault(); idx = Math.min(idx + 1, all.length - 1); mark(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); idx = Math.max(idx - 1, 0); mark(); }
      else if (e.key === "Enter" && idx >= 0) { e.preventDefault(); all[idx].click(); }
      else if (e.key === "Escape") { box.style.display = "none"; input.blur(); }
    });
  }

  /* ---- به‌روزرسانی خودکار ----
     Off by default. When on, it reloads a data page on the chosen interval —
     but only while the tab is actually visible, and never on a page carrying
     [data-no-autorefresh] (the settings screen, where it would discard a
     half-made choice, and the update console, which polls its own job status
     and would lose the running log). */
  function autoRefresh() {
    var secs = parseInt(prefs().auto_refresh, 10) || 0;
    if (!secs) return;
    if (document.querySelector("[data-no-autorefresh]")) return;
    var due = Date.now() + secs * 1000;
    setInterval(function () {
      if (Date.now() < due) return;
      if (document.hidden) { due = Date.now() + secs * 1000; return; }
      window.location.reload();
    }, 1000);
  }

  /* ---- منوی کاربر ----
     A dropdown, not a hover menu: hover menus are unreachable on a touchscreen
     and hostile to anyone using a keyboard. Escape closes it, focus returns to
     the trigger, and aria-expanded tracks the state so a screen reader
     announces it. */
  function userMenu() {
    dropdowns('[data-role="user-menu"]', "user-trigger", "user-pop");
  }

  /* ---- منوهای ناوبری («بازار ▾» و «تحلیل ▾») ----
     The nav outgrew a flat bar when شاخص‌ها, تابلوی زنده and پول حقیقی و حقوقی
     arrived: sixteen top-level links do not fit, and the existing
     `overflow-x:auto` turned the difference into a horizontal scroll nobody
     discovers. Two grouped menus bring it back to eight.

     Same machinery as the user menu, so there is ONE dropdown implementation
     rather than two that drift: click to open (not hover — a hover menu is
     unreachable on a touchscreen), Escape closes and returns focus, click
     outside closes, and aria-expanded tracks the state. Opening one closes the
     others so two panels can never overlap. */
  function dropdowns(selector, triggerRole, popRole) {
    var wraps = Array.prototype.slice.call(document.querySelectorAll(selector));
    if (!wraps.length) return;
    var all = [];
    wraps.forEach(function (wrap) {
      var trigger = wrap.querySelector('[data-role="' + triggerRole + '"]');
      var pop = wrap.querySelector('[data-role="' + popRole + '"]');
      if (!trigger || !pop) return;
      var entry = { wrap: wrap, trigger: trigger, pop: pop };
      all.push(entry);
      trigger.addEventListener("click", function (e) {
        e.stopPropagation();
        var willOpen = pop.hidden;
        all.forEach(function (o) { setOpen(o, false); });
        setOpen(entry, willOpen);
      });
    });
    function setOpen(o, on) {
      o.pop.hidden = !on;
      o.trigger.setAttribute("aria-expanded", on ? "true" : "false");
    }
    document.addEventListener("click", function (e) {
      all.forEach(function (o) {
        if (!o.pop.hidden && !o.wrap.contains(e.target)) setOpen(o, false);
      });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      all.forEach(function (o) {
        if (!o.pop.hidden) { setOpen(o, false); o.trigger.focus(); }
      });
    });
  }

  function navMenus() {
    dropdowns('[data-role="nav-menu"]', "nav-trigger", "nav-pop");
  }

  /* ---- ذخیرهٔ نما («غربالگر ذخیره‌شده») ----
     Every filtered screen in this app is fully described by its URL — that is
     why db.saved_screens stores the query string verbatim rather than parsed
     columns. Saving a view is therefore saving the URL you are looking at, and
     restoring one is opening it. A page opts in by carrying
     [data-preset-page]; nothing else has to know the feature exists. */
  function presets() {
    var host = document.querySelector("[data-preset-page]");
    var bar = document.getElementById("bn-actions");
    if (!host || !bar || document.documentElement.getAttribute("data-prefs") !== "server") return;

    var page = host.getAttribute("data-preset-page");
    var kind = host.getAttribute("data-preset-kind") || "stock";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tbl-btn";
    btn.textContent = "★ ذخیرهٔ این نما";
    btn.title = "ذخیرهٔ فیلترهای فعلی این صفحه با یک نام";
    btn.addEventListener("click", function () {
      var name = window.prompt("این نما با چه نامی ذخیره شود؟", document.title.replace(/^.*·\s*/, ""));
      if (!name) return;
      fetch("/api/me/screens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name, kind: kind, page: page,
          query: window.location.search.replace(/^\?/, "")
        })
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          btn.textContent = res.ok ? "★ ذخیره شد" : (res.d.error || "ذخیره نشد");
          setTimeout(function () { btn.textContent = "★ ذخیرهٔ این نما"; }, 2200);
        })
        .catch(function () { btn.textContent = "ذخیره نشد"; });
    });
    bar.appendChild(btn);

    var link = document.createElement("a");
    link.className = "tbl-btn";
    link.href = "/settings#screens";
    link.textContent = "نماهای ذخیره‌شده";
    bar.appendChild(link);
    bar.hidden = false;
  }

  function init() {
    userMenu();
    navMenus();
    presets();
    recordVisit();
    renderRecents();
    backToTop();
    shortcuts();
    searchKeys();
    autoRefresh();
  }

  document.addEventListener("DOMContentLoaded", init);

  var api = { renderRecents: renderRecents };
  try { if (typeof BN !== "undefined" && BN) BN.ui = api; } catch (e) {}
  window.BNUi = api;
  return api;
})();

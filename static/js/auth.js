/* auth.js — منطق صفحه‌های ورود و ثبت‌نام (تم + نمایش گذرواژه) */
(function () {
  "use strict";

  // ---------- Theme ----------
  //  THE SAME KEY THE APP USES. This file used to write "bourse-negar-theme"
  //  while static/js/theme.js reads "boursenegar-theme" — one letter apart, and
  //  the consequence was two separate memories: choose «نیمه‌شب» inside the app
  //  and the login page still opened white, toggle it dark here and the app did
  //  not know. The login page is the app's front door; it has to remember the
  //  same thing the app remembers.
  const THEME_KEY = "boursenegar-theme";
  const PREFS_KEY = "boursenegar-prefs";
  //  auth.css defines a palette for every dark theme id, so any of them may
  //  arrive here from the app's own picker.
  const DARK = ["dark", "midnight", "graphite"];

  function isDark(t) { return DARK.indexOf(t) !== -1; }

  function saved() {
    try {
      const prefs = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {};
      return localStorage.getItem(THEME_KEY) || prefs.theme || "light";
    } catch (e) { return "light"; }         // private browsing
  }

  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    const icon = document.querySelector("#themeToggle i");
    //  The icon shows the DESTINATION, not the current state.
    if (icon) icon.className = isDark(t) ? "fa-solid fa-sun" : "fa-solid fa-moon";
    try { localStorage.setItem(THEME_KEY, t); } catch (e) { /* private mode */ }
  }

  applyTheme(saved());
  const toggle = document.getElementById("themeToggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      //  Crosses to the other FAMILY's default, exactly as prefs.toggle_target()
      //  does server-side: a control whose destination depends on which
      //  alternate you last picked over there is a control nobody trusts.
      applyTheme(isDark(document.documentElement.getAttribute("data-theme"))
                 ? "light" : "dark");
    });
  }

  // ---------- Show / hide password ----------
  document.querySelectorAll(".auth-eye").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = document.getElementById(btn.dataset.target);
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      const icon = btn.querySelector("i");
      if (icon) icon.className = show ? "fa-solid fa-eye-slash" : "fa-solid fa-eye";
    });
  });
})();

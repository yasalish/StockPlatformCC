/* auth.js — منطق صفحه‌های ورود و ثبت‌نام (تم + نمایش گذرواژه) */
(function () {
  "use strict";

  // ---------- Theme (persisted, own key for this platform) ----------
  const THEME_KEY = "bourse-negar-theme";
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    const icon = document.querySelector("#themeToggle i");
    if (icon) icon.className = t === "dark" ? "fa-solid fa-sun" : "fa-solid fa-moon";
    localStorage.setItem(THEME_KEY, t);
  }
  applyTheme(localStorage.getItem(THEME_KEY) || "light");
  const toggle = document.getElementById("themeToggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme");
      applyTheme(cur === "dark" ? "light" : "dark");
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

/* jdatepicker.js — تقویم جلالی مستقل (بدون کتابخانهٔ خارجی)
   A tiny self-contained Jalali (Persian) date picker. Attaches to any
   <input data-jdate>, opens a calendar on focus/click, and writes the value
   back as ASCII "YYYY-MM-DD" (the format the backend parses). Jalali⇄Gregorian
   conversion uses the public-domain jalaali-js algorithm, embedded below. */
const JDatePicker = (function () {
  // ---- jalaali-js core (public domain) ----------------------------------
  const div = (a, b) => ~~(a / b);
  const mod = (a, b) => a - ~~(a / b) * b;

  function jalCal(jy) {
    const breaks = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210,
      1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];
    const bl = breaks.length, gy = jy + 621;
    let leapJ = -14, jp = breaks[0], jm, jump, leap, leapG, march, n, i;
    if (jy < jp || jy >= breaks[bl - 1]) throw new Error("bad jy " + jy);
    for (i = 1; i < bl; i += 1) {
      jm = breaks[i]; jump = jm - jp;
      if (jy < jm) break;
      leapJ = leapJ + div(jump, 33) * 8 + div(mod(jump, 33), 4);
      jp = jm;
    }
    n = jy - jp;
    leapJ = leapJ + div(n, 33) * 8 + div(mod(n, 33) + 3, 4);
    if (mod(jump, 33) === 4 && jump - n === 4) leapJ += 1;
    leapG = div(gy, 4) - div((div(gy, 100) + 1) * 3, 4) - 150;
    march = 20 + leapJ - leapG;
    if (jump - n < 6) n = n - jump + div(jump + 4, 33) * 33;
    leap = mod(mod(n + 1, 33) - 1, 4);
    if (leap === -1) leap = 4;
    return { leap, gy, march };
  }
  function g2d(gy, gm, gd) {
    let d = div((gy + div(gm - 8, 6) + 100100) * 1461, 4) +
      div(153 * mod(gm + 9, 12) + 2, 5) + gd - 34840408;
    d = d - div(div(gy + 100100 + div(gm - 8, 6), 100) * 3, 4) + 752;
    return d;
  }
  function d2g(jdn) {
    let j = 4 * jdn + 139361631;
    j = j + div(div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908;
    const i = div(mod(j, 1461), 4) * 5 + 308;
    const gd = div(mod(i, 153), 5) + 1;
    const gm = mod(div(i, 153), 12) + 1;
    const gy = div(j, 1461) - 100100 + div(8 - gm, 6);
    return { gy, gm, gd };
  }
  function jalToJd(jy, jm, jd) {
    const r = jalCal(jy);
    return g2d(r.gy, 3, r.march) + (jm - 1) * 31 - div(jm, 7) * (jm - 7) + jd - 1;
  }
  function jdToJal(jdn) {
    const gy = d2g(jdn).gy;
    let jy = gy - 621;
    const r = jalCal(jy), jdn1f = jalToJd(jy, 1, 1);
    let k = jdn - jdn1f, jm, jd;
    if (k >= 0) {
      if (k <= 185) { jm = 1 + div(k, 31); jd = mod(k, 31) + 1; return { jy, jm, jd }; }
      k -= 186;
    } else { jy -= 1; k += 179; if (r.leap === 1) k += 1; }
    jm = 7 + div(k, 30); jd = mod(k, 30) + 1;
    return { jy, jm, jd };
  }
  const toJalaali = (gy, gm, gd) => jdToJal(g2d(gy, gm, gd));
  const toGregorian = (jy, jm, jd) => d2g(jalToJd(jy, jm, jd));
  const isLeap = (jy) => jalCal(jy).leap === 0;
  const monthLen = (jy, jm) => (jm <= 6 ? 31 : jm <= 11 ? 30 : (isLeap(jy) ? 30 : 29));

  // ---- helpers -----------------------------------------------------------
  const MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"];
  const WEEK = ["ش", "ی", "د", "س", "چ", "پ", "ج"];   // شنبه … جمعه
  const FA = "۰۱۲۳۴۵۶۷۸۹";
  const fa = (s) => String(s).replace(/[0-9]/g, (d) => FA[d]);
  const toAscii = (s) => String(s).replace(/[۰-۹]/g, (d) => "۰۱۲۳۴۵۶۷۸۹".indexOf(d));
  const pad = (n) => (n < 10 ? "0" + n : "" + n);

  function todayJ() {
    const d = new Date();
    return toJalaali(d.getFullYear(), d.getMonth() + 1, d.getDate());
  }
  function parse(v) {
    const m = toAscii(v || "").trim().match(/^(\d{3,4})-(\d{1,2})-(\d{1,2})$/);
    if (!m) return null;
    const jy = +m[1], jm = +m[2], jd = +m[3];
    if (jm < 1 || jm > 12 || jd < 1 || jd > monthLen(jy, jm)) return null;
    return { jy, jm, jd };
  }
  // Jalali column (0=Saturday) for day 1 of a month
  function firstCol(jy, jm) {
    const g = toGregorian(jy, jm, 1);
    const wd = new Date(g.gy, g.gm - 1, g.gd).getDay();   // 0=Sun … 6=Sat
    return (wd + 1) % 7;                                   // 6(Sat)->0
  }

  let pop = null, curInput = null, view = null;

  function close() {
    if (pop) { pop.remove(); pop = null; curInput = null; }
    document.removeEventListener("click", onDocClick, true);
    document.removeEventListener("keydown", onKey);
  }
  function onDocClick(e) {
    if (pop && !pop.contains(e.target) && e.target !== curInput) close();
  }
  function onKey(e) { if (e.key === "Escape") close(); }

  function render() {
    const sel = parse(curInput.value);
    const { jy, jm, mode } = view;
    let html = "";

    if (mode === "year") {
      // year picker — a page of 12 years; ‹ › step the page by 12
      const base = view.ybase;
      html += `<div class="jdp-head">
          <button type="button" class="jdp-nav" data-step="-1">›</button>
          <span class="jdp-title">${fa(base)} – ${fa(base + 11)}</span>
          <button type="button" class="jdp-nav" data-step="1">‹</button>
        </div><div class="jdp-grid jdp-mgrid">`;
      for (let y = base; y < base + 12; y++) {
        html += `<button type="button" class="jdp-cell${y === jy ? " sel" : ""}" data-y="${y}">${fa(y)}</button>`;
      }
      html += `</div>`;
    } else if (mode === "month") {
      // month picker — the year is clickable to jump up to the year picker
      html += `<div class="jdp-head">
          <button type="button" class="jdp-nav" data-step="-1">›</button>
          <button type="button" class="jdp-title jdp-toyear">${fa(jy)}</button>
          <button type="button" class="jdp-nav" data-step="1">‹</button>
        </div><div class="jdp-grid jdp-mgrid">`;
      MONTHS.forEach((mn, i) => {
        html += `<button type="button" class="jdp-cell${i + 1 === jm ? " sel" : ""}" data-m="${i + 1}">${mn}</button>`;
      });
      html += `</div>`;
    } else {
      // day picker — month name and year in the title are each clickable
      html += `<div class="jdp-head">
          <button type="button" class="jdp-nav" data-step="-1">›</button>
          <span class="jdp-title">
            <button type="button" class="jdp-tbtn jdp-tomonth">${MONTHS[jm - 1]}</button>
            <button type="button" class="jdp-tbtn jdp-toyear">${fa(jy)}</button>
          </span>
          <button type="button" class="jdp-nav" data-step="1">‹</button>
        </div><div class="jdp-grid">`;
      WEEK.forEach((w) => (html += `<span class="jdp-wd">${w}</span>`));
      const off = firstCol(jy, jm), len = monthLen(jy, jm);
      for (let i = 0; i < off; i++) html += `<span class="jdp-empty"></span>`;
      const t = todayJ();
      for (let d = 1; d <= len; d++) {
        const isSel = sel && sel.jy === jy && sel.jm === jm && sel.jd === d;
        const isToday = t.jy === jy && t.jm === jm && t.jd === d;
        html += `<button type="button" class="jdp-day${isSel ? " sel" : ""}${isToday ? " today" : ""}" data-d="${d}">${fa(d)}</button>`;
      }
      html += `</div>`;
    }
    html += `<div class="jdp-foot"><button type="button" class="jdp-today">امروز</button></div>`;
    pop.innerHTML = html;
  }

  function open(input) {
    close();
    curInput = input;
    const p = parse(input.value) || todayJ();
    view = { jy: p.jy, jm: p.jm, mode: "day", ybase: p.jy - 5 };
    pop = document.createElement("div");
    pop.className = "jdp";
    document.body.appendChild(pop);
    render();
    const r = input.getBoundingClientRect();
    pop.style.top = (window.scrollY + r.bottom + 4) + "px";
    pop.style.left = (window.scrollX + r.left) + "px";

    pop.addEventListener("click", (e) => {
      const nav = e.target.closest(".jdp-nav");
      if (nav) {
        const step = +nav.dataset.step;
        if (view.mode === "year") view.ybase += step * 12;
        else if (view.mode === "month") view.jy += step;
        else {
          view.jm += step;
          if (view.jm < 1) { view.jm = 12; view.jy -= 1; }
          if (view.jm > 12) { view.jm = 1; view.jy += 1; }
        }
        render(); return;
      }
      // title → month / year picker
      if (e.target.closest(".jdp-tomonth")) { view.mode = "month"; render(); return; }
      if (e.target.closest(".jdp-toyear")) { view.mode = "year"; view.ybase = view.jy - 5; render(); return; }
      // choose a month → back to the day grid (same year)
      const mcell = e.target.closest("[data-m]");
      if (mcell) { view.jm = +mcell.dataset.m; view.mode = "day"; render(); return; }
      // choose a year → back to the day grid (same month)
      const ycell = e.target.closest("[data-y]");
      if (ycell) { view.jy = +ycell.dataset.y; view.mode = "day"; render(); return; }
      if (e.target.classList.contains("jdp-today")) {
        const t = todayJ(); view = { jy: t.jy, jm: t.jm, mode: "day", ybase: t.jy - 5 };
        pick(t.jy, t.jm, t.jd); return;
      }
      const day = e.target.closest(".jdp-day");
      if (day) pick(view.jy, view.jm, +day.dataset.d);
    });
    setTimeout(() => {
      document.addEventListener("click", onDocClick, true);
      document.addEventListener("keydown", onKey);
    }, 0);
  }

  function pick(jy, jm, jd) {
    curInput.value = `${jy}-${pad(jm)}-${pad(jd)}`;
    curInput.dispatchEvent(new Event("change", { bubbles: true }));
    close();
  }

  function attach(input) {
    input.setAttribute("autocomplete", "off");
    const openIf = () => { if (!input.disabled) open(input); };
    input.addEventListener("focus", openIf);
    input.addEventListener("click", openIf);
  }

  function init(selector) {
    document.querySelectorAll(selector || "[data-jdate]").forEach(attach);
  }

  document.addEventListener("DOMContentLoaded", () => init());
  return { init, attach, toJalaali, toGregorian };
})();

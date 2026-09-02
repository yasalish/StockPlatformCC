/* ===========================================================================
   landing.js — the product carousel and reveal-on-scroll, for index.html only
   ===========================================================================
   Loaded only by index.html. Deliberately not part of ui.js: every other page
   in this app pays for ui.js on load, and marketing behaviour has no business
   in that budget.

   Both features degrade to a complete, static page. The carousel's first slide
   and the dots' initial state are rendered by the server, and `.l-reveal` on
   its own paints nothing — so if this file is blocked or throws, the visitor
   gets one screenshot and all the copy rather than a blank hero.

   The contract with landing.css for the reveal:

     .l-reveal            — declared in the markup, no visual effect by itself
     .l-reveal.is-armed   — hidden and offset, ADDED BY THIS FILE
     .l-reveal.is-in      — visible, transitions back to its resting position

   Arming from JavaScript rather than from the stylesheet is the important part.
   If this file fails to load, is blocked, or throws, `.l-reveal` alone paints
   nothing at all — so the page renders complete and static instead of blank.
   The usual arrangement (hide in CSS, reveal in JS) turns any script failure
   into an empty page.
   --------------------------------------------------------------------------- */

/* ---------------------------------------------------------------------------
   Are we allowed to move things?
   ---------------------------------------------------------------------------
   Three separate ways the visitor can have asked for less motion: the OS
   setting, this app's own «کاهش حرکت» preference (prefs.py writes data-motion
   on <html>), and an old browser that should not be running scroll maths at
   all. Both features below consult this, so the answer is worked out once.
   --------------------------------------------------------------------------- */
function bnReduceMotion() {
  "use strict";
  try {
    return (
      (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) ||
      document.documentElement.getAttribute("data-motion") === "reduce"
    );
  } catch (e) {
    return true;
  }
}

/* ===========================================================================
   1. The product carousel
   ===========================================================================
   Five real screenshots of the real pages, stacked and cross-faded. The server
   renders the first slide with `is-on` already, so the hero shows a screenshot
   before this file runs — and if it never runs, that one screenshot is simply
   what the page has. The dots stay hidden until `is-ready` is set here, because
   a control that ignores the click is worse than no control.
   --------------------------------------------------------------------------- */
(function () {
  "use strict";

  var root = document.querySelector('[data-role="shot-carousel"]');
  if (!root) return;

  var slides = root.querySelectorAll(".l-slide");
  var dots = root.querySelectorAll('[data-role="shot-dot"]');
  var urlOut = root.querySelector('[data-role="shot-url"]');
  if (slides.length < 2 || dots.length !== slides.length) return;

  var current = 0;
  var timer = null;
  var stopped = bnReduceMotion();
  var DWELL = 5200;

  function show(i) {
    if (i === current) return;
    slides[current].classList.remove("is-on");
    // The off slides stay in the layout at opacity 0, so without this a screen
    // reader would announce five screenshots for one visible frame.
    slides[current].setAttribute("aria-hidden", "true");
    dots[current].classList.remove("is-on");
    dots[current].setAttribute("aria-selected", "false");
    current = (i + slides.length) % slides.length;
    slides[current].classList.add("is-on");
    slides[current].removeAttribute("aria-hidden");
    dots[current].classList.add("is-on");
    dots[current].setAttribute("aria-selected", "true");
    // The browser-chrome bar doubles as the caption, so it has to follow.
    if (urlOut) urlOut.textContent = slides[current].getAttribute("data-url") || "";
  }

  function start() {
    if (stopped || timer) return;
    timer = window.setInterval(function () { show(current + 1); }, DWELL);
  }
  function pause() {
    if (timer) { window.clearInterval(timer); timer = null; }
  }
  /* A click means the visitor is steering. Advancing under them a few seconds
     later would take the page they just chose away, so autoplay ends for good
     rather than merely pausing. */
  function surrender() { stopped = true; pause(); }

  for (var i = 0; i < dots.length; i++) {
    (function (idx) {
      dots[idx].addEventListener("click", function () { surrender(); show(idx); });
    })(i);
  }

  // Arrow keys walk the tablist. In RTL the visually-next pill is to the LEFT,
  // so the mapping follows the document direction rather than the key's name.
  root.addEventListener("keydown", function (ev) {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    var rtl = document.documentElement.getAttribute("dir") === "rtl";
    var forward = ev.key === (rtl ? "ArrowLeft" : "ArrowRight");
    ev.preventDefault();
    surrender();
    show(current + (forward ? 1 : -1));
    dots[current].focus();
  });

  // Hover and keyboard focus both mean "I am looking at this one".
  root.addEventListener("mouseenter", pause);
  root.addEventListener("mouseleave", start);
  root.addEventListener("focusin", pause);
  root.addEventListener("focusout", start);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) pause(); else start();
  });

  root.classList.add("is-ready");

  /* Slides 2-5 are marked loading="lazy" so they stay out of the critical path,
     but they are also visibility:hidden, which some browsers treat as "not
     needed yet" — the first advance would then show an empty frame while the
     image fetches. Promoting them once the page has finished loading gets them
     into cache during the first slide's dwell, off the critical path either
     way. */
  function warm() {
    for (var k = 0; k < slides.length; k++) {
      var img = slides[k].querySelector("img");
      if (img && img.loading === "lazy") img.loading = "eager";
    }
    start();
  }
  if (document.readyState === "complete") warm();
  else window.addEventListener("load", warm);
})();

/* ===========================================================================
   2. Reveal on scroll
   =========================================================================== */
(function () {
  "use strict";

  var nodes = document.querySelectorAll(".l-reveal");
  if (!nodes.length) return;

  // Reduced motion, or a browser with no IntersectionObserver — one that has no
  // business running scroll maths. Either way: leave every section visible.
  if (bnReduceMotion() || typeof IntersectionObserver !== "function") return;

  var obs = new IntersectionObserver(function (entries) {
    for (var i = 0; i < entries.length; i++) {
      if (!entries[i].isIntersecting) continue;
      var el = entries[i].target;
      el.classList.add("is-in");
      // One-shot: re-animating a section every time it scrolls back into view
      // is the thing that makes these pages feel restless.
      obs.unobserve(el);
    }
  }, {
    // Fire a little before the element's top edge reaches the fold, so the
    // motion has finished by the time the reader's eye arrives.
    rootMargin: "0px 0px -12% 0px",
    threshold: 0.08
  });

  for (var i = 0; i < nodes.length; i++) {
    // A section already on screen at load must not animate — it would flash
    // in behind the reader. Only arm what starts below the fold.
    var box = nodes[i].getBoundingClientRect();
    if (box.top < window.innerHeight * 0.9) continue;
    nodes[i].classList.add("is-armed");
    // Stagger siblings within a group, capped so a long list never ends up
    // waiting half a second for its last item.
    var step = Number(nodes[i].getAttribute("data-reveal-step") || 0);
    if (step) nodes[i].style.transitionDelay = Math.min(step * 70, 280) + "ms";
    obs.observe(nodes[i]);
  }
})();

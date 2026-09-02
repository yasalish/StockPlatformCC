/* ===========================================================================
   landing.js — reveal-on-scroll for the landing page, and nothing else
   ===========================================================================
   Loaded only by index.html. Deliberately not part of ui.js: every other page
   in this app pays for ui.js on load, and a marketing animation has no business
   in that budget.

   The contract with landing.css:

     .l-reveal            — declared in the markup, no visual effect by itself
     .l-reveal.is-armed   — hidden and offset, ADDED BY THIS FILE
     .l-reveal.is-in      — visible, transitions back to its resting position

   Arming from JavaScript rather than from the stylesheet is the important part.
   If this file fails to load, is blocked, or throws, `.l-reveal` alone paints
   nothing at all — so the page renders complete and static instead of blank.
   The usual arrangement (hide in CSS, reveal in JS) turns any script failure
   into an empty page.
   --------------------------------------------------------------------------- */
(function () {
  "use strict";

  var nodes = document.querySelectorAll(".l-reveal");
  if (!nodes.length) return;

  // Three ways the visitor can have asked for less motion: the OS setting, this
  // app's own «کاهش حرکت» preference (prefs.py writes data-motion on <html>),
  // and the absence of IntersectionObserver, which means an old browser that
  // should not be running scroll maths at all. In every case: show everything.
  var reduced = false;
  try {
    reduced = (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) ||
              document.documentElement.getAttribute("data-motion") === "reduce";
  } catch (e) { reduced = true; }

  if (reduced || typeof IntersectionObserver !== "function") return;

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

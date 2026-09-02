/* ===========================================================================
   errors.js — report JavaScript failures to the server (review finding C-4)
   ===========================================================================
   Before this file there was NO client-side error visibility anywhere: Sentry
   was wired for Flask, but a repository-wide search for a browser SDK,
   window.onerror or unhandledrejection found nothing. On the wide, older
   Android population this app serves, a bundle that throws on a common device
   presented as silent attrition — the user sees the server-rendered fallback,
   never reports it, and simply leaves. The server logs looked healthy the
   whole time.

   WHY NOT THE SENTRY BROWSER SDK, WHICH THE REVIEW ASKED FOR

   Three reasons, all specific to this deployment:

     1. It is ~25 KB gzipped of third-party JavaScript on every page, for an
        audience on slow mobile connections, to report an event that is a few
        hundred bytes.
     2. Every other vendor asset in this project is self-hosted because CDNs
        are unreliable from Iran (see the Vazirmatn note in style.css), so it
        would have to be vendored and kept up to date by hand.
     3. observability.setup_sentry() already installs Sentry's
        LoggingIntegration, so anything the server logs at ERROR becomes a
        Sentry event. Posting to our own endpoint therefore lands in exactly
        the same place, through infrastructure that already exists — and works
        when SENTRY_DSN is empty, where the SDK would report nowhere.

   The trade is real and worth stating: no source-mapped stack frames, no
   breadcrumb trail, no release health. What this buys is the thing that was
   missing — knowing that something broke, on which page, in which browser.

   RULES THIS FILE FOLLOWS

   * It must never throw. An error reporter that fails is worse than none,
     because it turns one broken feature into a broken page.
   * It must not loop. An error inside a reporting path could fire the handler
     again; the send count is capped and identical errors are sent once.
   * It must not leak. Query strings are stripped from the reported URL —
     `?next=/somewhere` and any future token-bearing parameter never leave the
     browser. The user agent is not sent either: the server already has it on
     the request.
   --------------------------------------------------------------------------- */
(function () {
  "use strict";

  var ENDPOINT = "/api/client-error";
  //: Per page load. Two is enough to see a failure and its knock-on; beyond
  //: that a report storm costs the user bandwidth and tells us nothing new.
  var MAX_SENDS = 4;
  var sent = 0;
  var seen = {};

  function cleanUrl(u) {
    try {
      var url = new URL(String(u || ""), location.href);
      // Path only. No query, no fragment — see the leak rule above.
      return url.origin === location.origin ? url.pathname : url.origin + url.pathname;
    } catch (e) {
      return "";
    }
  }

  function send(report) {
    if (sent >= MAX_SENDS) return;
    // One report per distinct failure. A handler firing in a render loop would
    // otherwise post hundreds of identical rows.
    var sig = report.message + "|" + report.source + "|" + report.line;
    if (seen[sig]) return;
    seen[sig] = 1;
    sent += 1;

    var body;
    try {
      body = JSON.stringify(report);
    } catch (e) {
      return;
    }

    try {
      // sendBeacon survives the page being closed, which is exactly when a
      // fatal error tends to happen. It is also fire-and-forget, so it cannot
      // block anything the user is doing.
      if (navigator.sendBeacon) {
        var blob = new Blob([body], { type: "application/json" });
        if (navigator.sendBeacon(ENDPOINT, blob)) return;
      }
      // keepalive so the request is not cancelled by the navigation that a
      // fatal error often triggers.
      fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
        keepalive: true,
        credentials: "same-origin",
      }).catch(function () { /* reporting is best-effort by definition */ });
    } catch (e) {
      /* never throw from the reporter */
    }
  }

  function base() {
    return {
      page: cleanUrl(location.href),
      release: String(window.BN_RELEASE || ""),
      // Viewport rather than a device string: it is the thing that actually
      // correlates with layout-triggered failures, and it identifies nobody.
      viewport: (window.innerWidth || 0) + "x" + (window.innerHeight || 0),
    };
  }

  window.addEventListener("error", function (ev) {
    try {
      // Resource errors (a failed <img> or <script>) arrive on the same event
      // with no `error` object. They are worth knowing about — a missing
      // bundle is why an island would silently not appear — but they are a
      // different kind and are tagged as such.
      if (ev && ev.target && ev.target !== window && ev.target.tagName) {
        var el = ev.target;
        var src = el.src || el.href || "";
        if (!src) return;
        var r = base();
        r.kind = "resource";
        r.message = el.tagName.toLowerCase() + " failed to load";
        r.source = cleanUrl(src);
        r.line = 0;
        send(r);
        return;
      }
      var rep = base();
      rep.kind = "error";
      rep.message = String((ev && ev.message) || "unknown error").slice(0, 300);
      rep.source = cleanUrl(ev && ev.filename);
      rep.line = (ev && ev.lineno) || 0;
      rep.col = (ev && ev.colno) || 0;
      rep.stack = ev && ev.error && ev.error.stack ? String(ev.error.stack).slice(0, 2000) : "";
      send(rep);
    } catch (e) { /* never throw */ }
  }, true);   // capture phase: resource errors do not bubble

  window.addEventListener("unhandledrejection", function (ev) {
    try {
      var reason = ev && ev.reason;
      var rep = base();
      rep.kind = "unhandledrejection";
      rep.message = String(
        (reason && (reason.message || reason)) || "unhandled rejection"
      ).slice(0, 300);
      rep.source = "";
      rep.line = 0;
      rep.stack = reason && reason.stack ? String(reason.stack).slice(0, 2000) : "";
      send(rep);
    } catch (e) { /* never throw */ }
  });
})();

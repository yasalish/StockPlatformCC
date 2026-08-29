"""
verify_nav_loader.py — the «در حال محاسبه…» overlay must wait, not flash

    «all time in app i see a window come in screen and closed fast»

That window was #nav-loader: a full-screen blurred backdrop with a box in the
middle of the screen, put up the instant any link was clicked or any form was
submitted. Measured on this machine, a page of this app answers in 47–220 ms —
so on virtually every click the box appeared and vanished again inside a tenth
of a second, on every page of the platform. A box that flashes on and off reads
as the app glitching, not as progress.

It is not removed: it is the answer to a filter submit that really does take
several seconds. It now waits 400 ms first, so a navigation that beats the
delay shows nothing at all and a slow one still explains itself.

  A  Source — the show is deferred, and hiding cancels a pending one (otherwise
     a fast navigation would still flash the box at the very end, once the timer
     fired against a page that had already arrived).
  B  Browser — node + Chrome/Edge. Five normal navigations: the overlay must
     NEVER appear, measured across the navigation rather than inside the
     document that the navigation destroys. Then one navigation held back for
     1.5 s: the overlay MUST appear, after the delay and not before.

Needs: the «Stock» database, node, and Chrome or Edge. --no-browser skips B.

Run:  python verify_nav_loader.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(HERE, "frontend")
sys.path.insert(0, HERE)
os.chdir(HERE)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BROWSER = "--no-browser" not in sys.argv
FAIL = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(label)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ===========================================================================
print("=" * 74)
print("PART A — the overlay is deferred, not immediate")
print("=" * 74)

js = read("static/js/app.js")
check("SHOW_AFTER_MS" in js and "setTimeout(" in js.split("const show = ()")[1][:200],
      "show() schedules the overlay instead of adding the class")
check("if (pending) { clearTimeout(pending); pending = null; }" in js,
      "hide() cancels a pending show",
      "pagehide fires when the new document is ready — a navigation that beat "
      "the timer must not flash the box on its way out")
check("if (shown || pending) return;" in js,
      "…and a second trigger does not stack a second timer")

css = read("static/css/style.css")
check("nav-loader-in" in css and "prefers-reduced-motion" in css,
      "when it does appear it fades in rather than popping")


# ===========================================================================
print()
print("=" * 74)
print("PART B — in a real browser")
print("=" * 74)

if not BROWSER:
    print("  SKIP  --no-browser")
else:
    import threading
    import werkzeug.serving

    import app as A
    import db

    uid = db._one("SELECT id FROM users ORDER BY id LIMIT 1")["id"]
    srv = werkzeug.serving.make_server("127.0.0.1", 5096, A.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    with A.app.test_request_context():
        cookie = A.app.session_interface.get_signing_serializer(A.app).dumps(
            {"_user_id": str(uid), "_fresh": True})

    proc = None
    try:
        proc = subprocess.run(
            ["node", "loader_check.mjs", "http://127.0.0.1:5096", cookie],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=FRONTEND, timeout=600, shell=(os.name == "nt"))
        out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except Exception as e:
        out = {}
        check(False, "the browser check ran", f"{e} {proc.stderr[:300] if proc else ''}")
    finally:
        srv.shutdown()

    if out:
        check(not out.get("errors"), "no JavaScript errors",
              "; ".join(out.get("errors") or [])[:300])

        for c in out["fast"]:
            if not c.get("found"):
                print(f"      skip  {c['label']}: no such nav link")
                continue
            check(c["atClick"] == "not-yet",
                  f"{c['label']}: nothing appears at the moment of the click",
                  f"navigation took {c['navMs']} ms")
            check(not c["everShown"],
                  f"{c['label']}: …and the overlay never appears at all during it")
            check(not c["stillOn"],
                  f"{c['label']}: …and the page that arrives is not covered by it")

        s = out.get("slow") or {}
        check(s.get("appeared"),
              "a navigation held back for 1.5 s DOES put the box up",
              f"after {s.get('appearedAfterMs')} ms")
        if s.get("appeared"):
            check(s["appearedAfterMs"] >= 350,
                  "…and not before the delay it is supposed to wait out",
                  f"{s['appearedAfterMs']} ms")


# ===========================================================================
print()
print("=" * 74)
print(f"{len(FAIL)} FAILED" if FAIL else "ALL CHECKS PASSED")
for f in FAIL:
    print("  x", f)
print("=" * 74)
sys.exit(1 if FAIL else 0)

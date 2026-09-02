"""landing_shots.py — regenerate the product screenshots on the landing page.

    python landing_shots.py

Run it whenever a page it photographs changes shape. It rewrites
static/shots/*.webp in place, so the landing page picks the new images up on the
next request with no template edit.

WHY IT BOOTS ITS OWN SERVER

Every page worth photographing sits behind app._require_login, and the point of
this script is to produce screenshots without anyone having to type a password
into a build tool. So it starts a SECOND, private instance of the app on a free
port with that one before_request handler removed, photographs it, and shuts it
down. Nothing is written to the database, no session is created, and the
instance is never reachable from outside the loopback interface.

That is safe here because the guard is the only thing being dropped: the admin
checks inside /update stay where they are, and the process exits when the
screenshots are done. It is NOT a pattern to copy into anything long-lived.

WHY WEBP

These are five wide screenshots of dense tables. As PNG they are around 200 KB
each and the hero would cost a megabyte before a visitor read a word; as WebP at
quality 80 they land near 60 KB with no visible difference on a photograph of a
UI. Pillow is already a dependency, so there is no new tooling either.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

from PIL import Image
from werkzeug.serving import make_server

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "static", "shots")
WIDTH, HEIGHT = 1600, 900
WEBP_QUALITY = 80


def _free_port() -> int:
    """Ask the OS for a port nobody is using, rather than guessing one.

    Guessing collides with the copy of the app the developer already has
    running, which is exactly the failure this avoids.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _node() -> str:
    exe = shutil.which("node")
    if not exe:
        sys.exit("node is not on PATH — needed to drive the browser")
    return exe


def main() -> int:
    import app as bn

    flask_app = bn.app

    # Drop the login gate BY NAME, so any other before_request handler (and
    # every after_request handler) stays exactly where it was.
    funcs = flask_app.before_request_funcs.get(None, [])
    kept = [f for f in funcs if getattr(f, "__name__", "") != "_require_login"]
    if len(kept) == len(funcs):
        print("warning: _require_login was not found — the app may have changed")
    flask_app.before_request_funcs[None] = kept

    port = _free_port()
    # threaded=True because the pages fetch their own data: a single-threaded
    # server deadlocks the moment a page's own XHR arrives while the page is
    # still being served.
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    print(f"private instance on {base} (login gate removed in this process only)")

    try:
        # Wait for it to actually accept, rather than sleeping a magic number.
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            return 1

        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    _node(),
                    os.path.join(HERE, "frontend", "landing_shots.mjs"),
                    base,
                    tmp.replace("\\", "/"),
                    str(WIDTH),
                    str(HEIGHT),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=HERE,
            )
            sys.stderr.write(proc.stderr or "")
            if proc.returncode != 0:
                print(f"browser step failed ({proc.returncode})")
                return proc.returncode

            report = json.loads(proc.stdout)
            os.makedirs(OUT_DIR, exist_ok=True)

            print()
            total = 0
            for shot in report["shots"]:
                png = shot["file"]
                if not os.path.exists(png):
                    print(f"  MISSING {shot['name']}")
                    continue
                webp = os.path.join(OUT_DIR, shot["name"] + ".webp")
                with Image.open(png) as im:
                    im.convert("RGB").save(
                        webp, "WEBP", quality=WEBP_QUALITY, method=6
                    )
                size = os.path.getsize(webp)
                total += size
                png_size = os.path.getsize(png)
                print(
                    f"  {shot['name']:<12} {shot['status']:<10} "
                    f"{png_size // 1024:>4} KB png -> {size // 1024:>3} KB webp"
                )

            print(f"\n  {len(report['shots'])} shots, {total // 1024} KB total, in {OUT_DIR}")
            not_ready = [s["name"] for s in report["shots"] if s["status"] != "ok"]
            if not_ready:
                print(f"  NOT READY when photographed: {', '.join(not_ready)}")
                return 1
    finally:
        server.shutdown()

    return 0


if __name__ == "__main__":
    sys.exit(main())

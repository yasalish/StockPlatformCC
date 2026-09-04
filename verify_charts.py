# -*- coding: utf-8 -*-
"""جهت محور زمان و نمودار دلار — permanent assertions for the chart direction.

Two things this file exists to stop coming back:

1. THE DIRECTION OF TIME. Every chart in the app draws the oldest session on
   the LEFT and the newest on the RIGHT. It has been the other way round, and
   the wrongness of it is invisible in a diff — you only see it on screen. The
   checks below pin the direction in app.spark(), in the pointer maths of
   BN.priceChart and in the two keyboard maps that have to follow it.

2. THE DOLLAR'S UNITS. /indices quotes the dollar in تومان and usd_rial stores
   ریال. Getting that wrong puts the same rate on one screen twice, ten times
   apart, which is exactly the bug that shipped once already.

    python verify_charts.py
"""
import re
import sys
import pathlib

# Persian labels on a Windows console default to cp1252 and raise. Same guard
# the other verify scripts carry.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK, BAD = [], []


def check(cond, label, detail=""):
    (OK if cond else BAD).append(label)
    print(f"  {'✓' if cond else '✗'} {label}{('  — ' + str(detail)) if detail else ''}")


def source(path):
    return pathlib.Path(path).read_text(encoding="utf-8")


print("=" * 74)
print("  جهت محور زمان")
print("=" * 74)

import app as A                                    # noqa: E402
import market_data as md                           # noqa: E402
import db                                          # noqa: E402

# ---- 1. app.spark(): x must ASCEND with time -------------------------------
# `points` — the per-sample hover data — is only emitted when labels are asked
# for, and it is the one place the x projection is readable from outside.
sp = A.spark([10, 20, 30, 40], w=100, h=20,
             labels=["۱۴۰۴-۰۱-۰۱", "۱۴۰۴-۰۱-۰۲", "۱۴۰۴-۰۱-۰۳", "۱۴۰۴-۰۱-۰۴"])
xs = [float(p.split(":")[0]) for p in sp["points"].split("|")]
check(xs == sorted(xs), "spark(): x ascends with time (oldest at the left)", xs)
check(xs[0] == 0 and xs[-1] == 100, "spark(): the series spans the full width", (xs[0], xs[-1]))

# The area has to close on the ACTUAL endpoints, not on hard-coded corners —
# that is what lets the direction be flipped by editing one line.
check(f"L{xs[-1]:.1f} " in sp["area"] and f"L{xs[0]:.1f} " in sp["area"],
      "spark(): the fill closes on the real first and last x", sp["area"][-28:])

# A one-point series must not divide by zero.
check(A.spark([5]) is None or True, "spark(): a one-point series is handled")

# ---- 2. BN.priceChart: the same direction ----------------------------------
js = source("static/js/app.js")
xdef = re.search(r"const x = \(i\) => padL \+ [^;]+;", js)
check(bool(xdef) and "plotW -" not in (xdef.group(0) if xdef else ""),
      "priceChart: x() ascends with time (no 'plotW -' term)",
      xdef.group(0)[:60] if xdef else "not found")
check("const t = (vx - padL) / plotW;" in js,
      "priceChart: indexFromClientX is the matching inverse")
check(re.search(r'if \(ev\.key === "ArrowRight"\) \{ ev\.preventDefault\(\); showAt\(\(active < 0 \? -1 : active\) \+ 1\)',
                js) is not None,
      "priceChart: ArrowRight steps FORWARD in time")
check('else if (ev.key === "Home") { ev.preventDefault(); showAt(0); }' in js,
      "priceChart: Home is the oldest session (the left edge)")

# ---- 3. spark-hover.js keyboard follows spark() -----------------------------
sh = source("static/js/spark-hover.js")
check('if (ev.key === "ArrowRight") { ev.preventDefault(); show((active < 0 ? -1 : active) + 1); }' in sh,
      "spark-hover: ArrowRight steps FORWARD in time")
check('else if (ev.key === "Home") { ev.preventDefault(); show(0); }' in sh,
      "spark-hover: Home is the oldest session")

print()
print("=" * 74)
print("  نمودار دلار در صفحهٔ شاخص‌ها")
print("=" * 74)

usd = md.usd_summary()
rows = md.usd_rows(bars=240)
if not usd or not rows:
    check(False, "the dollar table has data to chart")
else:
    check(rows[0][0] < rows[-1][0], "usd_rows() is oldest→newest",
          f"{rows[0][0]} … {rows[-1][0]}")
    # The LAST point is the right edge, and it must be the headline rate.
    check(rows[-1][0] == usd["j_date"],
          "the chart's right edge is the same session as «آخرین داده»",
          f"{rows[-1][0]} vs {usd['j_date']}")
    check(float(rows[-1][1]) == float(usd["value"]),
          "…and the same rate, so the axis matches the table", rows[-1][1])

tpl = source("templates/indices.html")
check('id="usd-chart"' in tpl, "the dollar panel carries its own chart box")
check('.map(v => v / 10)' in tpl, "the dollar series is converted ریال → تومان")
check('const inToman = (key, vs) => key === "usd" ? vs.map(v => v / 10) : vs;' in tpl,
      "…and the picker chart converts too, when the dollar is focused")

row = db._one("SELECT id FROM users ORDER BY id LIMIT 1")
if not row:
    check(False, "a user exists to sign the test client in as")
else:
    client = A.app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(row["id"])
        sess["_fresh"] = True

    page = client.get("/indices")
    html = page.get_data(as_text=True)
    check(page.status_code == 200, "/indices renders", page.status_code)
    check('<div id="usd-chart"' in html, "…with the dollar chart in the markup")
    check("let focusKey =" in html, "…and the focused key reaches the script")
    # The series the dollar chart draws must actually be in the page.
    m = re.search(r'const values = \(\[([^\]]*)\]\)\.map\(v => v / 10\)', html)
    check(bool(m) and len(m.group(1).split(",")) > 20,
          "…carrying a real series, not an empty array",
          f"{len(m.group(1).split(',')) if m else 0} points")

    # ?focus=usd has to survive a reload — the page writes that URL itself.
    p2 = client.get("/indices?focus=usd")
    h2 = p2.get_data(as_text=True)
    check(p2.status_code == 200, "/indices?focus=usd renders", p2.status_code)
    check('<h2 id="idx-chart-title">دلار آزاد</h2>' in h2,
          "…and stays on the dollar instead of falling back to شاخص کل")
    check('let focusKey = "usd";' in h2, "…so the chart knows to draw تومان")

    check(client.get("/indices?focus=nonsense").status_code == 200,
          "an unknown focus key still renders the page")

    # ---- the dashboard, whose charts are server-rendered SVG ---------------
    #
    # NOT "/": the dashboard's charts arrive from /dashboard/data as a lazily
    # loaded fragment, so grepping the shell page finds no <path> at all and
    # every direction check passes vacuously. That is exactly how a broken
    # chart would slip through, so the count is asserted before the direction.
    frag = client.get("/dashboard/data")
    fh = frag.get_data(as_text=True)
    check(frag.status_code == 200, "/dashboard/data renders", frag.status_code)
    check("شاخص کل" in fh and "دلار آزاد" in fh,
          "…carrying both the index and the dollar card")

    lines = re.findall(r'class="spark-line[^"]*" d="M([\d.]+) ', fh)
    check(len(lines) >= 5, "…with sparklines actually in the markup", len(lines))
    check(bool(lines) and all(x == "0.0" for x in lines),
          "…every one of them starting at x=0, i.e. oldest on the left",
          sorted(set(lines)))

    for m in re.finditer(r'data-spark-points="([^"]{200,})"', fh):
        rows_ = [q.split(":") for q in m.group(1).split("|")]
        xs_ = [float(q[0]) for q in rows_]
        check(xs_ == sorted(xs_),
              f"hoverable chart ({len(xs_)} pts) ascends with time",
              f"{rows_[0][2]} → {rows_[-1][2]}")

print()
print("=" * 74)
print(f"  {len(OK)} گذشت · {len(BAD)} ناموفق")
print("=" * 74)
if BAD:
    for b in BAD:
        print("  ✗", b)
sys.exit(1 if BAD else 0)

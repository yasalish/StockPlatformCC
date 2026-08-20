"""tests/test_update_page_states.py — the page must never take the form away

Everything else about the update is tested against Python. This is tested
against the actual JavaScript, because the bug was in the JavaScript and no
amount of Python could have caught it:

    «when i stop the updating, it stopped but the update part is gone from the
     page, i refresh page but no update part that i can select date and stocks
     or etfs for update»

«توقف» worked. The job then moved to 'finalizing' — every symbol done, the
analytics rebuilding — and render() hid #form-panel for any job it considered
`running`, which included that state. The rebuild took ten minutes (measured,
21:57→22:07), so the whole feature disappeared from the page for ten minutes,
survived a reload, and refused to start a new run. From the outside that is
indistinguishable from the page being broken.

So: render the real template, run its real script over a stand-in DOM, and
assert on the one thing the user was looking for — whether the form is there.

Skipped, not failed, where node or Flask is unavailable: this file needs both,
unlike its neighbours.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

node = shutil.which("node")
pytestmark = pytest.mark.skipif(not node, reason="needs node to run the page's JS")

# One job, as /update/status reports it. Overridden per state below.
STATUS = {
    "active": True, "running": True, "job_id": 1, "kind": "etf",
    "start": "1405-05-25", "end": "1405-05-28", "full": False, "subset": 0,
    "total": 293, "stopped": False, "paused": False, "processed": 14,
    "success": 13, "failed": 1, "success_list": ["ابتکار"],
    "failed_list": [{"ticker": "----", "reason": "بدون داده", "attempts": 3,
                     "detail": ""}],
    "current": "ابتکار", "result": None, "elapsed": 140, "attempts_total": 15,
    "idle": 5, "stalled": False, "queued": False, "source": "manual",
    "workers": {"fetch": True, "maintenance": True},
}

SUMMARY = {"stock_latest": "1405-05-24", "etf_latest": "1405-05-24",
           "stocks": 780, "etfs": 293}

DOM = r"""
const els = {};
function el(id) {
  if (!els[id]) els[id] = {
    // display starts as "" like a real element with no inline style, so an
    // element nothing ever touches reports "" rather than undefined — which
    // JSON.stringify would drop, turning "never hidden" into a missing key.
    id, style: { display: "" }, dataset: {}, textContent: "", innerHTML: "",
    disabled: false, className: "", value: "", checked: false,
    parentElement: { style: {} },
    addEventListener() {}, closest() { return null },
  };
  return els[id];
}
global.document = { getElementById: el, querySelectorAll: () => [],
                    querySelector: () => null, addEventListener() {} };
global.window = {};
global.fetch = () => Promise.reject(new Error("no network here"));
global.setTimeout = () => 0;
global.alert = () => {};
global.confirm = () => false;
"""

REPORT = r"""
const s = __STATUS__;
render(s);
console.log(JSON.stringify({
  form: document.getElementById("form-panel").style.display,
  runDisabled: document.getElementById("run-btn").disabled,
  runText: document.getElementById("run-btn").textContent,
  stop: document.getElementById("stop-btn").style.display,
  pause: document.getElementById("pause-btn").style.display,
  now: document.getElementById("prog-now").style.display,
  stall: document.getElementById("prog-stall").style.display,
  stallText: document.getElementById("prog-stall").textContent,
  msg: document.getElementById("prog-msg").textContent,
  current: document.getElementById("prog-current").textContent,
  retryAllDisabled: document.getElementById("retry-all-btn").disabled,
}));
"""


@pytest.fixture(scope="module")
def page_js():
    """Every inline <script> of a rendered /update, in order.

    Rendered through Flask rather than Jinja directly, so the context
    processors that supply asset_version() and prefs_json run."""
    webapp = pytest.importorskip("app")
    flask = pytest.importorskip("flask")
    with webapp.app.test_request_context("/update"):
        html = flask.render_template(
            "update.html", summary=SUMMARY, updater_available=True,
            updater_error=None, yesterday="1405-05-28",
            stock_next="1405-05-25", etf_next="1405-05-25", status=STATUS)
    js = "\n".join(body for tag, body in
                   re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S)
                   if "src=" not in tag)
    assert "function render" in js, "the progress script is not in the page"
    return js


def render_state(page_js, tmp_path, **over):
    status = dict(STATUS, **over)
    script = DOM + page_js + REPORT.replace(
        "__STATUS__", json.dumps(status, ensure_ascii=False))
    path = tmp_path / "page.js"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([node, str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr[:2000]
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# the regression
# ---------------------------------------------------------------------------
ALL_STATES = [
    dict(status="queued", current=None, processed=0),
    dict(status="running"),
    dict(status="stopping", stopped=True),
    dict(status="finalizing", stopped=True, current=None),
    dict(status="stopped", running=False, stopped=True, current=None,
         result="RESULT ok=13 fail=1 total=293"),
    dict(status="done", running=False, current=None,
         result="RESULT ok=293 fail=0 total=293"),
]


@pytest.mark.parametrize("over", ALL_STATES, ids=lambda o: o["status"])
def test_the_fetch_form_is_on_the_page_in_every_state(page_js, tmp_path, over):
    """«دریافت قیمت‌های جدید» is never taken off the page. It used to be hidden
    for the whole of a run AND for the whole of the analytics rebuild after it,
    and a page whose controls are gone cannot be told apart from a broken one.
    While a run is in flight the RUN BUTTON carries the reason instead — which
    is the only thing hiding the panel was communicating."""
    out = render_state(page_js, tmp_path, **over)
    assert out["form"] != "none", (
        f"the fetch form must be visible in '{over['status']}'")


def test_the_run_button_says_why_it_cannot_be_pressed_mid_run(page_js, tmp_path):
    out = render_state(page_js, tmp_path, status="running")
    assert out["runDisabled"] is True
    assert "در حال اجرا" in out["runText"], out["runText"]


def test_the_run_button_is_live_again_during_the_rebuild(page_js, tmp_path):
    """'finalizing' collides with nothing — see jobs.blocking_job_id()."""
    out = render_state(page_js, tmp_path, status="finalizing", stopped=True,
                       current=None)
    assert out["runDisabled"] is False
    assert "اجرای به‌روزرسانی" in out["runText"], out["runText"]
    assert out["retryAllDisabled"] is False
    assert "پس‌زمینه" in out["msg"], (
        "and it must say why the progress panel is still there: " + out["msg"])


def test_nothing_to_stop_means_no_stop_button(page_js, tmp_path):
    """A REFRESH MATERIALIZED VIEW cannot be interrupted half-way without
    leaving the analytics inconsistent. A greyed-out button next to a form that
    has come back reads as a page that cannot decide what state it is in."""
    out = render_state(page_js, tmp_path, status="finalizing", stopped=True,
                       current=None)
    assert out["stop"] == "none"
    assert out["pause"] == "none"


def test_a_run_in_flight_still_offers_the_stop_and_names_its_symbol(page_js,
                                                                   tmp_path):
    """The other half: two runs at once really would collide, so the form must
    not INVITE one while symbols are being fetched — but it stays readable, and
    the page keeps saying which symbol it is on."""
    out = render_state(page_js, tmp_path, status="running")
    assert out["runDisabled"] is True
    assert out["stop"] == ""
    assert out["retryAllDisabled"] is True
    assert out["current"] == "ابتکار"


def test_a_stop_in_progress_still_shows_the_stop_it_is_honouring(page_js, tmp_path):
    out = render_state(page_js, tmp_path, status="stopping", stopped=True)
    assert out["runDisabled"] is True, "a symbol is still in flight"
    assert "در حال توقف" in out["msg"], out["msg"]


def test_a_queued_job_does_not_claim_to_be_fetching(page_js, tmp_path):
    """'queued' means no worker has taken a batch yet. The spinner over a symbol
    name that was never fetched is what «we can't see which stock is updating
    now» looked like from the outside."""
    out = render_state(page_js, tmp_path, status="queued", current=None,
                       processed=0, success=0, failed=0, success_list=[],
                       failed_list=[])
    assert out["now"] == "none"
    assert "در صف" in out["msg"], out["msg"]


def test_a_finished_run_leaves_the_form_ready(page_js, tmp_path):
    out = render_state(page_js, tmp_path, status="stopped", running=False,
                       stopped=True, current=None,
                       result="RESULT ok=13 fail=1 total=293")
    assert out["form"] != "none"
    assert out["runDisabled"] is False
    assert out["stop"] == "none"


# ---------------------------------------------------------------------------
# the stall notice, which is the other thing the page used to hide
# ---------------------------------------------------------------------------
def test_a_stalled_run_says_so_and_names_the_missing_worker(page_js, tmp_path):
    """A run that is not moving used to be indistinguishable from a slow one:
    the «در حال دریافت» line held the last symbol it had ever seen. The reader
    can only act on this if the page says which worker is gone."""
    out = render_state(page_js, tmp_path, status="running", stalled=True,
                       idle=3600, current=None,
                       workers={"fetch": False, "maintenance": True})
    assert out["stall"] == "", "the warning must be visible"
    assert "fetch" in out["stallText"], out["stallText"]
    assert "maintenance" not in out["stallText"], (
        "only the worker that is actually missing: " + out["stallText"])
    assert "۳۶۰۰" in out["stallText"], (
        "say how long it has been silent, in Persian digits: " + out["stallText"])


def test_a_stall_with_both_workers_alive_reports_the_recovery_instead(page_js,
                                                                     tmp_path):
    """Nothing to restart — the watchdog is the answer, so say that rather than
    accusing a healthy worker."""
    out = render_state(page_js, tmp_path, status="running", stalled=True,
                       idle=200, current=None,
                       workers={"fetch": True, "maintenance": True})
    assert out["stall"] == ""
    assert "بازیابی" in out["stallText"], out["stallText"]


def test_a_healthy_run_shows_no_warning(page_js, tmp_path):
    out = render_state(page_js, tmp_path, status="running", stalled=False)
    assert out["stall"] == "none"

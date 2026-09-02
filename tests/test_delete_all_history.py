"""tests/test_delete_all_history.py — «کلیهٔ سوابق» on the /update delete panel

The panel could only ever delete inside a from/to range: to clear a symbol's
whole history you had to guess a start date early enough to cover it, and to
empty a table you had to do that for every symbol. «کلیهٔ سوابق» removes the
range from the request, and «همه (سهام و صندوق‌ها)» removes the table from it.

Both are one checkbox away from wiping the price tables, so the things worth
locking down are the guards, not the happy path:

  · the date range is still MANDATORY unless all_history is passed explicitly —
    it is what stops a stray call with no dates from deleting everything, so it
    must never be inferred from dates simply being absent;
  · with the flag set, no date clause reaches the SQL at all (a bound silently
    kept would make «کلیهٔ سوابق» a lie);
  · a ticker still narrows the delete, flag or no flag;
  · kind="all" touches both tables and reports the two counts as one.

The JavaScript half is tested against the real rendered page, like
test_update_page_states.py: the button is what sends `all_history`, and a
checkbox that greys out the date inputs but forgets to put the flag in the body
would delete one day and claim it deleted everything.
"""
import json
import re
import shutil
import subprocess

import pytest


# ---------------------------------------------------------------------------
# db.delete_price_history() — what actually reaches the database
# ---------------------------------------------------------------------------
class RecordingCursor:
    def __init__(self, sink, rowcount):
        self.sink = sink
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.sink.append((" ".join(sql.split()), list(params or [])))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RecordingConn:
    def __init__(self, sink, rowcount):
        self.sink, self._rowcount = sink, rowcount
        self.committed = False

    def cursor(self, *a, **k):
        return RecordingCursor(self.sink, self._rowcount)

    def commit(self):
        self.committed = True


@pytest.fixture
def db_mod(monkeypatch):
    db = pytest.importorskip("db")
    monkeypatch.setattr(db, "release", lambda conn: None)
    monkeypatch.setattr(db, "clear_cache", lambda: None)
    # A Jalali bound would otherwise be resolved against a real table.
    monkeypatch.setattr(db, "_date_for", lambda kind, d, side: "2026-08-15")
    return db


@pytest.fixture
def sql(monkeypatch, db_mod):
    """Every statement delete_price_history() issues, as (sql, params)."""
    sink = []
    monkeypatch.setattr(db_mod, "get_db", lambda: RecordingConn(sink, 12))
    return sink


def test_a_range_delete_still_carries_both_bounds(db_mod, sql):
    db_mod.delete_price_history("stock", start="1405-05-25", end="1405-05-27")
    stmt, params = sql[0]
    assert "DELETE FROM stockpricehistory WHERE date >= %s AND date <= %s" == stmt
    assert params == ["2026-08-15", "2026-08-15"]


def test_all_history_drops_the_date_bounds_entirely(db_mod, sql):
    """No WHERE at all: «کلیهٔ سوابق» for every symbol empties the table."""
    n = db_mod.delete_price_history("etf", all_history=True)
    stmt, params = sql[0]
    assert stmt == "DELETE FROM etfpricehistory", stmt
    assert params == []
    assert n == 12


def test_all_history_still_honours_a_single_ticker(db_mod, sql):
    """The whole history OF ONE SYMBOL — the flag widens the dates, not the
    scope. A ticker dropped here would wipe the table for a one-symbol click."""
    db_mod.delete_price_history("stock", ticker="فولاد", all_history=True)
    stmt, params = sql[0]
    assert stmt == "DELETE FROM stockpricehistory WHERE ticker = %s", stmt
    assert params == ["فولاد"]


def test_dates_left_over_from_the_form_are_ignored_when_the_flag_is_set(db_mod, sql):
    """The page keeps the (disabled) date inputs populated and sends them
    regardless, so the flag has to win over whatever bounds arrive with it."""
    db_mod.delete_price_history("stock", start="1405-05-25", end="1405-05-27",
                                all_history=True)
    stmt, _ = sql[0]
    assert "date" not in stmt, stmt


def test_a_delete_with_no_dates_and_no_flag_still_raises(db_mod, sql):
    """The guard the whole design rests on. Without it, any caller that forgot
    to pass a range would silently mean «everything»."""
    with pytest.raises(ValueError):
        db_mod.delete_price_history("stock")
    assert sql == [], "nothing may reach the database on that path"


def test_an_unknown_kind_is_refused_even_with_the_flag(db_mod, sql):
    with pytest.raises(ValueError):
        db_mod.delete_price_history("bonds", all_history=True)
    assert sql == []


# ---------------------------------------------------------------------------
# /update/delete — the route's own fan-out and validation
# ---------------------------------------------------------------------------
@pytest.fixture
def call(monkeypatch):
    """POST a JSON body to update_delete() and get (status, payload, calls).

    The view is called directly rather than through the test client: the
    platform's before_request gate would otherwise need a logged-in admin, and
    what is under test here is the fan-out, not the login."""
    webapp = pytest.importorskip("app")
    monkeypatch.setattr(webapp.market, "refresh_analytics_async", lambda why: True)
    calls, forgotten = [], []

    def fake_delete(kind, ticker=None, start=None, end=None, all_history=False):
        calls.append({"kind": kind, "ticker": ticker, "start": start,
                      "end": end, "all_history": all_history})
        return 5

    def fake_forget(kind, ticker=None, start=None, end=None, all_history=False):
        forgotten.append({"kind": kind, "ticker": ticker, "start": start,
                          "end": end, "all_history": all_history})
        return 3

    monkeypatch.setattr(webapp.db, "delete_price_history", fake_delete)
    monkeypatch.setattr(webapp.market, "forget_completed", fake_forget)

    def run(**body):
        with webapp.app.test_request_context("/update/delete", json=body):
            res = webapp.update_delete()
        payload, status = (res if isinstance(res, tuple) else (res, 200))
        return status, payload.get_json(), calls

    run.forgotten = forgotten
    return run


def test_kind_all_deletes_from_both_tables_and_sums_the_counts(call):
    status, body, calls = call(kind="all", all_history=True)
    assert status == 200 and body["ok"] is True
    assert [c["kind"] for c in calls] == ["stock", "etf"]
    assert body["deleted"] == 10, "both tables' counts, reported as one number"


def test_all_history_needs_no_dates(call):
    status, body, calls = call(kind="stock", all_history=True)
    assert status == 200 and body["ok"] is True
    assert calls[0]["all_history"] is True


def test_the_range_is_still_required_without_the_flag(call):
    status, body, calls = call(kind="stock")
    assert status == 400 and body["ok"] is False
    assert calls == [], "a rangeless delete must not reach db"


def test_a_backwards_range_is_still_refused(call):
    status, body, _ = call(kind="stock", start_date="1405-05-27",
                           end_date="1405-05-25")
    assert status == 400 and body["ok"] is False


def test_a_backwards_range_does_not_block_all_history(call):
    """The dates are ignored, so they must not be validated either — the page
    sends whatever the disabled inputs happen to hold."""
    status, body, _ = call(kind="stock", start_date="1405-05-27",
                           end_date="1405-05-25", all_history=True)
    assert status == 200 and body["ok"] is True


def test_an_unknown_kind_is_refused(call):
    status, body, calls = call(kind="bonds", all_history=True)
    assert status == 400 and calls == []


def test_the_delete_also_clears_the_resume_marks(call):
    """Otherwise the download that follows a wipe skips every symbol it just
    emptied: an earlier job is still on record as having fetched them for that
    window, and already_done_elsewhere() believes it. Same scope as the delete,
    kind for kind."""
    call(kind="all", all_history=True)
    assert [f["kind"] for f in call.forgotten] == ["stock", "etf"]
    assert all(f["all_history"] is True for f in call.forgotten)


def test_a_single_symbol_delete_only_clears_that_symbols_marks(call):
    call(kind="stock", ticker="فولاد", start_date="1405-05-25",
         end_date="1405-05-27")
    assert call.forgotten == [{"kind": "stock", "ticker": "فولاد",
                               "start": "1405-05-25", "end": "1405-05-27",
                               "all_history": False}]


def test_a_refused_delete_clears_nothing(call):
    call(kind="stock")                       # no dates, no flag → 400
    assert call.forgotten == []


# ---------------------------------------------------------------------------
# jobs.forget_completed() — the resume marks a delete invalidates
# ---------------------------------------------------------------------------
@pytest.fixture
def jobs_sql(monkeypatch):
    """(sql, params) of every statement jobs.forget_completed() issues."""
    jobs = pytest.importorskip("jobs")
    sink = []

    def fake_write(sql, params=(), fetch=False, count=False):
        sink.append((" ".join(sql.split()), list(params)))
        return 4

    monkeypatch.setattr(jobs, "_write", fake_write)
    return jobs, sink


def test_forget_only_touches_finished_jobs_of_that_kind(jobs_sql):
    """A run in flight owns its rows; rewriting them underneath it would hand
    symbols it has already fetched back to the queue."""
    jobs, sink = jobs_sql
    jobs.forget_completed("stock", all_history=True)
    stmt, params = sink[0]
    assert "j.finished_at IS NOT NULL" in stmt
    assert "t.status = 'ok'" in stmt and "status = 'purged'" in stmt
    assert "j.start_date" not in stmt, "«کلیهٔ سوابق» invalidates every window"
    assert params == [jobs.PURGED_MARK, "stock"]


def test_forget_matches_overlapping_windows_not_identical_ones(jobs_sql):
    """Deleting one day out of a month invalidates the month's mark too — that
    run can no longer be said to have covered the window."""
    jobs, sink = jobs_sql
    jobs.forget_completed("etf", start="1405-05-25", end="1405-05-27")
    stmt, params = sink[0]
    assert "j.start_date <= %s" in stmt and "j.end_date >= %s" in stmt
    assert params == [jobs.PURGED_MARK, "etf", "1405-05-27", "1405-05-25"], (
        "the bounds are crossed on purpose: overlap is start<=end_of_delete "
        "AND end>=start_of_delete")


def test_forget_narrows_to_one_symbol_when_the_delete_did(jobs_sql):
    jobs, sink = jobs_sql
    jobs.forget_completed("stock", ticker="فولاد", all_history=True)
    stmt, params = sink[0]
    assert "t.ticker = %s" in stmt
    assert params[-1] == "فولاد"


def test_a_purged_mark_no_longer_counts_as_done():
    """The whole point: already_done_elsewhere() skips on status 'ok', and
    'purged' is not it — nor is it a terminal state that would stop the symbol
    being handed out again."""
    jobs = pytest.importorskip("jobs")
    assert "purged" not in jobs.DONE_STATES


# ---------------------------------------------------------------------------
# the panel's JavaScript — what the checkbox actually sends
# ---------------------------------------------------------------------------
node = shutil.which("node")

DOM = r"""
const els = {}, listeners = {};
function el(id) {
  if (!els[id]) els[id] = {
    id, style: { opacity: "", display: "" }, textContent: "", value: "",
    checked: false, disabled: false, className: "",
    parentElement: { style: {}, parentElement: { style: {} } },
    _label: { textContent: "" },
    querySelector(sel) { return sel === ".btn-label" ? this._label : null },
    addEventListener(ev, fn) { (listeners[id] = listeners[id] || {})[ev] = fn; },
  };
  return els[id];
}

/*  «نوع» on the delete panel is a real <select> now: it lists every dataset the
    update page can write, and each option declares whether that dataset HAS a
    date range and a ticker column. The handler reads those flags to decide what
    to send, so the stand-in has to carry them — a select with no options is not
    a state the page can be in.

    data-dated / data-sym mirror the template. The defaults are the price
    tables' (both true), which is what every test in this file exercises.  */
const DEL_KINDS = {
  stock: { dated: "1", sym: "1" }, etf: { dated: "1", sym: "1" },
  all:   { dated: "1", sym: "1" }, ri:  { dated: "1", sym: "1" },
  index: { dated: "1", sym: "0" }, usd: { dated: "1", sym: "0" },
  watch: { dated: "1", sym: "1" }, orderbook: { dated: "0", sym: "1" },
  queue: { dated: "1", sym: "1" }, intraday_ob: { dated: "1", sym: "1" },
  intraday_trades: { dated: "1", sym: "1" }, shareholders: { dated: "0", sym: "1" },
};
(function (sel) {
  sel.options = Object.keys(DEL_KINDS).map((k) => ({
    value: k, textContent: k, dataset: DEL_KINDS[k],
  }));
  Object.defineProperty(sel, "selectedOptions", {
    get() { return [sel.options.find((o) => o.value === sel.value) || sel.options[0]]; },
  });
})(el("del-kind"));
function fire(id, ev) { return listeners[id][ev](); }
global.document = { getElementById: el, querySelectorAll: () => [],
                    querySelector: () => null, addEventListener() {} };
global.window = {};
const sent = [], asked = [];
global.confirm = (m) => { asked.push(m); return true; };
global.alert = () => {};
global.setTimeout = () => 0;
global.fetch = (url, opt) => {
  sent.push(JSON.parse(opt.body));
  return Promise.resolve({ json: () => Promise.resolve({ ok: true, deleted: 7,
                                                         all: !JSON.parse(opt.body).ticker }) });
};
"""

DRIVER = r"""
(async () => {
  const setup = __SETUP__;
  el("del-kind").value = setup.kind;
  el("del-start").value = setup.start;
  el("del-end").value = setup.end;
  if (setup.ticker) el("del-ticker").value = setup.ticker;
  if (setup.allSymbols) { el("del-all").checked = true; fire("del-all", "change"); }
  if (setup.allHistory) { el("del-history").checked = true; fire("del-history", "change"); }
  await fire("del-btn", "click");
  console.log(JSON.stringify({
    sent, asked,
    startDisabled: el("del-start").disabled,
    endDisabled: el("del-end").disabled,
    startFaded: el("del-start-lbl").style.opacity,
    msg: el("del-msg").textContent,
  }));
})();
"""


@pytest.fixture(scope="module")
def panel_js():
    """The shared inline script of a rendered /update page.

    Rendered with updater_available=False on purpose: the delete panel and its
    script sit OUTSIDE that guard (deleting rows needs no finpy_tse), so this
    isolates them from the progress console's own script and its polling."""
    webapp = pytest.importorskip("app")
    flask = pytest.importorskip("flask")
    with webapp.app.test_request_context("/update"):
        html = flask.render_template(
            "update.html", summary={"stock_latest": "1405-05-24",
                                    "etf_latest": "1405-05-24",
                                    "stocks": 780, "etfs": 293,
                                    "stock_rows": 2051797, "etf_rows": 218283},
            updater_available=False, updater_error="not installed",
            yesterday="1405-05-28", stock_next="1405-05-25",
            etf_next="1405-05-25", status={"active": False},
            # «پوشش داده» sits OUTSIDE the finpy_tse guard, for the same reason
            # the delete panel does: what the database already holds is a fact
            # worth showing on a machine that cannot fetch anything.
            fresh={"ri": {"latest": None, "rows": 0},
                   "ri_stock": {"latest": None}, "ri_etf": {"latest": None},
                   "index": {"latest": None, "rows": 0},
                   "usd": {"latest": None, "rows": 0},
                   "watch": {"latest": None, "rows": 0},
                   "orderbook": {"captured": None, "rows": 0},
                   "queue": {"latest": None, "rows": 0, "symbols": 0},
                   "intraday_ob": {"latest": None, "rows": 0, "symbols": 0},
                   "intraday_trades": {"latest": None, "rows": 0, "symbols": 0},
                   "shareholders": {"latest": None, "rows": 0}})
    js = "\n".join(body for tag, body in
                   re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S)
                   if "src=" not in tag)
    assert "del-history" in js, "the «کلیهٔ سوابق» checkbox is not wired up"
    return js


def click_delete(panel_js, tmp_path, **setup):
    setup.setdefault("kind", "stock")
    setup.setdefault("start", "1405-05-25")
    setup.setdefault("end", "1405-05-28")
    script = DOM + panel_js + DRIVER.replace(
        "__SETUP__", json.dumps(setup, ensure_ascii=False))
    path = tmp_path / "panel.js"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([node, str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr[:2000]
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not node, reason="needs node")
def test_checking_all_records_disables_and_fades_the_date_fields(panel_js, tmp_path):
    """The range no longer applies, so the page must stop presenting it as if
    it did — a live date field next to a delete that ignores it is a lie about
    what the button is going to do."""
    out = click_delete(panel_js, tmp_path, allHistory=True, allSymbols=True)
    assert out["startDisabled"] is True and out["endDisabled"] is True
    assert out["startFaded"] == ".45"


@pytest.mark.skipif(not node, reason="needs node")
def test_the_request_carries_the_flag_and_not_a_silent_range(panel_js, tmp_path):
    out = click_delete(panel_js, tmp_path, allHistory=True, allSymbols=True)
    body = out["sent"][0]
    assert body["all_history"] is True
    assert body["ticker"] == "" and body["kind"] == "stock"


@pytest.mark.skipif(not node, reason="needs node")
def test_a_plain_range_delete_does_not_set_the_flag(panel_js, tmp_path):
    out = click_delete(panel_js, tmp_path, ticker="فولاد")
    body = out["sent"][0]
    assert body["all_history"] is False
    assert body["start_date"] == "1405-05-25" and body["end_date"] == "1405-05-28"


@pytest.mark.skipif(not node, reason="needs node")
def test_emptying_everything_asks_twice(panel_js, tmp_path):
    """All symbols + all history + both tables is the one click that leaves the
    platform with no prices. A single confirm() is the same muscle memory as
    every other delete on the page, so it gets a second, differently worded
    one."""
    out = click_delete(panel_js, tmp_path, kind="all", allHistory=True,
                       allSymbols=True)
    assert len(out["asked"]) == 2, out["asked"]
    assert "تأیید نهایی" in out["asked"][1]
    assert out["sent"][0]["kind"] == "all"


@pytest.mark.skipif(not node, reason="needs node")
def test_a_single_symbols_history_is_only_confirmed_once(panel_js, tmp_path):
    """The second prompt is for the irreversible-for-everyone case only; asking
    it on every delete is how people learn to click through it."""
    out = click_delete(panel_js, tmp_path, ticker="فولاد", allHistory=True)
    assert len(out["asked"]) == 1, out["asked"]
    assert out["sent"][0]["ticker"] == "فولاد"


@pytest.mark.skipif(not node, reason="needs node")
def test_all_records_does_not_excuse_a_missing_symbol(panel_js, tmp_path):
    """«کلیهٔ سوابق» widens the dates; it says nothing about which symbol. With
    neither a ticker nor «حذف همهٔ نمادها» the panel must still refuse."""
    out = click_delete(panel_js, tmp_path, allHistory=True)
    assert out["sent"] == []
    assert "نماد" in out["msg"], out["msg"]

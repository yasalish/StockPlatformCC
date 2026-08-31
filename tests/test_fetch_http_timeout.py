"""tests/test_fetch_http_timeout.py — a fetch must never be able to wait for ever

Pure tests: no network, no database, no worker. `requests.Session.request` is
replaced with a recorder, and what is asserted is the TIMEOUT ARGUMENT that
reaches it.

THE FAILURE THIS LOCKS DOWN

An ETF update stopped at 197 of 293 and stayed there. The page kept saying
«در حال اجرا…» and «بازیابی خودکار در جریان است», the «ادامه از جایی که مانده»
button reported «۹۶ نماد دوباره در صف قرار گرفت» — and nothing moved, for 73
minutes, until the worker was killed by hand.

The chain, in full:

  1. finpy_tse fetches every symbol with `requests.get(url, headers=headers)`
     and never passes `timeout`. A `requests` call without one waits FOREVER.
  2. A TCP connection to TSETMC stalled without a reset — routine on a network
     where a filtering middlebox blackholes a connection rather than refusing
     it — so the read never returned. The symbol («طلوع») was left claimed, with
     started_at set and finished_at null.
  3. The Windows worker runs `--pool=solo`, which executes the task inline in
     the CONSUMER thread. So the worker stopped taking messages at the same
     instant: 212 batches piled up in Redis behind the blocked read, and every
     re-queue the watchdog performed added to a queue nothing was left to drain.
  4. celery_app.py sets `task_time_limit` (1800 s) — and it did not fire,
     because time limits are implemented by the PREFORK pool killing the child
     that runs the task. Solo has no child. The ceiling was configured and
     inert, which is why nothing in the logs said anything was wrong.

So the protection cannot live in Celery's configuration; it has to live at the
HTTP layer, which is the same on every pool and every platform. That is
`tse_fetch._install_http_timeout()`, and these tests are what keep it there.
"""
import pytest

tse_fetch = pytest.importorskip("tse_fetch")
requests = pytest.importorskip("requests")


@pytest.fixture
def recorder(monkeypatch):
    """Replace Session.request with something that records how it was called and
    never touches a socket. Restores the original afterwards, and resets the
    module's install-once flag so each test sees a clean slate."""
    seen = []

    def fake(self, method, url, *args, **kwargs):
        seen.append({"method": method, "url": url, "args": args, "kwargs": kwargs})
        return "response"

    monkeypatch.setattr(requests.Session, "request", fake, raising=True)
    monkeypatch.setattr(tse_fetch, "_TIMEOUT_INSTALLED", False, raising=False)
    return seen


# ---------------------------------------------------------------------------
# 1. A call with no timeout gets one
# ---------------------------------------------------------------------------
def test_timeout_is_injected_when_the_caller_gives_none(recorder, monkeypatch):
    monkeypatch.setenv("TSE_HTTP_TIMEOUT", "7,21")
    tse_fetch._install_http_timeout()

    # Exactly the shape finpy_tse uses: requests.get(url, headers=...).
    requests.get("http://old.tsetmc.com/tsev2/data/InstTradeHistory.aspx?i=1",
                 headers={"User-Agent": "x"})

    assert len(recorder) == 1
    assert recorder[0]["kwargs"]["timeout"] == (7.0, 21.0), (
        "a finpy-style call must not be able to reach the socket without a "
        "timeout — that is the whole bug"
    )


def test_the_default_is_used_when_the_environment_says_nothing(recorder, monkeypatch):
    monkeypatch.delenv("TSE_HTTP_TIMEOUT", raising=False)
    tse_fetch._install_http_timeout()
    requests.get("http://cdn.tsetmc.com/api/x")
    connect, read = recorder[0]["kwargs"]["timeout"]
    assert connect > 0 and read > 0
    # A read timeout is "no bytes for N seconds", not a deadline for the whole
    # download, so it has to be comfortably longer than a slow-but-alive
    # response — and still far below Celery's visibility_timeout (900 s), or a
    # batch would be redelivered while it is still legitimately working.
    assert 10 <= read <= 300


# ---------------------------------------------------------------------------
# 2. …and a caller that supplies one still wins
# ---------------------------------------------------------------------------
def test_an_explicit_timeout_is_never_overridden(recorder, monkeypatch):
    monkeypatch.setenv("TSE_HTTP_TIMEOUT", "7,21")
    tse_fetch._install_http_timeout()
    requests.get("http://cdn.tsetmc.com/api/x", timeout=(1, 2))
    assert recorder[0]["kwargs"]["timeout"] == (1, 2)


def test_a_positional_timeout_is_never_overridden(recorder, monkeypatch):
    """`timeout` is the 7th positional parameter after `url`. Nothing in this
    codebase passes it that way, but injecting a second one would raise
    TypeError inside the fetch — a worse failure than the one being fixed."""
    monkeypatch.setenv("TSE_HTTP_TIMEOUT", "7,21")
    tse_fetch._install_http_timeout()
    session = requests.Session()
    #        params data headers cookies files auth timeout
    session.request("GET", "http://x/", None, None, None, None, None, None, 5)
    assert "timeout" not in recorder[0]["kwargs"]
    assert recorder[0]["args"][6] == 5


# ---------------------------------------------------------------------------
# 3. Installing twice must not stack wrappers
# ---------------------------------------------------------------------------
def test_installing_twice_leaves_one_wrapper(recorder, monkeypatch):
    monkeypatch.setenv("TSE_HTTP_TIMEOUT", "7,21")
    tse_fetch._install_http_timeout()
    first = requests.Session.request
    tse_fetch._TIMEOUT_INSTALLED = False       # as a fresh import would look
    tse_fetch._install_http_timeout()
    assert requests.Session.request is first, (
        "each install must detect the previous one; stacking wrappers on every "
        "fetch would grow a chain as long as the job"
    )


# ---------------------------------------------------------------------------
# 4. A malformed setting must not disable the protection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "abc", "10", "10,20,30", "a,b"])
def test_a_broken_setting_falls_back_rather_than_removing_the_timeout(bad, monkeypatch):
    monkeypatch.setenv("TSE_HTTP_TIMEOUT", bad)
    connect, read = tse_fetch._http_timeout()
    assert connect > 0 and read > 0, (
        f"TSE_HTTP_TIMEOUT={bad!r} must fall back to the default, not to None — "
        "None is the value that waits for ever"
    )


# ---------------------------------------------------------------------------
# 5. fetch() installs it before it can make a request
# ---------------------------------------------------------------------------
def test_fetch_installs_the_timeout_before_calling_finpy(monkeypatch):
    """The install has to happen on the fetch path itself. Doing it at import
    time would miss any process that imports tse_fetch without fetching, and
    doing it after the finpy call would be too late by definition."""
    order = []
    monkeypatch.setattr(tse_fetch, "_install_http_timeout",
                        lambda: order.append("timeout"))
    monkeypatch.setattr(tse_fetch, "_bypass_proxy_for_tsetmc",
                        lambda: order.append("proxy"))

    # Make the finpy import fail: fetch() turns that into TransientFetchError,
    # which is enough to prove how far it got and keeps the test off the network.
    import builtins
    real_import = builtins.__import__

    def guarded(name, *a, **kw):
        if name == "finpy_tse":
            order.append("finpy")
            raise ImportError("blocked by the test")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(tse_fetch.TransientFetchError):
        tse_fetch.fetch("etf", "طلوع", "1405-06-05", "1405-06-07")

    assert "timeout" in order, "fetch() must install the HTTP timeout"
    assert order.index("timeout") < order.index("finpy"), (
        "the timeout must be installed BEFORE finpy_tse is reached, or the "
        "first request of the process is still unprotected"
    )

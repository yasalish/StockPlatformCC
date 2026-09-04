"""tests/test_dev_port_guard.py — refusing to start on an occupied dev port

The bug these guard against is not a crash; it is the opposite. On Windows,
Werkzeug's SO_REUSEADDR lets a second `python app.py` bind a port a first one is
already listening on, and BOTH stay bound. Connections go to one of them with no
rule you can rely on, and since each process caches its own Jinja templates, an
edit to a template then shows up on some requests and not others — while CSS and
JS edits appear every time, because asset_version() re-reads those from disk on
every request in any process.

That produced a real debugging round: three copies of this app were listening on
5002, started at 15:32, 17:25 and 23:02, and the user's reasonable conclusion
was that the changes had not been made.

Pure tests: one loopback socket the test opens itself, and a read of app.py's
source. No Flask app, no database, no Redis.
"""
import ast
import pathlib
import socket

import pytest

import dev_boot


@pytest.fixture
def listening_port():
    """A real listening socket on a port the OS picked, closed afterwards."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        yield srv.getsockname()[1]
    finally:
        srv.close()


def test_detects_a_port_that_is_listening(listening_port):
    assert dev_boot.port_already_serving("127.0.0.1", listening_port) is True


def test_a_closed_port_is_free(listening_port):
    """The same port, once its listener is gone, must read as free.

    Taking the port from the fixture and closing it is deliberate: a hard-coded
    "probably unused" port number is exactly the kind of test that passes for
    years and then fails on the one machine where something owns it.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    assert dev_boot.port_already_serving("127.0.0.1", port, timeout=0.2) is False


def test_an_unresolvable_host_is_not_reported_as_occupied():
    """A name that does not resolve is not a collision — let the real bind fail.

    Returning True here would refuse to start for a reason that has nothing to
    do with another instance, which is a worse failure than the one being
    prevented.
    """
    assert dev_boot.port_already_serving(
        "no-such-host.invalid", 5002, timeout=0.2) is False


# --- the wiring in app.py ----------------------------------------------------
#
# Source-level, because importing app.py to reach its __main__ block is not
# possible and spawning it needs a database. What can go wrong silently is the
# ORDERING and the escape hatch, and both are readable.

APP_SRC = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text(
    encoding="utf-8")


def test_app_py_still_parses():
    ast.parse(APP_SRC)


def test_the_guard_runs_before_the_url_is_announced():
    """A terminal that prints ' * Running on http://…' and then refuses to run
    is telling the reader two contradictory things."""
    assert APP_SRC.index("# PORT ALREADY IN USE") < APP_SRC.index(
        'print(f" * Running on')


def test_the_guard_is_skipped_inside_the_reloader_child():
    """WERKZEUG_RUN_MAIN is set in the child that actually binds the socket.

    Checking there would find the parent's own listener and refuse to restart
    on every code edit — i.e. it would break FLASK_DEBUG=1 entirely.
    """
    guard = APP_SRC[APP_SRC.index("# PORT ALREADY IN USE"):]
    guard = guard[:guard.index("raise SystemExit(1)")]
    assert 'if not os.environ.get("WERKZEUG_RUN_MAIN"):' in guard


def test_there_is_an_escape_hatch():
    """Two instances on one port is occasionally what someone wants."""
    guard = APP_SRC[APP_SRC.index("# PORT ALREADY IN USE"):]
    guard = guard[:guard.index("raise SystemExit(1)")]
    assert "BN_ALLOW_PORT_REUSE" in guard


def test_the_message_tells_you_how_to_find_the_other_process():
    """An error that says only 'port in use' leaves the user where they were."""
    guard = APP_SRC[APP_SRC.index("# PORT ALREADY IN USE"):]
    guard = guard[:guard.index("raise SystemExit(1)")]
    assert "netstat -ano" in guard and "taskkill" in guard
    assert "lsof" in guard
    assert "DEV_PORT" in guard

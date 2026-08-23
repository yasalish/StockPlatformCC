"""tests/test_alerts.py — the هشدارها rule engine

Pure tests: no database, no broker. db.check_alert_rule() was written as
arithmetic over a plain dict precisely so it could be tested this way — the SQL
in _alert_snapshot() gathers the numbers, this decides, and the decision is the
part that can be quietly wrong for weeks in a notification feature.

The failure mode this file exists to prevent is specific and nasty: an alert
that never fires raises nothing, logs nothing, and looks to the user like the
feature does not work.
"""
from decimal import Decimal

import pytest

db = pytest.importorskip("db")


def snap(**over):
    """A symbol's latest session, as _alert_snapshot() returns it.

    Deliberately built from Decimals, because that is what psycopg2 hands back
    for avg() and for a numeric column — and a Decimal reaching an f-string
    format spec or a float multiplication is a TypeError on the one code path
    that only runs when an alert actually fires.
    """
    s = {"ticker": "خودرو", "j_date": "1405-05-28", "final": Decimal("629"),
         "prev_final": Decimal("620"), "volume": 6476897694,
         "vol_avg": Decimal("3000000000"), "hi": Decimal("901"),
         "lo": Decimal("2")}
    s.update(over)
    return s


# ---------------------------------------------------------------------------
# price levels
# ---------------------------------------------------------------------------
def test_price_above_fires_at_and_over_the_level():
    assert db.check_alert_rule("price_above", 600, snap())[0] is True
    # At the level exactly: a rule set to the current price must fire, not sit
    # one rial away for ever.
    assert db.check_alert_rule("price_above", 629, snap())[0] is True
    assert db.check_alert_rule("price_above", 630, snap())[0] is False


def test_price_below_fires_at_and_under_the_level():
    assert db.check_alert_rule("price_below", 700, snap())[0] is True
    assert db.check_alert_rule("price_below", 629, snap())[0] is True
    assert db.check_alert_rule("price_below", 628, snap())[0] is False


# ---------------------------------------------------------------------------
# daily change — computed on FINAL, per the project's convention
# ---------------------------------------------------------------------------
def test_daily_change_is_measured_on_the_final_price():
    """«پایانی» (adj_final) is what every percentage in this app is computed on;
    measuring the same move on adj_close would give a different number and make
    the alert disagree with the table the user is looking at."""
    fired, value = db.check_alert_rule("pct_up", 1.0, snap())
    assert fired is True
    assert value == pytest.approx((629 - 620) / 620 * 100, rel=1e-9)


def test_pct_down_takes_the_magnitude_of_the_threshold():
    """A user typing "5" and a user typing "-5" both mean "fell more than five
    percent". Reading the sign literally would make one of them a rule that can
    never fire."""
    falling = snap(final=Decimal("580"), prev_final=Decimal("620"))
    assert db.check_alert_rule("pct_down", 5, falling)[0] is True
    assert db.check_alert_rule("pct_down", -5, falling)[0] is True
    assert db.check_alert_rule("pct_down", 10, falling)[0] is False


def test_a_rise_never_fires_a_fall_rule():
    assert db.check_alert_rule("pct_down", 1, snap())[0] is False
    assert db.check_alert_rule("pct_up", 1, snap(final=Decimal("600")))[0] is False


def test_a_symbol_with_no_previous_session_reports_no_change():
    """A newly listed symbol has one bar. Treating the missing previous close as
    zero would compute an infinite rise; treating it as equal would make it a
    0% day that fires every "fell more than 0" rule ever set."""
    assert db.check_alert_rule("pct_up", 1, snap(prev_final=None)) == (False, None)
    assert db.check_alert_rule("pct_down", 1, snap(prev_final=0)) == (False, None)


# ---------------------------------------------------------------------------
# unusual volume — Finviz's most-used preset, and the one rule whose threshold
# is a multiplier rather than a price or a percentage
# ---------------------------------------------------------------------------
def test_volume_spike_is_a_multiple_of_the_twenty_day_average():
    fired, times = db.check_alert_rule("vol_spike", 2, snap())
    assert fired is True
    assert times == pytest.approx(6476897694 / 3e9, rel=1e-9)
    assert db.check_alert_rule("vol_spike", 3, snap())[0] is False


def test_the_volume_average_excludes_today():
    """The SQL averages rows 2..21, not 1..20. Including today in its own
    baseline drags the ratio toward 1 exactly when the spike is largest, which
    is the moment the rule is supposed to fire."""
    import inspect
    src = inspect.getsource(db._alert_snapshot)
    assert "rn BETWEEN 2 AND" in src
    assert db.VOL_WINDOW == 20


def test_a_symbol_with_no_volume_history_does_not_fire():
    assert db.check_alert_rule("vol_spike", 2, snap(vol_avg=None)) == (False, None)
    assert db.check_alert_rule("vol_spike", 2, snap(vol_avg=Decimal("0"))) == (False, None)


# ---------------------------------------------------------------------------
# distance to the historical extremes
# ---------------------------------------------------------------------------
def test_near_high_measures_the_gap_as_a_percentage_of_the_extreme():
    """As a percentage of the extreme, not of the current price, so the number
    means the same thing for a 20-rial symbol and a 200,000-rial one."""
    fired, gap = db.check_alert_rule("near_high", 5, snap(final=Decimal("880")))
    assert fired is True
    assert gap == pytest.approx((901 - 880) / 901 * 100, rel=1e-9)
    assert db.check_alert_rule("near_high", 1, snap(final=Decimal("880")))[0] is False


def test_near_low_uses_the_low_and_not_the_high():
    fired, gap = db.check_alert_rule("near_low", 10, snap(final=Decimal("2.1"),
                                                          lo=Decimal("2")))
    assert fired is True
    assert gap == pytest.approx(5.0, rel=1e-9)


def test_a_missing_extreme_does_not_fire():
    """mv_alltime_* is LEFT JOINed, so a symbol absent from it arrives with
    nulls. Firing on a null extreme would report every symbol as at its high."""
    assert db.check_alert_rule("near_high", 5, snap(hi=None)) == (False, None)
    assert db.check_alert_rule("near_low", 5, snap(lo=None)) == (False, None)


# ---------------------------------------------------------------------------
# contract-level guarantees
# ---------------------------------------------------------------------------
def test_no_rule_fires_on_a_symbol_with_no_price():
    """Every rule needs the latest final. A symbol whose rows are all
    adj_final = 0 is filtered out by the snapshot query, but a rule must not
    depend on that filter to be safe."""
    for rule in db.ALERT_RULES:
        assert db.check_alert_rule(rule, 1, snap(final=None)) == (False, None), rule


def test_an_unknown_rule_never_fires():
    """Rules are stored as text. An id removed from the catalogue must go quiet,
    not throw inside the evaluator loop and stop every later alert from being
    checked."""
    assert db.check_alert_rule("no_such_rule", 1, snap()) == (False, None)


def test_every_rule_declares_a_unit_and_a_persian_label():
    """The unit is what stops the create form from asking for "a number": a
    price rule wants ریال, a percentage rule wants ٪, a volume rule wants a
    multiplier. A missing unit is how a user sets "volume above 3" meaning
    3 million and never hears from it."""
    for key, r in db.ALERT_RULES.items():
        assert r["unit"] in ("price", "pct", "times"), key
        assert r["fa"] and r["hint"], key


def test_every_rule_in_the_catalogue_is_implemented():
    """A rule the form offers but check_alert_rule() does not implement is a
    rule that silently never fires."""
    for rule in db.ALERT_RULES:
        unit = db.ALERT_RULES[rule]["unit"]
        threshold = {"price": 1, "pct": 1, "times": 1}[unit]
        fired, value = db.check_alert_rule(rule, threshold, snap())
        assert value is not None, f"{rule} returned no value on a full snapshot"


def test_repeat_modes_are_the_two_the_form_offers():
    assert set(db.REPEAT_MODES) == {"once", "always"}

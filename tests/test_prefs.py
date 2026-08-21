"""tests/test_prefs.py — the preference catalogue

Pure tests: no database, no Flask app, no Redis, no network. `pytest -q` must
pass on a laptop with nothing running, which is why the one test that needs
`db` is guarded by an import check rather than by a fixture.

The test that matters most is `test_every_theme_has_a_stylesheet_block`. A theme
added to `prefs.THEMES` but not to `static/css/style.css` renders as the light
theme — the picker offers it, the click "works", and the page does not change.
That is a bug nobody reports as a bug; they just decide the setting is broken.
"""
import io
import os
import re

import pytest

import prefs

CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "static", "css", "style.css")


# ---------------------------------------------------------------------------
# normalize() — rule 1: drop, never raise
# ---------------------------------------------------------------------------
def test_normalize_keeps_only_known_keys():
    out = prefs.normalize({"theme": "dark", "not_a_setting": 1, "__proto__": "x"})
    assert out == {"theme": "dark"}


def test_normalize_drops_invalid_values_without_raising():
    # A stale tab posting last week's option list must not 500 the save.
    assert prefs.normalize({"theme": "neon", "density": "roomy"}) == {}
    assert prefs.normalize({"digits": "٣"}) == {}


def test_normalize_survives_junk_payloads():
    for junk in (None, [], "theme=dark", 7, object()):
        assert prefs.normalize(junk) == {}


def test_rows_per_page_is_coerced_from_a_string_and_clamped():
    assert prefs.normalize({"rows_per_page": "100"}) == {"rows_per_page": 100}
    assert prefs.normalize({"rows_per_page": 100}) == {"rows_per_page": 100}
    # 75 is not one of ROWS_CHOICES: dropped, so the default applies.
    assert prefs.normalize({"rows_per_page": 75}) == {}
    assert prefs.normalize({"rows_per_page": "abc"}) == {}


def test_auto_refresh_accepts_only_the_offered_intervals():
    assert prefs.normalize({"auto_refresh": "300"}) == {"auto_refresh": 300}
    assert prefs.normalize({"auto_refresh": 7}) == {}


@pytest.mark.parametrize("value,expected", [
    (True, True), ("1", True), ("true", True), ("on", True), ("yes", True), (1, True),
    (False, False), ("0", False), ("false", False), ("", False), (0, False), ("off", False),
])
def test_reduce_motion_truthiness_forms(value, expected):
    # An unchecked checkbox is absent from a form post, so False has to be
    # reachable from the string forms too — not only from JSON's `false`.
    assert prefs.normalize({"reduce_motion": value})["reduce_motion"] is expected


# ---------------------------------------------------------------------------
# family_of() — rule 3: an unknown theme is LIGHT
# ---------------------------------------------------------------------------
def test_family_of_unknown_theme_is_light():
    # `:root` in style.css IS the light theme, so an unrecognised data-theme
    # renders light. Answering 'dark' would put a light-looking page under
    # dark-mode logic and make the ☾/☀ toggle appear broken.
    assert prefs.family_of("no-such-theme") == "light"
    assert prefs.family_of(None) == "light"
    assert prefs.family_of("") == "light"


def test_family_of_known_themes():
    assert prefs.family_of("light") == "light"
    assert prefs.family_of("sepia") == "light"
    assert prefs.family_of("paper") == "light"
    for dark in ("dark", "midnight", "graphite"):
        assert prefs.family_of(dark) == "dark"


def test_toggle_target_crosses_families_and_is_stable():
    assert prefs.toggle_target("light") == "dark"
    assert prefs.toggle_target("sepia") == "dark"
    assert prefs.toggle_target("midnight") == "light"
    # Predictability: toggling twice from any theme lands on a family default
    # and stays there, rather than wandering back to the alternate.
    once = prefs.toggle_target("graphite")
    assert prefs.toggle_target(once) == "dark"


# ---------------------------------------------------------------------------
# payload()
# ---------------------------------------------------------------------------
def test_payload_of_nothing_is_the_defaults():
    p = prefs.payload({})
    for key, value in prefs.DEFAULTS.items():
        assert p[key] == value
    # Derived, not defaulted: theme_family has to follow whatever DEFAULTS says
    # the theme is. It asserted "light" literally, which broke the day the
    # default theme became dark — while the code was working exactly as
    # intended. Assert the RELATIONSHIP instead.
    assert p["theme_family"] == prefs.family_of(prefs.DEFAULTS["theme"])


def test_payload_overlays_valid_values_only():
    p = prefs.payload({"theme": "midnight", "density": "nonsense"})
    assert p["theme"] == "midnight"
    assert p["theme_family"] == "dark"
    assert p["density"] == prefs.DEFAULTS["density"]


def test_client_payload_carries_exactly_the_settings():
    # base.html embeds this in every page: it must not grow a server-side field.
    assert set(prefs.client_payload({})) == set(prefs.DEFAULTS)


def test_validate_messages_are_persian_and_only_for_shape_errors():
    assert prefs.validate({"theme": "dark"}) is None
    for bad in (None, {}, {"nope": 1}):
        msg = prefs.validate(bad)
        assert msg and re.search(r"[؀-ۿ]", msg), msg


# ---------------------------------------------------------------------------
# The catalogue and the stylesheet
# ---------------------------------------------------------------------------
def test_theme_ids_are_unique_and_labelled():
    assert len(prefs.THEME_IDS) == len(set(prefs.THEME_IDS))
    for t in prefs.THEMES:
        assert t["label"] and t["family"] in ("light", "dark")
        assert len(t["swatch"]) == 2
        for hexval in t["swatch"]:
            assert re.fullmatch(r"#[0-9a-f]{6}", hexval), (t["id"], hexval)


def _css():
    return io.open(CSS, encoding="utf-8").read()


def test_every_theme_has_a_stylesheet_block():
    css = _css()
    for t in prefs.THEMES:
        if t["id"] == "light":
            # The light theme IS `:root` — deliberately not a [data-theme] block,
            # so that an unknown value falls back to it.
            continue
        assert f'[data-theme="{t["id"]}"]' in css, t["id"]


def test_every_theme_block_redefines_every_root_property():
    """The rule that actually bites: a block redefining half the properties
    inherits the rest from `:root` and renders as a broken light theme."""
    css = _css()
    root = re.search(r":root\{\s*color-scheme:light;(.*?)\n\}", css, re.S)
    assert root, "the :root palette block moved — update this test"
    wanted = set(re.findall(r"(--[a-z0-9-]+)\s*:", root.group(1)))
    assert len(wanted) > 20, wanted

    for t in prefs.THEMES:
        if t["id"] == "light":
            continue
        block = re.search(r'\[data-theme="%s"\]\{(.*?)\n\}' % t["id"], css, re.S)
        assert block, t["id"]
        have = set(re.findall(r"(--[a-z0-9-]+)\s*:", block.group(1)))
        missing = wanted - have
        assert not missing, f'theme "{t["id"]}" does not redefine: {sorted(missing)}'


def test_option_labels_cover_every_choice():
    # A value with no label renders as its raw id («comfortable») in a Persian
    # UI, which looks like a bug to the user and is one.
    for key, choices in (("digits", prefs.DIGIT_MODES),
                         ("default_kind", prefs.KINDS),
                         ("density", prefs.DENSITIES),
                         ("font_scale", prefs.FONT_SCALES),
                         ("scrollbar_size", prefs.SCROLLBAR_SIZES),
                         ("updown_scheme", prefs.UPDOWN_SCHEMES),
                         ("auto_refresh", prefs.AUTO_REFRESH_CHOICES),
                         ("default_period", prefs.PERIOD_KEYS)):
        for c in choices:
            label = prefs.option_label(key, c)
            assert label and label != str(c) or key == "rows_per_page", (key, c)


def test_every_setting_has_a_label():
    for key in prefs.DEFAULTS:
        assert prefs.LABELS.get(key), key


# ---------------------------------------------------------------------------
# The one place prefs and db meet
# ---------------------------------------------------------------------------
def test_period_keys_match_db_periods():
    """prefs.py may not import db (it must stay importable with no PostgreSQL
    environment), so the period list is duplicated as a literal. This is the
    only place the two definitions meet — without it they drift silently and a
    saved «دورهٔ پیش‌فرض» stops matching any column."""
    try:
        import db
    except Exception as e:                    # no STOCK_DB_PASSWORD on this machine
        pytest.skip(f"db could not be imported ({e.__class__.__name__}); set the "
                    f"STOCK_DB_* environment variables to run this check")
    assert prefs.PERIOD_KEYS == tuple(p["key"] for p in db.PERIODS)

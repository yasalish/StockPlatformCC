"""
prefs.py — تنظیمات کاربر (پوسته، ارقام، چگالی جدول، …)
The user-preference catalogue and the only place that decides what a preference
may legally be.

Pure module: no Flask, no database, no network, no Redis — exactly like
`plans.py`. `db.py` stores a row per user, `app.py` renders it into every page
and `static/js/theme.js` applies it in the browser, but *what a preference means
and which values are acceptable* is decided only here, so there is one file to
read when a saved setting renders as something unexpected.

Four rules shape everything below.

**1. An invalid value falls back to the default; it never raises.** These values
arrive from a `<select>` in a browser. A tab left open since last week posts the
option list it was rendered with, an old bookmark posts a theme that has been
renamed, and a hand-rolled client posts nonsense. None of those may 500 a
settings save — `normalize()` silently drops what it does not recognise.
`validate()` exists separately, for the API, so a *structurally* wrong request
(not a dict, an oversized payload) still gets a real Persian message.

**2. `DEFAULTS` is the schema.** `db.get_prefs()` merges the stored row *under*
`DEFAULTS`, so adding a preference here plus a nullable column is the whole
change — no backfill, and an account that has never opened the settings page
renders identically to one that opened it and pressed nothing. The corollary is
that the column defaults in the migration must equal these values exactly, or a
fresh account and a saved-then-reset account would look different.

**3. An unknown theme id is a LIGHT theme.** `:root` in `style.css` *is* the
light theme, so a `data-theme` the stylesheet does not know renders light. If
`family_of()` guessed `dark` for an unknown id, the ☾/☀ toggle and the chart
palette would treat a light-looking page as dark and flip the user to light
"again" — a control that appears broken. Guessing the visible truth is the only
safe answer.

**4. Every preference here has to *do* something.** A settings screen full of
switches that change nothing is worse than a short one. Each key below names, in
its comment, the code that reads it. If a preference ever loses its reader,
delete the preference — do not leave the switch on screen.
"""

# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------
# The picker list. Each entry's `id` MUST have a matching `[data-theme="<id>"]`
# block in static/css/style.css — except `light`, which IS `:root` and therefore
# has no block of its own (that is also why it is spelled as the absence of a
# theme in the DOM: `data-theme="light"` is written anyway, so the attribute is
# always present and JS never has to distinguish "unset" from "light").
#
# `swatch` is (surface, accent): the settings page paints each row in its own
# colours so the list is its own legend — you pick a theme by looking at it, not
# by reading its name.
#
# Adding a theme: append here, add the `[data-theme="…"]` block to style.css
# redefining EVERY custom property `:root` defines, and tests/test_prefs.py will
# check the two stayed in step.
# swatch = (the theme's page surface, the theme's accent). Kept in step with the
# [data-theme] blocks in static/css/style.css BY HAND, because a swatch that
# advertises a palette the stylesheet no longer has is worse than no swatch: the
# picker's whole premise is that you choose by looking.
THEMES = [
    {"id": "light",    "label": "روشن",       "family": "light", "swatch": ("#f5f7f9", "#2563eb")},
    {"id": "sepia",    "label": "سپیا",        "family": "light", "swatch": ("#efe6d5", "#1d63c9")},
    {"id": "paper",    "label": "کاغذ سفید",   "family": "light", "swatch": ("#f2f4f7", "#1f6feb")},
    {"id": "dark",     "label": "تاریک",       "family": "dark",  "swatch": ("#0f151b", "#4d9bff")},
    {"id": "midnight", "label": "نیمه‌شب",     "family": "dark",  "swatch": ("#070b12", "#5b9dff")},
    {"id": "graphite", "label": "ذغالی",       "family": "dark",  "swatch": ("#121314", "#6ba3f5")},

    {"id": "contrast",    "label": "کنتراست بالا", "family": "light", "swatch": ("#eaeef2", "#0b45c9")},
    {"id": "azure",       "label": "آسمانی", "family": "light", "swatch": ("#eef2fa", "#3b5bdb")},
    {"id": "sage",        "label": "مریم‌گلی", "family": "light", "swatch": ("#e9ece2", "#35669f")},
    {"id": "ivory",       "label": "عاجی", "family": "light", "swatch": ("#f5f2ea", "#1c5bb8")},
    {"id": "contrast-dark", "label": "کنتراست بالا — تاریک", "family": "dark", "swatch": ("#04060a", "#7ab8ff")},
    {"id": "steel",       "label": "فولادی", "family": "dark", "swatch": ("#131c21", "#62b8e2")},
    {"id": "indigo",      "label": "نیلی", "family": "dark", "swatch": ("#15162b", "#9d8cfa")},
    {"id": "espresso",    "label": "اسپرسو", "family": "dark", "swatch": ("#17100b", "#cf9ce8")},
]
THEME_IDS = tuple(t["id"] for t in THEMES)

# What ☾/☀ crosses to. The toggle must be predictable above all else, so it goes
# to the other family's DEFAULT — never to whichever alternate the user last
# picked over there. A control whose destination depends on history is a control
# nobody trusts.
FAMILY_DEFAULT = {"light": "light", "dark": "dark"}

# ---------------------------------------------------------------------------
# Value domains
# ---------------------------------------------------------------------------
DIGIT_MODES = ("fa", "en")               # ۱۲۳ vs 123 — read by static/js/theme.js
KINDS = ("stock", "etf")
ROWS_CHOICES = (25, 50, 100, 200)
DENSITIES = ("comfortable", "compact")
FONT_SCALES = ("sm", "md", "lg")
SCROLLBAR_SIZES = ("md", "lg", "xl")     # 14 / 20 / 28 px — see --sbar-h in style.css
UPDOWN_SCHEMES = ("classic", "colorblind")
AUTO_REFRESH_CHOICES = (0, 60, 300, 900)  # seconds; 0 = off

# The period keys the market tables understand. Declared as a literal because
# this module may not import `db` (it must stay importable with no PostgreSQL
# environment, which is what lets `pytest -q` run on a laptop with no database).
# tests/test_prefs.py asserts this tuple equals tuple(p["key"] for p in
# db.PERIODS) — that test is the only place the two definitions meet, and it is
# what stops them drifting apart silently.
PERIOD_KEYS = ("p5", "p20", "p60", "p120", "p240", "p360")

PERIOD_LABELS = {
    "p5": "۱ هفته", "p20": "۱ ماه", "p60": "۳ ماه",
    "p120": "۶ ماه", "p240": "۱ سال", "p360": "۱۸ ماه",
}

# ---------------------------------------------------------------------------
# The schema (rule 2)
# ---------------------------------------------------------------------------
DEFAULTS = {
    # پوسته — applied pre-paint by the inline script in base.html.
    #  DARK by default, with the redesign. Every platform this app is measured
    #  against — TradingView, the terminal products, the broker dashboards —
    #  opens dark, because a market screen is read for hours and its content is
    #  overwhelmingly coloured numerals on a neutral ground. The three light
    #  themes are unchanged and one click away (☾ in the header, or «پوستهٔ
    #  نمایش» in settings), and a signed-in user's saved choice still wins over
    #  this — it only decides what a NEW visitor sees.
    "theme": "dark",
    # ارقام — 'en' makes static/js/theme.js rewrite Persian digits to Latin in
    # the rendered page. Server-side text stays Persian; this is presentation.
    "digits": "fa",
    # پیش‌فرض سهام/صندوق — read by /heatmap and the dashboard breadth panel.
    "default_kind": "stock",
    # تعداد ردیف — page size of the OHLCV history table (static/js/chart.js).
    "rows_per_page": 50,
    # دورهٔ پیش‌فرض — initial period of /heatmap and the dashboard breadth panel.
    "default_period": "p20",
    # چگالی — row padding, via [data-density] in style.css.
    "density": "comfortable",
    # حرکت کمتر — kills transitions/animations, via [data-motion] in style.css.
    "reduce_motion": False,
    # اندازهٔ قلم — --font-scale in style.css.
    "font_scale": "md",
    # نوار پیمایش بالای جدول — static/js/tables.js mirrors the bottom scrollbar
    # above every wide table when true.
    "top_scrollbar": True,
    # ضخامت نوار پیمایش — --sbar-h in style.css.
    "scrollbar_size": "lg",
    # سرستون چسبان — sticky <thead> on scrollable tables.
    "sticky_head": True,
    # راه‌راه — zebra striping on table rows.
    "zebra": False,
    # رنگ صعود/نزول — 'colorblind' swaps green/red for blue/orange, which stay
    # distinguishable under deuteranopia (the common form). A trader who cannot
    # tell a gain from a loss at a glance is the one failure this app cannot
    # ship, and ~۸٪ of men see red/green that way.
    "updown_scheme": "classic",
    # به‌روزرسانی خودکار (ثانیه) — static/js/tables.js reloads data pages.
    "auto_refresh": 0,
    # نمای عریض — lets .container use the full window width.
    "wide": False,
}

# Booleans and integers are listed so normalize() can coerce without a chain of
# isinstance checks scattered through it.
_BOOL_KEYS = ("reduce_motion", "top_scrollbar", "sticky_head", "zebra", "wide")
_CHOICE_KEYS = {
    "theme": THEME_IDS,
    "digits": DIGIT_MODES,
    "default_kind": KINDS,
    "default_period": PERIOD_KEYS,
    "density": DENSITIES,
    "font_scale": FONT_SCALES,
    "scrollbar_size": SCROLLBAR_SIZES,
    "updown_scheme": UPDOWN_SCHEMES,
}
_INT_CHOICE_KEYS = {
    "rows_per_page": ROWS_CHOICES,
    "auto_refresh": AUTO_REFRESH_CHOICES,
}

# Persian labels for the settings screen, kept here rather than in the template
# so the template cannot invent a name for a value this module does not have.
LABELS = {
    "theme": "پوستهٔ نمایش",
    "digits": "ارقام",
    "default_kind": "بازار پیش‌فرض",
    "rows_per_page": "تعداد ردیف در هر صفحه",
    "default_period": "دورهٔ پیش‌فرض",
    "density": "چگالی جدول",
    "reduce_motion": "کاهش حرکت",
    "font_scale": "اندازهٔ قلم",
    "top_scrollbar": "نوار پیمایش بالای جدول‌ها",
    "scrollbar_size": "ضخامت نوار پیمایش",
    "sticky_head": "سرستون چسبان",
    "zebra": "ردیف‌های راه‌راه",
    "updown_scheme": "رنگ صعود و نزول",
    "auto_refresh": "به‌روزرسانی خودکار",
    "wide": "نمای عریض",
}

# Option labels shown next to each choice. Values not listed fall back to the
# raw value, so a new option renders (ugly but visible) rather than disappearing.
OPTION_LABELS = {
    "digits": {"fa": "فارسی (۱۲۳)", "en": "لاتین (123)"},
    "default_kind": {"stock": "سهام", "etf": "صندوق‌ها"},
    "density": {"comfortable": "راحت", "compact": "فشرده"},
    "font_scale": {"sm": "کوچک", "md": "معمولی", "lg": "بزرگ"},
    "scrollbar_size": {"md": "معمولی", "lg": "ضخیم", "xl": "خیلی ضخیم"},
    "updown_scheme": {"classic": "سبز / قرمز", "colorblind": "آبی / نارنجی (کوررنگی)"},
    "auto_refresh": {0: "خاموش", 60: "هر ۱ دقیقه", 300: "هر ۵ دقیقه", 900: "هر ۱۵ دقیقه"},
    "default_period": PERIOD_LABELS,
}


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def theme_option(theme_id):
    """The THEMES entry for `theme_id`, or None. Callers that need a guaranteed
    answer should use family_of()/normalize() instead of defaulting themselves —
    that is how the "unknown id is light" rule stays in one place."""
    for t in THEMES:
        if t["id"] == theme_id:
            return t
    return None


def family_of(theme_id):
    """'light' or 'dark' for a theme id — and 'light' for anything unknown.

    Rule 3: `:root` is the light theme, so a `data-theme` value the stylesheet
    does not recognise *renders* light. Answering 'dark' for it would put a
    light-looking page under dark-mode logic and make the ☾/☀ toggle look broken.
    """
    t = theme_option(theme_id)
    return t["family"] if t else "light"


def toggle_target(theme_id):
    """The theme ☾/☀ switches to: the other family's default (rule under
    FAMILY_DEFAULT). Kept here so the Python side and theme.js cannot disagree
    about what the button does."""
    return FAMILY_DEFAULT["dark" if family_of(theme_id) == "light" else "light"]


def option_label(key, value):
    """Persian label for one option value (falls back to the value itself)."""
    return OPTION_LABELS.get(key, {}).get(value, str(value))


# ---------------------------------------------------------------------------
# Coercion and validation
# ---------------------------------------------------------------------------
def _as_bool(v):
    """Accept every shape a browser can send a checkbox in. An unchecked box is
    simply absent from a form post, so `False` also has to be reachable from the
    JSON literal, the string 'false' and the string '0'."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ("1", "true", "on", "yes", "بله")


def normalize(values):
    """Keep only known keys, coerce types, drop anything invalid (rule 1).

    Never raises and never partially applies: a key whose value does not survive
    coercion is simply not in the result, which means db.get_prefs() will merge
    the default over it. Unknown keys are dropped rather than stored, so a
    client cannot use the prefs row as free key/value storage.
    """
    if not isinstance(values, dict):
        return {}
    out = {}
    for key, choices in _CHOICE_KEYS.items():
        if key in values and values[key] in choices:
            out[key] = values[key]
    for key, choices in _INT_CHOICE_KEYS.items():
        if key not in values:
            continue
        try:
            # A form posts "50", JSON posts 50, and a stale client may post 50.0.
            n = int(str(values[key]).strip())
        except (TypeError, ValueError):
            continue
        if n in choices:
            out[key] = n
    for key in _BOOL_KEYS:
        if key in values:
            out[key] = _as_bool(values[key])
    return out


def validate(values):
    """A Persian error message for a *structurally* wrong payload, or None.

    Deliberately narrow: individual bad values are normalize()'s business (they
    are dropped, not rejected). What this catches is a client that sent the
    wrong shape entirely, or one that sent nothing this module recognises — the
    difference between "your browser is out of date" and "your request was not a
    settings object at all", which a silent 200 would hide.
    """
    if not isinstance(values, dict):
        return "قالب تنظیمات نامعتبر است."
    if not values:
        return "هیچ تنظیمی برای ذخیره فرستاده نشده است."
    if not normalize(values):
        return "هیچ‌کدام از تنظیمات فرستاده‌شده شناخته نشد."
    return None


def payload(stored):
    """DEFAULTS overlaid with the valid parts of `stored`, plus the derived
    fields every template and the browser need.

    `theme_family` is derived here rather than in the template because the
    "unknown id is light" rule (rule 3) must not be re-implemented in Jinja, and
    `themes` travels with it so the picker cannot list a theme this module does
    not know.
    """
    merged = dict(DEFAULTS)
    merged.update(normalize(stored or {}))
    merged["theme_family"] = family_of(merged["theme"])
    return merged


def client_payload(stored):
    """Just the settings the browser applies, as a JSON-safe dict.

    Separate from payload() because base.html embeds this in every page: it must
    stay small, and it must never accidentally carry a server-side field (a
    user id, a timestamp) into the HTML of a page that any browser extension can
    read.
    """
    p = payload(stored)
    return {k: p[k] for k in DEFAULTS}

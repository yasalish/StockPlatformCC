"""
plans.py — نوع حساب، اشتراک و سهمیه‌ها
The account-type catalogue and every entitlement question answered about it.

Pure module: no database, no Flask, no network, no Redis. `db.py` stores which
plan an account holds, `billing.py` sells it and `guards.py` enforces it — but
*what a plan means* is decided only here, so there is exactly one place to read
when someone asks "is this account allowed to do that".

Six rules shape everything below. The first five are the ones that cost real
money or real trust if they get re-derived wrongly; the sixth is the one that is
specific to this app being a market-data platform rather than a text editor.

**1. Money is stored and charged in ریال; humans read تومان.** Every Iranian
gateway (ZarinPal, IDPay, Pay.ir) takes مبلغ in **ریال**, and every Iranian
*thinks* in **تومان**. Mixing the two is a 10× billing error, so prices are
declared once as `_rial` integers, every payload carries both figures, and no
other module is allowed to do the division. `TOMAN` is the only conversion
factor in the codebase.

**2. A limit of `None` is unlimited; `0` is a real limit.** The free plan
genuinely allows zero saved screens, so "no ceiling" cannot be spelled `0` — and
it cannot be `math.inf` either, because that is not JSON. `within()` is the only
correct way to compare a count against a limit.

**3. An expired subscription downgrades entitlements and never deletes
anything.** `effective_id()` answers `free` once `plan_expires_at` has passed,
and nothing anywhere removes a watchlist row or a saved screen for being over a
limit. Quotas are therefore checked at *creation* time only, against a live
count. An investor whose card expired mid-quarter must still be able to open,
read and export everything they had saved — they are only stopped from adding
*more*.

**4. Grandfathered accounts are permanent.** Every account that existed when the
plans migration ran is on حرفه‌ای with a NULL expiry and
`plan_source = 'grandfathered'` (badge «بنیان‌گذار»). They signed up for a tool,
not a trial. Do not add an expiry to them. This is also what makes the rollout
safe: no existing user wakes up to a smaller watchlist than they went to bed
with.

**5. An unknown plan id reads as رایگان.** An id nobody recognises has to
resolve to *something*, and the answer that cannot leak a paid feature is the
free one.

**6. Market data is never paywalled, and nothing that is free today becomes
paid.** Prices, the dashboard, the symbol pages, the charts, the gainer tables,
غربالگر, بازدهٔ بازه, استراتژی‌ها, فیلترها and the existing single-table Excel
export were public before plans existed and stay public after. What a
subscription buys is *capacity and convenience over the user's own saved work* —
a bigger دیده‌بان, saved غربالگر presets, the multi-sheet workbook — never
access to the market itself. A platform that puts the price of سهام behind a
paywall it did not have last week is not a professional tool; it is a bait and
switch. This is the direct analogue of «خودِ فیلمنامه هیچ‌وقت پشت پرداخت
نمی‌رود» in the sibling project.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
# ۱ تومان = ۱۰ ریال. Gateways charge Rial; humans read Toman. Rule 1 above.
TOMAN = 10

FREE = "free"
PRO = "pro"
ORG = "org"

# How an account arrived at its plan. Stored in users.plan_source, and the only
# one with behaviour attached is 'grandfathered' (badge «بنیان‌گذار», no expiry).
SOURCE_DEFAULT = "default"
SOURCE_GRANDFATHERED = "grandfathered"
SOURCE_PURCHASED = "purchased"
SOURCE_ADMIN = "admin"
SOURCE_TRIAL = "trial"
PLAN_SOURCES = (
    SOURCE_DEFAULT, SOURCE_GRANDFATHERED, SOURCE_PURCHASED, SOURCE_ADMIN, SOURCE_TRIAL,
)

SOURCE_LABELS = {
    SOURCE_DEFAULT: "پیش‌فرض",
    SOURCE_GRANDFATHERED: "بنیان‌گذار",
    SOURCE_PURCHASED: "خریداری‌شده",
    SOURCE_ADMIN: "اعطای مدیر",
    SOURCE_TRIAL: "آزمایشی",
}

# Billing periods, and the days each one buys. Days rather than calendar months
# on purpose: «۳۰ روز» is what the invoice says and what the user counts, and it
# sidesteps the "bought on ۳۱ مرداد, renews on what?" question entirely.
MONTHLY = "monthly"
YEARLY = "yearly"
PERIOD_DAYS = {MONTHLY: 30, YEARLY: 365}
PERIOD_LABELS = {MONTHLY: "ماهانه", YEARLY: "سالانه"}

# ---------------------------------------------------------------------------
# Feature flags — capabilities that are either present or not, as opposed to
# limits, which are numbers.
#
# Everything the platform already did for free is deliberately absent from this
# list (rule 6): the dashboard, سهام, صندوق‌ها, غربالگر, بازدهٔ بازه,
# استراتژی‌ها, فیلترها, the symbol pages and charts, and the existing
# `/export/<kind>.xlsx` single-table download. A flag that is always true is a
# flag someone will one day switch off by accident, and a flag over a page that
# used to be public is a promise broken.
# ---------------------------------------------------------------------------
WORKBOOK_EXPORT = "workbook_export"    # the multi-sheet Excel workbook (new)
SCREEN_SHARING = "screen_sharing"      # share a saved غربالگر by link
PRIORITY_SUPPORT = "priority_support"  # off-app promise, shown not enforced

FEATURE_LABELS = {
    WORKBOOK_EXPORT: "خروجی اکسل چندبرگی (دیده‌بان و غربالگرها در یک فایل)",
    SCREEN_SHARING: "هم‌رسانی غربالگر ذخیره‌شده با پیوند",
    PRIORITY_SUPPORT: "پشتیبانی اولویت‌دار",
}

# ---------------------------------------------------------------------------
# Limits — the keys, with the Persian noun each one counts. The label is used to
# build the refusal message, so it has to read naturally after «سهمیهٔ».
# ---------------------------------------------------------------------------
WATCHLIST = "watchlist_symbols"
SCREENS = "saved_screens"
ALERTS = "price_alerts"

LIMIT_LABELS = {
    WATCHLIST: "نماد در دیده‌بان",
    SCREENS: "غربالگر ذخیره‌شده",
    ALERTS: "هشدار قیمت",
}


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    tagline: str
    monthly_rial: int
    yearly_rial: int
    limits: dict
    features: frozenset = field(default_factory=frozenset)

    def limit(self, key):
        """The ceiling for `key`, or None for unlimited. An unknown key is
        unlimited rather than zero — a typo must not lock a user out of a
        feature that has no limit."""
        return self.limits.get(key, None)

    def has(self, feature):
        return feature in self.features

    def price(self, period):
        return self.yearly_rial if period == YEARLY else self.monthly_rial


# The catalogue. Prices are Rial (rule 1): ۹۹٬۰۰۰ تومان = ۹۹۰٬۰۰۰ ریال.
# The yearly price is ten months of the monthly one — «دو ماه هدیه» — which is
# the discount shape every Iranian SaaS uses and the one users check by hand.
#
# The free tier's ۱۵ نماد is chosen to be a real portfolio rather than a teaser:
# an investor watching a dozen symbols never meets the paywall at all, which is
# the point. Rule 4 means no *existing* account is measured against it.
PLANS = {
    FREE: Plan(
        id=FREE,
        name="رایگان",
        tagline="کل بازار، بدون هزینه — برای پیگیری یک سبد کوچک",
        monthly_rial=0,
        yearly_rial=0,
        limits={
            WATCHLIST: 15,
            SCREENS: 2,
            ALERTS: 0,
        },
        features=frozenset(),
    ),
    PRO: Plan(
        id=PRO,
        name="حرفه‌ای",
        tagline="برای سرمایه‌گذار فعالی که هر روز بازار را می‌خواند",
        monthly_rial=990_000,
        yearly_rial=9_900_000,
        limits={
            WATCHLIST: None,
            SCREENS: 30,
            ALERTS: 25,
        },
        features=frozenset({WORKBOOK_EXPORT}),
    ),
    ORG: Plan(
        id=ORG,
        name="سازمانی",
        tagline="برای سبدگردان، کارگزاری و تیم تحلیل",
        monthly_rial=2_900_000,
        yearly_rial=29_000_000,
        limits={
            WATCHLIST: None,
            SCREENS: None,
            ALERTS: None,
        },
        features=frozenset({WORKBOOK_EXPORT, SCREEN_SHARING, PRIORITY_SUPPORT}),
    ),
}

PLAN_IDS = (FREE, PRO, ORG)
PAID_IDS = (PRO, ORG)


# ---------------------------------------------------------------------------
# Reading a plan
# ---------------------------------------------------------------------------
def get(plan_id):
    """The Plan for an id, falling back to رایگان (rule 5)."""
    return PLANS.get(plan_id or FREE, PLANS[FREE])


def is_expired(expires_at, *, now=None):
    """Has this subscription run out?

    An empty/None `expires_at` means **never expires** — that is how both a free
    account and a grandfathered one are stored, so "no date" can never read as
    "expired long ago". Parsed the way `db._utcnow()` writes them (naive UTC
    ISO strings), and an unparseable value is treated as no date rather than as
    expired: the failure mode of guessing wrong here is locking a paying user
    out of what they bought.
    """
    if not expires_at:
        return False
    try:
        deadline = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError):
        return False
    return deadline <= (now or datetime.utcnow())


def effective_id(plan_id, expires_at, *, now=None):
    """The plan that is actually in force — رایگان once a paid one has lapsed.

    Rule 3: this downgrades entitlements and nothing else. Nothing the user
    saved is touched, and `db.plan_state` keeps reporting the *stored* plan
    alongside this one so the UI can say «اشتراک حرفه‌ای شما پایان یافته»
    instead of silently pretending the account was always free.
    """
    resolved = plan_id if plan_id in PLANS else FREE
    if resolved in PAID_IDS and is_expired(expires_at, now=now):
        return FREE
    return resolved


def limit(plan_id, key):
    return get(plan_id).limit(key)


def has_feature(plan_id, feature):
    return get(plan_id).has(feature)


def within(limit_value, count):
    """Is `count` inside `limit_value`? The only correct comparison (rule 2).

    Called *before* creating the next item, so the question is whether there is
    room for one more: a limit of 15 with 15 symbols already starred is full.
    """
    if limit_value is None:
        return True
    return count < limit_value


def allows(plan_id, key, count):
    return within(limit(plan_id, key), count)


def remaining(plan_id, key, count):
    """How many more of `key` this account may create, or None for unlimited.

    Never negative: a grandfathered account that was over what is now the free
    ceiling reads as 0 left rather than as -7, because «۷- باقی مانده» is not a
    sentence and the number is only ever shown, never used for arithmetic.
    """
    ceiling = limit(plan_id, key)
    if ceiling is None:
        return None
    return max(0, ceiling - int(count or 0))


# ---------------------------------------------------------------------------
# Refusal messages — Persian, with Persian digits, naming the limit and the way
# out. A quota error that does not say what the ceiling was reads as a bug.
# ---------------------------------------------------------------------------
def _fa(n):
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def limit_message(plan_id, key):
    """Why the write was refused, and what to do about it."""
    plan = get(plan_id)
    ceiling = plan.limit(key)
    noun = LIMIT_LABELS.get(key, key)
    if ceiling == 0:
        return (
            f"اشتراک «{plan.name}» امکان افزودن {noun} را ندارد. "
            "برای فعال‌کردن این بخش اشتراک خود را ارتقا دهید."
        )
    return (
        f"سهمیهٔ اشتراک «{plan.name}» برای {noun} پر شده است "
        f"(حداکثر {_fa(ceiling)}). برای ادامه اشتراک خود را ارتقا دهید."
    )


def feature_message(plan_id, feature):
    plan = get(plan_id)
    what = FEATURE_LABELS.get(feature, feature)
    return f"«{what}» در اشتراک «{plan.name}» فعال نیست. برای استفاده اشتراک خود را ارتقا دهید."


# ---------------------------------------------------------------------------
# Selling one
# ---------------------------------------------------------------------------
def is_purchasable(plan_id, period):
    return plan_id in PAID_IDS and period in PERIOD_DAYS


def amount_rial(plan_id, period):
    """What to charge, in Rial. None if this is not something you can buy —
    callers must treat that as a refusal, not as "free"."""
    if not is_purchasable(plan_id, period):
        return None
    return get(plan_id).price(period)


def toman(rial):
    """The display amount. The *only* place ریال becomes تومان (rule 1)."""
    return int(rial or 0) // TOMAN


def expiry_after(period, *, start=None, extend_from=None):
    """When a subscription bought now should end, as a naive-UTC ISO string.

    `extend_from` is the buyer's current expiry: renewing early stacks onto the
    time already paid for instead of throwing it away. A date in the past is
    ignored, so a lapsed subscription restarts from today rather than from the
    day it died.
    """
    base = start or datetime.utcnow()
    if extend_from:
        try:
            current = datetime.fromisoformat(str(extend_from))
            if current > base:
                base = current
        except (TypeError, ValueError):
            pass
    return (base + timedelta(days=PERIOD_DAYS.get(period, PERIOD_DAYS[MONTHLY]))).isoformat()


def days_left(expires_at, *, now=None):
    """Whole days until a subscription lapses; None when it never does.

    Rounded down and floored at zero, so «۰ روز» is the last day rather than a
    negative number, and an already-expired subscription reads 0 instead of
    inviting the caller to compare a negative.
    """
    if not expires_at:
        return None
    try:
        deadline = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError):
        return None
    return max(0, (deadline - (now or datetime.utcnow())).days)


# ---------------------------------------------------------------------------
# Serialisation for the API and the templates
# ---------------------------------------------------------------------------
def _limits_payload(plan):
    return {key: plan.limit(key) for key in LIMIT_LABELS}


def payload(plan_id):
    """One plan as the اشتراک screen renders it. Both currencies, always, so no
    client ever divides by ten (rule 1)."""
    plan = get(plan_id)
    return {
        "id": plan.id,
        "name": plan.name,
        "tagline": plan.tagline,
        "monthly_rial": plan.monthly_rial,
        "monthly_toman": toman(plan.monthly_rial),
        "yearly_rial": plan.yearly_rial,
        "yearly_toman": toman(plan.yearly_rial),
        # What the yearly price saves against twelve monthly ones, so the
        # «دو ماه هدیه» claim on the card is computed rather than asserted.
        "yearly_saving_toman": toman(max(0, plan.monthly_rial * 12 - plan.yearly_rial)),
        "limits": _limits_payload(plan),
        "features": sorted(plan.features),
        "paid": plan.id in PAID_IDS,
    }


def catalog():
    """Every plan, in upgrade order."""
    return [payload(pid) for pid in PLAN_IDS]


def feature_matrix():
    """Rows for the comparison table: one per capability, one column per plan.

    Built from the same dicts the enforcement reads, so a feature added to a
    plan cannot be missing from the pricing page — the two cannot disagree.
    """
    rows = []
    for key, label in LIMIT_LABELS.items():
        rows.append({
            "kind": "limit",
            "key": key,
            "label": label,
            "values": {pid: get(pid).limit(key) for pid in PLAN_IDS},
        })
    for key, label in FEATURE_LABELS.items():
        rows.append({
            "kind": "feature",
            "key": key,
            "label": label,
            "values": {pid: get(pid).has(key) for pid in PLAN_IDS},
        })
    return rows

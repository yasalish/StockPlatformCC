"""
account.py — API حساب کاربری: تنظیمات و غربالگرهای ذخیره‌شده
The signed-in user's own record: their display preferences and their saved
filter presets.

Two warnings about the name and the location, both learned the hard way in the
sibling project:

* It is **not** called `profile.py`. That name shadows the standard library's
  `profile` module, and the breakage is remote from the cause: `import cProfile`
  starts failing process-wide, in code that has nothing to do with this file.
* It does **not** live in `auth.py`. The endpoints there are reachable
  unauthenticated by design — that is what a login form is — and mixing routes
  that assume `current_user` into that module is how an authorisation check ends
  up "obviously" unnecessary to a later reader.

Every route here acts on `current_user` and takes no user id, so there is no
object to authorise: you can only ever read or write your own row. The one
exception is deleting a saved screen, which takes an id and therefore checks
ownership — and answers **404, not 403**, so the response cannot be used to
discover that someone else's screen exists.

Scope note: `.claude/PORT_CONTRACT.md` §5.1 describes this blueprint as also
carrying `/api/me` (profile), `/api/me/password` and the subscription fields.
Those belong to the plans/billing port, which is not implemented yet; the two
sections below are written to that contract's signatures so the rest can be
added around them without moving anything.
"""
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

import db
import prefs

account_bp = Blueprint("account", __name__)

# A sanity ceiling, not a plan quota. `.claude/PORT_CONTRACT.md` §8.1 puts the
# real, plan-aware limit in guards.require_quota(plans.SCREENS, …) — until that
# port lands, saved screens are free and unlimited to the user, and this number
# exists only so a scripted client cannot fill the table.
MAX_SCREENS = 100
MAX_NAME = 60
MAX_QUERY = 2000
SCREEN_PAGES = ("market", "screener", "performance", "strategies", "filters", "heatmap")


# ---------------------------------------------------------------------------
# تنظیمات — display preferences
# ---------------------------------------------------------------------------
@account_bp.route("/api/me/prefs", methods=["GET"])
@login_required
def get_prefs():
    return jsonify(prefs.payload(db.get_prefs(current_user.id)))


@account_bp.route("/api/me/prefs", methods=["PATCH"])
@login_required
def patch_prefs():
    """Save the settings this request carries.

    `silent=True` on get_json: a client that forgets the Content-Type header
    should get the Persian «قالب تنظیمات نامعتبر است» from prefs.validate(),
    not a 400 from Werkzeug's JSON parser with an English body the UI cannot
    show.
    """
    values = request.get_json(silent=True)
    error = prefs.validate(values)
    if error:
        return jsonify({"error": error}), 400
    # Individual bad values were already dropped by prefs.normalize() inside
    # set_prefs — a stale tab posting last week's option list saves the rest of
    # its changes instead of failing the whole request.
    return jsonify(db.set_prefs(current_user.id, values))


@account_bp.route("/api/me/prefs/reset", methods=["POST"])
@login_required
def reset_prefs():
    return jsonify(db.reset_prefs(current_user.id))


# ---------------------------------------------------------------------------
# غربالگرهای ذخیره‌شده — saved filter presets
# ---------------------------------------------------------------------------
def validate_screen_name(name):
    """None, or a Persian message. A preset with no name is unusable in a list,
    and a very long one breaks the layout of every row beside it."""
    name = (name or "").strip()
    if not name:
        return "نام غربالگر را وارد کنید."
    if len(name) > MAX_NAME:
        return f"نام غربالگر نباید بیش از {db.to_persian_plain(MAX_NAME)} نویسه باشد."
    return None


@account_bp.route("/api/me/screens", methods=["GET"])
@login_required
def list_screens():
    return jsonify({"screens": db.list_screens(current_user.id)})


@account_bp.route("/api/me/screens", methods=["POST"])
@login_required
def create_screen():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    error = validate_screen_name(name)
    if error:
        return jsonify({"error": error}), 400

    kind = body.get("kind") if body.get("kind") in prefs.KINDS else "stock"
    page = body.get("page") if body.get("page") in SCREEN_PAGES else "market"
    # The query string verbatim, minus a leading '?' the caller may have kept.
    query = (body.get("query") or "").lstrip("?")[:MAX_QUERY]

    if db.count_screens(current_user.id) >= MAX_SCREENS:
        return jsonify({"error": "تعداد غربالگرهای ذخیره‌شده به سقف رسیده است."}), 400

    row = db.create_screen(current_user.id, name, kind, page, query)
    if row is None:                       # UNIQUE (user_id, name)
        return jsonify({"error": "غربالگری با همین نام از قبل ذخیره شده است."}), 409
    return jsonify(row), 201


@account_bp.route("/api/me/screens/<int:screen_id>", methods=["DELETE"])
@login_required
def delete_screen(screen_id):
    row = db.get_screen(screen_id)
    # 404 rather than 403 for someone else's screen: a 403 would confirm that
    # the id exists, which is a fact about another account's data.
    if not row or row["user_id"] != current_user.id:
        return jsonify({"error": "یافت نشد."}), 404
    db.delete_screen(screen_id)
    return jsonify({"ok": True})

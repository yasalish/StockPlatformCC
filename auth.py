"""
auth.py — احراز هویت کاربران (ثبت‌نام / ورود / خروج)
Authentication for بورس‌نگار (BourseNegar): self-registration, login, logout,
and optional Google sign-in.

Uses Flask-Login for session management and Werkzeug for password hashing.
Passwords are never stored in plaintext — only a salted hash is persisted.
The very first account created becomes an «admin» (so the data-update page has
an owner); everyone after that is a normal «user».
"""
import os
import re

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash

import db

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "برای ادامه لطفاً وارد شوید."

auth_bp = Blueprint("auth", __name__)

# ---------------------------------------------------------------------------
# Google OAuth (OpenID Connect). Enabled only when GOOGLE_CLIENT_ID/SECRET are
# configured in the environment, so the app runs fine without Google set up.
# Authlib is imported lazily so the app doesn't hard-depend on it.
# ---------------------------------------------------------------------------
oauth = None
GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"


def google_enabled():
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def init_oauth(app):
    """Register the Google provider on the Flask app (call once at startup)."""
    global oauth
    if not google_enabled():
        return
    try:
        from authlib.integrations.flask_client import OAuth
    except ImportError:
        return
    oauth = OAuth()
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        server_metadata_url=GOOGLE_DISCOVERY,
        client_kwargs={"scope": "openid email profile"},
    )


@auth_bp.app_context_processor
def _inject_auth_flags():
    """Expose `google_enabled` to every template (drives the Google button)."""
    return {"google_enabled": google_enabled()}


# A username is 3–32 chars: Latin letters, digits, dot, dash, underscore.
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MIN_PASSWORD = 6


class User(UserMixin):
    """Thin wrapper Flask-Login stores in the session (by id)."""

    def __init__(self, row):
        self.id = row["id"]
        self.username = row["username"]
        self.display_name = row.get("display_name") or row["username"]
        self.role = row.get("role") or "user"

    @property
    def is_admin(self):
        return self.role == "admin"


@login_manager.user_loader
def load_user(user_id):
    try:
        row = db.get_user(int(user_id))
    except (TypeError, ValueError):
        return None
    return User(row) if row else None


def _validate_registration(username, password, confirm):
    """Return an error message (str) or None if the input is valid."""
    if not USERNAME_RE.match(username):
        return "نام کاربری باید ۳ تا ۳۲ نویسه و شامل حروف انگلیسی، عدد، نقطه، خط تیره یا زیرخط باشد."
    if len(password) < MIN_PASSWORD:
        return f"گذرواژه باید حداقل {db.to_persian(MIN_PASSWORD)} نویسه باشد."
    if password != confirm:
        return "گذرواژه و تکرار آن یکسان نیستند."
    return None


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        display_name = (request.form.get("display_name") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        err = _validate_registration(username, password, confirm)
        if err:
            flash(err, "error")
            return render_template("login_register.html", mode="register",
                                   username=username, display_name=display_name)

        # First account ever → admin (owns the data-update page); others → user.
        role = "admin" if db.count_users() == 0 else "user"
        uid = db.create_user(
            username,
            generate_password_hash(password),
            display_name=display_name or username,
            role=role,
        )
        if uid is None:
            flash("این نام کاربری قبلاً گرفته شده است.", "error")
            return render_template("login_register.html", mode="register",
                                   username="", display_name=display_name)

        # Log the new user straight in.
        login_user(User(db.get_user(uid)), remember=True)
        db.touch_user_login(uid)
        flash("خوش آمدید! حساب شما ساخته شد.", "success")
        return redirect(url_for("index"))

    return render_template("login_register.html", mode="register")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))

        row = db.get_user_by_username(username)
        if not row or not check_password_hash(row["password_hash"], password):
            flash("نام کاربری یا گذرواژه نادرست است.", "error")
            return render_template("login_register.html", mode="login", username=username)

        login_user(User(row), remember=remember)
        db.touch_user_login(row["id"])

        # Only honour a same-site relative `next` target (avoid open redirects).
        nxt = request.args.get("next") or ""
        if nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect(url_for("index"))

    return render_template("login_register.html", mode="login")


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("از حساب خود خارج شدید.", "success")
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Google sign-in
# ---------------------------------------------------------------------------
def _sanitize_username(base):
    """Reduce an arbitrary string (usually the email local-part) to a valid
    username: only [A-Za-z0-9._-], 3–28 chars."""
    base = re.sub(r"[^A-Za-z0-9._-]", "", base or "")[:28]
    while len(base) < 3:
        base += "0"
    return base


def _unique_username(base):
    uname = _sanitize_username(base)
    root, i = uname, 0
    while db.get_user_by_username(uname):
        i += 1
        uname = root[:26] + str(i)
    return uname


def _find_or_create_google_user(info):
    """Map a verified Google profile to a local user (find, link, or create)."""
    sub = info.get("sub")
    email = (info.get("email") or "").strip()
    name = (info.get("name") or "").strip()

    row = db.get_user_by_google_id(sub)
    if row:
        return row

    # Same email as an existing password account → link the two.
    if email:
        row = db.get_user_by_email(email)
        if row:
            db.link_google(row["id"], sub, email)
            return db.get_user(row["id"])

    base = email.split("@")[0] if email else ("g" + (sub or "")[:8])
    username = _unique_username(base)
    role = "admin" if db.count_users() == 0 else "user"
    uid = db.create_oauth_user(username, email, sub, display_name=name or username, role=role)
    if uid is None:  # extremely unlikely race on the username
        uid = db.create_oauth_user(_unique_username(base), email, sub,
                                   display_name=name or username, role=role)
    return db.get_user(uid)


@auth_bp.route("/login/google")
def google_login():
    if not google_enabled() or oauth is None:
        flash("ورود با گوگل پیکربندی نشده است.", "error")
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("auth.google_callback", _external=True)
    try:
        return oauth.google.authorize_redirect(redirect_uri)
    except Exception:
        # Google unreachable — typically the proxy/VPN is down (accounts.google.com
        # is filtered), so the discovery-document fetch fails.
        flash("اتصال به گوگل ممکن نشد. لطفاً فیلترشکن/پروکسی خود را روشن کنید و دوباره تلاش کنید.", "error")
        return redirect(url_for("auth.login"))


@auth_bp.route("/auth/google/callback")
def google_callback():
    if not google_enabled() or oauth is None:
        return redirect(url_for("auth.login"))
    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash("ورود با گوگل ناموفق بود یا لغو شد.", "error")
        return redirect(url_for("auth.login"))

    info = token.get("userinfo")
    if not info:
        try:
            info = oauth.google.userinfo(token=token)
        except Exception:
            info = None
    if not info or not info.get("sub"):
        flash("دریافت اطلاعات حساب گوگل ممکن نشد.", "error")
        return redirect(url_for("auth.login"))
    # Only trust verified Google emails for account linking / creation.
    if info.get("email") and info.get("email_verified") is False:
        info["email"] = ""

    user = _find_or_create_google_user(info)
    if not user:
        flash("ساخت حساب با گوگل ممکن نشد.", "error")
        return redirect(url_for("auth.login"))

    login_user(User(user), remember=True)
    db.touch_user_login(user["id"])
    flash("با گوگل وارد شدید.", "success")
    return redirect(url_for("index"))

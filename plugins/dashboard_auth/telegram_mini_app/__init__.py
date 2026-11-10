"""TelegramAuthProvider — dashboard sign-in from a Telegram Mini App.

A Mini App is a web page Telegram opens inside its own client, here from the bot's menu button. Telegram
hands the page signed launch data (``initData``: the user, ``auth_date`` and an HMAC over them keyed with
the bot token; https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app). That
data is this provider's credential:

1. The menu button opens ``/auth/login?provider=telegram-mini-app``. ``start_login`` answers with its own page
   (``LoginStart.page_html``), which reads the launch data from the URL fragment Telegram appended (or the
   tab's ``sessionStorage``, where it keeps it for a later sign-in in the same Mini App) and form-posts
   it as ``code`` to ``/auth/callback`` with the round trip's ``state``.
2. ``complete_login`` checks the HMAC, the age of ``auth_date`` and that the user id is in
   ``dashboard.telegram_mini_app.allowed_users``; anyone else, or a page outside Telegram, gets no session.

Sessions are stateless HMAC-signed tokens (``_shared.sign_token``) keyed with a key derived from the bot
token, so they survive restarts and end when the token is revoked; ``verify_session`` re-checks the
allow-list, so removing an id ends that user's sessions at once. The bot token comes from
``TELEGRAM_BOT_TOKEN`` (the Telegram platform's secret, in ``.env``).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import secrets
import time
from typing import Iterable, Optional
from urllib.parse import parse_qsl

from hermes_cli.dashboard_auth import (
    DashboardAuthProvider, InvalidCodeError, LoginStart, ProviderError, RefreshExpiredError, Session)
from plugins.dashboard_auth._shared import (
    SkipRegistration, load_config_section, register_provider, sign_token, unsign_token,
    validate_redirect_uri)

logger = logging.getLogger(__name__)
_TAG = "dashboard-auth-telegram"

_DEFAULT_TTL_SECONDS = 12 * 60 * 60
_REFRESH_TTL_SECONDS = 30 * 24 * 60 * 60
# Launch data older than this is refused. Telegram signs it when the Mini App opens; a sign-in later in
# the same Mini App (after the session lapsed) reuses it from sessionStorage, so allow a day.
LAUNCH_DATA_MAX_AGE_SECONDS = 24 * 60 * 60
# auth_date ahead of this host's clock by more than this is refused.
_CLOCK_SKEW_SECONDS = 60

LAST_SKIP_REASON: str = ""


class LaunchDataError(ValueError):
    """Mini App launch data that is malformed, unsigned, forged or expired."""


def verify_launch_data(init_data: str, bot_token: str, *, now: Optional[int] = None,
                       max_age: int = LAUNCH_DATA_MAX_AGE_SECONDS) -> dict:
    """The fields of Mini App launch data (``user`` decoded) once its HMAC and age check out.

    The data-check string is every field but ``hash``, ``key=value`` sorted by key and joined with
    ``\\n``; the key is HMAC-SHA256 of the bot token keyed with ``"WebAppData"``."""
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise LaunchDataError("launch data is not a query string") from exc
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise LaunchDataError("launch data repeats a field")
    received = fields.pop("hash", "")
    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not received or not hmac.compare_digest(expected, received):
        raise LaunchDataError("launch data signature does not match this bot")
    try:
        auth_date = int(fields.get("auth_date", ""))
        user = json.loads(fields.get("user", ""))
        user_id = int(user["id"])
    except (ValueError, TypeError, KeyError) as exc:
        raise LaunchDataError("launch data has no user or auth_date") from exc
    now = int(time.time()) if now is None else now
    if now - auth_date > max_age or auth_date - now > _CLOCK_SKEW_SECONDS:
        raise LaunchDataError("launch data has expired; reopen the Mini App")
    return {**fields, "user": {**user, "id": user_id}, "auth_date": auth_date}


# The page's one script carries no per-request value, so its CSP hash is stable.
_SIGN_IN_SCRIPT = """
(function () {
  var KEY = "hermes.telegram.launchData";
  var data = "";
  try { data = new URLSearchParams(location.hash.slice(1)).get("tgWebAppData") || ""; } catch (e) {}
  try {
    if (data) { sessionStorage.setItem(KEY, data); }
    else { data = sessionStorage.getItem(KEY) || ""; }
  } catch (e) {}
  function post(type) {
    try {
      if (window.TelegramWebviewProxy) { window.TelegramWebviewProxy.postEvent(type, undefined); }
      else if (window.external && "notify" in window.external) {
        window.external.notify(JSON.stringify({eventType: type, eventData: undefined}));
      }
    } catch (e) {}
  }
  post("web_app_ready");
  post("web_app_expand");
  var form = document.getElementById("sign-in");
  if (!data) {
    document.getElementById("status").hidden = true;
    document.getElementById("outside").hidden = false;
    return;
  }
  form.elements.code.value = data;
  form.submit();
})();
"""

_SIGN_IN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="referrer" content="no-referrer"><title>Hermes</title>
<style>
body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0; min-height: 100vh; display: flex;
  align-items: center; justify-content: center; padding: 1.5rem; box-sizing: border-box;
  color: #1f2328; background: #fff; }}
@media (prefers-color-scheme: dark) {{ body {{ color: #e6edf3; background: #0d1117; }} }}
main {{ max-width: 26rem; text-align: center; line-height: 1.5; }}
</style></head>
<body><main>
<p id="status">Signing in with Telegram&hellip;</p>
<div id="outside" hidden>
<p><strong>Open Hermes from Telegram.</strong></p>
<p>This sign-in works only inside the Telegram app: open your bot and tap its menu button.</p>
</div>
<form id="sign-in" method="post" action="{action}">
<input type="hidden" name="code" value="">
<input type="hidden" name="state" value="{state}">
</form>
</main>
<script>{script}</script>
</body></html>
"""


def render_sign_in_page(*, callback_url: str, state: str) -> str:
    return _SIGN_IN_PAGE.format(
        action=html.escape(callback_url, quote=True), state=html.escape(state, quote=True),
        script=_SIGN_IN_SCRIPT)


class TelegramAuthProvider(DashboardAuthProvider):
    """Mini App launch data in, stateless HMAC-signed sessions out; only ``allowed_users`` get one."""

    name = "telegram-mini-app"
    display_name = "Telegram"

    def __init__(self, *, bot_token: str, allowed_users: Iterable[int],
                 ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        if not bot_token:
            raise ValueError("bot_token must be non-empty")
        self._allowed = frozenset(int(user) for user in allowed_users)
        if not self._allowed:
            raise ValueError("allowed_users must name at least one Telegram user id")
        self._bot_token = bot_token
        # A key of its own for session tokens, bound to this bot: a new bot token ends every session.
        self._secret = hmac.new(bot_token.encode("utf-8"), b"hermes-dashboard-session", hashlib.sha256).digest()
        self._ttl = max(60, int(ttl_seconds))

    # ---- sign-in ----------------------------------------------------------

    def start_login(self, *, redirect_uri: str) -> LoginStart:
        validate_redirect_uri(redirect_uri)
        state = secrets.token_urlsafe(32)
        return LoginStart(
            redirect_url="", cookie_payload={"hermes_session_pkce": f"state={state};verifier="},
            page_html=render_sign_in_page(callback_url=redirect_uri, state=state))

    def complete_login(self, *, code: str, state: str, code_verifier: str, redirect_uri: str) -> Session:
        try:
            data = verify_launch_data(code, self._bot_token)
        except LaunchDataError as exc:
            raise InvalidCodeError(str(exc)) from exc
        user = data["user"]
        if user["id"] not in self._allowed:
            raise InvalidCodeError("this Telegram account is not allowed to sign in")
        name = " ".join(str(user.get(k, "")).strip() for k in ("first_name", "last_name")).strip()
        username = str(user.get("username", "")).strip()
        return self._mint_session(user["id"], name or (f"@{username}" if username else str(user["id"])))

    # ---- session lifecycle -------------------------------------------------

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        payload = unsign_token(access_token, self._secret, "access")
        if payload is None or not self._still_allowed(payload):
            return None
        return self._session(payload, access_token, "")

    def refresh_session(self, *, refresh_token: str) -> Session:
        payload = unsign_token(refresh_token, self._secret, "refresh") if refresh_token else None
        if payload is None or not self._still_allowed(payload):
            raise RefreshExpiredError("refresh token expired, invalid or no longer allowed")
        return self._mint_session(int(payload["sub"]), str(payload.get("name", "")))

    def revoke_session(self, *, refresh_token: str) -> None:
        return None  # stateless: a session ends with its TTL, the allow-list or the bot token

    # ---- internals ---------------------------------------------------------

    def _still_allowed(self, payload: dict) -> bool:
        try:
            return int(payload.get("sub", "")) in self._allowed
        except (TypeError, ValueError):
            return False

    def _mint_session(self, user_id: int, name: str) -> Session:
        now = int(time.time())
        jti = secrets.token_urlsafe(16)
        claims = {"sub": str(user_id), "name": name, "jti": jti}
        access = {**claims, "kind": "access", "exp": now + self._ttl}
        refresh = {**claims, "kind": "refresh", "exp": now + _REFRESH_TTL_SECONDS}
        return self._session(access, sign_token(access, self._secret), sign_token(refresh, self._secret))

    def _session(self, payload: dict, access_token: str, refresh_token: str) -> Session:
        return Session(
            user_id=str(payload["sub"]), email="", display_name=str(payload.get("name", "")) or str(payload["sub"]),
            org_id="", provider=self.name, expires_at=int(payload["exp"]),
            access_token=access_token, refresh_token=refresh_token)


# ---- Plugin entry point ----

def _bot_token() -> str:
    from agent.secret_scope import UnscopedSecretError, get_secret

    try:
        return (get_secret("TELEGRAM_BOT_TOKEN") or "").strip()
    except UnscopedSecretError as exc:
        raise ProviderError(str(exc)) from exc


def _allowed_users(raw: object) -> list[int]:
    values = raw if isinstance(raw, (list, tuple)) else [raw] if raw not in (None, "") else []
    try:
        return [int(str(value).strip()) for value in values]
    except ValueError as exc:
        raise SkipRegistration(
            f"dashboard.telegram_mini_app.allowed_users must hold numeric Telegram user ids: {exc}",
            level="warning") from exc


def _settings() -> dict:
    section = load_config_section(logger, _TAG, "dashboard", "telegram_mini_app")
    users = _allowed_users(section.get("allowed_users"))
    if not users:
        raise SkipRegistration(
            "dashboard.telegram_mini_app.allowed_users is empty; list your numeric Telegram user id there "
            "to sign in to the dashboard from the bot's Mini App.")
    token = _bot_token()
    if not token:
        raise SkipRegistration(
            "dashboard.telegram_mini_app.allowed_users is set but TELEGRAM_BOT_TOKEN is not: the Mini App "
            "sign-in needs the bot's token; set it in .env as the Telegram gateway does.", level="warning")
    try:
        ttl = int(section.get("session_ttl_seconds") or _DEFAULT_TTL_SECONDS)
    except (TypeError, ValueError):
        ttl = _DEFAULT_TTL_SECONDS
    return {"bot_token": token, "allowed_users": users, "ttl_seconds": ttl}


def register(ctx) -> None:
    """Register ``TelegramAuthProvider`` when ``dashboard.telegram_mini_app.allowed_users`` and the bot
    token are set; a no-op otherwise."""
    global LAST_SKIP_REASON
    LAST_SKIP_REASON = ""
    kwargs, LAST_SKIP_REASON = register_provider(ctx, logger, _TAG, TelegramAuthProvider, _settings)
    if kwargs is not None:
        logger.info("dashboard-auth-telegram: registered Mini App provider for %d user(s)",
                    len(kwargs["allowed_users"]))

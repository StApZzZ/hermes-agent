"""The Telegram Mini App dashboard-auth provider: launch-data checks and the sessions it mints.

Launch data is built here the way Telegram builds it (https://core.telegram.org/bots/webapps
#validating-data-received-via-the-mini-app), so a pass proves the HMAC scheme, not a mirror of it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

import hermes_yaml as yaml
from hermes_cli.dashboard_auth import InvalidCodeError, RefreshExpiredError
from hermes_cli.dashboard_auth.base import assert_protocol_compliance
from plugins.dashboard_auth import telegram_mini_app as telegram
from plugins.dashboard_auth.telegram_mini_app import LaunchDataError, TelegramAuthProvider, verify_launch_data

BOT_TOKEN = "123456:TEST-bot-token"
OWNER = 777000111


def launch_data(user_id: int = OWNER, *, token: str = BOT_TOKEN, auth_date: int | None = None,
                extra: dict | None = None) -> str:
    fields = {
        "query_id": "AAH-test",
        "user": json.dumps({"id": user_id, "first_name": "Alex", "username": "alex"}, separators=(",", ":")),
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        **(extra or {}),
    }
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def provider(**kw) -> TelegramAuthProvider:
    return TelegramAuthProvider(bot_token=BOT_TOKEN, allowed_users=kw.pop("allowed_users", [OWNER]), **kw)


def sign_in(p: TelegramAuthProvider, data: str):
    start = p.start_login(redirect_uri="https://hermes.example/auth/callback")
    state = start.cookie_payload["hermes_session_pkce"].split(";")[0].removeprefix("state=")
    return p.complete_login(code=data, state=state, code_verifier="", redirect_uri="")


def test_protocol_compliance():
    assert_protocol_compliance(TelegramAuthProvider)


class TestLaunchData:
    def test_genuine_data_verifies(self):
        data = verify_launch_data(launch_data(extra={"signature": "abc"}), BOT_TOKEN)
        assert data["user"]["id"] == OWNER

    @pytest.mark.parametrize("data", [
        launch_data(token="999:other-bot"),                       # another bot's signature
        launch_data().replace("Alex", "Eve"),                     # tampered user
        launch_data() + "&hash=00",                               # repeated field
        "user=%7B%7D&auth_date=1",                                # unsigned
        "not a query string",
    ])
    def test_forged_or_malformed_data_is_refused(self, data):
        with pytest.raises(LaunchDataError):
            verify_launch_data(data, BOT_TOKEN)

    def test_old_data_is_refused(self):
        stale = int(time.time()) - telegram.LAUNCH_DATA_MAX_AGE_SECONDS - 5
        with pytest.raises(LaunchDataError, match="expired"):
            verify_launch_data(launch_data(auth_date=stale), BOT_TOKEN)


class TestSessions:
    def test_owner_gets_a_session_that_verifies_and_refreshes(self):
        p = provider()
        session = sign_in(p, launch_data())
        assert (session.user_id, session.provider, session.display_name) == (str(OWNER), "telegram-mini-app", "Alex")
        assert p.verify_session(access_token=session.access_token).user_id == str(OWNER)
        assert p.refresh_session(refresh_token=session.refresh_token).user_id == str(OWNER)

    def test_another_account_gets_no_session(self):
        with pytest.raises(InvalidCodeError, match="not allowed"):
            sign_in(provider(), launch_data(user_id=OWNER + 1))

    def test_removing_the_user_ends_their_sessions(self):
        session = sign_in(provider(), launch_data())
        later = provider(allowed_users=[OWNER + 1])
        assert later.verify_session(access_token=session.access_token) is None
        with pytest.raises(RefreshExpiredError):
            later.refresh_session(refresh_token=session.refresh_token)

    def test_a_new_bot_token_ends_every_session(self):
        session = sign_in(provider(), launch_data())
        rotated = TelegramAuthProvider(bot_token="123456:NEW", allowed_users=[OWNER])
        assert rotated.verify_session(access_token=session.access_token) is None


class TestRegistration:
    @pytest.fixture
    def ctx(self):
        class Ctx:
            registered: list = []

            def register_dashboard_auth_provider(self, p):
                self.registered.append(p)
        return Ctx()

    def _config(self, section: dict) -> None:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text(yaml.safe_dump({"dashboard": {"telegram_mini_app": section}}))

    def test_registers_with_users_and_token(self, ctx, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
        self._config({"allowed_users": [OWNER]})
        telegram.register(ctx)
        assert [p.name for p in ctx.registered] == ["telegram-mini-app"]

    @pytest.mark.parametrize("section,token", [({"allowed_users": []}, BOT_TOKEN), ({"allowed_users": [OWNER]}, "")])
    def test_skips_without_users_or_token(self, ctx, monkeypatch, section, token):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
        self._config(section)
        telegram.register(ctx)
        assert ctx.registered == [] and telegram.LAST_SKIP_REASON


def test_a_mini_app_sign_in_is_the_owner_while_bot_chat_users_stay_foreign():
    """The provider's name must not be a messaging platform's: ``telegram:<id>`` labels name the
    people who chat with the bot, and a registered provider's ``<name>:<user>`` labels read as the owner's."""
    from gateway.owner_identity import host_owner_subject, principal_subject, same_owner
    from hermes_cli.dashboard_auth import clear_providers, register_provider

    clear_providers()
    register_provider(provider())
    try:
        owner = host_owner_subject()
        assert principal_subject({"user_id": str(OWNER), "provider": "telegram-mini-app"}) == owner
        assert not same_owner(f"telegram:{OWNER + 1}", owner)
    finally:
        clear_providers()

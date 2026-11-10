"""``dashboard.auth_networks`` limits where a provider's sign-in and sessions work; the Telegram
Mini App sign-in runs end to end through the real gate.

The harness drives the real ``web_server.app`` (gate engaged, as on a public bind) with the real
basic and Telegram providers, from clients at different addresses. The motivating setup: password
sign-in only from VPN exit addresses, while the Telegram Mini App works from anywhere for its owner.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import hermes_yaml as yaml
from hermes_cli import web_server
from hermes_cli.dashboard_auth import clear_providers, register_provider
from hermes_cli.dashboard_auth.routes import _reset_password_rate_limit
from plugins.dashboard_auth.basic import BasicAuthProvider, hash_password
from plugins.dashboard_auth.telegram_mini_app import TelegramAuthProvider

HOST = "hermes.example"
VPN = "198.51.100.7"
ELSEWHERE = "203.0.113.9"
BOT_TOKEN = "123456:TEST-bot-token"
OWNER = 777000111


def _write_networks(networks: dict) -> None:
    from hermes_constants import get_hermes_home

    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(yaml.safe_dump({"dashboard": {"auth_networks": networks}}))


@pytest.fixture
def gate():
    clear_providers()
    register_provider(BasicAuthProvider(username="admin", password_hash=hash_password("pw-123456"),
                                        secret=b"0123456789abcdef0123456789abcdef"))
    register_provider(TelegramAuthProvider(bot_token=BOT_TOKEN, allowed_users=[OWNER]))
    _reset_password_rate_limit()
    state = web_server.app.state
    saved = {k: getattr(state, k, None) for k in ("bound_host", "bound_port", "auth_required")}
    state.bound_host, state.bound_port, state.auth_required = HOST, 443, True
    _write_networks({"basic": [f"{VPN}/32", "2001:db8::/32"]})
    yield
    clear_providers()
    _reset_password_rate_limit()
    for key, value in saved.items():
        setattr(state, key, value)


def client(address: str) -> TestClient:
    return TestClient(web_server.app, base_url=f"https://{HOST}", client=(address, 50000))


def password_login(c: TestClient):
    return c.post("/auth/password-login", json={"provider": "basic", "username": "admin", "password": "pw-123456"})


def launch_data(user_id: int = OWNER) -> str:
    fields = {"user": json.dumps({"id": user_id, "first_name": "Alex"}), "auth_date": str(int(time.time()))}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(key, check.encode(), hashlib.sha256).hexdigest()})


def telegram_sign_in(c: TestClient, data: str):
    page = c.get("/auth/login?provider=telegram-mini-app")
    assert page.status_code == 200
    state = re.search(r'name="state" value="([^"]+)"', page.text).group(1)
    return c.post("/auth/callback", data={"code": data, "state": state}, follow_redirects=False)


class TestPasswordOnlyFromAllowedNetworks:
    def test_password_sign_in_from_the_vpn_works(self, gate):
        c = client(VPN)
        assert password_login(c).status_code == 200
        assert c.get("/api/auth/me").json()["provider"] == "basic"

    def test_password_sign_in_from_elsewhere_is_refused_before_the_password_is_checked(self, gate):
        assert password_login(client(ELSEWHERE)).status_code == 403

    def test_a_vpn_session_stops_working_off_the_vpn_and_resumes_on_it(self, gate):
        on_vpn = client(VPN)
        password_login(on_vpn)
        off_vpn = client(ELSEWHERE)
        off_vpn.cookies = on_vpn.cookies
        refused = off_vpn.get("/api/auth/me")
        assert (refused.status_code, refused.json()["error"]) == (403, "network_not_allowed")
        assert off_vpn.get("/sessions", follow_redirects=False).status_code == 403
        assert on_vpn.get("/api/auth/me").status_code == 200

    def test_ipv4_mapped_and_ipv6_addresses_match(self, gate):
        assert password_login(client(f"::ffff:{VPN}")).status_code == 200
        assert password_login(client("2001:db8::5")).status_code == 200

    def test_sign_in_options_hide_what_this_address_cannot_use(self, gate):
        def names(c: TestClient) -> list[str]:
            return [p["name"] for p in c.get("/api/auth/providers").json()["providers"]]

        assert names(client(VPN)) == ["basic", "telegram-mini-app"]
        assert names(client(ELSEWHERE)) == ["telegram-mini-app"]
        assert "/auth/password-login" not in client(ELSEWHERE).get("/login").text

    def test_an_address_no_provider_accepts_gets_a_refusal_page(self, gate):
        _write_networks({"basic": [VPN], "telegram-mini-app": [VPN]})
        assert client(ELSEWHERE).get("/login").status_code == 403

    def test_a_list_with_nothing_valid_accepts_no_address(self, gate):
        _write_networks({"basic": ["not-an-address"]})
        assert password_login(client(VPN)).status_code == 403

    def test_an_unnamed_provider_is_accepted_anywhere(self, gate):
        _write_networks({})
        assert password_login(client(ELSEWHERE)).status_code == 200


class TestTelegramMiniAppSignIn:
    def test_owner_signs_in_from_anywhere(self, gate):
        c = client(ELSEWHERE)
        done = telegram_sign_in(c, launch_data())
        assert (done.status_code, done.headers["location"]) == (303, "/")
        me = c.get("/api/auth/me").json()
        assert (me["provider"], me["user_id"]) == ("telegram-mini-app", str(OWNER))

    def test_another_telegram_account_is_refused(self, gate):
        c = client(ELSEWHERE)
        assert telegram_sign_in(c, launch_data(user_id=OWNER + 1)).status_code == 400
        assert c.get("/api/auth/me").status_code == 401

    def test_the_sign_in_page_runs_only_its_own_script_and_posts_only_here(self, gate):
        page = client(ELSEWHERE).get("/auth/login?provider=telegram-mini-app")
        csp = page.headers["content-security-policy"]
        assert "script-src 'sha256-" in csp and "form-action 'self'" in csp
        assert f'action="https://{HOST}/auth/callback"' in page.text

    def test_a_post_without_the_round_trip_cookie_is_refused(self, gate):
        c = client(ELSEWHERE)
        resp = c.post("/auth/callback", data={"code": launch_data(), "state": "guess"})
        assert resp.status_code == 400
        assert c.get("/api/auth/me").status_code == 401

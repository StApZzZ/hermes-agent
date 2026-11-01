"""The Nous free tier is opt-in: a profile that never set ``nous.guest: true`` creates no account.

Driven through the fake NAS (``anon_portal``) with Hermes' real client code, on a fresh profile with
no config at all, so the shipped default is what decides.
"""

from tests.hermes_cli.anon_portal import install_portal


def test_a_fresh_profile_offers_no_connectors_and_creates_no_guest(monkeypatch, tmp_path):
    from tools.connectors.gateway.config import connectors_available, ensure_guest_identity

    nas = install_portal(monkeypatch, tmp_path)

    assert not connectors_available()
    assert ensure_guest_identity() is None
    assert nas.calls == []

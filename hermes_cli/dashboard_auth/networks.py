"""Which client addresses may sign in with, and use a session from, each dashboard-auth provider.

``dashboard.auth_networks`` in the launch home's ``config.yaml`` maps a provider name to the IP
addresses and CIDR networks its sign-in and sessions are accepted from::

    dashboard:
      auth_networks:
        basic: [198.51.100.7, 2001:db8::/32]   # password sign-in only from these addresses

A provider the map does not name is accepted from any address. A named provider whose list holds
nothing usable (every entry invalid, or a value that is not a list) is accepted from no address:
a typo narrows access, never widens it. The map is read on each check (``load_config_readonly`` is
cached on the file's signature), so editing it needs no restart.

The client address is the ASGI peer (:func:`request_utils.client_ip`). Behind a reverse proxy uvicorn
rewrites it from ``X-Forwarded-For`` only for ``dashboard.trusted_proxies`` (loopback by default),
so the proxy must send that header; a proxy that does not makes every client look like the proxy,
which a list without loopback refuses.
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Iterable, Optional

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from hermes_cli.dashboard_auth.request_utils import client_ip

_log = logging.getLogger(__name__)

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network

#: ``reason`` of the 403 a request gets from an address its provider does not accept.
NETWORK_NOT_ALLOWED = "network_not_allowed"


def parse_networks(raw: object) -> tuple[_Network, ...]:
    """The networks a configured value names; invalid entries are dropped (and logged)."""
    if not isinstance(raw, (list, tuple)):
        _log.warning("dashboard.auth_networks: expected a list of addresses, got %r", raw)
        return ()
    networks = []
    for entry in raw:
        try:
            networks.append(ipaddress.ip_network(str(entry).strip(), strict=False))
        except ValueError:
            _log.warning("dashboard.auth_networks: ignoring %r (not an IP address or CIDR network)", entry)
    return tuple(networks)


def _configured() -> Optional[dict]:
    """The ``dashboard.auth_networks`` map, or ``None`` when config.yaml cannot be read."""
    from hermes_cli.config import load_config_readonly

    try:
        section = (load_config_readonly().get("dashboard") or {}).get("auth_networks")
    except Exception as exc:  # health: allow BLE001 -- an unreadable config.yaml must not open a restricted provider: the caller fails closed
        _log.warning("dashboard.auth_networks: config.yaml unreadable (%s); refusing every sign-in", exc)
        return None
    return section if isinstance(section, dict) else {}


def allowed_networks(provider: str) -> Optional[tuple[_Network, ...]]:
    """The networks *provider* is accepted from, or ``None`` when it is not restricted."""
    section = _configured()
    if section is None:
        return ()
    if provider not in section:
        return None
    return parse_networks(section[provider])


def _address(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return None
    # A dual-stack listener reports an IPv4 client as ::ffff:a.b.c.d.
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


def address_allowed(provider: str, address: str) -> bool:
    """Whether *provider*'s sign-in and sessions are accepted from *address*."""
    networks = allowed_networks(provider)
    if networks is None:
        return True
    parsed = _address(address)
    return parsed is not None and any(parsed in network for network in networks)


def request_allowed(request: Request, provider: str) -> bool:
    return address_allowed(provider, client_ip(request))


def providers_for(request: Request, providers: Iterable) -> list:
    """The subset of *providers* this request's address may sign in with."""
    address = client_ip(request)
    return [p for p in providers if address_allowed(p.name, address)]


_REFUSED_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Hermes</title></head>
<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1 style="font-size:1.25rem">Access from this network is not allowed</h1>
<p>This sign-in is accepted only from the networks the operator listed in
<code>dashboard.auth_networks</code>. Connect through one of them (for example your VPN) and reload.</p>
</body></html>"""


def refused_response(request: Request, provider: str) -> Response:
    """403 for a request whose address *provider* does not accept: JSON for ``/api/*``, else a page."""
    from hermes_cli.dashboard_auth.audit import AuditEvent, audit_log

    audit_log(AuditEvent.SESSION_REJECTED, reason=NETWORK_NOT_ALLOWED, provider=provider,
              path=request.url.path, ip=client_ip(request))
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"error": NETWORK_NOT_ALLOWED, "reason": NETWORK_NOT_ALLOWED,
             "detail": "Access from this network is not allowed."}, status_code=403)
    return HTMLResponse(_REFUSED_HTML, status_code=403, headers={"Cache-Control": "no-store"})

"""Carry a sync OpenAI client's credential and configured headers onto its async rebuild.

``_to_async_client`` recreates ``AsyncOpenAI`` from a resolved sync client. Two things do not
live on ``.api_key`` / the header set it recomputes: a per-request token provider (``key_cmd``,
Entra ID — the SDK parks the callable in ``_api_key_provider`` and leaves ``.api_key`` empty, so
an ``auth_headers`` built from the snapshot is ``{}`` and the request carries NO Authorization at
all) and the ``default_headers`` the client was constructed with (a named provider's
``extra_headers``). See #109595.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Dict

from openai import OpenAI


def async_api_key(sync_client: Any) -> Any:
    """Credential for the async twin: the sync token provider wrapped for ``await``, else the static key.

    The sync provider is ``Callable[[], str]``; ``AsyncOpenAI`` awaits its provider before every request,
    so the callable is run off-loop (it may shell out to a ``key_cmd``).
    """
    provider = getattr(sync_client, "_api_key_provider", None) if isinstance(sync_client, OpenAI) else None
    if not callable(provider):
        return sync_client.api_key

    async def _provide() -> str:
        return str(await asyncio.to_thread(provider))

    return _provide


def configured_default_headers(sync_client: Any) -> dict[str, str]:
    """The ``default_headers`` mapping the sync client was constructed with (SDK ``_custom_headers``).

    SECURITY: values may carry credentials — never log them.
    """
    configured = getattr(sync_client, "_custom_headers", None) if isinstance(sync_client, OpenAI) else None
    return dict(configured) if isinstance(configured, Mapping) else {}


class _AsyncProcessCompletions:
    def __init__(self, sync_completions: Any):
        self._sync = sync_completions

    async def create(self, **kwargs: Any) -> Any:
        import inspect

        result = await asyncio.to_thread(self._sync.create, **kwargs)
        if inspect.isawaitable(result):  # a plugin client that is natively async
            result = await result
        if kwargs.get("stream") and not hasattr(result, "choices") and not hasattr(result, "__aiter__"):
            return _aiter_off_loop(result)
        return result


async def _aiter_off_loop(iterable: Any):
    """A sync chunk stream as an async one; each ``next`` may block on a child process."""
    iterator, done = iter(iterable), object()
    while (item := await asyncio.to_thread(next, iterator, done)) is not done:
        yield item


class AsyncProcessClient:
    """Async face of a client that declares ``HERMES_SKIP_ASYNC_WRAP`` (subscription CLIs, ACP shims).

    Such a client is a subprocess driver, not an HTTP pool, so it must not be rebuilt as
    ``AsyncOpenAI`` from its ``cli://``/``acp://`` marker URL; but async callers ``await``
    ``chat.completions.create``, so its blocking ``create`` runs in a worker thread. Every other
    attribute (``base_url``, ``close``, capability flags) is the wrapped client's.
    """

    def __init__(self, sync_client: Any):
        self._sync_client = sync_client

    @property
    def chat(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(completions=_AsyncProcessCompletions(self._sync_client.chat.completions))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sync_client, name)

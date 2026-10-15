"""Keep one local-model route attached to an API operation and its worker.

Flask requests and background tasks can overlap, so changing process-wide
environment variables to select a run would send calls to the wrong budget.
The gateway itself remains live between preparation, OASIS, and reporting;
this context only selects its authenticated endpoint for in-process clients.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Mapping


_active_settings: ContextVar[dict[str, str] | None] = ContextVar(
    "mirofish_local_llm_settings", default=None
)


def active_llm_settings() -> dict[str, str] | None:
    """Return settings for the current task, if a gateway is active."""
    value = _active_settings.get()
    return dict(value) if value is not None else None


@contextmanager
def use_llm_settings(settings: Mapping[str, str]) -> Iterator[None]:
    """Route clients created inside this scope through one run gateway."""
    token = _active_settings.set(dict(settings))
    try:
        yield
    finally:
        _active_settings.reset(token)

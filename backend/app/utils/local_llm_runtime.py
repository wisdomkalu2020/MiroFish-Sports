"""Own budgeted local-model gateways for MiroFish project and simulation runs.

One simulation ID keeps the same SQLite budget through persona generation,
OASIS subprocess turns, ReportAgent, and report chat. Gateways bind only to
loopback and are recreated against the persisted budget after an app restart.
"""

from __future__ import annotations

import atexit
import os
import re
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator

from ..config import Config
from .local_llm_gateway import GatewayHandle, start_gateway
from .local_llm_context import use_llm_settings
from .run_budget import RunBudget


_LOCK = threading.RLock()
_HANDLES: dict[tuple[str, str, bool], GatewayHandle] = {}
_RUN_ID = re.compile(r"^(?:proj|sim)[_-][A-Za-z0-9_-]{1,100}$")


def _positive_int(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _budget_for(run_id: str) -> RunBudget:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid project or simulation ID for local LLM run")
    cost_limit = os.environ.get("MIROFISH_RUN_COST_LIMIT_USD", "0")
    return RunBudget(
        Path(Config.UPLOAD_FOLDER) / "llm_run_budgets.sqlite3",
        run_id,
        token_limit=_positive_int("MIROFISH_RUN_TOKEN_LIMIT", "50000"),
        cost_limit_usd=cost_limit,
        input_cost_per_1k_usd=0,
        output_cost_per_1k_usd=0,
    )


def local_run_budget_snapshot(run_id: str) -> dict[str, Any]:
    """Read the shared token ledger for one project or simulation."""

    return _budget_for(run_id).snapshot()


def get_local_gateway(run_id: str, *, report: bool = False) -> GatewayHandle:
    """Return a live gateway; fail before an operation if its ledger is spent."""
    if Config.LLM_PROVIDER != "local":
        raise ValueError("local gateway requires LLM_PROVIDER=local")
    model = Config.LLM_REPORT_MODEL_NAME if report else Config.LLM_MODEL_NAME
    if not model:
        raise ValueError("LLM_MODEL_NAME is required for local model runs")
    # Keep reporting separate from OASIS even when both use the same model.
    # Releasing the OASIS listener must not close a report client's endpoint.
    key = (run_id, model, report)
    with _LOCK:
        handle = _HANDLES.get(key)
        if handle is None:
            budget = _budget_for(run_id)
            budget.ensure_not_exhausted()
            handle = start_gateway(
                budget=budget,
                model=model,
                ollama_base_url=os.environ.get(
                    "MIROFISH_LOCAL_MODEL_BASE_URL",
                    os.environ.get(
                        "MIROFISH_OLLAMA_BASE_URL", "http://127.0.0.1:8080/v1"
                    ),
                ),
                max_completion_tokens=_positive_int("LLM_MAX_OUTPUT_TOKENS", "512"),
                request_timeout_seconds=_positive_int(
                    "MIROFISH_LOCAL_REQUEST_TIMEOUT_SECONDS", "480"
                ),
            )
            _HANDLES[key] = handle
        else:
            handle.budget.ensure_not_exhausted()
        return handle


def release_local_gateway(
    run_id: str, *, report: bool = False, expected: GatewayHandle | None = None
) -> bool:
    """Close a completed run's listener while retaining its SQLite budget.

    ``expected`` prevents a late monitor from closing a newer listener for the
    same run after another operation has reopened it.
    """
    model = Config.LLM_REPORT_MODEL_NAME if report else Config.LLM_MODEL_NAME
    key = (run_id, model, report)
    with _LOCK:
        handle = _HANDLES.get(key)
        if handle is None or (expected is not None and handle is not expected):
            return False
        _HANDLES.pop(key)
    handle.close()
    return True


@contextmanager
def local_model_run(run_id: str, *, report: bool = False) -> Iterator[GatewayHandle]:
    """Select a run's gateway for clients created in the current thread."""
    handle = get_local_gateway(run_id, report=report)
    with use_llm_settings(
        {"api_key": handle.api_key, "base_url": handle.base_url, "model": handle.model}
    ):
        yield handle


def budgeted_simulation_prepare(method: Callable[..., Any]) -> Callable[..., Any]:
    """Bind a simulation's budget to direct and API preparation calls."""
    @wraps(method)
    def wrapped(self: Any, simulation_id: str, *args: Any, **kwargs: Any) -> Any:
        if Config.LLM_PROVIDER == "local":
            with local_model_run(simulation_id):
                return method(self, simulation_id, *args, **kwargs)
        return method(self, simulation_id, *args, **kwargs)

    return wrapped


def close_local_gateways() -> None:
    """Stop all in-process gateway listeners during app shutdown."""
    with _LOCK:
        handles = list(_HANDLES.values())
        _HANDLES.clear()
    for handle in handles:
        handle.close()


atexit.register(close_local_gateways)

"""Authenticated loopback gateway for one budgeted local-model run.

The existing OpenAI-compatible clients and OASIS subprocesses can share this
endpoint.  Every request receives a mandatory completion cap, is reserved in
the SQLite ledger before contacting Ollama, and is reconciled from usage.
"""

from __future__ import annotations

import hmac
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .run_budget import BudgetExceeded, RunBudget


_MAX_REQUEST_BYTES = 2 * 1024 * 1024
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _loopback_openai_base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") not in ("", "/v1")
    ):
        raise ValueError("Ollama URL must be an HTTP /v1 endpoint on 127.0.0.1")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Ollama URL has an invalid port") from exc
    if port is None or port < 1:
        raise ValueError("Ollama URL must include a valid nonzero port")
    return f"http://127.0.0.1:{port}/v1"


def _output_cap(payload: dict[str, Any], configured_cap: int) -> int:
    requested: list[int] = []
    for name in ("max_tokens", "max_completion_tokens"):
        value = payload.get(name)
        if value is not None:
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
            requested.append(value)
    return min(configured_cap, *requested) if requested else configured_cap


def _prompt_token_upper_estimate(payload: dict[str, Any]) -> int:
    # Byte length conservatively bounds byte-pair text tokens; add headroom for
    # role delimiters and the provider's hidden chat-template tokens.
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    messages = payload.get("messages", [])
    message_count = len(messages) if isinstance(messages, list) else 0
    return len(encoded) + 64 + 16 * message_count


def _usage(payload: dict[str, Any]) -> tuple[int, int] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if (
        not isinstance(prompt, int)
        or isinstance(prompt, bool)
        or prompt < 0
        or not isinstance(completion, int)
        or isinstance(completion, bool)
        or completion < 0
    ):
        return None
    return prompt, completion


@dataclass
class GatewayHandle:
    """Live gateway and credentials for one model/budget combination."""

    server: ThreadingHTTPServer
    thread: threading.Thread
    api_key: str
    budget: RunBudget
    model: str

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def client_env(self) -> dict[str, str]:
        """Environment to pass to both MiroFish and its OASIS children."""
        return {
            "LLM_BASE_URL": self.base_url,
            "LLM_API_KEY": self.api_key,
            "LLM_MODEL_NAME": self.model,
            "MIROFISH_LOCAL_GATEWAY_TOKEN": self.api_key,
            "OPENAI_API_BASE_URL": self.base_url,
            "OPENAI_API_KEY": self.api_key,
        }

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def start_gateway(
    budget: RunBudget,
    model: str,
    ollama_base_url: str = "http://127.0.0.1:11434/v1",
    max_completion_tokens: int = 512,
    request_timeout_seconds: int = 480,
    api_key: str | None = None,
    port: int = 0,
) -> GatewayHandle:
    """Start a single-run OpenAI-compatible proxy on 127.0.0.1.

    The returned random bearer token must be provided as the clients' API key.
    ``port=0`` asks the OS to choose an available ephemeral port.  Call
    ``GatewayHandle.close()`` after all child processes have exited.
    """
    if not isinstance(budget, RunBudget):
        raise TypeError("budget must be a RunBudget")
    # A restarted gateway cannot assume an old pending model call was free.
    budget.ensure_settled()
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be configured")
    if (
        not isinstance(max_completion_tokens, int)
        or isinstance(max_completion_tokens, bool)
        or max_completion_tokens <= 0
    ):
        raise ValueError("max_completion_tokens must be a positive integer")
    if (
        not isinstance(request_timeout_seconds, int)
        or isinstance(request_timeout_seconds, bool)
        or not 1 <= request_timeout_seconds <= 900
    ):
        raise ValueError("request_timeout_seconds must be in 1..900")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer in 0..65535")
    upstream = _loopback_openai_base_url(ollama_base_url) + "/chat/completions"
    bearer = api_key or secrets.token_urlsafe(48)
    if len(bearer) < 24:
        raise ValueError("gateway bearer token must be at least 24 characters")
    halted = threading.Event()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            return None

    # Environment proxies and provider redirects must not carry prompts away
    # from the selected local Ollama listener.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *args: Any) -> None:
            # Avoid logging prompts, credentials, or upstream error bodies.
            return

        def _send_json(self, status: int, body: dict[str, Any], budget_exceeded: bool = False) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            if budget_exceeded:
                self.send_header("X-MiroFish-Budget-Exceeded", "1")
            self.end_headers()
            self.wfile.write(encoded)

        def _error(self, status: int, code: str, message: str, budget_exceeded: bool = False) -> None:
            self._send_json(
                status,
                {"error": {"message": message, "type": code, "code": code}},
                budget_exceeded,
            )

        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self._error(404, "not_found", "unsupported local gateway route")
                return
            authorization = self.headers.get("Authorization", "")
            if not hmac.compare_digest(authorization, f"Bearer {bearer}"):
                self._error(401, "unauthorized", "invalid local gateway credential")
                return
            if halted.is_set():
                self._error(503, "budget_accounting_error", "run budget accounting is unavailable")
                return
            try:
                content_length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                content_length = -1
            if not 0 < content_length <= _MAX_REQUEST_BYTES:
                self._error(413, "request_too_large", "request body length is invalid or too large")
                return
            try:
                payload = json.loads(self.rfile.read(content_length))
            except (ValueError, UnicodeError):
                self._error(400, "invalid_json", "request must contain JSON")
                return
            if not isinstance(payload, dict):
                self._error(400, "invalid_request", "request must be a JSON object")
                return
            if "stream" in payload and payload["stream"] is not False and payload["stream"] is not None:
                self._error(400, "streaming_disabled", "streaming is disabled by the run budget")
                return
            if payload.get("model") != model:
                self._error(400, "model_mismatch", "request model differs from the configured local model")
                return
            if not isinstance(payload.get("messages"), list):
                self._error(400, "invalid_request", "messages must be an array")
                return
            try:
                cap = _output_cap(payload, max_completion_tokens)
            except ValueError as exc:
                self._error(400, "invalid_request", str(exc))
                return
            payload.pop("max_completion_tokens", None)
            payload["max_tokens"] = cap
            payload["stream"] = False
            estimate = _prompt_token_upper_estimate(payload)
            try:
                reservation_id = budget.reserve(estimate, cap)
            except BudgetExceeded as exc:
                self._error(429, "budget_exceeded", str(exc), budget_exceeded=True)
                return
            except Exception:
                halted.set()
                self._error(503, "budget_accounting_error", "could not reserve model token usage")
                return
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            request = urllib.request.Request(
                upstream,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with opener.open(request, timeout=request_timeout_seconds) as response:
                    upstream_body = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(upstream_body) > _MAX_RESPONSE_BYTES:
                        raise ValueError("local model response is too large")
                    reply = json.loads(upstream_body)
                    if not isinstance(reply, dict):
                        raise ValueError("local model response is not a JSON object")
                    usage = _usage(reply)
                    if usage is None:
                        raise ValueError("local model did not report token usage")
            except (OSError, TimeoutError, ValueError, UnicodeError) as exc:
                try:
                    budget.mark_uncertain(reservation_id)
                except Exception:
                    halted.set()
                    self._error(502, "budget_accounting_error", "could not account for the failed model request")
                    return
                self._error(502, "local_model_error", f"local model request failed: {exc}")
                return
            try:
                budget.reconcile(reservation_id, usage[0], usage[1])
            except Exception:
                # A ledger failure means we cannot prove the run is under its
                # limit; do not present the model response as successful.
                halted.set()
                try:
                    budget.mark_uncertain(reservation_id)
                except Exception:
                    pass
                self._error(502, "budget_accounting_error", "could not reconcile model token usage")
                return
            if usage[0] > estimate or usage[1] > cap:
                self._error(502, "budget_accounting_error", "local model exceeded reserved tokens")
                return
            self._send_json(200, reply)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name=f"mirofish-gateway-{budget.run_id}", daemon=True)
    thread.start()
    return GatewayHandle(server=server, thread=thread, api_key=bearer, budget=budget, model=model)

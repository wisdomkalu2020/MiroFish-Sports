"""Focused standard-library tests for the local-model budget boundary.

The modules are loaded without importing Flask or Zep so this safety layer can
be checked before the full MiroFish environment is installed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


UTILS_DIR = Path(__file__).resolve().parents[1] / "app" / "utils"
TEST_TMPDIR = os.environ.get("MIROFISH_TEST_TMPDIR")


class _WorkDirectory:
    """Avoid Windows tempfile ACLs that can block SQLite in sandboxed runs."""

    def __init__(self):
        root = Path(TEST_TMPDIR) if TEST_TMPDIR else Path.cwd()
        self.root = root.resolve()
        self.path = self.root / f"mirofish-budget-test-{uuid.uuid4().hex}"
        self.path.mkdir()
        self.name = str(self.path)

    def cleanup(self):
        if self.path.resolve().parent != self.root:
            raise RuntimeError("test directory moved outside its parent")
        shutil.rmtree(self.path)

    def __enter__(self):
        return self.name

    def __exit__(self, _exc_type, _exc, _traceback):
        self.cleanup()
PACKAGE_NAME = "_mirofish_budget_test_utils"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(UTILS_DIR)]
sys.modules.setdefault(PACKAGE_NAME, package)


def _load(name: str):
    qualified = f"{PACKAGE_NAME}.{name}"
    spec = importlib.util.spec_from_file_location(qualified, UTILS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


budget_module = _load("run_budget")
gateway_module = _load("local_llm_gateway")
RunBudget = budget_module.RunBudget
BudgetExceeded = budget_module.BudgetExceeded
start_gateway = gateway_module.start_gateway


class RunBudgetTests(unittest.TestCase):
    def test_concurrent_reservations_cannot_exceed_one_run_limit(self):
        with _WorkDirectory() as directory:
            db_path = Path(directory) / "budget.sqlite3"
            budget = RunBudget(db_path, "run-1", token_limit=1000)

            def reserve(_index):
                worker_budget = RunBudget(db_path, "run-1", token_limit=1000)
                try:
                    return worker_budget.reserve(100, 25)
                except BudgetExceeded:
                    return None

            with ThreadPoolExecutor(max_workers=16) as pool:
                reservations = list(pool.map(reserve, range(30)))

            accepted = [reservation for reservation in reservations if reservation]
            self.assertLessEqual(len(accepted), 8)
            self.assertEqual(budget.snapshot()["reserved_tokens"], len(accepted) * 125)
            self.assertEqual(budget.snapshot()["exhausted"], True)
            for reservation in accepted:
                budget.reconcile(reservation, 60, 10)
            self.assertEqual(budget.snapshot()["pending_requests"], 0)
            self.assertEqual(budget.snapshot()["spent_tokens"], len(accepted) * 70)
            with self.assertRaises(BudgetExceeded):
                budget.reserve(1, 1)

    def test_unknown_usage_is_charged_and_blocks_run(self):
        with _WorkDirectory() as directory:
            budget = RunBudget(Path(directory) / "budget.sqlite3", "run-2", token_limit=1000)
            reservation = budget.reserve(100, 20)
            budget.mark_uncertain(reservation)
            state = budget.snapshot()
            self.assertEqual(state["spent_tokens"], 120)
            self.assertEqual(state["reserved_tokens"], 0)
            self.assertTrue(state["exhausted"])
            with self.assertRaises(BudgetExceeded):
                budget.ensure_not_exhausted()

    def test_cost_limit_and_immutable_run_limits(self):
        with _WorkDirectory() as directory:
            path = Path(directory) / "budget.sqlite3"
            budget = RunBudget(
                path,
                "run-3",
                token_limit=10000,
                cost_limit_usd="0.001",
                input_cost_per_1k_usd="0.01",
            )
            with self.assertRaises(BudgetExceeded):
                budget.reserve(200, 10)
            self.assertTrue(budget.snapshot()["exhausted"])
            with self.assertRaises(ValueError):
                RunBudget(path, "run-3", token_limit=20000, cost_limit_usd="0.001")

    def test_provider_overrun_exhausts_run(self):
        with _WorkDirectory() as directory:
            budget = RunBudget(Path(directory) / "budget.sqlite3", "run-4", token_limit=1000)
            reservation = budget.reserve(100, 10)
            budget.reconcile(reservation, 40, 11)
            self.assertTrue(budget.snapshot()["exhausted"])


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *args):
        return

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length))
        self.server.received.append(payload)
        reply = {
            "id": "fake-local-response",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}}],
        }
        if not self.server.omit_usage:
            reply["usage"] = {"prompt_tokens": 20, "completion_tokens": 2, "total_tokens": 22}
        body = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.directory = _WorkDirectory()
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
        self.upstream.received = []
        self.upstream.omit_usage = False
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        self.budget = RunBudget(Path(self.directory.name) / "budget.sqlite3", "gateway-run", 10000)
        self.gateway = start_gateway(
            self.budget,
            model="local-test-model",
            ollama_base_url=f"http://127.0.0.1:{self.upstream.server_address[1]}/v1",
            max_completion_tokens=9,
        )

    def tearDown(self):
        self.gateway.close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=5)
        self.directory.cleanup()

    def request(self, payload, token=None):
        if token is None:
            token = self.gateway.api_key
        request = urllib.request.Request(
            self.gateway.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read()), response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read()), exc.headers

    def test_authenticated_request_is_capped_and_accounted(self):
        status, reply, _headers = self.request(
            {"model": "local-test-model", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 40}
        )
        self.assertEqual(status, 200)
        self.assertEqual(reply["choices"][0]["message"]["content"], "OK")
        self.assertEqual(self.upstream.received[0]["max_tokens"], 9)
        self.assertFalse(self.upstream.received[0]["stream"])
        self.assertEqual(self.budget.snapshot()["spent_tokens"], 22)
        self.assertEqual(self.gateway.client_env()["MIROFISH_LOCAL_GATEWAY_TOKEN"], self.gateway.api_key)

    def test_unauthorized_and_streaming_requests_never_reach_model(self):
        payload = {"model": "local-test-model", "messages": []}
        self.assertEqual(self.request(payload, token="wrong-token")[0], 401)
        self.assertEqual(self.request({**payload, "stream": True})[0], 400)
        self.assertEqual(len(self.upstream.received), 0)

    def test_budget_exceeded_is_openai_shaped_and_does_not_reach_model(self):
        self.gateway.close()
        self.budget = RunBudget(Path(self.directory.name) / "tiny.sqlite3", "tiny-run", 100)
        self.gateway = start_gateway(
            self.budget,
            model="local-test-model",
            ollama_base_url=f"http://127.0.0.1:{self.upstream.server_address[1]}/v1",
            max_completion_tokens=9,
        )
        status, reply, headers = self.request(
            {"model": "local-test-model", "messages": [{"role": "user", "content": "Hi"}]}
        )
        self.assertEqual(status, 429)
        self.assertEqual(reply["error"]["code"], "budget_exceeded")
        self.assertEqual(headers["X-MiroFish-Budget-Exceeded"], "1")
        self.assertEqual(len(self.upstream.received), 0)
        self.assertTrue(self.budget.snapshot()["exhausted"])

    def test_missing_provider_usage_fails_closed(self):
        self.upstream.omit_usage = True
        status, reply, _headers = self.request(
            {"model": "local-test-model", "messages": [{"role": "user", "content": "Hi"}]}
        )
        self.assertEqual(status, 502)
        self.assertEqual(reply["error"]["code"], "local_model_error")
        self.assertTrue(self.budget.snapshot()["exhausted"])
        self.assertEqual(self.request({"model": "local-test-model", "messages": []})[0], 429)

    def test_rejects_non_loopback_upstream(self):
        with self.assertRaises(ValueError):
            start_gateway(self.budget, "local-test-model", ollama_base_url="https://example.com/v1")

    def test_rejects_unbounded_local_request_timeout(self):
        with self.assertRaisesRegex(ValueError, "request_timeout_seconds"):
            start_gateway(
                self.budget,
                "local-test-model",
                request_timeout_seconds=901,
            )


if __name__ == "__main__":
    unittest.main()

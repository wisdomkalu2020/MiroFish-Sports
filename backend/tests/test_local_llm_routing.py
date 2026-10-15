from types import SimpleNamespace

import pytest

from app.config import Config
from app.services.oasis_profile_generator import OasisProfileGenerator
from app.services.report_agent import ReportAgent
from app.services.simulation_config_generator import SimulationConfigGenerator
from app.services.zep_tools import ZepToolsService
from app.utils.llm_client import LLMClient
from app.utils.local_llm_context import use_llm_settings
from app.utils import local_llm_runtime


def _local_config(monkeypatch):
    monkeypatch.setattr(Config, 'LLM_PROVIDER', 'local')
    monkeypatch.setattr(Config, 'LLM_BASE_URL', '')
    monkeypatch.setattr(Config, 'LLM_MODEL_NAME', 'test-local-model')
    monkeypatch.setattr(Config, 'LLM_MAX_OUTPUT_TOKENS', 64)
    monkeypatch.delenv('MIROFISH_LOCAL_GATEWAY_TOKEN', raising=False)


def _gateway_settings():
    return {
        'api_key': 'test-gateway-token',
        'base_url': 'http://127.0.0.1:43210/v1',
        'model': 'test-local-model',
    }


def test_local_client_requires_a_budget_gateway(monkeypatch):
    _local_config(monkeypatch)
    monkeypatch.setenv('OPENAI_API_KEY', 'sentinel-remote-secret')

    with pytest.raises(ValueError, match='LLM_BASE_URL is required'):
        LLMClient()
    with pytest.raises(ValueError, match='LLM_BASE_URL is required'):
        LLMClient(base_url='http://127.0.0.1:11434/v1')


def test_local_client_uses_gateway_token_not_openai_key(monkeypatch):
    _local_config(monkeypatch)
    monkeypatch.setenv('OPENAI_API_KEY', 'sentinel-remote-secret')
    captured = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr('app.utils.llm_client.OpenAI', fake_openai)
    with use_llm_settings(_gateway_settings()):
        client = LLMClient()

    assert client.model == 'test-local-model'
    assert captured == {
        'api_key': 'test-gateway-token',
        'base_url': 'http://127.0.0.1:43210/v1',
        'max_retries': 0,
    }


def test_local_client_rejects_remote_endpoint_and_wrong_key(monkeypatch):
    _local_config(monkeypatch)
    with use_llm_settings(_gateway_settings()):
        with pytest.raises(ValueError, match='run gateway'):
            Config.resolve_llm_settings(base_url='https://api.openai.com/v1')
        with pytest.raises(ValueError, match='run gateway'):
            Config.resolve_llm_settings(base_url='http://127.0.0.1:8080/v1')
        with pytest.raises(ValueError, match='run gateway'):
            Config.resolve_llm_settings(model='another-local-model')
        with pytest.raises(ValueError, match='gateway token'):
            Config.resolve_llm_settings(api_key='sentinel-remote-secret')


def test_report_agent_uses_report_gateway_and_shares_llm_with_zep_tools(monkeypatch):
    _local_config(monkeypatch)
    gateway = SimpleNamespace(
        api_key='report-gateway-token',
        base_url='http://127.0.0.1:43211/v1',
        model='test-report-model',
    )
    requested = []
    captured = {}
    monkeypatch.setattr(
        'app.utils.local_llm_runtime.get_local_gateway',
        lambda run_id, report=False: requested.append((run_id, report)) or gateway,
    )

    def fake_llm_client(**kwargs):
        captured['llm'] = kwargs
        return object()

    def fake_zep_tools(**kwargs):
        captured['zep_tools'] = kwargs
        return object()

    monkeypatch.setattr('app.services.report_agent.LLMClient', fake_llm_client)
    monkeypatch.setattr('app.services.report_agent.ZepToolsService', fake_zep_tools)

    agent = ReportAgent('graph-id', 'sim_test', 'scenario')

    assert requested == [('sim_test', True)]
    assert captured['llm'] == {
        'api_key': gateway.api_key,
        'base_url': gateway.base_url,
        'model': gateway.model,
    }
    assert captured['zep_tools']['llm_client'] is agent.llm


class BudgetError(RuntimeError):
    status_code = 429
    body = {'error': {'code': 'budget_exceeded'}}


class RejectingCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise BudgetError('run budget exhausted')


def test_config_generator_does_not_retry_or_fallback_after_budget_hit(monkeypatch):
    _local_config(monkeypatch)
    generator = object.__new__(SimulationConfigGenerator)
    generator.model_name = 'test-local-model'
    completions = RejectingCompletions()
    generator.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    with pytest.raises(BudgetError):
        generator._call_llm_with_retry('prompt', 'system')

    assert len(completions.calls) == 1
    assert completions.calls[0]['max_tokens'] == 64


def test_profile_generator_does_not_fallback_after_budget_hit(monkeypatch):
    _local_config(monkeypatch)
    generator = object.__new__(OasisProfileGenerator)
    generator.model_name = 'test-local-model'
    completions = RejectingCompletions()
    generator.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(generator, '_build_group_persona_prompt', lambda *args: 'prompt')
    monkeypatch.setattr(generator, '_get_system_prompt', lambda *args: 'system')

    with pytest.raises(BudgetError):
        generator._generate_profile_with_llm('team', 'organization', '', {}, '')

    assert len(completions.calls) == 1
    assert completions.calls[0]['max_tokens'] == 64


def test_zep_report_tool_does_not_fallback_after_budget_hit():
    class RejectingLLM:
        def chat_json(self, **kwargs):
            raise BudgetError('run budget exhausted')

    tools = object.__new__(ZepToolsService)
    tools._llm_client = RejectingLLM()

    with pytest.raises(BudgetError):
        tools._generate_sub_queries('question', 'scenario', '', 3)


def test_released_oasis_gateway_reopens_with_persisted_budget(monkeypatch, tmp_path):
    _local_config(monkeypatch)
    monkeypatch.setattr(Config, 'LLM_REPORT_MODEL_NAME', 'test-local-model')
    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(tmp_path))
    monkeypatch.setenv('MIROFISH_RUN_TOKEN_LIMIT', '1000')
    monkeypatch.setenv('MIROFISH_RUN_COST_LIMIT_USD', '0')
    created = []

    class FakeHandle:
        def __init__(self, budget, model):
            self.budget = budget
            self.model = model
            self.closed = False

        def close(self):
            self.closed = True

    def fake_start_gateway(*, budget, model, **_kwargs):
        budget.ensure_settled()
        handle = FakeHandle(budget, model)
        created.append(handle)
        return handle

    monkeypatch.setattr(local_llm_runtime, 'start_gateway', fake_start_gateway)
    run_id = 'sim_gateway_release_test'
    try:
        oasis = local_llm_runtime.get_local_gateway(run_id)
        reservation = oasis.budget.reserve(100, 10)
        oasis.budget.reconcile(reservation, 20, 2)

        # ReportAgent has a separate listener, but shares the same ledger.
        report = local_llm_runtime.get_local_gateway(run_id, report=True)
        assert report is not oasis
        assert report.budget.snapshot()['spent_tokens'] == 22

        assert local_llm_runtime.release_local_gateway(run_id, expected=oasis)
        assert oasis.closed
        assert not report.closed
        assert not local_llm_runtime.release_local_gateway(run_id, expected=oasis)

        reopened = local_llm_runtime.get_local_gateway(run_id)
        assert reopened is not oasis
        assert reopened.budget.snapshot()['spent_tokens'] == 22
        assert not local_llm_runtime.release_local_gateway(run_id, expected=oasis)
        assert not reopened.closed
    finally:
        local_llm_runtime.release_local_gateway(run_id)
        local_llm_runtime.release_local_gateway(run_id, report=True)

    assert all(handle.closed for handle in created)

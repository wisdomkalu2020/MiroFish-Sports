from types import SimpleNamespace
import pytest
from app.services.report_agent import ReportAgent

@pytest.mark.parametrize('retry_id', [None, 'sim_report_retry_test'])
def test_report_retry_routes_all_research_to_explicit_ledger(monkeypatch, retry_id):
    import app.services.report_agent as module
    import app.utils.local_llm_runtime as runtime
    monkeypatch.setattr(module.Config, 'LLM_PROVIDER', 'local')
    calls=[]
    gateway=SimpleNamespace(api_key='local-test',base_url='http://127.0.0.1:9999/v1',model='test')
    monkeypatch.setattr(runtime, 'get_local_gateway', lambda run_id, **kw: calls.append((run_id,kw)) or gateway)
    client=object()
    monkeypatch.setattr(module, 'LLMClient', lambda **kw: client)
    monkeypatch.setattr(module, 'ZepToolsService', lambda **kw: SimpleNamespace(llm=kw['llm_client']))
    agent=ReportAgent('graph_test','sim_source','Test',llm_run_id=retry_id)
    assert agent.simulation_id=='sim_source'
    assert calls==[(retry_id or 'sim_source', {'report':True})]
    assert agent.llm is client and agent.zep_tools.llm is client

@pytest.mark.parametrize('sports', [False, True])
def test_sports_outline_uses_existing_graph_and_constrained_plan(sports):
    agent=object.__new__(ReportAgent)
    agent.sports_forecast={} if sports else None
    agent.graph_id='graph_test'; agent.simulation_requirement='Test'
    observed=[]
    def context(**kwargs):
        observed.append(kwargs)
        return {'graph_statistics':{},'related_facts':[]}
    def chat_json(**kwargs):
        observed.append(kwargs)
        return {'title':'Test','summary':'Synthetic','sections':[{'title':'Evidence'},{'title':'Limits'}]}
    agent.zep_tools=SimpleNamespace(get_simulation_context=context)
    agent.llm=SimpleNamespace(chat_json=chat_json)
    result=agent.plan_outline()
    assert observed[0]['graph_id']=='graph_test'
    assert len(result.sections)==2
    assert ('json_schema' in observed[1]) is sports
    if sports:
        assert observed[1]['json_schema']['properties']['sections']['maxItems']==2

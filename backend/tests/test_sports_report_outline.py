from types import SimpleNamespace
import pytest
from app.services.report_agent import ReportAgent

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

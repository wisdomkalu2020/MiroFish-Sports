import json
import pytest
from app.services.simulation_ipc import SimulationIPCClient
from app.services.simulation_runner import SimulationRunner
from app.services.zep_tools import ZepToolsService

@pytest.mark.parametrize('status,expected', [('completed',False),('stopped',False),('failed',False),('running',True)])
def test_terminal_run_overrides_stale_alive_file(tmp_path,status,expected):
    (tmp_path/'env_status.json').write_text(json.dumps({'status':'alive'}))
    (tmp_path/'run_state.json').write_text(json.dumps({'runner_status':status}))
    assert SimulationIPCClient(str(tmp_path)).check_env_alive() is expected

def test_closed_interviews_do_not_load_profiles_or_call_model(monkeypatch):
    monkeypatch.setattr(SimulationRunner,'check_env_alive',lambda _:False)
    service=object.__new__(ZepToolsService)
    def unexpected(*args,**kwargs): raise AssertionError('Must not spend tokens for a closed environment')
    monkeypatch.setattr(service,'_load_agent_profiles',unexpected)
    result=service.interview_agents('sim_closed','Question')
    assert 'No interview answers were generated' in result.summary

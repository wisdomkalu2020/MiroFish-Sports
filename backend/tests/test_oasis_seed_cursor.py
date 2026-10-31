import ast
import asyncio
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Any, Optional

import pytest

@pytest.mark.parametrize("provider,expected_concurrency", [("local",1),("other",30)])
def test_seed_not_replayed_as_discussion(tmp_path,monkeypatch,provider,expected_concurrency):
    monkeypatch.setenv("LLM_PROVIDER",provider)
    script=Path(__file__).resolve().parents[1]/"scripts/run_parallel_simulation.py"
    tree=ast.parse(script.read_text(encoding="utf-8"))
    fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=="run_reddit_simulation")
    rows=[]; logged=[]; concurrency=[]
    agent=object()
    class Env:
        agent_graph=SimpleNamespace(get_agent=lambda _id:agent,get_agents=lambda:[(0,agent)])
        async def reset(self): pass
        async def step(self,actions):
            rows.append({"agent_id":0,"agent_name":"Synthetic", "action_type":"CREATE_POST",
                         "action_args":{"content":"seed" if not rows else "genuine"}})
    env=Env()
    async def graph(**kwargs): return env.agent_graph
    def make(**kwargs):
        concurrency.append(kwargs["semaphore"])
        return env
    class Logger:
        def log_simulation_start(self,*args): pass
        def log_round_start(self,*args): pass
        def log_round_end(self,*args): pass
        def log_simulation_end(self,*args): pass
        def log_action(self,**kwargs): logged.append(kwargs)
    (tmp_path/"reddit_profiles.json").write_text('[{"user_id":0,"name":"Synthetic"}]')
    scope={"os":os,"json":__import__("json"),"datetime":datetime,"Dict":Dict,"Any":Any,
           "Optional":Optional,"PlatformActionLogger":object,"SimulationLogManager":object,
           "get_agent_names_from_config":lambda config:{0:"Synthetic"},"REDDIT_ACTIONS":[],"PlatformSimulation":SimpleNamespace,"create_model":lambda _:object(),
           "generate_reddit_agent_graph":graph,
           "oasis":SimpleNamespace(make=make,DefaultPlatformType=SimpleNamespace(REDDIT="reddit")),
           "ManualAction":lambda **kw:kw,"LLMAction":lambda:object(),
           "ActionType":SimpleNamespace(CREATE_POST="CREATE_POST"),"_shutdown_event":None,
           "get_active_agents_for_round":lambda *args:[(0,agent)],
           "fetch_new_actions_from_db":lambda path,cursor,names:(rows[cursor:],len(rows))}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(script),"exec"),scope)
    config={"time_config":{"total_simulation_hours":1,"minutes_per_round":60},
            "event_config":{"initial_posts":[{"poster_agent_id":0,"content":"seed"}]}}
    asyncio.run(scope["run_reddit_simulation"](config,str(tmp_path),Logger(),None,1))
    assert concurrency==[expected_concurrency]
    assert [(x["round_num"],x["action_args"]["content"]) for x in logged]==[(0,"seed"),(1,"genuine")]

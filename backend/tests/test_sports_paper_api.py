from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from flask import Flask
from app.api import sports_paper as api
from app.sports.paper_ledger import PaperLedger

@pytest.fixture
def setup(tmp_path, monkeypatch):
    ledger=PaperLedger(tmp_path/'ledger.sqlite')
    monkeypatch.setattr(api, '_ledger', lambda:ledger)
    context={'project_id':'proj_test','match_id':'fixture1','sport':'football','league':'EPL',
             'home_team':'North','away_team':'South','dossier_sha256':'testhash',
             'baseline_probabilities':{'home':0.45,'draw':0.25,'away':0.30}}
    monkeypatch.setattr(api,'load_forecast_context',lambda _:context)
    app=Flask(__name__);app.register_blueprint(api.sports_paper_bp,url_prefix='/paper')
    client=app.test_client()
    quote={'match_id':'fixture1','sport':'football','league':'EPL','home_team':'North',
           'away_team':'South','bookmaker':'Test','source':'manual test',
           'kickoff_at':(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),
           'decimal_odds':{'home':2.0,'draw':3.5,'away':4.0},'label':'opening'}
    return client,ledger,quote,context

def test_quote_prediction_and_kickoff_correction_are_append_only(setup):
    client,ledger,quote,context=setup
    response=client.post('/paper/quotes',json=quote)
    assert response.status_code==200
    saved=response.json['data']
    assert sum(saved['market_implied_probabilities'].values())==pytest.approx(1)
    prediction=client.post('/paper/predictions',json={'quote_id':saved['quote_id'],
        'project_id':'proj_test','pick':'home','stake_ngn':100})
    assert prediction.status_code==200
    original=prediction.json['data']
    assert original['probabilities']==context['baseline_probabilities']
    assert original['hypothetical'] and original['status']=='pending'
    correction=client.post('/paper/kickoff',json={'match_id':'fixture1',
        'kickoff_at':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),'source':'verified correction'})
    assert correction.status_code==200
    later=client.get('/paper').json['data']['predictions'][0]
    assert later['status']=='void_corrected_kickoff'
    assert later['recorded_at']==original['recorded_at']
    assert later['probabilities']==original['probabilities']

@pytest.mark.parametrize('key,value', [('recorded_at','2020-01-01T00:00:00Z'),('probabilities',{'home':1}),('api_key','no')])
def test_client_cannot_supply_backdated_time_or_model_probabilities(setup,key,value):
    client,ledger,quote,_=setup
    assert client.post('/paper/quotes',json={**quote,key:value}).status_code==400
    assert ledger.list_quotes()==()

def test_rejects_fixture_mismatch_and_invalid_stakes(setup):
    client,ledger,quote,context=setup
    saved=client.post('/paper/quotes',json=quote).json['data']
    payload={'quote_id':saved['quote_id'],'project_id':'proj_test','pick':'home','stake_ngn':500}
    assert client.post('/paper/predictions',json=payload).status_code==400
    context['home_team']='Different team';payload['stake_ngn']=100
    assert client.post('/paper/predictions',json=payload).status_code==400
    assert ledger.list_predictions()==()

def test_provider_uses_adapter_and_server_credentials(setup,monkeypatch):
    client,ledger,quote,_=setup
    seen=[]
    def provider(**kwargs):
        seen.append(kwargs)
        return (api.manual_quote(**{**quote,'kickoff_at':api._time(quote['kickoff_at'])}),)
    monkeypatch.setattr(api,'fetch_the_odds_api',provider)
    result=client.post('/paper/provider-quotes',json={'match_id':'fixture1','sport':'football',
        'league':'EPL','home_team':'North','away_team':'South'})
    assert result.status_code==200 and len(result.json['data']['quotes'])==1
    assert 'api_key' not in seen[0]

def test_settlement_requires_imported_matching_final(setup,monkeypatch):
    client,ledger,quote,_=setup
    monkeypatch.setattr(api,'load_history',lambda *args,**kwargs:())
    result=client.post('/paper/settle',json={'match_id':'fixture1','history_match_id':'missing','sport':'football','league':'EPL'})
    assert result.status_code==400 and 'imported final-score' in result.json['error']

def test_settle_endpoint_uses_imported_scores_and_is_idempotent(setup,monkeypatch):
    from app.sports.history import HistoricalMatch
    client,ledger,quote,context=setup
    saved=client.post('/paper/quotes',json=quote).json['data']
    client.post('/paper/predictions',json={'quote_id':saved['quote_id'],
        'project_id':'proj_test','pick':'home','stake_ngn':200})
    kickoff=api._time(quote['kickoff_at']); later=kickoff+timedelta(days=2)
    ledger.clock=lambda:later
    final=HistoricalMatch(match_id='imported1',sport='football',league='EPL',season='2026-27',
        home_team='North',away_team='South',match_date=kickoff.date(),kickoff_at=kickoff,
        kickoff_unverified=False,home_score=2,away_score=1,result_available_at=kickoff+timedelta(hours=3),
        source='test imported final',fetched_at=kickoff+timedelta(hours=4))
    monkeypatch.setattr(api,'load_history',lambda *args,**kwargs:(final,))
    payload={'match_id':'fixture1','history_match_id':'imported1','sport':'football','league':'EPL'}
    response=client.post('/paper/settle',json=payload)
    assert response.status_code==200
    assert client.post('/paper/settle',json=payload).json==response.json
    prediction=client.get('/paper').json['data']['predictions'][0]
    assert prediction['status']=='win' and prediction['net_ngn']=='200.00'


def test_agent_forecast_from_different_project_is_rejected(setup,monkeypatch):
    client,ledger,quote,_=setup
    saved=client.post('/paper/quotes',json=quote).json['data']
    monkeypatch.setattr(api,'SimulationManager',lambda:SimpleNamespace(
        get_simulation=lambda _:SimpleNamespace(project_id='proj_other')))
    response=client.post('/paper/predictions',json={'quote_id':saved['quote_id'],'project_id':'proj_test',
        'simulation_id':'sim_other','pick':'home','stake_ngn':100})
    assert response.status_code==400
    assert ledger.list_predictions()==()

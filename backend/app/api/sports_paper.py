"""Local paper-only UI/API backed by MiroFish's immutable sports ledger."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

from ..models.project import ProjectManager
from ..services.simulation_manager import SimulationManager
from ..sports.agent_layer import validate_forecast_context
from ..sports.forecast_context import load_forecast_context
from ..sports.history_store import default_history_root, load_history
from ..sports.odds import manual_quote, fetch_the_odds_api
from ..sports.paper_ledger import PaperLedger, default_paper_ledger_path
from .sports_evaluation import _selection

sports_paper_bp = Blueprint('sports_paper', __name__)


def _ledger():
    return PaperLedger(default_paper_ledger_path())


def _time(value):
    if not isinstance(value, str):
        raise ValueError('Supply an ISO timestamp with timezone')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include its timezone')
    return parsed.astimezone(timezone.utc)


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('A JSON object is required')
    if any(key in data for key in ('recorded_at', 'clock', 'api_key', 'probabilities')):
        raise ValueError('Timestamps, credentials and forecast probabilities are supplied by the server')
    return data


def _respond(action):
    try:
        return jsonify(success=True, data=action())
    except (ValueError, TypeError, KeyError) as error:
        return jsonify(success=False, error=str(error)), 400
    except OSError:
        return jsonify(success=False, error='Stored sports data could not be read'), 500


@sports_paper_bp.get('')
def list_paper_records():
    return _respond(lambda: {'quotes': _ledger().list_quotes(), 'predictions': _ledger().list_predictions()})


@sports_paper_bp.post('/quotes')
def capture_quote():
    def action():
        data = _body()
        sport, league = _selection(data.get('sport'), data.get('league'))
        quote = manual_quote(match_id=data['match_id'], sport=sport, league=league,
            home_team=data['home_team'], away_team=data['away_team'], bookmaker=data['bookmaker'],
            decimal_odds=data['decimal_odds'], kickoff_at=_time(data['kickoff_at']),
            source=data['source'], label=data.get('label', 'current'))
        ledger = _ledger()
        return ledger.get_quote(ledger.record_quote(quote))
    return _respond(action)


@sports_paper_bp.post('/provider-quotes')
def capture_provider_quotes():
    def action():
        data = _body()
        sport, league = _selection(data.get('sport'), data.get('league'))
        quotes = fetch_the_odds_api(match_id=data['match_id'], sport=sport, league=league,
            home_team=data['home_team'], away_team=data['away_team'],
            region=data.get('region', 'uk'), event_id=data.get('event_id'),
            bookmaker=data.get('bookmaker'), label=data.get('label', 'current'))
        ledger = _ledger()
        return {'quotes': [ledger.get_quote(ledger.record_quote(quote)) for quote in quotes]}
    return _respond(action)


@sports_paper_bp.post('/predictions')
def record_prediction():
    def action():
        data = _body()
        context = load_forecast_context(data['project_id'])
        ledger = _ledger()
        quote = ledger.get_quote(data['quote_id'])
        for key in ('match_id', 'sport', 'league', 'home_team', 'away_team'):
            if str(context[key]).casefold() != str(quote[key]).casefold():
                raise ValueError('Saved forecast and quote fixture identities differ')
        probabilities = context['baseline_probabilities']
        version = 'baseline:' + context['project_id'] + ':' + context['dossier_sha256']
        simulation_id = data.get('simulation_id')
        if simulation_id:
            if not isinstance(simulation_id, str) or not re.fullmatch(r'sim_[A-Za-z0-9_-]+', simulation_id):
                raise ValueError('Invalid simulation ID')
            state = SimulationManager().get_simulation(simulation_id)
            if state is None or state.project_id != context['project_id']:
                raise ValueError('Simulation does not belong to this project')
            validate_forecast_context(context=context, dossier_text=ProjectManager.get_extracted_text(state.project_id),
                                      project_id=state.project_id, simulation_id=simulation_id)
            path = Path(SimulationManager.SIMULATION_DATA_DIR) / simulation_id / 'sports_agent_forecast.json'
            if not path.is_file():
                raise ValueError('This simulation has no saved agent forecast')
            forecast = json.loads(path.read_text(encoding='utf-8'))
            if any(forecast.get(key) != value for key, value in (
                ('project_id', state.project_id), ('simulation_id', simulation_id),
                ('dossier_sha256', context['dossier_sha256']))):
                raise ValueError('Saved agent forecast binding is invalid')
            probabilities = forecast['final_probabilities']
            version = 'agents:' + simulation_id + ':' + context['dossier_sha256']
        prediction_id = ledger.record_prediction(quote_id=data['quote_id'], pick=data['pick'],
            probabilities=probabilities, stake_ngn=data['stake_ngn'], model_version=version)
        return ledger.prediction_report(prediction_id)
    return _respond(action)


@sports_paper_bp.post('/kickoff')
def correct_kickoff():
    def action():
        data = _body()
        return {'event_id': _ledger().record_kickoff_correction(data['match_id'],
            kickoff_at=_time(data['kickoff_at']), source=data['source'])}
    return _respond(action)


@sports_paper_bp.post('/settle')
def settle_imported_final():
    def action():
        data = _body()
        sport, league = _selection(data.get('sport'), data.get('league'))
        matches = [match for match in load_history(default_history_root(), sport=sport, league=league)
                   if match.match_id == data['history_match_id']]
        if len(matches) != 1:
            raise ValueError('Select exactly one imported final-score match ID')
        return {'event_id': _ledger().settle_from_final(matches[0], match_id=data['match_id'])}
    return _respond(action)

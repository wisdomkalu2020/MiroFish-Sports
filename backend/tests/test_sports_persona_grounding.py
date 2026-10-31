import json
from types import SimpleNamespace

import pytest

from app.services import oasis_profile_generator as module


def generator(monkeypatch, payload, finish='stop'):
    obj = object.__new__(module.OasisProfileGenerator)
    obj.sports_dossier = 'Cutoff dossier: injuries unknown.'
    obj.client = object()
    obj.model_name = 'test-model'
    calls = []

    def completion(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=finish,
            message=SimpleNamespace(content=json.dumps(payload)),
        )])

    monkeypatch.setattr(module, 'create_chat_completion', completion)
    return obj, calls


def test_sports_persona_does_not_promote_model_biography(monkeypatch):
    obj, calls = generator(monkeypatch, {
        'focus': 'tactics', 'persona': 'The club was founded in 1987.'})
    result = obj._generate_profile_with_llm('Coach', 'FootballCoach',
                                          'Unverified invented history', {}, 'Unverified context')
    assert '1987' not in result['persona']
    assert 'tactics' in result['persona']
    assert 'Unknown information must remain unknown' in result['persona']
    prompt = calls[0]['messages'][1]['content']
    assert 'injuries unknown' in prompt
    assert 'Unverified' not in prompt
    assert calls[0]['max_tokens'] <= 128


@pytest.mark.parametrize('payload,finish', [
    ({'focus': 'guaranteed_win'}, 'stop'),
    ({'focus': 'tactics'}, 'length'),
    ([], 'stop'),
])
def test_sports_persona_rejects_invalid_model_selection(monkeypatch, payload, finish):
    obj, _ = generator(monkeypatch, payload, finish)
    with pytest.raises(ValueError):
        obj._generate_sports_profile('Coach', 'FootballCoach')

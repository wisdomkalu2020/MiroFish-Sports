import pytest
from app.services.report_agent import local_report_observation
from app.config import Config


def test_local_excerpt_bounds_utf8_and_discloses_missing_evidence(monkeypatch):
    monkeypatch.setattr(Config, 'LLM_PROVIDER', 'local')
    monkeypatch.setenv('MIROFISH_LOCAL_REPORT_OBSERVATION_BYTES', '1000')
    original = 'Evidence 足球 ' * 2000
    result = local_report_observation(original)
    excerpt, warning = result.split('\n', 1)
    assert len(excerpt.encode('utf-8')) <= 1000
    assert '\ufffd' not in excerpt
    assert original.startswith(excerpt)
    assert 'Do not infer omitted facts' in warning
    assert len(original) > len(result)


def test_cloud_and_short_observations_preserved(monkeypatch):
    monkeypatch.setattr(Config, 'LLM_PROVIDER', 'openai')
    original = 'facts' * 5000
    assert local_report_observation(original) == original
    monkeypatch.setattr(Config, 'LLM_PROVIDER', 'local')
    monkeypatch.setenv('MIROFISH_LOCAL_REPORT_OBSERVATION_BYTES', '1000')
    assert local_report_observation('small result') == 'small result'


@pytest.mark.parametrize('value', ['0', '16001', 'invalid'])
def test_invalid_excerpt_limit_fails_explicitly(monkeypatch, value):
    monkeypatch.setattr(Config, 'LLM_PROVIDER', 'local')
    monkeypatch.setenv('MIROFISH_LOCAL_REPORT_OBSERVATION_BYTES', value)
    with pytest.raises(ValueError):
        local_report_observation('facts')

from types import SimpleNamespace

import pytest

from app.utils.openai_chat_compat import (
    create_chat_completion,
    extract_chat_completion_text,
    is_budget_exceeded_error,
    is_gpt5_family,
)
from app.config import Config


class CompletionRecorder:
    def __init__(self, result=None, error=None):
        self.result = result or object()
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def client_for(recorder):
    return SimpleNamespace(chat=SimpleNamespace(completions=recorder))


def test_gpt5_uses_completion_token_limit_without_temperature():
    recorder = CompletionRecorder()
    messages = [{"role": "user", "content": "hello"}]

    result = create_chat_completion(
        client_for(recorder),
        model="gpt-5-2025-08-07",
        messages=messages,
        temperature=0.2,
        max_tokens=123,
        response_format={"type": "json_object"},
    )

    assert result is recorder.result
    assert recorder.calls == [
        {
            "model": "gpt-5-2025-08-07",
            "messages": messages,
            "max_completion_tokens": 123,
            "response_format": {"type": "json_object"},
        }
    ]


def test_legacy_model_preserves_original_request_shape():
    recorder = CompletionRecorder()
    messages = [{"role": "user", "content": "hello"}]

    create_chat_completion(
        client_for(recorder),
        model="third-party-chat-model",
        messages=messages,
        temperature=0.7,
        max_tokens=456,
        response_format={"type": "json_object"},
    )

    assert recorder.calls == [
        {
            "model": "third-party-chat-model",
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 456,
            "response_format": {"type": "json_object"},
        }
    ]


def test_provider_error_is_propagated_without_guessing_or_retrying():
    provider_error = RuntimeError("unsupported max_tokens due to a server outage")
    recorder = CompletionRecorder(error=provider_error)

    with pytest.raises(RuntimeError) as captured:
        create_chat_completion(
            client_for(recorder),
            model="legacy-model",
            messages=[],
            max_tokens=10,
        )

    assert captured.value is provider_error
    assert len(recorder.calls) == 1


def test_every_completion_is_capped_even_if_caller_omits_limit(monkeypatch):
    monkeypatch.setattr(Config, 'LLM_MAX_OUTPUT_TOKENS', 128)
    recorder = CompletionRecorder()

    create_chat_completion(client_for(recorder), model='local-model', messages=[])
    create_chat_completion(
        client_for(recorder), model='local-model', messages=[], max_tokens=500
    )

    assert [call['max_tokens'] for call in recorder.calls] == [128, 128]


def test_budget_error_is_distinct_from_other_rate_limits():
    error = RuntimeError('budget')
    error.status_code = 429
    error.body = {'error': {'code': 'budget_exceeded'}}
    assert is_budget_exceeded_error(error)

    accounting_error = RuntimeError('accounting failed')
    accounting_error.status_code = 503
    accounting_error.body = {'error': {'code': 'budget_accounting_error'}}
    assert is_budget_exceeded_error(accounting_error)

    rate_limit = RuntimeError('rate limited')
    rate_limit.status_code = 429
    rate_limit.body = {'error': {'code': 'rate_limit_exceeded'}}
    assert not is_budget_exceeded_error(rate_limit)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5", True),
        (" GPT-5.1-mini ", True),
        ("gpt-4.1", False),
        ("my-gpt-5-proxy", False),
        (None, False),
    ],
)
def test_gpt5_family_detection(model, expected):
    assert is_gpt5_family(model) is expected


def test_extracts_text_from_supported_content_shapes():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=[
                        {"text": {"value": "first"}},
                        {"content": " second"},
                        SimpleNamespace(text=" third"),
                    ]
                )
            )
        ]
    )

    assert extract_chat_completion_text(response) == "first second third"
    assert extract_chat_completion_text(SimpleNamespace(choices=[])) == ""

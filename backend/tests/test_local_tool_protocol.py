import json
import pytest
from jsonschema import ValidationError, validate
from app.utils.local_tool_protocol import encode_tools, decode_tools


def request(choice='required'):
    return {'messages': [{'role': 'user', 'content': 'Post a test'}],
            'tool_choice': choice, 'tools': [{'type': 'function', 'function': {
                'name': 'create_post', 'parameters': {'type': 'object',
                'properties': {'content': {'type': 'string'}}, 'required': ['content']}}}]}


def reply(data, finish='stop'):
    return {'usage': {'prompt_tokens': 20, 'completion_tokens': 30},
            'choices': [{'finish_reason': finish, 'message': {'content': json.dumps(data)}}]}


def test_valid_action_preserves_usage_and_does_not_execute_tools():
    original = request()
    encoded, protocol = encode_tools(original)
    assert 'tools' not in encoded and 'tools' in original
    assert encoded['response_format']['type'] == 'json_object'
    schema = encoded['response_format']['schema']
    validate({'kind': 'tool', 'name': 'create_post', 'arguments': {'content': 'Synthetic'}}, schema)
    with pytest.raises(ValidationError):
        validate({'kind': 'tool', 'name': 'delete_account', 'arguments': {}}, schema)
    response = reply({'kind': 'tool', 'name': 'create_post', 'arguments': {'content': 'Synthetic'}})
    decoded = decode_tools(response, protocol)
    assert decoded['usage'] == response['usage']
    assert decoded['choices'][0]['finish_reason'] == 'tool_calls'
    assert json.loads(decoded['choices'][0]['message']['tool_calls'][0]['function']['arguments']) == {'content': 'Synthetic'}


@pytest.mark.parametrize('data', [
    {'kind': 'tool', 'name': 'delete_account', 'arguments': {}},
    {'kind': 'tool', 'name': 'create_post', 'arguments': {'content': 123}},
    {'kind': 'message', 'content': 'pretend the action worked'},
])
def test_invalid_required_action_rejected(data):
    _, protocol = encode_tools(request())
    with pytest.raises((ValueError, ValidationError)):
        decode_tools(reply(data), protocol)


def test_auto_allows_no_action_and_none_disallows_action():
    _, protocol = encode_tools(request('auto'))
    assert decode_tools(reply({'kind': 'message', 'content': 'No action'}), protocol)['choices'][0]['message']['content'] == 'No action'
    _, protocol = encode_tools(request('none'))
    with pytest.raises(ValueError):
        decode_tools(reply({'kind': 'tool', 'name': 'create_post', 'arguments': {'content': 'x'}}), protocol)


def test_truncated_output_and_unknown_named_tool_are_rejected():
    _, protocol = encode_tools(request())
    with pytest.raises(ValueError):
        decode_tools(reply({}, 'length'), protocol)
    with pytest.raises(ValueError):
        encode_tools(request({'type': 'function', 'function': {'name': 'unknown'}}))

def test_optional_local_text_limit_constrains_generation_and_validation(monkeypatch):
    monkeypatch.setenv('MIROFISH_LOCAL_TOOL_TEXT_LIMIT','40')
    encoded, protocol=encode_tools(request())
    data={'kind':'tool','name':'create_post','arguments':{'content':'x'*41}}
    with pytest.raises(ValidationError):
        validate(data,encoded['response_format']['schema'])
    with pytest.raises(ValidationError):
        decode_tools(reply(data),protocol)
    monkeypatch.setenv('MIROFISH_LOCAL_TOOL_TEXT_LIMIT','0')
    _, protocol=encode_tools(request())
    assert decode_tools(reply(data),protocol)['choices'][0]['finish_reason']=='tool_calls'

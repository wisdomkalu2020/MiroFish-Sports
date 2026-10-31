"""Validated JSON transport for local models without native tool calling.

This does not execute tools. OASIS/CAMEL retain execution and tool memory.
"""
import copy
import json
import uuid

from jsonschema import validate


def encode_tools(payload):
    tools = payload.get('tools')
    if not tools:
        return payload, None
    functions = {}
    for tool in tools:
        function = tool.get('function', {})
        name = function.get('name')
        if tool.get('type') != 'function' or not isinstance(name, str) or name in functions:
            raise ValueError('Only uniquely named function tools are supported')
        functions[name] = function
    choice = payload.get('tool_choice', 'auto')
    allowed = set(functions)
    if isinstance(choice, dict):
        allowed = {choice.get('function', {}).get('name')}
        if not allowed <= set(functions):
            raise ValueError('Requested tool is not available')
    elif choice not in ('auto', 'required', 'none'):
        raise ValueError('Unsupported tool choice')
    required = choice == 'required' or isinstance(choice, dict)
    result = copy.deepcopy(payload)
    result.pop('tools', None)
    result.pop('tool_choice', None)
    result.pop('parallel_tool_calls', None)
    instruction = (
        'Respond ONLY with a JSON object. To execute ONE available tool return '
        '{"kind":"tool","name":"tool_name","arguments":{...}}. '
        'Use the exact function name and valid arguments from these schemas. '
        'Do not describe code or invent a tool result. '
        + ('You MUST choose one tool. ' if required else
           'When no tool is needed return {"kind":"message","content":"your reply"}. ')
        + ('No tool may be chosen. ' if choice == 'none' else '')
        + 'Available functions: ' + json.dumps([functions[n] for n in sorted(allowed)])
    )
    messages = [{'role': 'system', 'content': instruction}]
    for original in payload['messages']:
        message = copy.deepcopy(original)
        calls = message.pop('tool_calls', None)
        if calls:
            message['content'] = json.dumps({'previous_tool_calls': calls})
        if message.get('role') == 'tool':
            message = {'role': 'user', 'content': json.dumps({
                'tool_result_for': message.get('tool_call_id'),
                'result': message.get('content'),
            })}
        messages.append(message)
    result['messages'] = messages
    result['response_format'] = {'type': 'json_object'}
    return result, (functions, allowed, required, choice == 'none')


def decode_tools(reply, protocol):
    if protocol is None:
        return reply
    functions, allowed, required, disabled = protocol
    choice = reply['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Local tool JSON did not finish normally')
    data = json.loads(choice['message']['content'])
    if not isinstance(data, dict):
        raise ValueError('Local tool response must be an object')
    result = copy.deepcopy(reply)
    output = result['choices'][0]
    if data.get('kind') == 'message' and not required:
        if not isinstance(data.get('content'), str):
            raise ValueError('Message content must be text')
        output['message'] = {'role': 'assistant', 'content': data['content']}
    elif data.get('kind') == 'tool' and not disabled and data.get('name') in allowed:
        name = data['name']
        args = data.get('arguments')
        if not isinstance(args, dict):
            raise ValueError('Tool arguments must be an object')
        validate(args, functions[name].get('parameters', {'type': 'object'}))
        output['finish_reason'] = 'tool_calls'
        output['message'] = {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'call_' + uuid.uuid4().hex, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)},
        }]}
    else:
        raise ValueError('Local model returned an invalid or disallowed tool choice')
    return result

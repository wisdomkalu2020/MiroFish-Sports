"""Focused route checks without requiring CAMEL/OASIS during collection."""

import ast
import os
from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_FUNCTIONS = (
    ("run_parallel_simulation.py", "create_model"),
    ("run_twitter_simulation.py", "_create_model"),
    ("run_reddit_simulation.py", "_create_model"),
)


def _load_model_factory(script_name, function_name):
    """Compile only the route function; OASIS is an optional heavy dependency."""
    tree = ast.parse((SCRIPTS / script_name).read_text(encoding="utf-8"))
    if function_name == "create_model":
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name)
    else:
        node = next(
            child
            for parent in tree.body
            if isinstance(parent, ast.ClassDef)
            for child in parent.body
            if isinstance(child, ast.FunctionDef) and child.name == function_name
        )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    factory = Mock()
    scope = {
        "os": os,
        "urlsplit": urlsplit,
        "ip_address": ip_address,
        "ModelFactory": factory,
        "ModelPlatformType": SimpleNamespace(OPENAI_COMPATIBLE_MODEL="compatible"),
        "Dict": dict,
        "Any": object,
    }
    exec(compile(module, str(SCRIPTS / script_name), "exec"), scope)
    function = scope[function_name]
    return (lambda: function({}) if function_name == "create_model" else function(object())), factory


@pytest.mark.parametrize("script_name,function_name", SCRIPT_FUNCTIONS)
def test_oasis_model_routes_to_explicit_local_endpoint(script_name, function_name):
    create, factory = _load_model_factory(script_name, function_name)
    environment = {
        "LLM_API_KEY": "local-test-credential",
        "MIROFISH_LOCAL_GATEWAY_TOKEN": "local-test-credential",
        "LLM_BASE_URL": "http://127.0.0.1:12345/v1",
        "LLM_MODEL_NAME": "phi4-mini",
        "LLM_MAX_OUTPUT_TOKENS": "64",
        "OPENAI_API_KEY": "old-cloud-credential",
    }
    with patch.dict(os.environ, environment, clear=True):
        create()
        assert os.environ["OPENAI_API_KEY"] == "local-test-credential"
        assert os.environ["OPENAI_BASE_URL"] == environment["LLM_BASE_URL"]
        assert os.environ["OPENAI_API_BASE_URL"] == environment["LLM_BASE_URL"]
    factory.create.assert_called_once_with(
        model_platform="compatible",
        model_type="phi4-mini",
        url="http://127.0.0.1:12345/v1",
        api_key="local-test-credential",
        model_config_dict={"max_tokens": 64},
        max_retries=0,
    )


@pytest.mark.parametrize("script_name,function_name", SCRIPT_FUNCTIONS)
def test_oasis_model_rejects_cloud_and_missing_local_route(script_name, function_name):
    create, factory = _load_model_factory(script_name, function_name)
    with patch.dict(os.environ, {
        "LLM_API_KEY": "local-test-credential",
        "MIROFISH_LOCAL_GATEWAY_TOKEN": "local-test-credential",
        "LLM_BASE_URL": "https://api.openai.com/v1",
        "LLM_MODEL_NAME": "phi4-mini",
    }, clear=True):
        with pytest.raises(ValueError, match="loopback"):
            create()
    with patch.dict(os.environ, {
        "LLM_API_KEY": "local-test-credential",
        "MIROFISH_LOCAL_GATEWAY_TOKEN": "local-test-credential",
        "LLM_BASE_URL": "http://127.0.0.1:12345/v1",
        "OPENAI_API_KEY": "old-cloud-credential",
    }, clear=True):
        with pytest.raises(ValueError, match="LLM_MODEL_NAME"):
            create()
    factory.create.assert_not_called()


@pytest.mark.parametrize("script_name,function_name", SCRIPT_FUNCTIONS)
@pytest.mark.parametrize("cap", ("0", "not-an-integer", "4097"))
def test_oasis_model_rejects_invalid_output_caps(script_name, function_name, cap):
    create, factory = _load_model_factory(script_name, function_name)
    with patch.dict(os.environ, {
        "LLM_API_KEY": "local-test-credential",
        "MIROFISH_LOCAL_GATEWAY_TOKEN": "local-test-credential",
        "LLM_BASE_URL": "http://127.0.0.1:12345/v1",
        "LLM_MODEL_NAME": "phi4-mini",
        "LLM_MAX_OUTPUT_TOKENS": cap,
    }, clear=True):
        with pytest.raises(ValueError, match="LLM_MAX_OUTPUT_TOKENS"):
            create()
    factory.create.assert_not_called()


@pytest.mark.parametrize("script_name,function_name", SCRIPT_FUNCTIONS)
def test_oasis_model_requires_gateway_token(script_name, function_name):
    create, factory = _load_model_factory(script_name, function_name)
    base = {
        "LLM_API_KEY": "local-test-credential",
        "LLM_BASE_URL": "http://127.0.0.1:12345/v1",
        "LLM_MODEL_NAME": "phi4-mini",
    }
    with patch.dict(os.environ, base, clear=True):
        with pytest.raises(ValueError, match="run budget gateway"):
            create()
    with patch.dict(os.environ, {**base, "MIROFISH_LOCAL_GATEWAY_TOKEN": "wrong"}, clear=True):
        with pytest.raises(ValueError, match="run budget gateway"):
            create()
    factory.create.assert_not_called()

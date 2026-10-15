"""
OpenAI Chat Completions compatibility helpers.

This module builds capped Chat Completions requests for local and optional
remote providers, while adapting parameters for GPT-5 family models.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..config import Config
from .run_budget import BudgetExceeded


def is_gpt5_family(model: Optional[str]) -> bool:
    """Return True when model belongs to GPT-5 family aliases/snapshots."""
    if not model:
        return False
    return model.strip().lower().startswith("gpt-5")


def is_budget_exceeded_error(error: BaseException) -> bool:
    """Recognize budget exhaustion and accounting failures that must stop a run.

    Other HTTP 429 errors retain their ordinary provider meaning.
    """
    seen: set[int] = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, BudgetExceeded):
            return True
        status = getattr(current, 'status_code', None)
        if status in {429, 502, 503}:
            body = getattr(current, 'body', None)
            details = body.get('error', body) if isinstance(body, dict) else None
            code = details.get('code') if isinstance(details, dict) else None
            if code in {'budget_exceeded', 'budget_accounting_error'}:
                return True
            response = getattr(current, 'response', None)
            headers = getattr(response, 'headers', None)
            if status == 429 and headers and headers.get('X-MiroFish-Budget-Exceeded') == '1':
                return True
        current = current.__cause__ or current.__context__
    return False


def create_chat_completion(
    client: Any,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    Create a chat completion with model-specific request parameters.

    Compatibility strategy:
    - For GPT-5 family, avoid sending temperature by default.
    - Every request has an output cap, including callers that pass None.
    - Use `max_completion_tokens` on GPT-5, `max_tokens` otherwise.
    - Propagate provider errors unchanged instead of guessing from message text.
    """
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
    }

    if response_format is not None:
        kwargs["response_format"] = response_format

    gpt5_family = is_gpt5_family(model)

    if temperature is not None and not gpt5_family:
        kwargs["temperature"] = temperature

    output_cap = Config.LLM_MAX_OUTPUT_TOKENS
    if output_cap < 1:
        raise ValueError('LLM_MAX_OUTPUT_TOKENS must be positive')
    if max_tokens is not None and max_tokens < 1:
        raise ValueError('max_tokens must be positive')
    output_cap = min(max_tokens, output_cap) if max_tokens is not None else output_cap
    if gpt5_family:
        kwargs["max_completion_tokens"] = output_cap
    else:
        kwargs["max_tokens"] = output_cap

    return client.chat.completions.create(**kwargs)


def extract_chat_completion_text(response: Any) -> str:
    """Extract plain text from chat completion response across SDK content shapes."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""

    message = getattr(choices[0], "message", None)
    if message is None:
        return ""

    content = getattr(message, "content", "")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text_obj = item.get("text")
                if isinstance(text_obj, dict):
                    text_obj = text_obj.get("value")
                if isinstance(text_obj, str):
                    chunks.append(text_obj)
                elif isinstance(item.get("content"), str):
                    chunks.append(item["content"])
                continue

            text_obj = getattr(item, "text", None)
            if isinstance(text_obj, dict):
                text_obj = text_obj.get("value")
            if isinstance(text_obj, str):
                chunks.append(text_obj)
                continue

            content_obj = getattr(item, "content", None)
            if isinstance(content_obj, str):
                chunks.append(content_obj)

        return "".join(chunks).strip()

    return str(content or "")

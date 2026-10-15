r"""Minimal Phi-4 Mini routing check; requires no Zep graph or sports data.

Run from backend in PowerShell:
    & .\.venv\Scripts\python.exe -m scripts.smoke_local_model
"""

from __future__ import annotations

import uuid

from app.utils.llm_client import LLMClient
from app.utils.local_llm_runtime import local_model_run


def main() -> None:
    run_id = f"sim_smoke_{uuid.uuid4().hex[:12]}"
    with local_model_run(run_id) as gateway:
        client = LLMClient()
        reply = client.chat(
            messages=[
                {"role": "system", "content": "Answer briefly and literally."},
                {"role": "user", "content": "Reply with the word READY."},
            ],
            temperature=0,
            max_tokens=32,
        )
        if not reply.strip():
            raise RuntimeError("Local model returned an empty response")
        usage = gateway.budget.snapshot()
        print(f"model={gateway.model} response={reply.strip()}")
        print(
            f"run={run_id} tokens={usage['spent_tokens']}/{usage['token_limit']} "
            f"estimated_cost_usd={usage['spent_cost_usd']:.6f}"
        )


if __name__ == "__main__":
    main()

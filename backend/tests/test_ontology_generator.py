from app.services.ontology_generator import OntologyGenerator


class RecordingLLMClient:
    def __init__(self):
        self.calls = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "entity_types": [],
            "edge_types": [],
            "analysis_summary": "ok",
        }


def _generator_for_test() -> OntologyGenerator:
    generator = OntologyGenerator(llm_client=object())
    generator.MAX_TEXT_LENGTH_FOR_LLM = 2000
    generator.LONG_TEXT_CHUNK_SIZE = 500
    generator.LONG_TEXT_CHUNK_OVERLAP = 0
    generator.MAX_LONG_TEXT_CHUNKS = 3
    generator.MIN_LONG_TEXT_EXCERPT = 120
    return generator


def test_short_ontology_context_keeps_original_text():
    generator = _generator_for_test()

    context = generator._build_document_context(["short document body"])

    assert context == "short document body"
    assert "长文本自动分块摘要" not in context


def test_long_ontology_context_samples_across_document():
    generator = _generator_for_test()
    long_text = "BEGIN" + ("a" * 1050) + "MIDDLE" + ("b" * 1050) + "END"

    context = generator._build_document_context([long_text])

    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert "长文本自动分块摘要" in context
    assert "BEGIN" in context
    assert "MIDDLE" in context
    assert "END" in context
    assert "分块 1/" in context
    assert "分块 3/" in context
    assert "分块 5/" in context


def test_very_long_ontology_context_selects_representative_chunks():
    generator = _generator_for_test()
    chunks = ["BEGIN"] + [
        f"CHUNK{i:02d}-" + (str(i) * 490)
        for i in range(12)
    ] + ["FINALEND"]
    long_text = "".join(chunks)

    context = generator._build_document_context([long_text])

    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert "BEGIN" in context
    assert "FINALEND" in context
    assert context.count("--- 文档 1 / 分块") == generator.MAX_LONG_TEXT_CHUNKS


def test_ontology_generation_does_not_cap_structured_output_tokens():
    llm = RecordingLLMClient()
    generator = OntologyGenerator(llm_client=llm)

    result = generator.generate(
        document_texts=["A short source document."],
        simulation_requirement="Simulate the public discussion.",
    )

    assert result["analysis_summary"] == "ok"
    assert llm.calls[0]["max_tokens"] is None
    assert llm.calls[0]["max_attempts"] == 2


def test_sports_ontology_prompt_preserves_generic_default():
    llm = RecordingLLMClient()
    generator = OntologyGenerator(llm_client=llm)

    generator.generate(["Pre-match dossier"], "Synthetic discussion", domain="sports")
    sports_call = llm.calls[-1]
    assert "pre-match sports discussion" in sports_call["messages"][0]["content"]
    assert "never be presented as a real statement" in sports_call["messages"][0]["content"]
    assert "social media" not in sports_call["messages"][0]["content"].lower()
    assert "Unknown sections remain unknown" in sports_call["messages"][1]["content"]

    generator.generate(["Original document"], "Public discussion")
    generic_call = llm.calls[-1]
    assert "社交媒体舆论模拟" in generic_call["messages"][0]["content"]
    assert "pre-match sports discussion" not in generic_call["messages"][0]["content"]

from types import SimpleNamespace

from rag.llm import OpenRouterClient
from rag.models import RetrievalHit
from rag.pipeline import RAGPipeline


class FakeRetriever:
    def search(self, question: str) -> list[RetrievalHit]:
        return [
            RetrievalHit(
                "id-p1-c00",
                "id",
                "order.pdf",
                "order.pdf",
                1,
                0,
                "USD 100",
                0.0,
            )
        ]


class FakeLlm:
    def __init__(self) -> None:
        self.messages = None

    def answer(self, messages):
        self.messages = messages
        return "USD 100 [order.pdf，第1页，id-p1-c00]"


def test_pipeline_keeps_retrieval_prompt_and_generation_separate() -> None:
    llm = FakeLlm()
    pipeline = RAGPipeline(FakeRetriever(), llm)  # type: ignore[arg-type]

    result = pipeline.ask("总额？")

    assert result.answer.startswith("USD 100")
    assert result.hits[0].chunk_id == "id-p1-c00"
    assert llm.messages == result.messages


def test_openrouter_client_uses_fixed_request_contract() -> None:
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=" answer "))]
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    settings = SimpleNamespace(
        openrouter_api_key="secret",
        openrouter_model="test-model",
    )
    wrapper = OpenRouterClient(settings, client=client)  # type: ignore[arg-type]

    answer = wrapper.answer([{"role": "user", "content": "question"}])

    assert answer == "answer"
    assert calls[0]["model"] == "test-model"
    assert calls[0]["temperature"] == 0


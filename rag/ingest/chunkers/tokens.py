"""Token counting for chunk budgets, fixed to the multilingual-e5 tokenizer whatever the embedder.

The budget is a chunking rule, not an encoder property: keeping one tokenizer makes chunks
identical across embedders, so embedder comparisons are not mixed with chunking changes.
Each embedder checks separately that a chunk fits its own input limit.
"""

from __future__ import annotations

from typing import Any

E5_TOKENIZER = "intfloat/multilingual-e5-small"
E5_TOKENIZER_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"


class E5TokenCounter:
    """Counts as multilingual-e5 sees a passage: `passage: ` prefix plus special tokens."""

    passage_prefix = "passage: "

    def __init__(self) -> None:
        self._tokenizer: Any = None

    def _load(self) -> Any:
        if self._tokenizer is None:
            from transformers import AutoTokenizer
            self._tokenizer = AutoTokenizer.from_pretrained(E5_TOKENIZER, revision=E5_TOKENIZER_REVISION)
        return self._tokenizer

    def _count(self, text: str, special: bool) -> int:
        ids = self._load()(text, add_special_tokens=special, truncation=False)["input_ids"]
        return len(ids[0] if ids and isinstance(ids[0], list) else ids)

    def count_passage(self, text: str) -> int:
        return self._count(self.passage_prefix + text, special=True)

    def count_text(self, text: str) -> int:
        return self._count(text, special=False)

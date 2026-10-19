"""Document language, judged once after parsing and read by every later stage."""

from __future__ import annotations

import re
from typing import Iterable, Literal

from rag.index.analyzer import detect_language

DocumentLanguage = Literal["chinese", "spanish", "english"]
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def document_language(texts: Iterable[str]) -> DocumentLanguage:
    """Chinese when CJK characters are more than half of all letters; otherwise by stopwords."""
    texts = list(texts)
    joined = "".join(texts)
    cjk = len(_CJK.findall(joined))
    letters = sum(ch.isalpha() for ch in joined)
    if letters and cjk * 2 > letters:
        return "chinese"
    return detect_language(texts)

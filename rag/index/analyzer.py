"""Text analysis for BM25: text → terms. Rules: docs/rules/lexical.md.

Questions and chunks go through the same steps: normalise, split into words (connected
alphanumeric runs are kept whole and also split into their parts), drop stopwords, and
optionally stem. Stemming is language-specific; a document's language is decided once
from its stopword counts.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import cache
from importlib import resources
from typing import Iterable, Literal

Language = Literal["spanish", "english"]
LANGUAGES: tuple[Language, ...] = ("spanish", "english")

# Every Han character is its own word; separating them first also splits "合同CONTRATO".
_HAN = re.compile(r"([㐀-䶿一-鿿豈-﫿\U00020000-\U0002ebef])")
# Alphanumeric runs joined by - . / , (model numbers, document numbers, amounts).
_WORD = re.compile(r"[^\W_]+(?:[-./,][^\W_]+)*")
_CONNECTOR = re.compile(r"[-./,]")
_LETTERS = re.compile(r"[^\W\d_]+")


def normalize(text: str) -> str:
    """NFKC, case-folded, accents removed."""
    text = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text).casefold())
    return unicodedata.normalize("NFC", "".join(c for c in text if not unicodedata.combining(c)))


def words(text: str) -> list[str]:
    """Words before stopwords and stemming: each connected run, then its parts."""
    result: list[str] = []
    for match in _WORD.finditer(_HAN.sub(r" \1 ", normalize(text))):
        word = match.group()
        result.append(word)
        if _CONNECTOR.search(word):
            result.extend(_CONNECTOR.split(word))
    return result


@cache
def stopwords(language: Language) -> frozenset[str]:
    """Snowball stopword list: first token of each line, '|' starts a comment."""
    raw = resources.files("rag.index").joinpath(f"stopwords/{language}.txt").read_text(encoding="utf-8")
    found = (line.split("|", 1)[0].split() for line in raw.splitlines())
    return frozenset(normalize(item[0]) for item in found if item)


@cache
def _stemmer(language: Language):
    import snowballstemmer
    return snowballstemmer.stemmer(language)


def detect_language(texts: Iterable[str]) -> Language:
    """The language whose stopwords occur more often; ties count as English."""
    tokens = [w for text in texts for w in words(text)]
    counts = {lang: sum(t in stopwords(lang) for t in tokens) for lang in LANGUAGES}
    return "spanish" if counts["spanish"] > counts["english"] else "english"


class Analyzer:
    def __init__(self, stopwords: bool = True, stemming: bool = False):
        if type(stopwords) is not bool or type(stemming) is not bool:
            raise ValueError("stopwords 和 stemming 必须为 true 或 false")
        self.remove_stopwords = stopwords
        self.stemming = stemming

    def settings(self) -> dict[str, bool]:
        return {"stopwords": self.remove_stopwords, "stemming": self.stemming}

    def terms(self, text: str, language: Language) -> Counter[str]:
        """Term frequencies; `language` only selects the stemmer."""
        dropped = stopwords("spanish") | stopwords("english") if self.remove_stopwords else frozenset()
        result: Counter[str] = Counter()
        for word in words(text):
            if word in dropped:
                continue
            if self.stemming and _LETTERS.fullmatch(word):
                word = _stemmer(language).stemWord(word)
            result[word] += 1
        return result

    def query_terms(self, text: str) -> dict[Language, frozenset[str]]:
        """Distinct query terms per document language (identical when not stemming)."""
        return {lang: frozenset(self.terms(text, lang)) for lang in LANGUAGES}

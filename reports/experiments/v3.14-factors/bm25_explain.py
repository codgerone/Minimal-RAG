"""Which query terms give a chunk its BM25 score: for a text and a configuration, every scored chunk of
the scoped documents with its per-term contributions (same formula as rag/index/lexical.py).

    from bm25_explain import explain
    explain("structured", ["5a4c…"], "What is the quantity …", top=6)
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from rag.index.analyzer import Analyzer
from rag.index.lexical import B, K1, idf

ROOT = Path(__file__).resolve().parents[3]


def explain(config: str, document_ids, text: str, top: int = 6, only=()) -> list[tuple[str, float, dict]]:
    con = sqlite3.connect(ROOT / f".rag/indexes/{config}/bm25.sqlite")
    terms = Analyzer().query_terms(text)
    wanted = sorted(set().union(*terms.values()))
    total, average = con.execute("SELECT COUNT(*), AVG(length) FROM chunks").fetchone()
    q = ",".join("?" * len(wanted))
    rows = con.execute("SELECT p.term, p.chunk_id, p.tf, c.document_id, c.language, c.length FROM postings p "
                       f"JOIN chunks c ON c.chunk_id = p.chunk_id WHERE p.term IN ({q})", wanted).fetchall()
    freq = Counter(r[0] for r in rows)
    out: dict[str, dict] = {}
    for term, chunk, tf, doc, lang, length in rows:
        if doc not in document_ids or term not in terms.get(lang, ()):
            continue
        s = idf(total, freq[term]) * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length / average))
        out.setdefault(chunk, {})[term] = round(s, 2)
    ranked = sorted(((c, round(sum(t.values()), 2), t) for c, t in out.items()), key=lambda x: -x[1])
    return [x for x in ranked if not only or any(o in x[0] for o in only)] if only else ranked[:top]


def query_terms(text: str) -> dict:
    return {k: sorted(v) for k, v in Analyzer().query_terms(text).items()}

"""Shared by the V3.14 query-side experiments: the planner and each question's plan inputs."""
from __future__ import annotations

from pathlib import Path

from rag.ingest.tables.llm_tables import LLMCache
from rag.paths import Workspace
from rag.query.llm import OpenRouterLLM
from rag.query.planning import Planner, document_languages

MODEL = "google/gemini-3.8-flash"


def planner(ws: Workspace) -> Planner:
    return Planner(OpenRouterLLM(model=MODEL, temperature=0, env_override=False),
                   LLMCache(ws.root / ".rag" / "llm-cache"))


def plan_inputs(ws: Workspace, scope) -> tuple[list[str], list[str]]:
    """Document languages of the scope (whole index when no code was found) and its codes."""
    langs = document_languages(ws.index_dir("structured"))
    docs = scope.document_ids or list(langs)
    return sorted({langs[d] for d in docs}), list(scope.codes)

"""Decide which documents a question is searched in. Rules: docs/rules/document-filter.md.

The catalog documents/documents.toml registers user-assigned codes per PDF. A question that
contains a registered code is searched only in the documents carrying that code.
"""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rag.config import ConfigError
from rag.ingest.sources import document_id
from rag.models import SourceDocument

CATALOG_FILE = "documents.toml"
MIN_CODE_CHARS = 4

ScopeKind = Literal["user", "identified", "unidentified", "no_catalog", "disabled"]


def normalize(text: str) -> str:
    return "".join(re.findall(r"[^\W_]", unicodedata.normalize("NFKC", text).casefold()))


@dataclass(frozen=True)
class CatalogEntry:
    file: str
    codes: tuple[str, ...]


@dataclass(frozen=True)
class Catalog:
    entries: dict[str, CatalogEntry]    # document_id → entry, only for PDFs present in documents/
    missing_files: tuple[str, ...]      # registered files not found in documents/
    unregistered: tuple[str, ...]       # PDFs in documents/ without an entry


def load_catalog(documents: Path, sources: tuple[SourceDocument, ...]) -> Catalog | None:
    """None when no catalog file exists. Invalid catalogs raise ConfigError."""
    path = documents / CATALOG_FILE
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"无法读取文档标识表 {CATALOG_FILE}：{exc}") from exc
    items = data.get("document", [])
    if set(data) - {"document"} or not isinstance(items, list):
        raise ConfigError(f"{CATALOG_FILE}：只接受 [[document]] 列表")
    present = {source.document_id: source for source in sources}
    entries: dict[str, CatalogEntry] = {}
    missing: list[str] = []
    owner: dict[str, str] = {}
    seen_files: set[str] = set()
    for item in items:
        file = item.get("file") if isinstance(item, dict) else None
        codes = item.get("codes") if isinstance(item, dict) else None
        if (not isinstance(file, str) or not isinstance(codes, list) or not codes
                or not all(isinstance(c, str) for c in codes) or set(item) - {"file", "codes"}):
            raise ConfigError(f"{CATALOG_FILE}：每个 [[document]] 需要 file = \"文件名\" 和非空的 codes 列表")
        identity = document_id(file.strip().replace("\\", "/"))
        if identity in seen_files:
            raise ConfigError(f"{CATALOG_FILE}：文件 {file} 登记了两次")
        seen_files.add(identity)
        for code in codes:
            key = normalize(code)
            if len(key) < MIN_CODE_CHARS:
                raise ConfigError(f"{CATALOG_FILE}：编码 {code!r} 规范化后不足 {MIN_CODE_CHARS} 个字符")
            if key in owner:
                raise ConfigError(f"{CATALOG_FILE}：编码 {code!r} 同时登记给了 {owner[key]} 和 {file}")
            owner[key] = file
        if identity in present:
            entries[identity] = CatalogEntry(file, tuple(codes))
        else:
            missing.append(file)
    unregistered = tuple(s.relative_path for s in sources if s.document_id not in entries)
    return Catalog(entries, tuple(missing), unregistered)


@dataclass(frozen=True)
class SearchScope:
    kind: ScopeKind
    document_ids: tuple[str, ...] | None    # None = whole index
    codes: tuple[str, ...] = ()             # catalog codes found in the question


def resolve_scope(question: str, catalog: Catalog | None, enabled: bool,
                  user_document_id: str | None = None) -> SearchScope:
    if user_document_id is not None:
        return SearchScope("user", (user_document_id,))
    if not enabled:
        return SearchScope("disabled", None)
    if catalog is None:
        return SearchScope("no_catalog", None)
    text = normalize(question)
    documents: list[str] = []
    codes: list[str] = []
    for identity, entry in catalog.entries.items():
        found = [code for code in entry.codes if normalize(code) in text]
        if found:
            documents.append(identity)
            codes.extend(found)
    if not documents:
        return SearchScope("unidentified", None)
    return SearchScope("identified", tuple(sorted(documents)), tuple(codes))


def describe(scope: SearchScope, names: dict[str, str]) -> str:
    """One line for command output; names maps document_id → file name."""
    if scope.kind == "user":
        return f"检索范围：用户指定 · {names.get(scope.document_ids[0], scope.document_ids[0])}"  # type: ignore[index]
    if scope.kind == "identified":
        files = "、".join(names.get(d, d) for d in scope.document_ids or ())
        return f"检索范围：按标识编码 {'、'.join(scope.codes)} → {files}"
    reason = {"unidentified": "问题中未识别出已登记的标识编码",
              "no_catalog": f"未配置文档标识表 documents/{CATALOG_FILE}",
              "disabled": "文档过滤已关闭"}[scope.kind]
    return f"检索范围：全库（{reason}）"

"""manifest.json: the single record of what an index contains. Written last, atomically."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag.jsonio import read_json, write_json


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class DocumentEntry:
    document_id: str
    document_name: str
    relative_path: str
    file_hash: str
    folder: str           # readable folder name under documents/ and reports/
    page_count: int
    chunk_count: int
    table_count: int
    built_at: str


@dataclass
class Manifest:
    config_name: str
    fingerprint: str
    build_settings: dict[str, Any]
    updated_at: str
    documents: dict[str, DocumentEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Manifest | None":
        if not path.is_file():
            return None
        data = read_json(path)
        documents = {key: DocumentEntry(**value) for key, value in data["documents"].items()}
        return cls(data["config_name"], data["fingerprint"], data["build_settings"],
                   data["updated_at"], documents)

    def save(self, path: Path) -> None:
        self.updated_at = now()
        self.documents = dict(sorted(self.documents.items(),
                                     key=lambda kv: kv[1].relative_path.casefold()))
        write_json(path, self)

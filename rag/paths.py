"""Where everything lives. Machine data under .rag/, human-readable reports under reports/."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


def readable_name(relative_path: str) -> str:
    """Folder name for a document: its path without `.pdf`, sub-folders joined by `__`."""
    stem = relative_path[:-4] if relative_path.casefold().endswith(".pdf") else relative_path
    name = stem.replace("/", "__")
    return re.sub(r'[<>:"\|?*\x00-\x1f]', "_", name).strip(" .") or "document"


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def documents(self) -> Path:
        return self.root / "documents"

    @property
    def configs(self) -> Path:
        return self.root / "configs"

    @property
    def ground_truth(self) -> Path:
        return self.root / "eval" / "ground-truth"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    def index_dir(self, config_name: str) -> Path:
        return self.root / ".rag" / "indexes" / config_name

    def ingest_reports(self, config_name: str) -> Path:
        return self.reports / "ingest" / config_name

    def eval_reports(self) -> Path:
        return self.reports / "eval"

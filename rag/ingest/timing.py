"""Wall-clock time of each ingest step for one document, kept in the manifest entry."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


class StepTimes:
    def __init__(self) -> None:
        self.seconds: dict[str, float] = {}
        self.cache: dict[str, str] = {}     # step → hit / miss, or "<hits>/<total>" for passages

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[name] = self.seconds.get(name, 0.0) + time.perf_counter() - start

    def rounded(self) -> dict[str, float]:
        return {name: round(value, 3) for name, value in self.seconds.items()}

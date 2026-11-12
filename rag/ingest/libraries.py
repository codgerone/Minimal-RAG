"""Installed versions of the third-party libraries a step calls; part of that step's identity, so an
upgrade marks the documents passing through the step as out of date."""

from __future__ import annotations

from functools import cache
from importlib.metadata import PackageNotFoundError, version


@cache
def _version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "not installed"


def versions(*distributions: str) -> dict[str, str]:
    return {name: _version(name) for name in distributions}

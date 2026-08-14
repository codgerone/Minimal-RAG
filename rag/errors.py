"""User-facing domain errors."""

from __future__ import annotations


class RagError(Exception):
    """Base class for expected errors that should not print a traceback."""

    exit_code = 2

    def __init__(
        self,
        message: str,
        remediation: str | None = None,
        *,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation
        self.cause = cause

    def user_message(self) -> str:
        if self.remediation:
            return f"{self.message}\n建议：{self.remediation}"
        return self.message


class ConfigurationError(RagError):
    pass


class DocumentDirectoryError(RagError):
    pass


class DocumentSelectionError(RagError):
    pass


class PdfParseError(RagError):
    pass


class EmbeddingError(RagError):
    exit_code = 4


class VectorStoreError(RagError):
    pass


class ManifestError(RagError):
    pass


class IndexNotReadyError(RagError):
    pass


class LlmServiceError(RagError):
    exit_code = 4


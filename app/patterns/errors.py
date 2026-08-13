"""Framework-neutral errors raised by the pattern memory layer.

The pattern implementation deliberately does not depend on FastAPI (or MCP).
Adapters translate these errors to their transport-specific status and payload
formats at the boundary.
"""

from __future__ import annotations


class PatternError(Exception):
    """Base class for expected pattern-memory failures."""

    status_code = 500

    def __init__(self, detail: object = "pattern operation failed") -> None:
        self.detail = detail
        super().__init__(str(detail))


class PatternDisabled(PatternError):
    """Pattern mining or retrieval is disabled by configuration."""

    status_code = 403


class PatternNotFound(PatternError):
    """A requested pattern or processing run does not exist."""

    status_code = 404


class PatternValidation(PatternError, ValueError):
    """A pattern card, evidence packet, or processing request is invalid."""

    status_code = 400


class PatternStorageUnavailable(PatternError):
    """The pattern database could not be read or written."""

    status_code = 503

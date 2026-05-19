"""Tool-layer exception types.

ParseError and ColumnNotFoundError already live in parser.py and are
re-exported here so callers have one import point for the full set.
"""

from __future__ import annotations

from .parser import ColumnNotFoundError, ParseError

__all__ = [
    "ColumnNotFoundError",
    "ExportNotFoundError",
    "ParseError",
    "PathTraversalError",
]


class ExportNotFoundError(FileNotFoundError):
    """Raised when a requested export file isn't present under the working root."""


class PathTraversalError(ValueError):
    """Raised when a user-supplied path resolves outside the working root."""

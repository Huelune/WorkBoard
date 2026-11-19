from __future__ import annotations


class WorkBoardError(Exception):
    """Base class for errors the UI can show as a kind plus a next step."""


class ValidationError(WorkBoardError, ValueError):
    """The input breaks a value or state rule; nothing was saved."""


class PermissionDenied(WorkBoardError):
    """The actor may not perform this change; nothing was saved."""


class NotFound(WorkBoardError):
    """The referenced record does not exist."""

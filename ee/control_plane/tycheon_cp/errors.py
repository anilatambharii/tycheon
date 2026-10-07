"""One error type for everything the API refuses, rendered as a fixed envelope.

Proprietary: see ee/LICENSE.
"""

from __future__ import annotations

from typing import Any


class ApiProblem(Exception):
    """A refusal with an HTTP status and a stable machine-readable code."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details = details or []
        self.headers = headers or {}

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


def not_found(what: str = "resource") -> ApiProblem:
    return ApiProblem(404, "not_found", f"No such {what}.")


def forbidden(message: str = "You do not have permission to do that.") -> ApiProblem:
    return ApiProblem(403, "forbidden", message)


def unauthorized(message: str = "A valid API key or session is required.") -> ApiProblem:
    return ApiProblem(401, "unauthorized", message, headers={"WWW-Authenticate": "Bearer"})

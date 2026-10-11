"""Application errors and RFC 9457-style problem responses."""

from dataclasses import dataclass
from typing import Any

from fastapi import status

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_PREFIX = "urn:stockflow:problem:"


@dataclass(frozen=True)
class Problem:
    """HTTP status and response body for a problem response."""

    status: int
    body: dict[str, object]


class AppError(Exception):
    """An expected application error safe to return to the client."""

    def __init__(
        self,
        status_code: int,
        title: str,
        detail: str,
        problem_type: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.problem_type = problem_type or title.lower().replace(" ", "-")


def _base(request_id: str) -> dict[str, object]:
    """Return common response fields."""
    return {
        "requestId": request_id,
        "instance": f"urn:stockflow:request:{request_id}",
    }


def bad_request(detail: str) -> AppError:
    return AppError(
        status.HTTP_400_BAD_REQUEST,
        "Bad Request",
        detail,
        "bad-request",
    )


def unauthorized(
    detail: str = "Authentication is required.",
) -> AppError:
    return AppError(
        status.HTTP_401_UNAUTHORIZED,
        "Unauthorized",
        detail,
        "unauthorized",
    )


def forbidden(
    detail: str = "You do not have permission to perform this action.",
) -> AppError:
    return AppError(
        status.HTTP_403_FORBIDDEN,
        "Forbidden",
        detail,
        "forbidden",
    )


def not_found(detail: str = "Resource not found.") -> AppError:
    return AppError(
        status.HTTP_404_NOT_FOUND,
        "Not Found",
        detail,
        "not-found",
    )


def conflict(detail: str) -> AppError:
    return AppError(
        status.HTTP_409_CONFLICT,
        "Conflict",
        detail,
        "conflict",
    )


def problem_from_app_error(
    exc: AppError,
    request_id: str,
) -> Problem:
    body: dict[str, object] = {
        "type": f"{PROBLEM_TYPE_PREFIX}{exc.problem_type}",
        "title": exc.title,
        "status": exc.status_code,
        "detail": exc.detail,
    }
    return Problem(exc.status_code, {**body, **_base(request_id)})


def problem_from_status(
    status_code: int,
    request_id: str,
) -> Problem:
    titles = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        409: "Conflict",
        422: "Validation Failed",
        429: "Too Many Requests",
    }
    title = titles.get(status_code, "HTTP Error")
    body: dict[str, object] = {
        "type": f"{PROBLEM_TYPE_PREFIX}http-error",
        "title": title,
        "status": status_code,
        "detail": title,
    }
    return Problem(status_code, {**body, **_base(request_id)})


def problem_from_validation(
    errors: list[dict[str, Any]],
    request_id: str,
) -> Problem:
    """Return validation paths and messages without submitted values."""
    items = [
        {
            "path": ".".join(
                str(part)
                for part in error.get("loc", ())
                if part != "body"
            ),
            "message": str(error["msg"]),
        }
        for error in errors
    ]

    body: dict[str, object] = {
        "type": f"{PROBLEM_TYPE_PREFIX}validation-failed",
        "title": "Validation Failed",
        "status": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "detail": "One or more fields are invalid.",
        "errors": items,
    }
    return Problem(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        {**body, **_base(request_id)},
    )


def problem_from_unexpected(
    exc: Exception,
    request_id: str,
    expose_message: bool = False,
) -> Problem:
    detail = (
        str(exc)
        if expose_message
        else "An unexpected error occurred."
    )
    body: dict[str, object] = {
        "type": f"{PROBLEM_TYPE_PREFIX}internal-error",
        "title": "Internal Server Error",
        "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "detail": detail,
    }
    return Problem(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        {**body, **_base(request_id)},
    )

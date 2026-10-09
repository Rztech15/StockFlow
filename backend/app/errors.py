"""Central error model: RFC 9457-style problem details. No stack traces or DB internals leak."""

from dataclasses import dataclass, field

PROBLEM_TYPE_PREFIX = "urn:stockflow:problem:"
PROBLEM_CONTENT_TYPE = "application/problem+json"

_TITLES = {
    400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
    405: "Method Not Allowed", 406: "Not Acceptable", 408: "Request Timeout",
    409: "Conflict", 413: "Payload Too Large", 415: "Unsupported Media Type",
    422: "Unprocessable Content", 429: "Too Many Requests",
}  # fmt: skip


class AppError(Exception):
    """An expected, client-facing failure."""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status, self.code, self.title = status, code, title
        self.detail, self.errors = detail, errors


def bad_request(detail: str | None = None) -> AppError:
    return AppError(400, "bad-request", "Bad Request", detail)


def unauthorized(detail: str | None = None) -> AppError:
    return AppError(401, "unauthorized", "Unauthorized", detail)


def forbidden(detail: str | None = None) -> AppError:
    return AppError(403, "forbidden", "Forbidden", detail)


def not_found(detail: str | None = None) -> AppError:
    return AppError(404, "not-found", "Not Found", detail)


def conflict(detail: str | None = None) -> AppError:
    return AppError(409, "conflict", "Conflict", detail)


@dataclass
class Problem:
    status: int
    body: dict[str, object] = field(default_factory=dict)


def _base(request_id: str) -> dict[str, object]:
    return {"instance": f"urn:stockflow:request:{request_id}", "requestId": request_id}


def problem_from_app_error(error: AppError, request_id: str) -> Problem:
    body: dict[str, object] = {
        "type": f"{PROBLEM_TYPE_PREFIX}{error.code}",
        "title": error.title,
        "status": error.status,
    }
    if error.detail:
        body["detail"] = error.detail
    if error.errors:
        body["errors"] = error.errors
    return Problem(error.status, {**body, **_base(request_id)})


def problem_from_status(status: int, request_id: str) -> Problem:
    """Generic 4xx/5xx from the framework (404 unknown route, 405, ...). No custom detail."""
    body = {"type": "about:blank", "title": _TITLES.get(status, "Error"), "status": status}
    return Problem(status, {**body, **_base(request_id)})


def problem_from_validation(errors: list[dict[str, object]], request_id: str) -> Problem:
    """Request validation failures. Only field path and message; never the submitted value."""
    items = [
        {"path": ".".join(str(p) for p in e.get("loc", ()) if p != "body"), "message": str(e["msg"])}
        for e in errors
    ]
    body: dict[str, object] = {
        "type": f"{PROBLEM_TYPE_PREFIX}validation-failed",
        "title": "Validation Failed",
        "status": 422,
        "detail": "One or more fields are invalid.",
        "errors": items,
    }
    return Problem(422, {**body, **_base(request_id)})


def problem_from_unexpected(error: Exception, request_id: str, expose_message: bool) -> Problem:
    """Unknown errors become a generic 500. Outside production only, the message (not the
    traceback) is added to help debugging."""
    detail = str(error) if expose_message else "An unexpected error occurred."
    body: dict[str, object] = {
        "type": f"{PROBLEM_TYPE_PREFIX}internal-error",
        "title": "Internal Server Error",
        "status": 500,
        "detail": detail,
    }
    return Problem(500, {**body, **_base(request_id)})

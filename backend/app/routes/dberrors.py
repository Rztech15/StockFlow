"""Translate the SQLSTATE codes raised by our SECURITY DEFINER functions into API errors."""

from typing import NoReturn

from app.errors import conflict, forbidden, not_found


def sqlstate(exc: Exception) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


def message(exc: Exception) -> str | None:
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "message_primary", None)


def raise_for_db_error(exc: Exception, *, duplicate: str = "Already exists.") -> NoReturn:
    """Re-raise `exc` as a safe AppError when it is one of our own expected errors."""
    state = sqlstate(exc)
    if state == "42501":
        raise forbidden("You are not allowed to do that.") from None
    if state == "P0002":
        raise not_found(message(exc) or "Not found.") from None
    if state == "23505":
        raise conflict(duplicate) from None
    if state == "23503":
        raise not_found("A referenced record was not found.") from None
    if state == "22023":
        raise conflict(message(exc) or "Invalid request.") from None
    raise exc

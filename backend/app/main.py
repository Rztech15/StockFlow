import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, load_settings_or_exit
from app.context import RequestContext
from app.db import Database
from app.errors import (
    PROBLEM_CONTENT_TYPE,
    AppError,
    Problem,
    problem_from_app_error,
    problem_from_status,
    problem_from_unexpected,
    problem_from_validation,
)
from app.routes.health import router as health_router
from app.routes.v1 import router as v1_router

log = logging.getLogger("stockflow")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def _secure_headers(response: Response, request_id: str) -> None:
    response.headers["x-request-id"] = request_id
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["cache-control"] = "no-store"


def _respond(problem: Problem, request_id: str) -> JSONResponse:
    response = JSONResponse(problem.body, status_code=problem.status, media_type=PROBLEM_CONTENT_TYPE)
    _secure_headers(response, request_id)
    return response


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or load_settings_or_exit()
    db = db or Database(settings.database_url.get_secret_value(), pool_size=settings.db_pool_max)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        db.dispose()

    prod = settings.is_production
    app = FastAPI(
        title="StockFlow API",
        version="0.0.0",
        docs_url=None if prod else "/docs",
        redoc_url=None,
        openapi_url=None if prod else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["content-type", "x-request-id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        inbound = request.headers.get("x-request-id", "")
        # Accept an inbound id only if well-formed (prevents log injection).
        rid = inbound if _REQUEST_ID.fullmatch(inbound) else str(uuid.uuid4())
        request.state.request_id = rid
        request.state.ctx = RequestContext(request_id=rid)
        response = await call_next(request)
        _secure_headers(response, rid)
        return response

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        rid = _request_id(request)
        return _respond(problem_from_app_error(exc, rid), rid)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        rid = _request_id(request)
        return _respond(problem_from_status(exc.status_code, rid), rid)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = _request_id(request)
        return _respond(problem_from_validation(list(exc.errors()), rid), rid)  # type: ignore[arg-type]

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = _request_id(request)
        log.error("unhandled error (request %s)", rid, exc_info=exc)  # traceback stays in logs
        return _respond(problem_from_unexpected(exc, rid, expose_message=not prod), rid)

    app.include_router(health_router)
    app.include_router(v1_router)
    return app

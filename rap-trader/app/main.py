import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.routes import (
    analyst,
    backtests,
    chairman,
    committee,
    data_platform,
    features,
    health,
    kronos,
    market_data,
    portfolio,
    risk,
    system,
)
from app.config import get_settings
from app.domain.models.error import ApiError
from app.logging_config import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name, version=__version__)
app.include_router(health.router)
app.include_router(system.router)
app.include_router(market_data.router)
app.include_router(kronos.router)
app.include_router(backtests.router)
app.include_router(analyst.router)
app.include_router(features.router)
app.include_router(data_platform.router)
app.include_router(portfolio.router)
app.include_router(risk.router)
app.include_router(committee.router)
app.include_router(chairman.router)


def _request_id(request: Request) -> str | None:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else None


def _error_response(status_code: int, code: str, safe_message: str, request: Request) -> JSONResponse:
    error = ApiError(code=code, safe_message=safe_message, request_id=_request_id(request))
    return JSONResponse(status_code=status_code, content=error.model_dump())


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(422, "VALIDATION_ERROR", "Request validation failed", request)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, str):
        safe_message = detail
    elif isinstance(detail, dict) and isinstance(detail.get("safe_message"), str):
        safe_message = detail["safe_message"]
    else:
        safe_message = "HTTP request failed"
    return _error_response(exc.status_code, "HTTP_ERROR", safe_message, request)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled request exception", extra={"request_id": _request_id(request)})
    return _error_response(500, "INTERNAL_ERROR", "An internal server error occurred", request)


@app.middleware("http")
async def request_logging(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info("request completed", extra={"request_id": request_id, "service": "api", "event": "request", "result": response.status_code})
    return response

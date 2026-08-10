import logging
from uuid import uuid4

from fastapi import FastAPI, Request

from app import __version__
from app.api.routes import analyst, backtests, data_platform, features, health, kronos, market_data, portfolio, risk, system
from app.config import get_settings
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


@app.middleware("http")
async def request_logging(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info("request completed", extra={"request_id": request_id, "service": "api", "event": "request", "result": response.status_code})
    return response

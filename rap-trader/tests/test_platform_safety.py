import pytest
from fastapi import APIRouter, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"KRONOS_OFFLINE_ONLY": False}, "KRONOS_OFFLINE_ONLY=false requires ALLOW_NON_OFFLINE_KRONOS=true"),
        ({"BACKTEST_OFFLINE_ONLY": False}, "BACKTEST_OFFLINE_ONLY=false requires ALLOW_NON_OFFLINE_BACKTEST=true"),
        (
            {"LIVE_TRADING_ENABLED": True, "TRADING_MODE": "live", "ALLOW_LIVE_TRADING": True},
            "live trading requires APP_ENV=staging or APP_ENV=production",
        ),
        (
            {"APP_ENV": "staging", "LIVE_TRADING_ENABLED": True, "TRADING_MODE": "live"},
            "live trading requires ALLOW_LIVE_TRADING=true",
        ),
    ],
)
def test_settings_reject_unsafe_runtime_combinations(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_explicit_research_connectivity_opt_ins_are_accepted() -> None:
    settings = Settings(
        _env_file=None,
        KRONOS_OFFLINE_ONLY=False,
        ALLOW_NON_OFFLINE_KRONOS=True,
        BACKTEST_OFFLINE_ONLY=False,
        ALLOW_NON_OFFLINE_BACKTEST=True,
    )
    assert settings.kronos_offline_only is False
    assert settings.backtest_offline_only is False


def test_validation_error_uses_api_error_and_request_id() -> None:
    response = TestClient(app).get("/market-data/bars", headers={"X-Request-ID": "validation-request"})
    assert response.status_code == 422
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "safe_message": "Request validation failed",
        "request_id": "validation-request",
    }


def test_http_and_internal_errors_use_safe_api_error() -> None:
    router = APIRouter()

    @router.get("/_phase14/http-error")
    def http_error() -> None:
        raise HTTPException(status_code=409, detail="Safe conflict")

    @router.get("/_phase14/internal-error")
    def internal_error() -> None:
        raise RuntimeError("secret internal detail")

    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    http_response = client.get("/_phase14/http-error", headers={"X-Request-ID": "http-request"})
    assert http_response.status_code == 409
    assert http_response.json() == {"code": "HTTP_ERROR", "safe_message": "Safe conflict", "request_id": "http-request"}

    internal_response = client.get("/_phase14/internal-error", headers={"X-Request-ID": "internal-request"})
    assert internal_response.status_code == 500
    assert internal_response.json() == {
        "code": "INTERNAL_ERROR",
        "safe_message": "An internal server error occurred",
        "request_id": "internal-request",
    }
    assert "secret internal detail" not in internal_response.text

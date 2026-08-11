from fastapi.testclient import TestClient

from app.main import app


def test_system_readiness_reports_safe_checks() -> None:
    response = TestClient(app).get("/system/readiness")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "config_invariants": {
                "status": "pass",
                "safe_message": "Runtime safety invariants are satisfied",
            },
            "environment": {
                "status": "pass",
                "safe_message": "Environment safety checks passed",
            },
        },
    }

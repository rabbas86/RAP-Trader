from pydantic import BaseModel, ConfigDict


class ApiError(BaseModel):
    """Safe, stable error payload returned by the HTTP API."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: str
    safe_message: str
    request_id: str | None = None

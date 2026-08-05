# API

## `GET /health`

Returns HTTP 200 with service health and the active trading mode.

## `GET /system/status`

Returns application name, environment, trading mode, live-trading flag, and version. Neither endpoint submits orders, and Phase 1 exposes no trading endpoint.

# Local type stubs for agntcy-identity-service-sdk 0.0.7 (no py.typed).

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

class IdentityServiceMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, public_paths: list[str] | None = None) -> None: ...

class IdentityServiceMCPMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None: ...

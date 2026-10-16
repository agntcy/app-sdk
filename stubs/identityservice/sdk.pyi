# Local type stubs for agntcy-identity-service-sdk 0.0.7, which ships no
# py.typed marker. Only the API this repository uses is declared.

from typing import Any

class IdentityServiceSdk:
    def __init__(
        self, api_key: str | None = None, async_mode: bool = False
    ) -> None: ...
    def access_token(
        self,
        resolver_metadata_id: str | None = None,
        tool_name: str | None = None,
    ) -> str | None: ...
    def authorize(self, access_token: str, tool_name: str | None = None) -> Any: ...

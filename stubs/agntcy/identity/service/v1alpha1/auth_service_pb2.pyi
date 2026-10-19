# Local type stubs for agntcy-identity-service-sdk 0.0.7 generated protobuf
# code, which ships no py.typed marker. Only the API this repository uses is
# declared.

class AuthorizeRequest:
    resolver_metadata_id: str
    tool_name: str
    user_token: str
    def __init__(
        self,
        resolver_metadata_id: str | None = ...,
        tool_name: str | None = ...,
        user_token: str | None = ...,
    ) -> None: ...

class TokenRequest:
    authorization_code: str
    def __init__(self, authorization_code: str = ...) -> None: ...

class ExtAuthzRequest:
    access_token: str
    tool_name: str
    def __init__(
        self, access_token: str = ..., tool_name: str | None = ...
    ) -> None: ...

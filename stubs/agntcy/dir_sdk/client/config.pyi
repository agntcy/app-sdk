# Local type stubs for agntcy-dir 1.0.0, which ships no py.typed marker.
# Only the API this repository uses is declared.

class Config:
    server_address: str
    def __init__(
        self,
        server_address: str = ...,
        dirctl_path: str = ...,
        spiffe_socket_path: str = ...,
        auth_mode: str = ...,
        jwt_audience: str = ...,
        tls_ca_file: str = ...,
        tls_cert_file: str = ...,
        tls_key_file: str = ...,
    ) -> None: ...
    @staticmethod
    def load_from_env(env_prefix: str = "DIRECTORY_CLIENT_") -> Config: ...

from agntcy_app_sdk.directory.base import BaseAgentDirectory


class AgntcyAgentDirectory(BaseAgentDirectory):
    def __init__(
        self,
        server_address: str = "localhost:8888",
        dirctl_path: str = "/usr/local/bin/dirctl",
    ):
        pass

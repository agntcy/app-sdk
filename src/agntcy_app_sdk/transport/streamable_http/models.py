# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0
from typing import Protocol, Any


class StreamsContextProtocol(Protocol):
    async def __aenter__(self) -> Any:
        ...

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        ...

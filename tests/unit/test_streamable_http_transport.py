# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import pytest

from agntcy_app_sdk.transport.streamable_http.transport import StreamableHTTPTransport


def test_from_client_is_not_supported() -> None:
    with pytest.raises(NotImplementedError, match="cannot wrap an existing client"):
        StreamableHTTPTransport.from_client(object())

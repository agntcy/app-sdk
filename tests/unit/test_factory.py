# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
import pytest

pytest_plugins = "pytest_asyncio"


@pytest.mark.asyncio
async def test_factory():
    """
    Unit test for the AgntcyFactory and its components.
    """

    factory = AgntcyFactory()

    protocols = factory.registered_protocols()
    transports = factory.registered_transports()
    observability_providers = factory.registered_observability_providers()

    print(f"\nRegistered protocols: {protocols}")
    print(f"Registered transports: {transports}")
    print(f"Registered observability providers: {observability_providers}")

    assert len(protocols) > 0, "No protocols registered in the factory."
    assert len(transports) > 0, "No transports registered in the factory."
    assert len(observability_providers) > 0, "No observability providers registered"

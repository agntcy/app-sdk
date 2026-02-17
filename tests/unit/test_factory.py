# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.semantic.mcp.client_factory import MCPClientFactory
from agntcy_app_sdk.semantic.fast_mcp.client_factory import FastMCPClientFactory
from agntcy_app_sdk.semantic.client_factory_base import BaseClientFactory
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

    assert protocols == [
        "A2A",
        "MCP",
        "FastMCP",
    ], f"Expected ['A2A', 'MCP', 'FastMCP'], got {protocols}"
    assert len(transports) > 0, "No transports registered in the factory."
    assert len(observability_providers) > 0, "No observability providers registered"

    # Test protocol-specific accessors return the correct concrete types
    assert isinstance(factory.a2a(), A2AClientFactory)
    assert isinstance(factory.mcp(), MCPClientFactory)
    assert isinstance(factory.fast_mcp(), FastMCPClientFactory)


@pytest.mark.asyncio
async def test_base_client_factory_protocol():
    """All client factory accessors satisfy the BaseClientFactory protocol."""
    factory = AgntcyFactory()

    assert isinstance(factory.a2a(), BaseClientFactory)
    assert isinstance(factory.mcp(), BaseClientFactory)
    assert isinstance(factory.fast_mcp(), BaseClientFactory)

    # Verify protocol_type returns expected labels
    assert factory.a2a().protocol_type() == "A2A"
    assert factory.mcp().protocol_type() == "MCP"
    assert factory.fast_mcp().protocol_type() == "FastMCP"


@pytest.mark.asyncio
async def test_create_transport_unknown_raises():
    """create_transport raises ValueError for an unknown transport type."""
    factory = AgntcyFactory()

    with pytest.raises(ValueError, match="No transport registered"):
        factory.create_transport("UNKNOWN_TRANSPORT", endpoint="http://localhost:1234")


@pytest.mark.asyncio
async def test_observability_providers_constant():
    """OBSERVABILITY_PROVIDERS class constant is accessible and consistent."""
    assert AgntcyFactory.OBSERVABILITY_PROVIDERS == ["ioa_observe"]

    factory = AgntcyFactory()
    assert factory.registered_observability_providers() == ["ioa_observe"]


@pytest.mark.asyncio
async def test_reexports():
    """Top-level package re-exports work for the primary public API."""
    import agntcy_app_sdk

    assert agntcy_app_sdk.AgntcyFactory is AgntcyFactory
    assert agntcy_app_sdk.A2AClientFactory is A2AClientFactory
    assert agntcy_app_sdk.MCPClientFactory is MCPClientFactory
    assert agntcy_app_sdk.FastMCPClientFactory is FastMCPClientFactory

    # Config re-exports
    from agntcy_app_sdk import (
        AppContainer,
        AppSession,
        ClientConfig,
        NatsTransportConfig,
        SlimRpcConfig,
        SlimTransportConfig,
    )

    assert ClientConfig is not None
    assert SlimTransportConfig is not None
    assert NatsTransportConfig is not None
    assert SlimRpcConfig is not None
    assert AppSession is not None
    assert AppContainer is not None

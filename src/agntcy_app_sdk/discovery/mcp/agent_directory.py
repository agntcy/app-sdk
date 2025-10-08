# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
import asyncio
from typing import Any
import pandas as pd
from a2a.types import (
    AgentCard,
)
from uvicorn import Config, Server
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.message import Message
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

REGISTRY_TOPIC = "mcp_directory.register_agent_record"

factory = AgntcyFactory()


class MCPAgentDirectoryClient(DirectoryBackend):
    def __init__(self, transport: BaseTransport):
        self.transport = transport

    def type(self):
        return "MCPAgentDirectoryClient"

    async def publish_agent_record(self, record: AgentCard):
        request = Message(type="A2ACardRegistry", payload=record.model_dump_json())
        ack = await self.transport.request(REGISTRY_TOPIC, message=request, timeout=10)
        return ack

    async def list_agent_records(self, *args, **kwargs):
        raise NotImplementedError(
            "list is not supported by MCPAgentDirectoryClient, use MCP protocol instead"
        )

    async def search_agent_records(self, query: Any, *args, **kwargs) -> list:
        raise NotImplementedError(
            "search is not supported by MCPAgentDirectoryClient, use MCP protocol instead"
        )

    async def delete_agent_record(self, ref: Any):
        raise NotImplementedError(
            "delete is not supported by MCPAgentDirectoryClient, use MCP protocol instead"
        )


class MCPAgentDirectory(DirectoryBackend):
    """MCP Server for managing and serving agent cards."""

    def __init__(self):
        """Initialize the Agent Cards Server.

        Args:
        """
        mcp = FastMCP("agntcy-mcp-agent-directory")

        @mcp.resource("resource://agent_cards/list", mime_type="application/json")
        def list_agent_cards() -> dict:
            return self.get_agent_cards()

        @mcp.resource(
            "resource://agent_cards/{card_name}", mime_type="application/json"
        )
        def get_card(card_name: str) -> dict:
            return self.get_agent_card(card_name)

        @mcp.tool(
            name="find_agent",
            description="Finds the most relevant agent card based on a natural language query string.",
        )
        def find_agent(query: str) -> str:
            """Finds the most relevant agent card based on a query string."""
            raise NotImplementedError()

        self.mcp = mcp
        self.df = pd.DataFrame()
        self.card_uris = []
        self.agent_cards = []

    async def publish_agent_record(self, record: AgentCard):
        raise NotImplementedError()

    async def list_agent_records(self, *args, **kwargs):
        raise NotImplementedError()

    async def search_agent_records(self, query: Any, *args, **kwargs) -> list:
        raise NotImplementedError()

    async def delete_agent_record(self, ref: Any):
        raise NotImplementedError(
            "delete is not supported by MCPAgentDirectoryClient, use MCP protocol instead"
        )

    def initialize_dataframe(self) -> None:
        self.df = pd.DataFrame({"card_uri": [], "agent_card": []})

    async def register_agent_card(self, msg: Message) -> None:
        """Callback to receive and register agent cards from transport.

        Args:
            msg: Message containing agent card payload.
        """
        try:
            raw_card = json.loads(msg.payload.decode("utf-8"))
            card = AgentCard.model_validate(raw_card)
        except Exception as e:
            logger.error(f"Failed to validate agent card: {e}")
            return

        card_uri = f"resource://agent_cards/{card.name}"
        # Add card to the DataFrame or update existing entry
        if card_uri in self.df["card_uri"].values:
            self.df.loc[self.df["card_uri"] == card_uri, "agent_card"] = card
        else:
            self.df.loc[len(self.df)] = [card_uri, card]

        return Message(type="A2ACardRegistryResponse", payload=b"OK")

    def get_agent_cards(self) -> dict:
        """Retrieves all loaded agent cards as a dictionary.

        This function serves as the handler for the MCP resource identified by
        the URI 'resource://agent_cards/list'.

        Returns:
            A dictionary structured as {'agent_cards': [...]}, where the value is a
            list containing all the loaded agent card URIs.
        """
        resources = {}
        logger.info("Starting read resources")
        resources["agent_cards"] = self.df["card_uri"].to_list()

        print("resources", resources)
        return resources

    def get_agent_card(self, card_name: str) -> dict:
        """Retrieves a specific agent card as a dictionary.

        This function serves as the handler for the MCP resource identified by
        the URI 'resource://agent_cards/{card_name}'.

        Args:
            card_name: The name of the agent card to retrieve.

        Returns:
            A dictionary containing the agent card data.
        """
        resources = {}
        logger.info(f"Starting read resource resource://agent_cards/{card_name}")
        resources["agent_card"] = (
            self.df.loc[
                self.df["card_uri"] == f"resource://agent_cards/{card_name}",
                "agent_card",
            ]
        ).to_list()

        return resources

    async def serve(
        self,
        transport: BaseTransport,
        topic: str,
        blocking: bool = False,
        serve_http: bool = True,
        host: str = None,
        port: int = None,
    ) -> None:
        """Initialize and run the Agent Cards MCP server.

        Args:
            transport: The transport mechanism for the MCP server (e.g., SLIM or NATS).
            blocking: Whether to run in blocking mode.
        """
        # parameter value checks
        if serve_http and (host is None or port is None):
            raise ValueError("Host and port must be provided if serve_http is True")

        logger.info("Starting Agent Cards MCP Server")

        # Initialize data
        self.initialize_dataframe()

        # Setup MCP resources
        # self.setup_mcp_resources()

        logger.info(f"Agent cards MCP Server at @{transport.type()}->{topic}")

        # create a message bridge to register agent records via the transport
        # since mcp does not support posting new resources, this must be separate
        # from main mcp serving bridge
        await transport.setup()
        await transport.subscribe(REGISTRY_TOPIC, callback=self.register_agent_card)

        # create a message bridge to expose the mcp server via the transport
        discovery_bridge = factory.create_bridge(
            self.mcp._mcp_server, transport=transport, topic=topic
        )

        try:
            if serve_http:
                app = self.mcp.streamable_http_app()
                for route in app.routes:
                    print(f"{route.path} ")

                config = Config(
                    app=app,
                    host=host,
                    port=port,
                    loop="asyncio",
                )
                userver = Server(config)
                server_task = asyncio.create_task(userver.serve())

            # Start the bridges
            logger.info(
                f"[start] Starting the bridge:\ndiscovery: {REGISTRY_TOPIC}\nregister: {topic}"
            )
            await discovery_bridge.start(blocking=blocking)
        except Exception as e:
            logger.error(f"error in server: {e}")

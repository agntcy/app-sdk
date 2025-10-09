# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
import asyncio
from uuid import uuid4
from typing import Any, Optional
import pandas as pd
from a2a.types import (
    AgentCard,
)
from uvicorn import Config, Server
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.discovery.directory import BaseAgentDirectory
from agntcy_app_sdk.protocols.message import Message
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

REGISTRY_TOPIC = "register_agent_record"


class MCPAgentDirectory(BaseAgentDirectory):
    """MCP Server for managing and serving agent cards."""

    def __init__(self, directory_name: str, transport: BaseTransport):
        """Initialize the MCP Agent Directory."""
        self.directory_name = directory_name
        self.transport = transport
        self.df = pd.DataFrame()
        self.card_uris = []
        self.agent_cards = []
        self.records = {}
        self.mcp = None
        self.transport_setup = False

    # Abstract method implementations
    #  Store API
    async def push_agent_record(self, record: Any, *args, **kwargs):
        """Push an agent record in the directory."""
        ref = uuid4()
        self.records[ref] = record
        return ref

    async def pull_agent_record(self, ref: Any, *args, **kwargs):
        """Pull an agent record from the directory."""
        if ref in self.records:
            return self.records[ref]
        return None

    async def delete_agent_record(self, ref: Any, *args, **kwargs):
        """Delete an agent record from the directory."""
        if ref in self.records:
            record = self.records[ref]
            del self.records[ref]
            return record
        return None

    async def create_remote_directory_sync(self, remote_dir: Any, *args, **kwargs):
        """Synchronize with a remote remote directory instance"""
        raise NotImplementedError

    async def list_remote_directory_syncs(self):
        """List all remote directory syncs."""
        raise NotImplementedError

    async def delete_remote_directory_sync(self, remote_dir: Any, *args, **kwargs):
        """Delete a remote directory sync."""
        raise NotImplementedError

    async def list_agent_records(self, *args, **kwargs) -> list:
        """List all agent records in the directory."""
        return list(self.records.values())

    async def search_agent_records(self, query: Any, *args, **kwargs) -> list:
        """Search for agent records matching the query."""
        raise NotImplementedError

    async def publish_agent_record(
        self, record_ref: Any, remote_dir: Optional[str] = None, *args, **kwargs
    ):
        """Publish an agent record to the directory."""
        record = self.pull_agent_record(ref=record_ref)
        if record is None:
            raise ValueError("Agent record not found in local store, push record first")

        if not remote_dir and not self.mcp:
            raise ValueError(
                "No remote directory specified and MCP server not initialized"
            )

        if self.mcp:
            # publish to this mcp server
            raise NotImplementedError
        else:
            # publish to a remote mcp server
            remote_dir_registry_topic = f"{remote_dir}.{REGISTRY_TOPIC}"
            if not self.transport_setup:
                await self.transport.setup()
                self.transport_setup = True
            try:
                request = Message(
                    type="A2ACardRegistry", payload=record.model_dump_json()
                )
                await self.transport.request(
                    remote_dir_registry_topic, message=request, timeout=10
                )
            except Exception as e:
                logger.error(f"Failed to publish agent record: {e}")
                raise

    async def unpublish_agent_record(
        self, record_ref: Any, remote_dir: Optional[str] = None, *args, **kwargs
    ):
        """Unpublish an agent record from the directory."""
        raise NotImplementedError

    async def sign_agent_record(self, record_ref: Any, provider: Any, *args, **kwargs):
        """Sign an agent record with a given key, oidc"""
        raise NotImplementedError

    async def verify_agent_record(self, record_ref: Any):
        """Verify signature"""
        raise NotImplementedError

    def initialize_record_store(self) -> None:
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

    async def run_directory_server(
        self,
        discovery_topic: str,
        blocking: bool = False,
        serve_http: bool = True,
        host: str = None,
        port: int = None,
    ) -> None:
        """
        The MCPAgentDirectory can be used as just a local in-memory directory, or as a full MCP server.
        This function enables the latter, enabling publishing and serving agent cards via MCP.

        Args:
            blocking: Whether to run in blocking mode.
        """
        # parameter value checks
        if serve_http and (host is None or port is None):
            raise ValueError("Host and port must be provided if serve_http is True")

        logger.info("Starting Agent Cards MCP Server")

        # Initialize data
        self.initialize_record_store()

        # Initialize the mcp server and mcp resources/tools
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

        logger.info(
            f"MCPAgentDirectory running @{self.transport.type()}->{discovery_topic}"
        )

        # create a message bridge to register agent records via the transport
        # since mcp does not support posting new resources, this must be separate
        # from main mcp serving bridge
        if not self.transport_setup:
            await self.transport.setup()
            self.transport_setup = True
        directory_inbox = f"{self.directory_name}.{REGISTRY_TOPIC}"
        await self.transport.subscribe(
            directory_inbox, callback=self.register_agent_card
        )

        # create a message bridge to expose the mcp server via the transport
        from agntcy_app_sdk.factory import AgntcyFactory

        factory = AgntcyFactory()
        discovery_bridge = factory.create_bridge(
            self.mcp._mcp_server, transport=self.transport, topic=discovery_topic
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
                _ = asyncio.create_task(userver.serve())

            # Start the bridges
            logger.info(
                f"[start] Starting the bridge:\ndiscovery: {directory_inbox}\nregister: {discovery_topic}"
            )
            await discovery_bridge.start(blocking=blocking)
        except Exception as e:
            logger.error(f"error in server: {e}")

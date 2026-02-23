# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Concrete ``BaseAgentDirectory`` implementation backed by the agntcy-dir SDK."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from a2a.types import AgentCard
from agntcy.dir_sdk.client.client import Client
from agntcy.dir_sdk.client.config import Config
from agntcy.dir_sdk.models import core_v1, search_v1
from google.protobuf.json_format import MessageToDict, ParseDict

from agntcy_app_sdk.directory.base import BaseAgentDirectory, RecordVisibility
from agntcy_app_sdk.directory.oasf_converter import (
    agent_card_to_oasf,
    oasf_to_agent_card,
)

logger = logging.getLogger(__name__)


class AgentDirectory(BaseAgentDirectory):
    """Agent directory backed by the agntcy-dir gRPC service.

    All gRPC calls are synchronous in the underlying SDK, so every call is
    wrapped with ``asyncio.to_thread`` to avoid blocking the event loop.
    """

    DIRECTORY_TYPE: str = "agntcy"

    @classmethod
    def from_config(
        cls, endpoint: str | None = None, **kwargs: Any
    ) -> "AgentDirectory":
        """Create an ``AgentDirectory`` from configuration parameters.

        Args:
            endpoint: Directory service gRPC address
                (e.g. ``"127.0.0.1:8888"``). When ``None`` the
                ``Config`` default is used.
            **kwargs: Reserved for future ``Config`` fields.
        """
        config = Config(server_address=endpoint) if endpoint else Config()
        return cls(config=config)

    def __init__(self, config: Optional[Config] = None) -> None:
        self._config = config or Config()
        self._client: Optional[Client] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Create the underlying gRPC client (idempotent)."""
        if self._client is not None:
            return
        self._client = await asyncio.to_thread(Client, self._config)
        logger.info(
            "AgentDirectory client connected to %s", self._config.server_address
        )

    async def teardown(self) -> None:
        """Release the client reference."""
        self._client = None
        logger.info("AgentDirectory client disconnected.")

    def _ensure_client(self) -> Client:
        """Return the live client or raise."""
        if self._client is None:
            raise RuntimeError("AgentDirectory is not connected. Call setup() first.")
        return self._client

    # ------------------------------------------------------------------
    # Store API
    # ------------------------------------------------------------------

    async def push_agent_record(
        self,
        record: Any,
        visibility: RecordVisibility = RecordVisibility.PUBLIC,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """Push an agent record and return its CID.

        *record* may be:
        - an ``AgentCard`` — automatically converted to OASF via
          :func:`agent_card_to_oasf`.
        - a ``dict`` — used as-is (assumed to be a valid OASF dict).

        Returns the content-identifier (CID) string of the stored record.
        """
        client = self._ensure_client()

        if isinstance(record, AgentCard):
            oasf_dict = agent_card_to_oasf(record)
        elif isinstance(record, dict):
            oasf_dict = record
        else:
            raise TypeError(
                f"Unsupported record type: {type(record).__name__}. "
                "Expected AgentCard or dict."
            )

        proto_record = core_v1.Record()
        ParseDict(oasf_dict, proto_record.data)

        refs: list[core_v1.RecordRef] = await asyncio.to_thread(
            client.push, [proto_record]
        )
        cid = refs[0].cid
        logger.info("Pushed record with CID %s", cid)
        return cid

    async def pull_agent_record(
        self,
        ref: Any,
        *args: Any,
        extract_card: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any] | AgentCard | None:
        """Pull a record by CID.

        Parameters
        ----------
        ref:
            A CID string or a ``core_v1.RecordRef`` instance.
        extract_card:
            When ``True``, attempt to extract an ``AgentCard`` from the OASF
            envelope.  If no A2A module is found the raw OASF dict is returned.

        Returns ``None`` when the directory returns no results.
        """
        client = self._ensure_client()

        if isinstance(ref, str):
            record_ref = core_v1.RecordRef(cid=ref)
        else:
            record_ref = ref

        records: list[core_v1.Record] = await asyncio.to_thread(
            client.pull, [record_ref]
        )
        if not records:
            return None

        oasf_dict: dict[str, Any] = MessageToDict(
            records[0].data, preserving_proto_field_name=True
        )

        if extract_card:
            card = oasf_to_agent_card(oasf_dict)
            if card is not None:
                return card

        return oasf_dict

    async def search_agent_records(
        self,
        query: Any,
        limit: int = 10,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Search for agent records.

        Parameters
        ----------
        query:
            A plain string (treated as a name query) or a fully-formed
            ``search_v1.SearchRecordsRequest``.
        limit:
            Maximum number of results (used when *query* is a string).
        """
        client = self._ensure_client()

        if isinstance(query, str):
            request = search_v1.SearchRecordsRequest(
                queries=[
                    search_v1.RecordQuery(
                        type=search_v1.RecordQueryType.RECORD_QUERY_TYPE_NAME,
                        value=query,
                    )
                ],
                limit=limit,
            )
        elif isinstance(query, search_v1.SearchRecordsRequest):
            request = query
        else:
            raise TypeError(
                f"Unsupported query type: {type(query).__name__}. "
                "Expected str or SearchRecordsRequest."
            )

        responses: list[search_v1.SearchRecordsResponse] = await asyncio.to_thread(
            client.search_records, request
        )

        results: list[dict[str, Any]] = []
        for resp in responses:
            oasf_dict = MessageToDict(
                resp.record.data, preserving_proto_field_name=True
            )
            results.append(oasf_dict)
        return results

    # ------------------------------------------------------------------
    # Stub methods (not yet implemented)
    # ------------------------------------------------------------------

    async def delete_agent_record(self, ref: Any, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("delete_agent_record is not yet implemented.")

    async def list_agent_records(self, *args: Any, **kwargs: Any) -> list:
        raise NotImplementedError("list_agent_records is not yet implemented.")

    async def sign_agent_record(
        self, record_ref: Any, provider: Any, *args: Any, **kwargs: Any
    ) -> None:
        raise NotImplementedError("sign_agent_record is not yet implemented.")

    async def verify_agent_record(self, record_ref: Any) -> None:
        raise NotImplementedError("verify_agent_record is not yet implemented.")

    async def get_record_visibility(self, ref: Any, *args: Any, **kwargs: Any) -> bool:
        raise NotImplementedError("get_record_visibility is not yet implemented.")

    async def set_record_visibility(
        self, ref: Any, visibility: RecordVisibility, *args: Any, **kwargs: Any
    ) -> bool:
        raise NotImplementedError("set_record_visibility is not yet implemented.")

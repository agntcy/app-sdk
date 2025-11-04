# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, List
from agntcy.dir_sdk.models import core_v1


class RecordVisibility(Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    PROTECTED = "protected"


class BaseAgentDirectory(ABC):
    """
    High level interface for storing, retrieving, searching, and signing agent records.
    """

    ###########################################################################
    #  Store API
    @abstractmethod
    async def push_agent_record(
        self,
        record: core_v1.Record,
        visibility: RecordVisibility = RecordVisibility.PUBLIC,
        *args,
        **kwargs,
    ):
        """Push an agent record in the directory."""
        pass

    @abstractmethod
    async def pull_agent_record(
        self, ref: str | core_v1.RecordRef, *args, **kwargs
    ) -> core_v1.Record:
        """Pull an agent record from the directory."""
        pass

    @abstractmethod
    async def delete_agent_record(self, ref: str | core_v1.RecordRef, *args, **kwargs):
        """Delete an agent record from the directory."""
        pass

    ###########################################################################
    # Search API
    @abstractmethod
    async def list_agent_records(self, *args, **kwargs) -> List[core_v1.Record]:
        """List all agent records in the directory."""
        pass

    @abstractmethod
    async def search_agent_records(
        self, query: Any, limit: int = 1, *args, **kwargs
    ) -> List[core_v1.Record]:
        """Search for agent records matching the query."""
        pass

    ###########################################################################
    # Signing and Verification API
    @abstractmethod
    async def sign_agent_record(
        self, record_ref: str | core_v1.RecordRef, provider: Any, *args, **kwargs
    ):
        """Sign an agent record with a given key, oidc"""
        pass

    @abstractmethod
    async def verify_agent_record(self, record_ref: str | core_v1.RecordRef):
        """Verify signature"""
        pass

    ###########################################################################
    # Publishing API
    @abstractmethod
    async def get_record_visibility(
        self, ref: str | core_v1.RecordRef, *args, **kwargs
    ) -> bool:
        """Check if an agent record is publicly visible."""
        pass

    @abstractmethod
    async def set_record_visibility(
        self,
        ref: str | core_v1.RecordRef,
        visibility: RecordVisibility,
        *args,
        **kwargs,
    ) -> bool:
        """Check if an agent record is publicly visible."""
        pass

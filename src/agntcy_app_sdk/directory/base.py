# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class RecordVisibility(Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    PROTECTED = "protected"


class BaseAgentDirectory(ABC):
    """
    High level interface for storing, retrieving, searching, and publishing agent records.
    """

    ###########################################################################
    #  Store API
    @abstractmethod
    async def push_agent_record(
        self,
        record: Any,
        visibility: RecordVisibility = RecordVisibility.PUBLIC,
        *args,
        **kwargs,
    ):
        """Push an agent record in the directory."""
        pass

    @abstractmethod
    async def pull_agent_record(self, ref: Any, *args, **kwargs):
        """Pull an agent record from the directory."""
        pass

    @abstractmethod
    async def delete_agent_record(self, ref: Any, *args, **kwargs):
        """Delete an agent record from the directory."""
        pass

    ###########################################################################
    # Search API
    @abstractmethod
    async def list_agent_records(self, *args, **kwargs) -> list:
        """List all agent records in the directory."""
        pass

    @abstractmethod
    async def search_agent_records(self, query: Any, *args, **kwargs) -> list:
        """Search for agent records matching the query."""
        pass

    ###########################################################################
    # Signing and Verification API
    @abstractmethod
    async def sign_agent_record(self, record_ref: Any, provider: Any, *args, **kwargs):
        """Sign an agent record with a given key, oidc"""
        pass

    @abstractmethod
    async def verify_agent_record(self, record_ref: Any):
        """Verify signature"""
        pass

    ###########################################################################
    # Publishing API
    @abstractmethod
    async def get_record_visibility(self, ref: Any, *args, **kwargs) -> bool:
        """Check if an agent record is publicly visible."""
        pass

    @abstractmethod
    async def set_record_visibility(
        self, ref: Any, visibility: RecordVisibility, *args, **kwargs
    ) -> bool:
        """Check if an agent record is publicly visible."""
        pass

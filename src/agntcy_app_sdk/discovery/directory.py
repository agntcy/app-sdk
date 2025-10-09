# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from typing import Any


class BaseAgentDirectory(ABC):
    """
    High level interface for storing, retrieving, searching, and publishing agent records.
    """

    ###########################################################################
    #  Store API
    @abstractmethod
    async def push_agent_record(self, record: Any, *args, **kwargs):
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

    @abstractmethod
    async def create_remote_directory_sync(self, remote_dir: Any, *args, **kwargs):
        """Synchronize with a remote remote directory instance"""
        pass

    @abstractmethod
    async def list_remote_directory_syncs(self):
        """List all remote directory syncs."""
        pass

    @abstractmethod
    async def delete_remote_directory_sync(self, remote_dir: Any, *args, **kwargs):
        """Delete a remote directory sync."""
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
    # Publishing API
    @abstractmethod
    async def publish_agent_record(self, record_ref: Any, *args, **kwargs):
        """Publish an agent record to the directory."""
        pass

    @abstractmethod
    async def unpublish_agent_record(self, record_ref: Any, *args, **kwargs):
        """Unpublish an agent record from the directory."""
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

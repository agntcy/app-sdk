# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any
from agntcy_app_sdk.directory.base import BaseAgentDirectory, RecordVisibility
from agntcy.dir_sdk.client import Config, Client
from agntcy.dir_sdk.models import core_v1, search_v1, routing_v1
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


class AgntcyAgentDirectory(BaseAgentDirectory):
    """
    Implementation of the BaseAgentDirectory using the AGNTCY Dir SDK.

    We leverage the AGNTCY Dir SDK to provide a seamless integration with the Directory API.
    """

    def __init__(
        self,
        server_address: str = "localhost:8888",
        dirctl_path: str = "/usr/local/bin/dirctl",
        spiffe_socket_path: str = None,  # "/tmp/agent.sock",
        auth_mode: str = None,  # x509 | jwt
        jwt_audience: str = None,  # "spiffe://example.org/dir-server"
    ):
        # Initialize the Dir SDK client
        if auth_mode == "x509":
            config = Config(
                server_address=server_address,
                dirctl_path=dirctl_path,
                spiffe_socket_path=spiffe_socket_path,
                auth_mode=auth_mode,
            )
            logger.info("Using X.509 authentication with SPIRE")
        elif auth_mode == "jwt":
            config = Config(
                server_address=server_address,
                dirctl_path=dirctl_path,
                spiffe_socket_path=spiffe_socket_path,
                auth_mode=auth_mode,
                jwt_audience=jwt_audience,
            )
            logger.info("Using JWT authentication with SPIRE")
        else:
            logger.info("No authentication mode specified, defaulting to no auth.")
            config = Config(
                server_address=server_address,
                dirctl_path=dirctl_path,
            )

        self.client = Client(config)

    async def push_agent_record(
        self,
        record: core_v1.Record,
        visibility: RecordVisibility = RecordVisibility.PRIVATE,
        *args,
        **kwargs,
    ) -> str:
        """Push an agent record in the directory."""

        # push objects to the store
        try:
            record_refs = self.client.push([record])
        except Exception as e:
            logger.error("Error pushing record: %s", str(e))
            return None

        logger.info("Pushed record refs: %s", record_refs)

        # if visibility is public, publish
        if visibility == RecordVisibility.PUBLIC:
            routing_record_refs = routing_v1.RecordRefs(refs=[record_refs[0]])
            publish_request = routing_v1.PublishRequest(record_refs=routing_record_refs)

            try:
                self.client.publish(publish_request)
            except Exception as e:
                logger.error("Error publishing record: %s", str(e))

        return record_refs[0]

    async def pull_agent_record(
        self, ref: str | core_v1.RecordRef, *args, **kwargs
    ) -> core_v1.Record:
        """Pull an agent record from the directory."""
        if isinstance(ref, str):
            ref = core_v1.RecordRef(cid=ref)
        try:
            pulled_records = self.client.pull([ref])
        except Exception as e:
            logger.error("Error pulling record with ref %s: %s", ref, str(e))
            return None

        return pulled_records[0] if pulled_records else None

    async def delete_agent_record(self, ref: str | core_v1.RecordRef, *args, **kwargs):
        """Delete an agent record from the directory."""
        if isinstance(ref, str):
            ref = core_v1.RecordRef(cid=ref)

        try:
            self.client.delete([ref])
        except Exception as e:
            logger.error("Error deleting record with ref %s: %s", ref, str(e))

        logger.info("Deleted record with ref: %s", ref)

    ###########################################################################
    # Search API

    async def list_agent_records(
        self,
        list_query_type: routing_v1.RecordQueryType = None,
        list_query: str = None,
        limit: int = 10,
        *args,
        **kwargs,
    ) -> list:
        """List all agent records in the directory."""

        if list_query_type and list_query:
            query = routing_v1.RecordQuery(
                type=list_query_type,  # routing_v1.RECORD_QUERY_TYPE_SKILL,
                value=list_query,  # "/skills/Natural Language Processing/Text Completion",
            )
            list_request = routing_v1.ListRequest(queries=[query], limit=limit)
        else:
            list_request = routing_v1.ListRequest(limit=limit)

        objects = list(self.client.list(list_request))
        return objects

    async def search_agent_records(
        self, query: Any, limit: int = 1, *args, **kwargs
    ) -> list:
        """Search for agent records matching the query."""
        # Search objects
        search_query = search_v1.RecordQuery(
            type=search_v1.RECORD_QUERY_TYPE_SKILL_ID,
            value="1",
        )

        search_request = search_v1.SearchRequest(queries=[search_query], limit=3)
        objects = list(self.client.search(search_request))
        return objects

    ###########################################################################
    # Signing and Verification API

    async def sign_agent_record(
        self, record_ref: str | core_v1.RecordRef, provider: Any, *args, **kwargs
    ):
        """Sign an agent record with a given key, oidc"""
        raise NotImplementedError("sign_agent_record is not implemented yet.")

    async def verify_agent_record(self, ref: str | core_v1.RecordRef):
        """Verify signature"""
        raise NotImplementedError("verify_agent_record is not implemented yet.")

    ###########################################################################
    # Publishing API

    async def get_record_visibility(
        self, ref: str | core_v1.RecordRef, *args, **kwargs
    ) -> bool:
        """Check if an agent record is publicly visible."""
        raise NotImplementedError("get_record_visibility is not implemented yet.")

    async def set_record_visibility(
        self,
        ref: str | core_v1.RecordRef,
        visibility: RecordVisibility,
        *args,
        **kwargs,
    ) -> bool:
        """Check if an agent record is publicly visible."""

        if isinstance(ref, str):
            ref = core_v1.RecordRef(cid=ref)

        # first pull the record
        records = self.client.pull([ref])
        if not records:
            return False

        if visibility == RecordVisibility.PUBLIC:
            # publish the record
            publish_request = routing_v1.PublishRequest(record_refs=[ref])
            self.client.publish(publish_request)
        elif visibility == RecordVisibility.PRIVATE:
            # unpublish the record
            unpublish_request = routing_v1.UnpublishRequest(record_refs=[ref])
            self.client.unpublish(unpublish_request)
        elif visibility == RecordVisibility.PROTECTED:
            # currently not supported
            raise NotImplementedError("Protected visibility is not supported yet.")

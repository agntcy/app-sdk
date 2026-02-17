# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import abstractmethod
from typing import Any, Optional

from a2a.types import AgentCard

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.base import ServerHandler

logger = get_logger(__name__)


class BaseA2AServerHandler(ServerHandler):
    """Shared base for all A2A server handlers.

    Provides:
    - ``protocol_type()`` → ``"A2A"``
    - ``get_agent_record()`` → the ``AgentCard``
    - ``_set_preferred_transport(name)`` — stamps the agent card
    """

    def protocol_type(self) -> str:
        return "A2A"

    @property
    @abstractmethod
    def agent_card(self) -> AgentCard:
        """Return the AgentCard managed by this handler."""
        ...

    def get_agent_record(self) -> Optional[Any]:
        """Return the agent card as the directory record."""
        return self.agent_card

    def _set_preferred_transport(self, name: str) -> None:
        """Set ``preferred_transport`` on the agent card.

        Logs a warning when overriding a value that was already set to
        something other than the default ``"JSONRPC"``.
        """
        card = self.agent_card
        current = card.preferred_transport
        if current is not None and current != "JSONRPC" and current != name:
            logger.warning(
                "Overriding agent card preferred_transport "
                f"from '{current}' to '{name}'"
            )
        card.preferred_transport = name
        logger.info(f"Agent card preferred_transport set to '{name}'")

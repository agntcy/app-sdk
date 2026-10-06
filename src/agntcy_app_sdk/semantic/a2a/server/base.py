# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import abstractmethod
from typing import Any, Optional

from a2a.types import AgentCard, AgentInterface

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.card_utils import add_interface, find_interface
from agntcy_app_sdk.semantic.base import ServerHandler

logger = get_logger(__name__)


class BaseA2AServerHandler(ServerHandler):
    """Shared base for all A2A server handlers.

    Provides:
    - ``protocol_type()`` → ``"A2A"``
    - ``get_agent_record()`` → the ``AgentCard``
    - ``_declare_interface(transport, url)`` — ensures the agent card lists
      the transport this handler serves in ``supported_interfaces``
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

    def _declare_interface(
        self,
        transport: str,
        url: str,
        *,
        prefer: bool = False,
    ) -> AgentInterface:
        """Ensure the agent card declares an interface for *transport*.

        a2a-sdk 1.x expresses transports as the ordered
        ``AgentCard.supported_interfaces`` list.  If the agent author already
        declared an interface for this transport it is left completely
        untouched — neither its URL (which may carry an explicit endpoint) nor
        its position (the author's preference order) is changed.  Otherwise a
        new entry with *url* is appended — or prepended when ``prefer=True`` so
        it becomes the card's preferred transport.
        """
        card = self.agent_card
        existing = find_interface(card, transport)
        if existing is not None:
            return existing

        iface = add_interface(card, transport, url, prefer=prefer)
        logger.info(
            f"Agent card now declares '{iface.protocol_binding}' interface "
            f"at '{iface.url}'"
        )
        return iface

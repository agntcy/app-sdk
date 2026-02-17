# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Protocol, runtime_checkable


@runtime_checkable
class BaseClientFactory(Protocol):
    """Structural protocol that all client factories satisfy.

    This is a :pep:`544` ``Protocol`` — concrete factories do **not** need to
    inherit from it.  The ``@runtime_checkable`` decorator enables
    ``isinstance()`` checks at runtime::

        assert isinstance(factory.mcp(), BaseClientFactory)
    """

    def protocol_type(self) -> str:
        """Return a human-readable label for the protocol (e.g. ``"A2A"``)."""
        ...

# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for :func:`agntcy_app_sdk.transport.base.accepts_publish_fn`."""

import functools
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.base import accepts_publish_fn

PublishFn = Callable[[Message], Awaitable[None]]


async def _plain(message: Message) -> None:
    return None


async def _keyword(message: Message, publish_fn: PublishFn | None = None) -> None:
    return None


async def _keyword_only(
    message: Message, *, publish_fn: PublishFn | None = None
) -> None:
    return None


async def _var_keyword(message: Message, **kwargs: Any) -> None:
    return None


class _Handler:
    async def method(
        self, message: Message, *, publish_fn: PublishFn | None = None
    ) -> None:
        return None

    async def __call__(self, message: Message) -> None:
        return None


@functools.wraps(_keyword)
async def _wraps_message_only(message: Message) -> None:
    await _keyword(message)


@functools.wraps(_plain)
async def _wraps_forwarding_all(*args: Any, **kwargs: Any) -> None:
    await _keyword(*args, **kwargs)


@pytest.mark.parametrize(
    ("callback", "expected"),
    [
        (_plain, False),
        (_keyword, True),
        (_keyword_only, True),
        (_var_keyword, True),
        (_Handler().method, True),
        (_Handler(), False),
        (functools.partial(_keyword_only), True),
        (_wraps_message_only, False),
        (_wraps_forwarding_all, True),
        (AsyncMock(), True),
    ],
    ids=[
        "plain",
        "keyword",
        "keyword-only",
        "var-keyword",
        "bound-method",
        "callable-object",
        "partial",
        "wraps-message-only",
        "wraps-forwarding-all",
        "async-mock",
    ],
)
def test_accepts_publish_fn(
    callback: Callable[..., Awaitable[Any]], expected: bool
) -> None:
    assert accepts_publish_fn(callback) is expected

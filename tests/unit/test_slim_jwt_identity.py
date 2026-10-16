# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for :func:`jwt_identity` (SLIM JWT provider/verifier configs)."""

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import slim_bindings

from agntcy_app_sdk.transport.slim import common
from agntcy_app_sdk.transport.slim.common import jwt_identity

pytest_plugins = "pytest_asyncio"

_JWKS = '{"keys": []}'


def _write_files(tmp_path: Path, jwk_content: str) -> tuple[str, str]:
    jwt_path = tmp_path / "token.jwt"
    jwt_path.write_text("dummy.jwt.token")
    jwk_path = tmp_path / "jwks.json"
    jwk_path.write_text(jwk_content)
    return str(jwt_path), str(jwk_path)


def test_decoding_key_is_the_decoded_jwks_text(tmp_path: Path) -> None:
    encoded = base64.b64encode(_JWKS.encode("utf-8")).decode("ascii")
    jwt_path, jwk_path = _write_files(tmp_path, json.dumps({"k": encoded}))

    _, verifier = jwt_identity(jwt_path, jwk_path, "org/ns/app")

    assert isinstance(verifier, slim_bindings.IdentityVerifierConfig.JWT)
    key_type = verifier.config.key
    assert isinstance(key_type, slim_bindings.JwtKeyType.DECODING)
    key_data = key_type.key.key
    assert isinstance(key_data, slim_bindings.JwtKeyData.DATA)
    assert key_data.value == _JWKS


def test_empty_jwk_file_raises_value_error(tmp_path: Path) -> None:
    jwt_path, jwk_path = _write_files(tmp_path, "{}")

    with pytest.raises(ValueError, match="contains no keys"):
        jwt_identity(jwt_path, jwk_path, "org/ns/app")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("jwt", "bundle", "shared_secret", "match"),
    [
        ("token.jwt", None, "s" * 32, "got only jwt"),
        (None, "jwks.json", "s" * 32, "got only bundle"),
        (None, None, None, "or a shared secret"),
    ],
)
async def test_incomplete_identity_is_rejected(
    jwt: str | None, bundle: str | None, shared_secret: str | None, match: str
) -> None:
    service = MagicMock()
    with (
        patch.object(common, "global_slim", None),
        patch.object(common, "global_slim_service", None),
        patch.object(common, "get_or_init_slim_service", return_value=service),
        patch.object(common, "new_client_config", MagicMock()),
    ):
        with pytest.raises(ValueError, match=match):
            await common.get_or_create_slim_instance(
                MagicMock(),
                "https://slim.example:443",
                slim_insecure_client=False,
                shared_secret=shared_secret,
                jwt=jwt,
                bundle=bundle,
            )

    service.create_app.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shared_secret", "warned"),
    [(common.PLACEHOLDER_SHARED_SECRET, True), ("s" * 32, False)],
    ids=["placeholder", "own-secret"],
)
async def test_placeholder_shared_secret_logs_a_warning(
    shared_secret: str, warned: bool
) -> None:
    service = MagicMock()
    service.connect_async = AsyncMock(return_value=1)
    service.create_app.return_value.subscribe_async = AsyncMock()
    with (
        patch.object(common, "global_slim", None),
        patch.object(common, "global_slim_service", None),
        patch.object(common, "global_connection_id", None),
        patch.object(common, "global_slim_insecure", None),
        patch.object(common, "global_slim_endpoint", None),
        patch.object(common, "get_or_init_slim_service", return_value=service),
        patch.object(common, "new_client_config", MagicMock()),
        patch.object(common, "shared_secret_identity", return_value=(None, None)),
        patch.object(common, "logger") as log,
    ):
        await common.get_or_create_slim_instance(
            MagicMock(),
            "http://localhost:46357",
            slim_insecure_client=True,
            shared_secret=shared_secret,
        )

    warnings = [c for c in log.warning.call_args_list if "placeholder" in c.args[0]]
    assert bool(warnings) is warned
    if warned:
        advice = warnings[0].args[0]
        assert "shared_secret_identity" in advice
        assert ".with_shared_secret()" in advice
        assert "SLIM_SHARED_SECRET" in advice

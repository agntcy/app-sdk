# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import os


def is_identity_auth_enabled() -> bool:
    """Check if identity authentication is enabled based on environment variables."""

    return (
        os.getenv("IDENTITY_AUTH_ENABLED", "false").lower() in ["true", "enabled"]
        and os.getenv("IDENTITY_SERVICE_API_KEY", "") != ""
    )

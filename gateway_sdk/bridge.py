# Copyright 2025 Cisco Systems, Inc. and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0


# define utilities for onloading and offloading gateway messages to frameworks / protocols

def fastapi_bridge(
    app: FastAPI,
    settings: APISettings,
    gateway: Optional[Gateway] = None,
) -> None:
    
    # set fastapi app for each gateway type
    pass


async def agp_connect(app: FastAPI, settings: APISettings):
    """
    Attempts to connect to the AGP Gateway, logs errors but does not raise.
    This ensures the REST server remains available even if AGP fails.
    """
    try:
        AGPConfig.gateway_container.set_config(
            endpoint=settings.AGP_GATEWAY_ENDPOINT, insecure=True
        )
        AGPConfig.gateway_container.set_fastapi_app(app)

        _ = await AGPConfig.gateway_container.connect_with_retry(
            agent_container=AGPConfig.agent_container,
            max_duration=10,
            initial_delay=1,
            remote_agent="server",  # Connect to the server agent
        )

        await AGPConfig.gateway_container.start_server(
            agent_container=AGPConfig.agent_container
        )
        logger.info("AGP client connected and running.")
    except RuntimeError as e:
        logger.error("AGP RuntimeError: %s", e)
    except Exception as e:
        logger.error("AGP client connection failed: %s. Continuing without AGP.", e)
        
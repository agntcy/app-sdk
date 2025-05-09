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

from ..logging_config import configure_logging, get_logger
from python_a2a import A2AClient
from python_a2a import A2AServer

configure_logging()
logger = get_logger(__name__)

def create_client(url, transport=None, auth=None):
    """
    Create an A2A client, passing in the transport and authentication details.
    """

    # Create an A2A client
    a2a_client = A2AClient(url)
    
    return a2a_client

def create_receiver():
    """
    A receiver should connect to a gateway and then offload messages to A2A agents
    """
    pass
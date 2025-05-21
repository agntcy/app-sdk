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

from traceloop.sdk import Traceloop
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.propagate import set_global_textmap
from os import environ


def init_tracing(
    app_name: str, collector_url="http://localhost:4318", disable_batch: bool = True
) -> None:
    """
    Initialize Traceloop for observability.
    This function sets up the Traceloop SDK for tracing and observability.
    Args:
        app_name (str): The name of the application for which tracing is being initialized.
        disable_batch (bool): If True, disables batch processing of traces. Defaults to True.
    """

    # Initialize Traceloop for observability
    environ["TRACELOOP_BASE_URL"] = collector_url
    Traceloop.init(app_name=app_name, disable_batch=disable_batch)

    # Set up propagators to extract context from incoming requests
    set_global_textmap(TraceContextTextMapPropagator())

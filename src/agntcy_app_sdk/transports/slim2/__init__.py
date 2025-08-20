# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
A2A over SLIM 2.0 Transport

Production-ready transport implementation that bridges A2A protocol 
over SLIM v0.4.0 messaging infrastructure with robust error handling,
session management, and performance optimizations.
"""

from .transport import SLIM2Transport

__all__ = ["SLIM2Transport"]

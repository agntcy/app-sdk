# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Structured logging for agntcy-app-sdk.

The SDK uses structlog throughout.  Application code may optionally call
``configure_logging()`` at startup to control output format; if it doesn't,
structlog's defaults produce reasonable output.

Environment Variables
---------------------
LOG_LEVEL          Root log level (default: INFO)
LOG_FORMATTER      "json" or "colored" (default: colored)
LOG_TO_FILE        "1" to also write JSON logs to logs/application.log
"""

from __future__ import annotations

import logging
import logging.config
import os
from pathlib import Path

import structlog

__all__: list[str] = ["configure_logging", "get_logger"]

_configured = False


def configure_logging() -> None:
    """Configure structlog + stdlib logging from environment variables.

    Safe to call multiple times (idempotent).  Applications should call
    this once at startup.  If never called, structlog uses safe defaults.
    """
    global _configured  # noqa: PLW0603
    if _configured:
        return
    _configured = True

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    formatter = os.getenv("LOG_FORMATTER", "colored").lower()
    log_to_file = os.getenv("LOG_TO_FILE", "0") == "1"

    # Shared processors for structlog pipeline
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    # Foreign pre-chain for stdlib loggers routed through ProcessorFormatter.
    # Excludes filter_by_level because foreign log records may carry a None
    # logger reference, and stdlib already applies level filtering before
    # the record reaches the formatter.
    foreign_pre_chain: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=shared_processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Choose renderer
    if formatter == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # stdlib dictConfig
    formatters = {
        "structlog": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": renderer,
            "foreign_pre_chain": foreign_pre_chain,
        },
    }

    handlers: dict[str, dict[str, object]] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "structlog",
            "level": log_level,
        },
    }

    if log_to_file:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.FileHandler",
            "filename": str(log_dir / "application.log"),
            "formatter": "structlog",
            "level": log_level,
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": formatters,
            "handlers": handlers,
            "loggers": {
                "uvicorn": {
                    "handlers": list(handlers),
                    "level": log_level,
                    "propagate": False,
                },
            },
            "root": {
                "handlers": list(handlers),
                "level": log_level,
            },
        }
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, optionally bound to *name*."""
    if not _configured:
        configure_logging()
    return structlog.get_logger(name or "agntcy_app_sdk")

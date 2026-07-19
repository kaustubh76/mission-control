"""
Logging — text for humans, JSON for shippers.

`get_logger()` is the legacy entry point: text formatter to stdout +
WARNING+ to data/logs/scanner.log. Unchanged.

`get_json_logger()` writes one JSON object per record to data/logs/
{name}.json.log. Each record carries pair / signal_id / strategy
context as structured fields, ready for ingestion by Loki / ELK /
DataDog without a regex parser.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from ictbot.settings import LOGS_DIR

LOG_FILE = LOGS_DIR / "scanner.log"


def get_logger(name: str = "ict") -> logging.Logger:
    """Plain-text logger. Kept for backwards compat."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(logging.INFO)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    fileh = logging.FileHandler(LOG_FILE)
    fileh.setLevel(logging.WARNING)
    fileh.setFormatter(fmt)
    logger.addHandler(fileh)

    return logger


# -- JSON logger -------------------------------------------------------------


_RESERVED_LOGRECORD_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class JSONFormatter(logging.Formatter):
    """Render every LogRecord as a single-line JSON object.

    Any custom keys passed via `logger.info("...", extra={"pair": ...})`
    are merged into the top level — that's how callers attach context.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # User-supplied `extra=` fields land on the record as attributes
        # whose names aren't in the reserved set.
        for k, v in record.__dict__.items():
            if k in _RESERVED_LOGRECORD_FIELDS or k.startswith("_"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except TypeError:
                payload[k] = repr(v)

        return json.dumps(payload, default=str)


def get_json_logger(name: str = "ict.json") -> logging.Logger:
    """JSON-structured logger writing to data/logs/{name}.json.log."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False  # don't double-emit through root

    safe_name = name.replace("/", "_")
    path = LOGS_DIR / f"{safe_name}.json.log"
    fh = logging.FileHandler(path)
    fh.setLevel(logging.INFO)
    fh.setFormatter(JSONFormatter())
    logger.addHandler(fh)

    return logger

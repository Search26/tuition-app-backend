"""Log setup: one JSON object per line in production, readable text in dev.

Every line written while handling a request (or a job a request enqueued) carries its request id,
taken from a context variable the request-id middleware and the worker set.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else on a record came from `extra=` and is logged.
# (color_message is uvicorn's ANSI-coloured copy of msg.)
_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    "taskName",
    "color_message",
}
_HANDLER_MARK = "_tuition_handler"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class _TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        request_id = request_id_var.get()
        return f"{line} [{request_id}]" if request_id else line


def configure_logging(log_format: str, level: str) -> None:
    """Send app, uvicorn and arq logs to stdout in one format. Safe to call more than once."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    setattr(handler, _HANDLER_MARK, True)
    handler.setFormatter(
        JsonFormatter()
        if log_format == "json"
        else _TextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn and arq install their own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "arq"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # The request-id middleware writes the access line (with the id); uvicorn's would double it.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False

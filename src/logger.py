import json
import logging
import os
import re
from datetime import datetime

_BASE_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__.keys())
_FORMATTER_ADDED_FIELDS = {"message", "asctime"}


class ExtraFieldsFormatter(logging.Formatter):
    """Formatter that appends `extra` fields as compact JSON."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        extra = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _BASE_LOG_RECORD_FIELDS and k not in _FORMATTER_ADDED_FIELDS and not k.startswith("_")
        }
        if not extra:
            return message
        return f"{message} {jdump(extra)}"

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

def get_logger(name: str = "app") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    handler = logging.StreamHandler()
    fmt = ExtraFieldsFormatter(
        fmt="%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    logger.propagate = False
    return logger

def jdump(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)

def now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def redact(s: str) -> str:
    if not s:
        return s
    return re.sub(
        r'("?(api[_-]?key|token|authorization)"?\s*:\s*")[^"]+(")',
        r'\1***REDACTED***\3',
        s,
        flags=re.I,
    )

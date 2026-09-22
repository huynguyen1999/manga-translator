import logging
import os
import sys
import threading
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Optional
import colorama

from .generic import replace_prefix

colorama.init(autoreset=False)

UTC7 = timezone(timedelta(hours=7))

# ContextVar storing the current correlation ID (e.g. req-a1b2c or batch-xxx/item-yyy)
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="-")

def set_correlation_id(correlation_id: str) -> None:
    """Set the correlation ID for the current async/thread context."""
    correlation_id_ctx.set(correlation_id)

def get_correlation_id() -> str:
    """Get the correlation ID for the current async/thread context."""
    return correlation_id_ctx.get()

ROOT_TAG = 'manga-translator'


class UTC7Formatter(logging.Formatter):
    """
    Formatter that formats timestamps in UTC+7 (YYYY-MM-DD HH:MM:SS.mmm +07:00)
    and formats lines as:
    YYYY-MM-DD HH:MM:SS.mmm +07:00 [LEVEL] [ID] [module] Message
    """
    def __init__(self, use_color: bool = False):
        super().__init__()
        self.use_color = use_color

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone(UTC7)
        if datefmt:
            base_time = dt.strftime(datefmt)
        else:
            base_time = dt.strftime("%Y-%m-%d %H:%M:%S")
        millis = int(record.msecs)
        return f"{base_time}.{millis:03d} +07:00"

    def format(self, record: logging.LogRecord) -> str:
        cid = getattr(record, "correlation_id", None)
        if not cid or cid == "-":
            cid = correlation_id_ctx.get()

        levelname = record.levelname
        if self.use_color:
            if record.levelno >= logging.ERROR:
                colored_level = f"{colorama.Fore.RED}{levelname}{colorama.Fore.RESET}"
            elif record.levelno >= logging.WARNING:
                colored_level = f"{colorama.Fore.YELLOW}{levelname}{colorama.Fore.RESET}"
            elif record.levelno == logging.INFO:
                colored_level = f"{colorama.Fore.GREEN}{levelname}{colorama.Fore.RESET}"
            elif record.levelno == logging.DEBUG:
                colored_level = f"{colorama.Fore.CYAN}{levelname}{colorama.Fore.RESET}"
            else:
                colored_level = levelname
        else:
            colored_level = levelname

        time_str = self.formatTime(record)

        # Clean module/logger name
        name = record.name
        if name.startswith(ROOT_TAG + "."):
            name = name[len(ROOT_TAG) + 1:]
        elif name == ROOT_TAG:
            name = "translator"

        message = record.getMessage()
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            if message and not message.endswith("\n"):
                message += "\n"
            message += record.exc_text
        if record.stack_info:
            if message and not message.endswith("\n"):
                message += "\n"
            message += self.formatStack(record.stack_info)

        return f"{time_str} [{colored_level}] [{cid}] [{name}] {message}"


# Backward compatibility alias
Formatter = UTC7Formatter


class Filter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Injects correlation_id from contextvars if not already set
        if not hasattr(record, "correlation_id") or record.correlation_id == "-":
            record.correlation_id = correlation_id_ctx.get()
        # Shorten the name
        record.name = replace_prefix(record.name, ROOT_TAG + '.', '')
        return True


CorrelationIdFilter = Filter

root = logging.getLogger(ROOT_TAG)
_default_handler = logging.StreamHandler(sys.stdout)
_default_handler.setFormatter(UTC7Formatter(use_color=True))
_default_handler.addFilter(Filter())
root.addHandler(_default_handler)
root.setLevel(logging.INFO)
root.propagate = False

def init_logging():
    if not root.handlers:
        root.addHandler(_default_handler)
    root.setLevel(logging.INFO)
    root.propagate = False

def set_log_level(level):
    root.setLevel(level)

def get_logger(name: str):
    return root.getChild(name)

file_handlers = {}

def add_file_logger(path: str):
    if path in file_handlers:
        return
    handler = logging.FileHandler(path, encoding='utf8')
    handler.setFormatter(UTC7Formatter(use_color=False))
    handler.addFilter(Filter())
    file_handlers[path] = handler
    root.addHandler(file_handlers[path])

def remove_file_logger(path: str):
    if path in file_handlers:
        root.removeHandler(file_handlers[path])
        file_handlers[path].close()
        del file_handlers[path]

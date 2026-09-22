import logging
import os
import re
import sys
import threading
from datetime import datetime, timedelta
from typing import Optional

from manga_translator.utils.log import (
    UTC7,
    ROOT_TAG,
    correlation_id_ctx,
    set_correlation_id,
    get_correlation_id,
    UTC7Formatter,
    CorrelationIdFilter,
)


class DailyRotatingFileHandler(logging.Handler):
    """
    Handler that writes logs to <log_dir>/<prefix>_YYYY-MM-DD.log based on UTC+7 date.
    Rotates daily at midnight UTC+7 and retains backup_count days of history.
    """
    def __init__(self, log_dir: str, prefix: str = "server", backup_count: int = 14, encoding: str = "utf-8"):
        super().__init__()
        self.log_dir = os.path.abspath(log_dir)
        os.makedirs(self.log_dir, exist_ok=True)
        self.prefix = prefix
        self.backup_count = backup_count
        self.encoding = encoding
        self._current_date = ""
        self._stream = None
        self._lock = threading.RLock()
        self._open_current_file()

    def _get_filename_for_date(self, date_str: str) -> str:
        return os.path.join(self.log_dir, f"{self.prefix}_{date_str}.log")

    def _open_current_file(self):
        with self._lock:
            now_utc7 = datetime.now(UTC7)
            date_str = now_utc7.strftime("%Y-%m-%d")
            if date_str != self._current_date or self._stream is None or getattr(self._stream, "closed", True):
                if self._stream is not None:
                    try:
                        self._stream.flush()
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None
                self._current_date = date_str
                filepath = self._get_filename_for_date(date_str)
                try:
                    self._stream = open(filepath, "a", encoding=self.encoding)
                    self._update_symlink(filepath)
                    self._cleanup_old_logs()
                except Exception:
                    self._stream = None

    def _update_symlink(self, target_filepath: str):
        if os.name == "posix":
            symlink_path = os.path.join(self.log_dir, f"{self.prefix}.log")
            try:
                if os.path.islink(symlink_path) or os.path.exists(symlink_path):
                    os.remove(symlink_path)
                os.symlink(os.path.basename(target_filepath), symlink_path)
            except Exception:
                pass

    def _cleanup_old_logs(self):
        try:
            now_utc7 = datetime.now(UTC7).date()
            cutoff_date = now_utc7 - timedelta(days=self.backup_count)
            pattern = re.compile(rf"^{re.escape(self.prefix)}_(\d{{4}}-\d{{2}}-\d{{2}})\.log$")
            for fname in os.listdir(self.log_dir):
                match = pattern.match(fname)
                if match:
                    try:
                        file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
                        if file_date < cutoff_date:
                            os.remove(os.path.join(self.log_dir, fname))
                    except Exception:
                        pass
        except Exception:
            pass

    def emit(self, record: logging.LogRecord):
        try:
            with self._lock:
                now_date = datetime.now(UTC7).strftime("%Y-%m-%d")
                if now_date != self._current_date or self._stream is None or getattr(self._stream, "closed", True):
                    self._open_current_file()
                if self._stream is None or getattr(self._stream, "closed", True):
                    return
                msg = self.format(record)
                self._stream.write(msg + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        with self._lock:
            self._current_date = ""
            if self._stream is not None:
                try:
                    self._stream.flush()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            super().close()


_configured = False
_server_file_handler: Optional[DailyRotatingFileHandler] = None
_server_console_handler: Optional[logging.StreamHandler] = None

def setup_server_logging(
    log_dir: str = "logs",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    retention_days: int = 14,
    force_reconfigure: bool = False,
) -> None:
    """
    Configures server logging with UTC+7 timestamps, daily rotating files,
    and correlation ID tracking.
    """
    global _configured, _server_file_handler, _server_console_handler

    if _configured and not force_reconfigure:
        return

    # Console Handler (colored)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(UTC7Formatter(use_color=True))
    console_handler.addFilter(CorrelationIdFilter())

    # Daily Rotating File Handler (plain text)
    file_handler = DailyRotatingFileHandler(
        log_dir=log_dir,
        prefix="server",
        backup_count=retention_days,
        encoding="utf-8",
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(UTC7Formatter(use_color=False))
    file_handler.addFilter(CorrelationIdFilter())

    # Clean existing root handlers
    root_logger = logging.getLogger()
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(min(console_level, file_level))

    # Configure manga-translator logger to propagate to root
    mt_logger = logging.getLogger(ROOT_TAG)
    for h in list(mt_logger.handlers):
        mt_logger.removeHandler(h)
    mt_logger.propagate = True
    mt_logger.setLevel(min(console_level, file_level))

    # Also configure manga_translator (with underscore)
    mt_underscore = logging.getLogger("manga_translator")
    for h in list(mt_underscore.handlers):
        mt_underscore.removeHandler(h)
    mt_underscore.propagate = True
    mt_underscore.setLevel(min(console_level, file_level))

    # Configure uvicorn loggers
    uvicorn_logger = logging.getLogger("uvicorn")
    for h in list(uvicorn_logger.handlers):
        uvicorn_logger.removeHandler(h)
    uvicorn_logger.propagate = True

    # Suppress uvicorn.access as our HTTP middleware logs richer context with correlation IDs
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.setLevel(logging.WARNING)

    # Cap noisy third-party loggers at WARNING
    for noisy in ["PIL", "urllib3", "asyncio", "multipart", "torch", "httpcore", "httpx", "transformers"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _server_file_handler = file_handler
    _server_console_handler = console_handler
    _configured = True


def get_logger(name: str = "server") -> logging.Logger:
    """Return a logger child of manga-translator for uniform naming."""
    return logging.getLogger(ROOT_TAG).getChild(name)

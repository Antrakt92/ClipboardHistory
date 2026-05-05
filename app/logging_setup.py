import logging
import os
import sys
from logging.handlers import RotatingFileHandler


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"
APP_HANDLER_ATTR = "_clipboard_history_handler"


def _has_console_stream():
    return sys.stderr is not None and not getattr(sys.stderr, "closed", False)


def _is_app_handler(handler):
    return getattr(handler, APP_HANDLER_ATTR, False)


def _remove_app_handlers(logger):
    for handler in list(logger.handlers):
        if not _is_app_handler(handler):
            continue
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


def _mark_app_handler(handler):
    setattr(handler, APP_HANDLER_ATTR, True)
    return handler


def _make_formatter():
    return logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)


def _add_handler(logger, handler, level, formatter):
    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger.addHandler(_mark_app_handler(handler))


def configure_logging(
    log_path,
    level=logging.INFO,
    console=None,
    max_bytes=1_000_000,
    backup_count=3,
    logger=None,
):
    logger = logger or logging.getLogger()
    logger.setLevel(level)
    _remove_app_handlers(logger)

    formatter = _make_formatter()
    added_handler = False
    file_error = None

    try:
        log_dir = os.path.dirname(os.path.abspath(log_path))
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        _add_handler(logger, file_handler, level, formatter)
        added_handler = True
    except OSError as exc:
        file_error = exc

    wants_console = _has_console_stream() if console is None else console
    if wants_console:
        _add_handler(logger, logging.StreamHandler(), level, formatter)
        added_handler = True

    if not added_handler:
        _add_handler(logger, logging.NullHandler(), level, formatter)
    elif file_error is not None:
        logger.warning("Failed to configure file logging at %s: %s", log_path, file_error)

    return logger

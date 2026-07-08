# Logging 

# qkd_logging.py

import logging
import logging.handlers

from qkd_runtime import *


def initialize_logging(args):
    """
    Initialize application logging.
    """

    LOG_LEVELS = [
        logging.ERROR,
        logging.WARNING,
        logging.INFO,
        logging.DEBUG,
    ]

    logging.captureWarnings(True)

    verbosity = min(args.verbose, len(LOG_LEVELS) - 1)
    log_level = LOG_LEVELS[verbosity]

    log = logging.getLogger("qkd")

    #
    # Prevent duplicate handlers if initialize_logging()
    # is called more than once.
    #
    if log.handlers:
        return log

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s"
    )

    #
    # Console logging
    #
    stderr = logging.StreamHandler()
    stderr.setLevel(log_level)
    stderr.setFormatter(formatter)
    log.addHandler(stderr)

    #
    # Trace logging
    #
    if args.trace:
        trace_handler = logging.FileHandler("qkd_trace.log")
        trace_handler.setLevel(logging.DEBUG)
        trace_handler.setFormatter(formatter)
        log.addHandler(trace_handler)

    #
    # On-box persistent logging
    #
    if ONBOX:
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILENAME,
            maxBytes=10_000_000,
            backupCount=5,
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)

    #
    # Logger level
    #
    if args.trace:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(log_level)

    log.info("Logging initialized")

    return log

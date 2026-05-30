import logging
import structlog


def setup_logging() -> None:
    # Surface stdlib INFO logs from app modules. Many services log via the
    # stdlib logger (logging.getLogger), which otherwise inherits the default
    # WARNING level — hiding useful INFO (e.g. visual scoring, segment
    # selection). basicConfig covers non-uvicorn contexts (scripts); the
    # explicit "src" level ensures INFO also propagates to uvicorn's handlers.
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("src").setLevel(logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)

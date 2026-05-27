"""Structured logging using loguru."""
import sys
from loguru import logger

from app.core.config import settings


def configure_logging() -> None:
    """Configure global logger sinks. Called once at app startup."""
    logger.remove()
    logger.add(
        sys.stdout,
        level="DEBUG" if settings.debug else "INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )
    if not settings.debug:
        logger.add(
            "logs/app.log",
            rotation="10 MB",
            retention="14 days",
            level="INFO",
            enqueue=True,
        )


__all__ = ["logger", "configure_logging"]

"""MySQL connection pool using mysql-connector-python.

Why a pool?
    Each request gets a connection from the pool, runs its work, and returns
    the connection to the pool. Avoids the latency cost of opening a new
    TCP+auth handshake on every API call.
"""
from contextlib import contextmanager
from typing import Generator

import mysql.connector
from mysql.connector import pooling
from mysql.connector.connection import MySQLConnection

from app.core.config import settings
from app.core.logger import logger


class Database:
    """Singleton wrapper around a mysql-connector connection pool."""

    _pool: pooling.MySQLConnectionPool | None = None

    @classmethod
    def init_pool(cls) -> None:
        if cls._pool is not None:
            return
        try:
            cls._pool = pooling.MySQLConnectionPool(
                pool_name="iia_pool",
                pool_size=settings.db_pool_size,
                pool_reset_session=True,
                host=settings.db_host,
                port=settings.db_port,
                user=settings.db_user,
                password=settings.db_password,
                database=settings.db_name,
                charset="utf8mb4",
                use_unicode=True,
                autocommit=False,
            )
            logger.info(
                f"MySQL pool initialised — {settings.db_host}:{settings.db_port}/"
                f"{settings.db_name} (size={settings.db_pool_size})"
            )
        except mysql.connector.Error as e:
            logger.error(f"Failed to initialise MySQL pool: {e}")
            raise

    @classmethod
    def get_connection(cls) -> MySQLConnection:
        if cls._pool is None:
            cls.init_pool()
        assert cls._pool is not None
        return cls._pool.get_connection()  # type: ignore[return-value]

    @classmethod
    def close_pool(cls) -> None:
        # mysql-connector pools don't expose explicit close; relying on GC + reset.
        cls._pool = None
        logger.info("MySQL pool released")


@contextmanager
def get_db() -> Generator[MySQLConnection, None, None]:
    """Context manager — yields a pooled connection and ensures cleanup.

    Usage:
        with get_db() as conn:
            with conn.cursor(dictionary=True) as cur:
                cur.execute("SELECT 1")
    """
    conn = Database.get_connection()
    try:
        yield conn
    finally:
        try:
            conn.close()  # returns the connection to the pool
        except Exception:  # noqa: BLE001
            pass


def fastapi_db_dependency() -> Generator[MySQLConnection, None, None]:
    """FastAPI Depends() dependency."""
    with get_db() as conn:
        yield conn

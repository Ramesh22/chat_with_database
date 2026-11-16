"""Database access for Chat with Your Database.

Connections are intended to use a dedicated read-only database role. As
defense in depth, every session is also forced into read-only mode:

* Postgres: ``default_transaction_read_only=on`` plus a statement timeout, so a
  runaway LLM-generated query cannot write data or hang the server.
* SQLite: ``PRAGMA query_only = ON`` on each connection (used for the local
  demo database).

Credentials come from the ``DATABASE_URL`` environment variable (see
``.env.example``); nothing is hardcoded. Never commit a real ``.env``.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, event, inspect, make_url
from sqlalchemy.engine import URL

load_dotenv()

DEFAULT_DATABASE_URL = "sqlite:///./chat_with_database.db"
DEFAULT_STATEMENT_TIMEOUT_MS = 15_000


def _database_url() -> URL:
    return make_url(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))


def _statement_timeout_ms() -> int:
    raw = os.getenv("SQL_STATEMENT_TIMEOUT_MS", str(DEFAULT_STATEMENT_TIMEOUT_MS))
    try:
        return max(int(raw), 1)
    except ValueError:
        return DEFAULT_STATEMENT_TIMEOUT_MS


def _connect_args(url: URL) -> dict:
    backend = url.get_backend_name()
    if backend == "sqlite":
        return {"check_same_thread": False}
    if backend == "postgresql":
        return {
            "options": (
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={_statement_timeout_ms()}"
            )
        }
    return {}


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create (once) the SQLAlchemy engine for the read-only database role."""
    url = _database_url()
    engine = create_engine(url, connect_args=_connect_args(url), pool_pre_ping=True)

    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_query_only(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA query_only = ON")
            cursor.close()

    return engine


def get_schema(engine: Engine | None = None) -> dict[str, list[dict]]:
    """Fetch table names and column metadata visible to the configured role.

    Returns a mapping of table name to a list of columns::

        {
            "orders": [
                {"name": "id", "type": "INTEGER", "nullable": False, "primary_key": True},
                ...
            ],
            ...
        }
    """
    engine = engine or get_engine()
    inspector = inspect(engine)

    schema: dict[str, list[dict]] = {}
    for table_name in sorted(inspector.get_table_names()):
        primary_keys = set(
            inspector.get_pk_constraint(table_name).get("constrained_columns") or []
        )
        schema[table_name] = [
            {
                "name": column["name"],
                "type": str(column["type"]),
                "nullable": column["nullable"],
                "primary_key": column["name"] in primary_keys,
            }
            for column in inspector.get_columns(table_name)
        ]
    return schema

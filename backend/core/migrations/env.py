"""Alembic environment.

At runtime the app passes an open connection via ``config.attributes["connection"]``.
Autogenerate from the CLI:
    uv run alembic -x db=path/to/dev.db revision --autogenerate -m "msg"
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine
from sqlmodel import SQLModel

import backend.core.models  # noqa: F401  (registers tables on SQLModel.metadata)

config = context.config
target_metadata = SQLModel.metadata


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    db = context.get_x_argument(as_dictionary=True).get("db", "data/dev.db")
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        _run(conn)

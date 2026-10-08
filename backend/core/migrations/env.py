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
from backend.core.db import install_extension_loader

config = context.config
target_metadata = SQLModel.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:  # type: ignore[no-untyped-def]
    # Virtual tables (FTS5, sqlite-vec) and their shadow tables are managed by hand-written
    # migrations, not by SQLModel metadata; don't let autogenerate drop them.
    return not (type_ == "table" and reflected and compare_to is None)


def _run(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
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
    install_extension_loader(engine)
    with engine.begin() as conn:
        _run(conn)

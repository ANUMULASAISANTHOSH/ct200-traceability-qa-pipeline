"""Database engine, session management, and SQLite configuration."""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

Base = declarative_base()


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable SQLite foreign key constraints and WAL mode if applicable."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
    cursor.close()


def get_engine(database_url: str) -> Engine:
    """Create a configured SQLAlchemy engine."""
    connect_args = {}
    engine_kwargs = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in database_url:
            from sqlalchemy.pool import StaticPool
            engine_kwargs["poolclass"] = StaticPool
    return create_engine(database_url, connect_args=connect_args, **engine_kwargs)



def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a configured sessionmaker bound to the given engine."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db(engine: Engine) -> None:
    """Create all database tables defined on Base metadata."""
    Base.metadata.create_all(bind=engine)

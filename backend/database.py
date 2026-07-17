"""
database.py
Async SQLAlchemy engine, session factory, and base class.
Auto-detects SQLite (local) vs PostgreSQL (Render) from DATABASE_URL.
"""
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from .config import settings


from urllib.parse import urlparse, urlunparse, parse_qs, urlencode


def _normalise_db_url(url: str) -> str:
    """
    Convert 'postgresql://' / 'postgres://' -> 'postgresql+asyncpg://'.
    Also strips 'sslmode' and 'channel_binding' query params — these are
    libpq-style parameter names (used by Neon, Render, etc. in their
    connection strings) that asyncpg's connect() does not accept as
    keyword arguments. SSL is instead enforced explicitly via connect_args
    below. SQLite URLs are left untouched.
    """
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    else:
        return url

    parsed = urlparse(url)
    query  = parse_qs(parsed.query)
    query.pop("sslmode", None)
    query.pop("channel_binding", None)
    url = urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    return url


DATABASE_URL = _normalise_db_url(settings.DATABASE_URL)

_connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}
elif DATABASE_URL.startswith("postgresql+asyncpg"):
    _connect_args = {"ssl": "require"}   # enforce TLS explicitly, asyncpg-style

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
)


DATABASE_URL = _normalise_db_url(settings.DATABASE_URL)

# SQLite needs check_same_thread=False; Postgres does not accept it.
_connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Every ORM model inherits from this."""
    pass

# AsyncSessionLocal = async_sessionmaker(
#     bind=engine,
#     class_=AsyncSession,
#     expire_on_commit=False,   # keeps ORM objects usable after commit
# )


# class Base(DeclarativeBase):
#     """Every ORM model inherits from this."""
#     pass


async def get_db():
    """
    FastAPI dependency — yields a DB session per request.

    Usage in a route:
        async def my_route(db: AsyncSession = Depends(get_db)):
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """
    Creates all tables on startup (safe to call if tables already exist).
    Called from main.py lifespan.
    """
    async with engine.begin() as conn:
        from . import models  # noqa: F401 — ensures models are registered
        await conn.run_sync(Base.metadata.create_all)
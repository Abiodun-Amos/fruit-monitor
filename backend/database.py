"""
database.py
Async SQLAlchemy engine, session factory, and base class.
"""
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from .config import settings

# check_same_thread=False is required for SQLite with async
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,   # keeps ORM objects usable after commit
)


class Base(DeclarativeBase):
    """Every ORM model inherits from this."""
    pass


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
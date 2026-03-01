"""
CORELINK Database Configuration

Production-ready PostgreSQL setup using SQLAlchemy 2.0 with async support.
Provides async engine, session factory, and FastAPI dependency injection.
"""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool, QueuePool

from app.config import settings


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy models.
    
    Usage:
        from app.database import Base
        
        class User(Base):
            __tablename__ = "users"
            ...
    """
    pass


# Convert PostgreSQL URL to async format
def get_async_database_url() -> str:
    """
    Convert standard PostgreSQL URL to async format.
    
    Changes postgresql:// to postgresql+asyncpg://
    """
    db_url = str(settings.DATABASE_URL)
    
    
    # Replace scheme for asyncpg driver
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    
    return db_url


# Create async engine
engine: AsyncEngine = create_async_engine(
    get_async_database_url(),
    echo=settings.DEBUG,  # Log SQL queries in debug mode
)


# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Don't expire objects after commit
    autocommit=False,  # Manual commit control
    autoflush=False,  # Manual flush control
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for database sessions.
    
    Provides clean session lifecycle management:
    - Creates new session for each request
    - Automatically closes session after request
    - Handles exceptions gracefully
    
    Usage in FastAPI routes:
        from app.database import get_db
        from sqlalchemy.ext.asyncio import AsyncSession
        
        @router.get("/users")
        async def get_users(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(User))
            users = result.scalars().all()
            return users
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """
    Initialize database tables.
    
    Creates all tables defined in models.
    Should be called on application startup.
    
    Usage:
        from app.database import init_db
        
        @app.on_event("startup")
        async def startup():
            await init_db()
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """
    Close database connections.
    
    Disposes of the engine and closes all connections.
    Should be called on application shutdown.
    
    Usage:
        @app.on_event("shutdown")
        async def shutdown():
            await close_db()
    """
    await engine.dispose()

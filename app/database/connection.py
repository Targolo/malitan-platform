"""
Database connection management
Handles both SQLite (dev) and PostgreSQL (prod) connections
"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool
from config.settings import get_database_url, DATABASE_TYPE, is_development
from database.models import Base

# Global session factory
SessionLocal = None
engine = None


def init_database():
    """Initialize database connection (tables are created by `flask init-db`)"""
    global engine, SessionLocal
    
    database_url = get_database_url()
    
    # Create engine
    if DATABASE_TYPE == 'sqlite':
        # SQLite creates the file but not its directory (e.g. DATA_DIR on a fresh volume)
        sqlite_file = make_url(database_url).database
        if sqlite_file and sqlite_file != ':memory:':
            Path(sqlite_file).parent.mkdir(parents=True, exist_ok=True)

        # SQLite configuration
        engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=is_development()  # Show SQL queries in dev
        )
    else:
        # PostgreSQL configuration
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=10,  # Increased from 5
            max_overflow=20,  # Increased from 10
            pool_recycle=3600,
            echo=False,
            connect_args={
                "connect_timeout": 10,
                "options": "-c timezone=utc"
            }
        )
    
    # Create session factory
    SessionLocal = scoped_session(
        sessionmaker(autocommit=False, autoflush=False, bind=engine)
    )

    return engine


def get_db():
    """
    Get database session
    Usage in Streamlit:
        db = get_db()
        try:
            # Your database operations
            result = db.query(Model).all()
        finally:
            db.close()
    """
    if SessionLocal is None:
        init_database()
    
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session():
    """
    Get a database session for direct use
    Returns a session that must be closed manually
    """
    if SessionLocal is None:
        init_database()
    
    return SessionLocal()


def close_db():
    """Close database connections"""
    global SessionLocal
    if SessionLocal:
        SessionLocal.remove()


def reset_database():
    """
    Reset database (drop all tables and recreate)
    WARNING: This will delete all data!
    Only use in development
    """
    if not is_development():
        raise RuntimeError("Cannot reset database in production!")
    
    global engine
    if engine is None:
        init_database()
    
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("Database reset complete!")


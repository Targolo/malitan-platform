"""
Database initialization script
Creates tables and initial data (shared by `flask init-db` and `python -m database.init_db`)
"""
from sqlalchemy import inspect

from database import connection
from database.connection import get_db_session
from database.models import Base, User, UserRole
from config.settings import is_development, DEV_USERS
from utils.auth import hash_password, UNUSABLE_PASSWORD_HASH

INITIAL_USERS = (
    ('david', UserRole.DAVID),
    ('lea', UserRole.LEA),
    ('danielle', UserRole.DANIELLE),
)


def create_tables() -> list:
    """Create missing tables (idempotent). Returns the names of the tables created."""
    engine = connection.engine if connection.engine is not None else connection.init_database()
    existing = set(inspect(engine).get_table_names())
    Base.metadata.create_all(bind=engine)
    return [table.name for table in Base.metadata.sorted_tables if table.name not in existing]


def create_initial_users() -> list:
    """
    Create the initial users if the users table is empty. Returns the usernames created.

    In development they get the DEV_USERS passwords; otherwise an unusable hash,
    so nobody can log in until `flask set-password <user>` is run.
    """
    db = get_db_session()
    try:
        if db.query(User).count() > 0:
            return []

        for username, role in INITIAL_USERS:
            if is_development():
                password_hash = hash_password(DEV_USERS[username]['password'])
            else:
                password_hash = UNUSABLE_PASSWORD_HASH
            db.add(User(
                username=username,
                password_hash=password_hash,
                full_name=DEV_USERS[username]['full_name'],
                role=role,
            ))

        db.commit()
        return [username for username, _ in INITIAL_USERS]
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize_database(echo=print):
    """Create missing tables and seed the initial users if there are none."""
    created_tables = create_tables()
    if created_tables:
        echo(f"✓ Created tables: {', '.join(created_tables)}")
    else:
        echo("✓ All tables already exist")

    created_users = create_initial_users()
    if not created_users:
        echo("✓ Users already exist, skipping user creation")
    elif is_development():
        echo(f"✓ Created users with development passwords: {', '.join(created_users)}")
    else:
        echo(f"✓ Created users: {', '.join(created_users)}")
        echo("  They cannot log in yet. Set each password with: flask set-password <user>")


if __name__ == "__main__":
    initialize_database()

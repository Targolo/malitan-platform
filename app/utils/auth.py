"""
Authentication utilities for Flask
Adapted from utils/auth.py for Flask sessions
"""
from typing import Optional
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import Session
from database.models import User
from database.connection import get_db_session

# Stored for accounts whose password has not been set yet. It has no "$"
# separator, so check_password_hash rejects every password against it.
UNUSABLE_PASSWORD_HASH = "!unusable"


def hash_password(password: str) -> str:
    """Hash a password with a random salt using scrypt"""
    return generate_password_hash(password, method="scrypt")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return check_password_hash(hashed_password, plain_password)


def authenticate_user(username: str, password: str) -> Optional[User]:
    """
    Authenticate a user
    
    Args:
        username: Username
        password: Plain text password
    
    Returns:
        User object if authentication successful, None otherwise
    """
    db = get_db_session()
    try:
        user = db.query(User).filter(User.username == username).first()
        
        if user and verify_password(password, user.password_hash):
            return user
        return None
    finally:
        db.close()


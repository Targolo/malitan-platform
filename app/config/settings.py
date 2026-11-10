"""
Configuration settings for Malitan 3D Manager
Handles both development and production environments
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
env_file = Path(__file__).parent.parent / '.env.local'
if env_file.exists():
    load_dotenv(env_file)
else:
    load_dotenv()

# Environment
ENV = os.getenv('ENV', 'production')

# Application
APP_NAME = os.getenv('APP_NAME', 'Malitan 3D Manager')
APP_VERSION = os.getenv('APP_VERSION', '2.11.4')

# Paths
BASE_DIR = Path(__file__).parent.parent
# Not created at import time: whoever writes to them creates them.
DATA_DIR = Path(os.getenv('DATA_DIR', str(BASE_DIR / 'data')))
EXPORTS_DIR = Path(os.getenv('EXPORTS_DIR', str(BASE_DIR / 'exports')))
BACKUPS_DIR = Path(os.getenv('BACKUPS_DIR', str(BASE_DIR / 'backups')))

# Database
DATABASE_TYPE = os.getenv('DATABASE_TYPE', 'sqlite')
DATABASE_URL = os.getenv('DATABASE_URL', f"sqlite:///{DATA_DIR / 'malitan.db'}")

# Session cookie: sent over HTTPS only unless explicitly disabled (e.g. local http dev)
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', 'true').strip().lower() not in ('0', 'false', 'no', 'off')

# Authentication
AUTH_SECRET_KEY_MIN_LENGTH = 32
_DEV_ONLY_AUTH_SECRET_KEY = 'dev-only-insecure-auth-secret-key-never-use-in-production'


def _load_auth_secret_key() -> str:
    """Return the session signing key; fail closed outside development."""
    key = os.getenv('AUTH_SECRET_KEY', '')
    if ENV == 'development' and not key:
        return _DEV_ONLY_AUTH_SECRET_KEY
    if len(key) < AUTH_SECRET_KEY_MIN_LENGTH:
        raise RuntimeError(
            f"AUTH_SECRET_KEY is missing or shorter than {AUTH_SECRET_KEY_MIN_LENGTH} "
            f"characters (ENV={ENV!r}). Set it to a long random value."
        )
    return key


AUTH_SECRET_KEY = _load_auth_secret_key()

# Development users (only for dev mode)
DEV_USERS = {
    'david': {
        'password': os.getenv('DEV_PASS_DAVID', 'dev123'),
        'full_name': 'David',
        'role': 'david'
    },
    'lea': {
        'password': os.getenv('DEV_PASS_LEA', 'dev123'),
        'full_name': 'Lea',
        'role': 'lea'
    },
    'danielle': {
        'password': os.getenv('DEV_PASS_DANIELLE', 'dev123'),
        'full_name': 'Danielle',
        'role': 'danielle'
    }
}

# Stock alerts
FILAMENT_LOW_STOCK_THRESHOLD = 500  # grams

# Performance monitoring (set to True to enable performance timing display)
SHOW_PERFORMANCE_TIMING = os.getenv('SHOW_PERFORMANCE_TIMING', 'false').lower() == 'true'

# Brevo (Email service)
# In development: Set in .env.local
# In production: Set in Streamlit Cloud Secrets
BREVO_API_KEY = os.getenv('BREVO_API_KEY', '')
BREVO_SENDER_EMAIL = os.getenv('BREVO_SENDER_EMAIL', 'malitan.studio@gmail.com')
BREVO_SENDER_NAME = os.getenv('BREVO_SENDER_NAME', 'Malitan Studio')
BREVO_ENABLED = bool(BREVO_API_KEY)  # Auto-enable if API key is set

# BulkGate (SMS service - cheaper for Israel)
# In development: Set in .env.local
# In production: Set in Streamlit Cloud Secrets
BULKGATE_APPLICATION_ID = os.getenv('BULKGATE_APPLICATION_ID', '')
BULKGATE_APPLICATION_TOKEN = os.getenv('BULKGATE_APPLICATION_TOKEN', '')
BULKGATE_ENABLED = bool(BULKGATE_APPLICATION_ID and BULKGATE_APPLICATION_TOKEN)

# Emails déclenchés lorsque les commandes sont prêtes à être livrées
ENABLE_PACKAGING_EMAILS = os.getenv('ENABLE_PACKAGING_EMAILS', 'false').lower() == 'true'

# Telegram Bot (notifications internes David / Lea)
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN)

# Read-only status API for external agents (GET /api/status), reachable from the internet.
# Disabled when empty or shorter than the minimum, so a weak token cannot enable it by mistake.
STATUS_API_TOKEN = os.getenv('STATUS_API_TOKEN', '').strip()
STATUS_API_TOKEN_MIN_LENGTH = 32

def get_database_url():
    """Return the configured database URL"""
    return DATABASE_URL

def is_development():
    """Check if running in development mode"""
    return ENV == 'development'

def is_production():
    """Check if running in production mode"""
    return ENV == 'production'


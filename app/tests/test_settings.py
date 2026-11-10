import importlib
import os

import dotenv
import pytest

# Obviously fake values; never real secrets.
VALID_KEY = "x" * 32
SHORT_KEY = "x" * 31


@pytest.fixture
def reload_settings(test_app, monkeypatch):
    """
    Reload config.settings under a given ENV / AUTH_SECRET_KEY, then restore the
    original environment and reload settings again so later tests see the
    session's development configuration.
    """
    import config.settings

    # Keep a local .env file from filling in AUTH_SECRET_KEY during the reload.
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    saved_env = os.environ.copy()

    def _reload(env, key):
        os.environ["ENV"] = env
        if key is None:
            os.environ.pop("AUTH_SECRET_KEY", None)
        else:
            os.environ["AUTH_SECRET_KEY"] = key
        return importlib.reload(config.settings)

    try:
        yield _reload
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        importlib.reload(config.settings)


def test_production_without_key_raises(reload_settings):
    with pytest.raises(RuntimeError, match="AUTH_SECRET_KEY"):
        reload_settings("production", None)


def test_production_with_short_key_raises(reload_settings):
    with pytest.raises(RuntimeError, match="AUTH_SECRET_KEY") as exc_info:
        reload_settings("production", SHORT_KEY)

    assert SHORT_KEY not in str(exc_info.value)


def test_production_with_valid_key_works(reload_settings):
    settings = reload_settings("production", VALID_KEY)

    assert settings.AUTH_SECRET_KEY == VALID_KEY


def test_data_dirs_come_from_env_and_are_not_created_on_import(reload_settings, tmp_path):
    for name in ("DATA_DIR", "EXPORTS_DIR", "BACKUPS_DIR"):
        os.environ[name] = str(tmp_path / name.lower())

    settings = reload_settings("production", VALID_KEY)

    for name in ("DATA_DIR", "EXPORTS_DIR", "BACKUPS_DIR"):
        assert getattr(settings, name) == tmp_path / name.lower()
        assert not getattr(settings, name).exists()


def test_development_without_key_uses_dev_only_key(reload_settings):
    settings = reload_settings("development", None)

    assert settings.AUTH_SECRET_KEY == settings._DEV_ONLY_AUTH_SECRET_KEY
    assert "dev-only" in settings.AUTH_SECRET_KEY

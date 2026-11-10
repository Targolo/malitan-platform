import getpass
import importlib

import pytest
from sqlalchemy import inspect


def _cli_runner():
    import flask_app

    return importlib.reload(flask_app).app.test_cli_runner()


@pytest.fixture
def empty_db(test_app):
    """Drop every table, and recreate them afterwards for the other tests."""
    metadata = test_app.models.Base.metadata
    engine = test_app.connection.engine
    metadata.drop_all(bind=engine)
    try:
        yield engine
    finally:
        metadata.create_all(bind=test_app.connection.engine)


def _snapshot(test_app):
    db = test_app.connection.get_db_session()
    try:
        users = db.query(test_app.models.User).order_by(test_app.models.User.username).all()
        return [(u.id, u.username, u.password_hash, u.role) for u in users]
    finally:
        db.close()


def test_init_db_creates_tables_and_users_and_is_idempotent(test_app, empty_db):
    runner = _cli_runner()
    model_tables = set(test_app.models.Base.metadata.tables)
    assert inspect(empty_db).get_table_names() == []

    first = runner.invoke(args=["init-db"])

    assert first.exit_code == 0, first.output
    assert set(inspect(test_app.connection.engine).get_table_names()) == model_tables
    users = _snapshot(test_app)
    assert [u[1] for u in users] == ["danielle", "david", "lea"]
    # Development (as configured by conftest): the DEV_USERS passwords work.
    assert test_app.auth.authenticate_user("lea", "dev123") is not None

    second = runner.invoke(args=["init-db"])

    assert second.exit_code == 0, second.output
    assert "All tables already exist" in second.output
    assert "Users already exist" in second.output
    assert _snapshot(test_app) == users


def test_seeded_users_cannot_log_in_outside_development_until_password_set(
    test_app, empty_db, monkeypatch
):
    import config.settings

    runner = _cli_runner()
    monkeypatch.setattr(config.settings, "ENV", "production")
    auth = test_app.auth

    result = runner.invoke(args=["init-db"])

    assert result.exit_code == 0, result.output
    assert "flask set-password <user>" in result.output
    for username, _hash in [(u[1], u[2]) for u in _snapshot(test_app)]:
        assert _hash == auth.UNUSABLE_PASSWORD_HASH
        assert auth.authenticate_user(username, "dev123") is None
        assert auth.authenticate_user(username, "") is None
        assert auth.authenticate_user(username, auth.UNUSABLE_PASSWORD_HASH) is None

    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "n3w-pass")
    set_result = runner.invoke(args=["set-password", "lea"])

    assert set_result.exit_code == 0, set_result.output
    assert auth.authenticate_user("lea", "n3w-pass") is not None
    assert auth.authenticate_user("david", "n3w-pass") is None

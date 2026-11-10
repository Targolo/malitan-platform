import getpass
import importlib


def test_same_password_hashed_twice_differs(test_app):
    auth = test_app.auth
    first = auth.hash_password("s3cret")
    second = auth.hash_password("s3cret")

    assert first != second
    assert first.startswith("scrypt:")


def test_correct_password_verifies(test_app):
    auth = test_app.auth
    hashed = auth.hash_password("s3cret")

    assert auth.verify_password("s3cret", hashed) is True


def test_wrong_password_fails(test_app):
    auth = test_app.auth
    hashed = auth.hash_password("s3cret")

    assert auth.verify_password("wrong", hashed) is False


def test_legacy_sha256_hash_is_rejected(test_app):
    legacy = "87274af01876341455b32d805946f272871bb42effa6604dccf28bb027afa82b"  # sha256("dev123")

    assert test_app.auth.verify_password("dev123", legacy) is False


def _cli_runner():
    import flask_app

    return importlib.reload(flask_app).app.test_cli_runner()


def test_set_password_updates_hash(test_app, db_session, workflow_order, monkeypatch):
    models, auth = test_app.models, test_app.auth
    old_hash = db_session.get(models.User, workflow_order.user_lea_id).password_hash
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "n3w-pass")

    result = _cli_runner().invoke(args=["set-password", workflow_order.user_lea_username])

    assert result.exit_code == 0, result.output
    db_session.expire_all()
    new_hash = db_session.get(models.User, workflow_order.user_lea_id).password_hash
    assert new_hash != old_hash
    assert auth.verify_password("n3w-pass", new_hash)
    assert auth.authenticate_user(workflow_order.user_lea_username, "n3w-pass") is not None
    assert auth.authenticate_user(workflow_order.user_lea_username, "dev123") is None


def test_set_password_unknown_user_fails(test_app, monkeypatch):
    def _fail(prompt=""):
        raise AssertionError("should not prompt for an unknown user")

    monkeypatch.setattr(getpass, "getpass", _fail)

    result = _cli_runner().invoke(args=["set-password", "nobody"])

    assert result.exit_code != 0
    assert "does not exist" in result.output

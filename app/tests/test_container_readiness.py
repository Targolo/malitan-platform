import importlib
import io
import logging
import time
from pathlib import Path

import pytest

# Obviously fake values; never real secrets.
FAKE_DB_PASSWORD = "pw-not-a-real-secret"
SECRET_DETAIL = "internal-detail-must-not-leak"


class _BrokenEngine:
    def connect(self):
        raise RuntimeError(SECRET_DETAIL)


class _HangingEngine:
    def connect(self):
        time.sleep(0.5)
        raise RuntimeError("too late")


def _login(client, user_id):
    with client.session_transaction() as s:
        s["user_id"] = user_id


# ---------- /livez and /readyz ----------

def test_livez_ok_with_database_unreachable(web_client, test_app, monkeypatch):
    def _no_db(*args, **kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(test_app.connection, "engine", _BrokenEngine())
    monkeypatch.setattr(test_app.connection, "get_db_session", _no_db)

    resp = web_client.get("/livez")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_readyz_ok_when_database_answers(web_client):
    resp = web_client.get("/readyz")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_readyz_503_with_broken_engine_and_generic_body(web_client, test_app, monkeypatch, caplog):
    monkeypatch.setattr(test_app.connection, "engine", _BrokenEngine())

    with caplog.at_level(logging.ERROR):
        resp = web_client.get("/readyz")

    assert resp.status_code == 503
    assert resp.get_json() == {"status": "unavailable"}
    assert SECRET_DETAIL not in resp.get_data(as_text=True)
    assert "Readiness check failed" in caplog.text  # logged server-side instead


def test_readyz_503_when_database_hangs(web_client, test_app, monkeypatch):
    import flask_app

    monkeypatch.setattr(flask_app, "READYZ_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(test_app.connection, "engine", _HangingEngine())

    started = time.monotonic()
    resp = web_client.get("/readyz")

    assert resp.status_code == 503
    assert time.monotonic() - started < 0.4


# ---------- Product images under DATA_DIR ----------

@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    import config.settings

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


def test_uploaded_image_lands_under_data_dir_and_is_served(
    web_client, workflow_order, data_dir, db_session, test_app
):
    _login(web_client, workflow_order.user_lea_id)
    png = b"\x89PNG\r\n\x1a\nfake-image-bytes"

    resp = web_client.post(
        "/catalogue/products/new",
        data={
            "nom": "Lampe Upload",
            "product_type": "partage_benefices",
            "prix_vente_base": "100",
            "image": (io.BytesIO(png), "photo.png"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    filename = f"Lampe_Upload_{workflow_order.user_lea_id}.png"
    assert (data_dir / "product_images" / filename).read_bytes() == png
    assert not (Path.cwd() / "data" / "product_images" / filename).exists()
    product = db_session.query(test_app.models.Product).filter_by(nom="Lampe Upload").one()
    assert product.image_url == filename

    with web_client.get(f"/data/product_images/{filename}") as served:
        assert served.status_code == 200
        assert served.data == png


def test_legacy_relative_image_url_is_served_and_deleted_by_file_name(
    web_client, workflow_order, data_dir, db_session, test_app
):
    images_dir = data_dir / "product_images"
    images_dir.mkdir(parents=True)
    (images_dir / "old.png").write_bytes(b"old")
    product = db_session.get(test_app.models.Product, workflow_order.product_id)
    product.image_url = "data/product_images/old.png"
    db_session.commit()
    _login(web_client, workflow_order.user_lea_id)

    with web_client.get("/data/product_images/old.png") as served:
        assert served.data == b"old"

    resp = web_client.post(
        f"/catalogue/products/{workflow_order.product_id}/update",
        data={"nom": "Lampe 3D", "prix_vente_base": "180", "image": (io.BytesIO(b"new"), "new.png")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert not (images_dir / "old.png").exists()
    assert (images_dir / f"Lampe_3D_{workflow_order.product_id}.png").read_bytes() == b"new"


# ---------- Startup log ----------

@pytest.mark.parametrize("init_fails", [False, True], ids=["init-ok", "init-error-quotes-url"])
def test_startup_log_hides_database_password(test_app, monkeypatch, capsys, init_fails):
    import config.settings
    import database.connection
    import flask_app

    url = f"postgresql://malitan:{FAKE_DB_PASSWORD}@db.example:5432/malitan"

    def _init_database():
        if init_fails:
            raise ValueError(f"Could not connect using {url}")

    with monkeypatch.context() as m:
        m.setattr(config.settings, "DATABASE_URL", url)
        m.setattr(config.settings, "DATABASE_TYPE", "postgresql")
        m.setattr(database.connection, "init_database", _init_database)
        capsys.readouterr()
        importlib.reload(flask_app)
        out = capsys.readouterr().out

    importlib.reload(flask_app)  # back to the test database configuration

    assert FAKE_DB_PASSWORD not in out
    assert "malitan:***@db.example" in out
    if init_fails:
        assert "Failed to initialize database" in out


# ---------- Session cookie ----------

def test_session_cookie_flags(web_client, workflow_order):
    resp = web_client.post(
        "/login",
        data={"username": workflow_order.user_lea_username, "password": workflow_order.user_password},
    )

    assert resp.status_code == 302
    cookie = next(h for h in resp.headers.getlist("Set-Cookie") if h.startswith("session="))
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" in cookie

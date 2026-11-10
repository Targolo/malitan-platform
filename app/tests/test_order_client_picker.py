"""
Order creation starts from an identified client: a picker step returns a client id,
then the order form shows that client as a fixed label.
"""
import html as html_lib
import json

import pytest


def _login(client, user_id):
    with client.session_transaction() as s:
        s["user_id"] = user_id
        s["username"] = "lea"


def _text(response):
    return html_lib.unescape(response.get_data(as_text=True))


def test_picker_lists_clients_with_continue_disabled(web_client, workflow_order):
    _login(web_client, workflow_order.user_lea_id)

    response = web_client.get("/orders/form/new/client")

    assert response.status_code == 200
    page = _text(response)
    assert f'value="{workflow_order.client_id}"' in page
    assert "John Doe" in page
    assert 'id="picker-new-client"' in page
    assert 'id="picker-continue"' in page
    assert "disabled" in page.split('id="picker-continue"')[1].split(">")[0]


def test_order_form_shows_fixed_client_label(web_client, workflow_order):
    _login(web_client, workflow_order.user_lea_id)

    response = web_client.get(f"/orders/form/new?client_id={workflow_order.client_id}")

    assert response.status_code == 200
    page = _text(response)
    assert 'id="client_search"' not in page
    assert f'<input type="hidden" name="client_id" value="{workflow_order.client_id}">' in page
    assert 'class="order-client-label"' in page
    assert "John Doe" in page
    # Delivery address prefilled from the client's address
    assert "Tel Aviv\nIsrael</textarea>" in page


def test_order_form_requires_client_id(web_client, workflow_order):
    _login(web_client, workflow_order.user_lea_id)

    assert web_client.get("/orders/form/new").status_code == 400
    assert web_client.get("/orders/form/new?client_id=999999").status_code == 404


def test_edit_form_shows_fixed_client_label(web_client, workflow_order):
    _login(web_client, workflow_order.user_lea_id)

    response = web_client.get(f"/orders/{workflow_order.order_id}/form")

    assert response.status_code == 200
    page = _text(response)
    assert 'id="client_search"' not in page
    assert 'class="order-client-label"' in page
    assert "John Doe" in page


def _post_order(client, order_info, client_id):
    return client.post(
        "/orders/new",
        data={
            "client_id": str(client_id),
            "mode_livraison": "pickup",
            "lines[0][product_id]": str(order_info.product_id),
            "lines[0][color_id]": str(order_info.color_id),
            "lines[0][quantite]": "1",
            "lines[0][prix_vente_unitaire]": "180",
        },
    )


def test_order_create_rejects_unknown_client(web_client, workflow_order, db_session, test_app):
    _login(web_client, workflow_order.user_lea_id)
    models = test_app.models
    before = db_session.query(models.Order).count()

    response = _post_order(web_client, workflow_order, 999999)

    assert response.status_code == 400
    db_session.expire_all()
    assert db_session.query(models.Order).count() == before


def test_order_create_accepts_existing_client(web_client, workflow_order, db_session, test_app):
    _login(web_client, workflow_order.user_lea_id)
    models = test_app.models
    before = db_session.query(models.Order).filter_by(client_id=workflow_order.client_id).count()

    response = _post_order(web_client, workflow_order, workflow_order.client_id)

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.query(models.Order).filter_by(client_id=workflow_order.client_id).count() == before + 1


def test_client_create_reports_new_id(web_client, workflow_order, db_session, test_app):
    _login(web_client, workflow_order.user_lea_id)

    response = web_client.post("/clients/new", data={"nom": "Levy", "prenom": "Dana"})

    assert response.status_code == 200
    trigger = json.loads(response.headers["HX-Trigger"])
    new_id = trigger["clientCreated"]["id"]
    created = db_session.get(test_app.models.Client, new_id)
    assert created.full_name == "Dana Levy"


def test_create_order_rejects_unknown_client(db_session, test_app, workflow_order):
    with pytest.raises(ValueError, match="Client introuvable"):
        test_app.orders.create_order(db=db_session, client_id=999999, user_id=workflow_order.user_lea_id)


def test_order_create_non_numeric_client_id_is_rejected_cleanly(web_client, workflow_order):
    _login(web_client, workflow_order.user_lea_id)

    response = _post_order(web_client, workflow_order, "abc")

    assert response.status_code == 400
    assert response.get_json()["error"] == "Client requis"

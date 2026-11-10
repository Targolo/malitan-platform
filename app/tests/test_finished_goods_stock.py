"""
Finished-goods stock page: production needs must come only from paid orders;
unpaid orders are reported in their own column, never mixed in.
"""
import html as html_lib
import re


def _entry(db, order):
    from business_logic.finished_goods import get_all_stock_with_needs

    entries = get_all_stock_with_needs(db, product_id=order.product_id, color_id=order.color_id)
    assert len(entries) == 1
    return entries[0]


def test_unpaid_order_is_not_counted_in_production(db_session, test_app, workflow_order):
    entry = _entry(db_session, workflow_order)

    assert entry.besoin_production == 0
    assert entry.besoin_non_paye == 1


def test_paid_order_moves_to_production_column(db_session, test_app, workflow_order):
    test_app.orders.update_order_status(
        db_session, workflow_order.order_id, test_app.models.OrderStatus.PAYE, workflow_order.user_lea_id
    )

    entry = _entry(db_session, workflow_order)

    assert entry.besoin_production == 1
    assert entry.besoin_non_paye == 0


def _login(client, user_id):
    with client.session_transaction() as s:
        s["user_id"] = user_id
        s["username"] = "david"


def test_stock_page_shows_unpaid_column(web_client, workflow_order):
    _login(web_client, workflow_order.user_david_id)
    html = html_lib.unescape(web_client.get("/stock-produits-finis").get_data(as_text=True))

    assert re.search(r"1 non pay", html)
    assert not re.search(r"\d+ en prod", html)


def test_increment_button_adds_one_to_stock(db_session, web_client, workflow_order):
    from database.models import FinishedGoodsStock

    _login(web_client, workflow_order.user_david_id)
    html = web_client.get("/stock-produits-finis").get_data(as_text=True)
    assert 'title="Ajouter 1 au stock"' in html

    for _ in range(2):
        response = web_client.post("/stock-produits-finis/add", data={
            "product_id": workflow_order.product_id, "color_id": workflow_order.color_id, "quantity": 1,
        })
        assert response.status_code == 200

    db_session.expire_all()
    stock = db_session.query(FinishedGoodsStock).filter_by(
        product_id=workflow_order.product_id, color_id=workflow_order.color_id).one()
    assert stock.quantite_disponible == 2

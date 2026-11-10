"""
Rendered-page tests: dashboard, Production page and sidebar badge must agree on
how many objects (sum of line quantities, not order lines) are left to print.
"""
import html as html_lib
import re

import pytest


@pytest.fixture
def partially_printed_order(db_session, test_app, workflow_order):
    """
    Paid order with 6 objects: 1 in queue, 3 prioritized (PROCHAIN), 2 already printed.
    """
    orders_logic = test_app.orders
    production_logic = test_app.production
    models = test_app.models

    def add_line(quantite):
        return orders_logic.add_order_line(
            db=db_session, order_id=workflow_order.order_id, product_id=workflow_order.product_id,
            color_id=workflow_order.color_id, quantite=quantite, prix_vente_unitaire=180,
        )

    next_line = add_line(3)
    printed_line = add_line(2)
    orders_logic.update_order_status(db_session, workflow_order.order_id, models.OrderStatus.PAYE, workflow_order.user_lea_id)
    production_logic.set_fabrication_status_to_prochain(db_session, next_line.id)
    production_logic.complete_fabrication(db_session, printed_line.id, workflow_order.user_david_id)
    return workflow_order


def _get_as(client, user_id, path):
    with client.session_transaction() as s:
        s["user_id"] = user_id
        s["username"] = "david"
    response = client.get(path)
    assert response.status_code == 200
    return html_lib.unescape(response.get_data(as_text=True))


def _metric_cards(html):
    return {
        label: int(num)
        for num, label in re.findall(
            r'<div class="metric-num">\s*(\d+)\s*</div>\s*<div class="metric-label">([^<]+)</div>', html
        )
    }


def _nav_badge(html, label):
    match = re.search(label + r'\s*<span class="nav-count">(\d+)</span>', html)
    return int(match.group(1)) if match else 0


def test_dashboard_counts_objects_and_hides_printed_lines(web_client, partially_printed_order):
    html = _get_as(web_client, partially_printed_order.user_david_id, "/")

    assert _metric_cards(html) == {
        "Commandes en attente de paiement": 0,
        "Commandes en attente d'impression": 1,
        "Objets à imprimer": 4,
        "Commandes à l'emballage": 0,
        "Commandes en attente de livraison": 0,
    }
    assert "4 objets à imprimer" in html
    assert "1 commande en cours" in html
    # Only the two lines left to print are listed, not the printed one
    assert len(re.findall(r'class="q-item"', html)) == 2
    assert _nav_badge(html, 'Production') == 4


def test_dashboard_recent_orders_hide_delivered(web_client, test_app, db_session, packaged_order):
    orders_logic = test_app.orders
    order = db_session.get(test_app.models.Order, packaged_order.order_id)

    html = _get_as(web_client, packaged_order.user_david_id, "/")
    assert order.numero_commande in html

    orders_logic.mark_order_ready_for_delivery(db_session, order.id, packaged_order.user_lea_id)
    orders_logic.update_order_status(db_session, order.id, test_app.models.OrderStatus.LIVRE, packaged_order.user_lea_id)

    html = _get_as(web_client, packaged_order.user_david_id, "/")
    assert order.numero_commande not in html
    assert "Aucune commande en cours." in html


def test_production_page_counts_objects(web_client, partially_printed_order):
    html = _get_as(web_client, partially_printed_order.user_david_id, "/production")

    queue_count = re.search(r'Objets en file</p>\s*<p class="mini-value">(\d+)</p>', html)
    assert int(queue_count.group(1)) == 4
    section_counts = dict(re.findall(r'<h3>.*?</svg> ([^<]+?)</h3>\s*<span class="tag[^"]*">(\d+)</span>', html))
    assert section_counts == {
        "En cours d'impression": "0",
        "Prochain à imprimer": "3",
        "En file d'attente": "1",
        "Imprimé - commande incomplète": "2",
    }
    assert _nav_badge(html, 'Production') == 4


def test_delivery_badge_counts_pickup_orders(web_client, test_app, db_session, packaged_order):
    """Le badge Livraison compte toutes les commandes prêtes, retraits compris, comme la page Livraison."""
    order = db_session.get(test_app.models.Order, packaged_order.order_id)
    order.mode_livraison = test_app.models.DeliveryMode.PICKUP
    db_session.commit()
    test_app.orders.mark_order_ready_for_delivery(db_session, order.id, packaged_order.user_lea_id)

    html = _get_as(web_client, packaged_order.user_david_id, "/")
    assert _metric_cards(html)["Commandes en attente de livraison"] == 1
    assert _nav_badge(html, "Livraison") == 1

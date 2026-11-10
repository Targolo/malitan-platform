"""
Read-only status API for external agents (GET /api/status, bearer token).
"""
import importlib

import pytest

TOKEN = "test-status-token-" + "x" * 24  # 42 chars, above the 32-char minimum


@pytest.fixture
def mixed_order(db_session, test_app, workflow_order):
    """
    Paid order: 1 Bleu Nuit in queue, 3 Rouge prioritized, 2 Bleu Nuit already printed.
    """
    models = test_app.models
    orders_logic = test_app.orders
    production_logic = test_app.production

    rouge = models.Color(nom="Rouge", type_filament=models.FilamentType.PLA, stock_grammes=1000, cout_par_kg=100)
    db_session.add(rouge)
    db_session.commit()

    def add_line(color_id, quantite):
        return orders_logic.add_order_line(
            db=db_session, order_id=workflow_order.order_id, product_id=workflow_order.product_id,
            color_id=color_id, quantite=quantite, prix_vente_unitaire=180,
        )

    next_line = add_line(rouge.id, 3)
    printed_line = add_line(workflow_order.color_id, 2)
    orders_logic.update_order_status(db_session, workflow_order.order_id, models.OrderStatus.PAYE, workflow_order.user_lea_id)
    production_logic.set_fabrication_status_to_prochain(db_session, next_line.id)
    production_logic.complete_fabrication(db_session, printed_line.id, workflow_order.user_david_id)
    return workflow_order


def test_status_lists_objects_by_product_and_color(db_session, mixed_order):
    from business_logic.status_api import get_status_metrics

    metrics = get_status_metrics(db_session)

    assert list(metrics) == [
        "objets_a_imprimer", "objets_imprimes", "commandes_a_emballer", "commandes_en_attente_livraison",
    ]
    assert metrics["objets_a_imprimer"] == {
        "label": "Objets à imprimer",
        "count": 4,
        "items": [
            {"objet": "Lampe 3D", "couleur": "Bleu Nuit", "code_couleur": "#001f3f", "quantite": 1},
            {"objet": "Lampe 3D", "couleur": "Rouge", "code_couleur": None, "quantite": 3},
        ],
    }
    assert metrics["objets_imprimes"] == {
        "label": "Objets imprimés",
        "count": 2,
        "items": [{"objet": "Lampe 3D", "couleur": "Bleu Nuit", "code_couleur": "#001f3f", "quantite": 2}],
    }
    assert metrics["commandes_a_emballer"] == {"label": "Commandes à l'emballage", "count": 0}
    assert metrics["commandes_en_attente_livraison"] == {"label": "Commandes en attente de livraison", "count": 0}


def test_status_printed_objects_leave_once_packaged(db_session, test_app, packaged_order):
    from business_logic.status_api import get_status_metrics

    metrics = get_status_metrics(db_session)
    assert metrics["objets_imprimes"]["count"] == 1
    assert metrics["commandes_a_emballer"]["count"] == 1

    test_app.orders.mark_order_ready_for_delivery(db_session, packaged_order.order_id, packaged_order.user_lea_id)

    metrics = get_status_metrics(db_session)
    assert metrics["objets_imprimes"] == {"label": "Objets imprimés", "count": 0, "items": []}
    assert metrics["commandes_a_emballer"]["count"] == 0
    assert metrics["commandes_en_attente_livraison"]["count"] == 1


def test_status_ignores_unpaid_orders(db_session, workflow_order):
    from business_logic.status_api import get_status_metrics

    assert get_status_metrics(db_session)["objets_a_imprimer"] == {
        "label": "Objets à imprimer", "count": 0, "items": [],
    }


@pytest.fixture
def status_token(monkeypatch, test_app):
    monkeypatch.setattr(test_app.settings, "STATUS_API_TOKEN", TOKEN, raising=False)
    return TOKEN


def test_api_status_returns_metrics_with_valid_token(web_client, status_token, mixed_order, test_app):
    response = web_client.get("/api/status", headers={"Authorization": f"Bearer {status_token}"})

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.get_json()
    # Metrics keep the STATUS_METRICS order (it drives the presentation), not alphabetical order
    assert list(payload["metrics"]) == [
        "objets_a_imprimer", "objets_imprimes", "commandes_a_emballer", "commandes_en_attente_livraison",
    ]
    assert payload["version"] == test_app.settings.APP_VERSION
    assert payload["generated_at"]
    assert payload["metrics"]["objets_a_imprimer"]["count"] == 4
    # Only these fields may leave the app: no client, order number or price
    assert set(payload) == {"generated_at", "version", "metrics"}
    for metric in payload["metrics"].values():
        assert set(metric) <= {"label", "count", "items"}
        for item in metric.get("items", []):
            assert set(item) == {"objet", "couleur", "code_couleur", "quantite"}


@pytest.mark.parametrize("header", [None, "Bearer wrong-token", TOKEN, f"Basic {TOKEN}"])
def test_api_status_rejects_missing_or_wrong_token(web_client, status_token, header):
    headers = {"Authorization": header} if header else {}
    response = web_client.get("/api/status", headers=headers)

    assert response.status_code == 401
    assert "metrics" not in (response.get_json() or {})


def test_api_status_is_disabled_without_configured_token(web_client, monkeypatch, test_app):
    monkeypatch.setattr(test_app.settings, "STATUS_API_TOKEN", "", raising=False)

    response = web_client.get("/api/status", headers={"Authorization": "Bearer "})

    assert response.status_code == 404


def test_api_status_is_disabled_with_a_short_token(web_client, monkeypatch, test_app):
    """Un jeton faible ne doit pas pouvoir activer l'API par erreur."""
    short = "x" * 31
    monkeypatch.setattr(test_app.settings, "STATUS_API_TOKEN", short, raising=False)

    response = web_client.get("/api/status", headers={"Authorization": f"Bearer {short}"})

    assert response.status_code == 404


def test_status_token_ignores_surrounding_whitespace_in_env(monkeypatch, test_app):
    with monkeypatch.context() as m:
        m.setenv("STATUS_API_TOKEN", f"  {TOKEN}\n")
        settings = importlib.reload(test_app.settings)
        assert settings.STATUS_API_TOKEN == TOKEN
    importlib.reload(test_app.settings)

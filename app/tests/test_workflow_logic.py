"""
Unit tests that validate the business workflow logic without touching the UI.
"""
import pytest


def test_full_order_workflow(db_session, test_app, packaged_order):
    """
    Ensure an order flows from EN_PREPARATION to LIVRE through the packaging steps.
    """
    models = test_app.models
    orders_logic = test_app.orders

    order = db_session.get(models.Order, packaged_order.order_id)
    assert order.statut == models.OrderStatus.EN_PREPARATION

    packaging_queue = orders_logic.get_orders_ready_for_packaging(db_session)
    assert [o.id for o in packaging_queue] == [order.id]

    orders_logic.mark_order_ready_for_delivery(
        db_session,
        order.id,
        packaged_order.user_lea_id,
    )
    db_session.refresh(order)
    assert order.statut == models.OrderStatus.PRET_LIVRAISON

    delivery_queue = orders_logic.get_orders_ready_for_delivery(db_session)
    assert [o.id for o in delivery_queue] == [order.id]

    orders_logic.update_order_status(
        db_session,
        order.id,
        models.OrderStatus.LIVRE,
        packaged_order.user_lea_id,
    )
    db_session.refresh(order)
    assert order.statut == models.OrderStatus.LIVRE

    # Delivered orders must disappear from both queues
    assert not orders_logic.get_orders_ready_for_packaging(db_session)
    assert not orders_logic.get_orders_ready_for_delivery(db_session)


def test_skip_packaging_goes_through_transfer(db_session, test_app, skip_packaging_order):
    """
    Orders flagged as 'ne_pas_emballer' still land in EN_PREPARATION once printed
    ("À transférer"), and only reach delivery when Lea transfers them.
    """
    models = test_app.models
    orders_logic = test_app.orders
    production_logic = test_app.production

    orders_logic.update_order_status(
        db_session,
        skip_packaging_order.order_id,
        models.OrderStatus.PAYE,
        skip_packaging_order.user_lea_id,
    )
    production_logic.assign_printer(
        db_session,
        skip_packaging_order.line_id,
        skip_packaging_order.printer_id,
    )
    production_logic.start_fabrication(
        db_session,
        skip_packaging_order.line_id,
        printer_id=skip_packaging_order.printer_id,
    )
    production_logic.complete_fabrication(
        db_session,
        skip_packaging_order.line_id,
        skip_packaging_order.user_david_id,
    )

    order = db_session.get(models.Order, skip_packaging_order.order_id)
    assert order.statut == models.OrderStatus.EN_PREPARATION
    assert order.date_pret_livraison is None
    assert [o.id for o in orders_logic.get_orders_ready_for_packaging(db_session)] == [order.id]
    assert not orders_logic.get_orders_ready_for_delivery(db_session)

    orders_logic.mark_order_ready_for_delivery(
        db_session,
        order.id,
        skip_packaging_order.user_lea_id,
    )
    db_session.refresh(order)
    assert order.statut == models.OrderStatus.PRET_LIVRAISON
    assert order.date_pret_livraison is not None
    assert not orders_logic.get_orders_ready_for_packaging(db_session)
    assert [o.id for o in orders_logic.get_orders_ready_for_delivery(db_session)] == [order.id]


def test_mark_order_ready_requires_packaging_status(db_session, test_app, workflow_order):
    models = test_app.models
    orders_logic = test_app.orders

    order = db_session.get(models.Order, workflow_order.order_id)
    assert order.statut == models.OrderStatus.EN_ATTENTE

    with pytest.raises(ValueError):
        orders_logic.mark_order_ready_for_delivery(
            db_session,
            workflow_order.order_id,
            workflow_order.user_lea_id,
        )


def test_set_order_skip_packaging_keeps_order_to_transfer(db_session, test_app, packaged_order):
    models = test_app.models
    orders_logic = test_app.orders

    order = db_session.get(models.Order, packaged_order.order_id)
    assert order.statut == models.OrderStatus.EN_PREPARATION

    orders_logic.set_order_skip_packaging(
        db_session,
        packaged_order.order_id,
        True,
        packaged_order.user_lea_id,
    )
    db_session.refresh(order)
    assert order.statut == models.OrderStatus.EN_PREPARATION
    assert order.ne_pas_emballer is True
    assert order.date_pret_livraison is None


def test_check_order_printed_waits_for_all_lines(db_session, test_app, workflow_order):
    models = test_app.models
    orders_logic = test_app.orders
    production_logic = test_app.production

    # Add a second line to the same order
    second_line = orders_logic.add_order_line(
        db=db_session,
        order_id=workflow_order.order_id,
        product_id=workflow_order.product_id,
        color_id=workflow_order.color_id,
        quantite=1,
        prix_vente_unitaire=180,
    )

    orders_logic.update_order_status(
        db_session,
        workflow_order.order_id,
        models.OrderStatus.PAYE,
        workflow_order.user_lea_id,
    )

    production_logic.assign_printer(
        db_session,
        workflow_order.line_id,
        workflow_order.printer_id,
    )
    production_logic.start_fabrication(
        db_session,
        workflow_order.line_id,
        printer_id=workflow_order.printer_id,
    )
    production_logic.complete_fabrication(
        db_session,
        workflow_order.line_id,
        workflow_order.user_david_id,
    )

    order = db_session.get(models.Order, workflow_order.order_id)
    # Only one line printed, order should still wait in PAYE
    assert order.statut == models.OrderStatus.PAYE
    assert second_line.statut_fabrication == models.FabricationStatus.FILE_ATTENTE



def test_dashboard_counts_objects_of_paid_orders_only(db_session, test_app, workflow_order):
    """Objets en production = quantités des lignes à imprimer des commandes payées, PROCHAIN compris."""
    import business_logic.dashboard as dashboard

    models = test_app.models
    orders_logic = test_app.orders
    production_logic = test_app.production

    second_line = orders_logic.add_order_line(
        db=db_session, order_id=workflow_order.order_id, product_id=workflow_order.product_id,
        color_id=workflow_order.color_id, quantite=3, prix_vente_unitaire=180,
    )

    metrics = dashboard.get_dashboard_metrics(db_session)
    assert metrics["total_objets_en_prod"] == 0
    assert metrics["total_commandes_en_cours"] == 1

    orders_logic.update_order_status(db_session, workflow_order.order_id, models.OrderStatus.PAYE, workflow_order.user_lea_id)
    production_logic.set_fabrication_status_to_prochain(db_session, second_line.id)
    production_logic.start_fabrication(db_session, workflow_order.line_id, printer_id=workflow_order.printer_id)

    metrics = dashboard.get_dashboard_metrics(db_session)
    assert metrics["total_objets_en_prod"] == 4
    assert metrics["total_objets_en_cours"] == 1
    assert metrics["total_en_attente_impression"] == 1


def test_dashboard_partially_printed_order(db_session, test_app, workflow_order):
    """Une commande payée en partie imprimée reste « en attente d'impression » pour ses seuls objets restants."""
    import business_logic.dashboard as dashboard

    models = test_app.models
    orders_logic = test_app.orders
    production_logic = test_app.production

    printed_line = orders_logic.add_order_line(
        db=db_session, order_id=workflow_order.order_id, product_id=workflow_order.product_id,
        color_id=workflow_order.color_id, quantite=2, prix_vente_unitaire=180,
    )
    orders_logic.update_order_status(db_session, workflow_order.order_id, models.OrderStatus.PAYE, workflow_order.user_lea_id)
    production_logic.complete_fabrication(db_session, printed_line.id, workflow_order.user_david_id)

    metrics = dashboard.get_dashboard_metrics(db_session)
    assert metrics["total_en_attente_impression"] == 1
    assert metrics["total_objets_en_prod"] == 1
    assert metrics["total_a_emballer"] == 0

    production_logic.complete_fabrication(db_session, workflow_order.line_id, workflow_order.user_david_id)

    metrics = dashboard.get_dashboard_metrics(db_session)
    assert metrics["total_en_attente_impression"] == 0
    assert metrics["total_objets_en_prod"] == 0
    assert metrics["total_a_emballer"] == 1


def test_dashboard_order_counts_follow_packaging_and_delivery(db_session, test_app, packaged_order):
    import business_logic.dashboard as dashboard

    models = test_app.models
    orders_logic = test_app.orders

    metrics = dashboard.get_dashboard_metrics(db_session)
    assert metrics["total_objets_en_prod"] == 0
    assert metrics["total_a_emballer"] == 1
    assert metrics["total_commandes_en_cours"] == 1

    orders_logic.mark_order_ready_for_delivery(db_session, packaged_order.order_id, packaged_order.user_lea_id)
    orders_logic.update_order_status(
        db_session, packaged_order.order_id, models.OrderStatus.LIVRE, packaged_order.user_lea_id
    )
    metrics = dashboard.get_dashboard_metrics(db_session)
    assert metrics["total_a_emballer"] == 0
    assert metrics["total_commandes_en_cours"] == 0


def test_assign_busy_printer_then_start_is_blocked(db_session, test_app, workflow_order):
    """Une imprimante occupée peut être pré-assignée, mais pas démarrée tant qu'elle imprime."""
    models = test_app.models
    orders_logic = test_app.orders
    production_logic = test_app.production

    second_line = orders_logic.add_order_line(
        db=db_session,
        order_id=workflow_order.order_id,
        product_id=workflow_order.product_id,
        color_id=workflow_order.color_id,
        quantite=1,
        prix_vente_unitaire=180,
    )
    orders_logic.update_order_status(
        db_session, workflow_order.order_id, models.OrderStatus.PAYE, workflow_order.user_lea_id
    )
    production_logic.start_fabrication(db_session, workflow_order.line_id, printer_id=workflow_order.printer_id)

    production_logic.assign_printer(db_session, second_line.id, workflow_order.printer_id)
    assert second_line.printer_id == workflow_order.printer_id

    with pytest.raises(ValueError):
        production_logic.start_fabrication(db_session, second_line.id)


def test_cancel_paid_order_returns_printed_items_and_frees_printer(db_session, test_app, workflow_order):
    """Annuler une commande payée : objets imprimés remis en stock, imprimante libérée, motif tracé."""
    from database.models import FinishedGoodsStock, FinishedGoodsMovement, Printer, PrinterStatus
    import business_logic.dashboard as dashboard

    models = test_app.models
    orders_logic = test_app.orders
    production_logic = test_app.production

    second_line = orders_logic.add_order_line(
        db=db_session, order_id=workflow_order.order_id, product_id=workflow_order.product_id,
        color_id=workflow_order.color_id, quantite=1, prix_vente_unitaire=180,
    )
    orders_logic.update_order_status(db_session, workflow_order.order_id, models.OrderStatus.PAYE, workflow_order.user_lea_id)
    production_logic.start_fabrication(db_session, workflow_order.line_id, printer_id=workflow_order.printer_id)
    production_logic.complete_fabrication(db_session, workflow_order.line_id, workflow_order.user_david_id)
    production_logic.start_fabrication(db_session, second_line.id, printer_id=workflow_order.printer_id)

    order = orders_logic.cancel_order(db_session, workflow_order.order_id, workflow_order.user_lea_id, "Cliente a changé d'avis")

    assert order.statut == models.OrderStatus.ANNULE
    assert order.returned_to_stock == 1
    assert "Annulée le" in order.observations and "Cliente a changé d'avis" in order.observations
    stock = db_session.query(FinishedGoodsStock).filter_by(
        product_id=workflow_order.product_id, color_id=workflow_order.color_id).one()
    assert stock.quantite_disponible == 1
    assert db_session.query(FinishedGoodsMovement).filter_by(order_line_id=workflow_order.line_id).count() == 1
    db_session.refresh(second_line)
    assert second_line.statut_fabrication == models.FabricationStatus.FILE_ATTENTE
    assert db_session.get(Printer, workflow_order.printer_id).statut == PrinterStatus.DISPONIBLE

    metrics = dashboard.get_dashboard_metrics(db_session)
    assert metrics["total_en_attente_impression"] == 0
    assert metrics["total_objets_en_prod"] == 0
    assert not production_logic.get_production_queue(db_session)


def test_cancel_delivered_order_is_refused(db_session, test_app, packaged_order):
    models = test_app.models
    orders_logic = test_app.orders
    orders_logic.mark_order_ready_for_delivery(db_session, packaged_order.order_id, packaged_order.user_lea_id)
    orders_logic.update_order_status(db_session, packaged_order.order_id, models.OrderStatus.LIVRE, packaged_order.user_lea_id)
    with pytest.raises(ValueError):
        orders_logic.cancel_order(db_session, packaged_order.order_id, packaged_order.user_lea_id)


def test_cannot_mark_delivered_without_ready_for_delivery(db_session, test_app, packaged_order):
    """« Livré » n'est possible qu'après « Prêt à livrer », sinon la commande n'apparaît jamais en Livraison."""
    models = test_app.models
    orders_logic = test_app.orders
    with pytest.raises(ValueError):
        orders_logic.update_order_status(
            db_session, packaged_order.order_id, models.OrderStatus.LIVRE, packaged_order.user_lea_id
        )
    order = db_session.get(models.Order, packaged_order.order_id)
    assert order.statut == models.OrderStatus.EN_PREPARATION


def test_order_numbers_unique_within_same_second(db_session, test_app, workflow_order, monkeypatch):
    """Plusieurs commandes créées dans la même seconde reçoivent un suffixe au lieu d'entrer en collision."""
    from datetime import datetime as real_datetime

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 10, 11, 18, 0, 0)

    orders_logic = test_app.orders
    monkeypatch.setattr(orders_logic, "datetime", FrozenDatetime)
    numbers = [
        orders_logic.create_order(db_session, workflow_order.client_id, workflow_order.user_lea_id).numero_commande
        for _ in range(3)
    ]
    assert numbers == ["CMD-20261011-180000", "CMD-20261011-180000-2", "CMD-20261011-180000-3"]


def test_create_order_retries_when_number_taken_concurrently(db_session, test_app, workflow_order, monkeypatch):
    """Si une autre requête prend le numéro entre la vérification et le commit, create_order réessaie."""
    models = test_app.models
    orders_logic = test_app.orders
    taken = db_session.get(models.Order, workflow_order.order_id).numero_commande
    real_generate = orders_logic.generate_order_number
    calls = []

    def racing_generate(db):
        calls.append(1)
        return taken if len(calls) == 1 else real_generate(db)

    monkeypatch.setattr(orders_logic, "generate_order_number", racing_generate)
    order = orders_logic.create_order(db_session, workflow_order.client_id, workflow_order.user_lea_id)
    assert order.numero_commande != taken
    assert len(calls) == 2


def test_order_update_can_clear_delivery_date(web_client, db_session, test_app, workflow_order):
    """Emptying 'Date de livraison prévue' in the edit form removes the date."""
    from datetime import datetime

    models = test_app.models
    order = db_session.get(models.Order, workflow_order.order_id)
    order.date_livraison_prevue = datetime(2026, 11, 4)
    db_session.commit()
    with web_client.session_transaction() as s:
        s["user_id"] = workflow_order.user_lea_id

    response = web_client.post(
        f"/orders/{workflow_order.order_id}/update",
        data={"mode_livraison": "pickup", "date_livraison_prevue": "", "observations": ""},
    )

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(models.Order, workflow_order.order_id).date_livraison_prevue is None


def test_order_update_keeps_date_when_not_sent(db_session, test_app, workflow_order):
    """Callers that do not pass a date leave it untouched."""
    from datetime import datetime

    models = test_app.models
    order = db_session.get(models.Order, workflow_order.order_id)
    order.date_livraison_prevue = datetime(2026, 11, 4)
    db_session.commit()

    test_app.orders.update_order_details(db_session, order.id, workflow_order.user_lea_id, observations="x")

    db_session.expire_all()
    assert db_session.get(models.Order, order.id).date_livraison_prevue == datetime(2026, 11, 4)

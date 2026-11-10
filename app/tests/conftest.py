import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure the repository root is importable (handles spaces in path like "Malitan 3D")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _reload_modules():
    """
    Reload core modules so tests always use the in-memory SQLite database.
    """
    import config.settings  # noqa: F401
    import database.models  # noqa: F401
    import database.connection  # noqa: F401
    import business_logic.catalogue  # noqa: F401
    import business_logic.orders  # noqa: F401
    import business_logic.production  # noqa: F401
    import utils.auth  # noqa: F401

    return SimpleNamespace(
        settings=importlib.reload(config.settings),
        models=importlib.reload(database.models),
        connection=importlib.reload(database.connection),
        catalogue=importlib.reload(business_logic.catalogue),
        orders=importlib.reload(business_logic.orders),
        production=importlib.reload(business_logic.production),
        auth=importlib.reload(utils.auth),
    )


@pytest.fixture(scope="session")
def test_app(tmp_path_factory):
    """
    Configure the application to use an isolated SQLite database file for tests.
    """
    db_file = tmp_path_factory.mktemp("db") / "malitan_workflow.sqlite"
    os.environ["ENV"] = "development"
    os.environ["DATABASE_TYPE"] = "sqlite"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"
    # Hermetic: the caller's shell must not change the run, nor enable real services
    os.environ["AUTH_SECRET_KEY"] = "tests-only-auth-secret-key-not-a-real-secret"
    os.environ["ENABLE_PACKAGING_EMAILS"] = "false"
    for var in ("BREVO_API_KEY", "BULKGATE_APPLICATION_ID", "BULKGATE_APPLICATION_TOKEN",
                "TELEGRAM_BOT_TOKEN", "STATUS_API_TOKEN", "SESSION_COOKIE_SECURE"):
        os.environ.pop(var, None)

    modules = _reload_modules()
    modules.connection.init_database()
    modules.models.Base.metadata.create_all(bind=modules.connection.engine)

    yield modules

    modules.connection.close_db()


@pytest.fixture(autouse=True)
def clean_database(test_app):
    """
    Wipe all tables between tests to guarantee isolation.
    """
    metadata = test_app.models.Base.metadata
    engine = test_app.connection.engine
    with engine.begin() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def db_session(test_app):
    """
    Provide a SQLAlchemy session tied to the test database.
    """
    session = test_app.connection.get_db_session()
    try:
        yield session
    finally:
        session.close()


def create_test_order(db, modules, *, skip_packaging: bool = False):
    """
    Seed the minimum data set required to exercise the workflow.
    """
    m = modules.models
    auth = modules.auth

    lea = m.User(
        username="lea",
        password_hash=auth.hash_password("dev123"),
        full_name="Lea Test",
        role=m.UserRole.LEA,
    )
    david = m.User(
        username="david",
        password_hash=auth.hash_password("dev123"),
        full_name="David Test",
        role=m.UserRole.DAVID,
    )
    client = m.Client(
        nom="Doe",
        prenom="John",
        email="john@example.com",
        telephone="+972501234567",
        adresse="Tel Aviv\nIsrael",
        created_by=lea.id if lea.id else None,
    )
    color = m.Color(
        nom="Bleu Nuit",
        nom_original="Navy",
        code_hex="#001f3f",
        marque="Prusament",
        type_filament=m.FilamentType.PLA,
        stock_grammes=5000,
        cout_par_kg=100,
        seuil_alerte=500,
    )
    product = m.Product(
        nom="Lampe 3D",
        description="Lampe décorative",
        product_type=m.ProductType.PARTAGE_BENEFICES,
        prix_revient_david=35,
        prix_revient_lea=15,
        prix_vente_base=180,
        poids_grammes=150,
        temps_impression_minutes=360,
        personalisable=False,
        gestion_stock=True,
    )
    printer = m.Printer(
        nom="Prusa MK4",
        modele="MK4",
        statut=m.PrinterStatus.DISPONIBLE,
    )

    db.add_all([lea, david, client, color, product, printer])
    db.commit()

    order = modules.orders.create_order(
        db=db,
        client_id=client.id,
        user_id=lea.id,
        observations="Commande de test workflow",
    )
    line = modules.orders.add_order_line(
        db=db,
        order_id=order.id,
        product_id=product.id,
        color_id=color.id,
        quantite=1,
        prix_vente_unitaire=product.prix_vente_base,
    )

    if skip_packaging:
        modules.orders.set_order_skip_packaging(db, order.id, True, lea.id)

    db.refresh(order)
    db.refresh(line)

    return SimpleNamespace(
        order_id=order.id,
        line_id=line.id,
        user_lea_id=lea.id,
        user_lea_username=lea.username,
        user_password="dev123",
        user_david_id=david.id,
        printer_id=printer.id,
        client_id=client.id,
        product_id=product.id,
        color_id=color.id,
    )


@pytest.fixture
def workflow_order(db_session, test_app):
    """
    Provide a freshly created order that starts in EN_ATTENTE with one line.
    """
    return create_test_order(db_session, test_app)


@pytest.fixture
def skip_packaging_order(db_session, test_app):
    """
    Provide an order configured with the 'do not package' flag.
    """
    return create_test_order(db_session, test_app, skip_packaging=True)


@pytest.fixture
def packaged_order(db_session, test_app, workflow_order):
    """
    Drive the workflow until the order reaches EN_PREPARATION.
    """
    modules = test_app
    orders_logic = modules.orders
    production_logic = modules.production
    models = modules.models

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

    return workflow_order


@pytest.fixture
def ready_skip_packaging_order(db_session, test_app):
    """
    Provide an order flagged as 'ne pas emballer', printed and transferred (PRET_LIVRAISON).
    """
    order_info = create_test_order(db_session, test_app, skip_packaging=True)
    modules = test_app
    orders_logic = modules.orders
    production_logic = modules.production
    models = modules.models

    orders_logic.update_order_status(
        db_session,
        order_info.order_id,
        models.OrderStatus.PAYE,
        order_info.user_lea_id,
    )
    production_logic.assign_printer(
        db_session,
        order_info.line_id,
        order_info.printer_id,
    )
    production_logic.start_fabrication(
        db_session,
        order_info.line_id,
        printer_id=order_info.printer_id,
    )
    production_logic.complete_fabrication(
        db_session,
        order_info.line_id,
        order_info.user_david_id,
    )
    orders_logic.mark_order_ready_for_delivery(
        db_session,
        order_info.order_id,
        order_info.user_lea_id,
    )

    order = db_session.get(models.Order, order_info.order_id)
    assert order.statut == models.OrderStatus.PRET_LIVRAISON
    assert order.ne_pas_emballer is True
    return order_info


@pytest.fixture
def web_client(test_app):
    """
    Flask test client on the test database (log in by setting session['user_id']).
    """
    import flask_app

    app_module = importlib.reload(flask_app)
    return app_module.app.test_client()

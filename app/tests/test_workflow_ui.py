"""
Selenium end-to-end tests that validate the Flask UI follows the workflow.
"""
import importlib
import os
import threading
import time
from datetime import datetime
from urllib.parse import urljoin

import pytest
from selenium import webdriver  # type: ignore[import]
from selenium.common.exceptions import TimeoutException, WebDriverException  # type: ignore[import]
from selenium.webdriver.chrome.options import Options as ChromeOptions  # type: ignore[import]
from selenium.webdriver.common.by import By  # type: ignore[import]
from selenium.webdriver.common.action_chains import ActionChains  # type: ignore[import]
from selenium.webdriver.common.keys import Keys  # type: ignore[import]
from selenium.webdriver.support import expected_conditions as EC  # type: ignore[import]
from selenium.webdriver.support.ui import WebDriverWait  # type: ignore[import]
from werkzeug.serving import make_server


@pytest.fixture(scope="session")
def flask_server(test_app):
    """
    Launch the Flask application on a background thread for UI tests.
    """
    import flask_app

    app_module = importlib.reload(flask_app)
    server = make_server("127.0.0.1", 5001, app_module.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield "http://127.0.0.1:5001"

    server.shutdown()
    thread.join()


@pytest.fixture
def selenium_driver():
    """
    Provide a headless Chrome driver (skips test gracefully if unavailable).
    """
    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1600,1200")
    options.add_argument("--no-sandbox")
    chrome_binary = os.getenv("CHROME_BINARY")
    if chrome_binary:
        options.binary_location = chrome_binary

    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as exc:
        pytest.skip(f"Chrome driver unavailable: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        pytest.skip(f"Unable to start Selenium driver: {exc}")

    yield driver

    driver.quit()


def _accept_alert_if_present(driver, timeout: int = 3):
    """
    Accept htmx confirmation dialogs if they appear.
    """
    try:
        WebDriverWait(driver, timeout).until(EC.alert_is_present())
        driver.switch_to.alert.accept()
    except TimeoutException:
        return


def _login(driver, base_url, username, password):
    driver.get(urljoin(base_url, "/login"))
    driver.find_element(By.NAME, "username").send_keys(username)
    driver.find_element(By.NAME, "password").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    WebDriverWait(driver, 10).until(lambda d: "/login" not in d.current_url)


def _wait_for_order_status(db_session, models, order_id, expected_status, timeout: int = 10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db_session.expire_all()
        order = db_session.get(models.Order, order_id)
        if order and order.statut == expected_status:
            return order
        time.sleep(0.2)
    raise AssertionError(f"Order {order_id} did not reach status {expected_status}")


def _post_via_fetch(driver, url: str):
    driver.execute_async_script(
        """
        const postUrl = arguments[0];
        const done = arguments[1];
        fetch(postUrl, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'HX-Request': 'true',
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
        .then(response => response.text())
        .then(() => done(true))
        .catch(error => done('ERROR:' + error));
        """,
        url,
    )


@pytest.mark.ui
def test_packaging_to_delivery_ui_flow(
    flask_server,
    selenium_driver,
    db_session,
    test_app,
    packaged_order,
):
    """
    Simulate Lea packaging a printed order then delivering it to the client.
    """
    base_url = flask_server
    driver = selenium_driver
    wait = WebDriverWait(driver, 10)
    models = test_app.models

    order = db_session.get(models.Order, packaged_order.order_id)
    order_number = order.numero_commande

    # Login
    _login(driver, base_url, packaged_order.user_lea_username, packaged_order.user_password)

    # Packaging page
    driver.get(urljoin(base_url, "/emballage"))
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".packaging-card")))
    card_title = driver.find_element(By.CSS_SELECTOR, ".packaging-card h3").text
    assert order_number in card_title

    _post_via_fetch(driver, urljoin(base_url, f"/emballage/orders/{packaged_order.order_id}/ready"))
    _wait_for_order_status(db_session, models, packaged_order.order_id, models.OrderStatus.PRET_LIVRAISON)
    driver.get(urljoin(base_url, "/emballage"))
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".empty-state")))

    # Delivery page
    driver.get(urljoin(base_url, "/livraison"))
    card_selector = f"#order-{packaged_order.order_id} .order-number"
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, card_selector)))
    assert order_number in driver.find_element(By.CSS_SELECTOR, card_selector).text

    _post_via_fetch(driver, urljoin(base_url, f"/livraison/orders/{packaged_order.order_id}/deliver"))
    _wait_for_order_status(db_session, models, packaged_order.order_id, models.OrderStatus.LIVRE)
    driver.get(urljoin(base_url, "/livraison"))
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".empty-state")))

    db_session.refresh(order)
    assert order.statut == models.OrderStatus.LIVRE


@pytest.mark.ui
def test_delivery_no_packaged_orders_flow(
    flask_server,
    selenium_driver,
    db_session,
    test_app,
    ready_skip_packaging_order,
):
    """
    Validate that 'ne pas emballer' orders show in the dedicated delivery section and can be completed.
    """
    base_url = flask_server
    driver = selenium_driver
    wait = WebDriverWait(driver, 10)
    models = test_app.models

    order = db_session.get(models.Order, ready_skip_packaging_order.order_id)
    order_number = order.numero_commande

    _login(driver, base_url, ready_skip_packaging_order.user_lea_username, ready_skip_packaging_order.user_password)

    driver.get(urljoin(base_url, "/livraison"))
    card = wait.until(
        EC.presence_of_element_located((By.ID, f"order-{ready_skip_packaging_order.order_id}"))
    )
    assert order_number in card.find_element(By.CSS_SELECTOR, ".order-number").text
    assert "Ne pas emballer" in card.text

    _post_via_fetch(driver, urljoin(base_url, f"/livraison/orders/{ready_skip_packaging_order.order_id}/deliver"))
    _wait_for_order_status(db_session, models, ready_skip_packaging_order.order_id, models.OrderStatus.LIVRE)
    driver.get(urljoin(base_url, "/livraison"))
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".empty-state")))

    db_session.refresh(order)
    assert order.statut == models.OrderStatus.LIVRE



def _click_livre_and_wait_reload(driver, order_id: int):
    """Click the real "Livré" button and wait for a full page reload (not an in-place HTMX swap)."""
    driver.execute_script("window.__beforeDeliver = true;")
    button = driver.find_element(By.CSS_SELECTOR, f"#order-{order_id} .order-summary-actions button")
    button.click()
    _accept_alert_if_present(driver)
    WebDriverWait(driver, 10).until(lambda d: d.execute_script("return window.__beforeDeliver === undefined"))
    WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")


@pytest.mark.ui
def test_delivery_page_two_consecutive_livre_clicks(
    flask_server,
    selenium_driver,
    db_session,
    test_app,
    ready_skip_packaging_order,
):
    """
    Regression: the second "Livré" click used to swap the whole <body> via HTMX and loop on GET /livraison.
    """
    base_url = flask_server
    driver = selenium_driver
    wait = WebDriverWait(driver, 10)
    models = test_app.models
    info = ready_skip_packaging_order

    second = test_app.orders.create_order(
        db=db_session,
        client_id=info.client_id,
        user_id=info.user_lea_id,
        skip_packaging=True,
    )
    test_app.orders.add_order_line(
        db=db_session,
        order_id=second.id,
        product_id=info.product_id,
        color_id=info.color_id,
        quantite=1,
        prix_vente_unitaire=180,
    )
    second.statut = models.OrderStatus.PRET_LIVRAISON
    db_session.commit()

    _login(driver, base_url, info.user_lea_username, info.user_password)
    driver.get(urljoin(base_url, "/livraison"))
    wait.until(EC.presence_of_element_located((By.ID, f"order-{second.id}")))

    _click_livre_and_wait_reload(driver, info.order_id)
    _wait_for_order_status(db_session, models, info.order_id, models.OrderStatus.LIVRE)
    assert not driver.find_elements(By.ID, f"order-{info.order_id}")

    _click_livre_and_wait_reload(driver, second.id)
    _wait_for_order_status(db_session, models, second.id, models.OrderStatus.LIVRE)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".empty-state")))


def _open_client_picker(driver, base_url, wait):
    driver.get(urljoin(base_url, "/orders"))
    driver.find_element(By.CSS_SELECTOR, "button[onclick=\"openOrderModal('new')\"]").click()
    return wait.until(EC.visibility_of_element_located((By.ID, "picker-continue")))


@pytest.mark.ui
def test_new_order_starts_from_existing_client(flask_server, selenium_driver, workflow_order):
    """
    Typing a name is not enough: the order form opens only for a client picked from the list.
    """
    driver = selenium_driver
    wait = WebDriverWait(driver, 10)
    _login(driver, flask_server, workflow_order.user_lea_username, workflow_order.user_password)

    continue_button = _open_client_picker(driver, flask_server, wait)
    assert not continue_button.is_enabled()

    search = driver.find_element(By.ID, "client_search")
    search.send_keys("Joh")
    assert not continue_button.is_enabled()

    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".client-result-item:not(.no-results)"))).click()
    assert continue_button.is_enabled()

    # Editing the text after a pick cancels the pick
    search.send_keys("x")
    assert not continue_button.is_enabled()
    search.send_keys(Keys.BACKSPACE)
    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".client-result-item:not(.no-results)"))).click()

    continue_button.click()
    label = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".order-client-label")))
    assert "John Doe" in label.text
    hidden = driver.find_element(By.CSS_SELECTOR, "#order-form input[name='client_id']")
    assert hidden.get_attribute("value") == str(workflow_order.client_id)


@pytest.mark.ui
def test_new_order_starts_from_new_client(flask_server, selenium_driver, workflow_order, db_session, test_app):
    """
    'Nouveau client' opens the client form; creating it goes straight to the order form for that client.
    """
    driver = selenium_driver
    wait = WebDriverWait(driver, 10)
    _login(driver, flask_server, workflow_order.user_lea_username, workflow_order.user_password)

    _open_client_picker(driver, flask_server, wait)
    # The "no match" list must not cover the 'Nouveau client' button
    driver.find_element(By.ID, "client_search").send_keys("Inconnu")
    wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".client-result-item.no-results")))

    # Cancelling the client form (button or Escape) returns to the picker
    driver.find_element(By.ID, "picker-new-client").click()
    wait.until(EC.visibility_of_element_located((By.ID, "nom-new")))
    driver.find_element(By.XPATH, "//form//button[contains(., 'Annuler')]").click()
    wait.until(EC.visibility_of_element_located((By.ID, "picker-new-client"))).click()
    wait.until(EC.visibility_of_element_located((By.ID, "nom-new"))).send_keys(Keys.ESCAPE)
    wait.until(EC.visibility_of_element_located((By.ID, "picker-new-client"))).click()

    wait.until(EC.visibility_of_element_located((By.ID, "nom-new"))).send_keys("Levy")
    driver.find_element(By.ID, "prenom-new").send_keys("Dana")
    # A double click must not create the client twice
    ActionChains(driver).double_click(driver.find_element(By.XPATH, "//form//button[@type='submit']")).perform()

    label = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, ".order-client-label")))
    assert "Dana Levy" in label.text
    created = db_session.query(test_app.models.Client).filter_by(nom="Levy").one()
    hidden = driver.find_element(By.CSS_SELECTOR, "#order-form input[name='client_id']")
    assert hidden.get_attribute("value") == str(created.id)
    time.sleep(1)  # let a queued second submit land, if any
    db_session.expire_all()
    assert db_session.query(test_app.models.Client).filter_by(nom="Levy").count() == 1


@pytest.mark.ui
def test_production_rows_keep_a_fixed_two_line_layout(
    flask_server, selenium_driver, workflow_order, db_session, test_app
):
    """
    Every production card shows what to print on line 1 and the order context (date first) on line 2,
    whatever the color name or personalization length.
    """
    models = test_app.models
    orders_logic = test_app.orders
    order = db_session.get(models.Order, workflow_order.order_id)
    order.date_livraison_prevue = datetime(2026, 11, 4)
    db_session.commit()
    for personalization in ["Elie", "Aaron et Mia", "Une personnalisation vraiment très longue pour un objet"]:
        orders_logic.add_order_line(
            db=db_session, order_id=order.id, product_id=workflow_order.product_id,
            color_id=workflow_order.color_id, quantite=1, prix_vente_unitaire=180,
            personnalisation=personalization,
        )
    orders_logic.update_order_status(db_session, order.id, models.OrderStatus.PAYE, workflow_order.user_lea_id)

    driver = selenium_driver
    driver.set_window_size(1280, 900)
    _login(driver, flask_server, workflow_order.user_lea_username, workflow_order.user_password)
    driver.get(urljoin(flask_server, "/production"))
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".production-row")))

    rows = driver.execute_script(
        """
        const tops = el => [...el.children].map(c => Math.round(c.getBoundingClientRect().top));
        const visible = [...document.querySelectorAll('.production-row')].filter(r => r.offsetParent !== null);
        return visible.map(row => ({
            title: tops(row.querySelector('.production-title')),
            meta: tops(row.querySelector('.production-order-meta')),
            metaFirst: row.querySelector('.production-order-meta').firstElementChild.className,
        }));
        """
    )
    assert len(rows) == 4
    for row in rows:
        # Same line = tops within a few px (chips of different heights are centered)
        assert max(row["title"]) - min(row["title"]) < 12, row
        assert max(row["meta"]) - min(row["meta"]) < 12, row
    for row in rows:
        assert "delivery-chip" in row["metaFirst"], row


@pytest.mark.ui
def test_edit_order_clears_delivery_date_with_x(flask_server, selenium_driver, workflow_order, db_session, test_app):
    """The × next to 'Date de livraison prévue' empties the field; saving removes the date."""
    models = test_app.models
    order = db_session.get(models.Order, workflow_order.order_id)
    order.date_livraison_prevue = datetime(2026, 11, 4)
    order.adresse_livraison = "Tel Aviv"  # required to save a delivered order
    db_session.commit()

    driver = selenium_driver
    wait = WebDriverWait(driver, 10)
    _login(driver, flask_server, workflow_order.user_lea_username, workflow_order.user_password)
    driver.get(urljoin(flask_server, "/orders"))
    driver.execute_script("openOrderModal('edit', arguments[0])", workflow_order.order_id)

    date_input = wait.until(EC.visibility_of_element_located((By.ID, "date_livraison_prevue")))
    assert date_input.get_attribute("value") == "2026-11-04"
    clear = driver.find_element(By.CSS_SELECTOR, ".date-clear")
    clear.click()
    assert date_input.get_attribute("value") == ""
    assert not clear.is_displayed()

    driver.find_element(By.XPATH, "//form[@id='order-form']//button[@type='submit']").click()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        db_session.expire_all()
        if db_session.get(models.Order, workflow_order.order_id).date_livraison_prevue is None:
            break
        time.sleep(0.2)
    assert db_session.get(models.Order, workflow_order.order_id).date_livraison_prevue is None

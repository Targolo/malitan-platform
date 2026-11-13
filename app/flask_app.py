"""
Malitan 3D Manager - Flask Application
"""
import getpass
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import click
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, send_from_directory, flash, get_flashed_messages
from dotenv import find_dotenv, load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import make_url

# Load environment variables
# Priority: .env.flask_local (local dev) > .env.production (production) > .env (fallback)
# In production these vars are also injected by systemd (EnvironmentFile=.env.production);
# this load_dotenv is a redundant safety net so a manual `python flask_app.py` run still works.
flask_env_file = Path(__file__).parent / '.env.flask_local'
if flask_env_file.exists():
    load_dotenv(flask_env_file)
    print(f"✅ Loaded Flask local config: {flask_env_file}")
else:
    # Try production config (single consolidated file)
    flask_prod_file = Path(__file__).parent / '.env.production'
    if flask_prod_file.exists():
        load_dotenv(flask_prod_file)
        print(f"✅ Loaded Flask production config: {flask_prod_file}")
    else:
        # Fallback to regular .env (searched upward from this file); in a container
        # there is usually none and the variables come from the process environment.
        default_env_file = find_dotenv()
        if default_env_file:
            load_dotenv(default_env_file)
            print(f"✅ Loaded config: {default_env_file}")
        else:
            print("ℹ️  No .env file found; using process environment variables")

# Import settings after loading the appropriate .env so DATABASE_URL est à jour
import config.settings as app_settings
from config.settings import APP_NAME, APP_VERSION, AUTH_SECRET_KEY, DATABASE_URL, DATABASE_TYPE, ENV, SESSION_COOKIE_SECURE, get_database_url
from database.connection import init_database, get_db_session
from database.models import OrderStatus, Order, OrderLine, FabricationStatus, DeliveryMode

# Add parent directory to path to import existing modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Initialize Flask app
app = Flask(__name__, template_folder='flask_templates', static_folder='flask_static')
app.config['SECRET_KEY'] = AUTH_SECRET_KEY
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = SESSION_COOKIE_SECURE

# Filtre Jinja2 pour formater les statuts de commande
@app.template_filter('format_order_status')
def format_order_status(status):
    """Formate le statut de commande pour l'affichage"""
    status_labels = {
        OrderStatus.EN_ATTENTE: 'En attente',
        OrderStatus.PAYE: 'Payé',
        OrderStatus.IMPRIME: 'Imprimé',
        OrderStatus.EN_PREPARATION: 'À l\'emballage',
        OrderStatus.PRET_LIVRAISON: 'Prêt à livrer',
        OrderStatus.LIVRE: 'Livré',
        OrderStatus.ANNULE: 'Annulé'
    }
    return status_labels.get(status, status.value.replace('_', ' ').title())

# Filtre Jinja2 pour extraire le nom de fichier d'un chemin
@app.template_filter('basename')
def basename_filter(path):
    """Extrait le nom de fichier d'un chemin"""
    if not path:
        return ''
    return os.path.basename(path)

ISRAEL_TZ = ZoneInfo('Asia/Jerusalem')

@app.template_filter('israel_time')
def israel_time_filter(value, fmt='%d/%m/%Y %H:%M'):
    """Convertit un datetime UTC en heure israélienne"""
    if not value:
        return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ISRAEL_TZ).strftime(fmt)

def _display_db_url(url: str) -> str:
    """Database URL safe for logs: password replaced by ***."""
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return '[unparseable database URL]'


# Initialize database
# Uses .env.flask_local (local dev) or .env.production (production)
print(f"📊 Database Type: {DATABASE_TYPE}")
if DATABASE_URL:
    display_env_url = _display_db_url(DATABASE_URL)
    if len(display_env_url) > 200:
        display_env_url = display_env_url[:197] + "..."
    print(f"📊 Database URL (from env): {display_env_url}")
else:
    print("📊 Database URL (from env): [non défini]")

# Vérifier quelle URL sera réellement utilisée
actual_url = get_database_url()
display_actual_url = _display_db_url(actual_url)
if len(display_actual_url) > 200:
    display_actual_url = display_actual_url[:197] + "..."
print(f"📊 Database URL (actual): {display_actual_url}")

# Vérifier le port utilisé
parsed = urlparse(actual_url)
print(f"📊 Hostname: {parsed.hostname}")
print(f"📊 Port: {parsed.port or 'default'}")
if actual_url.startswith('sqlite:///'):
    sqlite_file = actual_url.replace('sqlite:///', '')
    from pathlib import Path as _Path
    try:
        print(f"🗂️  SQLite file in use: {_Path(sqlite_file).resolve()}")
    except Exception:
        print(f"🗂️  SQLite relative path: {sqlite_file}")

try:
    init_database()
    print("✅ Database initialized successfully")
except Exception as e:
    # The message (and traceback) can quote the raw URL, password included
    error_message = str(e).replace(actual_url, display_actual_url) if actual_url else str(e)
    print(f"❌ ERROR: Failed to initialize database: {type(e).__name__}: {error_message}")
    # Don't crash - let Flask start anyway, errors will show when accessing DB
    print("⚠️  Continuing anyway - database errors will show when accessing routes")

# Add teardown context to ensure DB sessions are closed
@app.teardown_appcontext
def shutdown_session(exception=None):
    """Close database session at the end of the request"""
    from database.connection import close_db
    close_db()


def login_required(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.context_processor
def inject_app_metadata():
    """Expose app metadata to every template."""
    return {
        'app_name': APP_NAME,
        'app_version': APP_VERSION,
        'app_logo_path': 'img/logo.svg'
    }


@app.context_processor
def inject_menu_counts():
    """Inject menu notification counts to every template."""
    if not session.get('user_id'): 
        return {}
        
    try:
        # Use get_db_session() which returns the scoped session
        # Do NOT close it here, as it might be used by the view function (DetachedInstanceError)
        # It will be closed by teardown_appcontext
        db = get_db_session()
        
        # 1. Commandes non payées (Status EN_ATTENTE)
        nb_commandes_non_payees = db.query(Order).filter(Order.statut == OrderStatus.EN_ATTENTE).count()
        
        # 2. Objets à imprimer : même calcul que le tableau de bord
        from business_logic.dashboard import count_objects_to_print
        nb_objets_file_attente = count_objects_to_print(db)
        
        # 3. Emballage (EN_PREPARATION) - "pickup ou livraison"
        nb_objets_a_emballer = db.query(Order).filter(
            Order.statut == OrderStatus.EN_PREPARATION
        ).count()
        
        # 4. Livraison (PRET_LIVRAISON, pickup compris : la page Livraison liste les deux modes)
        nb_objets_a_envoyer = db.query(Order).filter(
            Order.statut == OrderStatus.PRET_LIVRAISON
        ).count()
        
        return {
            'nb_commandes_non_payees': nb_commandes_non_payees,
            'nb_objets_file_attente': nb_objets_file_attente,
            'nb_objets_a_emballer': nb_objets_a_emballer,
            'nb_objets_a_envoyer': nb_objets_a_envoyer
        }
    except Exception as e:
        # Fail silently for menu counts to not break the app
        # print(f"Error calculating menu counts: {e}")
        return {}


def _parse_int_param(value, allow_all: bool = True):
    """
    Safely parse an integer from query/form parameters.
    Returns None when value is empty or equals 'Tous'/'all' (when allow_all=True).
    """
    if value is None:
        return None
    if allow_all and str(value).lower() in {'', 'tous', 'all'}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_bool_param(value, default: bool = False) -> bool:
    """
    Parse boolean-ish values from query/form parameters.
    """
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'on', 'oui', 'yes'}


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        from utils.auth import authenticate_user
        user = authenticate_user(request.form['username'], request.form['password'])
        if user:
            session['user_id'] = user.id
            session['username'] = user.username
            return redirect(url_for('dashboard'))
        return render_template('auth/login.html', error="Identifiants incorrects")
    return render_template('auth/login.html')


@app.route('/logout')
def logout():
    """Logout"""
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def dashboard():
    """Accueil - Page d'accueil"""
    db = get_db_session()
    try:
        from business_logic.dashboard import get_dashboard_metrics
        from business_logic.automation import get_all_alerts
        from business_logic.production import get_production_queue
        from database.models import Order, OrderStatus
        from sqlalchemy.orm import joinedload

        metrics = get_dashboard_metrics(db)
        alerts = get_all_alerts(db)

        # File de fabrication : prochains objets à imprimer (priorité EN_COURS > PROCHAIN > file).
        # Les lignes déjà imprimées restent dans get_production_queue mais n'ont plus rien à faire ici.
        fab_priority = {
            FabricationStatus.EN_COURS: 0,
            FabricationStatus.PROCHAIN: 1,
            FabricationStatus.FILE_ATTENTE: 2,
        }
        production_queue = sorted(
            (l for l in get_production_queue(db) if l.statut_fabrication in fab_priority),
            key=lambda l: (fab_priority[l.statut_fabrication], l.order.date_commande),
        )[:6]

        # Dernières commandes en cours (hors livrées et annulées)
        recent_orders = (
            db.query(Order)
            .options(joinedload(Order.client))
            .filter(Order.statut.notin_([OrderStatus.LIVRE, OrderStatus.ANNULE]))
            .order_by(Order.date_commande.desc())
            .limit(6)
            .all()
        )

        return render_template(
            'dashboard.html',
            metrics=metrics,
            alerts=alerts,
            production_queue=production_queue,
            recent_orders=recent_orders,
        )
    finally:
        db.close()


@app.route('/api/status')
def api_status():
    """Indicateurs en lecture seule pour les agents externes (jeton Bearer, sans session)."""
    import hmac
    import json
    import config.settings as settings
    from business_logic.status_api import get_status_metrics

    # The env is read once at startup: changing STATUS_API_TOKEN needs a service restart
    token = settings.STATUS_API_TOKEN
    if len(token) < settings.STATUS_API_TOKEN_MIN_LENGTH:
        if token:
            app.logger.warning('STATUS_API_TOKEN is shorter than %d characters: /api/status stays disabled',
                               settings.STATUS_API_TOKEN_MIN_LENGTH)
        return jsonify({'error': 'Not found'}), 404
    provided = request.headers.get('Authorization', '')
    if not hmac.compare_digest(provided.encode(), f'Bearer {token}'.encode()):
        return jsonify({'error': 'Unauthorized'}), 401

    db = get_db_session()
    try:
        payload = {
            'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'version': APP_VERSION,
            'metrics': get_status_metrics(db),
        }
        # json.dumps rather than jsonify: jsonify sorts keys, the metrics order matters to the reader
        response = app.response_class(json.dumps(payload, ensure_ascii=False), mimetype='application/json')
        response.headers['Cache-Control'] = 'no-store'
        return response
    finally:
        db.close()


@app.route('/clients')
@login_required
def clients_list():
    """Liste des clients"""
    db = get_db_session()
    try:
        from business_logic.clients import get_all_clients
        from business_logic.orders import get_client_orders
        search = request.args.get('search', '')
        clients = get_all_clients(db, search_query=search if search else None)
        
        # Charger les commandes pour chaque client
        clients_with_orders = []
        for client in clients:
            orders = get_client_orders(db, client.id)
            clients_with_orders.append({
                'client': client,
                'orders': orders
            })
        
        # Si c'est une requête HTMX pour juste la liste (sans la page complète)
        if request.headers.get('HX-Request'):
            return render_template('clients/_clients_list_only.html', clients_with_orders=clients_with_orders)
        
        return render_template('clients/list.html', clients_with_orders=clients_with_orders, search=search)
    finally:
        db.close()

@app.route('/clients/<int:client_id>/modal')
@login_required
def client_modal(client_id):
    """Fiche client compacte pour affichage modal"""
    db = get_db_session()
    try:
        from database.models import Client
        client = db.query(Client).get(client_id)
        if not client:
            return '<div class="alert-error">Client introuvable.</div>', 404
        return render_template('clients/_client_modal.html', client=client)
    finally:
        db.close()


@app.route('/orders')
@login_required
def orders_list():
    """Liste des commandes"""
    db = get_db_session()
    try:
        from business_logic.orders import get_orders_by_status_filter
        from business_logic.clients import get_all_clients
        from business_logic.automation import get_all_alerts, filter_alerts
        
        # Récupérer les filtres
        status_filter = request.args.get('status', 'En cours')
        client_filter = request.args.get('client', 'Tous')
        personalization_filter = request.args.get('personalization', '').strip()
        
        # Récupérer les commandes
        orders = get_orders_by_status_filter(db, status_filter)
        
        # Appliquer le filtre client
        if client_filter != 'Tous':
            try:
                client_id = int(client_filter)
                orders = [o for o in orders if o.client_id == client_id]
            except ValueError:
                pass
        
        # Appliquer le filtre de personnalisation
        if personalization_filter:
            filtered_orders = []
            search_term = personalization_filter.lower()
            for order in orders:
                has_match = False
                for line in order.order_lines:
                    if line.personnalisation and search_term in line.personnalisation.lower():
                        has_match = True
                        break
                if has_match:
                    filtered_orders.append(order)
            orders = filtered_orders
        
        # Récupérer tous les clients pour le filtre
        clients = get_all_clients(db)
        
        # Si c'est une requête HTMX pour juste la liste (sans la page complète)
        if request.headers.get('HX-Request'):
            return render_template('orders/_orders_list_only.html', orders=orders)
        
        alerts_all = get_all_alerts(db)
        order_alerts = filter_alerts(alerts_all, ['overdue_delivery', 'old_pending_order'])
        
        return render_template('orders/list.html', 
                             orders=orders, 
                             clients=clients,
                             status_filter=status_filter,
                             client_filter=client_filter,
                             personalization_filter=personalization_filter,
                             alerts=order_alerts)
    finally:
        db.close()


@app.route('/orders/<int:order_id>')
@login_required
def order_view(order_id):
    """Visualiser une commande"""
    db = get_db_session()
    try:
        from database.models import Order, OrderLine, OrderStatus
        from sqlalchemy.orm import joinedload
        
        order = db.query(Order)\
            .options(
                joinedload(Order.client),
                joinedload(Order.order_lines).joinedload(OrderLine.product),
                joinedload(Order.order_lines).joinedload(OrderLine.color)
            )\
            .filter(Order.id == order_id)\
            .first()
        
        if not order:
            return jsonify({'error': 'Commande non trouvée'}), 404
        
        # Créer un mapping des statuts pour l'affichage
        status_labels = {
            OrderStatus.EN_ATTENTE: 'En attente',
            OrderStatus.PAYE: 'Payé',
            OrderStatus.IMPRIME: 'Imprimé',
            OrderStatus.EN_PREPARATION: 'À l\'emballage',
            OrderStatus.PRET_LIVRAISON: 'Prêt à livrer',
            OrderStatus.LIVRE: 'Livré',
            OrderStatus.ANNULE: 'Annulé'
        }
        
        return render_template('orders/view.html', order=order, status_labels=status_labels)
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/orders/<int:order_id>/card')
@login_required
def order_card(order_id):
    """Récupérer la carte de commande (fragment HTML pour modal)"""
    db = get_db_session()
    try:
        from database.models import Order, OrderLine
        from sqlalchemy.orm import joinedload
        
        order = db.query(Order)\
            .options(
                joinedload(Order.client),
                joinedload(Order.order_lines).joinedload(OrderLine.product),
                joinedload(Order.order_lines).joinedload(OrderLine.color)
            )\
            .filter(Order.id == order_id)\
            .first()
        
        if not order:
            return jsonify({'error': 'Commande non trouvée'}), 404
        
        return render_template('orders/_order_card.html', order=order)
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/orders/<int:order_id>/modal')
@login_required
def order_modal(order_id):
    """Récupérer la fiche détaillée de commande pour affichage dans un modal"""
    db = get_db_session()
    try:
        from database.models import Order, OrderLine
        from sqlalchemy.orm import joinedload

        order = db.query(Order)\
            .options(
                joinedload(Order.client),
                joinedload(Order.order_lines).joinedload(OrderLine.product),
                joinedload(Order.order_lines).joinedload(OrderLine.color)
            )\
            .filter(Order.id == order_id)\
            .first()

        if not order:
            return jsonify({'error': 'Commande non trouvée'}), 404

        return render_template('orders/_order_modal.html', order=order)
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


CANCEL_ALLOWED_USERS = ('david', 'lea')


@app.route('/orders/<int:order_id>/cancel', methods=['POST'])
@login_required
def order_cancel(order_id):
    """Annuler une commande non livrée (objets imprimés remis en stock, motif facultatif)"""
    if session.get('username') not in CANCEL_ALLOWED_USERS:
        return jsonify({'error': "Vous n'avez pas le droit d'annuler une commande"}), 403
    db = get_db_session()
    try:
        from business_logic.orders import cancel_order
        order = cancel_order(db, order_id, session['user_id'], request.form.get('reason'))
        return jsonify({'ok': True, 'numero': order.numero_commande, 'returned_to_stock': order.returned_to_stock})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Erreur inattendue: {e}'}), 500
    finally:
        db.close()


@app.route('/orders/<int:order_id>/status', methods=['POST'])
@login_required
def order_update_status(order_id):
    """Mettre à jour le statut d'une commande"""
    db = get_db_session()
    try:
        from business_logic.orders import update_order_status, cancel_order
        
        new_status_str = request.form.get('status')
        if not new_status_str:
            return jsonify({'error': 'Statut manquant'}), 400
        
        # Convertir le statut string en enum
        try:
            new_status = OrderStatus(new_status_str)
        except ValueError:
            return jsonify({'error': f'Statut invalide: {new_status_str}'}), 400
        
        # Gérer l'annulation séparément
        if new_status == OrderStatus.ANNULE:
            if session.get('username') not in CANCEL_ALLOWED_USERS:
                return jsonify({'error': "Vous n'avez pas le droit d'annuler une commande"}), 403
            cancel_order(db, order_id, session['user_id'], request.form.get('reason'))
        else:
            update_order_status(db, order_id, new_status, session['user_id'])
        
        # Retourner la carte mise à jour
        from database.models import Order, OrderLine
        from sqlalchemy.orm import joinedload
        order = db.query(Order)\
            .options(
                joinedload(Order.client),
                joinedload(Order.order_lines).joinedload(OrderLine.product),
                joinedload(Order.order_lines).joinedload(OrderLine.color)
            )\
            .filter(Order.id == order_id)\
            .first()
        
        if not order:
            return jsonify({'error': 'Commande non trouvée'}), 404
        
        return render_template('orders/_order_card.html', order=order)
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/orders/form/new/client')
@login_required
def order_client_picker():
    """Première étape d'une nouvelle commande : choisir ou créer le client (pour le modal)"""
    db = get_db_session()
    try:
        from business_logic.clients import get_all_clients
        return render_template('orders/_client_picker.html', clients=get_all_clients(db))
    finally:
        db.close()


@app.route('/orders/form/new')
@login_required
def order_form_new():
    """Formulaire pour créer une nouvelle commande pour un client déjà identifié (pour le modal)"""
    client_id = request.args.get('client_id', type=int)
    if not client_id:
        return jsonify({'error': 'Client requis'}), 400
    db = get_db_session()
    try:
        from business_logic.clients import get_client_by_id
        from business_logic.catalogue import get_all_products
        from business_logic.catalogue import get_all_colors
        from database.models import DeliveryMode

        client = get_client_by_id(db, client_id)
        if not client:
            return jsonify({'error': 'Client introuvable'}), 404
        products = get_all_products(db)
        colors = get_all_colors(db)

        return render_template('orders/_order_form.html',
                             client=client,
                             products=products,
                             colors=colors,
                             order=None,
                             delivery_modes=DeliveryMode)
    finally:
        db.close()


@app.route('/orders/temp-line/form')
@login_required
def temp_order_line_form():
    """Formulaire pour ajouter une ligne temporaire lors de la création d'une commande"""
    db = get_db_session()
    try:
        from business_logic.catalogue import get_all_products, get_all_colors
        
        products = get_all_products(db)
        colors = get_all_colors(db)
        
        return render_template('orders/_order_line_form.html', 
                             order_id=None,
                             products=products,
                             colors=colors,
                             order_line=None)
    finally:
        db.close()


@app.route('/orders/<int:order_id>/form')
@login_required
def order_form_edit(order_id):
    """Formulaire pour éditer une commande (pour le modal)"""
    db = get_db_session()
    try:
        from business_logic.catalogue import get_all_products
        from business_logic.catalogue import get_all_colors
        from database.models import Order, OrderLine, DeliveryMode
        from sqlalchemy.orm import joinedload

        order = db.query(Order)\
            .options(
                joinedload(Order.client),
                joinedload(Order.order_lines).joinedload(OrderLine.product),
                joinedload(Order.order_lines).joinedload(OrderLine.color)
            )\
            .filter(Order.id == order_id)\
            .first()
        
        if not order:
            return jsonify({'error': 'Commande non trouvée'}), 404
        
        products = get_all_products(db)
        colors = get_all_colors(db)

        return render_template('orders/_order_form.html',
                             client=order.client,
                             products=products,
                             colors=colors,
                             order=order,
                             delivery_modes=DeliveryMode)
    finally:
        db.close()


@app.route('/orders/new', methods=['POST'])
@login_required
def order_create():
    """Créer une nouvelle commande"""
    db = get_db_session()
    try:
        from business_logic.orders import create_order, add_order_line
        from database.models import DeliveryMode
        from datetime import datetime
        
        # Récupérer les données du formulaire
        client_id = request.form.get('client_id', type=int)
        if not client_id:
            return jsonify({'error': 'Client requis'}), 400

        mode_livraison_str = request.form.get('mode_livraison', 'livraison')
        mode_livraison = DeliveryMode(mode_livraison_str)
        
        date_livraison_prevue = None
        if request.form.get('date_livraison_prevue'):
            date_livraison_prevue = datetime.strptime(request.form.get('date_livraison_prevue'), '%Y-%m-%d')
        
        adresse_livraison = request.form.get('adresse_livraison', '').strip() or None
        observations = request.form.get('observations', '').strip() or None
        skip_packaging = _parse_bool_param(request.form.get('skip_packaging', None))
        
        status_str = request.form.get('status', 'en_attente')
        status = OrderStatus(status_str)

        if mode_livraison == DeliveryMode.LIVRAISON and not adresse_livraison:
            return jsonify({'error': 'L’adresse de livraison est obligatoire pour une commande livrée.'}), 400
        
        # Récupérer les lignes (indices non nécessairement continus)
        import re
        from collections import defaultdict
        
        line_pattern = re.compile(r'^lines\[(\d+)\]\[(\w+)\]$')
        raw_lines = defaultdict(dict)
        for key, value in request.form.items():
            match = line_pattern.match(key)
            if match:
                idx = int(match.group(1))
                field = match.group(2)
                raw_lines[idx][field] = value
        
        lines_data = []
        for idx in sorted(raw_lines.keys()):
            data = raw_lines[idx]
            product_id = data.get('product_id')
            color_id = data.get('color_id')
            if not product_id or not color_id:
                continue
            try:
                quantite = int(data.get('quantite', 1))
                prix_vente_unitaire = float(data.get('prix_vente_unitaire', 0) or 0)
                remise_pourcent = float(data.get('remise_pourcent', 0) or 0)
            except ValueError:
                return jsonify({'error': 'Valeurs numériques invalides dans les lignes'}), 400
            
            personnalisation_line = (data.get('personnalisation') or '').strip() or None
            observations_line = (data.get('observations') or '').strip() or None
            
            lines_data.append({
                'product_id': int(product_id),
                'color_id': int(color_id),
                'quantite': quantite,
                'prix_vente_unitaire': prix_vente_unitaire,
                'remise_pourcent': remise_pourcent,
                'personnalisation': personnalisation_line,
                'observations': observations_line
            })
        
        if not lines_data:
            return jsonify({'error': 'Au moins une ligne de commande est requise'}), 400
        
        # Créer la commande
        order = create_order(
            db=db,
            client_id=client_id,
            user_id=session['user_id'],
            mode_livraison=mode_livraison,
            date_livraison_prevue=date_livraison_prevue,
            adresse_livraison=adresse_livraison,
            observations=observations,
            statut=status,
            skip_packaging=skip_packaging
        )
        
        # Ajouter les lignes
        for line_data in lines_data:
            add_order_line(
                db=db,
                order_id=order.id,
                product_id=line_data['product_id'],
                color_id=line_data['color_id'],
                quantite=line_data['quantite'],
                prix_vente_unitaire=line_data['prix_vente_unitaire'],
                remise_pourcent=line_data['remise_pourcent'],
                personnalisation=line_data['personnalisation'],
                observations=line_data['observations']
            )
        
        # Telegram: notify David of new order
        try:
            db.refresh(order)
            from utils.notifications import notify_telegram_new_order
            notify_telegram_new_order(order, db)
        except Exception as _e:
            app.logger.warning(f"Telegram new-order notification failed: {_e}")

        # Retourner une réponse qui ferme le modal et recharge la liste
        return '<script>if (typeof window.closeOrderModal === "function") window.closeOrderModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/orders/<int:order_id>/update', methods=['POST'])
@login_required
def order_update(order_id):
    """Mettre à jour une commande"""
    db = get_db_session()
    try:
        from business_logic.orders import update_order_details, set_order_skip_packaging
        from database.models import DeliveryMode
        from datetime import datetime
        
        # Récupérer les données du formulaire
        mode_livraison_str = request.form.get('mode_livraison', 'livraison')
        mode_livraison = DeliveryMode(mode_livraison_str)
        
        date_livraison_prevue = None
        if request.form.get('date_livraison_prevue'):
            date_livraison_prevue = datetime.strptime(request.form.get('date_livraison_prevue'), '%Y-%m-%d')
        
        adresse_livraison = request.form.get('adresse_livraison', '').strip() or None
        # Pour observations, on garde la chaîne vide pour permettre de vider le champ
        observations_raw = request.form.get('observations', '')
        observations = observations_raw.strip() if observations_raw else ''
        skip_packaging = _parse_bool_param(request.form.get('skip_packaging', None))

        if mode_livraison == DeliveryMode.LIVRAISON and not adresse_livraison:
            return jsonify({'error': 'L’adresse de livraison est obligatoire pour une commande livrée.'}), 400
        
        # Mettre à jour les détails de la commande
        order = update_order_details(
            db=db,
            order_id=order_id,
            mode_livraison=mode_livraison,
            date_livraison_prevue=date_livraison_prevue,
            adresse_livraison=adresse_livraison,
            observations=observations,
            user_id=session['user_id']
        )
        
        if order and bool(order.ne_pas_emballer) != skip_packaging:
            set_order_skip_packaging(db, order_id, skip_packaging, session['user_id'])
        
        # TODO: Gérer la mise à jour des lignes (pour l'instant, on ne met à jour que les détails)
        
        # Retourner une réponse qui ferme le modal et recharge la liste
        return '<script>if (typeof window.closeOrderModal === "function") window.closeOrderModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/orders/<int:order_id>/lines/form/new')
@login_required
def order_line_form_new(order_id):
    """Formulaire pour ajouter une ligne de commande (pour le modal)"""
    db = get_db_session()
    try:
        from business_logic.catalogue import get_all_products, get_all_colors
        from database.models import Order
        
        # Vérifier que la commande existe
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return jsonify({'error': 'Commande non trouvée'}), 404
        
        products = get_all_products(db)
        colors = get_all_colors(db)
        
        return render_template('orders/_order_line_form.html', 
                             order_id=order_id,
                             products=products,
                             colors=colors,
                             order_line=None)
    finally:
        db.close()


@app.route('/orders/<int:order_id>/lines/<int:line_id>/form')
@login_required
def order_line_form_edit(order_id, line_id):
    """Formulaire pour modifier une ligne de commande (pour le modal)"""
    db = get_db_session()
    try:
        from business_logic.catalogue import get_all_products, get_all_colors
        from database.models import Order, OrderLine
        from sqlalchemy.orm import joinedload
        
        # Vérifier que la commande existe
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return jsonify({'error': 'Commande non trouvée'}), 404
        
        # Récupérer la ligne
        order_line = db.query(OrderLine)\
            .options(
                joinedload(OrderLine.product),
                joinedload(OrderLine.color)
            )\
            .filter(OrderLine.id == line_id, OrderLine.order_id == order_id)\
            .first()
        
        if not order_line:
            return jsonify({'error': 'Ligne de commande non trouvée'}), 404
        
        products = get_all_products(db)
        colors = get_all_colors(db)
        
        return render_template('orders/_order_line_form.html', 
                             order_id=order_id,
                             products=products,
                             colors=colors,
                             order_line=order_line)
    finally:
        db.close()


@app.route('/orders/<int:order_id>/lines/add', methods=['POST'])
@login_required
def order_line_add(order_id):
    """Ajouter une ligne à une commande"""
    db = get_db_session()
    try:
        from business_logic.orders import add_order_line
        from database.models import Order, Product
        
        # Vérifier que la commande existe
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return jsonify({'error': 'Commande non trouvée'}), 404
        
        # Récupérer les données du formulaire
        product_id = request.form.get('product_id')
        color_id = request.form.get('color_id')
        quantite = int(request.form.get('quantite', 1))
        prix_vente_unitaire = float(request.form.get('prix_vente_unitaire', 0))
        remise_pourcent = float(request.form.get('remise_pourcent', 0))
        personnalisation = request.form.get('personnalisation', '').strip() or None
        observations = request.form.get('observations', '').strip() or None
        
        if not product_id or not color_id:
            return jsonify({'error': 'Produit et couleur requis'}), 400
        
        product = db.query(Product).get(int(product_id))
        if product and product.personalisable:
            quantite = 1
            if not personnalisation:
                return jsonify({'error': 'La personnalisation est obligatoire pour ce produit'}), 400

        # Ajouter la ligne
        add_order_line(
            db=db,
            order_id=order_id,
            product_id=int(product_id),
            color_id=int(color_id),
            quantite=quantite,
            prix_vente_unitaire=prix_vente_unitaire if prix_vente_unitaire > 0 else None,
            remise_pourcent=remise_pourcent,
            personnalisation=personnalisation,
            observations=observations
        )
        
        # Retourner une réponse qui ferme le modal et recharge la page
        return '<script>if (typeof window.closeOrderLineModal === "function") window.closeOrderLineModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/orders/<int:order_id>/lines/<int:line_id>/update', methods=['POST'])
@login_required
def order_line_update(order_id, line_id):
    """Mettre à jour une ligne de commande"""
    db = get_db_session()
    try:
        from business_logic.orders import update_order_line_personnalisation, update_order_line_observations
        from database.models import OrderLine, Product, FabricationStatus
        from business_logic.financial import calculate_line_financials
        
        # Vérifier que la ligne existe
        order_line = db.query(OrderLine).filter(OrderLine.id == line_id, OrderLine.order_id == order_id).first()
        if not order_line:
            return jsonify({'error': 'Ligne de commande non trouvée'}), 404
        
        # Vérifier que la ligne n'est pas imprimée ou après
        if order_line.statut_fabrication in [FabricationStatus.IMPRIME, FabricationStatus.LIVRE_LEA]:
            return jsonify({'error': 'Cette ligne est imprimée et ne peut plus être modifiée'}), 403
        
        # Récupérer les données du formulaire
        product_id = request.form.get('product_id')
        color_id = request.form.get('color_id')
        quantite = int(request.form.get('quantite', 1))
        prix_vente_unitaire = float(request.form.get('prix_vente_unitaire', 0))
        remise_pourcent = float(request.form.get('remise_pourcent', 0))
        personnalisation = request.form.get('personnalisation', '').strip() or None
        observations = request.form.get('observations', '').strip() or None
        
        # Déterminer le produit cible pour validation
        target_product_id = order_line.product_id
        if product_id:
            target_product_id = int(product_id)
        product = db.query(Product).get(target_product_id)
        if not product:
            return jsonify({'error': 'Produit invalide'}), 400

        if product.personalisable and quantite != 1:
            return jsonify({'error': 'La quantité doit être égale à 1 pour un produit personnalisable'}), 400
        if product.personalisable and not personnalisation:
            return jsonify({'error': 'La personnalisation est obligatoire pour ce produit'}), 400
        
        # Mettre à jour les champs de base
        order_line.product_id = target_product_id
        if color_id:
            order_line.color_id = int(color_id)
        order_line.quantite = quantite
        
        # Recalculer les prix si nécessaire
        if prix_vente_unitaire > 0 or remise_pourcent > 0:
            prix_reference = prix_vente_unitaire if prix_vente_unitaire > 0 else product.prix_vente_base
            financials = calculate_line_financials(
                product=product,
                prix_vente_unitaire=prix_reference,
                remise_pourcent=remise_pourcent,
                quantite=quantite
            )
            order_line.prix_vente_unitaire = financials['prix_vente_unitaire']
            order_line.remise_pourcent = financials['remise_pourcent']
            order_line.prix_vente_final = financials['prix_vente_final']
            order_line.benefice_calcule = financials['benefice_calcule']
            order_line.montant_a_reverser_david = financials['montant_a_reverser_david']
        
        # Mettre à jour personnalisation et observations
        update_order_line_personnalisation(db, line_id, personnalisation)
        update_order_line_observations(db, line_id, observations)
        
        db.commit()
        
        # Retourner une réponse qui ferme le modal et recharge la page
        return '<script>if (typeof window.closeOrderLineModal === "function") window.closeOrderLineModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/orders/<int:order_id>/lines/<int:line_id>/delete', methods=['POST'])
@login_required
def order_line_delete(order_id, line_id):
    """Supprimer une ligne de commande"""
    db = get_db_session()
    try:
        from database.models import OrderLine, FabricationStatus
        
        # Vérifier que la ligne existe
        order_line = db.query(OrderLine).filter(OrderLine.id == line_id, OrderLine.order_id == order_id).first()
        if not order_line:
            return jsonify({'error': 'Ligne de commande non trouvée'}), 404
        
        # Vérifier que la ligne n'est pas imprimée ou après
        if order_line.statut_fabrication in [FabricationStatus.IMPRIME, FabricationStatus.LIVRE_LEA]:
            return jsonify({'error': 'Cette ligne est imprimée et ne peut plus être supprimée'}), 403
        
        # Supprimer la ligne
        db.delete(order_line)
        db.commit()
        
        # Retourner une réponse qui recharge la page
        return '<script>setTimeout(function() { window.location.reload(); }, 100);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/clients/form/new')
@login_required
def client_form_new():
    """Formulaire pour créer un nouveau client (pour le modal)"""
    return render_template('clients/_client_form.html', client=None, client_id=None, action_url=url_for('client_create'))


@app.route('/clients/<int:client_id>/form')
@login_required
def client_form_edit(client_id):
    """Formulaire pour éditer un client (pour le modal)"""
    db = get_db_session()
    try:
        from business_logic.clients import get_client_by_id, can_delete_client
        client = get_client_by_id(db, client_id)
        if not client:
            return jsonify({'error': 'Client introuvable'}), 404
        can_delete, active_orders_count = can_delete_client(db, client_id)
        return render_template('clients/_client_form.html', 
                             client=client, 
                             client_id=client_id, 
                             action_url=url_for('client_edit', client_id=client_id),
                             can_delete=can_delete,
                             active_orders_count=active_orders_count)
    finally:
        db.close()


@app.route('/clients/<int:client_id>/edit', methods=['POST'])
@login_required
def client_edit(client_id):
    """Éditer un client"""
    db = get_db_session()
    try:
        from business_logic.clients import update_client
        # Pour les champs optionnels, on passe toujours une valeur pour forcer la mise à jour
        prenom = request.form.get('prenom', '').strip()
        email = request.form.get('email', '').strip()
        telephone = request.form.get('telephone', '').strip()
        provenance = request.form.get('provenance', '').strip()
        adresse = request.form.get('adresse', '').strip()
        notes = request.form.get('notes', '').strip()
        
        updated_client = update_client(
            db=db,
            client_id=client_id,
            nom=request.form.get('nom'),
            prenom=prenom,
            email=email,
            telephone=telephone,
            provenance=provenance,
            adresse=adresse,
            notes=notes
        )
        
        # Retourner la carte mise à jour pour HTMX
        # Le script dans list.html écoutera l'événement htmx:afterSwap pour fermer le modal
        return render_template('clients/_client_card.html', client=updated_client)
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/clients/<int:client_id>/delete', methods=['POST'])
@login_required
def client_delete(client_id):
    """Supprimer un client"""
    db = get_db_session()
    try:
        from business_logic.clients import delete_client, can_delete_client
        
        # Vérifier si on peut supprimer
        can_delete, active_orders_count = can_delete_client(db, client_id)
        if not can_delete:
            return jsonify({
                'error': f'Impossible de supprimer ce client : {active_orders_count} commande(s) active(s) (non annulée(s))'
            }), 400
        
        # Supprimer le client
        success = delete_client(db, client_id)
        if not success:
            return jsonify({'error': 'Échec de la suppression'}), 400
        
        # Retourner une réponse qui ferme le modal et recharge la liste
        return '<script>if (typeof window.closeClientModal === "function") window.closeClientModal(); setTimeout(function() { window.location.reload(); }, 100);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/clients/new', methods=['POST'])
@login_required
def client_create():
    """Créer un nouveau client"""
    db = get_db_session()
    try:
        from business_logic.clients import create_client
        client = create_client(
            db=db,
            nom=request.form.get('nom'),
            user_id=session['user_id'],
            prenom=request.form.get('prenom') or None,
            email=request.form.get('email') or None,
            telephone=request.form.get('telephone') or None,
            provenance=request.form.get('provenance') or None,
            adresse=request.form.get('adresse') or None,
            notes=request.form.get('notes') or None
        )
        # Corps vide : la page Clients ferme le modal et recharge. L'événement clientCreated
        # permet à la création de commande d'enchaîner sur le nouveau client.
        return '', 200, {'HX-Trigger': json.dumps({'clientCreated': {'id': client.id}})}
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


# ========== CATALOGUE ==========

def _product_images_dir(create: bool = False) -> Path:
    """DATA_DIR/product_images; created on demand (uploads), with a clear error if not writable."""
    images_dir = app_settings.DATA_DIR / 'product_images'
    if create:
        try:
            images_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"Cannot create product image directory {images_dir}: {e.strerror}") from e
    return images_dir


def _product_image_path(image_url):
    """
    Resolve a stored image_url to its file under DATA_DIR/product_images, by file name.
    Handles legacy values such as 'data/product_images/x.png' (or Windows separators).
    """
    if not image_url:
        return None
    filename = image_url.replace('\\', '/').rsplit('/', 1)[-1]
    if filename in ('', '.', '..'):
        return None
    return _product_images_dir() / filename


@app.route('/data/product_images/<path:filename>')
@login_required
def serve_product_image(filename):
    """Servir les images des produits depuis DATA_DIR/product_images/"""
    return send_from_directory(str(_product_images_dir()), filename)

@app.route('/catalogue')
@login_required
def catalogue():
    """Page Catalogue - Gestion des produits, couleurs et imprimantes"""
    db = get_db_session()
    try:
        from business_logic.catalogue import get_all_products, get_all_colors, get_all_printers
        from business_logic.inventory import get_low_stock_colors
        
        # Récupérer l'onglet actif depuis l'URL (par défaut: produits)
        active_tab = request.args.get('tab', 'produits')
        
        # Récupérer le paramètre pour afficher les produits inactifs
        show_inactive = request.args.get('show_inactive', 'false').lower() == 'true'
        
        # Récupérer les données selon l'onglet
        products = get_all_products(db, active_only=not show_inactive)
        colors = get_all_colors(db, active_only=True)
        printers = get_all_printers(db)
        low_stock_colors = get_low_stock_colors(db)
        
        return render_template('catalogue/index.html',
                             products=products,
                             colors=colors,
                             printers=printers,
                             low_stock_colors=low_stock_colors,
                             active_tab=active_tab,
                             show_inactive=show_inactive)
    finally:
        db.close()


# ========== PRODUCTS ROUTES ==========

@app.route('/catalogue/products/form/new')
@login_required
def product_form_new():
    """Formulaire pour créer un nouveau produit"""
    from database.models import ProductType, FilamentType
    return render_template('catalogue/_product_form.html', 
                         product=None,
                         product_types=ProductType,
                         filament_types=FilamentType)


@app.route('/catalogue/products/<int:product_id>/form')
@login_required
def product_form_edit(product_id):
    """Formulaire pour éditer un produit"""
    db = get_db_session()
    try:
        from business_logic.catalogue import get_product_by_id
        from database.models import ProductType, FilamentType
        
        product = get_product_by_id(db, product_id)
        if not product:
            return jsonify({'error': 'Produit introuvable'}), 404
        
        return render_template('catalogue/_product_form.html', 
                             product=product,
                             product_types=ProductType,
                             filament_types=FilamentType)
    finally:
        db.close()


@app.route('/catalogue/products/new', methods=['POST'])
@login_required
def product_create():
    """Créer un nouveau produit"""
    db = get_db_session()
    try:
        from business_logic.catalogue import create_product
        from database.models import ProductType
        from pathlib import Path
        import re
        import traceback
        
        # Récupérer les données du formulaire
        nom = request.form.get('nom')
        description = request.form.get('description', '').strip() or None
        product_type_str = request.form.get('product_type')
        
        if not product_type_str:
            error_msg = "Le type de produit est obligatoire"
            app.logger.error(f"Product creation error: {error_msg}")
            return f'<div class="alert alert-error" style="padding: 15px; margin: 10px; background: #fee; border: 1px solid #fcc; border-radius: 4px; color: #c00;">❌ {error_msg}</div>', 400
        
        try:
            product_type = ProductType(product_type_str)
        except (ValueError, AttributeError) as e:
            error_msg = f"Type de produit invalide: {product_type_str}"
            app.logger.error(f"Product creation error: {error_msg} - {str(e)}")
            return f'<div class="alert alert-error" style="padding: 15px; margin: 10px; background: #fee; border: 1px solid #fcc; border-radius: 4px; color: #c00;">❌ {error_msg}</div>', 400
        
        # Helper function to safely convert form values to numbers
        def safe_float(value, default=0.0):
            if not value or value.strip() == '':
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default
        
        def safe_int(value, default=0):
            if not value or value.strip() == '':
                return default
            try:
                return int(value)
            except (ValueError, TypeError):
                return default
        
        try:
            prix_vente_base = safe_float(request.form.get('prix_vente_base'), 0)
            prix_revient_david = safe_float(request.form.get('prix_revient_david'), 0)
            prix_revient_lea = safe_float(request.form.get('prix_revient_lea'), 0)
            poids_grammes = safe_float(request.form.get('poids_grammes'), 0)
            temps_impression_minutes = safe_int(request.form.get('temps_impression_minutes'), 0)
        except Exception as e:
            error_msg = f"Erreur de format dans les valeurs numériques: {str(e)}"
            app.logger.error(f"Product creation error: {error_msg}")
            return f'<div class="alert alert-error" style="padding: 15px; margin: 10px; background: #fee; border: 1px solid #fcc; border-radius: 4px; color: #c00;">❌ {error_msg}</div>', 400
        
        prix_vente_lea = None
        if product_type == ProductType.REVENTE_LEA:
            prix_vente_lea_val = request.form.get('prix_vente_lea', '').strip()
            if not prix_vente_lea_val:
                error_msg = "Le prix de vente à Lea est obligatoire pour les produits REVENTE_LEA"
                app.logger.error(f"Product creation error: {error_msg}")
                return f'<div class="alert alert-error" style="padding: 15px; margin: 10px; background: #fee; border: 1px solid #fcc; border-radius: 4px; color: #c00;">❌ {error_msg}</div>', 400
            try:
                prix_vente_lea = float(prix_vente_lea_val)
            except (ValueError, TypeError) as e:
                error_msg = f"Le prix de vente à Lea doit être un nombre valide: {str(e)}"
                app.logger.error(f"Product creation error: {error_msg}")
                return f'<div class="alert alert-error" style="padding: 15px; margin: 10px; background: #fee; border: 1px solid #fcc; border-radius: 4px; color: #c00;">❌ {error_msg}</div>', 400
        
        personalisable = request.form.get('personalisable') == 'on'
        gestion_stock = request.form.get('gestion_stock') == 'on'
        alerte_stock = safe_int(request.form.get('alerte_stock'), 0) if gestion_stock else 0
        observations = request.form.get('observations', '').strip() or None
        
        # Gérer l'upload d'image
        image_url = None
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file and image_file.filename:
                try:
                    # Créer un nom de fichier sécurisé
                    safe_name = re.sub(r'[^\w\s-]', '', nom).strip().replace(' ', '_')
                    file_ext = image_file.filename.rsplit('.', 1)[1].lower()
                    image_filename = f"{safe_name}_{session['user_id']}.{file_ext}"
                    
                    # Sauvegarder dans DATA_DIR/product_images/ (on stocke le nom de fichier)
                    image_path = _product_images_dir(create=True) / image_filename
                    image_file.save(str(image_path))
                    image_url = image_filename
                except Exception as e:
                    error_msg = f"Erreur lors de l'upload de l'image: {str(e)}"
                    app.logger.error(f"Product creation error: {error_msg}\n{traceback.format_exc()}")
                    return f'<div class="alert alert-error" style="padding: 15px; margin: 10px; background: #fee; border: 1px solid #fcc; border-radius: 4px; color: #c00;">❌ {error_msg}</div>', 400
        
        # Créer le produit
        try:
            create_product(
                db=db,
                nom=nom,
                product_type=product_type,
                prix_vente_base=prix_vente_base,
                prix_revient_david=prix_revient_david,
                prix_revient_lea=prix_revient_lea,
                poids_grammes=poids_grammes,
                temps_impression_minutes=temps_impression_minutes,
                user_id=session['user_id'],
                prix_vente_lea=prix_vente_lea,
                description=description,
                image_url=image_url,
                personalisable=personalisable,
                gestion_stock=gestion_stock,
                alerte_stock=alerte_stock,
                observations=observations,
                liens_tinkercad=request.form.get('liens_tinkercad'),
                fichiers_3mf=request.form.get('fichiers_3mf')
            )
        except Exception as e:
            error_msg = f"Erreur lors de la création du produit: {str(e)}"
            app.logger.error(f"Product creation error: {error_msg}\n{traceback.format_exc()}")
            return f'<div class="alert alert-error" style="padding: 15px; margin: 10px; background: #fee; border: 1px solid #fcc; border-radius: 4px; color: #c00;">❌ {error_msg}</div>', 400
        
        # Retourner une réponse qui ferme le modal et recharge la page
        return '<script>if (typeof window.closeProductModal === "function") window.closeProductModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        error_msg = f"Erreur inattendue: {str(e)}"
        app.logger.error(f"Product creation unexpected error: {error_msg}\n{traceback.format_exc()}")
        return f'<div class="alert alert-error" style="padding: 15px; margin: 10px; background: #fee; border: 1px solid #fcc; border-radius: 4px; color: #c00;">❌ {error_msg}</div>', 400
    finally:
        db.close()


@app.route('/catalogue/products/<int:product_id>/update', methods=['POST'])
@login_required
def product_update(product_id):
    """Mettre à jour un produit"""
    db = get_db_session()
    try:
        from business_logic.catalogue import (
            update_product, get_product_by_id, normalize_tinkercad_links, normalize_3mf_files
        )
        from database.models import ProductType
        from pathlib import Path
        import re

        product = get_product_by_id(db, product_id)
        if not product:
            return jsonify({'error': 'Produit introuvable'}), 404

        # Récupérer les données du formulaire
        nom = request.form.get('nom')
        description = request.form.get('description', '').strip() or None

        # Valider les liens avant l'upload d'image (qui supprime l'ancienne image)
        liens_tinkercad = request.form.get('liens_tinkercad')
        fichiers_3mf = request.form.get('fichiers_3mf')
        normalize_tinkercad_links(liens_tinkercad)
        normalize_3mf_files(fichiers_3mf)

        prix_vente_base = float(request.form.get('prix_vente_base', 0))
        prix_revient_david = float(request.form.get('prix_revient_david', 0))
        prix_revient_lea = float(request.form.get('prix_revient_lea', 0))
        poids_grammes = float(request.form.get('poids_grammes', 0))
        temps_impression_minutes = int(request.form.get('temps_impression_minutes', 0))
        
        prix_vente_lea = None
        if product.product_type == ProductType.REVENTE_LEA:
            prix_vente_lea = float(request.form.get('prix_vente_lea', 0))
        
        personalisable = request.form.get('personalisable') == 'on'
        gestion_stock = request.form.get('gestion_stock') == 'on'
        alerte_stock = int(request.form.get('alerte_stock', 0)) if gestion_stock else 0
        actif = request.form.get('actif') == 'on'
        observations = request.form.get('observations', '').strip() or None
        
        # Gérer l'upload d'image
        image_url = product.image_url
        if 'image' in request.files:
            image_file = request.files['image']
            if image_file and image_file.filename:
                # Supprimer l'ancienne image si elle existe
                old_image_path = _product_image_path(product.image_url)
                if old_image_path and old_image_path.exists():
                    try:
                        old_image_path.unlink()
                    except OSError:
                        pass
                
                # Créer un nom de fichier sécurisé
                safe_name = re.sub(r'[^\w\s-]', '', nom).strip().replace(' ', '_')
                file_ext = image_file.filename.rsplit('.', 1)[1].lower()
                image_filename = f"{safe_name}_{product_id}.{file_ext}"
                
                # Sauvegarder dans DATA_DIR/product_images/ (on stocke le nom de fichier)
                image_path = _product_images_dir(create=True) / image_filename
                image_file.save(str(image_path))
                image_url = image_filename
        
        # Mettre à jour le produit
        update_product(
            db=db,
            product_id=product_id,
            nom=nom,
            description=description,
            image_url=image_url,
            prix_vente_base=prix_vente_base,
            prix_revient_david=prix_revient_david,
            prix_revient_lea=prix_revient_lea,
            prix_vente_lea=prix_vente_lea,
            poids_grammes=poids_grammes,
            temps_impression_minutes=temps_impression_minutes,
            personalisable=personalisable,
            gestion_stock=gestion_stock,
            alerte_stock=alerte_stock,
            actif=actif,
            observations=observations,
            liens_tinkercad=liens_tinkercad,
            fichiers_3mf=fichiers_3mf
        )

        # Retourner une réponse qui ferme le modal et recharge la page
        return '<script>if (typeof window.closeProductModal === "function") window.closeProductModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


# ========== COLORS ROUTES ==========

@app.route('/catalogue/colors/form/new')
@login_required
def color_form_new():
    """Formulaire pour créer une nouvelle couleur"""
    from database.models import FilamentType
    return render_template('catalogue/_color_form.html', 
                         color=None,
                         filament_types=FilamentType)


@app.route('/catalogue/colors/<int:color_id>/form')
@login_required
def color_form_edit(color_id):
    """Formulaire pour éditer une couleur"""
    db = get_db_session()
    try:
        from business_logic.catalogue import get_color_by_id
        from database.models import FilamentType
        
        color = get_color_by_id(db, color_id)
        if not color:
            return jsonify({'error': 'Couleur introuvable'}), 404
        
        return render_template('catalogue/_color_form.html', 
                             color=color,
                             filament_types=FilamentType)
    finally:
        db.close()


@app.route('/catalogue/colors/<int:color_id>/add-stock-form')
@login_required
def color_add_stock_form(color_id):
    """Formulaire pour ajouter du stock à une couleur"""
    db = get_db_session()
    try:
        from business_logic.catalogue import get_color_by_id
        
        color = get_color_by_id(db, color_id)
        if not color:
            return jsonify({'error': 'Couleur introuvable'}), 404
        
        return render_template('catalogue/_color_add_stock_form.html', color=color)
    finally:
        db.close()


@app.route('/catalogue/colors/new', methods=['POST'])
@login_required
def color_create():
    """Créer une nouvelle couleur"""
    db = get_db_session()
    try:
        from business_logic.catalogue import create_color
        from database.models import FilamentType
        
        # Récupérer les données du formulaire
        nom = request.form.get('nom')
        nom_original = request.form.get('nom_original', '').strip() or None
        code_hex = request.form.get('code_hex', '').strip() or None
        marque = request.form.get('marque', '').strip() or None
        type_filament_str = request.form.get('type_filament')
        type_filament = FilamentType(type_filament_str)
        
        stock_initial = float(request.form.get('stock_initial', 0))
        cout_par_kg = float(request.form.get('cout_par_kg', 0))
        seuil_alerte = float(request.form.get('seuil_alerte', 500))
        
        # Créer la couleur
        create_color(
            db=db,
            nom=nom,
            type_filament=type_filament,
            cout_par_kg=cout_par_kg,
            stock_initial=stock_initial,
            nom_original=nom_original,
            code_hex=code_hex,
            marque=marque,
            seuil_alerte=seuil_alerte
        )
        
        # Retourner une réponse qui ferme le modal et recharge la page
        return '<script>if (typeof window.closeColorModal === "function") window.closeColorModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/catalogue/colors/<int:color_id>/update', methods=['POST'])
@login_required
def color_update(color_id):
    """Mettre à jour une couleur"""
    db = get_db_session()
    try:
        from business_logic.catalogue import update_color, get_color_by_id
        from database.models import FilamentType
        
        color = get_color_by_id(db, color_id)
        if not color:
            return jsonify({'error': 'Couleur introuvable'}), 404
        
        # Récupérer les données du formulaire
        nom = request.form.get('nom')
        nom_original = request.form.get('nom_original', '').strip() or None
        code_hex = request.form.get('code_hex', '').strip() or None
        marque = request.form.get('marque', '').strip() or None
        type_filament_str = request.form.get('type_filament')
        type_filament = FilamentType(type_filament_str) if type_filament_str else None
        
        cout_par_kg = float(request.form.get('cout_par_kg', 0))
        seuil_alerte = float(request.form.get('seuil_alerte', 500))
        actif = request.form.get('actif') == 'on'
        
        # Mettre à jour la couleur
        update_color(
            db=db,
            color_id=color_id,
            nom=nom,
            nom_original=nom_original,
            code_hex=code_hex,
            marque=marque,
            type_filament=type_filament,
            cout_par_kg=cout_par_kg,
            seuil_alerte=seuil_alerte,
            actif=actif
        )
        
        # Retourner une réponse qui ferme le modal et recharge la page
        return '<script>if (typeof window.closeColorModal === "function") window.closeColorModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/catalogue/colors/<int:color_id>/add-stock', methods=['POST'])
@login_required
def color_add_stock(color_id):
    """Ajouter du stock à une couleur"""
    db = get_db_session()
    try:
        from business_logic.inventory import add_stock
        from business_logic.catalogue import get_color_by_id
        
        color = get_color_by_id(db, color_id)
        if not color:
            return jsonify({'error': 'Couleur introuvable'}), 404
        
        # Récupérer les données du formulaire
        quantite = float(request.form.get('quantite', 0))
        observations = request.form.get('observations', '').strip() or None
        
        if quantite <= 0:
            return jsonify({'error': 'La quantité doit être supérieure à 0'}), 400
        
        # Ajouter le stock
        add_stock(
            db=db,
            color_id=color_id,
            quantite=quantite,
            user_id=session['user_id'],
            observations=observations
        )
        
        # Retourner une réponse qui ferme le modal et recharge la page
        return '<script>if (typeof window.closeColorModal === "function") window.closeColorModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


# ========== PRINTERS ROUTES ==========

@app.route('/catalogue/printers/form/new')
@login_required
def printer_form_new():
    """Formulaire pour créer une nouvelle imprimante"""
    from database.models import PrinterStatus
    return render_template('catalogue/_printer_form.html', 
                         printer=None,
                         printer_statuses=PrinterStatus)


@app.route('/catalogue/printers/<int:printer_id>/form')
@login_required
def printer_form_edit(printer_id):
    """Formulaire pour éditer une imprimante"""
    db = get_db_session()
    try:
        from business_logic.catalogue import get_printer_by_id
        from database.models import PrinterStatus
        
        printer = get_printer_by_id(db, printer_id)
        if not printer:
            return jsonify({'error': 'Imprimante introuvable'}), 404
        
        return render_template('catalogue/_printer_form.html', 
                             printer=printer,
                             printer_statuses=PrinterStatus)
    finally:
        db.close()


@app.route('/catalogue/printers/new', methods=['POST'])
@login_required
def printer_create():
    """Créer une nouvelle imprimante"""
    db = get_db_session()
    try:
        from business_logic.catalogue import create_printer
        from database.models import PrinterStatus
        
        # Récupérer les données du formulaire
        nom = request.form.get('nom')
        modele = request.form.get('modele', '').strip() or None
        statut_str = request.form.get('statut', 'disponible')
        statut = PrinterStatus(statut_str)
        observations = request.form.get('observations', '').strip() or None
        
        # Créer l'imprimante
        create_printer(
            db=db,
            nom=nom,
            statut=statut,
            modele=modele,
            observations=observations
        )
        
        # Retourner une réponse qui ferme le modal et recharge la page
        return '<script>if (typeof window.closePrinterModal === "function") window.closePrinterModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/catalogue/printers/<int:printer_id>/update', methods=['POST'])
@login_required
def printer_update(printer_id):
    """Mettre à jour une imprimante"""
    db = get_db_session()
    try:
        from business_logic.catalogue import update_printer, get_printer_by_id
        from database.models import PrinterStatus
        
        printer = get_printer_by_id(db, printer_id)
        if not printer:
            return jsonify({'error': 'Imprimante introuvable'}), 404
        
        # Récupérer les données du formulaire
        nom = request.form.get('nom')
        modele = request.form.get('modele', '').strip() or None
        statut_str = request.form.get('statut')
        statut = PrinterStatus(statut_str) if statut_str else None
        observations = request.form.get('observations', '').strip() or None
        
        # Mettre à jour l'imprimante
        update_printer(
            db=db,
            printer_id=printer_id,
            nom=nom,
            modele=modele,
            statut=statut,
            observations=observations
        )
        
        # Retourner une réponse qui ferme le modal et recharge la page
        return '<script>if (typeof window.closePrinterModal === "function") window.closePrinterModal(); setTimeout(function() { window.location.reload(); }, 200);</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


# ========== FINISHED GOODS STOCK ==========

def _get_filtered_finished_stock(db, include_empty: bool, product_id=None, color_id=None):
    """Helper to fetch and filter finished goods stock entries, including entries with production needs."""
    from business_logic.finished_goods import get_all_stock_with_needs
    entries = get_all_stock_with_needs(
        db, 
        filter_available_only=not include_empty,
        product_id=product_id,
        color_id=color_id
    )
    return entries


def _get_stock_movements_history(db, product_id=None, color_id=None, limit: int = 60):
    """Helper to fetch stock movement history."""
    from business_logic.finished_goods import get_stock_movements
    return get_stock_movements(
        db=db,
        product_id=product_id,
        color_id=color_id,
        limit=limit
    )


def _calculate_finished_stock_metrics(db, product_id: int, color_id: int, quantity: int):
    """Compute helper metrics for the add-to-stock form."""
    if not product_id or not color_id or not quantity:
        return None
    if quantity < 0:
        return None
    from business_logic.catalogue import get_product_by_id, get_color_by_id
    from business_logic.finished_goods import get_stock_by_product_color
    
    product = get_product_by_id(db, product_id)
    color = get_color_by_id(db, color_id)
    
    if not product or not color:
        return None
    
    stock_entry = get_stock_by_product_color(db, product_id, color_id)
    poids_unitaire = product.poids_grammes or 0
    temps_unitaire = product.temps_impression_minutes or 0
    poids_total = poids_unitaire * quantity
    temps_total = temps_unitaire * quantity
    cout_filament = 0
    if color.cout_par_kg and poids_total:
        cout_filament = (poids_total / 1000) * color.cout_par_kg
    
    return {
        'product': product,
        'color': color,
        'quantity': quantity,
        'poids_total': poids_total,
        'temps_total': temps_total,
        'filament_cost': round(cout_filament, 2),
        'current_stock': stock_entry.quantite_disponible if stock_entry else 0
    }


@app.route('/stock-produits-finis')
@login_required
def stock_finished_goods():
    """Gestion du stock de produits finis"""
    db = get_db_session()
    try:
        from business_logic.catalogue import get_all_products, get_all_colors
        from business_logic.finished_goods import get_stock_summary
        from business_logic.automation import get_all_alerts, filter_alerts
        
        # Filters
        show_all = _parse_bool_param(request.args.get('show_all'), default=False)
        product_filter_value = request.args.get('product_id', 'all')
        color_filter_value = request.args.get('color_id', 'all')
        history_product_value = request.args.get('history_product_id', 'all')
        history_color_value = request.args.get('history_color_id', 'all')
        
        product_filter_id = _parse_int_param(product_filter_value)
        color_filter_id = _parse_int_param(color_filter_value)
        history_product_id = _parse_int_param(history_product_value)
        history_color_id = _parse_int_param(history_color_value)
        
        products = get_all_products(db, active_only=True)
        colors = get_all_colors(db, active_only=True)
        products_with_stock = [p for p in products if getattr(p, 'gestion_stock', False)]
        
        stock_entries = _get_filtered_finished_stock(db, include_empty=show_all,
                                                     product_id=product_filter_id,
                                                     color_id=color_filter_id)
        history_entries = _get_stock_movements_history(
            db,
            product_id=history_product_id,
            color_id=history_color_id,
            limit=80
        )
        
        summary = get_stock_summary(db)
        alerts_all = get_all_alerts(db)
        stock_alerts = filter_alerts(alerts_all, ['low_finished_goods_stock'])
        
        default_add_product_id = products_with_stock[0].id if products_with_stock else None
        default_add_color_id = colors[0].id if colors else None
        default_quantity = 1
        initial_metrics = None
        if default_add_product_id and default_add_color_id:
            initial_metrics = _calculate_finished_stock_metrics(
                db,
                default_add_product_id,
                default_add_color_id,
                default_quantity
            )
        
        context = {
            'summary': summary,
            'alerts': stock_alerts,
            'products': products,
            'colors': colors,
            'products_with_stock': products_with_stock,
            'stock_entries': stock_entries,
            'stock_count': len(stock_entries),
            'history_entries': history_entries,
            'history_count': len(history_entries),
            'stock_filters': {
                'product': product_filter_value,
                'color': color_filter_value,
                'show_all': show_all
            },
            'history_filters': {
                'product': history_product_value,
                'color': history_color_value
            },
            'add_defaults': {
                'product_id': default_add_product_id,
                'color_id': default_add_color_id,
                'quantity': default_quantity,
                'observations': ''
            },
            'initial_metrics': initial_metrics
        }
        
        return render_template('stock/index.html', **context)
    finally:
        db.close()


@app.route('/stock-produits-finis/partials/list')
@login_required
def stock_list_partial():
    """Fragment HTMX pour la liste du stock"""
    db = get_db_session()
    try:
        show_all = _parse_bool_param(request.args.get('show_all'), default=False)
        product_filter_id = _parse_int_param(request.args.get('product_id'))
        color_filter_id = _parse_int_param(request.args.get('color_id'))
        
        entries = _get_filtered_finished_stock(
            db,
            include_empty=show_all,
            product_id=product_filter_id,
            color_id=color_filter_id
        )
        
        return render_template('stock/_stock_list.html',
                               stock_entries=entries,
                               stock_count=len(entries),
                               show_all=show_all)
    finally:
        db.close()


@app.route('/stock-produits-finis/partials/history')
@login_required
def stock_history_partial():
    """Fragment HTMX pour l'historique"""
    db = get_db_session()
    try:
        product_filter_id = _parse_int_param(request.args.get('history_product_id'))
        color_filter_id = _parse_int_param(request.args.get('history_color_id'))
        limit = _parse_int_param(request.args.get('limit'), allow_all=False) or 80
        
        entries = _get_stock_movements_history(
            db,
            product_id=product_filter_id,
            color_id=color_filter_id,
            limit=limit
        )
        
        return render_template('stock/_history_list.html',
                               history_entries=entries,
                               history_count=len(entries))
    finally:
        db.close()


@app.route('/stock-produits-finis/partials/summary')
@login_required
def stock_summary_partial():
    """Fragment HTMX pour les cartes de synthèse"""
    db = get_db_session()
    try:
        from business_logic.finished_goods import get_stock_summary
        summary = get_stock_summary(db)
        return render_template('stock/_summary_cards.html', summary=summary)
    finally:
        db.close()


@app.route('/stock-produits-finis/add/metrics')
@login_required
def stock_add_metrics():
    """Fragment HTMX pour les métriques d'ajout"""
    db = get_db_session()
    try:
        product_id = _parse_int_param(request.args.get('product_id'), allow_all=False)
        color_id = _parse_int_param(request.args.get('color_id'), allow_all=False)
        quantity = _parse_int_param(request.args.get('quantity'), allow_all=False) or 0
        
        metrics = None
        if product_id and color_id and quantity > 0:
            metrics = _calculate_finished_stock_metrics(db, product_id, color_id, quantity)
        
        return render_template('stock/_add_metrics.html', metrics=metrics)
    finally:
        db.close()


@app.route('/stock-produits-finis/add', methods=['POST'])
@login_required
def stock_add_entry():
    """Ajouter des produits au stock"""
    db = get_db_session()
    try:
        from business_logic.finished_goods import add_to_stock
        
        product_id = _parse_int_param(request.form.get('product_id'), allow_all=False)
        color_id = _parse_int_param(request.form.get('color_id'), allow_all=False)
        quantity = _parse_int_param(request.form.get('quantity'), allow_all=False)
        observations = request.form.get('observations', '').strip() or None
        
        if not product_id or not color_id:
            return '<div class="alert-error">Produit ou couleur invalide</div>', 400
        if not quantity or quantity <= 0:
            return '<div class="alert-error">La quantité doit être positive</div>', 400
        
        add_to_stock(
            db=db,
            product_id=product_id,
            color_id=color_id,
            quantite=quantity,
            user_id=session.get('user_id'),
            observations=observations
        )
        
        return '<script>window.location.reload();</script>'
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


@app.route('/stock-produits-finis/stock/<int:stock_id>/adjust-form')
@login_required
def stock_adjust_form(stock_id):
    """Afficher le formulaire d'ajustement"""
    db = get_db_session()
    try:
        from database.models import FinishedGoodsStock
        from sqlalchemy.orm import joinedload
        
        stock_entry = db.query(FinishedGoodsStock)\
            .options(
                joinedload(FinishedGoodsStock.product),
                joinedload(FinishedGoodsStock.color)
            )\
            .get(stock_id)
        
        if not stock_entry:
            return '<div class="alert-error">Entrée introuvable.</div>', 404
        
        return render_template('stock/_adjust_form.html', stock_entry=stock_entry)
    finally:
        db.close()


@app.route('/stock-produits-finis/stock/<int:stock_id>/adjust', methods=['POST'])
@login_required
def stock_adjust(stock_id):
    """Ajuster une entrée de stock"""
    db = get_db_session()
    try:
        from business_logic.finished_goods import adjust_stock
        from database.models import FinishedGoodsStock
        
        stock_entry = db.query(FinishedGoodsStock).get(stock_id)
        if not stock_entry:
            return '<div class="alert-error">Entrée introuvable.</div>', 404
        
        new_quantity = _parse_int_param(request.form.get('new_quantity'), allow_all=False)
        observations = request.form.get('observations', '').strip() or None
        
        if new_quantity is None or new_quantity < 0:
            return '<div class="alert-error">Quantité invalide.</div>', 400
        
        adjust_stock(
            db=db,
            product_id=stock_entry.product_id,
            color_id=stock_entry.color_id,
            new_quantity=new_quantity,
            user_id=session.get('user_id'),
            observations=observations
        )
        
        return '<script>window.closeStockAdjustModal && window.closeStockAdjustModal(); window.location.reload();</script>'
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


# ========== PRODUCTION ==========

def _build_finished_stock_cache(db, lines, cache=None):
    """Cache stock quantities per product/color combination."""
    from business_logic.finished_goods import get_stock_by_product_color
    cache = cache or {}
    for line in lines:
        key = f"{line.product_id}-{line.color_id}"
        if key in cache:
            continue
        stock_entry = get_stock_by_product_color(db, line.product_id, line.color_id)
        cache[key] = stock_entry.quantite_disponible if stock_entry else 0
    return cache


def _build_time_slots(lines):
    """Return start/end datetimes for in-progress lines."""
    slots = {}
    for line in lines:
        if line.date_debut_impression and line.temps_impression_total_minutes:
            duration = line.temps_impression_total_minutes or 0
            slots[line.id] = {
                'start': line.date_debut_impression,
                'end': line.date_debut_impression + timedelta(minutes=duration)
            }
    return slots


def _production_reload_response(close_modal: bool = False):
    """Return a small JS snippet that closes modal (optional) then reloads."""
    close_js = 'if(window.closeProductionModal){window.closeProductionModal();}' if close_modal else ''
    return f'<script>{close_js}window.location.reload();</script>'

def _production_error_response(message: str):
    """Return an inline error block for production modals."""
    return f'<div class="alert-error">{message}</div>'


# ========== PAGES VIDES (À MIGRER) ==========

@app.route('/production')
@login_required
def production():
    """Gestion de la production"""
    db = get_db_session()
    try:
        from business_logic.production import (
            get_production_queue,
            get_personalized_items,
            get_all_printers_workload
        )
        from business_logic.automation import get_all_alerts, filter_alerts
        from database.models import FabricationStatus, OrderLine
        from sqlalchemy.orm import joinedload
        
        product_filter_value = request.args.get('product_id', 'all')
        color_filter_value = request.args.get('color_id', 'all')
        order_search = request.args.get('order_search', '').strip()
        product_filter_id = _parse_int_param(product_filter_value)
        color_filter_id = _parse_int_param(color_filter_value)
        
        queue_all = get_production_queue(db)
        
        # Build filter options before filtering
        # Only include orders that have at least one item left to print (not IMPRIME/LIVRE_LEA)
        orders_to_print = {
            line.order_id 
            for line in queue_all 
            if line.statut_fabrication != FabricationStatus.IMPRIME
        }
        
        product_options = sorted(
            {(line.product_id, line.product.nom) for line in queue_all if line.product},
            key=lambda item: item[1].lower()
        )
        color_options = sorted(
            {(line.color_id, line.color.nom) for line in queue_all if line.color},
            key=lambda item: item[1].lower()
        )
        
        # Apply filters
        queue_lines = queue_all
        if product_filter_id:
            queue_lines = [line for line in queue_lines if line.product_id == product_filter_id]
        if color_filter_id:
            queue_lines = [line for line in queue_lines if line.color_id == color_filter_id]
        if order_search:
            # Filtrer par numéro de commande, nom ou prénom du client
            search_lower = order_search.lower()
            filtered_queue_lines = []
            for line in queue_lines:
                if not line.order:
                    continue
                # Vérifier le numéro de commande
                if search_lower in line.order.numero_commande.lower():
                    filtered_queue_lines.append(line)
                    continue
                # Vérifier le nom et prénom du client
                if line.order.client:
                    client_nom = (line.order.client.nom or '').lower()
                    client_prenom = (line.order.client.prenom or '').lower()
                    client_full_name = (line.order.client.full_name or '').lower()
                    if (search_lower in client_nom or 
                        search_lower in client_prenom or 
                        search_lower in client_full_name):
                        filtered_queue_lines.append(line)
            queue_lines = filtered_queue_lines
        
        # Cache stock and time slots
        stock_cache = _build_finished_stock_cache(db, queue_all)
        personalized_items = get_personalized_items(db)
        
        # Apply filters to personalized items too
        if product_filter_id:
            personalized_items = [line for line in personalized_items if line.product_id == product_filter_id]
        if color_filter_id:
            personalized_items = [line for line in personalized_items if line.color_id == color_filter_id]
        if order_search:
            # Filtrer par numéro de commande, nom ou prénom du client
            search_lower = order_search.lower()
            filtered_personalized = []
            for line in personalized_items:
                if not line.order:
                    continue
                # Vérifier le numéro de commande
                if search_lower in line.order.numero_commande.lower():
                    filtered_personalized.append(line)
                    continue
                # Vérifier le nom et prénom du client
                if line.order.client:
                    client_nom = (line.order.client.nom or '').lower()
                    client_prenom = (line.order.client.prenom or '').lower()
                    client_full_name = (line.order.client.full_name or '').lower()
                    if (search_lower in client_nom or 
                        search_lower in client_prenom or 
                        search_lower in client_full_name):
                        filtered_personalized.append(line)
            personalized_items = filtered_personalized
            
        stock_cache = _build_finished_stock_cache(db, personalized_items, cache=stock_cache)
        time_slots = _build_time_slots(queue_lines)
        
        # Helper function to check if an order is complete (all lines printed)
        def _is_order_complete(order):
            """Check if all order lines are printed (IMPRIME or LIVRE_LEA)"""
            if not order or not order.order_lines:
                return False
            return all(
                line.statut_fabrication in [FabricationStatus.IMPRIME, FabricationStatus.LIVRE_LEA]
                for line in order.order_lines
            )
        
        # Group queue by status
        status_groups = {
            'en_cours': [],
            'prochain': [],
            'file_attente': [],
            'imprime': []
        }
        for line in queue_lines:
            if line.statut_fabrication == FabricationStatus.EN_COURS:
                status_groups['en_cours'].append(line)
            elif line.statut_fabrication == FabricationStatus.PROCHAIN:
                status_groups['prochain'].append(line)
            elif line.statut_fabrication == FabricationStatus.FILE_ATTENTE:
                status_groups['file_attente'].append(line)
            elif line.statut_fabrication == FabricationStatus.IMPRIME:
                # Only include IMPRIME lines if the order is NOT complete
                # (i.e., there are other lines not yet printed)
                if not _is_order_complete(line.order):
                    status_groups['imprime'].append(line)
        
        def _file_attente_sort_key(order_line):
            order_obj = order_line.order
            delivery = order_obj.date_livraison_prevue if order_obj and order_obj.date_livraison_prevue else datetime.max
            creation = order_obj.date_commande if order_obj and order_obj.date_commande else (
                order_obj.created_at if order_obj and order_obj.created_at else datetime.max
            )
            return (delivery, creation, order_line.id)
        
        status_groups['file_attente'] = sorted(status_groups['file_attente'], key=_file_attente_sort_key)
        
        active_queue_lines = [
            line for line in queue_lines
            if line.statut_fabrication != FabricationStatus.IMPRIME
        ]
        
        queue_sections = [
            {'key': 'en_cours', 'icon': 'refresh', 'title': "En cours d'impression", 'lines': status_groups['en_cours']},
            {'key': 'prochain', 'icon': 'star', 'title': "Prochain à imprimer", 'lines': status_groups['prochain']},
            {'key': 'file_attente', 'icon': 'clock', 'title': "En file d'attente", 'lines': status_groups['file_attente']},
            {'key': 'imprime', 'icon': 'check', 'title': "Imprimé - commande incomplète", 'lines': status_groups['imprime']},
        ]
        
        # Group personalization by color
        personalized_groups = []
        if personalized_items:
            grouped = {}
            for line in personalized_items:
                color_id = line.color_id or 0
                if color_id not in grouped:
                    grouped[color_id] = {
                        'color': line.color,
                        'statuses': {
                            'en_cours': [],
                            'prochain': [],
                            'file_attente': []
                        }
                    }
                if line.statut_fabrication == FabricationStatus.EN_COURS:
                    grouped[color_id]['statuses']['en_cours'].append(line)
                elif line.statut_fabrication == FabricationStatus.PROCHAIN:
                    grouped[color_id]['statuses']['prochain'].append(line)
                else:
                    grouped[color_id]['statuses']['file_attente'].append(line)
            personalized_groups = sorted(
                grouped.values(),
                key=lambda item: (item['color'].nom if item['color'] else '')
            )
        
        workloads = get_all_printers_workload(db)
        
        history_lines = db.query(OrderLine)\
            .options(
                joinedload(OrderLine.product),
                joinedload(OrderLine.color),
                joinedload(OrderLine.order),
                joinedload(OrderLine.printer)
            )\
            .filter(
                OrderLine.statut_fabrication.in_([
                    FabricationStatus.IMPRIME,
                    FabricationStatus.LIVRE_LEA
                ])
            )\
            .order_by(OrderLine.date_fin_impression.desc())\
            .limit(25)\
            .all()
        
        alerts_all = get_all_alerts(db)
        order_alerts = filter_alerts(alerts_all, ['overdue_delivery', 'old_pending_order'])
        
        context = {
            'queue_sections': queue_sections,
            'queue_count': sum(line.quantite for line in active_queue_lines),
            'product_options': [{'id': pid, 'label': label} for pid, label in product_options],
            'color_options': [{'id': cid, 'label': label} for cid, label in color_options],
            'filters': {
                'product': product_filter_value,
                'color': color_filter_value,
                'order_search': order_search
            },
            'stock_cache': stock_cache,
            'time_slots': time_slots,
            'personalized_groups': personalized_groups,
            'personalized_count': sum(line.quantite for line in personalized_items),
            'workloads': workloads,
            'history_lines': history_lines,
            'alerts': order_alerts
        }
        
        return render_template('production/index.html', **context)
    finally:
        db.close()


@app.route('/production/lines/<int:line_id>/start-form')
@login_required
def production_start_form(line_id):
    """Formulaire de démarrage : imprimante pré-assignée proposée par défaut, imprimantes occupées non validables"""
    return _render_printer_selection_modal(
        line_id,
        action_endpoint='production_start_line',
        submit_label='Démarrer',
        submit_icon='play',
        block_busy=True
    )


@app.route('/production/lines/<int:line_id>/assign-form')
@login_required
def production_assign_form(line_id):
    """Formulaire d'assignation : toutes les imprimantes, même occupées (l'impression démarrera plus tard)"""
    return _render_printer_selection_modal(
        line_id,
        action_endpoint='production_assign_printer',
        submit_label='Assigner',
        submit_icon='printer',
        block_busy=False
    )


def _render_printer_selection_modal(line_id, action_endpoint, submit_label, submit_icon, block_busy):
    db = get_db_session()
    try:
        from database.models import OrderLine, Printer, FabricationStatus
        from sqlalchemy.orm import joinedload

        line = db.query(OrderLine)\
            .options(
                joinedload(OrderLine.product),
                joinedload(OrderLine.color),
                joinedload(OrderLine.order)
            ).get(line_id)
        if not line:
            return '<div class="alert-error">Ligne introuvable.</div>', 404

        printers = db.query(Printer).order_by(Printer.nom.asc()).all()
        if not printers:
            return _production_error_response("Aucune imprimante n'est configurée.")

        busy_jobs = {
            job.printer_id: job for job in
            db.query(OrderLine)
              .options(joinedload(OrderLine.order), joinedload(OrderLine.product))
              .filter(
                  OrderLine.printer_id.isnot(None),
                  OrderLine.statut_fabrication == FabricationStatus.EN_COURS,
                  OrderLine.id != line.id
              )
        }
        printer_options = []
        for printer in printers:
            job = busy_jobs.get(printer.id)
            busy_label = None
            if job:
                busy_label = (
                    f"{printer.nom} est en cours d'impression "
                    f"({job.order.numero_commande if job.order else '?'} — {job.product.nom if job.product else 'objet'}). "
                    "Termine cette impression ou choisis une autre imprimante."
                )
            printer_options.append({'printer': printer, 'busy': bool(job), 'busy_label': busy_label})

        if line.printer_id and any(o['printer'].id == line.printer_id for o in printer_options):
            selected_id = line.printer_id
        else:
            free = [o for o in printer_options if not o['busy']]
            selected_id = (free[0] if free else printer_options[0])['printer'].id
        selected = next(o for o in printer_options if o['printer'].id == selected_id)

        return render_template(
            'production/_start_form.html',
            line=line,
            printer_options=printer_options,
            selected_id=selected_id,
            selected_busy=block_busy and selected['busy'],
            selected_busy_label=selected['busy_label'] if block_busy else None,
            block_busy=block_busy,
            form_action=url_for(action_endpoint, line_id=line.id),
            submit_label=submit_label,
            submit_icon=submit_icon
        )
    finally:
        db.close()


@app.route('/production/lines/<int:line_id>/start', methods=['POST'])
@login_required
def production_start_line(line_id):
    """Démarrer l'impression d'une ligne"""
    db = get_db_session()
    try:
        from business_logic.production import start_fabrication
        printer_id = _parse_int_param(request.form.get('printer_id'), allow_all=False)
        if not printer_id:
            return '<div class="alert-error">Sélection d\'imprimante invalide.</div>', 400
        
        start_fabrication(db, line_id, printer_id)
        return _production_reload_response(close_modal=True)
    except ValueError as e:
        return _production_error_response(str(e))
    except Exception as e:
        return _production_error_response(f'Erreur inattendue: {str(e)}')
    finally:
        db.close()


@app.route('/production/lines/<int:line_id>/assign', methods=['POST'])
@login_required
def production_assign_printer(line_id):
    """Assigner une imprimante à une ligne sans la démarrer"""
    db = get_db_session()
    try:
        from business_logic.production import assign_printer
        printer_id = _parse_int_param(request.form.get('printer_id'), allow_all=False)
        if not printer_id:
            return '<div class="alert-error">Sélection d\'imprimante invalide.</div>', 400
        
        assign_printer(db, line_id, printer_id)
        return _production_reload_response(close_modal=True)
    except ValueError as e:
        return _production_error_response(str(e))
    except Exception as e:
        return _production_error_response(f'Erreur inattendue: {str(e)}')
    finally:
        db.close()


@app.route('/production/lines/<int:line_id>/complete', methods=['POST'])
@login_required
def production_complete_line(line_id):
    """Marquer l'impression comme terminée"""
    db = get_db_session()
    try:
        from business_logic.production import complete_fabrication
        complete_fabrication(db, line_id, session.get('user_id'))
        return _production_reload_response()
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


@app.route('/production/lines/<int:line_id>/use-stock', methods=['POST'])
@login_required
def production_use_stock(line_id):
    """Utiliser un objet du stock fini pour cette ligne"""
    db = get_db_session()
    try:
        from business_logic.finished_goods import use_from_stock
        use_from_stock(db, line_id, session.get('user_id'))
        return _production_reload_response()
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


@app.route('/production/lines/<int:line_id>/reset', methods=['POST'])
@login_required
def production_reset_line(line_id):
    """Remettre la ligne en file d'attente"""
    db = get_db_session()
    try:
        from business_logic.production import set_fabrication_status_to_file_attente
        set_fabrication_status_to_file_attente(db, line_id)
        return _production_reload_response()
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


@app.route('/production/lines/<int:line_id>/prioritize', methods=['POST'])
@login_required
def production_prioritize_line(line_id):
    """Marquer la ligne comme prioritaire (prochain)"""
    db = get_db_session()
    try:
        from business_logic.production import set_fabrication_status_to_prochain
        set_fabrication_status_to_prochain(db, line_id)
        return _production_reload_response()
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


@app.route('/production/lines/<int:line_id>/unprioritize', methods=['POST'])
@login_required
def production_unprioritize_line(line_id):
    """Retirer la priorité d'une ligne"""
    db = get_db_session()
    try:
        from business_logic.production import set_fabrication_status_to_file_attente_from_prochain
        set_fabrication_status_to_file_attente_from_prochain(db, line_id)
        return _production_reload_response()
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


@app.route('/emballage')
@login_required
def packaging():
    """File d'emballage"""
    db = get_db_session()
    try:
        from business_logic.orders import get_orders_ready_for_packaging, get_orders_ready_for_delivery
        from database.models import DeliveryMode
        
        orders = get_orders_ready_for_packaging(db)
        total_weight = sum(
            (line.poids_total_grammes or 0)
            for order in orders for line in order.order_lines
        )
        
        # Séparer les commandes par type d'action
        # "transfer_orders" : Commandes EN_PREPARATION qui ont ne_pas_emballer=True -> Action: Transférer
        # "packaging_orders" : Commandes EN_PREPARATION qui ont ne_pas_emballer=False -> Action: Emballer
        
        transfer_orders = [o for o in orders if o.ne_pas_emballer]
        packaging_orders_list = [o for o in orders if not o.ne_pas_emballer]
        
        # Pour les commandes "Emballer", on les sépare par mode de livraison
        pickup_orders = [o for o in packaging_orders_list if o.mode_livraison == DeliveryMode.PICKUP]
        delivery_orders = [o for o in packaging_orders_list if o.mode_livraison == DeliveryMode.LIVRAISON]
        
        # Récupérer les commandes "Ne pas emballer" QUI SONT DÉJÀ EN PRET_LIVRAISON
        # Celles-ci sont déjà transférées, donc purement indicatives dans cette vue
        ready_orders = get_orders_ready_for_delivery(db)
        skipped_packaging_orders = [o for o in ready_orders if o.ne_pas_emballer]
        
        packaging_stats = {
            'orders_count': len(orders),
            'lines_count': sum(len(order.order_lines) for order in orders),
            'total_weight': int(total_weight)
        }
        return render_template(
            'packaging/index.html',
            transfer_orders=transfer_orders,
            pickup_orders=pickup_orders,
            delivery_orders=delivery_orders,
            skipped_packaging_orders=skipped_packaging_orders,
            stats=packaging_stats
        )
    finally:
        db.close()


@app.route('/emballage/orders/<int:order_id>/ready', methods=['POST'])
@login_required
def packaging_mark_ready(order_id):
    """Bouton 'À livrer'."""
    db = get_db_session()
    try:
        from business_logic.orders import mark_order_ready_for_delivery
        mark_order_ready_for_delivery(db, order_id, session.get('user_id'))
        return '<script>window.location.reload();</script>'
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


@app.route('/livraison')
@login_required
def delivery():
    """Gestion des livraisons clients"""
    db = get_db_session()
    try:
        from business_logic.orders import get_orders_ready_for_delivery
        from database.models import DeliveryMode
        
        ready_orders = get_orders_ready_for_delivery(db)
        
        # Séparer les commandes par mode de livraison
        pickup_orders = [o for o in ready_orders if o.mode_livraison == DeliveryMode.PICKUP]
        delivery_orders = [o for o in ready_orders if o.mode_livraison == DeliveryMode.LIVRAISON]
        
        context = {
            'pickup_orders': pickup_orders,
            'delivery_orders': delivery_orders,
            'orders_count': len(ready_orders)
        }
        return render_template('delivery/index.html', **context)
    finally:
        db.close()


@app.route('/livraison/orders/<int:order_id>/deliver', methods=['POST'])
@login_required
def delivery_mark_order(order_id):
    """Marquer une commande comme livrée au client"""
    db = get_db_session()
    try:
        from business_logic.orders import update_order_status
        from database.models import Order
        order_obj = db.get(Order, order_id)
        if not order_obj:
            return jsonify({'error': f'Commande {order_id} introuvable.'}), 404
        if order_obj.statut != OrderStatus.PRET_LIVRAISON:
            return jsonify({'error': 'La commande doit être en statut "Prêt à livrer".'}), 400
        
        update_order_status(db, order_id, OrderStatus.LIVRE, session.get('user_id'))
        return '<script>window.location.reload();</script>'
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/finances')
@login_required
def finances():
    """Tableau de bord financier - Système de balance/solde"""
    db = get_db_session()
    try:
        from business_logic.financial import (
            get_financial_tables,
            get_total_reverse,
            get_reversements_history,
            get_benefice_statistics
        )
        
        # Date filters for statistics
        stats_start_str = request.args.get('stats_start')
        stats_end_str = request.args.get('stats_end')
        today = datetime.utcnow().date()
        default_start = today - timedelta(days=30)
        try:
            stats_start_date = datetime.strptime(stats_start_str, '%Y-%m-%d').date() if stats_start_str else default_start
        except ValueError:
            stats_start_date = default_start
        try:
            stats_end_date = datetime.strptime(stats_end_str, '%Y-%m-%d').date() if stats_end_str else today
        except ValueError:
            stats_end_date = today
        
        start_dt = datetime.combine(stats_start_date, datetime.min.time())
        end_dt = datetime.combine(stats_end_date, datetime.max.time())
        
        # Get financial tables (4 tables by product)
        tables = get_financial_tables(db)
        
        # Get balance data for Table 5
        total_deja_reverse = get_total_reverse(db)
        solde_restant = round(tables['total_reversement'] - total_deja_reverse, 2)
        
        # Get statistics and history
        stats = get_benefice_statistics(db, start_dt, end_dt)
        history = get_reversements_history(db, limit=50)
        
        context = {
            # 4 tables
            'benefices': tables['benefices'],
            'benefices_totals': tables['benefices_totals'],
            'retrocessions_david': tables['retrocessions_david'],
            'retrocessions_david_totals': tables['retrocessions_david_totals'],
            'benefice_lea': tables['benefice_lea'],
            'benefice_lea_totals': tables['benefice_lea_totals'],
            'revente_lea': tables['revente_lea'],
            'revente_lea_totals': tables['revente_lea_totals'],
            # Table 5: Solde
            'total_reversement': tables['total_reversement'],
            'total_deja_reverse': total_deja_reverse,
            'solde_restant': solde_restant,
            # Stats
            'stats': stats,
            'stats_filters': {
                'start': stats_start_date.strftime('%Y-%m-%d'),
                'end': stats_end_date.strftime('%Y-%m-%d')
            },
            'history': history
        }
        return render_template('finances/index.html', **context)
    finally:
        db.close()


@app.route('/finances/reversements', methods=['POST'])
@login_required
def finances_create_reversement():
    """Créer un reversement (paiement à David)"""
    db = get_db_session()
    try:
        from business_logic.financial import create_reversement
        
        # Get amount
        montant_str = request.form.get('montant', '').strip()
        if not montant_str:
            return '<div class="alert-error">Le montant est requis.</div>', 400
        
        try:
            montant = float(montant_str.replace(',', '.'))
        except ValueError:
            return '<div class="alert-error">Montant invalide.</div>', 400
        
        if montant <= 0:
            return '<div class="alert-error">Le montant doit être positif.</div>', 400
        
        commentaire = request.form.get('commentaire', '').strip() or None
        
        create_reversement(db, montant, commentaire, session.get('user_id'))
        return '<script>window.location.reload();</script>'
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


@app.route('/finances/reversements/<int:reversement_id>', methods=['DELETE'])
@login_required
def finances_delete_reversement(reversement_id):
    """Supprimer un reversement (en cas d'erreur)"""
    db = get_db_session()
    try:
        from business_logic.financial import delete_reversement
        
        if delete_reversement(db, reversement_id):
            return '<script>window.location.reload();</script>'
        else:
            return '<div class="alert-error">Reversement non trouvé.</div>', 404
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


# Custom Jinja filters
@app.template_filter('format_number')
def format_number(value):
    """Format number with space as thousand separator"""
    if value is None:
        return "0"
    try:
        return "{:,.0f}".format(float(value)).replace(",", " ")
    except (ValueError, TypeError):
        return str(value)

@app.route('/analytics')
@login_required
def analytics():
    """Statistiques et analytics"""
    db = get_db_session()
    try:
        from database.models import Order, OrderLine, OrderStatus
        from sqlalchemy.orm import joinedload
        from business_logic.financial import get_benefice_statistics
        
        start_str = request.args.get('start')
        end_str = request.args.get('end')
        today = datetime.utcnow().date()
        default_start = today - timedelta(days=90)
        try:
            start_date = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else default_start
        except ValueError:
            start_date = default_start
        try:
            end_date = datetime.strptime(end_str, '%Y-%m-%d').date() if end_str else today
        except ValueError:
            end_date = today
        
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        
        stats = get_benefice_statistics(db, start_dt, end_dt)
        
        # Valid statuses for charts: PAYE and onwards
        valid_statuses = [
            OrderStatus.PAYE,
            OrderStatus.IMPRIME,
            OrderStatus.EN_PREPARATION,
            OrderStatus.PRET_LIVRAISON,
            OrderStatus.LIVRE
        ]
        
        # Lines data for products/colors
        lines = db.query(OrderLine)\
            .options(
                joinedload(OrderLine.product),
                joinedload(OrderLine.color),
                joinedload(OrderLine.order)
            )\
            .join(Order)\
            .filter(
                Order.date_commande >= start_dt,
                Order.date_commande <= end_dt,
                Order.statut.in_(valid_statuses)
            ).all()
        
        product_stats = {}
        color_stats = {}
        for line in lines:
            product = line.product
            color = line.color
            if product:
                prod_entry = product_stats.setdefault(product.id, {
                    'nom': product.nom,
                    'quantite': 0,
                    'ventes': 0,
                    'benefice': 0
                })
                prod_entry['quantite'] += line.quantite or 0
                prod_entry['ventes'] += line.prix_vente_total or 0
                prod_entry['benefice'] += line.benefice_total or 0
            if color:
                color_entry = color_stats.setdefault(color.id, {
                    'nom': color.nom,
                    'code_hex': color.code_hex or '#cccccc',
                    'quantite': 0
                })
                color_entry['quantite'] += line.quantite or 0
        
        product_list = sorted(product_stats.values(), key=lambda x: x['ventes'], reverse=True)
        color_list = sorted(color_stats.values(), key=lambda x: x['quantite'], reverse=True)
        
        # Orders timeline
        orders = db.query(Order)\
            .filter(
                Order.date_commande >= start_dt,
                Order.date_commande <= end_dt,
                Order.statut.in_(valid_statuses)
            ).all()
        timeline_map = {}
        for order in orders:
            date_key = order.date_commande.date() if order.date_commande else None
            if not date_key:
                continue
            entry = timeline_map.setdefault(date_key, {'ventes': 0, 'count': 0})
            entry['ventes'] += order.total_prix_vente or 0
            # Sum quantities from order lines for 'count' (now represents objects count)
            entry['count'] += sum(line.quantite for line in order.order_lines)
        timeline = sorted(timeline_map.items(), key=lambda item: item[0])
        
        context = {
            'stats': stats,
            'date_filters': {
                'start': start_date.strftime('%Y-%m-%d'),
                'end': end_date.strftime('%Y-%m-%d')
            },
            'products': product_list,
            'colors': color_list,
            'timeline': [{'date': date.strftime('%Y-%m-%d'), 'ventes': data['ventes'], 'count': data['count']} for date, data in timeline]
        }
        return render_template('analytics/index.html', **context)
    finally:
        db.close()


@app.route('/parametres')
@login_required
def settings():
    """Paramètres et exports"""
    db = get_db_session()
    try:
        from database.models import Order, Client, Product, Color, Printer, OrderLine
        
        counts = {
            'orders': db.query(Order).count(),
            'clients': db.query(Client).count(),
            'products': db.query(Product).count(),
            'colors': db.query(Color).count(),
            'printers': db.query(Printer).count(),
            'order_lines': db.query(OrderLine).count()
        }
        
        from database.models import User
        current_user = db.query(User).filter(User.username == session['username']).first()
        context = {
            'counts': counts,
            'current_user': current_user,
        }
        return render_template('settings/index.html', **context)
    finally:
        db.close()


@app.route('/parametres/telegram', methods=['GET'])
@login_required
def settings_telegram():
    """Page de configuration Telegram pour l'utilisateur courant"""
    db = get_db_session()
    try:
        from database.models import User
        from config.settings import TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN
        user = db.query(User).filter(User.username == session['username']).first()
        return render_template(
            'settings/telegram.html',
            telegram_enabled=TELEGRAM_ENABLED,
            current_chat_id=user.telegram_chat_id if user else None,
        )
    finally:
        db.close()


@app.route('/parametres/telegram', methods=['POST'])
@login_required
def settings_telegram_save():
    """Sauvegarder le chat_id Telegram de l'utilisateur courant"""
    db = get_db_session()
    try:
        from database.models import User
        chat_id = request.form.get('telegram_chat_id', '').strip() or None
        user = db.query(User).filter(User.username == session['username']).first()
        if not user:
            return jsonify({'error': 'Utilisateur introuvable'}), 404
        user.telegram_chat_id = chat_id
        db.commit()

        # Test ping si un chat_id a été renseigné
        if chat_id:
            from utils.notifications import send_telegram_message
            send_telegram_message(chat_id, f"✅ Notifications Malitan 3D activées pour <b>{user.full_name}</b> !")

        flash('Paramètres Telegram sauvegardés.', 'success')
        return redirect(url_for('settings_telegram'))
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        db.close()


@app.route('/parametres/export/<string:dataset>', methods=['POST'])
@login_required
def settings_export(dataset):
    """Générer un export Excel"""
    db = get_db_session()
    try:
        from utils import exports
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        dataset = dataset.lower()
        filename_map = {
            'orders': f'commandes_{timestamp}.xlsx',
            'clients': f'clients_{timestamp}.xlsx',
            'products': f'produits_{timestamp}.xlsx',
            'stock': f'stock_{timestamp}.xlsx',
            'finances': f'finances_{timestamp}.xlsx',
            'full': f'malitan_full_{timestamp}.xlsx'
        }
        if dataset not in filename_map:
            return '<div class="alert-error">Export inconnu.</div>', 400
        
        if dataset == 'orders':
            data = exports.export_orders_to_excel(db)
        elif dataset == 'clients':
            data = exports.export_clients_to_excel(db)
        elif dataset == 'products':
            data = exports.export_products_to_excel(db)
        elif dataset == 'stock':
            data = exports.export_stock_to_excel(db)
        elif dataset == 'finances':
            data = exports.export_finances_to_excel(db)
        else:
            data = exports.export_full_database_to_excel(db)
        
        buffer = io.BytesIO(data)
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename_map[dataset],
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        return f'<div class="alert-error">Erreur: {str(e)}</div>', 400
    finally:
        db.close()


# Kubernetes probes (no login required)
READYZ_TIMEOUT_SECONDS = 2
# One worker: a hung database check can't pile up threads; later probes time out instead.
_readyz_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='readyz')


@app.route('/livez')
def livez():
    """Liveness: the process answers HTTP. Never touches the database."""
    return jsonify({'status': 'ok'}), 200


def _check_database():
    from database import connection
    engine = connection.engine if connection.engine is not None else connection.init_database()
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))


@app.route('/readyz')
def readyz():
    """Readiness: the database answers SELECT 1 within READYZ_TIMEOUT_SECONDS."""
    try:
        _readyz_executor.submit(_check_database).result(timeout=READYZ_TIMEOUT_SECONDS)
    except Exception:
        app.logger.exception('Readiness check failed')
        return jsonify({'status': 'unavailable'}), 503
    return jsonify({'status': 'ok'}), 200


@app.cli.command('init-db')
def init_db_command():
    """Create missing tables and seed the initial users if there are none."""
    from database.init_db import initialize_database

    initialize_database(echo=click.echo)


@app.cli.command('set-password')
@click.argument('username')
def set_password_command(username):
    """Set a user's password (prompted, never passed on the command line)."""
    from database.models import User
    from utils.auth import hash_password

    db = get_db_session()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            raise click.ClickException(f"User '{username}' does not exist.")

        password = getpass.getpass('New password: ')
        if not password:
            raise click.ClickException('Password cannot be empty.')
        if getpass.getpass('Confirm password: ') != password:
            raise click.ClickException('Passwords do not match.')

        user.password_hash = hash_password(password)
        db.commit()
        click.echo(f"Password updated for '{username}'.")
    finally:
        db.close()


if __name__ == '__main__':
    # Use port 5001 to avoid conflict with AirPlay on macOS
    print("🚀 Starting Flask development server on http://0.0.0.0:5001")
    app.run(debug=(ENV == 'development'), host='0.0.0.0', port=5001)
else:
    # Production mode (Gunicorn)
    print("🚀 Flask app loaded (production mode)")
    print(f"📊 Environment: {ENV}")
    print(f"📊 Database Type: {DATABASE_TYPE}")


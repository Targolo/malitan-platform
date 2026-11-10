"""
Indicators exposed to external agents by GET /api/status.

To add, remove or change an indicator, edit STATUS_METRICS only: each entry is
(key, label, function(db) -> dict). The response format stays the same.
Counts reuse the dashboard definitions so both always show the same numbers.
Never expose client names, order numbers or prices: the API is reachable from the internet.
"""
from typing import Dict
from sqlalchemy.orm import Session
from sqlalchemy import func
from database.models import Order, OrderLine, OrderStatus, Product, Color, FabricationStatus
from business_logic.dashboard import PRODUCTION_ORDER_STATUSES, TO_PRINT_STATUSES, count_orders

# Printed objects whose order is not packaged yet (paid orders, packaging included)
PRINTED_STATUSES = [FabricationStatus.IMPRIME, FabricationStatus.LIVRE_LEA]


def objects_by_product_and_color(db: Session, fabrication_statuses) -> Dict:
    """Objects of paid orders in `fabrication_statuses`, grouped by product and color (with its hex code)."""
    rows = db.query(Product.nom, Color.nom, Color.code_hex, func.sum(OrderLine.quantite))\
        .select_from(OrderLine)\
        .join(Order, OrderLine.order_id == Order.id)\
        .join(Product, OrderLine.product_id == Product.id)\
        .join(Color, OrderLine.color_id == Color.id)\
        .filter(
            Order.statut.in_(PRODUCTION_ORDER_STATUSES),
            OrderLine.statut_fabrication.in_(fabrication_statuses)
        )\
        .group_by(Product.nom, Color.nom, Color.code_hex)\
        .order_by(Product.nom, Color.nom)\
        .all()
    items = [
        {'objet': objet, 'couleur': couleur, 'code_couleur': code_couleur, 'quantite': quantite}
        for objet, couleur, code_couleur, quantite in rows
    ]
    return {'count': sum(item['quantite'] for item in items), 'items': items}


STATUS_METRICS = [
    ('objets_a_imprimer', "Objets à imprimer", lambda db: objects_by_product_and_color(db, TO_PRINT_STATUSES)),
    ('objets_imprimes', "Objets imprimés", lambda db: objects_by_product_and_color(db, PRINTED_STATUSES)),
    ('commandes_a_emballer', "Commandes à l'emballage",
     lambda db: {'count': count_orders(db, OrderStatus.EN_PREPARATION)}),
    ('commandes_en_attente_livraison', "Commandes en attente de livraison",
     lambda db: {'count': count_orders(db, OrderStatus.PRET_LIVRAISON)}),
]


def get_status_metrics(db: Session) -> Dict[str, Dict]:
    """All STATUS_METRICS, in order, as {key: {'label': ..., 'count': ..., ['items': [...]]}}."""
    return {key: {'label': label, **compute(db)} for key, label, compute in STATUS_METRICS}

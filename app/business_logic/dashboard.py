"""
Dashboard metrics and statistics
Centralizes all dashboard-related calculations
"""
from typing import Dict
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from database.models import Order, OrderStatus, OrderLine, FabricationStatus

# Same scope as the production page (get_production_queue): only paid orders are printed
PRODUCTION_ORDER_STATUSES = [OrderStatus.PAYE, OrderStatus.IMPRIME, OrderStatus.EN_PREPARATION]
TO_PRINT_STATUSES = [FabricationStatus.FILE_ATTENTE, FabricationStatus.PROCHAIN, FabricationStatus.EN_COURS]


def count_objects_to_print(db: Session, statuses=TO_PRINT_STATUSES) -> int:
    """Objects (sum of line quantities) of paid orders whose fabrication status is in `statuses`."""
    return db.query(func.coalesce(func.sum(OrderLine.quantite), 0)).join(Order).filter(
        Order.statut.in_(PRODUCTION_ORDER_STATUSES),
        OrderLine.statut_fabrication.in_(statuses)
    ).scalar()


def count_orders(db: Session, statut: OrderStatus) -> int:
    """Number of orders currently in `statut`."""
    return db.query(Order).filter(Order.statut == statut).count()


def get_dashboard_metrics(db: Session) -> Dict[str, int]:
    """
    Get all dashboard key metrics

    Each metric counts either orders (commandes) or physical objects (sum of line
    quantities), never order lines, so the dashboard units stay unambiguous.

    Args:
        db: Database session

    Returns:
        Dictionary with all dashboard metrics:
        - total_en_attente_paiement: Orders waiting for payment
        - total_en_attente_impression: Paid orders with at least one object left to print
        - total_objets_en_prod: Objects left to print (queue, next, printing)
        - total_objets_en_cours: Objects currently printing
        - total_a_emballer: Orders being packaged (EN_PREPARATION)
        - total_en_attente_livraison: Orders ready for delivery
        - total_commandes_en_cours: Orders neither delivered nor cancelled
    """
    # 1. Commandes en attente de paiement
    total_en_attente_paiement = db.query(Order).filter(
        Order.statut == OrderStatus.EN_ATTENTE
    ).count()

    # 2. Commandes payées avec au moins un objet encore à imprimer
    total_en_attente_impression = db.query(Order.id).join(OrderLine).filter(
        Order.statut.in_(PRODUCTION_ORDER_STATUSES)
    ).group_by(Order.id).having(
        func.count(
            case(
                (OrderLine.statut_fabrication.in_(TO_PRINT_STATUSES), 1),
                else_=None
            )
        ) > 0
    ).count()

    # 3. Objets à imprimer (file d'attente + prochain + en cours)
    total_objets_en_prod = count_objects_to_print(db)

    # 4. Objets en cours d'impression
    total_objets_en_cours = count_objects_to_print(db, [FabricationStatus.EN_COURS])

    # 5. Commandes en attente de livraison
    total_en_attente_livraison = count_orders(db, OrderStatus.PRET_LIVRAISON)

    # 6. Commandes à l'emballage
    total_a_emballer = count_orders(db, OrderStatus.EN_PREPARATION)

    # 7. Commandes en cours (même définition que le filtre "En cours" de la page Commandes)
    total_commandes_en_cours = db.query(Order).filter(
        Order.statut.notin_([OrderStatus.LIVRE, OrderStatus.ANNULE])
    ).count()

    return {
        'total_en_attente_paiement': total_en_attente_paiement,
        'total_en_attente_impression': total_en_attente_impression,
        'total_objets_en_prod': total_objets_en_prod,
        'total_objets_en_cours': total_objets_en_cours,
        'total_en_attente_livraison': total_en_attente_livraison,
        'total_a_emballer': total_a_emballer,
        'total_commandes_en_cours': total_commandes_en_cours,
    }

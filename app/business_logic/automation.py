"""
Automation and notification logic (future enhancements)
Placeholder for future automation features
"""
from typing import Iterable, List, Optional
from sqlalchemy.orm import Session, joinedload
from database.models import Color, Order, OrderStatus, Product, FinishedGoodsStock
from business_logic.inventory import get_low_stock_colors
from business_logic.finished_goods import get_all_stock


def check_low_stock_alerts(db: Session) -> List[dict]:
    """
    Check for low stock colors and return alert information
    NOTE: This function is kept for compatibility but alerts are now hidden
    (filament stock management is not currently used)
    
    Args:
        db: Database session
    
    Returns:
        Empty list (alerts are hidden)
    """
    # Filament stock alerts are hidden - not managing filament stock currently
    return []


def check_low_finished_goods_stock(db: Session) -> List[dict]:
    """
    Check for low stock finished goods and return alert information
    
    Args:
        db: Database session
    
    Returns:
        List of alert dicts
    """
    # Get all products with gestion_stock enabled
    products_with_stock_management = db.query(Product).filter(
        Product.gestion_stock == True,
        Product.actif == True
    ).all()
    
    if not products_with_stock_management:
        return []
    
    alerts = []
    low_stock_items = []
    
    # Check stock for each product
    for product in products_with_stock_management:
        if product.alerte_stock is None or product.alerte_stock <= 0:
            continue  # Skip products without alert threshold
        
        # Get all stock entries for this product
        stock_entries = db.query(FinishedGoodsStock).filter(
            FinishedGoodsStock.product_id == product.id
        ).options(
            joinedload(FinishedGoodsStock.color)
        ).all()
        
        # Check each stock entry (product + color combination)
        # Only alert for products that already have stock entries
        for stock in stock_entries:
            if stock.quantite_disponible <= product.alerte_stock:
                low_stock_items.append({
                    'product': product,
                    'color': stock.color,
                    'stock': stock.quantite_disponible,
                    'threshold': product.alerte_stock
                })
    
    if low_stock_items:
        # Group all low stock items into a single alert
        item_names = []
        has_critical = False
        
        for item in low_stock_items:
            product_name = item['product'].nom
            color_name = item['color'].nom
            stock_qty = item['stock']
            threshold = item['threshold']
            
            item_names.append(f"{product_name} ({color_name}): {stock_qty} unité(s)")
            
            # Critical if stock is 0
            if stock_qty == 0:
                has_critical = True
        
        severity = 'critical' if has_critical else 'warning'
        message = f"{len(low_stock_items)} produit(s) fini(s) avec stock bas: {', '.join(item_names)}"
        
        alerts.append({
            'type': 'low_finished_goods_stock',
            'severity': severity,
            'items': low_stock_items,
            'message': message
        })
    
    return alerts


def check_pending_orders(db: Session) -> List[dict]:
    """
    Check for orders needing attention
    
    Args:
        db: Database session
    
    Returns:
        List of alert dicts
    """
    from datetime import datetime, timedelta
    
    alerts = []
    
    # Orders in EN_ATTENTE for more than 3 days
    three_days_ago = datetime.utcnow() - timedelta(days=3)
    old_pending = db.query(Order).filter(
        Order.statut == OrderStatus.EN_ATTENTE,
        Order.date_commande < three_days_ago
    ).all()
    
    for order in old_pending:
        alerts.append({
            'type': 'old_pending_order',
            'severity': 'warning',
            'order': order,
            'message': f"Commande {order.numero_commande} en attente depuis {(datetime.utcnow() - order.date_commande).days} jours"
        })
    
    # Orders past expected delivery date
    today = datetime.utcnow()
    overdue = db.query(Order).filter(
        Order.date_livraison_prevue != None,
        Order.date_livraison_prevue < today,
        Order.statut != OrderStatus.LIVRE,
        Order.statut != OrderStatus.ANNULE
    ).all()
    
    for order in overdue:
        alerts.append({
            'type': 'overdue_delivery',
            'severity': 'critical',
            'order': order,
            'message': f"Commande {order.numero_commande} en retard (livraison prévue le {order.date_livraison_prevue.date()})"
        })
    
    return alerts


def get_all_alerts(db: Session) -> dict:
    """
    Get all system alerts
    
    Args:
        db: Database session
    
    Returns:
        dict with categorized alerts
    """
    # Filament stock alerts are hidden (not managing filament stock currently)
    stock_alerts = check_low_stock_alerts(db)  # Returns empty list
    finished_goods_alerts = check_low_finished_goods_stock(db)
    order_alerts = check_pending_orders(db)
    
    all_alerts = stock_alerts + finished_goods_alerts + order_alerts
    
    critical = [a for a in all_alerts if a['severity'] == 'critical']
    warning = [a for a in all_alerts if a['severity'] == 'warning']
    
    return {
        'total': len(all_alerts),
        'critical': critical,
        'warning': warning,
        'all': all_alerts
    }


def filter_alerts(alerts: Optional[dict], allowed_types: Optional[Iterable[str]] = None) -> dict:
    """
    Return a filtered copy of the alerts dictionary limited to certain types.
    Preserves the structure expected by the UI (total/critical/warning/all).
    """
    if not alerts:
        return {
            'total': 0,
            'critical': [],
            'warning': [],
            'all': []
        }
    
    if not allowed_types:
        return {
            'total': alerts.get('total', len(alerts.get('all', []))),
            'critical': list(alerts.get('critical', [])),
            'warning': list(alerts.get('warning', [])),
            'all': list(alerts.get('all', []))
        }
    
    allowed = set(allowed_types)
    
    def _filter(items):
        return [item for item in items if item.get('type') in allowed]
    
    filtered_all = _filter(alerts.get('all', []))
    filtered_critical = _filter(alerts.get('critical', []))
    filtered_warning = _filter(alerts.get('warning', []))
    
    return {
        'total': len(filtered_all),
        'critical': filtered_critical,
        'warning': filtered_warning,
        'all': filtered_all
    }


# Future enhancements:
# - Email notifications
# - SMS notifications
# - Automatic backup scheduling
# - Client notification templates
# - Production scheduling optimization


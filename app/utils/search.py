"""
Global Search Utilities
Search functionality for orders, clients, and products
"""
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from database.models import Order, Client, Product


def search_orders(db: Session, query: str, limit: int = 10) -> List[Order]:
    """
    Search orders by order number
    
    Args:
        db: Database session
        query: Search query string
        limit: Maximum number of results
    
    Returns:
        List of Order objects matching the query
    """
    search_term = f"%{query}%"
    return db.query(Order).filter(
        Order.numero_commande.ilike(search_term)
    ).order_by(Order.date_commande.desc()).limit(limit).all()


def search_clients(db: Session, query: str, limit: int = 10) -> List[Client]:
    """
    Search clients by name, email, or phone
    
    Args:
        db: Database session
        query: Search query string
        limit: Maximum number of results
    
    Returns:
        List of Client objects matching the query
    """
    search_term = f"%{query}%"
    query_lower = query.lower()
    
    # Search in nom, prenom, email, and telephone fields
    results = db.query(Client).filter(
        or_(
            Client.nom.ilike(search_term),
            Client.prenom.ilike(search_term),
            Client.email.ilike(search_term),
            Client.telephone.ilike(search_term)
        )
    ).order_by(Client.nom, Client.prenom).limit(limit * 2).all()
    
    # Filter results to also match full name if query contains multiple words
    if ' ' in query_lower:
        words = query_lower.split()
        filtered_results = []
        for client in results:
            full_name_lower = client.full_name.lower()
            # Check if all words appear in the full name
            if all(word in full_name_lower for word in words):
                filtered_results.append(client)
        results = filtered_results
    
    return results[:limit]


def search_products(db: Session, query: str, limit: int = 10) -> List[Product]:
    """
    Search products by name
    
    Args:
        db: Database session
        query: Search query string
        limit: Maximum number of results
    
    Returns:
        List of Product objects matching the query
    """
    search_term = f"%{query}%"
    return db.query(Product).filter(
        Product.nom.ilike(search_term)
    ).order_by(Product.nom).limit(limit).all()


def global_search(db: Session, query: str, limit_per_type: int = 5) -> Dict[str, List]:
    """
    Perform a global search across orders and clients
    
    Args:
        db: Database session
        query: Search query string
        limit_per_type: Maximum number of results per type
    
    Returns:
        Dictionary with keys 'orders', 'clients' containing lists of results
    """
    if not query or len(query.strip()) < 2:
        return {
            'orders': [],
            'clients': []
        }
    
    query = query.strip()
    
    return {
        'orders': search_orders(db, query, limit_per_type),
        'clients': search_clients(db, query, limit_per_type)
    }


def format_order_result(order: Order) -> str:
    """Format an order for display in search results"""
    status_icons = {
        'en_attente': '⏳',
        'paye': '💳',
        'imprime': '🖨️',
        'en_preparation': '📦',
        'livre': '✅',
        'annule': '❌'
    }
    icon = status_icons.get(order.statut.value, '📋')
    return f"{icon} {order.numero_commande} - {order.client.full_name} ({order.date_commande.strftime('%d/%m/%Y')})"


def format_client_result(client: Client) -> str:
    """Format a client for display in search results"""
    info_parts = [client.full_name]
    if client.email:
        info_parts.append(f"📧 {client.email}")
    if client.telephone:
        info_parts.append(f"📞 {client.telephone}")
    return " | ".join(info_parts)


def format_product_result(product: Product) -> str:
    """Format a product for display in search results"""
    status = "✅ Actif" if product.actif else "❌ Inactif"
    return f"{product.nom} - {status} ({product.prix_vente_base:.0f} ₪)"


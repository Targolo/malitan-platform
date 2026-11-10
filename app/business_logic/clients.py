"""
Client management business logic
Handles client CRUD operations
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from database.models import Client, Order, OrderStatus


def get_all_clients(db: Session, search_query: Optional[str] = None) -> List[Client]:
    """
    Get all clients, optionally filtered by search query
    
    Args:
        db: Database session
        search_query: Optional search query (searches in nom, prenom, email)
    
    Returns:
        List of Client objects
    """
    query = db.query(Client)
    
    if search_query:
        search_lower = search_query.lower()
        query = query.filter(
            (Client.nom.ilike(f"%{search_lower}%")) |
            (Client.prenom.ilike(f"%{search_lower}%")) |
            (Client.email.ilike(f"%{search_lower}%"))
        )
    
    # Trier par prénom puis nom (alphabétique, insensible à la casse)
    # Utiliser LOWER() pour un tri insensible à la casse
    from sqlalchemy import func
    return query.order_by(
        func.lower(Client.prenom).nulls_last(),
        func.lower(Client.nom),
        Client.prenom.nulls_last(),
        Client.nom
    ).all()


def get_client_by_id(db: Session, client_id: int) -> Optional[Client]:
    """
    Get a client by ID
    
    Args:
        db: Database session
        client_id: Client ID
    
    Returns:
        Client object or None
    """
    return db.query(Client).get(client_id)


def create_client(
    db: Session,
    nom: str,
    user_id: int,
    prenom: Optional[str] = None,
    email: Optional[str] = None,
    telephone: Optional[str] = None,
    provenance: Optional[str] = None,
    adresse: Optional[str] = None,
    notes: Optional[str] = None
) -> Client:
    """
    Create a new client
    
    Args:
        db: Database session
        nom: Last name (required)
        user_id: User creating the client
        prenom: First name (optional)
        email: Email (optional)
        telephone: Phone number (optional)
        provenance: Source/origin (optional)
        adresse: Address (optional)
        notes: Notes (optional)
    
    Returns:
        Created Client object
    
    Raises:
        ValueError: If nom is empty
    """
    if not nom:
        raise ValueError("Le nom est obligatoire")
    
    client = Client(
        nom=nom,
        prenom=prenom if prenom else None,
        email=email if email else None,
        telephone=telephone if telephone else None,
        provenance=provenance if provenance else None,
        adresse=adresse if adresse else None,
        notes=notes if notes else None,
        created_by=user_id
    )
    
    db.add(client)
    db.commit()
    db.refresh(client)
    
    return client


def update_client(
    db: Session,
    client_id: int,
    nom: Optional[str] = None,
    prenom: Optional[str] = None,
    email: Optional[str] = None,
    telephone: Optional[str] = None,
    provenance: Optional[str] = None,
    adresse: Optional[str] = None,
    notes: Optional[str] = None
) -> Client:
    """
    Update a client
    
    Args:
        db: Database session
        client_id: Client ID
        nom: Last name (optional)
        prenom: First name (optional)
        email: Email (optional)
        telephone: Phone number (optional)
        provenance: Source/origin (optional)
        adresse: Address (optional)
        notes: Notes (optional)
    
    Returns:
        Updated Client object
    
    Raises:
        ValueError: If client not found or nom is empty
    """
    client = db.query(Client).get(client_id)
    if not client:
        raise ValueError(f"Client {client_id} introuvable")
    
    if nom is not None:
        if not nom:
            raise ValueError("Le nom est obligatoire")
        client.nom = nom
    
    if prenom is not None:
        client.prenom = prenom if prenom else None
    
    if email is not None:
        client.email = email if email else None
    
    if telephone is not None:
        client.telephone = telephone if telephone else None
    
    if provenance is not None:
        client.provenance = provenance if provenance else None
    
    if adresse is not None:
        client.adresse = adresse if adresse else None
    
    if notes is not None:
        client.notes = notes if notes else None
    
    client.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(client)
    
    return client


def can_delete_client(db: Session, client_id: int):
    """
    Check if a client can be deleted (no active orders)
    
    Args:
        db: Database session
        client_id: Client ID
    
    Returns:
        Tuple of (can_delete: bool, active_orders_count: int)
        can_delete is True if client has no orders with status other than ANNULE
    """
    client = db.query(Client).get(client_id)
    if not client:
        return False, 0
    
    # Count orders with status other than ANNULE
    active_orders_count = db.query(Order).filter(
        Order.client_id == client_id,
        Order.statut != OrderStatus.ANNULE
    ).count()
    
    return active_orders_count == 0, active_orders_count


def delete_client(db: Session, client_id: int) -> bool:
    """
    Delete a client (only if no active orders)
    
    Args:
        db: Database session
        client_id: Client ID
    
    Returns:
        True if deleted, False if cannot delete
    
    Raises:
        ValueError: If client not found
    """
    client = db.query(Client).get(client_id)
    if not client:
        raise ValueError(f"Client {client_id} introuvable")
    
    # Check if can delete
    can_delete, active_orders_count = can_delete_client(db, client_id)
    if not can_delete:
        return False
    
    # Delete the client
    db.delete(client)
    db.commit()
    
    return True


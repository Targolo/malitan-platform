"""
Inventory management business logic
Handles filament stock tracking
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from database.models import (
    Color, StockMovement, StockMovementType, OrderLine
)


def add_stock(
    db: Session,
    color_id: int,
    quantite_grammes: float,
    user_id: int,
    observations: Optional[str] = None
) -> StockMovement:
    """
    Add filament stock (entry)
    
    Args:
        db: Database session
        color_id: Color ID
        quantite_grammes: Quantity in grams
        user_id: User adding stock
        observations: Optional notes
    
    Returns:
        Created StockMovement object
    """
    color = db.get(Color, color_id)
    if not color:
        raise ValueError(f"Couleur {color_id} introuvable")
    
    # Create movement
    movement = StockMovement(
        color_id=color_id,
        type=StockMovementType.ENTREE,
        quantite_grammes=quantite_grammes,
        date=datetime.utcnow(),
        observations=observations,
        created_by=user_id
    )
    db.add(movement)
    
    # Update color stock
    color.stock_grammes += quantite_grammes
    
    db.commit()
    db.refresh(movement)
    
    return movement


def deduct_filament_stock(
    db: Session,
    order_line_id: int,
    user_id: int
) -> StockMovement:
    """
    Deduct filament stock based on order line (automatic after fabrication)
    
    Args:
        db: Database session
        order_line_id: Order line ID
        user_id: User deducting stock
    
    Returns:
        Created StockMovement object
    """
    line = db.get(OrderLine, order_line_id)
    if not line:
        raise ValueError(f"Ligne de commande {order_line_id} introuvable")
    
    # Calculate total weight
    quantite_grammes = line.poids_total_grammes
    
    # Check stock availability
    color = line.color
    if color.stock_grammes < quantite_grammes:
        raise ValueError(
            f"Insufficient stock for {color.nom}: "
            f"need {quantite_grammes}g, have {color.stock_grammes}g"
        )
    
    # Create movement
    movement = StockMovement(
        color_id=line.color_id,
        type=StockMovementType.SORTIE,
        quantite_grammes=quantite_grammes,
        order_line_id=order_line_id,
        date=datetime.utcnow(),
        observations=f"Fabrication: {line.product.nom} x{line.quantite}",
        created_by=user_id
    )
    db.add(movement)
    
    # Update color stock
    color.stock_grammes -= quantite_grammes
    
    db.commit()
    db.refresh(movement)
    
    return movement


def get_low_stock_colors(db: Session) -> List[Color]:
    """
    Get colors with stock below alert threshold
    
    Args:
        db: Database session
    
    Returns:
        List of Color objects with low stock
    """
    return db.query(Color).filter(
        Color.actif == True,
        Color.stock_grammes < Color.seuil_alerte
    ).all()


def get_stock_movements(
    db: Session,
    color_id: Optional[int] = None,
    movement_type: Optional[StockMovementType] = None,
    limit: Optional[int] = None
) -> List[StockMovement]:
    """
    Get stock movements with optional filters
    
    Args:
        db: Database session
        color_id: Optional color filter
        movement_type: Optional type filter
        limit: Optional limit
    
    Returns:
        List of StockMovement objects
    """
    query = db.query(StockMovement).order_by(StockMovement.date.desc())
    
    if color_id:
        query = query.filter(StockMovement.color_id == color_id)
    
    if movement_type:
        query = query.filter(StockMovement.type == movement_type)
    
    if limit:
        query = query.limit(limit)
    
    return query.all()


def get_color_stock_value(db: Session, color_id: int) -> dict:
    """
    Calculate the monetary value of a color's stock
    
    Args:
        db: Database session
        color_id: Color ID
    
    Returns:
        dict with stock information and value
    """
    color = db.get(Color, color_id)
    if not color:
        raise ValueError(f"Couleur {color_id} introuvable")
    
    # Calculate value
    valeur_stock = (color.stock_grammes / 1000) * color.cout_par_kg
    
    return {
        'color': color,
        'stock_grammes': color.stock_grammes,
        'stock_kg': color.stock_kg,
        'cout_par_kg': color.cout_par_kg,
        'valeur_stock': round(valeur_stock, 2),
        'is_low_stock': color.is_low_stock
    }


def get_total_stock_value(db: Session) -> dict:
    """
    Calculate total value of all filament stock
    
    Args:
        db: Database session
    
    Returns:
        dict with total stock information
    """
    colors = db.query(Color).filter(Color.actif == True).all()
    
    total_grammes = sum(c.stock_grammes for c in colors)
    total_value = sum(
        (c.stock_grammes / 1000) * c.cout_par_kg for c in colors
    )
    
    low_stock_count = sum(1 for c in colors if c.is_low_stock)
    
    return {
        'nb_couleurs': len(colors),
        'total_grammes': round(total_grammes, 2),
        'total_kg': round(total_grammes / 1000, 2),
        'total_value': round(total_value, 2),
        'low_stock_count': low_stock_count,
        'colors': colors
    }


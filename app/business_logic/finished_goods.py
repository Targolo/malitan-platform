"""
Finished Goods Stock Management
Handles pre-printed products stock and movements
"""
from datetime import datetime
from typing import Optional, List, Dict
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_
from database.models import (
    FinishedGoodsStock, FinishedGoodsMovement, FinishedGoodsMovementType,
    Product, Color, OrderLine, FabricationStatus
)


def get_stock_by_product_color(db: Session, product_id: int, color_id: int) -> Optional[FinishedGoodsStock]:
    """
    Get stock for a specific product and color combination
    
    Args:
        db: Database session
        product_id: Product ID
        color_id: Color ID
        
    Returns:
        FinishedGoodsStock object or None if no stock entry exists
    """
    return db.query(FinishedGoodsStock).filter(
        and_(
            FinishedGoodsStock.product_id == product_id,
            FinishedGoodsStock.color_id == color_id
        )
    ).first()


def get_all_stock(db: Session, filter_available_only: bool = False) -> List[FinishedGoodsStock]:
    """
    Get all stock entries
    
    Args:
        db: Database session
        filter_available_only: If True, only return entries with quantity > 0
        
    Returns:
        List of FinishedGoodsStock objects
    """
    query = db.query(FinishedGoodsStock)\
        .join(Product)\
        .join(Color)\
        .options(
            joinedload(FinishedGoodsStock.product),
            joinedload(FinishedGoodsStock.color)
        )
    
    if filter_available_only:
        query = query.filter(FinishedGoodsStock.quantite_disponible > 0)
    
    return query.order_by(Product.nom, Color.nom).all()


def add_to_stock(
    db: Session,
    product_id: int,
    color_id: int,
    quantite: int,
    user_id: int,
    observations: Optional[str] = None
) -> FinishedGoodsStock:
    """
    Add pre-printed products to stock
    
    Args:
        db: Database session
        product_id: Product ID
        color_id: Color ID
        quantite: Quantity to add
        user_id: User performing the action
        observations: Optional notes
        
    Returns:
        Updated FinishedGoodsStock object
        
    Raises:
        ValueError: If quantity is invalid
    """
    if quantite <= 0:
        raise ValueError("La quantité doit être positive")
    
    # Get or create stock entry
    stock = get_stock_by_product_color(db, product_id, color_id)
    
    if stock is None:
        # Create new stock entry
        stock = FinishedGoodsStock(
            product_id=product_id,
            color_id=color_id,
            quantite_disponible=quantite
        )
        db.add(stock)
    else:
        # Update existing stock
        stock.quantite_disponible += quantite
        stock.updated_at = datetime.utcnow()
    
    # Create movement record
    movement = FinishedGoodsMovement(
        product_id=product_id,
        color_id=color_id,
        type=FinishedGoodsMovementType.ENTREE,
        quantite=quantite,
        observations=observations,
        created_by=user_id
    )
    db.add(movement)
    
    db.commit()
    db.refresh(stock)
    
    return stock


def use_from_stock(
    db: Session,
    order_line_id: int,
    user_id: int,
    observations: Optional[str] = None
) -> FinishedGoodsStock:
    """
    Use a product from stock for an order line
    Decrements stock by 1 and marks order line as IMPRIME
    
    Args:
        db: Database session
        order_line_id: OrderLine ID to fulfill from stock
        user_id: User performing the action
        observations: Optional notes
        
    Returns:
        Updated FinishedGoodsStock object
        
    Raises:
        ValueError: If order line not found, already printed, or insufficient stock
    """
    # Get order line
    order_line = db.query(OrderLine).get(order_line_id)
    if not order_line:
        raise ValueError("Ligne de commande introuvable")
    
    # Check if already printed
    if order_line.statut_fabrication in [FabricationStatus.IMPRIME, FabricationStatus.LIVRE_LEA]:
        raise ValueError("Cette ligne est déjà imprimée")
    
    # Check if quantity is 1 (we only handle individual items)
    if order_line.quantite != 1:
        raise ValueError("Cette ligne a une quantité différente de 1. Le stock ne gère que les objets unitaires.")
    
    # Get stock
    stock = get_stock_by_product_color(db, order_line.product_id, order_line.color_id)
    if not stock or stock.quantite_disponible <= 0:
        raise ValueError("Stock insuffisant pour ce produit/couleur")
    
    # Decrement stock
    stock.quantite_disponible -= 1
    stock.updated_at = datetime.utcnow()
    
    # Create movement record
    movement = FinishedGoodsMovement(
        product_id=order_line.product_id,
        color_id=order_line.color_id,
        type=FinishedGoodsMovementType.SORTIE,
        quantite=1,
        order_line_id=order_line_id,
        observations=observations,
        created_by=user_id
    )
    db.add(movement)
    
    # Mark order line as printed
    order_line.statut_fabrication = FabricationStatus.IMPRIME
    order_line.date_fin_impression = datetime.utcnow()
    order_line.updated_at = datetime.utcnow()
    
    db.commit()
    
    # Check if all lines are printed and update order status
    from business_logic.orders import check_order_printed
    check_order_printed(db, order_line.order_id)
    
    # No need to refresh stock - we already have the updated object in memory
    
    return stock


def get_stock_movements(
    db: Session,
    product_id: Optional[int] = None,
    color_id: Optional[int] = None,
    limit: int = 50
) -> List[FinishedGoodsMovement]:
    """
    Get stock movement history
    
    Args:
        db: Database session
        product_id: Optional filter by product
        color_id: Optional filter by color
        limit: Maximum number of records to return
        
    Returns:
        List of FinishedGoodsMovement objects
    """
    query = db.query(FinishedGoodsMovement)\
        .options(
            joinedload(FinishedGoodsMovement.product),
            joinedload(FinishedGoodsMovement.color),
            joinedload(FinishedGoodsMovement.order_line).joinedload(OrderLine.order)
        )
    
    if product_id:
        query = query.filter(FinishedGoodsMovement.product_id == product_id)
    
    if color_id:
        query = query.filter(FinishedGoodsMovement.color_id == color_id)
    
    return query.order_by(FinishedGoodsMovement.date.desc()).limit(limit).all()


def get_stock_summary(db: Session) -> Dict[str, any]:
    """
    Get summary statistics about finished goods stock
    
    Args:
        db: Database session
        
    Returns:
        Dictionary with summary statistics
    """
    all_stock = get_all_stock(db)
    available_stock = [s for s in all_stock if s.quantite_disponible > 0]
    
    total_products = len(all_stock)
    total_quantity = sum(s.quantite_disponible for s in all_stock)
    products_with_stock = len(available_stock)
    
    # Get recent movements
    recent_movements = get_stock_movements(db, limit=10)
    recent_entrees = [m for m in recent_movements if m.type == FinishedGoodsMovementType.ENTREE]
    recent_sorties = [m for m in recent_movements if m.type == FinishedGoodsMovementType.SORTIE]
    
    return {
        'total_products': total_products,
        'total_quantity': total_quantity,
        'products_with_stock': products_with_stock,
        'recent_entrees_count': len(recent_entrees),
        'recent_sorties_count': len(recent_sorties)
    }


def get_production_needs_by_product_color(db: Session, order_statuses=None) -> Dict[tuple, int]:
    """
    Objects not yet printed, grouped by (product_id, color_id).

    Args:
        db: Database session
        order_statuses: Order statuses to count. Defaults to paid orders, the same
            scope as the production queue, so unpaid orders never inflate the need.

    Returns:
        Dictionary mapping (product_id, color_id) tuples to total quantity needed
    """
    from database.models import Order
    from business_logic.dashboard import PRODUCTION_ORDER_STATUSES, TO_PRINT_STATUSES

    lines_to_print = db.query(OrderLine)\
        .join(Order)\
        .join(Product)\
        .filter(
            OrderLine.statut_fabrication.in_(TO_PRINT_STATUSES),
            Order.statut.in_(order_statuses or PRODUCTION_ORDER_STATUSES),
            Product.gestion_stock == True,  # Only products with stock management
            Product.actif == True  # Only active products
        )\
        .all()

    needs = {}
    for line in lines_to_print:
        key = (line.product_id, line.color_id)
        needs[key] = needs.get(key, 0) + line.quantite

    return needs


def get_all_stock_with_needs(db: Session, filter_available_only: bool = False, 
                             product_id: Optional[int] = None, 
                             color_id: Optional[int] = None) -> List[FinishedGoodsStock]:
    """
    Get all stock entries, including entries with production needs even if stock is 0.
    Each entry gets 'besoin_production' (paid orders left to print) and
    'besoin_non_paye' (unpaid orders, kept separate since they may never be printed).
    
    Args:
        db: Database session
        filter_available_only: If True, only return entries with quantity > 0 OR with any need
        product_id: Optional filter by product
        color_id: Optional filter by color
        
    Returns:
        List of FinishedGoodsStock objects with both need attributes
    """
    from database.models import OrderStatus

    needs = get_production_needs_by_product_color(db)
    unpaid_needs = get_production_needs_by_product_color(db, [OrderStatus.EN_ATTENTE])
    
    # Get existing stock entries (with relationships loaded)
    existing_stock = get_all_stock(db, filter_available_only=False)
    
    # Create a map of existing stock by (product_id, color_id)
    stock_map = {(s.product_id, s.color_id): s for s in existing_stock}
    
    for stock in existing_stock:
        key = (stock.product_id, stock.color_id)
        stock.besoin_production = needs.get(key, 0)
        stock.besoin_non_paye = unpaid_needs.get(key, 0)

    # Create virtual entries for products/colors with needs but no stock
    for (prod_id, col_id) in set(needs) | set(unpaid_needs):
        if (prod_id, col_id) not in stock_map:
            # Get product and color to ensure they exist and are loaded
            product = db.get(Product, prod_id)
            color = db.get(Color, col_id)
            
            if product and color:
                # Create a virtual stock entry with 0 quantity
                virtual_stock = FinishedGoodsStock(
                    product_id=prod_id,
                    color_id=col_id,
                    quantite_disponible=0
                )
                # Set relationships directly (they're already loaded)
                virtual_stock.product = product
                virtual_stock.color = color
                virtual_stock.besoin_production = needs.get((prod_id, col_id), 0)
                virtual_stock.besoin_non_paye = unpaid_needs.get((prod_id, col_id), 0)
                existing_stock.append(virtual_stock)
    
    # Apply filters
    if product_id:
        existing_stock = [s for s in existing_stock if s.product_id == product_id]
    if color_id:
        existing_stock = [s for s in existing_stock if s.color_id == color_id]
    
    # Apply filter_available_only if needed
    if filter_available_only:
        existing_stock = [
            s for s in existing_stock 
            if s.quantite_disponible > 0 or s.besoin_production > 0 or s.besoin_non_paye > 0
        ]
    
    # Sort by product name and color name
    existing_stock.sort(key=lambda s: (
        s.product.nom if s.product else '',
        s.color.nom if s.color else ''
    ))
    
    return existing_stock


def adjust_stock(
    db: Session,
    product_id: int,
    color_id: int,
    new_quantity: int,
    user_id: int,
    observations: Optional[str] = None
) -> FinishedGoodsStock:
    """
    Adjust stock to a specific quantity (inventory correction)
    
    Args:
        db: Database session
        product_id: Product ID
        color_id: Color ID
        new_quantity: New quantity to set
        user_id: User performing the action
        observations: Reason for adjustment
        
    Returns:
        Updated FinishedGoodsStock object
        
    Raises:
        ValueError: If new_quantity is negative
    """
    if new_quantity < 0:
        raise ValueError("La quantité ne peut pas être négative")
    
    # Get stock
    stock = get_stock_by_product_color(db, product_id, color_id)
    
    if stock is None:
        # Create new stock entry
        if new_quantity > 0:
            stock = FinishedGoodsStock(
                product_id=product_id,
                color_id=color_id,
                quantite_disponible=new_quantity
            )
            db.add(stock)
            
            # Create ENTREE movement
            movement = FinishedGoodsMovement(
                product_id=product_id,
                color_id=color_id,
                type=FinishedGoodsMovementType.ENTREE,
                quantite=new_quantity,
                observations=f"Ajustement initial: {observations}" if observations else "Ajustement initial",
                created_by=user_id
            )
            db.add(movement)
        else:
            raise ValueError("Impossible de créer un stock avec une quantité de 0")
    else:
        # Calculate difference
        difference = new_quantity - stock.quantite_disponible
        
        if difference != 0:
            # Update stock
            stock.quantite_disponible = new_quantity
            stock.updated_at = datetime.utcnow()
            
            # Create movement record
            movement_type = FinishedGoodsMovementType.ENTREE if difference > 0 else FinishedGoodsMovementType.SORTIE
            movement = FinishedGoodsMovement(
                product_id=product_id,
                color_id=color_id,
                type=movement_type,
                quantite=abs(difference),
                observations=f"Ajustement ({difference:+d}): {observations}" if observations else f"Ajustement ({difference:+d})",
                created_by=user_id
            )
            db.add(movement)
    
    db.commit()
    db.refresh(stock)
    
    return stock


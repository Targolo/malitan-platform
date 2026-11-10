"""
Production management business logic
Handles fabrication workflow and printer assignment
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session, joinedload
from database.models import (
    OrderLine, FabricationStatus, OrderStatus,
    Printer, PrinterStatus, Order
)


def get_production_queue(db: Session) -> List[OrderLine]:
    """
    Get all order lines ready for production
    (Order status PAYE or IMPRIME and fabrication status not yet LIVRE_LEA)
    
    Args:
        db: Database session
    
    Returns:
        List of OrderLine objects
    """
    return db.query(OrderLine)\
        .join(Order)\
        .options(
            joinedload(OrderLine.product),
            joinedload(OrderLine.color),
            joinedload(OrderLine.order).joinedload(Order.client),
            joinedload(OrderLine.printer)
        )\
        .filter(
            Order.statut.in_([
                OrderStatus.PAYE,
                OrderStatus.IMPRIME,
                OrderStatus.EN_PREPARATION
            ]),
            OrderLine.statut_fabrication != FabricationStatus.LIVRE_LEA
        )\
        .order_by(Order.date_commande.asc())\
        .all()


def get_lines_by_fabrication_status(
    db: Session,
    statut: FabricationStatus
) -> List[OrderLine]:
    """
    Get order lines by fabrication status
    
    Args:
        db: Database session
        statut: Fabrication status
    
    Returns:
        List of OrderLine objects
    """
    return db.query(OrderLine)\
        .options(
            joinedload(OrderLine.product),
            joinedload(OrderLine.color),
            joinedload(OrderLine.order).joinedload(Order.client),
            joinedload(OrderLine.printer)
        )\
        .filter(
            OrderLine.statut_fabrication == statut
        )\
        .all()


def assign_printer(
    db: Session,
    order_line_id: int,
    printer_id: int
) -> OrderLine:
    """
    Assign a printer to an order line
    
    Args:
        db: Database session
        order_line_id: Order line ID
        printer_id: Printer ID
    
    Returns:
        Updated OrderLine object
    """
    line = db.get(OrderLine, order_line_id)
    if not line:
        raise ValueError(f"Ligne de commande {order_line_id} introuvable")
    
    printer = db.get(Printer, printer_id)
    if not printer:
        raise ValueError(f"Imprimante {printer_id} introuvable")
    
    line.printer_id = printer_id
    
    db.commit()
    db.refresh(line)
    
    return line


def start_fabrication(
    db: Session,
    order_line_id: int,
    printer_id: Optional[int] = None
) -> OrderLine:
    """
    Start fabrication for an order line
    
    Args:
        db: Database session
        order_line_id: Order line ID
        printer_id: Optional printer ID (if not already assigned)
    
    Returns:
        Updated OrderLine object
    
    Raises:
        ValueError: If printer is already in use
    """
    line = db.get(OrderLine, order_line_id)
    if not line:
        raise ValueError(f"Ligne de commande {order_line_id} introuvable")
    
    # Assign printer if provided
    if printer_id:
        line.printer_id = printer_id
    
    if not line.printer_id:
        raise ValueError("Aucune imprimante assignée à cette ligne")
    
    # CHECK: Verify printer is not already in use
    existing_job = db.query(OrderLine).filter(
        OrderLine.printer_id == line.printer_id,
        OrderLine.statut_fabrication == FabricationStatus.EN_COURS,
        OrderLine.id != order_line_id  # Exclude current line
    ).first()
    
    if existing_job:
        printer = db.get(Printer, line.printer_id)
        raise ValueError(
            f"L'imprimante '{printer.nom}' est déjà en cours d'impression "
            f"(Commande {existing_job.order.numero_commande} - {existing_job.product.nom})"
        )
    
    # Update printer status
    printer = db.get(Printer, line.printer_id)
    printer.statut = PrinterStatus.EN_COURS
    
    # Update line status
    line.statut_fabrication = FabricationStatus.EN_COURS
    line.date_debut_impression = datetime.utcnow()
    
    db.commit()
    db.refresh(line)
    
    return line


def complete_fabrication(
    db: Session,
    order_line_id: int,
    user_id: int
) -> OrderLine:
    """
    Mark fabrication as complete (printed)
    Automatically deducts filament from stock
    
    Args:
        db: Database session
        order_line_id: Order line ID
        user_id: User completing the fabrication (David)
    
    Returns:
        Updated OrderLine object
    """
    line = db.get(OrderLine, order_line_id)
    if not line:
        raise ValueError(f"Ligne de commande {order_line_id} introuvable")
    
    # Update line status to IMPRIME
    line.statut_fabrication = FabricationStatus.IMPRIME
    line.date_fin_impression = datetime.utcnow()
    
    # Update printer status to available
    if line.printer:
        line.printer.statut = PrinterStatus.DISPONIBLE
    
    # Deduct filament from stock
    try:
        from business_logic.inventory import deduct_filament_stock
        deduct_filament_stock(
            db=db,
            order_line_id=order_line_id,
            user_id=user_id
        )
    except Exception as e:
        print(f"Warning: Could not deduct filament stock: {e}")
        # Don't fail the whole operation if stock deduction fails
    
    db.commit()
    db.refresh(line)
    
    # Check if all lines are printed and update order status
    from business_logic.orders import check_order_printed
    check_order_printed(db, line.order_id)
    
    return line


def deliver_to_lea(
    db: Session,
    order_line_id: int
) -> OrderLine:
    """
    Mark order line as delivered to Lea
    
    Args:
        db: Database session
        order_line_id: Order line ID
    
    Returns:
        Updated OrderLine object
    """
    line = db.get(OrderLine, order_line_id)
    if not line:
        raise ValueError(f"Ligne de commande {order_line_id} introuvable")
    
    line.statut_fabrication = FabricationStatus.LIVRE_LEA
    line.date_livraison_lea = datetime.utcnow()
    
    db.commit()
    db.refresh(line)

    # Check if entire order is complete
    from business_logic.orders import check_order_completion
    check_order_completion(db, line.order_id)

    # Telegram: notify Lea
    try:
        from utils.notifications import notify_telegram_livre_lea
        notify_telegram_livre_lea(line, db)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Telegram LIVRE_LEA notification failed: {e}")

    return line


def get_printer_workload(db: Session, printer_id: int) -> dict:
    """
    Get current workload for a printer
    
    Args:
        db: Database session
        printer_id: Printer ID
    
    Returns:
        dict with workload information
    """
    printer = db.get(Printer, printer_id)
    if not printer:
        raise ValueError(f"Imprimante {printer_id} introuvable")
    
    # Count lines by status
    file_attente = db.query(OrderLine).filter(
        OrderLine.printer_id == printer_id,
        OrderLine.statut_fabrication == FabricationStatus.FILE_ATTENTE
    ).count()
    
    en_cours = db.query(OrderLine).filter(
        OrderLine.printer_id == printer_id,
        OrderLine.statut_fabrication == FabricationStatus.EN_COURS
    ).count()
    
    # Get current job (en cours)
    current_job = db.query(OrderLine)\
        .options(
            joinedload(OrderLine.product),
            joinedload(OrderLine.color),
            joinedload(OrderLine.order)
        )\
        .filter(
            OrderLine.printer_id == printer_id,
            OrderLine.statut_fabrication == FabricationStatus.EN_COURS
        )\
        .first()
    
    # Calculate total pending time
    pending_lines = db.query(OrderLine)\
        .options(joinedload(OrderLine.product))\
        .filter(
            OrderLine.printer_id == printer_id,
            OrderLine.statut_fabrication.in_([
                FabricationStatus.FILE_ATTENTE,
                FabricationStatus.EN_COURS
            ])
        )\
        .all()
    
    total_time_minutes = sum(
        line.temps_impression_total_minutes for line in pending_lines
    )
    
    # Convert printer to serializable dict
    printer_dict = {
        'id': printer.id,
        'nom': printer.nom,
        'statut': printer.statut.value if hasattr(printer.statut, 'value') else str(printer.statut),
        'modele': printer.modele,
        'observations': printer.observations
    }
    
    # Convert current_job to serializable dict if exists
    current_job_dict = None
    if current_job:
        current_job_dict = {
            'id': current_job.id,
            'product_nom': current_job.product.nom if current_job.product else None,
            'color_nom': current_job.color.nom if current_job.color else None,
            'color_hex': current_job.color.code_hex if current_job.color else None,
            'quantite': current_job.quantite,
            'personnalisation': current_job.personnalisation,
            'temps_impression_total_minutes': current_job.temps_impression_total_minutes,
            'poids_total_grammes': current_job.poids_total_grammes,
            'order_numero': current_job.order.numero_commande if current_job.order else None,
            'date_debut_impression': current_job.date_debut_impression.isoformat() if current_job.date_debut_impression else None,
        }
    
    return {
        'printer': printer_dict,
        'file_attente': file_attente,
        'en_cours': en_cours,
        'current_job': current_job_dict,
        'total_time_minutes': total_time_minutes,
        'total_time_hours': round(total_time_minutes / 60, 2)
    }


def get_all_printers_workload(db: Session) -> List[dict]:
    """
    Get workload for all printers
    
    Args:
        db: Database session
    
    Returns:
        List of workload dicts
    """
    printers = db.query(Printer).all()
    return [get_printer_workload(db, p.id) for p in printers]


def get_personalized_items(db: Session) -> List[OrderLine]:
    """
    Get all order lines with personalization that are pending printing
    (Order status PAYE or IMPRIME and fabrication status not yet IMPRIME)
    
    Args:
        db: Database session
    
    Returns:
        List of OrderLine objects with personalization, ordered by color
    """
    return db.query(OrderLine)\
        .join(Order)\
        .options(
            joinedload(OrderLine.product),
            joinedload(OrderLine.color),
            joinedload(OrderLine.order).joinedload(Order.client),
            joinedload(OrderLine.printer)
        )\
        .filter(
            Order.statut.in_([OrderStatus.PAYE, OrderStatus.IMPRIME]),
            OrderLine.statut_fabrication != FabricationStatus.LIVRE_LEA,
            OrderLine.statut_fabrication != FabricationStatus.IMPRIME,
            OrderLine.personnalisation.isnot(None),
            OrderLine.personnalisation != ''
        )\
        .order_by(OrderLine.color_id.asc(), Order.date_commande.asc())\
        .all()


def set_fabrication_status_to_file_attente(
    db: Session,
    order_line_id: int
) -> OrderLine:
    """
    Set fabrication status to FILE_ATTENTE and free the printer
    
    Args:
        db: Database session
        order_line_id: Order line ID
    
    Returns:
        Updated OrderLine object
    
    Raises:
        ValueError: If order line not found
    """
    line = db.get(OrderLine, order_line_id)
    if not line:
        raise ValueError(f"Ligne de commande {order_line_id} introuvable")
    
    # Reset fabrication status
    line.statut_fabrication = FabricationStatus.FILE_ATTENTE
    
    # Free printer if assigned
    if line.printer_id:
        printer = db.get(Printer, line.printer_id)
        if printer:
            printer.statut = PrinterStatus.DISPONIBLE
    
    # Reset printer assignment and dates
    line.printer_id = None
    line.date_debut_impression = None
    
    db.commit()
    db.refresh(line)
    
    return line


def set_fabrication_status_to_prochain(
    db: Session,
    order_line_id: int
) -> OrderLine:
    """
    Set fabrication status to PROCHAIN (prioritize)
    
    Args:
        db: Database session
        order_line_id: Order line ID
    
    Returns:
        Updated OrderLine object
    
    Raises:
        ValueError: If order line not found
    """
    line = db.get(OrderLine, order_line_id)
    if not line:
        raise ValueError(f"Ligne de commande {order_line_id} introuvable")
    
    line.statut_fabrication = FabricationStatus.PROCHAIN
    
    db.commit()
    db.refresh(line)
    
    return line


def set_fabrication_status_to_file_attente_from_prochain(
    db: Session,
    order_line_id: int
) -> OrderLine:
    """
    Set fabrication status from PROCHAIN back to FILE_ATTENTE (unprioritize)
    
    Args:
        db: Database session
        order_line_id: Order line ID
    
    Returns:
        Updated OrderLine object
    
    Raises:
        ValueError: If order line not found
    """
    line = db.get(OrderLine, order_line_id)
    if not line:
        raise ValueError(f"Ligne de commande {order_line_id} introuvable")
    
    line.statut_fabrication = FabricationStatus.FILE_ATTENTE
    
    db.commit()
    db.refresh(line)
    
    return line


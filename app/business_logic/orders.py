"""
Order management business logic
Handles order creation, updates, and workflow
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from database.models import (
    Order, OrderLine, OrderStatus, FabricationStatus,
    Client, Product, Color, DeliveryMode
)
from business_logic.financial import calculate_line_financials
from config.settings import ENABLE_PACKAGING_EMAILS, TELEGRAM_ENABLED


def generate_order_number(db: Session) -> str:
    """
    Generate a unique order number
    Format: CMD-YYYYMMDD-HHMMSS, suffixed -2, -3... when that second is already taken
    """
    base = datetime.now().strftime('CMD-%Y%m%d-%H%M%S')
    taken = {
        n for (n,) in db.query(Order.numero_commande).filter(Order.numero_commande.like(f'{base}%'))
    }
    if base not in taken:
        return base
    suffix = 2
    while f'{base}-{suffix}' in taken:
        suffix += 1
    return f'{base}-{suffix}'


def create_order(
    db: Session,
    client_id: int,
    user_id: int,
    mode_livraison: DeliveryMode = DeliveryMode.LIVRAISON,
    date_livraison_prevue: Optional[datetime] = None,
    adresse_livraison: Optional[str] = None,
    observations: Optional[str] = None,
    statut: OrderStatus = OrderStatus.EN_ATTENTE,
    skip_packaging: bool = False
) -> Order:
    """
    Create a new order
    
    Args:
        db: Database session
        client_id: Client ID
        user_id: User creating the order
        mode_livraison: Delivery mode
        date_livraison_prevue: Expected delivery date
        adresse_livraison: Delivery address (if delivery mode)
        observations: Optional notes
        statut: Order status (default: EN_ATTENTE)
        skip_packaging: Whether the order should bypass the packaging step
    
    Returns:
        Created Order object

    Raises:
        ValueError: if the client does not exist (SQLite does not enforce the foreign key)
    """
    if db.get(Client, client_id) is None:
        raise ValueError('Client introuvable')

    # Another request can take the same number between the check and the commit: retry
    for attempt in range(3):
        order = Order(
            numero_commande=generate_order_number(db),
            client_id=client_id,
            date_commande=datetime.utcnow(),
            date_livraison_prevue=date_livraison_prevue,
            statut=statut,
            mode_livraison=mode_livraison,
            adresse_livraison=adresse_livraison,
            observations=observations,
            ne_pas_emballer=skip_packaging,
            created_by=user_id,
            updated_by=user_id
        )
        db.add(order)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise
    db.refresh(order)
    
    return order


def add_order_line(
    db: Session,
    order_id: int,
    product_id: int,
    color_id: int,
    quantite: int,
    prix_vente_unitaire: Optional[float] = None,
    remise_pourcent: float = 0,
    personnalisation: Optional[str] = None,
    observations: Optional[str] = None
) -> OrderLine:
    """
    Add a line to an order
    
    Args:
        db: Database session
        order_id: Order ID
        product_id: Product ID
        color_id: Color ID
        quantite: Quantity
        prix_vente_unitaire: Unit selling price (if None, use product base price)
        remise_pourcent: Discount percentage
        personnalisation: Optional personalization text (required if product is personalisable)
        observations: Optional notes
    
    Returns:
        Created OrderLine object
    """
    # Get product
    product = db.get(Product, product_id)
    if not product:
        raise ValueError(f"Produit {product_id} introuvable")
    
    # If product is personalisable, personnalisation is required and quantity limited to 1
    if product.personalisable:
        if not personnalisation:
            raise ValueError("La personnalisation est obligatoire pour ce produit")
        if quantite != 1:
            raise ValueError("La quantité doit être égale à 1 pour un produit personnalisable")
    
    # Use base price if not specified
    if prix_vente_unitaire is None:
        prix_vente_unitaire = product.prix_vente_base
    
    # Calculate financials
    financials = calculate_line_financials(
        product=product,
        prix_vente_unitaire=prix_vente_unitaire,
        remise_pourcent=remise_pourcent,
        quantite=quantite
    )
    
    # Create order line
    order_line = OrderLine(
        order_id=order_id,
        product_id=product_id,
        color_id=color_id,
        quantite=quantite,
        prix_vente_unitaire=financials['prix_vente_unitaire'],
        remise_pourcent=financials['remise_pourcent'],
        prix_vente_final=financials['prix_vente_final'],
        benefice_calcule=financials['benefice_calcule'],
        montant_a_reverser_david=financials['montant_a_reverser_david'],
        personnalisation=personnalisation,
        observations=observations,
        statut_fabrication=FabricationStatus.FILE_ATTENTE
    )
    
    db.add(order_line)
    db.commit()
    db.refresh(order_line)
    
    return order_line


def update_order_status(
    db: Session,
    order_id: int,
    new_status: OrderStatus,
    user_id: int
) -> Order:
    """
    Update order status
    
    Args:
        db: Database session
        order_id: Order ID
        new_status: New status
        user_id: User making the change
    
    Returns:
        Updated Order object
    """
    order = db.get(Order, order_id)
    if not order:
        raise ValueError(f"Commande {order_id} introuvable")
    
    old_status = order.statut
    if new_status == OrderStatus.LIVRE and old_status != OrderStatus.PRET_LIVRAISON:
        raise ValueError(
            "Une commande doit d'abord être « Prêt à livrer » (bouton « À livrer ») avant d'être marquée livrée"
        )
    if old_status in (OrderStatus.LIVRE, OrderStatus.ANNULE) and new_status != old_status:
        raise ValueError(f"Cette commande est déjà {'livrée' if old_status == OrderStatus.LIVRE else 'annulée'}")
    order.statut = new_status
    if new_status == OrderStatus.PRET_LIVRAISON:
        order.date_pret_livraison = order.date_pret_livraison or datetime.utcnow()
    order.updated_by = user_id
    order.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(order)
    
    # Send notifications based on status change
    try:
        from utils.notifications import notify_order_ready, notify_telegram_order_paid, notify_telegram_pret_livraison

        # Email: commande prête à envoyer (optionnel, OFF par défaut)
        if (
            new_status == OrderStatus.PRET_LIVRAISON
            and old_status != OrderStatus.PRET_LIVRAISON
            and ENABLE_PACKAGING_EMAILS
        ):
            notify_order_ready(order)

        # Telegram: David est notifié quand une commande est payée
        if new_status == OrderStatus.PAYE and old_status != OrderStatus.PAYE:
            notify_telegram_order_paid(order, db)

        # Telegram: Lea est notifiée quand une commande est prête à livrer
        if new_status == OrderStatus.PRET_LIVRAISON and old_status != OrderStatus.PRET_LIVRAISON:
            notify_telegram_pret_livraison(order, db)

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to send notification: {e}")
    
    return order


def get_orders_by_status(
    db: Session,
    statut: Optional[OrderStatus] = None,
    limit: Optional[int] = None
) -> List[Order]:
    """
    Get orders filtered by status
    
    Args:
        db: Database session
        statut: Optional status filter
        limit: Optional limit
    
    Returns:
        List of Order objects
    """
    query = db.query(Order)\
        .options(
            joinedload(Order.client),
            joinedload(Order.order_lines).joinedload(OrderLine.product),
            joinedload(Order.order_lines).joinedload(OrderLine.color)
        )\
        .order_by(Order.date_commande.desc())
    
    if statut:
        query = query.filter(Order.statut == statut)
    
    if limit:
        query = query.limit(limit)
    
    return query.all()


def get_client_orders(db: Session, client_id: int) -> List[Order]:
    """
    Get all orders for a specific client
    
    Args:
        db: Database session
        client_id: Client ID
    
    Returns:
        List of Order objects
    """
    return db.query(Order)\
        .options(
            joinedload(Order.client),
            joinedload(Order.order_lines).joinedload(OrderLine.product),
            joinedload(Order.order_lines).joinedload(OrderLine.color)
        )\
        .filter(
            Order.client_id == client_id
        )\
        .order_by(Order.date_commande.desc())\
        .all()


def check_order_printed(db: Session, order_id: int) -> bool:
    """
    Check if all order lines are printed (IMPRIME or LIVRE_LEA legacy)
    If yes, update order status to EN_PREPARATION (or PRET_LIVRAISON when packaging is skipped)
    
    Args:
        db: Database session
        order_id: Order ID
    
    Returns:
        True if all lines are printed
    """
    order = db.get(Order, order_id)
    if not order:
        return False
    
    # Check if all lines are printed (IMPRIME or LIVRE_LEA)
    all_printed = all(
        line.statut_fabrication in [FabricationStatus.IMPRIME, FabricationStatus.LIVRE_LEA]
        for line in order.order_lines
    )
    
    if all_printed and order.statut in [
        OrderStatus.PAYE,
        OrderStatus.IMPRIME,
        OrderStatus.EN_PREPARATION
    ]:
        # Nouvelle logique : TOUTES les commandes passent par EN_PREPARATION
        # (même celles ne_pas_emballer, qui deviennent "A transférer")
        target_status = OrderStatus.EN_PREPARATION
        
        if order.statut != target_status:
            order.statut = target_status
            # On ne met PAS date_pret_livraison ici, c'est fait au clic bouton
            order.updated_at = datetime.utcnow()
        db.commit()
    
    return all_printed


def check_order_completion(db: Session, order_id: int) -> bool:
    """
    Legacy helper kept for backward compatibility with l'ancienne UI Streamlit.
    Simply proxies vers check_order_printed (nouvelles règles).
    """
    return check_order_printed(db, order_id)


def cancel_order(
    db: Session,
    order_id: int,
    user_id: int,
    reason: Optional[str] = None
) -> Order:
    """
    Annule une commande tant qu'elle n'est pas livrée.

    - La commande passe en ANNULE (elle reste en base, exclue des stats et des files).
    - Les objets déjà imprimés (ou pris dans le stock) retournent au stock produits finis.
    - Une impression en cours est remise en file d'attente et l'imprimante est libérée.
    - Le motif (facultatif) est ajouté aux observations avec la date et l'auteur.

    Raises:
        ValueError: commande introuvable, déjà livrée ou déjà annulée
    """
    from database.models import (
        FinishedGoodsStock, FinishedGoodsMovement, FinishedGoodsMovementType,
        Printer, PrinterStatus, User
    )

    order = db.get(Order, order_id)
    if not order:
        raise ValueError(f"Commande {order_id} introuvable")
    if order.statut == OrderStatus.LIVRE:
        raise ValueError("Impossible d'annuler une commande déjà livrée")
    if order.statut == OrderStatus.ANNULE:
        raise ValueError("Cette commande est déjà annulée")

    now = datetime.utcnow()
    returned_to_stock = 0
    for line in order.order_lines:
        if line.statut_fabrication in (FabricationStatus.IMPRIME, FabricationStatus.LIVRE_LEA):
            if line.product_id and line.color_id and (line.quantite or 0) > 0:
                stock = db.query(FinishedGoodsStock).filter(
                    FinishedGoodsStock.product_id == line.product_id,
                    FinishedGoodsStock.color_id == line.color_id
                ).first()
                if stock is None:
                    stock = FinishedGoodsStock(
                        product_id=line.product_id, color_id=line.color_id, quantite_disponible=0
                    )
                    db.add(stock)
                stock.quantite_disponible = (stock.quantite_disponible or 0) + line.quantite
                stock.updated_at = now
                db.add(FinishedGoodsMovement(
                    product_id=line.product_id,
                    color_id=line.color_id,
                    type=FinishedGoodsMovementType.ENTREE,
                    quantite=line.quantite,
                    order_line_id=line.id,
                    observations=f"Annulation {order.numero_commande}",
                    created_by=user_id
                ))
                returned_to_stock += line.quantite
        elif line.statut_fabrication == FabricationStatus.EN_COURS:
            printer_id = line.printer_id
            line.statut_fabrication = FabricationStatus.FILE_ATTENTE
            line.date_debut_impression = None
            line.updated_at = now
            if printer_id:
                other_job = db.query(OrderLine).filter(
                    OrderLine.printer_id == printer_id,
                    OrderLine.statut_fabrication == FabricationStatus.EN_COURS,
                    OrderLine.id != line.id
                ).first()
                printer = db.get(Printer, printer_id)
                if printer and not other_job:
                    printer.statut = PrinterStatus.DISPONIBLE

    user = db.get(User, user_id) if user_id else None
    author = (user.full_name or user.username) if user else 'inconnu'
    note = f"[Annulée le {now.strftime('%d/%m/%Y')} par {author}]"
    if reason and reason.strip():
        note += f" {reason.strip()}"
    order.observations = f"{order.observations}\n{note}" if order.observations else note

    order.statut = OrderStatus.ANNULE
    order.updated_by = user_id
    order.updated_at = now
    db.commit()
    db.refresh(order)
    order.returned_to_stock = returned_to_stock
    return order


# Distinguishes "date not sent" (keep it) from None (clear it) in update_order_details
_UNCHANGED = object()


def update_order_details(
    db: Session,
    order_id: int,
    user_id: int,
    mode_livraison: Optional[DeliveryMode] = None,
    date_livraison_prevue: Optional[datetime] = _UNCHANGED,
    adresse_livraison: Optional[str] = None,
    observations: Optional[str] = None
) -> Order:
    """
    Update order details (delivery mode, date, address, observations)
    Can be done at any status
    
    Args:
        db: Database session
        order_id: Order ID
        user_id: User making the change
        mode_livraison: New delivery mode (optional)
        date_livraison_prevue: New delivery date; None clears it, omitted keeps it
        adresse_livraison: New delivery address (optional)
        observations: New observations (optional)
    
    Returns:
        Updated Order object
    """
    order = db.get(Order, order_id)
    if not order:
        raise ValueError(f"Commande {order_id} introuvable")
    
    # Update fields if provided
    if mode_livraison is not None:
        order.mode_livraison = mode_livraison
    
    if date_livraison_prevue is not _UNCHANGED:
        order.date_livraison_prevue = date_livraison_prevue
    
    if adresse_livraison is not None:
        order.adresse_livraison = adresse_livraison
    
    if observations is not None:
        order.observations = observations
    
    order.updated_by = user_id
    order.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(order)
    
    return order


def update_order_line_observations(
    db: Session,
    order_line_id: int,
    observations: Optional[str]
) -> OrderLine:
    """
    Update observations for an order line
    
    Args:
        db: Database session
        order_line_id: Order line ID
        observations: New observations (can be None to clear)
    
    Returns:
        Updated OrderLine object
    """
    order_line = db.get(OrderLine, order_line_id)
    if not order_line:
        raise ValueError(f"Ligne de commande {order_line_id} introuvable")
    
    order_line.observations = observations
    order_line.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(order_line)
    
    return order_line


def update_order_line_personnalisation(
    db: Session,
    order_line_id: int,
    personnalisation: Optional[str]
) -> OrderLine:
    """
    Update personalization for an order line
    
    Args:
        db: Database session
        order_line_id: Order line ID
        personnalisation: New personalization text (can be None to clear)
    
    Returns:
        Updated OrderLine object
    
    Raises:
        ValueError: If product is personalisable and personnalisation is empty
    """
    order_line = db.get(OrderLine, order_line_id)
    if not order_line:
        raise ValueError(f"Ligne de commande {order_line_id} introuvable")
    
    # Check if product is personalisable
    product = db.get(Product, order_line.product_id)
    if not product:
        raise ValueError(f"Produit {order_line.product_id} introuvable")
    
    # If product is personalisable, personnalisation is required
    if product.personalisable and not personnalisation:
        raise ValueError("Personnalisation est obligatoire pour ce produit personnalisable")
    
    order_line.personnalisation = personnalisation
    order_line.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(order_line)
    
    return order_line


def get_orders_by_status_filter(
    db: Session,
    status_filter: str
) -> List[Order]:
    """
    Get orders filtered by status filter string
    Supports "Tous", "En cours", or specific OrderStatus value
    
    Args:
        db: Database session
        status_filter: Filter string ("Tous", "En cours", or OrderStatus value)
    
    Returns:
        List of Order objects
    """
    if status_filter == "Tous":
        return db.query(Order).order_by(Order.date_commande.desc()).all()
    elif status_filter == "En cours":
        # Exclude LIVRE and ANNULE
        return db.query(Order).filter(
            ~Order.statut.in_([OrderStatus.LIVRE, OrderStatus.ANNULE])
        ).order_by(Order.date_commande.desc()).all()
    else:
        return db.query(Order).filter(
            Order.statut == OrderStatus(status_filter)
        ).order_by(Order.date_commande.desc()).all()


def get_ready_for_delivery_lines(db: Session) -> List[OrderLine]:
    """
    Get order lines ready for delivery (LIVRE_LEA status, excluding already delivered orders)
    
    Args:
        db: Database session
    
    Returns:
        List of OrderLine objects ready for delivery
    """
    return db.query(OrderLine)\
        .join(Order)\
        .options(
            joinedload(OrderLine.product),
            joinedload(OrderLine.color),
            joinedload(OrderLine.order).joinedload(Order.client)
        )\
        .filter(
            OrderLine.statut_fabrication == FabricationStatus.LIVRE_LEA,
            Order.statut != OrderStatus.LIVRE
        )\
        .all()


def get_orders_ready_for_packaging(db: Session) -> List[Order]:
    """
    Retourne les commandes prêtes à être emballées (statut EN_PREPARATION).
    """
    return db.query(Order)\
        .options(
            joinedload(Order.client),
            joinedload(Order.order_lines).joinedload(OrderLine.product),
            joinedload(Order.order_lines).joinedload(OrderLine.color)
        )\
        .filter(Order.statut == OrderStatus.EN_PREPARATION)\
        .order_by(Order.date_commande.asc())\
        .all()


def get_orders_ready_for_delivery(db: Session) -> List[Order]:
    """
    Retourne les commandes prêtes pour la livraison (statut PRET_LIVRAISON).
    """
    return db.query(Order)\
        .options(
            joinedload(Order.client),
            joinedload(Order.order_lines).joinedload(OrderLine.product),
            joinedload(Order.order_lines).joinedload(OrderLine.color)
        )\
        .filter(Order.statut == OrderStatus.PRET_LIVRAISON)\
        .order_by(
            Order.ne_pas_emballer.desc(),
            Order.date_pret_livraison.asc().nullslast(),
            Order.date_commande.asc()
        )\
        .all()


def mark_order_ready_for_delivery(
    db: Session,
    order_id: int,
    user_id: int
) -> Order:
    """
    Passe une commande en statut PRET_LIVRAISON via le bouton "À livrer".
    """
    order = db.get(Order, order_id)
    if not order:
        raise ValueError(f"Commande {order_id} introuvable")
    if order.statut not in [OrderStatus.EN_PREPARATION, OrderStatus.PRET_LIVRAISON]:
        raise ValueError("Cette commande n'est pas en phase d'emballage")
    return update_order_status(db, order_id, OrderStatus.PRET_LIVRAISON, user_id)


def set_order_skip_packaging(
    db: Session,
    order_id: int,
    skip_packaging: bool,
    user_id: int
) -> Order:
    """
    Active/Désactive l'option "Ne pas emballer" pour une commande.
    """
    order = db.get(Order, order_id)
    if not order:
        raise ValueError(f"Commande {order_id} introuvable")

    order.ne_pas_emballer = skip_packaging
    order.updated_by = user_id
    order.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(order)

    # Si toutes les lignes sont déjà imprimées, recalculer automatiquement
    if skip_packaging:
        check_order_printed(db, order_id)

    return order


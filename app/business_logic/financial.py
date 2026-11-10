"""
Financial calculations and reversement logic
Handles profit calculations and payment tracking

Balance System (v2.3.0+):
- Reversements are NOT linked to specific order lines
- Outstanding balance = Total from delivered orders - Sum of all payments
- Simple payment journal with amount + comment
"""
import enum
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from database.models import OrderLine, Reversement, Product, ProductType, Order, OrderStatus


def _ensure_product_type_enum(product_type):
    """
    Normalize product_type values returned by SQLAlchemy Enum columns configured with values_callable.
    They may be actual ProductType enums or plain strings (the enum's value).
    """
    if isinstance(product_type, enum.Enum):
        try:
            return ProductType(product_type.value)
        except ValueError:
            raise ValueError(f"Type de produit inconnu: {product_type.value}") from None
    if isinstance(product_type, str):
        try:
            return ProductType(product_type)
        except ValueError:
            raise ValueError(f"Type de produit inconnu: {product_type}") from None
    raise ValueError(f"Type de produit invalide: {product_type!r}")


def calculate_line_financials(
    product: Product,
    prix_vente_unitaire: float,
    remise_pourcent: float = 0,
    quantite: int = 1
) -> dict:
    """
    Calculate financial data for an order line
    
    Two business models:
    
    Type A - PARTAGE_BENEFICES (shared profit):
    - Prix vente final = Prix vente unitaire * (1 - remise%)
    - Bénéfice = Prix vente final - Prix revient David - Prix revient Lea
    - Part bénéfice = Bénéfice / 2
    - Montant à reverser David = Prix revient David + Part bénéfice
    - Montant Lea garde = Prix revient Lea + Part bénéfice
    
    Type B - REVENTE_LEA (fixed resale price):
    - Prix vente final = Prix vente unitaire * (1 - remise%)
    - Montant à reverser David = Prix vente à Lea (FIXE)
    - Bénéfice = 0 (no shared profit)
    - Montant Lea = Prix vente final - Prix vente à Lea - Prix revient Lea
    
    Args:
        product: Product object
        prix_vente_unitaire: Unit selling price
        remise_pourcent: Discount percentage (0-100)
        quantite: Quantity
    
    Returns:
        dict with financial calculations
    """
    # Apply discount
    prix_vente_final = prix_vente_unitaire * (1 - remise_pourcent / 100)
    
    # Calculate total cost
    prix_revient_total = product.prix_revient_david + product.prix_revient_lea
    
    # Special case: If selling price is less than cost (e.g., free gift to influencer)
    # No reversement should be calculated
    if prix_vente_final < prix_revient_total:
        return {
            'prix_vente_unitaire': round(prix_vente_unitaire, 2),
            'remise_pourcent': remise_pourcent,
            'prix_vente_final': round(prix_vente_final, 2),
            'benefice_calcule': round(prix_vente_final - prix_revient_total, 2),  # Negative or zero
            'montant_a_reverser_david': 0,  # No reversement for free/gift items
            'montant_lea': round(prix_vente_final, 2),  # Lea keeps the full amount (if any)
            'prix_vente_total': round(prix_vente_final * quantite, 2),
            'benefice_total': round((prix_vente_final - prix_revient_total) * quantite, 2),
            'montant_david_total': 0,  # No reversement
            'montant_lea_total': round(prix_vente_final * quantite, 2)
        }
    
    product_type = _ensure_product_type_enum(product.product_type)

    if product_type == ProductType.PARTAGE_BENEFICES:
        # TYPE A: Shared profit model (classic)
        benefice_brut = prix_vente_final - product.prix_revient_david - product.prix_revient_lea
        part_benefice = benefice_brut / 2
        montant_david = product.prix_revient_david + part_benefice
        montant_lea = product.prix_revient_lea + part_benefice
    
    else:  # ProductType.REVENTE_LEA
        # TYPE B: Fixed resale price to Lea
        if product.prix_vente_lea is None:
            raise ValueError(
                f"Le produit '{product.nom}' est en mode REVENTE_LEA mais 'prix_vente_lea' n'est pas défini."
            )
        # Special case: If selling price is less than cost, no reversement
        if prix_vente_final < prix_revient_total:
            # Selling price is less than cost (e.g., free gift to influencer)
            montant_david = 0
            benefice_brut = prix_vente_final - prix_revient_total  # Negative or zero
            montant_lea = prix_vente_final - product.prix_revient_lea
        elif prix_vente_final < product.prix_vente_lea:
            # Selling price is less than fixed price to Lea - no reversement
            montant_david = 0
            benefice_brut = prix_vente_final - prix_revient_total
            montant_lea = prix_vente_final - product.prix_revient_lea
        else:
            # Normal case: Fixed resale price to Lea
            montant_david = product.prix_vente_lea  # FIXED AMOUNT
            benefice_brut = 0  # No shared profit
            # Lea's margin = selling price - amount to David - Lea's costs
            montant_lea = prix_vente_final - montant_david - product.prix_revient_lea
    
    return {
        'prix_vente_unitaire': round(prix_vente_unitaire, 2),
        'remise_pourcent': remise_pourcent,
        'prix_vente_final': round(prix_vente_final, 2),
        'benefice_calcule': round(benefice_brut, 2),
        'montant_a_reverser_david': round(montant_david, 2),
        'montant_lea': round(montant_lea, 2),
        'prix_vente_total': round(prix_vente_final * quantite, 2),
        'benefice_total': round(benefice_brut * quantite, 2),
        'montant_david_total': round(montant_david * quantite, 2),
        'montant_lea_total': round(montant_lea * quantite, 2)
    }


# ============================================================
# BALANCE SYSTEM (v2.3.0+)
# ============================================================

def get_total_a_reverser(db: Session) -> float:
    """
    Get total amount to pay to David from delivered orders.
    
    Only counts order lines from orders with status LIVRE (delivered).
    
    Args:
        db: Database session
    
    Returns:
        Total amount calculated from all delivered order lines
    """
    result = db.query(
        func.sum(OrderLine.montant_a_reverser_david * OrderLine.quantite)
    ).join(Order).filter(
        Order.statut == OrderStatus.LIVRE
    ).scalar()
    
    return round(result or 0, 2)


def get_total_reverse(db: Session) -> float:
    """
    Get total amount already paid to David (sum of all reversements).
    
    Args:
        db: Database session
    
    Returns:
        Total of all payments made
    """
    result = db.query(func.sum(Reversement.montant)).scalar()
    return round(result or 0, 2)


def get_solde_a_reverser(db: Session) -> dict:
    """
    Get the balance summary: total due, total paid, outstanding.
    
    Args:
        db: Database session
    
    Returns:
        dict with 'total_du', 'total_paye', 'solde'
    """
    total_du = get_total_a_reverser(db)
    total_paye = get_total_reverse(db)
    solde = round(total_du - total_paye, 2)
    
    return {
        'total_du': total_du,
        'total_paye': total_paye,
        'solde': solde
    }


def create_reversement(
    db: Session,
    montant: float,
    commentaire: Optional[str] = None,
    user_id: Optional[int] = None
) -> Reversement:
    """
    Create a new payment (reversement) to David.
    
    Args:
        db: Database session
        montant: Amount paid
        commentaire: Optional comment/note
        user_id: User creating the reversement
    
    Returns:
        Created Reversement object
    """
    if montant <= 0:
        raise ValueError("Le montant doit être positif")
    
    reversement = Reversement(
        montant=round(montant, 2),
        date_reversement=datetime.utcnow(),
        commentaire=commentaire.strip() if commentaire else None,
        created_by=user_id
    )
    db.add(reversement)
    db.commit()
    db.refresh(reversement)
    
    return reversement


def get_reversements_history(
    db: Session,
    limit: Optional[int] = None
) -> List[Reversement]:
    """
    Get reversements history, most recent first.
    
    Args:
        db: Database session
        limit: Optional limit on number of results
    
    Returns:
        List of Reversement objects
    """
    query = db.query(Reversement).order_by(Reversement.date_reversement.desc())
    
    if limit:
        query = query.limit(limit)
    
    return query.all()


def delete_reversement(db: Session, reversement_id: int) -> bool:
    """
    Delete a reversement (in case of error).
    
    Args:
        db: Database session
        reversement_id: ID of the reversement to delete
    
    Returns:
        True if deleted, False if not found
    """
    reversement = db.query(Reversement).filter(Reversement.id == reversement_id).first()
    if not reversement:
        return False
    
    db.delete(reversement)
    db.commit()
    return True


# ============================================================
# FINANCIAL TABLES BY PRODUCT (for finances page)
# ============================================================

def get_financial_tables(db: Session) -> Dict[str, Any]:
    """
    Get all financial tables for the finances page.
    
    Returns 4 tables:
    1. Bénéfices (PARTAGE_BENEFICES products)
    2. Rétrocessions David (PARTAGE_BENEFICES products)
    3. Bénéfice Léa (PARTAGE_BENEFICES products)
    4. Reventes Léa (REVENTE_LEA products)
    
    Plus summary with total reversement and balance.
    
    Args:
        db: Database session
    
    Returns:
        dict with all tables data
    """
    # Get all order lines from delivered orders
    lines = db.query(OrderLine).join(Order).filter(
        Order.statut == OrderStatus.LIVRE
    ).all()
    
    if not lines:
        return {
            'benefices': [],
            'benefices_totals': _empty_benefices_totals(),
            'retrocessions_david': [],
            'retrocessions_david_totals': _empty_retrocessions_totals(),
            'benefice_lea': [],
            'benefice_lea_totals': _empty_benefice_lea_totals(),
            'revente_lea': [],
            'revente_lea_totals': _empty_revente_lea_totals(),
            'total_reversement': 0,
        }
    
    # Group lines by product
    products_partage: Dict[int, Dict] = {}  # product_id -> aggregated data
    products_revente: Dict[int, Dict] = {}  # product_id -> aggregated data
    
    for line in lines:
        product = line.product
        if not product:
            continue
        
        product_type = _ensure_product_type_enum(product.product_type)
        
        if product_type == ProductType.PARTAGE_BENEFICES:
            if product.id not in products_partage:
                products_partage[product.id] = {
                    'product': product,
                    'quantite': 0,
                    'revenu_total': 0,
                    'cout_david_total': 0,
                    'cout_lea_total': 0,
                    'benefice_total': 0,
                    'prix_vente_sum': 0,  # For average calculation
                }
            
            data = products_partage[product.id]
            data['quantite'] += line.quantite
            data['revenu_total'] += line.prix_vente_total
            data['cout_david_total'] += product.prix_revient_david * line.quantite
            data['cout_lea_total'] += product.prix_revient_lea * line.quantite
            data['benefice_total'] += line.benefice_total
            data['prix_vente_sum'] += line.prix_vente_final * line.quantite
            
        else:  # REVENTE_LEA
            if product.id not in products_revente:
                products_revente[product.id] = {
                    'product': product,
                    'quantite': 0,
                    'reversement_total': 0,
                }
            
            data = products_revente[product.id]
            data['quantite'] += line.quantite
            # For REVENTE_LEA, reversement = prix_vente_lea (fixed) * quantite
            data['reversement_total'] += line.montant_a_reverser_david_total
    
    # Build Table 1: Bénéfices (PARTAGE_BENEFICES)
    benefices = []
    for product_id, data in products_partage.items():
        product = data['product']
        qte = data['quantite']
        cout_total = data['cout_david_total'] + data['cout_lea_total']
        prix_vente_moy = data['prix_vente_sum'] / qte if qte > 0 else 0
        benefice_par_piece = data['benefice_total'] / qte if qte > 0 else 0
        
        benefices.append({
            'nom': product.nom,
            'prix_vente_moy': round(prix_vente_moy, 2),
            'prix_revient_david': product.prix_revient_david,
            'prix_revient_lea': product.prix_revient_lea,
            'quantite': qte,
            'benefice_par_piece': round(benefice_par_piece, 2),
            'cout_total': round(cout_total, 2),
            'revenu_total': round(data['revenu_total'], 2),
            'benefice_total': round(data['benefice_total'], 2),
        })
    
    # Sort by product name
    benefices.sort(key=lambda x: x['nom'])
    
    # Totals for Table 1
    benefices_totals = {
        'quantite': sum(b['quantite'] for b in benefices),
        'cout_total': round(sum(b['cout_total'] for b in benefices), 2),
        'revenu_total': round(sum(b['revenu_total'] for b in benefices), 2),
        'benefice_total': round(sum(b['benefice_total'] for b in benefices), 2),
    }
    
    # Build Table 2: Rétrocessions David (PARTAGE_BENEFICES)
    retrocessions_david = []
    for product_id, data in products_partage.items():
        product = data['product']
        prix_revient_total = data['cout_david_total']
        part_benefice = data['benefice_total'] / 2
        reversement = prix_revient_total + part_benefice
        
        retrocessions_david.append({
            'nom': product.nom,
            'prix_revient_total': round(prix_revient_total, 2),
            'part_benefice': round(part_benefice, 2),
            'reversement': round(reversement, 2),
        })
    
    retrocessions_david.sort(key=lambda x: x['nom'])
    
    # Totals for Table 2
    retrocessions_david_totals = {
        'prix_revient_total': round(sum(r['prix_revient_total'] for r in retrocessions_david), 2),
        'part_benefice': round(sum(r['part_benefice'] for r in retrocessions_david), 2),
        'reversement': round(sum(r['reversement'] for r in retrocessions_david), 2),
    }
    
    # Build Table 3: Bénéfice Léa (PARTAGE_BENEFICES)
    benefice_lea = []
    for product_id, data in products_partage.items():
        product = data['product']
        prix_revient_total = data['cout_lea_total']
        part_benefice = data['benefice_total'] / 2
        
        benefice_lea.append({
            'nom': product.nom,
            'prix_revient_total': round(prix_revient_total, 2),
            'part_benefice': round(part_benefice, 2),
        })
    
    benefice_lea.sort(key=lambda x: x['nom'])
    
    # Totals for Table 3
    benefice_lea_totals = {
        'prix_revient_total': round(sum(b['prix_revient_total'] for b in benefice_lea), 2),
        'part_benefice': round(sum(b['part_benefice'] for b in benefice_lea), 2),
    }
    
    # Build Table 4: Reventes Léa (REVENTE_LEA)
    revente_lea = []
    for product_id, data in products_revente.items():
        product = data['product']
        prix_revient_david = product.prix_vente_lea or 0  # Fixed price to David
        
        revente_lea.append({
            'nom': product.nom,
            'prix_revient_david': prix_revient_david,
            'quantite': data['quantite'],
            'reversement': round(data['reversement_total'], 2),
        })
    
    revente_lea.sort(key=lambda x: x['nom'])
    
    # Totals for Table 4
    revente_lea_totals = {
        'quantite': sum(r['quantite'] for r in revente_lea),
        'reversement': round(sum(r['reversement'] for r in revente_lea), 2),
    }
    
    # Total reversement (Table 2 + Table 4)
    total_reversement = retrocessions_david_totals['reversement'] + revente_lea_totals['reversement']
    
    return {
        'benefices': benefices,
        'benefices_totals': benefices_totals,
        'retrocessions_david': retrocessions_david,
        'retrocessions_david_totals': retrocessions_david_totals,
        'benefice_lea': benefice_lea,
        'benefice_lea_totals': benefice_lea_totals,
        'revente_lea': revente_lea,
        'revente_lea_totals': revente_lea_totals,
        'total_reversement': round(total_reversement, 2),
    }


def _empty_benefices_totals():
    return {'quantite': 0, 'cout_total': 0, 'revenu_total': 0, 'benefice_total': 0}


def _empty_retrocessions_totals():
    return {'prix_revient_total': 0, 'part_benefice': 0, 'reversement': 0}


def _empty_benefice_lea_totals():
    return {'prix_revient_total': 0, 'part_benefice': 0}


def _empty_revente_lea_totals():
    return {'quantite': 0, 'reversement': 0}


# ============================================================
# STATISTICS (for dashboard/analytics)
# ============================================================

def get_benefice_statistics(db: Session, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> dict:
    """
    Get profit statistics for a date range.
    
    Args:
        db: Database session
        start_date: Optional start date
        end_date: Optional end date
    
    Returns:
        dict with statistics
    """
    # Base query
    query = db.query(OrderLine).join(Order)
    
    # Apply date filters
    if start_date:
        query = query.filter(Order.date_commande >= start_date)
    if end_date:
        query = query.filter(Order.date_commande <= end_date)
    
    # Get completed orders (PAYE and onwards)
    valid_statuses = [
        OrderStatus.PAYE,
        OrderStatus.IMPRIME,
        OrderStatus.EN_PREPARATION,
        OrderStatus.PRET_LIVRAISON,
        OrderStatus.LIVRE
    ]
    query = query.filter(Order.statut.in_(valid_statuses))
    
    lines = query.all()
    
    if not lines:
        return {
            'total_ventes': 0,
            'total_benefice': 0,
            'total_david': 0,
            'total_lea': 0,
            'nb_commandes': 0,
            'nb_commandes_livrees': 0,
            'nb_commandes_pret': 0,
            'nb_commandes_emballage': 0,
            'total_commandes_passees': 0,
            'total_objets': 0,
            'total_objets_produits': 0,
            'total_objets_commandes': 0,
            'nb_lignes': 0,
            'benefice_moyen_par_ligne': 0
        }
    
    # Calculate totals
    total_ventes = sum(line.prix_vente_total for line in lines)
    total_benefice = sum(line.benefice_total for line in lines)
    total_david = sum(line.montant_a_reverser_david_total for line in lines)
    total_lea = total_ventes - total_david
    
    # Metrics Calculation
    # 1. Total Objets (in PAYE+ orders)
    total_objets = sum(line.quantite for line in lines)
    
    # 2. Commandes metrics
    order_ids_livre = set(line.order_id for line in lines if line.order.statut == OrderStatus.LIVRE)
    nb_commandes_livrees = len(order_ids_livre)
    
    order_ids_pret = set(line.order_id for line in lines if line.order.statut == OrderStatus.PRET_LIVRAISON)
    nb_commandes_pret = len(order_ids_pret)
    
    order_ids_emballage = set(line.order_id for line in lines if line.order.statut == OrderStatus.EN_PREPARATION)
    nb_commandes_emballage = len(order_ids_emballage)
    
    # 3. Objets Produits (Fabrication status IMPRIME/LIVRE_LEA OR Order status LIVRE)
    from database.models import FabricationStatus
    produced_statuses = ['imprime', 'livre_lea']
    
    total_objets_produits = 0
    for line in lines:
        is_order_livre = line.order.statut == OrderStatus.LIVRE
        fab_status = str(line.statut_fabrication.value) if hasattr(line.statut_fabrication, 'value') else str(line.statut_fabrication)
        is_line_produced = fab_status in produced_statuses
        
        if is_order_livre or is_line_produced:
            total_objets_produits += line.quantite
            
    # Count unique orders (PAYE+)
    order_ids = set(line.order_id for line in lines)
    
    # Calculate total ordered (all statuses except ANNULE and EN_ATTENTE)
    query_total = db.query(OrderLine).join(Order)
    if start_date:
        query_total = query_total.filter(Order.date_commande >= start_date)
    if end_date:
        query_total = query_total.filter(Order.date_commande <= end_date)
    
    query_total = query_total.filter(
        Order.statut != OrderStatus.ANNULE,
        Order.statut != OrderStatus.EN_ATTENTE
    )
    
    lines_total = query_total.all()
    total_objets_commandes = sum(line.quantite for line in lines_total)
    total_commandes_passees = len(set(line.order_id for line in lines_total))
    
    return {
        'total_ventes': round(total_ventes, 2),
        'total_benefice': round(total_benefice, 2),
        'total_david': round(total_david, 2),
        'total_lea': round(total_lea, 2),
        'nb_commandes': len(order_ids),
        'nb_commandes_livrees': nb_commandes_livrees,
        'nb_commandes_pret': nb_commandes_pret,
        'nb_commandes_emballage': nb_commandes_emballage,
        'total_commandes_passees': total_commandes_passees,
        'total_objets': total_objets,
        'total_objets_produits': total_objets_produits,
        'total_objets_commandes': total_objets_commandes,
        'nb_lignes': len(lines),
        'benefice_moyen_par_ligne': round(total_benefice / len(lines), 2) if lines else 0
    }

"""
Export and backup utilities
Handle database exports and Excel generation
"""
import io
import pandas as pd
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from database.models import (
    Order, OrderLine, Client, Product, Color, 
    Printer, Reversement, StockMovement
)


def export_orders_to_excel(db: Session, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> bytes:
    """
    Export orders to Excel
    
    Args:
        db: Database session
        start_date: Optional start date filter
        end_date: Optional end date filter
    
    Returns:
        Excel file as bytes
    """
    # Query orders
    query = db.query(Order)
    if start_date:
        query = query.filter(Order.date_commande >= start_date)
    if end_date:
        query = query.filter(Order.date_commande <= end_date)
    
    orders = query.all()
    
    # Prepare data
    data = []
    for order in orders:
        for line in order.order_lines:
            data.append({
                'Numero Commande': order.numero_commande,
                'Date Commande': order.date_commande.strftime('%d/%m/%Y'),
                'Client': order.client.full_name,
                'Produit': line.product.nom,
                'Couleur': line.color.nom,
                'Quantite': line.quantite,
                'Prix Unitaire': line.prix_vente_unitaire,
                'Remise %': line.remise_pourcent,
                'Prix Final': line.prix_vente_final,
                'Prix Total': line.prix_vente_total,
                'Benefice': line.benefice_total,
                'A Reverser David': line.montant_a_reverser_david_total,
                'Statut Commande': order.statut.value,
                'Statut Fabrication': line.statut_fabrication.value,
            })
    
    # Create Excel
    df = pd.DataFrame(data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Commandes', index=False)
    
    return output.getvalue()


def export_clients_to_excel(db: Session) -> bytes:
    """
    Export clients to Excel
    
    Args:
        db: Database session
    
    Returns:
        Excel file as bytes
    """
    clients = db.query(Client).all()
    
    data = []
    for client in clients:
        data.append({
            'ID': client.id,
            'Nom': client.nom,
            'Prenom': client.prenom,
            'Email': client.email,
            'Telephone': client.telephone,
            'Adresse': client.adresse,
            'Notes': client.notes,
            'Date Creation': client.date_creation.strftime('%d/%m/%Y'),
            'Nb Commandes': len(client.orders)
        })
    
    df = pd.DataFrame(data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Clients', index=False)
    
    return output.getvalue()


def export_products_to_excel(db: Session) -> bytes:
    """
    Export products to Excel
    
    Args:
        db: Database session
    
    Returns:
        Excel file as bytes
    """
    products = db.query(Product).all()
    
    data = []
    for product in products:
        data.append({
            'ID': product.id,
            'Nom': product.nom,
            'Description': product.description,
            'Prix Revient David': product.prix_revient_david,
            'Prix Revient Lea': product.prix_revient_lea,
            'Prix Vente Base': product.prix_vente_base,
            'Poids (g)': product.poids_grammes,
            'Temps Impression (min)': product.temps_impression_minutes,
            'Actif': 'Oui' if product.actif else 'Non',
            'Observations': product.observations
        })
    
    df = pd.DataFrame(data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Produits', index=False)
    
    return output.getvalue()


def export_stock_to_excel(db: Session) -> bytes:
    """
    Export stock and movements to Excel
    
    Args:
        db: Database session
    
    Returns:
        Excel file as bytes
    """
    colors = db.query(Color).all()
    movements = db.query(StockMovement).order_by(StockMovement.date.desc()).all()
    
    # Stock data
    stock_data = []
    for color in colors:
        stock_data.append({
            'Couleur': color.nom,
            'Stock (g)': color.stock_grammes,
            'Stock (kg)': color.stock_kg,
            'Cout par kg': color.cout_par_kg,
            'Valeur Stock': round((color.stock_grammes / 1000) * color.cout_par_kg, 2),
            'Seuil Alerte': color.seuil_alerte,
            'Stock Bas': 'Oui' if color.is_low_stock else 'Non'
        })
    
    # Movements data
    movements_data = []
    for mov in movements:
        movements_data.append({
            'Date': mov.date.strftime('%d/%m/%Y %H:%M'),
            'Couleur': mov.color.nom,
            'Type': mov.type.value,
            'Quantite (g)': mov.quantite_grammes,
            'Commande': mov.order_line.order.numero_commande if mov.order_line else '',
            'Observations': mov.observations
        })
    
    # Create Excel with multiple sheets
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(stock_data).to_excel(writer, sheet_name='Stock Actuel', index=False)
        pd.DataFrame(movements_data).to_excel(writer, sheet_name='Mouvements', index=False)
    
    return output.getvalue()


def export_finances_to_excel(db: Session) -> bytes:
    """
    Export financial data to Excel
    
    Balance system (v2.3.0+):
    - 4 financial tables by product
    - Balance summary
    - Reversements history
    
    Args:
        db: Database session
    
    Returns:
        Excel file as bytes
    """
    from business_logic.financial import (
        get_financial_tables,
        get_total_reverse
    )
    
    # Get financial tables
    tables = get_financial_tables(db)
    total_deja_reverse = get_total_reverse(db)
    solde_restant = round(tables['total_reversement'] - total_deja_reverse, 2)
    
    # Get reversements history
    reversements = db.query(Reversement).order_by(Reversement.date_reversement.desc()).all()
    
    # Balance summary
    balance_data = [{
        'Total à reverser': tables['total_reversement'],
        'Déjà reversé': total_deja_reverse,
        'Reste à reverser': solde_restant
    }]
    
    # Table 1: Bénéfices
    benefices_data = []
    for row in tables['benefices']:
        benefices_data.append({
            'Produit': row['nom'],
            'Prix vente (moy)': row['prix_vente_moy'],
            'PR David': row['prix_revient_david'],
            'PR Léa': row['prix_revient_lea'],
            'Quantité': row['quantite'],
            'Bénéf/pièce': row['benefice_par_piece'],
            'Coût total': row['cout_total'],
            'Revenu total': row['revenu_total'],
            'Bénéfice total': row['benefice_total'],
        })
    if benefices_data:
        totals = tables['benefices_totals']
        benefices_data.append({
            'Produit': 'TOTAL',
            'Prix vente (moy)': '',
            'PR David': '',
            'PR Léa': '',
            'Quantité': totals['quantite'],
            'Bénéf/pièce': '',
            'Coût total': totals['cout_total'],
            'Revenu total': totals['revenu_total'],
            'Bénéfice total': totals['benefice_total'],
        })
    
    # Table 2: Rétrocessions David
    retrocessions_data = []
    for row in tables['retrocessions_david']:
        retrocessions_data.append({
            'Produit': row['nom'],
            'Prix revient total': row['prix_revient_total'],
            'Part bénéfice': row['part_benefice'],
            'Reversement': row['reversement'],
        })
    if retrocessions_data:
        totals = tables['retrocessions_david_totals']
        retrocessions_data.append({
            'Produit': 'TOTAL',
            'Prix revient total': totals['prix_revient_total'],
            'Part bénéfice': totals['part_benefice'],
            'Reversement': totals['reversement'],
        })
    
    # Table 3: Bénéfice Léa
    benefice_lea_data = []
    for row in tables['benefice_lea']:
        benefice_lea_data.append({
            'Produit': row['nom'],
            'Prix revient total': row['prix_revient_total'],
            'Part bénéfice': row['part_benefice'],
        })
    if benefice_lea_data:
        totals = tables['benefice_lea_totals']
        benefice_lea_data.append({
            'Produit': 'TOTAL',
            'Prix revient total': totals['prix_revient_total'],
            'Part bénéfice': totals['part_benefice'],
        })
    
    # Table 4: Reventes Léa
    revente_lea_data = []
    for row in tables['revente_lea']:
        revente_lea_data.append({
            'Produit': row['nom'],
            'Prix revient David': row['prix_revient_david'],
            'Quantité': row['quantite'],
            'Reversement': row['reversement'],
        })
    if revente_lea_data:
        totals = tables['revente_lea_totals']
        revente_lea_data.append({
            'Produit': 'TOTAL',
            'Prix revient David': '',
            'Quantité': totals['quantite'],
            'Reversement': totals['reversement'],
        })
    
    # Reversements history
    reversements_data = []
    for rev in reversements:
        reversements_data.append({
            'Date': rev.date_reversement.strftime('%d/%m/%Y'),
            'Montant': rev.montant,
            'Commentaire': rev.commentaire or ''
        })
    
    # Create Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(balance_data).to_excel(writer, sheet_name='Solde', index=False)
        pd.DataFrame(benefices_data).to_excel(writer, sheet_name='1-Bénéfices', index=False)
        pd.DataFrame(retrocessions_data).to_excel(writer, sheet_name='2-Rétrocessions David', index=False)
        pd.DataFrame(benefice_lea_data).to_excel(writer, sheet_name='3-Bénéfice Léa', index=False)
        if revente_lea_data:
            pd.DataFrame(revente_lea_data).to_excel(writer, sheet_name='4-Reventes Léa', index=False)
        pd.DataFrame(reversements_data).to_excel(writer, sheet_name='Historique', index=False)
    
    return output.getvalue()


def export_full_database_to_excel(db: Session) -> bytes:
    """
    Export entire database to Excel (all tables)
    
    Args:
        db: Database session
    
    Returns:
        Excel file as bytes
    """
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Orders
        orders_df = pd.read_sql_query("SELECT * FROM orders", db.bind)
        orders_df.to_excel(writer, sheet_name='Orders', index=False)
        
        # Order Lines
        order_lines_df = pd.read_sql_query("SELECT * FROM order_lines", db.bind)
        order_lines_df.to_excel(writer, sheet_name='Order Lines', index=False)
        
        # Clients
        clients_df = pd.read_sql_query("SELECT * FROM clients", db.bind)
        clients_df.to_excel(writer, sheet_name='Clients', index=False)
        
        # Products
        products_df = pd.read_sql_query("SELECT * FROM products", db.bind)
        products_df.to_excel(writer, sheet_name='Products', index=False)
        
        # Colors
        colors_df = pd.read_sql_query("SELECT * FROM colors", db.bind)
        colors_df.to_excel(writer, sheet_name='Colors', index=False)
        
        # Printers
        printers_df = pd.read_sql_query("SELECT * FROM printers", db.bind)
        printers_df.to_excel(writer, sheet_name='Printers', index=False)
        
        # Reversements
        reversements_df = pd.read_sql_query("SELECT * FROM reversements", db.bind)
        reversements_df.to_excel(writer, sheet_name='Reversements', index=False)
        
        # Stock Movements
        stock_df = pd.read_sql_query("SELECT * FROM stock_movements", db.bind)
        stock_df.to_excel(writer, sheet_name='Stock Movements', index=False)
    
    return output.getvalue()


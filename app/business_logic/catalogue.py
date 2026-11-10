"""
Catalogue management business logic
Handles products, colors, and printers CRUD operations
"""
import re
from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from database.models import Product, Color, Printer, ProductType, FilamentType, PrinterStatus


# ========== PRODUCT DESIGN LINKS ==========

TINKERCAD_THING_URL = "https://www.tinkercad.com/things/"
_TINKERCAD_ID = r"[A-Za-z0-9]{11}"
_TINKERCAD_URL_RE = re.compile(rf"^(?:https?://)?(?:www\.)?tinkercad\.com/things/({_TINKERCAD_ID})(?:[-/?#].*)?$")
_TINKERCAD_ID_RE = re.compile(rf"^{_TINKERCAD_ID}$")


def _unique_lines(text: Optional[str]) -> List[str]:
    """Return the stripped, non-empty lines of text, without duplicates, in order"""
    lines = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and line not in lines:
            lines.append(line)
    return lines


def normalize_tinkercad_links(text: Optional[str]) -> Optional[str]:
    """
    Normalize Tinkercad links (one per line) to canonical design URLs.

    Accepts full design URLs (with or without the name slug, /edit, query string)
    or bare design ids. The name slug is dropped so links survive design renames.

    Raises:
        ValueError: If a line is not a Tinkercad design link
    """
    urls = []
    for line in _unique_lines(text):
        match = _TINKERCAD_URL_RE.match(line)
        if match:
            design_id = match.group(1)
        elif _TINKERCAD_ID_RE.match(line):
            design_id = line
        else:
            raise ValueError(f"Lien Tinkercad invalide : « {line} » (attendu : https://www.tinkercad.com/things/...)")
        url = TINKERCAD_THING_URL + design_id
        if url not in urls:
            urls.append(url)
    return "\n".join(urls) or None


def normalize_3mf_files(text: Optional[str]) -> Optional[str]:
    """
    Normalize 3MF file names (one per line). Adds the .3mf extension when missing.

    Raises:
        ValueError: If a line contains a folder path or another file extension
    """
    files = []
    for line in _unique_lines(text):
        if "/" in line or "\\" in line:
            raise ValueError(f"Fichier 3MF : indiquer le nom du fichier seul, sans dossier (« {line} »)")
        if not line.lower().endswith(".3mf"):
            if re.search(r"\.[A-Za-z][A-Za-z0-9]{1,3}$", line):
                raise ValueError(f"Seuls les fichiers .3mf sont acceptés (« {line} »)")
            line += ".3mf"
        if line not in files:
            files.append(line)
    return "\n".join(files) or None


# ========== PRODUCTS ==========

def get_all_products(db: Session, active_only: bool = True) -> List[Product]:
    """
    Get all products
    
    Args:
        db: Database session
        active_only: If True, only return active products
    
    Returns:
        List of Product objects
    """
    query = db.query(Product)
    if active_only:
        query = query.filter(Product.actif == True)
    return query.order_by(Product.nom).all()


def get_product_by_id(db: Session, product_id: int) -> Optional[Product]:
    """
    Get a product by ID
    
    Args:
        db: Database session
        product_id: Product ID
    
    Returns:
        Product object or None
    """
    return db.query(Product).get(product_id)


def create_product(
    db: Session,
    nom: str,
    product_type: ProductType,
    prix_vente_base: float,
    prix_revient_david: float,
    prix_revient_lea: float,
    poids_grammes: float,
    temps_impression_minutes: int,
    user_id: int,
    prix_vente_lea: Optional[float] = None,
    description: Optional[str] = None,
    image_url: Optional[str] = None,
    personalisable: bool = False,
    gestion_stock: bool = False,
    alerte_stock: int = 0,
    observations: Optional[str] = None,
    liens_tinkercad: Optional[str] = None,
    fichiers_3mf: Optional[str] = None
) -> Product:
    """
    Create a new product
    
    Args:
        db: Database session
        nom: Product name (required)
        product_type: Product type (PARTAGE_BENEFICES or REVENTE_LEA)
        prix_vente_base: Base selling price (required)
        prix_revient_david: Cost for David (required)
        prix_revient_lea: Cost for Lea (required)
        poids_grammes: Weight in grams (required)
        temps_impression_minutes: Print time in minutes (required)
        user_id: User creating the product
        prix_vente_lea: Selling price to Lea (required for REVENTE_LEA type)
        description: Description (optional)
        image_url: Image URL/path (optional)
        personalisable: Whether product can be personalized (default: False)
        gestion_stock: Whether stock management is enabled (default: False)
        alerte_stock: Stock alert threshold (default: 0)
        observations: Notes (optional)
        liens_tinkercad: Tinkercad design links, one per line (optional)
        fichiers_3mf: 3MF file names, one per line (optional)

    Returns:
        Created Product object

    Raises:
        ValueError: If required fields are missing or links are invalid
    """
    if not nom:
        raise ValueError("Le nom est obligatoire")

    if not prix_vente_base:
        raise ValueError("Le prix de vente est obligatoire")

    if product_type == ProductType.REVENTE_LEA and prix_vente_lea is None:
        raise ValueError("Le prix de vente à Lea est obligatoire pour les produits de type REVENTE_LEA")

    liens_tinkercad = normalize_tinkercad_links(liens_tinkercad)
    fichiers_3mf = normalize_3mf_files(fichiers_3mf)

    product = Product(
        nom=nom,
        description=description if description else None,
        image_url=image_url if image_url else None,
        product_type=product_type,
        prix_revient_david=prix_revient_david,
        prix_revient_lea=prix_revient_lea,
        prix_vente_base=prix_vente_base,
        prix_vente_lea=prix_vente_lea,
        poids_grammes=poids_grammes,
        temps_impression_minutes=temps_impression_minutes,
        personalisable=personalisable,
        gestion_stock=gestion_stock,
        alerte_stock=alerte_stock if gestion_stock else 0,
        actif=True,
        observations=observations if observations else None,
        liens_tinkercad=liens_tinkercad,
        fichiers_3mf=fichiers_3mf,
        created_by=user_id
    )
    
    db.add(product)
    db.commit()
    db.refresh(product)
    
    return product


def update_product(
    db: Session,
    product_id: int,
    nom: Optional[str] = None,
    description: Optional[str] = None,
    image_url: Optional[str] = None,
    product_type: Optional[ProductType] = None,
    prix_revient_david: Optional[float] = None,
    prix_revient_lea: Optional[float] = None,
    prix_vente_base: Optional[float] = None,
    prix_vente_lea: Optional[float] = None,
    poids_grammes: Optional[float] = None,
    temps_impression_minutes: Optional[int] = None,
    personalisable: Optional[bool] = None,
    gestion_stock: Optional[bool] = None,
    alerte_stock: Optional[int] = None,
    actif: Optional[bool] = None,
    observations: Optional[str] = None,
    liens_tinkercad: Optional[str] = None,
    fichiers_3mf: Optional[str] = None
) -> Product:
    """
    Update a product
    
    Args:
        db: Database session
        product_id: Product ID
        nom: Product name (optional)
        description: Description (optional)
        image_url: Image URL/path (optional)
        product_type: Product type (optional)
        prix_revient_david: Cost for David (optional)
        prix_revient_lea: Cost for Lea (optional)
        prix_vente_base: Base selling price (optional)
        prix_vente_lea: Selling price to Lea (optional)
        poids_grammes: Weight in grams (optional)
        temps_impression_minutes: Print time in minutes (optional)
        personalisable: Whether product can be personalized (optional)
        gestion_stock: Whether stock management is enabled (optional)
        alerte_stock: Stock alert threshold (optional)
        actif: Whether product is active (optional)
        observations: Notes (optional)
        liens_tinkercad: Tinkercad design links, one per line (optional, empty string clears)
        fichiers_3mf: 3MF file names, one per line (optional, empty string clears)

    Returns:
        Updated Product object

    Raises:
        ValueError: If product not found or required fields are invalid
    """
    product = db.query(Product).get(product_id)
    if not product:
        raise ValueError(f"Produit {product_id} introuvable")

    # Validate links before touching the product ("" means: clear the field)
    if liens_tinkercad is not None:
        liens_tinkercad = normalize_tinkercad_links(liens_tinkercad) or ""
    if fichiers_3mf is not None:
        fichiers_3mf = normalize_3mf_files(fichiers_3mf) or ""

    if nom is not None:
        if not nom:
            raise ValueError("Le nom est obligatoire")
        product.nom = nom
    
    if description is not None:
        product.description = description if description else None
    
    if image_url is not None:
        product.image_url = image_url if image_url else None
    
    if product_type is not None:
        product.product_type = product_type
    
    if prix_revient_david is not None:
        product.prix_revient_david = prix_revient_david
    
    if prix_revient_lea is not None:
        product.prix_revient_lea = prix_revient_lea
    
    if prix_vente_base is not None:
        if not prix_vente_base:
            raise ValueError("Le prix de vente est obligatoire")
        product.prix_vente_base = prix_vente_base
    
    if prix_vente_lea is not None:
        product.prix_vente_lea = prix_vente_lea
    
    if poids_grammes is not None:
        product.poids_grammes = poids_grammes
    
    if temps_impression_minutes is not None:
        product.temps_impression_minutes = temps_impression_minutes
    
    if personalisable is not None:
        product.personalisable = personalisable
    
    if gestion_stock is not None:
        product.gestion_stock = gestion_stock
    
    if alerte_stock is not None:
        product.alerte_stock = alerte_stock if product.gestion_stock else 0
    
    if actif is not None:
        product.actif = actif
    
    if observations is not None:
        product.observations = observations if observations else None

    if liens_tinkercad is not None:
        product.liens_tinkercad = liens_tinkercad if liens_tinkercad else None

    if fichiers_3mf is not None:
        product.fichiers_3mf = fichiers_3mf if fichiers_3mf else None

    product.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(product)
    
    return product


def deactivate_product(db: Session, product_id: int) -> Product:
    """
    Deactivate a product (set actif=False)
    
    Args:
        db: Database session
        product_id: Product ID
    
    Returns:
        Updated Product object
    
    Raises:
        ValueError: If product not found
    """
    return update_product(db, product_id, actif=False)


# ========== COLORS ==========

def get_all_colors(db: Session, active_only: bool = True) -> List[Color]:
    """
    Get all colors
    
    Args:
        db: Database session
        active_only: If True, only return active colors
    
    Returns:
        List of Color objects
    """
    query = db.query(Color)
    if active_only:
        query = query.filter(Color.actif == True)
    return query.order_by(Color.nom).all()


def get_color_by_id(db: Session, color_id: int) -> Optional[Color]:
    """
    Get a color by ID
    
    Args:
        db: Database session
        color_id: Color ID
    
    Returns:
        Color object or None
    """
    return db.query(Color).get(color_id)


def create_color(
    db: Session,
    nom: str,
    type_filament: FilamentType,
    cout_par_kg: float,
    stock_initial: float = 0.0,
    nom_original: Optional[str] = None,
    code_hex: Optional[str] = None,
    marque: Optional[str] = None,
    seuil_alerte: float = 500.0
) -> Color:
    """
    Create a new color
    
    Args:
        db: Database session
        nom: Color name (required)
        type_filament: Filament type (required)
        cout_par_kg: Cost per kg (required)
        stock_initial: Initial stock in grams (default: 0.0)
        nom_original: Original name from manufacturer (optional)
        code_hex: Hex color code (optional)
        marque: Brand name (optional)
        seuil_alerte: Alert threshold in grams (default: 500.0)
    
    Returns:
        Created Color object
    
    Raises:
        ValueError: If required fields are missing
    """
    if not nom:
        raise ValueError("Le nom est obligatoire")
    
    if not type_filament:
        raise ValueError("Le type de filament est obligatoire")
    
    color = Color(
        nom=nom,
        nom_original=nom_original if nom_original else None,
        code_hex=code_hex if code_hex else None,
        marque=marque if marque else None,
        type_filament=type_filament,
        stock_grammes=stock_initial,
        cout_par_kg=cout_par_kg,
        seuil_alerte=seuil_alerte,
        actif=True
    )
    
    db.add(color)
    db.commit()
    db.refresh(color)
    
    return color


def update_color(
    db: Session,
    color_id: int,
    nom: Optional[str] = None,
    nom_original: Optional[str] = None,
    code_hex: Optional[str] = None,
    marque: Optional[str] = None,
    type_filament: Optional[FilamentType] = None,
    cout_par_kg: Optional[float] = None,
    seuil_alerte: Optional[float] = None,
    actif: Optional[bool] = None
) -> Color:
    """
    Update a color
    
    Args:
        db: Database session
        color_id: Color ID
        nom: Color name (optional)
        nom_original: Original name (optional)
        code_hex: Hex color code (optional)
        marque: Brand name (optional)
        type_filament: Filament type (optional)
        cout_par_kg: Cost per kg (optional)
        seuil_alerte: Alert threshold (optional)
        actif: Whether color is active (optional)
    
    Returns:
        Updated Color object
    
    Raises:
        ValueError: If color not found or required fields are invalid
    """
    color = db.query(Color).get(color_id)
    if not color:
        raise ValueError(f"Couleur {color_id} introuvable")
    
    if nom is not None:
        if not nom:
            raise ValueError("Le nom est obligatoire")
        color.nom = nom
    
    if nom_original is not None:
        color.nom_original = nom_original if nom_original else None
    
    if code_hex is not None:
        color.code_hex = code_hex if code_hex else None
    
    if marque is not None:
        color.marque = marque if marque else None
    
    if type_filament is not None:
        if not type_filament:
            raise ValueError("Le type de filament est obligatoire")
        color.type_filament = type_filament
    
    if cout_par_kg is not None:
        color.cout_par_kg = cout_par_kg
    
    if seuil_alerte is not None:
        color.seuil_alerte = seuil_alerte
    
    if actif is not None:
        color.actif = actif
    
    color.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(color)
    
    return color


def deactivate_color(db: Session, color_id: int) -> Color:
    """
    Deactivate a color (set actif=False)
    
    Args:
        db: Database session
        color_id: Color ID
    
    Returns:
        Updated Color object
    
    Raises:
        ValueError: If color not found
    """
    return update_color(db, color_id, actif=False)


# ========== PRINTERS ==========

def get_all_printers(db: Session) -> List[Printer]:
    """
    Get all printers
    
    Args:
        db: Database session
    
    Returns:
        List of Printer objects
    """
    return db.query(Printer).order_by(Printer.nom).all()


def get_printer_by_id(db: Session, printer_id: int) -> Optional[Printer]:
    """
    Get a printer by ID
    
    Args:
        db: Database session
        printer_id: Printer ID
    
    Returns:
        Printer object or None
    """
    return db.query(Printer).get(printer_id)


def create_printer(
    db: Session,
    nom: str,
    statut: PrinterStatus = PrinterStatus.DISPONIBLE,
    modele: Optional[str] = None,
    observations: Optional[str] = None
) -> Printer:
    """
    Create a new printer
    
    Args:
        db: Database session
        nom: Printer name (required)
        statut: Printer status (default: DISPONIBLE)
        modele: Model name (optional)
        observations: Notes (optional)
    
    Returns:
        Created Printer object
    
    Raises:
        ValueError: If nom is empty
    """
    if not nom:
        raise ValueError("Le nom est obligatoire")
    
    printer = Printer(
        nom=nom,
        modele=modele if modele else None,
        statut=statut,
        observations=observations if observations else None
    )
    
    db.add(printer)
    db.commit()
    db.refresh(printer)
    
    return printer


def update_printer(
    db: Session,
    printer_id: int,
    nom: Optional[str] = None,
    modele: Optional[str] = None,
    statut: Optional[PrinterStatus] = None,
    observations: Optional[str] = None
) -> Printer:
    """
    Update a printer
    
    Args:
        db: Database session
        printer_id: Printer ID
        nom: Printer name (optional)
        modele: Model name (optional)
        statut: Printer status (optional)
        observations: Notes (optional)
    
    Returns:
        Updated Printer object
    
    Raises:
        ValueError: If printer not found or nom is empty
    """
    printer = db.query(Printer).get(printer_id)
    if not printer:
        raise ValueError(f"Imprimante {printer_id} introuvable")
    
    if nom is not None:
        if not nom:
            raise ValueError("Le nom est obligatoire")
        printer.nom = nom
    
    if modele is not None:
        printer.modele = modele if modele else None
    
    if statut is not None:
        printer.statut = statut
    
    if observations is not None:
        printer.observations = observations if observations else None
    
    printer.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(printer)
    
    return printer


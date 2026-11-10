"""
SQLAlchemy models for Malitan 3D Manager
Defines all database tables and relationships

Note on Security:
Row Level Security (RLS) is enabled on all tables in the PostgreSQL production database
(via migrations). SQLite (development) does not support RLS.
The application connects as a privileged user (service_role/postgres) which bypasses RLS,
ensuring backend functionality while blocking public API access.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, 
    Text, ForeignKey, Enum, ARRAY
)
from sqlalchemy.orm import declarative_base, relationship
import enum

Base = declarative_base()


class UserRole(enum.Enum):
    """User roles"""
    DAVID = "david"
    LEA = "lea"
    DANIELLE = "danielle"


class OrderStatus(enum.Enum):
    """Order statuses"""
    EN_ATTENTE = "en_attente"  # Waiting for payment
    PAYE = "paye"              # Paid, printing in progress
    IMPRIME = "imprime"        # Printed, ready for packaging
    EN_PREPARATION = "en_preparation"  # Being packaged
    PRET_LIVRAISON = "pret_livraison"  # Packaged (or skipped) and ready to deliver
    LIVRE = "livre"            # Delivered to client
    ANNULE = "annule"          # Cancelled


class FabricationStatus(enum.Enum):
    """Fabrication statuses"""
    FILE_ATTENTE = "file_attente"  # Waiting in queue
    PROCHAIN = "prochain"          # Next to be printed (prioritized)
    EN_COURS = "en_cours"          # Currently printing
    IMPRIME = "imprime"            # Printed (with David)
    LIVRE_LEA = "livre_lea"        # Delivered to Lea


class PrinterStatus(enum.Enum):
    """Printer statuses"""
    DISPONIBLE = "disponible"
    EN_COURS = "en_cours"
    MAINTENANCE = "maintenance"


class DeliveryMode(enum.Enum):
    """Delivery modes"""
    LIVRAISON = "livraison"
    PICKUP = "pickup"


class StockMovementType(enum.Enum):
    """Stock movement types (filament)"""
    ENTREE = "entree"
    SORTIE = "sortie"


class FinishedGoodsMovementType(enum.Enum):
    """Finished goods stock movement types"""
    ENTREE = "entree"  # Adding to stock (pre-printed)
    SORTIE = "sortie"  # Using from stock (assigned to order)


class FilamentType(enum.Enum):
    """Filament types"""
    PLA = "pla"
    PLA_MATTE = "pla_matte"
    PLA_SOIE = "pla_soie"
    PLA_MARBRE = "pla_marbre"
    PLA_SATIN = "pla_satin"


class ProductType(enum.Enum):
    """Product types - business model"""
    PARTAGE_BENEFICES = "partage_benefices"  # Classic: shared profit model
    REVENTE_LEA = "revente_lea"              # Resale: fixed amount to David


class User(Base):
    """Users table - David and Lea"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False)
    role = Column(Enum(UserRole, values_callable=lambda x: [e.value for e in x]), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    telegram_chat_id = Column(String(100), nullable=True)

    def __repr__(self):
        return f"<User {self.username} ({self.full_name})>"


class Client(Base):
    """Clients table"""
    __tablename__ = 'clients'
    
    id = Column(Integer, primary_key=True)
    nom = Column(String(100), nullable=False)
    prenom = Column(String(100))
    email = Column(String(255))
    telephone = Column(String(50))
    adresse = Column(Text)  # Unified address field (replaces adresse_fr and adresse_he)
    provenance = Column(String(255))  # Source/origin of the client
    notes = Column(Text)
    date_creation = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey('users.id'))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    orders = relationship("Order", back_populates="client")
    creator = relationship("User", foreign_keys=[created_by])
    
    def __repr__(self):
        return f"<Client {self.prenom} {self.nom}>"
    
    @property
    def full_name(self):
        if self.prenom:
            return f"{self.prenom} {self.nom}"
        return self.nom


class Product(Base):
    """Products (3D objects) table"""
    __tablename__ = 'products'
    
    id = Column(Integer, primary_key=True)
    nom = Column(String(200), nullable=False)
    description = Column(Text)
    image_url = Column(String(500))  # URL or path to product image
    product_type = Column(Enum(ProductType, values_callable=lambda x: [e.value for e in x]), nullable=False, default=ProductType.PARTAGE_BENEFICES)
    prix_revient_david = Column(Float, nullable=False, default=0)  # Cost for David (printing) or indicative for REVENTE_LEA
    prix_revient_lea = Column(Float, nullable=False, default=0)    # Cost for Lea (accessories/packaging)
    prix_vente_lea = Column(Float)  # Selling price to Lea (fixed amount for REVENTE_LEA type)
    prix_vente_base = Column(Float, nullable=False)                # Base selling price
    poids_grammes = Column(Float, nullable=False, default=0)       # Weight in grams
    temps_impression_minutes = Column(Integer, nullable=False, default=0)  # Print time in minutes
    personalisable = Column(Boolean, default=False)                 # Whether product can be personalized
    gestion_stock = Column(Boolean, default=False)                  # Whether stock management is enabled for this product
    alerte_stock = Column(Integer, default=0)                      # Stock alert threshold (alert when stock <= this value)
    observations = Column(Text)
    liens_tinkercad = Column(Text)  # Tinkercad design URLs, one per line
    fichiers_3mf = Column(Text)     # 3MF file names (relative to the per-machine 3MF folder), one per line
    actif = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey('users.id'))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    order_lines = relationship("OrderLine", back_populates="product")
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<Product {self.nom}>"

    @property
    def temps_impression_heures(self):
        """Get print time in hours"""
        return round(self.temps_impression_minutes / 60, 2)

    @property
    def liens_tinkercad_list(self):
        """Tinkercad design URLs as a list"""
        return self.liens_tinkercad.splitlines() if self.liens_tinkercad else []

    @property
    def fichiers_3mf_list(self):
        """3MF file names as a list"""
        return self.fichiers_3mf.splitlines() if self.fichiers_3mf else []


class Color(Base):
    """Colors (filament) table"""
    __tablename__ = 'colors'
    
    id = Column(Integer, primary_key=True)
    nom = Column(String(100), nullable=False)
    nom_original = Column(String(100))  # Original name from manufacturer
    code_hex = Column(String(7))  # e.g., #FF5733
    marque = Column(String(100))  # Brand name
    type_filament = Column(Enum(FilamentType, values_callable=lambda x: [e.value for e in x]), nullable=False)  # Type of filament
    stock_grammes = Column(Float, nullable=False, default=0)
    cout_par_kg = Column(Float, nullable=False, default=0)
    seuil_alerte = Column(Float, nullable=False, default=500)  # Low stock alert threshold
    actif = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    order_lines = relationship("OrderLine", back_populates="color")
    stock_movements = relationship("StockMovement", back_populates="color")
    
    def __repr__(self):
        return f"<Color {self.nom} ({self.stock_grammes}g)>"
    
    @property
    def is_low_stock(self):
        """Check if stock is below alert threshold"""
        return self.stock_grammes < self.seuil_alerte
    
    @property
    def stock_kg(self):
        """Get stock in kg"""
        return round(self.stock_grammes / 1000, 2)


class Printer(Base):
    """3D Printers table"""
    __tablename__ = 'printers'
    
    id = Column(Integer, primary_key=True)
    nom = Column(String(100), nullable=False)
    modele = Column(String(100))
    statut = Column(Enum(PrinterStatus, values_callable=lambda x: [e.value for e in x]), nullable=False, default=PrinterStatus.DISPONIBLE)
    observations = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    order_lines = relationship("OrderLine", back_populates="printer")
    
    def __repr__(self):
        return f"<Printer {self.nom} - {self.statut.value}>"


class Order(Base):
    """Orders (Commandes) table"""
    __tablename__ = 'orders'
    
    id = Column(Integer, primary_key=True)
    numero_commande = Column(String(50), unique=True, nullable=False)
    client_id = Column(Integer, ForeignKey('clients.id'), nullable=False)
    date_commande = Column(DateTime, default=datetime.utcnow, nullable=False)
    date_livraison_prevue = Column(DateTime)
    statut = Column(Enum(OrderStatus, values_callable=lambda x: [e.value for e in x]), nullable=False, default=OrderStatus.EN_ATTENTE)
    mode_livraison = Column(Enum(DeliveryMode, values_callable=lambda x: [e.value for e in x]), nullable=False, default=DeliveryMode.LIVRAISON)
    adresse_livraison = Column(Text)  # Selected delivery address
    observations = Column(Text)
    invoice_path = Column(String(500))  # Path to generated invoice PDF
    ne_pas_emballer = Column(Boolean, nullable=False, default=False)
    date_pret_livraison = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey('users.id'))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(Integer, ForeignKey('users.id'))
    
    # Relationships
    client = relationship("Client", back_populates="orders")
    order_lines = relationship("OrderLine", back_populates="order", cascade="all, delete-orphan")
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    
    def __repr__(self):
        return f"<Order {self.numero_commande} - {self.statut.value}>"
    
    @property
    def total_prix_vente(self):
        """Calculate total selling price"""
        return sum(line.prix_vente_total for line in self.order_lines)
    
    @property
    def total_benefice(self):
        """Calculate total profit"""
        return sum(line.benefice_total for line in self.order_lines)
    
    @property
    def total_a_reverser_david(self):
        """Calculate total amount to give back to David"""
        return sum(line.montant_a_reverser_david_total for line in self.order_lines)


class OrderLine(Base):
    """Order lines (Lignes de commande) table"""
    __tablename__ = 'order_lines'
    
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    color_id = Column(Integer, ForeignKey('colors.id'), nullable=False)
    quantite = Column(Integer, nullable=False, default=1)
    
    # Pricing
    prix_vente_unitaire = Column(Float, nullable=False)  # Unit selling price
    remise_pourcent = Column(Float, default=0)           # Discount percentage
    prix_vente_final = Column(Float, nullable=False)     # Final price after discount
    
    # Financial calculations
    benefice_calcule = Column(Float, nullable=False, default=0)           # Calculated profit
    montant_a_reverser_david = Column(Float, nullable=False, default=0)   # Amount to give back to David per unit
    
    # Note: Reversement tracking removed in v2.3.0
    # Balance system: Total to pay = Sum(montant_a_reverser_david) - Sum(reversements)
    
    # Personalization
    personnalisation = Column(Text)  # Personalization text (e.g., name, custom text)
    
    # Observations
    observations = Column(Text)
    
    # Fabrication status
    statut_fabrication = Column(Enum(FabricationStatus, values_callable=lambda x: [e.value for e in x]), nullable=False, default=FabricationStatus.FILE_ATTENTE)
    printer_id = Column(Integer, ForeignKey('printers.id'))
    date_debut_impression = Column(DateTime)
    date_fin_impression = Column(DateTime)
    date_livraison_lea = Column(DateTime)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    order = relationship("Order", back_populates="order_lines")
    product = relationship("Product", back_populates="order_lines")
    color = relationship("Color", back_populates="order_lines")
    printer = relationship("Printer", back_populates="order_lines")
    stock_movements = relationship("StockMovement", back_populates="order_line")
    
    def __repr__(self):
        return f"<OrderLine {self.order.numero_commande} - {self.product.nom}>"
    
    @property
    def prix_vente_total(self):
        """Calculate total selling price (quantity * final price)"""
        return self.prix_vente_final * self.quantite
    
    @property
    def benefice_total(self):
        """Calculate total profit"""
        return self.benefice_calcule * self.quantite
    
    @property
    def montant_a_reverser_david_total(self):
        """Calculate total amount to give back to David"""
        return self.montant_a_reverser_david * self.quantite
    
    @property
    def poids_total_grammes(self):
        """Calculate total weight"""
        return self.product.poids_grammes * self.quantite
    
    @property
    def temps_impression_total_minutes(self):
        """Calculate total print time"""
        return self.product.temps_impression_minutes * self.quantite


class Reversement(Base):
    """
    Reversements (payments to David) table
    
    Balance system (v2.3.0+):
    - This table is a simple payment journal
    - Each entry represents a payment made to David
    - Outstanding balance = Total calculated from order_lines - Sum(reversements)
    - Payments are NOT linked to specific order lines
    """
    __tablename__ = 'reversements'
    
    id = Column(Integer, primary_key=True)
    montant = Column(Float, nullable=False)  # Renamed from montant_total for clarity
    date_reversement = Column(DateTime, default=datetime.utcnow, nullable=False)
    commentaire = Column(Text)  # Renamed from observations for clarity
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey('users.id'))
    
    # Relationships
    creator = relationship("User", foreign_keys=[created_by])
    
    def __repr__(self):
        return f"<Reversement {self.montant}₪ - {self.date_reversement.date()}>"


class StockMovement(Base):
    """Stock movements (filament) table"""
    __tablename__ = 'stock_movements'
    
    id = Column(Integer, primary_key=True)
    color_id = Column(Integer, ForeignKey('colors.id'), nullable=False)
    type = Column(Enum(StockMovementType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    quantite_grammes = Column(Float, nullable=False)  # Positive for entry, negative for exit
    order_line_id = Column(Integer, ForeignKey('order_lines.id'))  # If related to production
    date = Column(DateTime, default=datetime.utcnow, nullable=False)
    observations = Column(Text)
    created_by = Column(Integer, ForeignKey('users.id'))
    
    # Relationships
    color = relationship("Color", back_populates="stock_movements")
    order_line = relationship("OrderLine", back_populates="stock_movements")
    creator = relationship("User", foreign_keys=[created_by])
    
    def __repr__(self):
        return f"<StockMovement {self.type.value} {self.quantite_grammes}g - {self.color.nom}>"


class FinishedGoodsStock(Base):
    """Finished goods stock table - Tracks pre-printed products"""
    __tablename__ = 'finished_goods_stock'
    
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    color_id = Column(Integer, ForeignKey('colors.id'), nullable=False)
    quantite_disponible = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    product = relationship("Product")
    color = relationship("Color")
    
    def __repr__(self):
        return f"<FinishedGoodsStock {self.product.nom} - {self.color.nom} ({self.quantite_disponible})>"


class FinishedGoodsMovement(Base):
    """Finished goods movements table - History of stock changes"""
    __tablename__ = 'finished_goods_movements'
    
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    color_id = Column(Integer, ForeignKey('colors.id'), nullable=False)
    type = Column(Enum(FinishedGoodsMovementType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    quantite = Column(Integer, nullable=False)  # Always positive, type indicates direction
    order_line_id = Column(Integer, ForeignKey('order_lines.id'))  # If SORTIE related to order
    date = Column(DateTime, default=datetime.utcnow, nullable=False)
    observations = Column(Text)
    created_by = Column(Integer, ForeignKey('users.id'))
    
    # Relationships
    product = relationship("Product")
    color = relationship("Color")
    order_line = relationship("OrderLine")
    creator = relationship("User", foreign_keys=[created_by])
    
    def __repr__(self):
        return f"<FinishedGoodsMovement {self.type.value} {self.quantite}x {self.product.nom}>"


from sqlalchemy import Column, String, Float, Integer, ForeignKey, DateTime, Text, Numeric, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship # <-- AJOUT IMPORTANT POUR LA RELATION
from sqlalchemy.sql import func
import uuid
from database import Base
from datetime import datetime
from zoneinfo import ZoneInfo

class Agence(Base):
    __tablename__ = "agences"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nom_agence = Column(String, unique=True, nullable=False)
    ville = Column(String, nullable=False)
    adresse = Column(String, nullable=True) # <-- À AJOUTER
    telephone = Column(String, nullable=True) # <-- À AJOUTER

    zones_magasin = relationship("MagasinZone", back_populates="agence", cascade="all, delete-orphan")

class Colis(Base):
    __tablename__ = "colis"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tracking_code = Column(String(20), unique=True, nullable=False)
    expediteur_nom = Column(String(150), nullable=False)
    expediteur_tel = Column(String(20), nullable=False)
    expediteur_email = Column(String(150), nullable=True)
    destinataire_nom = Column(String(150), nullable=False)
    destinataire_tel = Column(String(20), nullable=False)
    destinataire_email = Column(String(150), nullable=True)
    
    nature_contenu = Column(Text, nullable=False) # Résumé global (ex: "Effets personnels")
    poids_kg = Column(Numeric(10, 2), default=0.00) # Poids total combiné
    nombre_pieces = Column(Integer, default=1)
    prix_transport = Column(Numeric(10, 2), nullable=False)
    id_agence_depart = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=True)
    id_agence_destination = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=True)
    statut = Column(String(50), default="Reçu") # Devient un résumé de situation
    qr_code_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())

    updated_at = Column(DateTime, default=func.now(), onupdate=func.now()) # <-- AJOUTEZ CETTE LIGNE
    
    # Relation un-à-plusieurs vers les pièces
    items = relationship("ColisItem", back_populates="colis_principal", cascade="all, delete-orphan")


class ColisItem(Base):
    __tablename__ = "colis_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    colis_id = Column(UUID(as_uuid=True), ForeignKey("colis.id"), nullable=False)
    sub_tracking_code = Column(String(255), unique=True, nullable=False)
    nature_contenu = Column(Text, nullable=False) # Contenu précis de CE colis (ex: "Télévision")
    poids_kg = Column(Float, default=0.0)
    qr_code_data = Column(Text, nullable=True)
    statut = Column(String(50), default="Reçu") # Individuel : Reçu, Embarqué, Arrivé, Livré
    
    # CORRECTION : Remplacement de bus_id par voyage_id
    voyage_id = Column(UUID(as_uuid=True), ForeignKey("voyages.id"), nullable=True)
    
    # Nouvelle colonne pour tracer précisément l'agent local qui fait la réception
    id_agent_reception = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    
    # Dans la classe ColisItem :
    created_at = Column(DateTime, default=lambda: datetime.now(ZoneInfo("Africa/Lubumbashi")))

    # Relations
    colis_principal = relationship("Colis", back_populates="items")
    voyage_associe = relationship("Voyage") # Permet de remonter facilement au bus/chauffeur du trajet
    
    # Relation pour accéder directement aux informations de l'agent de réception si besoin
    agent_reception = relationship("Agent", foreign_keys=[id_agent_reception])
    id_emplacement = Column(UUID(as_uuid=True), ForeignKey("magasin_emplacements.id"), nullable=True)
    emplacement = relationship("MagasinEmplacement", back_populates="colis_items")

class Agent(Base):
    __tablename__ = "agents"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nom_complet = Column(String(150), nullable=False)
    username = Column(String(50), nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    id_agence = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=False)
    role = Column(String(20), default="agent")
    created_at = Column(DateTime, default=datetime.utcnow)

class SMSQueue(Base):
    __tablename__ = "sms_queue"
    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String)
    message = Column(Text)
    status = Column(String, default="pending")
    operator = Column(String, nullable=True)

# TA TABLE SQL VOYAGE (SQLAlchemy)
# class Voyage(Base):
#     __tablename__ = "voyages"

#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
#     id_bus = Column(UUID(as_uuid=True), ForeignKey("bus.id"), nullable=False)
#     id_chauffeur = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
#     id_agence_depart = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=False)
#     id_agence_destination = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=False)
#     date_depart = Column(DateTime, nullable=False)
#     statut = Column(String(20), default="en_preparation")
class Voyage(Base):
    __tablename__ = "voyages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_ligne = Column(UUID(as_uuid=True), ForeignKey("lignes.id"), nullable=False)
    id_bus = Column(UUID(as_uuid=True), ForeignKey("bus.id"), nullable=False)
    id_chauffeur = Column(UUID(as_uuid=True), nullable=True)  # Reste à NULL si pas encore assigné
    id_vrai_chauffeur = Column(UUID(as_uuid=True), nullable=True)
    date_depart = Column(DateTime, nullable=False)
    date_arrivee_prevue = Column(DateTime, nullable=True)
    statut = Column(String(20), default="en_preparation")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relations magiques d'SQLAlchemy
    ligne = relationship("Ligne", backref="voyages")
    bus = relationship("Bus")


class Bus(Base):
    __tablename__ = "bus"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    numero_plaque = Column(String(20), nullable=False, unique=True)
    modele = Column(String(50), nullable=True)
    capacite_colis_kg = Column(Numeric(10, 2), nullable=True)
    capacite_passagers = Column(Integer, default=0, nullable=True)
    statut = Column(String(20), default="disponible")
    created_at = Column(DateTime, default=func.now())

    id_agence_actuelle = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=True)

# class Ligne(Base):
#     __tablename__ = "lignes"

#     id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
#     id_agence_depart = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=False)
#     id_agence_destination = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=False)
#     prix_ticket_passager = Column(Float, nullable=False)
#     prix_fret_par_kg = Column(Float, nullable=False)
#     created_at = Column(DateTime(timezone=True), server_default=func.now())

#     # Relations pour récupérer les informations de l'agence associée
#     agence_depart = relationship("Agence", foreign_keys=[id_agence_depart])
#     agence_destination = relationship("Agence", foreign_keys=[id_agence_destination])

class Ligne(Base):
    __tablename__ = "lignes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_agence_depart = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=False)
    id_agence_destination = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=False)
    
    # --- CONFIGURATION DES PRIX FLEXIBLES (FC / USD) ---
    prix_ticket_passager = Column(Numeric(10, 2), nullable=False)
    devise_ticket = Column(String(3), default="FC") # "FC" ou "USD"
    
    prix_fret_par_kg = Column(Numeric(10, 2), nullable=False)
    devise_fret = Column(String(3), default="FC") # "FC" ou "USD"
    
    # Sécurité cadenas : Permet de savoir si le prix est verrouillé côté admin
    is_locked = Column(Boolean, default=True) 
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations pour récupérer les informations de l'agence associée
    agence_depart = relationship("Agence", foreign_keys=[id_agence_depart])
    agence_destination = relationship("Agence", foreign_keys=[id_agence_destination])


class MagasinZone(Base):
    __tablename__ = "magasin_zones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_agence = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=False)
    code_zone = Column(String(50), nullable=False) # Ex: 'ZONE-A'
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    agence = relationship("Agence", back_populates="zones_magasin")
    racks = relationship("MagasinRack", back_populates="zone", cascade="all, delete-orphan")


class MagasinRack(Base):
    __tablename__ = "magasin_racks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_zone = Column(UUID(as_uuid=True), ForeignKey("magasin_zones.id", ondelete="CASCADE"), nullable=False)
    code_rack = Column(String(50), nullable=False) # Ex: 'RACK-01' ou 'AA'
    nombre_sections = Column(Integer, nullable=False) # Ex: 6
    hauteur_max_lettre = Column(String(1), nullable=False) # Ex: 'F'
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    zone = relationship("MagasinZone", back_populates="racks")
    emplacements = relationship("MagasinEmplacement", back_populates="rack", cascade="all, delete-orphan")


class MagasinEmplacement(Base):
    __tablename__ = "magasin_emplacements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_rack = Column(UUID(as_uuid=True), ForeignKey("magasin_racks.id", ondelete="CASCADE"), nullable=False)
    code_emplacement = Column(String(100), nullable=False, unique=True) # Ex: 'ZONE-A-RACK-01-03-B'
    section_index = Column(String(10), nullable=False) # Ex: '03'
    niveau_index = Column(String(10), nullable=False) # Ex: 'B'
    poids_max_kg = Column(Float, default=50.0)
    statut = Column(String(20), default="disponible") # 'disponible', 'occupé'
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    rack = relationship("MagasinRack", back_populates="emplacements")
    # Relation inverse vers les colis qui occupent cette place
    colis_items = relationship("ColisItem", back_populates="emplacement")


class Chauffeur(Base):
    __tablename__ = "chauffeurs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nom_complet = Column(String(150), nullable=False)
    telephone = Column(String(20), nullable=False)
    numero_permis = Column(String(50), nullable=True)
    statut = Column(String(20), default="disponible") # disponible, en_voyage, maintenance
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 1. Agence d'attachement administrative
    id_agence = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=True)
    
    # 2. Localisation physique actuelle (La fameuse colonne ajoutée)
    id_agence_actuelle = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=True)

    # --- RELATIONS EXPLICITES (L'anti-panique pour SQLAlchemy) ---
    
    # Lie la relation .agence à la clé id_agence
    agence = relationship("Agence", foreign_keys=[id_agence])
    
    # Optionnel mais super propre : permet de savoir instantanément où il est physiquement en Python
    agence_actuelle = relationship("Agence", foreign_keys=[id_agence_actuelle])


class Billet(Base):
    __tablename__ = "billets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_numero = Column(String(50), unique=True, nullable=False) # Ex: CC-458921
    
    # Relations clés
    id_voyage = Column(UUID(as_uuid=True), ForeignKey("voyages.id"), nullable=False)
    id_agent_emetteur = Column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False)
    id_agence_emission = Column(UUID(as_uuid=True), ForeignKey("agences.id"), nullable=False)
    
    # Infos Passager
    nom_passager = Column(String(150), nullable=False)
    telephone_passager = Column(String(20), nullable=True)
    
    # Finance & Transaction
    montant_paye = Column(Numeric(10, 2), nullable=False)
    devise = Column(String(3), nullable=False) # "FC" ou "USD" fige la devise à l'achat
    mode_paiement = Column(String(50), default="especes") # especes, mobile_money, carte
    
    # Statut du billet
    statut = Column(String(30), default="valide") # valide, annule, utilise
    
    qr_code_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relations magiques SQLAlchemy
    voyage = relationship("Voyage", backref="billets")
    agent = relationship("Agent")
    agence = relationship("Agence", foreign_keys=[id_agence_emission])
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List # <-- AJOUT DE List POUR LE PANIER
from uuid import UUID
from datetime import datetime

class LoginSchema(BaseModel):
    username: str
    password: str

class AgentResponse(BaseModel):
    agent_id: str
    agent_name: str
    agence_id: Optional[str] = None  # <--- ICI: On accepte str OU None (valeur par défaut)
    agence_nom: str
    agence_ville: str
    role: str

    class Config:
        from_attributes = True

# 🔥 NOUVEAU SCHEMA : Représente une pièce dans le panier du formulaire
class ColisItemBase(BaseModel):
    nature_contenu: str
    poids_kg: float

class ColisItemCreate(ColisItemBase):
    pass

class ColisItemResponse(ColisItemBase):
    id: UUID
    sub_tracking_code: str
    statut: str
    qr_code_data: str
    voyage_id: Optional[UUID] = None # <-- Modifié ici (à la place de bus_id)

    class Config:
        from_attributes = True


# MISE À JOUR : Le schéma principal accepte désormais la liste des colis
class ColisBase(BaseModel):
    expediteur_nom: str
    expediteur_email: EmailStr
    expediteur_tel: str
    destinataire_nom: str
    destinataire_email: EmailStr
    destinataire_tel: str
    
    # On garde ces deux champs pour le résumé global du reçu en BDD
    nature_contenu: str 
    poids_kg: float
    
    nombre_pieces: Optional[int] = 1
    prix_transport: float
    id_agence_destination: UUID 
    agence_depart_id: UUID  
    
    # 🔥 LE PANIER MULTI-COLIS INJECTÉ ICI
    colis_items: List[ColisItemCreate] = []

class ColisCreate(ColisBase):
    pass

# Schéma de retour incluant le reçu et ses pièces jointes
class ColisResponse(ColisBase):
    id: UUID
    tracking_code: str
    statut: str
    qr_code_data: str
    items: List[ColisItemResponse] = [] # Retourne la liste des pièces avec leurs codes respectifs

    class Config:
        from_attributes = True

class Agence(BaseModel):
    id: UUID
    nom_agence: str
    ville: str
    adresse: Optional[str] = None # <-- À AJOUTER (gère le fait que ça puisse être NULL)
    telephone: Optional[str] = None # <-- À AJOUTER

    class Config:
        from_attributes = True

# TON SCHÉMA DE VALIDATION (Pydantic)


class AssignerAgentRequest(BaseModel):
    id_agent: UUID

# À vérifier dans tes schémas si tu en utilises un ici :
class RequestReception(BaseModel):
    tracking_code: str
    agent_id: str 



class BusCreate(BaseModel):
    numero_plaque: str
    modele: Optional[str] = None
    capacite_colis_kg: Optional[float] = None
    capacite_passagers: Optional[int] = 0  # <--- AJOUT ICI
    statut: Optional[str] = "disponible"

class BusResponse(BaseModel):
    id: UUID
    numero_plaque: str
    modele: Optional[str]
    capacite_colis_kg: Optional[float]
    capacite_passagers: int  # <--- AJOUT ICI
    statut: str
    created_at: datetime

    class Config:
        from_attributes = True



class LigneBase(BaseModel):
    id_agence_depart: UUID
    id_agence_destination: UUID
    prix_ticket_passager: float
    prix_fret_par_kg: float

class LigneCreate(LigneBase):
    pass

# Schémas imbriqués légers pour la réponse
class AgenceLigneMin(BaseModel):
    nom_agence: str
    ville: str
    class Config:
        from_attributes = True

class LigneResponse(BaseModel):
    id: UUID
    id_agence_depart: UUID
    id_agence_destination: UUID
    prix_ticket_passager: float
    prix_fret_par_kg: float
    created_at: datetime
    
    # 🔥 AJOUT DES NOUVELLES COLONNES EN OPTIONNEL POUR ÉVITER LE BLOCAGE PYDANTIC
    devise_ticket: Optional[str] = "FC"
    devise_fret: Optional[str] = "FC"
    is_locked: Optional[bool] = True
    
    # Tes relations d'origine qui vont refonctionner
    agence_depart: AgenceLigneMin
    agence_destination: AgenceLigneMin

    class Config:
        from_attributes = True



class VoyageBase(BaseModel):
    id_ligne: UUID
    id_bus: UUID
    id_chauffeur: Optional[UUID] = None
    date_depart: datetime
    date_arrivee_prevue: Optional[datetime] = None
    statut: Optional[str] = "en_preparation"

class VoyageCreate(VoyageBase):
    pass

# Schéma de réponse enrichi pour le tableau de bord
class VoyageResponse(BaseModel):
    id: UUID
    id_ligne: UUID
    id_bus: UUID
    id_chauffeur: Optional[UUID]
    date_depart: datetime
    date_arrivee_prevue: Optional[datetime]
    statut: str
    created_at: datetime
    
    # On réutilise les schémas de réponse des entités liées
    ligne: LigneResponse  
    bus: BusResponse      # Assure-toi que ton schéma de Bus s'appelle bien comme ça

    class Config:
        from_attributes = True


class ScanColisInput(BaseModel):
    sub_tracking_code: str



# --- SCHEMAS POUR LES ZONES ---
class ZoneCreate(BaseModel):
    code_zone: str = Field(..., example="ZONE-A", description="Code ou nom de l'allée/zone")
    description: Optional[str] = None

class ZoneResponse(BaseModel):
    id: UUID
    id_agence: UUID
    code_zone: str
    description: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

# --- SCHEMAS POUR LES RACKS (ÉTAGÈRES) ---
class RackCreate(BaseModel):
    code_rack: str = Field(..., example="RACK-01", description="Nom ou numéro de l'étagère")
    nombre_sections: int = Field(..., ge=1, example=6, description="Nombre de colonnes/sections verticales")
    hauteur_max_lettre: str = Field(..., max_length=1, example="F", description="Lettre max pour la hauteur (A à Z)")

class RackResponse(BaseModel):
    id: UUID
    id_zone: UUID
    code_rack: str
    nombre_sections: int
    hauteur_max_lettre: str
    created_at: datetime

    class Config:
        from_attributes = True

# --- SCHEMAS POUR LES EMPLACEMENTS (CASES) ---
class EmplacementResponse(BaseModel):
    id: UUID
    id_rack: UUID
    code_emplacement: str
    section_index: str
    niveau_index: str
    poids_max_kg: float
    statut: str

    class Config:
        from_attributes = True

# 1. On définit le schéma des données attendues dans le corps du POST
class ConfirmationEmplacementRequest(BaseModel):
    sub_tracking_code: str
    emplacement_code: str


from decimal import Decimal

# 1. Le schéma de la Ligne qui contient les prix
class LigneDansVoyageSchema(BaseModel):
    id: UUID
    prix_ticket_passager: Decimal
    devise_ticket: str
    id_agence_depart: UUID

    class Config:
        from_attributes = True

# 2. Le schéma du Voyage qui inclut la ligne ci-dessus
class VoyageResponseSchema(BaseModel):
    id: UUID
    id_ligne: UUID
    id_bus: UUID
    date_depart: datetime
    statut: str
    
    # C'est cette ligne qui fait la magie ! Elle va chercher la relation SQLAlchemy
    ligne: LigneDansVoyageSchema 

    class Config:
        from_attributes = True
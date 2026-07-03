from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
import database, models 
from decimal import Decimal

router = APIRouter(
    prefix="/admin",
    tags=["Administration Centrale"]
)

# --- SCHÉMAS PYDANTIC ---
class AgentCreate(BaseModel):
    nom_complet: str
    username: str
    id_agence: uuid.UUID
    role: str # 'admin', 'chef_agence', 'agent_billetterie', 'agent_colis', 'agent'

class AgentResponse(BaseModel):
    id: uuid.UUID
    nom_complet: str
    username: str
    id_agence: Optional[uuid.UUID] = None  # Reçoit proprement les valeurs NULL si nécessaire
    role: str

    class Config:
        from_attributes = True

class AgenceResponse(BaseModel):
    id: uuid.UUID
    nom_agence: str
    ville: str

    class Config:
        from_attributes = True


# --- ROUTES ---

# Liste de toutes les agences (pour alimenter le menu déroulant du formulaire)
@router.get("/agences", response_model=List[AgenceResponse])
def get_agences(db: Session = Depends(database.get_db)):
    return db.query(models.Agence).all()


# Liste de tous les agents
@router.get("/agents", response_model=List[AgentResponse])
def get_agents(db: Session = Depends(database.get_db)):
    return db.query(models.Agent).all()


# Création d'un agent avec le vrai hash Bcrypt par défaut pour '123456'
# ... reste du fichier (schémas, GET routes, etc.) ...

# Création d'un agent avec le vrai hash Bcrypt par défaut pour '123456'
@router.post("/agents", response_model=AgentResponse)
def create_agent(agent_data: AgentCreate, db: Session = Depends(database.get_db)):
    # 1. Vérifier si le nom d'utilisateur est déjà pris
    existing = db.query(models.Agent).filter(models.Agent.username == agent_data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ce nom d'utilisateur est déjà utilisé.")
    
    # 2. AJOUT ICI : Importation locale pour casser la boucle circulaire
    from main import pwd_context
    
    # 3. Génération du vrai hash Bcrypt via passlib pour le mot de passe "123456"
    vrai_hash_bcrypt = pwd_context.hash("123456")
    
    # 4. Insertion propre de l'agent dans la base de données
    nouvel_agent = models.Agent(
        nom_complet=agent_data.nom_complet,
        username=agent_data.username,
        password_hash=vrai_hash_bcrypt,
        id_agence=agent_data.id_agence,
        role=agent_data.role
    )
    
    db.add(nouvel_agent)
    db.commit()
    db.refresh(nouvel_agent)
    return nouvel_agent


# --- SCHÉMA DE CONFIGURATION DES TARIFS ---
from pydantic import BaseModel, Field, field_validator
from decimal import Decimal

class LigneResponseSchema(BaseModel):
    id: uuid.UUID
    id_agence_depart: uuid.UUID
    id_agence_destination: uuid.UUID
    prix_ticket_passager: Decimal
    devise_ticket: str = "FC"
    prix_fret_par_kg: Decimal
    devise_fret: str = "FC"
    is_locked: bool
    
    # Prise en charge des deux structures pour éviter tout conflit d'affichage Front
    id_agence_depart_nom: Optional[str] = None
    id_agence_destination_nom: Optional[str] = None

    class Config:
        from_attributes = True


class ConfigurerTarifLigneSchema(BaseModel):
    prix_ticket_passager: Decimal = Field(..., ge=0)
    devise_ticket: str = Field("FC", max_length=3)

    @field_validator('devise_ticket')
    @classmethod
    def valider_devise(cls, v: str) -> str:
        if not v:
            return "FC"
        devise_nettoyee = str(v).upper().strip()
        if devise_nettoyee not in ["FC", "USD"]:
            raise ValueError("Devise non supportée. Utilisez uniquement 'FC' ou 'USD'.")
        return devise_nettoyee  # L'erreur "Ellipsis (...)" a été supprimée ici !


# --- ROUTE 1 : LECTURE DES LIGNES (Extraction sécurisée pour ton tableau) ---
@router.get("/lignes", response_model=List[LigneResponseSchema], status_code=status.HTTP_200_OK)
def lister_lignes(db: Session = Depends(database.get_db)):
    """
    Retourne la liste complète des lignes logistiques.
    Injecte à la fois les objets imbriqués et les propriétés plates lues par React.
    """
    lignes_orm = db.query(models.Ligne).all()
    
    lignes_valides = []
    for ligne in lignes_orm:
        nom_depart = ligne.agence_depart.nom_agence if ligne.agence_depart else "Gare Départ"
        nom_dest = ligne.agence_destination.nom_agence if ligne.agence_destination else "Gare Arrivée"
        
        ligne_dict = {
            "id": ligne.id,
            "id_agence_depart": ligne.id_agence_depart,
            "id_agence_destination": ligne.id_agence_destination,
            "prix_ticket_passager": ligne.prix_ticket_passager,
            "devise_ticket": ligne.devise_ticket if ligne.devise_ticket else "FC",
            "prix_fret_par_kg": ligne.prix_fret_par_kg,
            "devise_fret": ligne.devise_fret if ligne.devise_fret else "FC",
            "is_locked": ligne.is_locked,
            
            # Alimentation des champs attendus par ton code React :
            "id_agence_depart_nom": nom_depart,
            "id_agence_destination_nom": nom_dest
        }
        lignes_valides.append(ligne_dict)
        
    return lignes_valides


# --- ROUTE 2 : CONFIGURATION DU TARIF (Correction du bug de type) ---
@router.patch("/lignes/{id_ligne}/tarif", status_code=status.HTTP_200_OK)
def configurer_tarif_ligne(
    id_ligne: uuid.UUID,
    tarif_data: ConfigurerTarifLigneSchema,
    db: Session = Depends(database.get_db)
):
    """
    Met à jour le prix du ticket et la devise d'une ligne après validation stricte.
    """
    ligne = db.query(models.Ligne).filter(models.Ligne.id == id_ligne).first()
    if not ligne:
        raise HTTPException(status_code=404, detail="Ligne logistique introuvable.")
    
    # Application directe des valeurs validées sans effets de bord
    ligne.prix_ticket_passager = tarif_data.prix_ticket_passager
    ligne.devise_ticket = tarif_data.devise_ticket
    
    db.commit()
    db.refresh(ligne)
    
    nom_depart = ligne.agence_depart.nom_agence if ligne.agence_depart else "Gare Départ"
    nom_dest = ligne.agence_destination.nom_agence if ligne.agence_destination else "Gare Arrivée"
    
    return {
        "message": f"Tarification de la ligne [{nom_depart} → {nom_dest}] mise à jour avec succès.",
        "prix_ticket_passager": float(ligne.prix_ticket_passager),
        "devise_ticket": ligne.devise_ticket
    }
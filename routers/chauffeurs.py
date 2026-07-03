from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, aliased
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import uuid
import database, models

# --- SCHÉMAS PYDANTIC ---
class ChauffeurCreateSchema(BaseModel):
    nom_complet: str
    telephone: str
    numero_permis: Optional[str] = None
    id_agence: uuid.UUID

class ChauffeurResponseSchema(BaseModel):
    id: uuid.UUID
    nom_complet: str
    telephone: str
    numero_permis: Optional[str]
    id_agence: uuid.UUID
    id_agence_actuelle: Optional[uuid.UUID]
    statut: str
    created_at: datetime

    class Config:
        from_attributes = True

# --- ROUTER CHAUFFEURS ---
router_chauffeurs = APIRouter(prefix="/chauffeurs", tags=["Gestion Chauffeurs"])

@router_chauffeurs.post("", response_model=ChauffeurResponseSchema)
def ajouter_chauffeur(data: ChauffeurCreateSchema, db: Session = Depends(database.get_db)):
    donnees_chauffeur = data.dict()
    donnees_chauffeur["id_agence_actuelle"] = data.id_agence 
    
    nouveau = models.Chauffeur(**donnees_chauffeur)
    db.add(nouveau)
    db.commit()
    db.refresh(nouveau)
    return nouveau

@router_chauffeurs.get("/agence/{id_agence}", response_model=List[ChauffeurResponseSchema])
def lister_chauffeurs_agence(id_agence: uuid.UUID, db: Session = Depends(database.get_db)):
    return db.query(models.Chauffeur).filter(models.Chauffeur.id_agence == id_agence).all()

@router_chauffeurs.get("/{id_chauffeur}/local-status")
def obtenir_etat_chauffeur(id_chauffeur: uuid.UUID, db: Session = Depends(database.get_db)):
    """
    Route intelligente : Récupère l'activité, le nom de l'agence d'attachement ET le nom de l'agence actuelle
    """
    try:
        # Création d'alias pour joindre la table Agence deux fois différemment
        agence_attachement = aliased(models.Agence)
        agence_actuelle = aliased(models.Agence)

        # 1. Récupération des noms des deux agences par jointure SQL
        chauffeur_gares = db.query(
            models.Chauffeur.id,
            agence_attachement.ville.label("nom_agence_attachement"),
            agence_actuelle.ville.label("nom_agence_actuelle")
        ).join(
            agence_attachement, models.Chauffeur.id_agence == agence_attachement.id
        ).outerjoin(
            agence_actuelle, models.Chauffeur.id_agence_actuelle == agence_actuelle.id
        ).filter(
            models.Chauffeur.id == id_chauffeur
        ).first()

        nom_attachement = chauffeur_gares.nom_agence_attachement if chauffeur_gares else "Inconnue"
        nom_actuelle = chauffeur_gares.nom_agence_actuelle if chauffeur_gares else "Inconnue"

        # 2. Recherche du voyage actif (colonne id_vrai_chauffeur)
        voyage_actif = db.query(models.Voyage).filter(
            models.Voyage.id_vrai_chauffeur == id_chauffeur,
            models.Voyage.statut.in_(["en_preparation", "en_cours"])
        ).first()
        
        bus_plaque = None
        if voyage_actif:
            bus = db.query(models.Bus).filter(models.Bus.id == voyage_actif.id_bus).first()
            bus_plaque = bus.numero_plaque if bus else None

        # 3. Historique des voyages clos
        historique = db.query(models.Voyage).filter(
            models.Voyage.id_vrai_chauffeur == id_chauffeur,
            models.Voyage.statut == "arrive"
        ).order_by(models.Voyage.date_depart.desc()).limit(5).all()

        return {
            "en_voyage": voyage_actif is not None,
            "voyage_id": voyage_actif.id if voyage_actif else None,
            "numero_plaque_actuel": bus_plaque,
            "historique_voyages_clos_count": len(historique),
            "nom_agence_attachement": nom_attachement, # Gare administrative d'origine
            "nom_agence_actuelle": nom_actuelle       # Position physique en temps réel 📍
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur d'analyse logistique interne : {str(e)}"
        )
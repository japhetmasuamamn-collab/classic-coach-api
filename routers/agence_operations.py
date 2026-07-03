from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import uuid
import database, models

router = APIRouter(
    prefix="/agence-operations",
    tags=["Opérations Agence Locale"]
)

# --- SCHÉMAS PYDANTIC ---
class VoyageCreateSchema(BaseModel):
    id_bus: uuid.UUID
    id_chauffeur: Optional[uuid.UUID] = None
    id_ligne: uuid.UUID
    date_depart: datetime

class BusResponseSchema(BaseModel):
    id: uuid.UUID
    numero_plaque: str
    modele: Optional[str]
    capacite_passagers: Optional[int]
    statut: str
    id_agence_actuelle: Optional[uuid.UUID]

    class Config:
        from_attributes = True

# --- SCHÉMAS PYDANTIC ---

class VoyageResponseSchema(BaseModel):
    id: uuid.UUID
    id_bus: uuid.UUID
    id_chauffeur: Optional[uuid.UUID] = None
    id_vrai_chauffeur: Optional[uuid.UUID] = None
    id_ligne: uuid.UUID
    date_depart: datetime
    statut: str
    nom_ligne: Optional[str] = None 
    id_agence_depart: Optional[uuid.UUID] = None
    id_agence_destination: Optional[uuid.UUID] = None
    numero_plaque: Optional[str] = None
    nom_chauffeur: Optional[str] = None  # 👈 AJOUTEZ CETTE LIGNE ICI

    class Config:
        from_attributes = True

# --- ROUTE 1 : RÉCUPÉRER TOUS LES BUS PHYSIQUEMENT PRÉSENTS ET OPÉRATIONNELS ---
@router.get("/bus-disponibles", response_model=List[BusResponseSchema])
def get_bus_disponibles(id_agence: uuid.UUID, db: Session = Depends(database.get_db)):
    """
    Récupère les bus qui sont en bon état mécanique et présents à l'agence.
    Le Front se chargera de calculer leur occupation à partir des voyages actifs.
    """
    bus_locaux = db.query(models.Bus).filter(
        models.Bus.statut == 'disponible',  # 'disponible' ici = en bon état mécanique
        models.Bus.id_agence_actuelle == id_agence
    ).all()
    return bus_locaux


# --- ROUTE 2 : PLANIFIER UN VOYAGE (SANS ALTÉRER LE STATUT MÉCANIQUE DU BUS) ---
@router.post("/voyages", response_model=VoyageResponseSchema)
def planifier_voyage(voyage_data: VoyageCreateSchema, id_agence: uuid.UUID, db: Session = Depends(database.get_db)):
    # 1. Vérification de la ligne
    ligne = db.query(models.Ligne).filter(models.Ligne.id == voyage_data.id_ligne).first()
    if not ligne:
        raise HTTPException(status_code=404, detail="Ligne introuvable.")
    
    if ligne.id_agence_depart != id_agence:
        raise HTTPException(
            status_code=400, 
            detail="Cette ligne ne démarre pas depuis votre agence locale."
        )

    # 2. Vérification du bus
    bus = db.query(models.Bus).filter(models.Bus.id == voyage_data.id_bus).first()
    if not bus:
        raise HTTPException(status_code=404, detail="Bus introuvable.")
        
    if bus.id_agence_actuelle != id_agence:
        raise HTTPException(status_code=400, detail="Le bus sélectionné n'est pas dans votre dépôt.")
        
    if bus.statut != 'disponible':
        raise HTTPException(status_code=400, detail="Ce véhicule est actuellement immobilisé (en panne/maintenance).")

    # 3. Vérification logistique : Est-ce que ce bus a déjà un voyage actif ?
    voyage_actif = db.query(models.Voyage).filter(
        models.Voyage.id_bus == voyage_data.id_bus,
        models.Voyage.statut.in_(['en_preparation', 'en_cours'])
    ).first()
    
    if voyage_actif:
        raise HTTPException(
            status_code=400, 
            detail="Ce bus est déjà affecté à un voyage actif (au quai ou sur route)."
        )

    # 4. Création du voyage
    nouveau_voyage = models.Voyage(
        id_ligne=voyage_data.id_ligne,
        id_bus=voyage_data.id_bus,
        id_chauffeur=voyage_data.id_chauffeur,
        date_depart=voyage_data.date_depart,
        statut="en_preparation"
    )

    # ⚡ IMPORTANT : On ne touche plus à bus.statut ! Il reste 'disponible' (opérationnel).
    db.add(nouveau_voyage)
    db.commit()
    db.refresh(nouveau_voyage)
    
    nouveau_voyage.id_agence_depart = ligne.id_agence_depart
    nouveau_voyage.id_agence_destination = ligne.id_agence_destination
    nouveau_voyage.numero_plaque = bus.numero_plaque
    
    return nouveau_voyage

# --- ROUTE 3 : CHANGER LE STATUT D'UN VOYAGE ---
@router.put("/voyages/{id_voyage}/statut")
def mettre_a_jour_statut_voyage(id_voyage: uuid.UUID, nouveau_statut: str, id_agence: uuid.UUID, db: Session = Depends(database.get_db)):
    voyage = db.query(models.Voyage).filter(models.Voyage.id == id_voyage).first()
    if not voyage:
        raise HTTPException(status_code=404, detail="Voyage introuvable.")

    ligne = db.query(models.Ligne).filter(models.Ligne.id == voyage.id_ligne).first()
    if not ligne:
        raise HTTPException(status_code=404, detail="Ligne logistique introuvable.")

    bus = db.query(models.Bus).filter(models.Bus.id == voyage.id_bus).first()

    if nouveau_statut == "en_cours":
        if ligne.id_agence_depart != id_agence:
            raise HTTPException(
                status_code=403, 
                detail="Interdit : Seule l'agence de DEPART peut lancer ce voyage."
            )
        voyage.statut = "en_cours"
        if bus:
            bus.statut = "en_route"

    elif nouveau_statut == "arrive":
        if ligne.id_agence_destination != id_agence:
            raise HTTPException(status_code=403, detail="Interdit...")
        
        voyage.statut = "arrive"
        
        if bus:
            bus.statut = "disponible"
            bus.id_agence_actuelle = id_agence 
            db.add(bus)
            
        if voyage.id_vrai_chauffeur:
            chauffeur = db.query(models.Chauffeur).filter(models.Chauffeur.id == voyage.id_vrai_chauffeur).first()
            if chauffeur:
                chauffeur.id_agence_actuelle = id_agence
                db.add(chauffeur)

    # 🛠️ FIX : Le commit doit être ICI, à la sortie des conditions, 
    # pour valider AUSSI le statut "en_cours"
    db.commit()
    
    if bus:
        db.refresh(bus)
    db.refresh(voyage)
        
    return {"message": f"Voyage mis à jour avec succès au statut : {nouveau_statut}"}

# --- ROUTE 4 : RÉCUPÉRER TOUS LES VOYAGES CONCERNANT MON AGENCE ---
@router.get("/voyages", response_model=List[VoyageResponseSchema])
def get_voyages_agence(id_agence: uuid.UUID, db: Session = Depends(database.get_db)):
    voyages = db.query(models.Voyage).join(
        models.Ligne, models.Voyage.id_ligne == models.Ligne.id
    ).filter(
        (models.Ligne.id_agence_depart == id_agence) | (models.Ligne.id_agence_destination == id_agence)
    ).all()
    
    resultats = []
    for v in voyages:
        ligne = v.ligne
        nom_ligne = "Itinéraire Inter-Agence"
        id_dep = None
        id_dest = None
        
        if ligne:
            id_dep = ligne.id_agence_depart
            id_dest = ligne.id_agence_destination
            dep = db.query(models.Agence).filter(models.Agence.id == id_dep).first()
            dest = db.query(models.Agence).filter(models.Agence.id == id_dest).first()
            if dep and dest:
                nom_ligne = f"{dep.ville} → {dest.ville}"
        
        statut_sql = getattr(v, 'statut', 'en_preparation') or 'en_preparation'
        num_plaque = v.bus.numero_plaque if v.bus else "Inconnu"
        
        # 💡 ALLER CHERCHER LE NOM DU VRAI CHAUFFEUR POUR LE FRONTEND
        nom_vrai_chauffeur = "Chauffeur Assigné"
        if hasattr(v, 'id_vrai_chauffeur') and v.id_vrai_chauffeur:
            chauffeur_obj = db.query(models.Chauffeur).filter(models.Chauffeur.id == v.id_vrai_chauffeur).first()
            if chauffeur_obj:
                nom_vrai_chauffeur = chauffeur_obj.nom_complet

        voyage_formate = VoyageResponseSchema(
            id=v.id,
            id_bus=v.id_bus,
            id_chauffeur=v.id_chauffeur,
            id_vrai_chauffeur=getattr(v, 'id_vrai_chauffeur', None),  # ⚡ FIX ICI : On passe la bonne colonne
            id_ligne=v.id_ligne,
            date_depart=v.date_depart,
            statut=statut_sql,
            nom_ligne=nom_ligne,
            id_agence_depart=id_dep,
            id_agence_destination=id_dest,
            numero_plaque=num_plaque,
            nom_chauffeur=nom_vrai_chauffeur # 💡 Optionnel mais recommandé (Pense à l'ajouter comme Optional[str] = None dans ton VoyageResponseSchema)
        )
        resultats.append(voyage_formate)
                
    return resultats


class AffecterChauffeurSchema(BaseModel):
    id_chauffeur: uuid.UUID

# Schéma de réponse pour le chauffeur
class ChauffeurResponseSchema(BaseModel):
    id: uuid.UUID
    nom_complet: str
    telephone: Optional[str]
    id_agence_actuelle: Optional[uuid.UUID]

    class Config:
        from_attributes = True

@router.get("/chauffeurs-disponibles", response_model=List[ChauffeurResponseSchema])
def get_chauffeurs_disponibles(id_agence: uuid.UUID, db: Session = Depends(database.get_db)):
    # 1. Trouver les IDs des chauffeurs actuellement occupés
    # ⚠️ MODIFICATION ICI : On sélectionne 'id_vrai_chauffeur'
    chauffeurs_occupes = db.query(models.Voyage.id_vrai_chauffeur).filter(
        models.Voyage.id_vrai_chauffeur.isnot(None), # 👈 ICI
        models.Voyage.statut.in_(['en_preparation', 'en_cours', 'incident_en_route'])
    ).subquery()

    # 2. Récupérer les chauffeurs de l'agence libres
    chauffeurs_libres = db.query(models.Chauffeur).filter(
        models.Chauffeur.id_agence_actuelle == id_agence,
        models.Chauffeur.id.notin_(chauffeurs_occupes)
    ).all()

    return chauffeurs_libres

@router.put("/voyages/{id_voyage}/chauffeur")
def attribuer_chauffeur_voyage(
    id_voyage: uuid.UUID, 
    payload: AffecterChauffeurSchema, 
    id_agence: uuid.UUID, 
    db: Session = Depends(database.get_db)
):
    # 1. Vérification du voyage
    voyage = db.query(models.Voyage).filter(models.Voyage.id == id_voyage).first()
    if not voyage:
        raise HTTPException(status_code=404, detail="Voyage introuvable.")

    if voyage.statut != "en_preparation":
        raise HTTPException(status_code=400, detail="Impossible de modifier le chauffeur...")

    # 2. Vérification des droits de l'agence
    ligne = db.query(models.Ligne).filter(models.Ligne.id == voyage.id_ligne).first()
    if not ligne or ligne.id_agence_depart != id_agence:
        raise HTTPException(status_code=403, detail="Interdit...")

    # 3. Vérification du chauffeur
    chauffeur = db.query(models.Chauffeur).filter(models.Chauffeur.id == payload.id_chauffeur).first()
    if not chauffeur:
        raise HTTPException(status_code=404, detail="Chauffeur introuvable.")

    if chauffeur.id_agence_actuelle != id_agence:
        raise HTTPException(status_code=400, detail="Ce chauffeur n'est pas dans votre agence.")

    # 4. Vérification du double engagement 
    # ⚠️ MODIFICATION ICI : On cherche dans 'id_vrai_chauffeur'
    double_engagement = db.query(models.Voyage).filter(
        models.Voyage.id_vrai_chauffeur == payload.id_chauffeur, # 👈 ICI
        models.Voyage.statut.in_(['en_preparation', 'en_cours', 'incident_en_route']),
        models.Voyage.id != id_voyage
    ).first()

    if double_engagement:
        raise HTTPException(status_code=400, detail="Ce conducteur est déjà assigné ailleurs.")

    # 5. Application du chauffeur
    # ⚠️ MODIFICATION ICI : On enregistre dans la bonne colonne !
    voyage.id_vrai_chauffeur = payload.id_chauffeur # 👈 ICI
    db.commit()
    db.refresh(voyage)

    return {"message": "Chauffeur affecté avec succès au voyage."}
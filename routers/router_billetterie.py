from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
import uuid
from uuid import UUID
import database, models

router = APIRouter(
    prefix="/agence-billetterie",
    tags=["Billetterie et Vente Guichet"]
)

# --- SCHÉMAS PYDANTIC ---

class BilletCreateSchema(BaseModel):
    id_voyage: uuid.UUID
    nom_passager: str = Field(..., max_length=150)
    telephone_passager: Optional[str] = Field(None, max_length=20)
    montant_paye: Decimal
    devise: str = Field("FC", max_length=3)  # 'FC' ou 'USD'
    mode_paiement: str = Field("especes", max_length=50) # 'especes', 'mobile_money', 'carte'

class BilletResponseSchema(BaseModel):
    id: uuid.UUID
    ticket_numero: str
    id_voyage: uuid.UUID
    id_agent_emetteur: uuid.UUID
    id_agence_emission: uuid.UUID
    nom_passager: str
    telephone_passager: Optional[str]
    montant_paye: Decimal
    devise: str
    mode_paiement: str
    statut: str
    created_at: datetime

    class Config:
        from_attributes = True


# --- ROUTE 1 : ÉMETTRE ET ENCAISSER UN BILLET (GUICHET LOCALE) ---
@router.post("/billets", response_model=BilletResponseSchema, status_code=status.HTTP_201_CREATED)
def emettre_billet_guichet(
    billet_data: BilletCreateSchema, 
    id_agence: uuid.UUID, 
    id_agent: uuid.UUID,  # Passé par le front ou extrait du token
    db: Session = Depends(database.get_db)
):
    """
    Émet un billet thermique pour un voyage au départ de l'agence connectée.
    Bloque la vente si le bus est plein ou si le voyage n'appartient pas à la gare.
    """
    # 1. Vérification du voyage
    voyage = db.query(models.Voyage).filter(models.Voyage.id == billet_data.id_voyage).first()
    if not voyage:
        raise HTTPException(status_code=404, detail="Voyage sélectionné introuvable.")
    
    if voyage.statut != "en_preparation":
        raise HTTPException(
            status_code=400, 
            detail=f"Impossible de vendre un billet. Le voyage est déjà : {voyage.statut}."
        )

    # 2. Sécurité Multi-Agence : Vérifier que la ligne part bien de CETTE agence locale
    ligne = db.query(models.Ligne).filter(models.Ligne.id == voyage.id_ligne).first()
    if not ligne or ligne.id_agence_depart != id_agence:
        raise HTTPException(
            status_code=403, 
            detail="Interdit : Vous ne pouvez pas émettre de billet pour un voyage démarrant d'une autre gare."
        )

    # 3. Vérification de la capacité physique du Bus (Anti-Surbooking)
    bus = db.query(models.Bus).filter(models.Bus.id == voyage.id_bus).first()
    capacite_max = bus.capacite_passagers if (bus and bus.capacite_passagers) else 0
    
    # Compter les billets valides déjà vendus pour ce voyage
    # billets_vendus_count = db.query(models.Billet).filter(
    #     models.Billet.id_voyage == voyage.id_voyage,
    #     models.Billet.statut == "valide"
    # ).count()
    billets_vendus_count = db.query(models.Billet).filter(
        models.Billet.id_voyage == voyage.id,
        models.Billet.statut == "valide"
    ).count()

    if billets_vendus_count >= capacite_max:
        raise HTTPException(
            status_code=400, 
            detail=f"Désolé, ce bus est complet ({billets_vendus_count}/{capacite_max} places occupées)."
        )

    # 4. Génération d'un numéro de ticket unique propre (Ex: CC-741258)
    ticket_fictif = f"CC-{uuid.uuid4().hex[:6].upper()}"

    # 5. Création et enregistrement comptable figé (Même si les prix de la ligne changent demain)
    nouveau_billet = models.Billet(
        ticket_numero=ticket_fictif,
        id_voyage=billet_data.id_voyage,
        id_agent_emetteur=id_agent,
        id_agence_emission=id_agence,
        nom_passager=billet_data.nom_passager,
        telephone_passager=billet_data.telephone_passager,
        montant_paye=billet_data.montant_paye,
        devise=billet_data.devise.upper(),
        mode_paiement=billet_data.mode_paiement,
        statut="valide"
    )

    db.add(nouveau_billet)
    db.commit()
    db.refresh(nouveau_billet)

    return nouveau_billet


# --- ROUTE 2 : STATISTIQUES FINANCIÈRES DU JOUR DE L'AGENCE ---
@router.get("/stats-recettes-jour")
def get_stats_recettes_agence_locale(id_agence: uuid.UUID, db: Session = Depends(database.get_db)):
    """
    Calcule les recettes totales accumulées aujourd'hui par la gare connectée, 
    séparées proprement par Devise (FC et USD).
    """
    aujourdhui = datetime.utcnow().date()

    # Récupérer les billets vendus aujourd'hui dans cette agence
    billets_du_jour = db.query(models.Billet).filter(
        models.Billet.id_agence_emission == id_agence,
        models.Billet.statut == "valide",
        models.Billet.created_at >= aujourdhui
    ).all()

    recettes_fc = sum(b.montant_paye for b in billets_du_jour if b.devise == "FC")
    recettes_usd = sum(b.montant_paye for b in billets_du_jour if b.devise == "USD")
    total_billets = len(billets_du_jour)

    return {
        "date": aujourdhui.isoformat(),
        "total_passagers_jour": total_billets,
        "recettes": {
            "FC": recettes_fc,
            "USD": recettes_usd
        }
    }


# --- ROUTE 3 : ANNULER UN BILLET (AVEC DROIT DE RECOURS) ---
@router.put("/billets/{id_billet}/annuler")
def annuler_billet_guichet(id_billet: uuid.UUID, id_agence: uuid.UUID, db: Session = Depends(database.get_db)):
    """
    Annule un ticket de voyage. Seule l'agence émettrice a le pouvoir de l'annuler.
    """
    billet = db.query(models.Billet).filter(models.Billet.id == id_billet).first()
    if not billet:
        raise HTTPException(status_code=404, detail="Billet introuvable.")

    if billet.id_agence_emission != id_agence:
        raise HTTPException(
            status_code=403, 
            detail="Action refusée : Vous ne pouvez annuler qu'un billet émis par votre propre guichet."
        )

    if billet.statut == "utilise":
        raise HTTPException(status_code=400, detail="Impossible d'annuler un billet déjà consommé à l'embarquement.")

    billet.statut = "annule"
    db.commit()

    return {"message": f"Le ticket numéro {billet.ticket_numero} a été annulé avec succès."}


# --- ROUTE 4 : RECUPERER LES VOYAGES AVEC LES COMPOSANTS DE LEURS LIGNES (PRIX/DEVISE) ---
@router.get("/voyages-actifs")
def get_voyages_actifs(id_agence: uuid.UUID, db: Session = Depends(database.get_db)):
    """
    Récupère les voyages actifs et prépare un affichage intelligent 
    adapté à la gare de connexion de l'agent.
    """
    resultats = db.query(models.Voyage, models.Ligne).\
        join(models.Ligne, models.Voyage.id_ligne == models.Ligne.id).\
        filter(models.Voyage.statut == "en_preparation").all()
    
    voyages_avec_prix = []
    for voyage, ligne in resultats:
        plaque = voyage.bus.numero_plaque if voyage.bus else "Non spécifié"

        # Noms des agences via les relations
        nom_depart = ligne.agence_depart.nom_agence if ligne.agence_depart else "Départ"
        nom_destination = ligne.agence_destination.nom_agence if ligne.agence_destination else "Destination"

        # Itinéraire complet officiel (parfait pour le ticket de caisse)
        nom_ligne_complet = f"{nom_depart} ➔ {nom_destination}"

        # OPTIMISATION UX : Si le voyage part de la gare de l'agent connecté
        if str(ligne.id_agence_depart) == str(id_agence):
            affichage_guichet = f"Vers {nom_destination}"
        else:
            # Fallback au cas où l'admin regarde les voyages d'une autre gare
            affichage_guichet = nom_ligne_complet

        voyages_avec_prix.append({
            "id": str(voyage.id),
            "id_agence_depart": str(ligne.id_agence_depart),
            "date_depart": voyage.date_depart.isoformat() if isinstance(voyage.date_depart, datetime) else voyage.date_depart,
            "statut": voyage.statut,
            "numero_plaque": plaque,
            "nom_ligne": nom_ligne_complet, # Reste intact pour l'impression
            "affichage_guichet": affichage_guichet, # Nouveau champ épuré pour le select
            "prix_ticket_passager": float(ligne.prix_ticket_passager),
            "devise_ticket": ligne.devise_ticket
        })
        
    return voyages_avec_prix
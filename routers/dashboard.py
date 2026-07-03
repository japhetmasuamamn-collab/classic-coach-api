from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date
import uuid
import database, models

router_dashboard = APIRouter(prefix="/agence-dashboard", tags=["Dashboard Statistiques"])

@router_dashboard.get("/statistiques/{id_agence}")
def obtenir_statistiques_journalieres(id_agence: uuid.UUID, db: Session = Depends(database.get_db)):
    """
    Calcule en temps réel les recettes du jour (séparées par devise) 
    et le nombre total de passagers (billets valides émis aujourd'hui) pour une agence donnée.
    """
    try:
        # 1. Requête pour sommer les montants groupés par devise pour AUJOURD'HUI et pour CETTE agence
        recettes_requete = db.query(
            models.Billet.devise,
            func.sum(models.Billet.montant_paye).label("total")
        ).filter(
            models.Billet.id_agence_emission == id_agence,
            models.Billet.statut == "valide",
            func.date(models.Billet.created_at) == func.current_date()
        ).group_by(models.Billet.devise).all()

        # Structuration propre des devises (valeur par défaut à 0)
        recettes = {"FC": 0.0, "USD": 0.0}
        for ligne in recettes_requete:
            if ligne.devise in recettes:
                recettes[ligne.devise] = float(ligne.total)

        # 2. Compter le nombre de billets uniques (Passagers) vendus aujourd'hui par cette agence
        total_passagers = db.query(func.count(models.Billet.id)).filter(
            models.Billet.id_agence_emission == id_agence,
            models.Billet.statut == "valide",
            func.date(models.Billet.created_at) == func.current_date()
        ).scalar() or 0

        return {
            "recettes": recettes,  # Renvoie un objet: {"FC": X, "USD": Y}
            "billets_vendus": total_passagers
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors du calcul des indicateurs financiers : {str(e)}"
        )
    

import os
import uuid
from uuid import UUID  # <--- AJOUTE ÇA ICI
import qrcode
from fastapi import Request, FastAPI, Depends, BackgroundTasks, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import func
from email.message import EmailMessage
import aiosmtplib
from dotenv import load_dotenv
from typing import List
from passlib.context import CryptContext
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional
from routers import admin, agence_operations
from routers import chauffeurs, dashboard
from routers import router_documents, router_billetterie
import os

# Import de tes fichiers locaux
import models
import schemas
import database

from zoneinfo import ZoneInfo


# On définit la Timezone de Lubumbashi / Kolwezi (UTC+2)
RDC_TIMEZONE = ZoneInfo("Africa/Lubumbashi")



# Dans main.py, après les imports
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


print("===== MAIN DEMARRE =====")

app = FastAPI(title="Classic Coach API")

load_dotenv("config.env")

SERVER_IP = os.getenv("SERVER_IP")
# SERVER_IP = "192.168.11.108"
#SERVER_IP = "10.163.132.142"

# 1. Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router)

app.include_router(agence_operations.router)
app.include_router(chauffeurs.router_chauffeurs)
app.include_router(router_documents.router)
app.include_router(router_billetterie.router)
app.include_router(dashboard.router_dashboard)

# 2. Dossier static pour les QR Codes
if not os.path.exists("static/qrcodes"):
    os.makedirs("static/qrcodes")
app.mount("/static", StaticFiles(directory="static"), name="static")

# 3. Création des tables
print("===== CREATION DES TABLES =====")
print("===== TABLES CREEES =====")
models.Base.metadata.create_all(bind=database.engine)



# --- FONCTION D'ENVOI D'EMAIL ---
async def send_notification_email(email_to: str, subject: str, message_text: str, tracking_code: str):
    try:
        # On crée le lien qui pointe vers ton React (port 5173 par défaut)
        # L'utilisateur cliquera dessus pour voir l'interface
        link = f"http://{SERVER_IP}:5173/suivi/{tracking_code}"
        
        full_body = f"{message_text}\n\nSuivez votre colis ici : {link}"

        message = EmailMessage()
        message["From"] = os.getenv("SMTP_USER")
        message["To"] = email_to
        message["Subject"] = subject
        message.set_content(full_body)

        await aiosmtplib.send(
            message,
            hostname=os.getenv("SMTP_HOST", "smtp.gmail.com"),
            port=int(os.getenv("SMTP_PORT", 587)),
            username=os.getenv("SMTP_USER"),
            password=os.getenv("SMTP_PASSWORD"),
            use_tls=False,
            start_tls=True,
        )
    except Exception as e:
        print(f"Erreur email: {e}")

def ajouter_sms(db, numero, message, tracking_code):
    try:
        operateur = detecter_operateur(numero)

        link = f"http://{SERVER_IP}:5173/suivi/{tracking_code}"
        full_message = f"{message}\nSuivi: {link}"

        sms = models.SMSQueue(
            phone=numero,
            message=full_message,
            status="pending",
            operator=operateur   # 👈 AJOUT
        )

        db.add(sms)
        db.commit()
        db.refresh(sms)

    except Exception as e:
        print("Erreur ajout SMS:", e)

# --- ROUTES SMS QUEUE ---

@app.get("/sms/pending")
def get_pending_sms(db: Session = Depends(database.get_db)):
    sms_list = db.query(models.SMSQueue).filter(models.SMSQueue.status == "pending").all()
    return sms_list

@app.post("/sms/done")
def mark_sms_done(sms_id: int, db: Session = Depends(database.get_db)):
    sms = db.query(models.SMSQueue).filter(models.SMSQueue.id == sms_id).first()
    if sms:
        sms.status = "sent"
        db.commit()
    return {"status": "ok"}

# --- ROUTES AGENCES ---
def detecter_operateur(numero: str) -> str:
    numero = numero.strip().replace(" ", "")

    # Normalisation RDC 🇨🇩
    if numero.startswith("+243"):
        numero = "0" + numero[4:]
    elif numero.startswith("243"):
        numero = "0" + numero[3:]

    # Détection
    if numero.startswith(("081", "082", "083")):
        return "vodacom"

    elif numero.startswith(("097", "098", "099")):
        return "airtel"

    elif numero.startswith(("089", "084", "085")):
        return "orange"

    elif numero.startswith(("090",)):
        return "africell"

    return "unknown"



@app.get("/agences", response_model=List[schemas.Agence]) # Assure-toi d'avoir schemas.Agence
def liste_agences(db: Session = Depends(database.get_db)):
    return db.query(models.Agence).all()

# --- ROUTE D'ENREGISTREMENT ---

@app.post("/colis/enregistrer")
async def enregistrer_colis(
    colis_in: schemas.ColisCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(database.get_db)
):
    # 1. Extraction propre des données
    colis_data = colis_in.model_dump()
    id_depart = colis_data.get("agence_depart_id")
    items_du_panier = colis_in.colis_items 

    if not id_depart:
        raise HTTPException(status_code=400, detail="L'ID de l'agence de départ est manquant.")

    # 2. Générer le code de suivi Maître
    tracking_code_principal = f"CC-{uuid.uuid4().hex[:6].upper()}"

    # 3. Préparation des champs du modèle Colis
    champs_colis_principal = {}
    for k, v in colis_data.items():
        if k not in ["colis_items", "agence_depart_id"]:
            champs_colis_principal[k] = None if v == "" else v
    
    nouveau_colis_maitre = models.Colis(
        **champs_colis_principal,
        id_agence_depart=id_depart, 
        tracking_code=tracking_code_principal,
        qr_code_data=f"https://classic-coach.app/track/{tracking_code_principal}",
        statut="Reçu"
    )

    # 4. Sauvegarde du reçu principal
    try:
        db.add(nouveau_colis_maitre)
        db.commit()
        db.refresh(nouveau_colis_maitre)
    except Exception as e:
        db.rollback()
        print(f"❌ Erreur DB lors du reçu principal: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur lors de l'enregistrement du reçu principal: {str(e)}")

    # 5. Générer le QR Code du reçu principal
    qr_img_principal = qrcode.make(nouveau_colis_maitre.qr_code_data)
    qr_filename_principal = f"{tracking_code_principal}.png"
    qr_path_principal = os.path.join("static", "qrcodes", qr_filename_principal)
    os.makedirs(os.path.dirname(qr_path_principal), exist_ok=True)
    qr_img_principal.save(qr_path_principal)

    # 6. Traitement et préparation des pièces
    liste_items_enregistres = []
    
    try:
        for index, item_data in enumerate(items_du_panier, start=1):
            sub_tracking_code = f"{tracking_code_principal}-{index}"
            qr_sub_filename = f"{sub_tracking_code}.png"
            qr_sub_path = os.path.join("static", "qrcodes", qr_sub_filename)
            
            nouvelle_piece = models.ColisItem(
                colis_id=nouveau_colis_maitre.id,
                sub_tracking_code=sub_tracking_code,
                nature_contenu=item_data.nature_contenu,
                poids_kg=item_data.poids_kg,
                qr_code_data=f"https://classic-coach.app/track/item/{sub_tracking_code}",
                statut="Reçu",
                voyage_id=None 
            )
            
            db.add(nouvelle_piece)
            
            # Génération du QR Code individuel
            qr_img_sub = qrcode.make(nouvelle_piece.qr_code_data)
            qr_img_sub.save(qr_sub_path)
            
            liste_items_enregistres.append({
                "sub_tracking_code": sub_tracking_code,
                "nature": nouvelle_piece.nature_contenu,
                BASE_URL = "https://classic-coach-api.onrender.com"

                qr_url_item = f"{BASE_URL}/static/qrcodes/{qr_sub_filename}"
            })
            
        db.commit()
        
    except Exception as e:
        db.rollback()
        print(f"❌ Erreur lors de l'enregistrement des pièces : {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de l'enregistrement des pièces du colis.")

    # =========================================================================
    # --- TA FONCTION UTILITAIRE DE NOTIFICATION INSPIRÉE DU PREMIER CODE ---
    # =========================================================================
    def notifier_enregistrement(sujet, message_dest, message_exp, sms_dest=None, sms_exp=None):
        # 1. Gestion des Emails
        if nouveau_colis_maitre.destinataire_email and nouveau_colis_maitre.destinataire_email.strip():
            try:
                background_tasks.add_task(send_notification_email, nouveau_colis_maitre.destinataire_email, sujet, message_dest, nouveau_colis_maitre.tracking_code)
                print(f"📩 Tâche d'email ajoutée pour le Destinataire : {nouveau_colis_maitre.destinataire_email}")
            except Exception as mail_err:
                print(f"⚠️ Erreur Background Task Email Destinataire: {mail_err}")

        if nouveau_colis_maitre.expediteur_email and nouveau_colis_maitre.expediteur_email.strip():
            try:
                background_tasks.add_task(send_notification_email, nouveau_colis_maitre.expediteur_email, sujet, message_exp, nouveau_colis_maitre.tracking_code)
                print(f"📩 Tâche d'email ajoutée pour l'Expéditeur : {nouveau_colis_maitre.expediteur_email}")
            except Exception as mail_err:
                print(f"⚠️ Erreur Background Task Email Expéditeur: {mail_err}")

        # 2. Gestion des SMS
        if sms_dest and nouveau_colis_maitre.destinataire_tel and nouveau_colis_maitre.destinataire_tel.strip():
            try:
                ajouter_sms(db, nouveau_colis_maitre.destinataire_tel, sms_dest, nouveau_colis_maitre.tracking_code)
                print(f"📱 SMS Destinataire planifié pour {nouveau_colis_maitre.destinataire_tel}")
            except Exception as sms_err:
                print(f"⚠️ Erreur lors de l'ajout du SMS destinataire: {sms_err}")

        if sms_exp and nouveau_colis_maitre.expediteur_tel and nouveau_colis_maitre.expediteur_tel.strip():
            try:
                ajouter_sms(db, nouveau_colis_maitre.expediteur_tel, sms_exp, nouveau_colis_maitre.tracking_code)
                print(f"📱 SMS Expéditeur planifié pour {nouveau_colis_maitre.expediteur_tel}")
            except Exception as sms_err:
                print(f"⚠️ Erreur lors de l'ajout du SMS expéditeur: {sms_err}")

    # =========================================================================
    # --- DÉCLENCHEMENT DES NOTIFICATIONS (Remplace les étapes 7 & 8) ---
    # =========================================================================
    total_pieces = len(items_du_panier)
    notifier_enregistrement(
        sujet="Confirmation d'enregistrement de votre colis - Classic Coach",
        message_dest=f"Bonjour {nouveau_colis_maitre.destinataire_nom}, un colis de {total_pieces} pièce(s) envoyé par {nouveau_colis_maitre.expediteur_nom} a été enregistré en votre nom. Code de suivi global : {tracking_code_principal}.",
        message_exp=f"Bonjour {nouveau_colis_maitre.expediteur_nom}, votre envoi comprenant {total_pieces} pièce(s) (Code de suivi global : {tracking_code_principal}) a bien été enregistré. Merci de votre confiance !",
        sms_dest=f"Bonjour {nouveau_colis_maitre.destinataire_nom}, {total_pieces} colis envoyé(s) par {nouveau_colis_maitre.expediteur_nom} à votre attention. Suivi global: {tracking_code_principal}.",
        sms_exp=f"Bonjour {nouveau_colis_maitre.expediteur_nom}, votre envoi {tracking_code_principal} ({total_pieces} pces) a bien été enregistré chez Classic Coach."
    )

    # 9. Réponse structurée (Inchangée)
    return {
        "status": "success",
        "id": nouveau_colis_maitre.id,
        "code_recu": tracking_code_principal,
        "qr_url_recu": f"http://{SERVER_IP}:8000/static/qrcodes/{qr_filename_principal}",
        "nombre_total_pieces": total_pieces,
        "colis_details": liste_items_enregistres
    }

@app.get("/colis/stats")
def get_colis_stats(id_agence: str, db: Session = Depends(database.get_db)):
    # --- VUE OPÉRATIONNELLE (Gestion physique locale) ---

    # 1. Colis déposés ici (En attente de départ)
    a_expedier = db.query(models.Colis).filter(
        models.Colis.id_agence_depart == id_agence,
        models.Colis.statut == "Reçu"
    ).count()

    # 2. Arrivages prévus (Viennent d'ailleurs vers ici)
    en_route_vers_ici = db.query(models.Colis).filter(
        models.Colis.id_agence_destination == id_agence,
        models.Colis.statut == "En transit"
    ).count()

    # 3. Au comptoir (Prêts pour le client final ici)
    en_agence_ici = db.query(models.Colis).filter(
        models.Colis.id_agence_destination == id_agence,
        models.Colis.statut == "Arrivé"
    ).count()

    # --- VUE SUIVI / PERFORMANCE (Visibilité sur le flux généré) ---

    # 4. Total expédié par cette agence (Flux complet)
    # On ajoute "Livré" pour que l'agence de départ voit aussi ses succès passés.
    suivi_envoi = db.query(models.Colis).filter(
        models.Colis.id_agence_depart == id_agence,
        models.Colis.statut.in_(["En transit", "Arrivé", "Livré"])
    ).count()

    return {
        "recu": a_expedier,              # Badge Orange
        "en_transit": en_route_vers_ici, # Badge Jaune
        "arrive": en_agence_ici,         # Badge Bleu
        "suivi_envoi": suivi_envoi,      # Flux total généré par l'agence
        "livre": db.query(models.Colis).filter(
            models.Colis.id_agence_destination == id_agence,
            models.Colis.statut == "Livré"
        ).count()
    }

@app.get("/colis/liste")
def get_colis_liste(id_agence: str, filtre: str, db: Session = Depends(database.get_db)):
    from sqlalchemy.orm import joinedload # 💡 Import local pour être sûr que ça fonctionne direct
    
    # 💡 MODIFICATION ICI : On force SQLAlchemy à charger la relation avec les sous-colis
    query = db.query(models.Colis).options(joinedload(models.Colis.items))
    
    if filtre == "recu":
        query = query.filter(models.Colis.id_agence_depart == id_agence, models.Colis.statut == "Reçu")
    elif filtre == "en_transit":
        query = query.filter(models.Colis.id_agence_destination == id_agence, models.Colis.statut == "En transit")
    elif filtre == "arrive":
        query = query.filter(models.Colis.id_agence_destination == id_agence, models.Colis.statut == "Arrivé")
    elif filtre == "livre":
        query = query.filter(models.Colis.id_agence_destination == id_agence, models.Colis.statut == "Livré")
    elif filtre == "suivi_envoi":
        # On ajoute "Livré" dans la liste ci-dessous pour que l'agence de départ 
        # voie ses colis même une fois remis au client.
        query = query.filter(
            models.Colis.id_agence_depart == id_agence, 
            models.Colis.statut.in_(["En transit", "Arrivé", "Livré"])
        )
    
    return query.order_by(models.Colis.created_at.desc()).all()


# # 1. Récupérer TOUS les colis (utilisé par l'onglet Expéditions)
# @app.get("/colis", response_model=List[schemas.ColisOut])
# def get_colis(
#     db: Session = Depends(database.get_db),
#     agence_id: str = Header(None),

#     # filtres
#     target_date: Optional[str] = Query(None),  # format YYYY-MM-DD
#     statut: Optional[str] = Query(None),
#     type_flux: Optional[str] = Query(None)  # expedition | reception
# ):
#     if not agence_id:
#         raise HTTPException(status_code=400, detail="Agence manquante")

#     query = db.query(models.Colis)

#     # 🔹 FILTRE PAR AGENCE + TYPE
#     if type_flux == "expedition":
#         query = query.filter(models.Colis.id_agence_depart == agence_id)

#     elif type_flux == "reception":
#         query = query.filter(models.Colis.id_agence_destination == agence_id)

#     else:
#         query = query.filter(
#             (models.Colis.id_agence_depart == agence_id) |
#             (models.Colis.id_agence_destination == agence_id)
#         )

#     # 🔹 FILTRE PAR DATE (corrige ton bug)
#     if target_date:
#         try:
#             date_obj = datetime.strptime(target_date, "%Y-%m-%d").date()
#         except:
#             raise HTTPException(status_code=400, detail="Format date invalide")

#         query = query.filter(
#             models.Colis.created_at >= date_obj,
#             models.Colis.created_at < date_obj + timedelta(days=1)
#         )

#     # 🔹 FILTRE PAR STATUT
#     if statut:
#         query = query.filter(models.Colis.statut == statut)

#     return query.order_by(models.Colis.created_at.desc()).all()


@app.post("/login", response_model=schemas.AgentResponse)
def login(data: schemas.LoginSchema, db: Session = Depends(database.get_db)):
    agent = db.query(models.Agent).filter(models.Agent.username == data.username).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")

    if not pwd_context.verify(data.password, agent.password_hash):
        raise HTTPException(status_code=401, detail="Mot de passe incorrect")

    # INITIALISATION DES VARIABLES PAR DÉFAUT
    agence_id = None
    agence_nom = "Direction Générale"
    agence_ville = "Central"

    # ON CHERCHE L'AGENCE UNIQUEMENT SI L'AGENT EN A UNE (Cas des agents de guichet)
    if agent.id_agence is not None:
        agence = db.query(models.Agence).filter(models.Agence.id == agent.id_agence).first()
        if not agence:
            raise HTTPException(status_code=404, detail="Agence rattachée introuvable")
        agence_id = str(agence.id)
        agence_nom = agence.nom_agence
        agence_ville = agence.ville

    return {
        "agent_id": str(agent.id),
        "agent_name": agent.nom_complet,
        "agence_id": agence_id,        # Vaudra None pour le Super Admin
        "agence_nom": agence_nom,      # "Direction Générale"
        "agence_ville": agence_ville,  # "Central"
        "role": agent.role
    }


# @app.post("/colis/scanner")
# async def scanner_colis(
#     tracking_code: str,
#     agent_id: str,
#     id_voyage: str, # NOUVEAU PARAMÈTRE REÇU DU FRONTEND
#     background_tasks: BackgroundTasks,
#     db: Session = Depends(database.get_db)
# ):
#     print("\n========== SCAN COLIS ==========")

#     # --- FONCTION UTILITAIRE ---
#     def notifier_colis(
#         sujet,
#         message_dest,
#         message_exp,
#         sms_dest=None,
#         sms_exp=None
#     ):
#         # =========================
#         # EMAIL 📧
#         # =========================
#         if colis.destinataire_email:
#             background_tasks.add_task(
#                 send_notification_email,
#                 colis.destinataire_email,
#                 sujet,
#                 message_dest, 
#                 colis.tracking_code
#             )

#         if colis.expediteur_email:
#             background_tasks.add_task(
#                 send_notification_email,
#                 colis.expediteur_email,
#                 sujet,
#                 message_exp,
#                 colis.tracking_code
#             )

#         # =========================
#         # SMS SIMPLES ⚡
#         # =========================
#         if sms_dest and colis.destinataire_tel:
#             ajouter_sms(db, colis.destinataire_tel, sms_dest, colis.tracking_code)

#         if sms_exp and colis.expediteur_tel:
#             ajouter_sms(db, colis.expediteur_tel, sms_exp, colis.tracking_code)

#     # 1. Vérifications de base (Agent et Voyage)
#     try:
#         u_agent_id = UUID(agent_id)
#     except ValueError:
#         raise HTTPException(status_code=400, detail="Format ID agent invalide")

#     # --- NOUVELLE VÉRIFICATION : Le voyage choisi existe-t-il ? ---
#     try:
#         u_voyage_id = UUID(id_voyage)
#     except ValueError:
#         raise HTTPException(status_code=400, detail="Format ID Voyage invalide")
        
#     voyage = db.query(models.Voyage).filter(models.Voyage.id == u_voyage_id).first()
#     if not voyage:
#         raise HTTPException(status_code=404, detail="Feuille de route (Voyage) introuvable.")

#     agent = db.query(models.Agent).filter(models.Agent.id == u_agent_id).first()
#     if not agent:
#         raise HTTPException(status_code=404, detail="Agent non identifié")

#     agence = db.query(models.Agence).filter(models.Agence.id == agent.id_agence).first()
#     nom_de_l_agence = agence.nom_agence if agence else "l'agence"

#     colis = db.query(models.Colis).filter(models.Colis.tracking_code == tracking_code).first()
#     if not colis:
#         raise HTTPException(status_code=404, detail="Colis introuvable")

#     # DEBUG GLOBAL
#     print(f"Tracking: {colis.tracking_code}")
#     print(f"Statut AVANT: '{colis.statut}'")
#     print(f"Email destinataire: '{colis.destinataire_email}'")
#     print(f"Email expéditeur: '{colis.expediteur_email}'")

#     # 2. Préparation des IDs
#     agent_agence_id = str(agent.id_agence).strip().lower()
#     colis_depart_id = str(colis.id_agence_depart).strip().lower()
#     colis_dest_id = str(colis.id_agence_destination).strip().lower()

#     print(f"Agent agence: {agent_agence_id}")
#     print(f"Colis départ: {colis_depart_id}")
#     print(f"Colis destination: {colis_dest_id}")

#     # --- LOGIQUE DE DÉPART (AJUSTÉE AVEC LE VOYAGE) ---
#     if agent_agence_id == colis_depart_id:
#         if colis.statut != "Reçu":
#             raise HTTPException(status_code=400, detail=f"Action impossible : déjà {colis.statut}")

#         # MUTATION INTELLIGENTE
#         colis.statut = "En transit"
#         colis.id_bus = voyage.id_bus  # Association du bus lié au voyage choisi
        
#         # Optionnel : Si tu as cette colonne dans ton modèle Colis, tu peux la décommenter :
#         # colis.id_voyage_actuel = voyage.id 

#         message = "Colis chargé : Départ validé dans le bus."

#         notifier_colis(
#             sujet="Votre colis est en route !",

#             # EMAIL (long)
#             message_dest=f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} vient de quitter l'agence de {nom_de_l_agence}.",
#             message_exp=f"Bonjour {colis.expediteur_nom}, votre colis {colis.tracking_code} est maintenant en transit depuis {nom_de_l_agence}.",

#             # SMS (court ⚡)
#             sms_dest=(
#                 f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} "
#                 f"a quitté l'agence de {nom_de_l_agence} et est en route vers sa destination.\n"
#                 f"Code de suivi: {colis.tracking_code}"
#             ),

#             sms_exp=(
#                 f"Bonjour {colis.expediteur_nom}, votre colis {colis.tracking_code} "
#                 f"a quitté l'agence de {nom_de_l_agence} et est actuellement en cours de livraison.\n"
#                 f"Code de suivi: {colis.tracking_code}"
#             )
#         )

#     # --- LOGIQUE D'ARRIVÉE (INCHANGÉE) ---
#     elif agent_agence_id == colis_dest_id:
#         if colis.statut == "Arrivé":
#             return {"status": "info", "message": "Ce colis est déjà marqué comme arrivé."}

#         if colis.statut != "En transit":
#             raise HTTPException(status_code=400, detail=f"Le colis doit être 'En transit' (actuellement: {colis.statut})")

#         colis.statut = "Arrivé"
#         message = "Arrivée validée : Colis disponible pour le client."

#         notifier_colis(
#             sujet="📍 Votre colis est disponible - Classic Coach",

#             # EMAIL (détaillé)
#             message_dest=(
#                 f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} est arrivé à l'agence de {nom_de_l_agence}.\n\n"
#                 "Prière de passer le récupérer dans les 2 prochains jours ouvrables. "
#                 "Veuillez noter qu'au-delà de ce délai de 48h, des pénalités de magasinage seront appliquées par jour de retard.\n\n"
#                 "Merci de votre confiance !"
#             ),

#             message_exp=(
#                 f"Bonjour {colis.expediteur_nom}, nous vous confirmons que votre colis {colis.tracking_code} "
#                 f"est bien arrivé à destination ({nom_de_l_agence}) et est prêt à être récupéré."
#             ),

#             # SMS ⚡ ULTRA COURT
#             sms_dest=(
#                 f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} "
#                 f"est arrivé à l'agence de {nom_de_l_agence}. Retrait sous 48h.\n"
#                 f"Code de suivi: {colis.tracking_code}"
#             ),

#             sms_exp=(
#                 f"Bonjour {colis.expediteur_nom}, votre colis {colis.tracking_code} "
#                 f"est bien arrivé à destination ({nom_de_l_agence}).\n"
#                 f"Code de suivi: {colis.tracking_code}"
#             )
#         )

#     else:
#         raise HTTPException(status_code=403, detail="Interdit : Agence non autorisée pour ce colis.")

#     db.commit()

#     print(f"Statut APRÈS: '{colis.statut}'")
#     print("========== FIN SCAN ==========\n")

#     return {"status": "success", "message": message}


@app.post("/colis/scanner")
async def scanner_colis(
    tracking_code: str,       # Reçoit soit le sub_tracking_code d'une pièce, soit le tracking_code du reçu global
    agent_id: str,
    id_voyage: str,          # L'ID du voyage actif sélectionné sur l'application frontend
    background_tasks: BackgroundTasks,
    db: Session = Depends(database.get_db)
):
    print("\n========== SCAN MULTI-COLIS (HYBRIDE PIECE / REÇU) ==========")

    # 1. Vérifications de base et conversions des UUIDs
    try:
        u_agent_id = UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format ID agent invalide")

    try:
        u_voyage_id = UUID(id_voyage)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format ID Voyage invalide")
    
    # ====================================================================
    # ÉTAPE 2 CORRIGÉE : Utiliser .id à la place de .id_voyage
    # ====================================================================
    voyage = db.query(models.Voyage).filter(models.Voyage.id == u_voyage_id).first()

    if not voyage:
        raise HTTPException(status_code=404, detail="Voyage introuvable.")

    # 🚨 NOUVEAU VERROU : On n'autorise le scan QUE si le bus est en préparation
    if voyage.statut != "en_preparation":
        raise HTTPException(
            status_code=400, 
            detail=f"Action impossible : Le chargement de ce voyage est '{voyage.statut}'. Impossible d'ajouter ou modifier des colis."
        )

    agent = db.query(models.Agent).filter(models.Agent.id == u_agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent non identifié")

    bus = db.query(models.Bus).filter(models.Bus.id == voyage.id_bus).first()
    if not bus:
        raise HTTPException(status_code=404, detail="Véhicule associé introuvable.")

    # 🛠️ ALIGNEMENT : capacite_colis_kg est un type numeric(10, 2) en SQL, conversion en float propre
    capacite_max_bus = float(bus.capacite_colis_kg) if (bus and hasattr(bus, 'capacite_colis_kg') and bus.capacite_colis_kg) else 1000.0

    agence = db.query(models.Agence).filter(models.Agence.id == agent.id_agence).first()
    nom_de_l_agence = agence.nom_agence if agence else "l'agence"

    # --- RECHERCHE HYBRIDE ---
    piece = db.query(models.ColisItem).filter(models.ColisItem.sub_tracking_code == tracking_code).first()
    colis = None

    if piece:
        colis = db.query(models.Colis).filter(models.Colis.id == piece.colis_id).first()
    else:
        colis = db.query(models.Colis).filter(models.Colis.tracking_code == tracking_code).first()

    if not colis:
        raise HTTPException(status_code=404, detail="Aucun colis ou pièce trouvé avec ce code.")

    # --- FONCTION UTILITAIRE DE NOTIFICATION ---
    def notifier_colis(sujet, message_dest, message_exp, sms_dest=None, sms_exp=None):
        if colis.destinataire_email:
            background_tasks.add_task(send_notification_email, colis.destinataire_email, sujet, message_dest, colis.tracking_code)
        if colis.expediteur_email:
            background_tasks.add_task(send_notification_email, colis.expediteur_email, sujet, message_exp, colis.tracking_code)
        if sms_dest and colis.destinataire_tel:
            ajouter_sms(db, colis.destinataire_tel, sms_dest, colis.tracking_code)
        if sms_exp and colis.expediteur_tel:
            ajouter_sms(db, colis.expediteur_tel, sms_exp, colis.tracking_code)

    # 2. Préparation des IDs d'agences pour comparaison
    if not voyage.ligne:
        raise HTTPException(status_code=400, detail="Le voyage sélectionné n'est rattaché à aucune ligne active.")

    agent_agence_id = str(agent.id_agence).strip().lower()
    colis_depart_id = str(colis.id_agence_depart).strip().lower()
    colis_dest_id = str(colis.id_agence_destination).strip().lower()
    
    voyage_depart_id = str(voyage.ligne.id_agence_depart).strip().lower()
    voyage_dest_id = str(voyage.ligne.id_agence_destination).strip().lower()

    message = ""

    # =========================================================================
    # --- LOGIQUE DE DÉPART (L'agent scanne à l'agence d'origine) ---
    # =========================================================================
    if agent_agence_id == colis_depart_id:
        
        # 🚨 VERROU COHÉRENCE VOYAGE (ORIGINE)
        if colis_depart_id != voyage_depart_id:
            raise HTTPException(
                status_code=400, 
                detail=f"Erreur d'affectation : Le voyage sélectionné (Bus {bus.numero_plaque}) ne part pas de votre agence actuelle."
            )

        # 🚨 LE VERROU DESTINATION
        if colis_dest_id != voyage_dest_id:
            agence_voyage_dest = db.query(models.Agence).filter(models.Agence.id == voyage.ligne.id_agence_destination).first()
            nom_dest_voyage = agence_voyage_dest.nom_agence if agence_voyage_dest else "une autre agence"
            
            agence_colis_dest = db.query(models.Agence).filter(models.Agence.id == colis.id_agence_destination).first()
            nom_dest_colis = agence_colis_dest.nom_agence if agence_colis_dest else "sa destination prévue"

            raise HTTPException(
                status_code=400,
                detail=f"Erreur Destination : Ce bus va vers '{nom_dest_voyage}', mais le colis est destiné à '{nom_dest_colis}'."
            )

        # ⚖️ CALCUL DU POIDS ACTUEL DANS LE BUS (En accord avec la colonne poids_kg)
        pieces_embarquees = db.query(models.ColisItem).filter(models.ColisItem.voyage_id == voyage.id).all()
        poids_actuel_bus = sum(float(p.poids_kg) if p.poids_kg else 0.0 for p in pieces_embarquees)

        # CAS 1 : L'agent a scanné le REÇU GLOBAL à l'embarquement
        if piece is None:
            toutes_les_pieces = db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).all()
            total_pieces = len(toutes_les_pieces)
            
            poids_nouveau_colis = sum(float(p.poids_kg) if p.poids_kg else 0.0 for p in toutes_les_pieces if p.voyage_id != voyage.id)

            # 🚨 VERROU SECURITE SURCHARGE
            if poids_actuel_bus + poids_nouveau_colis > capacite_max_bus:
                reste_dispo = max(0.0, capacite_max_bus - poids_actuel_bus)
                raise HTTPException(
                    status_code=400,
                    detail=f"Surcharge empêchée ! Poids requis : {poids_nouveau_colis} kg. Espace dispo dans le bus : {reste_dispo} kg (Capacité max : {capacite_max_bus} kg)."
                )
            
            for p in toutes_les_pieces:
                if p.statut in ["Reçu", "En Agence", "en_agence", "reçu"]:
                    p.statut = "En transit"
                    p.voyage_id = voyage.id
            
            colis.statut = "En transit"
            colis.id_bus = voyage.id_bus
            db.commit()
            
            notifier_colis(
                sujet="Votre colis est en route !",
                message_dest=f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} ({total_pieces} pces) vient de quitter l'agence de {nom_de_l_agence}.",
                message_exp=f"Bonjour {colis.expediteur_nom}, votre colis {colis.tracking_code} ({total_pieces} pces) est maintenant en transit depuis {nom_de_l_agence}.",
                sms_dest=f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} ({total_pieces} pces) a quitté {nom_de_l_agence} en route vers sa destination.",
                sms_exp=f"Bonjour {colis.expediteur_nom}, votre envoi {colis.tracking_code} ({total_pieces} pces) a quitté {nom_de_l_agence} et est en cours de transport."
            )
            message = f"Embarquement global validé ! Les {total_pieces} pièces ({poids_nouveau_colis} kg) sont chargées."

        # CAS 2 : L'agent a scanné une PIÈCE INDIVIDUELLE à l'embarquement
        else:
            if piece.statut != "Reçu" and "transit" in piece.statut.lower():
                raise HTTPException(status_code=400, detail=f"Cette pièce est déjà marquée comme : {piece.statut}")

            poids_piece = float(piece.poids_kg) if piece.poids_kg else 0.0

            # 🚨 VERROU SECURITE SURCHARGE PIÈCE
            if poids_actuel_bus + poids_piece > capacite_max_bus:
                reste_dispo = max(0.0, capacite_max_bus - poids_actuel_bus)
                raise HTTPException(
                    status_code=400,
                    detail=f"Surcharge empêchée ! La pièce pèse {poids_piece} kg. Espace dispo dans le bus : {reste_dispo} kg (Capacité max : {capacite_max_bus} kg)."
                )

            piece.statut = "En transit"
            piece.voyage_id = voyage.id
            db.commit()

            toutes_les_pieces = db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).all()
            total_pieces = len(toutes_les_pieces)
            pieces_en_transit = sum(1 for p in toutes_les_pieces if p.statut == "En transit")

            if pieces_en_transit == total_pieces:
                colis.statut = "En transit"
                colis.id_bus = voyage.id_bus
                
                notifier_colis(
                    sujet="Votre colis est en route !",
                    message_dest=f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} ({total_pieces} pces) vient de quitter l'agence de {nom_de_l_agence}.",
                    message_exp=f"Bonjour {colis.expediteur_nom}, votre colis {colis.tracking_code} ({total_pieces} pces) est maintenant en transit depuis {nom_de_l_agence}.",
                    sms_dest=f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} ({total_pieces} pces) a quitté {nom_de_l_agence} en route vers sa destination.",
                    sms_exp=f"Bonjour {colis.expediteur_nom}, votre envoi {colis.tracking_code} ({total_pieces} pces) a quitté {nom_de_l_agence} et est en cours de transport."
                )
                message = f"Embarquement complet ! Pièce {piece.sub_tracking_code} chargée. Le reçu global passe 'En transit'."
            else:
                colis.statut = "En transit"
                message = f"Embarquement partiel. Pièce {piece.sub_tracking_code} chargée. ({pieces_en_transit}/{total_pieces} pièces embarquées)."
            
            db.commit()

    # =========================================================================
    # --- LOGIQUE D'ARRIVÉE (L'agent scanne à l'agence de destination) ---
    # =========================================================================
    elif agent_agence_id == colis_dest_id:
        if piece is None:
            toutes_les_pieces = db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).all()
            total_pieces = len(toutes_les_pieces)
            
            for p in toutes_les_pieces:
                if p.statut == "En transit":
                    p.statut = "Arrivé"
            
            colis.statut = "Arrivé"
            db.commit()
            
            notifier_colis(
                sujet="📍 Votre colis est disponible - Classic Coach",
                message_dest=f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} est arrivé au complet ({total_pieces} pces) à l'agence de {nom_de_l_agence}.\n\nPrière de passer le récupérer sous 48h.",
                message_exp=f"Bonjour {colis.expediteur_nom}, nous vous confirmons que votre envoi {colis.tracking_code} est bien arrivé à destination ({nom_de_l_agence}).",
                sms_dest=f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} ({total_pieces} pces) est arrivé à l'agence de {nom_de_l_agence}. Retrait sous 48h.",
                sms_exp=f"Bonjour {colis.expediteur_nom}, votre envoi {colis.tracking_code} est arrivé à destination ({nom_de_l_agence})."
            )
            message = f"Réception totale validée pour le reçu {colis.tracking_code} !"

        else:
            if piece.statut == "Arrivé":
                pieces_embarquees = db.query(models.ColisItem).filter(models.ColisItem.voyage_id == voyage.id).all()
                poids_final_bus = sum(float(p.poids_kg) if p.poids_kg else 0.0 for p in pieces_embarquees)

                return {
                    "status": "info", 
                    "message": f"La pièce {piece.sub_tracking_code} est déjà marquée comme arrivée.",
                    "poids_actuel": poids_final_bus,
                    "capacite_colis_kg": capacite_max_bus,
                    "poids_restant": max(0.0, capacite_max_bus - poids_final_bus)
                }

            if piece.statut != "En transit":
                raise HTTPException(status_code=400, detail=f"La pièce doit être 'En transit' pour être réceptionnée (Actuel: {piece.statut})")

            piece.statut = "Arrivé"
            db.commit()

            toutes_les_pieces = db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).all()
            total_pieces = len(toutes_les_pieces)
            pieces_arrivees = sum(1 for p in toutes_les_pieces if p.statut == "Arrivé")

            if pieces_arrivees == total_pieces:
                colis.statut = "Arrivé"
                
                notifier_colis(
                    sujet="📍 Votre colis est disponible - Classic Coach",
                    message_dest=f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} est arrivé au complet ({total_pieces} pces) à l'agence de {nom_de_l_agence}.\n\nPrière de passer le récupérer sous 48h.",
                    message_exp=f"Bonjour {colis.expediteur_nom}, nous vous confirmons que votre envoi {colis.tracking_code} est bien arrivé à destination ({nom_de_l_agence}).",
                    sms_dest=f"Bonjour {colis.destinataire_nom}, your colis {colis.tracking_code} ({total_pieces} pces) est arrivé à l'agence de {nom_de_l_agence}. Retrait sous 48h.",
                    sms_exp=f"Bonjour {colis.expediteur_nom}, votre envoi {colis.tracking_code} est arrivé à destination ({nom_de_l_agence})."
                )
                message = f"Réception totale validée ! Les {total_pieces} pièces du reçu sont sécurisées au dépôt."
            else:
                colis.statut = "Arrivé"
                message = f"Réception partielle. Pièce {piece.sub_tracking_code} stockée. ({pieces_arrivees}/{total_pieces} arrivées)."
            
            db.commit()

    else:
        raise HTTPException(status_code=403, detail="Interdit : Votre agence n'est autorisée ni comme départ ni comme destination de ce colis.")

    # 📊 MISE À JOUR DES COMPTEURS DE FRET POUR LE RETOUR COMPOSANT UI
    pieces_embarquees = db.query(models.ColisItem).filter(models.ColisItem.voyage_id == voyage.id).all()
    poids_final_bus = sum(float(p.poids_kg) if p.poids_kg else 0.0 for p in pieces_embarquees)

    print(f"Statut final global: '{colis.statut}' | Charge Bus: {poids_final_bus}/{capacite_max_bus} kg")
    print("========== FIN SCAN ==========\n")

    return {
        "status": "success", 
        "message": message,
        "poids_actuel": poids_final_bus,
        "capacite_colis_kg": capacite_max_bus,
        "poids_restant": max(0.0, capacite_max_bus - poids_final_bus)
    }

# Petite fonction helper pour afficher le numéro de pièce proprement dans les logs
def index_piece_str(sub_code: str) -> str:
    try:
        return sub_code.split("-")[-1]
    except:
        return "1"

@app.get("/colis/dashboard-details")
def get_dashboard_details(id_agence: str, db: Session = Depends(database.get_db)):
    # --- 1. ACTIVITÉ LOCALE (Les 5 derniers mouvements) ---
    # On cherche les colis qui partent d'ici OU qui arrivent ici
    activites = db.query(models.Colis).filter(
        (models.Colis.id_agence_depart == id_agence) | 
        (models.Colis.id_agence_destination == id_agence)
    ).order_by(models.Colis.created_at.desc()).limit(5).all()

    # --- 2. FLUX HEBDOMADAIRE (7 derniers jours) ---
    stats_jours = []
    for i in range(6, -1, -1):
        date_cible = datetime.now().date() - timedelta(days=i)
        
        # Nombre de colis créés (Envois) ce jour-là pour cette agence
        count = db.query(models.Colis).filter(
            models.Colis.id_agence_depart == id_agence,
            func.date(models.Colis.created_at) == date_cible
        ).count()
        
        stats_jours.append({
            "jour": date_cible.strftime("%a"), # Ex: Mon, Tue...
            "valeur": count
        })

    return {
        "activites": [
            {
                "id": c.tracking_code,
                "type": "Départ" if str(c.id_agence_depart) == id_agence else "Arrivée",
                "client": c.expediteur_nom if str(c.id_agence_depart) == id_agence else c.destinataire_nom,
                "time": c.created_at.strftime("%H:%M"),
                "statut": c.statut
            } for c in activites
        ],
        "graphique": stats_jours
    }

@app.patch("/colis/{colis_id}/statut")
async def livrer_colis(
    colis_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(database.get_db)
):
    colis = db.query(models.Colis).filter(models.Colis.id == colis_id).first()
    
    if not colis:
        raise HTTPException(status_code=404, detail="Colis introuvable")

    if colis.statut != "Arrivé":
        raise HTTPException(
            status_code=400, 
            detail=f"Impossible de livrer : le colis est '{colis.statut}' et non 'Arrivé'."
        )

    # 1. Mise à jour du statut
    colis.statut = "Livré"
    colis.updated_at = datetime.now()

    # On prépare les données avant le commit pour être sûr qu'elles sont chargées
    sujet = "Colis livré avec succès !"
    dest_email = colis.destinataire_email
    dest_nom = colis.destinataire_nom
    exp_email = colis.expediteur_email
    exp_nom = colis.expediteur_nom
    tracking = colis.tracking_code

    # 2. Logique de notification (Comme dans ton enregistrement)
    # Email au Destinataire
    if dest_email:
        background_tasks.add_task(
            send_notification_email,
            dest_email,
            sujet,
            f"Bonjour {dest_nom}, votre colis {tracking} vous a été remis en main propre. Merci de votre confiance !",
            tracking  # <-- TRÈS IMPORTANT : le 4ème argument pour le lien de suivi
        )
    
    # Email à l'Expéditeur
    if exp_email:
        background_tasks.add_task(
            send_notification_email,
            exp_email,
            sujet,
            f"Bonjour {exp_nom}, nous vous confirmons que le colis {tracking} a été livré à {dest_nom}.",
            tracking  # <-- TRÈS IMPORTANT : le 4ème argument pour le lien de suivi
        )
    
    # =========================
    # SMS DESTINATAIRE 🔥
    # =========================
    if colis.destinataire_tel:
        ajouter_sms(
            db,
            colis.destinataire_tel,
            f"Bonjour {dest_nom}, votre colis {tracking} vous a été remis.\nCode de suivi: {tracking}",
            tracking
        )

    # =========================
    # SMS EXPEDITEUR 🔥
    # =========================
    if colis.expediteur_tel:
        ajouter_sms(
            db,
            colis.expediteur_tel,
            f"Bonjour {exp_nom}, le colis {tracking} a été livré à {dest_nom}.\nCode de suivi: {tracking}",
            tracking
        )

    db.commit()
    return {"status": "success", "message": "Colis marqué comme livré et notifications envoyées."}

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

@app.get("/public/colis/{tracking_code}")
def get_public_colis(tracking_code: str, db: Session = Depends(database.get_db)):
    # 1. Recherche hybride (Code maître ou pièce individuelle)
    piece_scanne = db.query(models.ColisItem).filter(models.ColisItem.sub_tracking_code == tracking_code).first()
    colis = None

    if piece_scanne:
        colis = db.query(models.Colis).filter(models.Colis.id == piece_scanne.colis_id).first()
    else:
        colis = db.query(models.Colis).filter(models.Colis.tracking_code == tracking_code).first()

    if not colis:
        raise HTTPException(status_code=404, detail="Numéro de suivi introuvable")

    toutes_les_pieces = db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).all()
    
    # Récupération des agences
    agence_depart = db.query(models.Agence).filter(models.Agence.id == colis.id_agence_depart).first()
    agence_dest = db.query(models.Agence).filter(models.Agence.id == colis.id_agence_destination).first()

    ville_dep = agence_depart.ville if agence_depart else "Agence de départ"
    ville_arr = agence_dest.ville if agence_dest else "Agence de destination"

    # Détermination du statut de référence
    piece_cible = piece_scanne if piece_scanne else colis
    statut_actuel = piece_cible.statut.lower() if piece_cible.statut else "reçu"

    # 2. Construction de la timeline linéaire sans dates
    historique = []

    # Étape 1 : Toujours valide (Reçu)
    historique.append({
        "titre": "Pris en charge",
        "description": f"Colis déposé et enregistré à l'agence de {ville_dep}."
    })

    # Étape 2 : Transit
    if statut_actuel in ["en transit", "en_transit", "arrivé", "arrive", "livré", "livre"]:
        historique.append({
            "titre": "En transit",
            "description": f"Le colis a quitté l'agence de {ville_dep} et est en cours d'acheminement."
        })

    # Étape 3 : Arrivé
    if statut_actuel in ["arrivé", "arrive", "livré", "livre"]:
        historique.append({
            "titre": "Disponible en agence",
            "description": f"Arrivé à l'agence de {ville_arr}. Prêt à être récupéré par le destinataire."
        })

    # Étape 4 : Livré
    if statut_actuel in ["livré", "livre"]:
        historique.append({
            "titre": "Colis livré",
            "description": f"Le colis a été remis en main propre au destinataire avec succès."
        })

    # Inversion pour placer le statut actuel au sommet
    historique.reverse()

    # Estimation globale (Conservée uniquement pour l'en-tête du widget)
    voyage_id_possible = next((p.voyage_id for p in toutes_les_pieces if p.voyage_id), None) if toutes_les_pieces else None
    voyage = db.query(models.Voyage).filter(models.Voyage.id == voyage_id_possible).first() if voyage_id_possible else None
    
    date_estimee = "Sous 24h à 48h"
    if voyage and voyage.date_arrivee_prevue:
        date_estimee = voyage.date_arrivee_prevue.strftime("%d/%m/%Y")

    return {
        "id": colis.id,
        "tracking_code": colis.tracking_code,
        "statut_global": colis.statut,
        "statut_piece_scanne": piece_scanne.statut if piece_scanne else None,
        "expediteur_nom": colis.expediteur_nom,
        "destinataire_nom": colis.destinataire_nom,
        "destination": ville_arr,
        "date_estimee": date_estimee,
        "poids_total": float(colis.poids_kg) if colis.poids_kg else sum(p.poids_kg for p in toutes_les_pieces if p.poids_kg),
        "nombre_pieces": len(toutes_les_pieces),
        "code_scanne_est_piece": piece_scanne.sub_tracking_code if piece_scanne else None,
        "historique": historique,
        "pieces": [
            {
                "sub_tracking_code": p.sub_tracking_code,
                "nature_contenu": p.nature_contenu,
                "poids_kg": p.poids_kg,
                "statut": p.statut
            } for p in toutes_les_pieces
        ]
    }

# --- AJOUT DANS MAIN.PY ---

@app.get("/colis/recherche_globale")
def get_all_agency_colis(id_agence: str, db: Session = Depends(database.get_db)):
    """
    Récupère tous les colis liés à une agence (départ ou destination)
    pour la recherche dynamique du Dashboard.
    """
    try:
        # On filtre les colis où l'agence est soit le point de départ, soit la destination
        # .all() renvoie la liste complète triée par date de création
        colis = db.query(models.Colis).filter(
            (models.Colis.id_agence_depart == id_agence) | 
            (models.Colis.id_agence_destination == id_agence)
        ).order_by(models.Colis.created_at.desc()).all()
        
        return colis
    except Exception as e:
        print(f"Erreur recherche_globale: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")
    
    
# --- EN-TÊTE DU VOYAGE (Si tu as mis VoyageCreate au-dessus des routes dans main.py) ---
# Si tu l'as mis dans un fichier schemas.py, remplace par : payload: schemas.VoyageCreate
# Si tu l'as laissé au-dessus des routes, garde : payload: VoyageCreate


# 1. Récupérer toutes les agences pour les menus déroulants (select) du frontend
@app.get("/agences/liste", response_model=List[schemas.Agence]) # <-- Ajout du type de retour
def get_agences_liste(db: Session = Depends(database.get_db)):
    return db.query(models.Agence).order_by(models.Agence.ville).all()


# 2. Récupérer uniquement les bus DISPONIBLES au dépôt
@app.get("/bus/disponibles")
def get_bus_disponibles(db: Session = Depends(database.get_db)):
    # Utilise bien models.Bus pour interroger la table SQLAlchemy
    return db.query(models.Bus).filter(models.Bus.statut == "disponible").all()


# 3. Créer et Programmer un nouveau voyage (Met aussi à jour le statut du bus)
@app.post("/voyages/creer")
def creer_voyage(payload: schemas.VoyageCreate, db: Session = Depends(database.get_db)):
    # Sécurité : On utilise models.Bus pour chercher dans la base de données
    bus = db.query(models.Bus).filter(models.Bus.id == payload.id_bus).first()
    if not bus or bus.statut != "disponible":
        raise HTTPException(status_code=400, detail="Ce véhicule n'est plus disponible ou en panne.")

    # Ici, on instancie le modèle SQLAlchemy (models.Voyage) avec les données validées par Pydantic (payload)
    nouveau_voyage = models.Voyage(
        id_bus=payload.id_bus,
        id_agence_depart=payload.id_agence_depart,
        id_agence_destination=payload.id_agence_destination,
        date_depart=payload.date_depart,
        statut="en_cours"  # Mis direct à 'en_cours' pour être capté par les scanners mobiles
    )
    db.add(nouveau_voyage)
    
    # Mutation intelligente : On verrouille le statut du bus récupéré via SQLAlchemy
    bus.statut = "en voyage"
    
    db.commit()
    return {"status": "success", "message": "Voyage ouvert et planifié avec succès."}


# 4. Récupérer la liste des mouvements actifs avec les détails (Jointures manuelles)
# @app.get("/voyages/actifs")
# def get_voyages_actifs(db: Session = Depends(database.get_db)):
#     voyages = db.query(models.Voyage).filter(models.Voyage.statut == "en_cours").all()
    
#     resultat = []
#     for v in voyages:
#         bus = db.query(models.Bus).filter(models.Bus.id == v.id_bus).first()
#         ag_dep = db.query(models.Agence).filter(models.Agence.id == v.id_agence_depart).first()
#         ag_dest = db.query(models.Agence).filter(models.Agence.id == v.id_agence_destination).first()
        
#         # CHOSE MANQUANTE : Récupérer l'agent associé au champ id_chauffeur
#         agent = None
#         if v.id_chauffeur:
#             agent = db.query(models.Agent).filter(models.Agent.id == v.id_chauffeur).first()
        
#         resultat.append({
#             "id": v.id,
#             "bus_plaque": bus.numero_plaque if bus else "Inconnu",
#             "bus_modele": bus.modele if bus else "",
#             "agence_depart": ag_dep.nom_agence if ag_dep else "Inconnu",
#             "agence_destination": ag_dest.nom_agence if ag_dest else "Inconnu",
#             "date_depart": v.date_depart,
#             "statut": v.statut,
#             # AJOUT : On renvoie l'ID pour que le select React s'active sur le bon agent
#             "id_chauffeur": str(v.id_chauffeur) if v.id_chauffeur else "",
#             "nom_chauffeur": agent.nom_complet if agent else "Non assigné"
#         })
        
#     return resultat


from sqlalchemy import or_ # 🌟 NE PAS OUBLIER CET IMPORT EN HAUT DE TON FICHIER

@app.get("/voyages/actifs")
def get_voyages_actifs(id_agence: Optional[UUID] = None, db: Session = Depends(database.get_db)):
    query = db.query(models.Voyage).filter(models.Voyage.statut.in_(["en_preparation", "en_cours"]))
    
    # 🌟 CORRECTION CRUCIALE : On filtre si l'agence est au DEPART ou à la DESTINATION
    if id_agence:
        query = query.join(models.Ligne).filter(
            or_(
                models.Ligne.id_agence_depart == id_agence,
                models.Ligne.id_agence_destination == id_agence
            )
        )
        
    voyages = query.all()
    
    resultat = []
    for v in voyages:
        bus = db.query(models.Bus).filter(models.Bus.id == v.id_bus).first()
        
        ag_dep = None
        ag_dest = None
        if v.ligne:
            ag_dep = db.query(models.Agence).filter(models.Agence.id == v.ligne.id_agence_depart).first()
            ag_dest = db.query(models.Agence).filter(models.Agence.id == v.ligne.id_agence_destination).first()
        
        agent = None
        if v.id_chauffeur:
            agent = db.query(models.Agent).filter(models.Agent.id == v.id_chauffeur).first()
        
        # ⚖️ CALCUL DU POIDS DÉJÀ EMBARQUÉ
        pieces_embarquees = db.query(models.ColisItem).filter(models.ColisItem.voyage_id == v.id).all()
        poids_actuel_bus = sum(float(p.poids_kg) if p.poids_kg else 0.0 for p in pieces_embarquees)
        
        # 🚌 CAPACITÉ DU BUS
        capacite_bus = float(bus.capacite_colis_kg) if (bus and bus.capacite_colis_kg) else 1000.0

        resultat.append({
            "id": v.id,
            # 🚨 ON AJOUTE LES DEUX COMPORTEMENTS DANS LE DICTIONNAIRE POUR LE TRICORRECT DU FRONTEND
            "id_agence_depart": str(ag_dep.id) if ag_dep else "", 
            "id_agence_destination": str(ag_dest.id) if ag_dest else "", 
            "bus_plaque": bus.numero_plaque if bus else "Inconnu",
            "bus_modele": bus.modele if bus else "",
            
            "capacite_colis_kg": capacite_bus,
            "poids_actuel": poids_actuel_bus,
            "poids_restant": max(0.0, capacite_bus - poids_actuel_bus),
            
            "agence_depart": ag_dep.nom_agence if ag_dep else "Inconnu",
            "agence_destination": ag_dest.nom_agence if ag_dest else "Inconnu",
            "date_depart": v.date_depart,
            "statut": v.statut,
            "id_chauffeur": str(v.id_chauffeur) if v.id_chauffeur else "",
            "nom_chauffeur": agent.nom_complet if agent else "Non assigné"
        })
        
    return resultat


# =====================================================================
# ROUTE 1 : Récupérer les agents d'une agence spécifique (SQLAlchemy)
# =====================================================================
@app.get("/agents/agence/{id_agence}")
def get_agents_by_agence(id_agence: UUID, db: Session = Depends(database.get_db)):
    """
    Récupère la liste de tous les agents rattachés à une agence spécifique.
    Retourne les objets correspondants au modèle SQLAlchemy.
    """
    # Utilise l'ORM pour filtrer les agents appartenant à cette agence
    agents = db.query(models.Agent).filter(models.Agent.id_agence == id_agence).order_by(models.Agent.nom_complet.asc()).all()
    
    # Pratique : FastAPI et Pydantic s'occuperont de filtrer/sérialiser les champs 
    # si tu as configuré un response_model, sinon il renvoie la structure par défaut.
    return agents


# =====================================================================
# ROUTE 2 : Assigner un agent (chauffeur) à un voyage (Mutation ORM)
# =====================================================================
@app.patch("/voyages/{id_voyage}/assigner-agent")
def assigner_agent_au_voyage(
    id_voyage: UUID, 
    payload: schemas.AssignerAgentRequest, 
    db: Session = Depends(database.get_db)
):
    """
    Met à jour un voyage spécifique en lui attribuant un agent de l'agence
    via la mutation intelligente de l'instance SQLAlchemy.
    """
    # 1. Vérifier et récupérer le voyage depuis models.Voyage
    voyage = db.query(models.Voyage).filter(models.Voyage.id == id_voyage).first()
    if not voyage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Voyage introuvable."
        )
        
    # 2. Vérifier si l'agent sélectionné existe bien dans la table des agents
    agent = db.query(models.Agent).filter(models.Agent.id == payload.id_agent).first()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="L'agent sélectionné n'existe pas."
        )

    # 3. Mutation de l'objet : on assigne l'agent au champ id_chauffeur du voyage
    voyage.id_chauffeur = payload.id_agent
    
    # 4. Sauvegarde dans la base de données PostgreSQL
    db.commit()
    
    return {"status": "success", "message": f"L'agent {agent.nom_complet} a été assigné avec succès."}




from datetime import date, datetime, timedelta
from sqlalchemy import and_, func


# @app.get("/agents/{agent_id}/voyages-du-jour")
# def get_voyages_du_jour_agent(agent_id: UUID, db: Session = Depends(database.get_db)):
#     """
#     Récupère les voyages assignés à l'agent (en tant que id_chauffeur)
#     qui partent AUJOURD'HUI de son agence.
#     """
#     # 1. Récupérer l'agent pour connaître son agence
#     agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
#     if not agent:
#         raise HTTPException(status_code=404, detail="Agent non trouvé")
        
#     aujourdhui = date.today()
    
#     # 2. Requête ORM SQLAlchemy avec jointures et filtres complexes
#     voyages = (
#         db.query(models.Voyage)
#         .filter(
#             and_(
#                 # Voyage assigné à cet agent
#                 models.Voyage.id_chauffeur == agent_id,
#                 # Partant de son agence
#                 models.Voyage.id_agence_depart == agent.id_agence,
#                 # Dont la date de départ est AUJOURD'HUI
#                 func.date(models.Voyage.date_depart) == aujourdhui,
#                 # Statut cohérent (pas encore 'Arrivé')
#                 models.Voyage.statut.in_(["en_preparation", "en_cours"])
#             )
#         )
#         .order_by(models.Voyage.date_depart.asc())
#         .all()
#     )
    
#     # 3. Formatage propre des résultats pour le frontend React
#     resultat = []
#     for v in voyages:
#         # Récupération des infos liées (Bus et Destination)
#         bus = db.query(models.Bus).filter(models.Bus.id == v.id_bus).first()
#         ag_dest = db.query(models.Agence).filter(models.Agence.id == v.id_agence_destination).first()
        
#         resultat.append({
#             "id_voyage": v.id,
#             "id_bus": v.id_bus,
#             "bus_plaque": bus.numero_plaque if bus else "Sans plaque",
#             "bus_modele": bus.modele if bus else "Modèle inconnu",
#             "destination_nom": ag_dest.nom_agence if ag_dest else "Destination inconnue",
#             "destination_ville": ag_dest.ville if ag_dest else "",
#             "heure_depart": v.date_depart.strftime("%H:%M"), # Juste l'heure pour l'affichage mobile
#             "statut_voyage": v.statut
#         })
        
#     return resultat


@app.get("/agents/{agent_id}/voyages-du-jour")
def get_voyages_du_jour_agent(agent_id: UUID, db: Session = Depends(database.get_db)):
    # --- TA LOGIQUE EXISTANTE : Vérification de l'agent ---
    agent = db.query(models.Agent).filter(models.Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent non trouvé")
        
    aujourdhui = date.today()
    
    # --- TA LOGIQUE EXISTANTE : Récupération filtrée et triée des voyages ---
    voyages = (
        db.query(models.Voyage)
        .join(models.Ligne)
        .filter(
            and_(
                models.Voyage.id_chauffeur == agent_id,
                models.Ligne.id_agence_depart == agent.id_agence, 
                func.date(models.Voyage.date_depart) == aujourdhui,
                models.Voyage.statut.in_(["en_preparation", "en_cours"])
            )
        )
        .order_by(models.Voyage.date_depart.asc())
        .all()
    )
    
    resultat = []
    for v in voyages:
        # --- TA LOGIQUE EXISTANTE : Infos du bus et agence de destination ---
        bus = db.query(models.Bus).filter(models.Bus.id == v.id_bus).first()
        
        ag_dest = None
        if v.ligne:
            ag_dest = db.query(models.Agence).filter(models.Agence.id == v.ligne.id_agence_destination).first()
        
        # ─── ⚖️ AJUSTEMENT : CALCUL EN TEMPS RÉEL DE LA CHARGE DU VÉHICULE ───
        # 1. Définition de la capacité max du bus (avec fallback si non configurée)
        capacite_max = float(bus.capacite_colis_kg) if (bus and bus.capacite_colis_kg) else 1000.0
        
        # 2. On récupère toutes les pièces liées à ce voyage actuellement "En transit"
        pieces_en_transit = db.query(models.ColisItem).filter(
            models.ColisItem.voyage_id == v.id,
            models.ColisItem.statut == "En transit"
        ).all()
        
        # 3. Calculs des poids et restants
        poids_actuel = sum(float(p.poids_kg) if p.poids_kg else 0.0 for p in pieces_en_transit)
        poids_restant = max(0.0, capacite_max - poids_actuel)
        
        # --- FUSION : Dictionnaire final combinant tes données et les indicateurs de poids ---
        resultat.append({
            # Clés d'origine conservées
            "id_voyage": v.id,
            "id": v.id, # Clé alias doublée pour assurer la compatibilité frontend
            "id_bus": v.id_bus,
            "bus_plaque": bus.numero_plaque if bus else "Sans plaque",
            "bus_modele": bus.modele if bus else "Modèle inconnu",
            "destination_nom": ag_dest.nom_agence if ag_dest else "Destination inconnue",
            "destination_ville": ag_dest.ville if ag_dest else "",
            "heure_depart": v.date_depart.strftime("%H:%M"),
            "statut_voyage": v.statut,
            
            # 🔥 Nouveaux indicateurs dynamiques injectés
            "poids_actuel": poids_actuel,
            "capacite_colis_kg": capacite_max,
            "poids_restant": poids_restant,
            "nombre_pieces": len(pieces_en_transit)
        })

    # --- AJOUTE CE PRINT POUR DEBUGGER DANS TA CONSOLE PYTHON ---
    print(f"DEBUG: Agent connecté ID = {agent_id} (Agence: {agent.id_agence})")
    print(f"DEBUG: Nombre de voyages trouvés après filtres = {len(voyages)}")
    
    # Si tu veux voir la requête SQL exacte générée par SQLAlchemy :
    requete_sql = db.query(models.Voyage).join(models.Ligne).filter(
        and_(
            models.Voyage.id_chauffeur == agent_id,
            models.Ligne.id_agence_depart == agent.id_agence, 
            func.date(models.Voyage.date_depart) == aujourdhui,
            models.Voyage.statut.in_(["en_preparation", "en_cours"])
        )
    )
    print(f"DEBUG SQL: {requete_sql}")
        
    return resultat





@app.get("/colis/inspecter/{tracking_code}")
def inspecter_colis(tracking_code: str, db: Session = Depends(database.get_db)):
    print(f"\n--- INSPECTION DU STATUT & DU TRAJET : {tracking_code} ---")
    
    item_scanne = db.query(models.ColisItem).filter(models.ColisItem.sub_tracking_code == tracking_code).first()
    
    voyage_id_actuel = None
    if item_scanne:
        colis = db.query(models.Colis).filter(models.Colis.id == item_scanne.colis_id).first()
        statut_actuel = item_scanne.statut
        voyage_id_actuel = item_scanne.voyage_id  
    else:
        colis = db.query(models.Colis).filter(models.Colis.tracking_code == tracking_code).first()
        item_scanne = None
        statut_actuel = colis.statut if colis else None

    if not colis:
        raise HTTPException(status_code=404, detail="Aucun colis ou item trouvé avec ce code.")

    agence_dep = db.query(models.Agence).filter(models.Agence.id == colis.id_agence_depart).first()
    agence_dest = db.query(models.Agence).filter(models.Agence.id == colis.id_agence_destination).first()
    nom_dep = agence_dep.nom_agence if agence_dep else "l'agence de départ"
    nom_dest = agence_dest.nom_agence if agence_dest else "l'agence de destination"

    localisation_message = "Statut non défini."
    infos_vehicule = None

    # Sécurisation avec .lower() pour intercepter toutes les variantes de chaînes
    statut_clean = statut_actuel.lower().strip() if statut_actuel else ""

    if statut_clean in ["reçu", "en agence", "en_agence"]:
        localisation_message = f"En Stock : Le colis est actuellement sécurisé à l'agence de départ ({nom_dep})."
    
    elif statut_clean in ["embarqué", "en_transit", "embarque", "en transit"]:
        localisation_message = "En Transit : Le colis est actuellement en route dans un de nos véhicules."
        
        if voyage_id_actuel:
            # 💥 CORRECTION ICI : Remplacement de id_voyage par id pour correspondre au modèle Voyage
            voyage = db.query(models.Voyage).filter(models.Voyage.id == voyage_id_actuel).first()
            if voyage and voyage.id_bus:
                bus = db.query(models.Bus).filter(models.Bus.id == voyage.id_bus).first()
                if bus:
                    infos_vehicule = {
                        "plaque": bus.numero_plaque, 
                        "modele": bus.modele if bus.modele else "Modèle inconnu"
                    }
                    localisation_message = f"En Transit : Chargé dans le bus [{bus.numero_plaque}] ({bus.modele}), en déplacement vers {nom_dest}."

    elif statut_clean in ["arrivé", "arrive"]:
        localisation_message = f"Disponible : Le colis est arrivé à destination. Prêt à être récupéré à l'agence de : {nom_dest}."
    
    elif statut_clean in ["livré", "livre"]:
        localisation_message = f"Terminé : Le colis a été officiellement remis au destinataire à {nom_dest}."

    tous_les_items = db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).all()
    items_liste = []
    for it in tous_les_items:
        items_liste.append({
            "id": str(it.id),
            "tracking_code": it.sub_tracking_code,
            "nature": it.nature_contenu,
            "poids": it.poids_kg,
            "statut": it.statut,
            "est_le_colis_scanne": True if (item_scanne and it.id == item_scanne.id) else False
        })

    return {
        "tracking_code": colis.tracking_code,
        "statut_global": colis.statut,
        "statut_scanne": statut_actuel,
        "localisation_message": localisation_message,
        "vehicule": infos_vehicule, 
        "prix": colis.prix_transport,
        "nom_expediteur": colis.expediteur_nom,
        "telephone_expediteur": colis.expediteur_tel,
        "nom_destinataire": colis.destinataire_nom,
        "telephone_destinataire": colis.destinataire_tel,
        "agence_depart": nom_dep,
        "agence_destination": nom_dest,
        "date_creation": colis.created_at.strftime("%d/%m/%Y %H:%M") if colis.created_at else "Non définie",
        "items": items_liste,
        "nombre_total_pieces": len(items_liste)
    }



from datetime import datetime
from uuid import UUID
from fastapi import Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

RDC_TIMEZONE = ZoneInfo("Africa/Lubumbashi")

@app.post("/colis/receptionner")
async def receptionner_colis(
    tracking_code: str, 
    agent_id: str, 
    voyage_id: str,  # 🔥 AJOUT CRITIQUE : Le manifeste en cours est désormais obligatoire
    background_tasks: BackgroundTasks, 
    db: Session = Depends(database.get_db)
):
    clean_code = str(tracking_code).strip()
    print(f"\n========== RÉCEPTION AVEC MANIFESTE [{voyage_id}] : {clean_code} ==========")
    
    # -------------------------------------------------------------------------
    # 🔥 SÉCURITÉ INTERCEPTION SCAN RACK SANS MODIFICATION
    # -------------------------------------------------------------------------
    un_emplacement = db.query(models.MagasinEmplacement).filter(
        models.MagasinEmplacement.code_emplacement == clean_code
    ).first()

    if un_emplacement:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "detail": "Action non autorisée. Vous devez scanner uniquement le code-barres du colis."
            }
        )

    # 1. Vérification des formats UUID
    try:
        u_agent_id = UUID(agent_id)
        u_voyage_id = UUID(voyage_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format ID agent ou ID voyage invalide")

    agent = db.query(models.Agent).filter(models.Agent.id == u_agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent non identifié")

    agence = db.query(models.Agence).filter(models.Agence.id == agent.id_agence).first()
    nom_de_l_agence = agence.nom_agence if agence else "l'agence de destination"

    # 2. Récupération de l'item ou du colis
    item_scanne = db.query(models.ColisItem).filter(models.ColisItem.sub_tracking_code == clean_code).first()
    
    if item_scanne:
        colis = db.query(models.Colis).filter(models.Colis.id == item_scanne.colis_id).first()
    else:
        colis = db.query(models.Colis).filter(models.Colis.tracking_code == clean_code).first()

    if not colis:
        raise HTTPException(status_code=404, detail=f"Aucun colis ou pièce valide trouvé pour le code [{clean_code}].")

    # -------------------------------------------------------------------------
    # 🔥 BLINDAGE LOGISTIQUE : SÉCURITÉ MANIFESTE & VOYAGE
    # -------------------------------------------------------------------------
    if item_scanne:
        if not item_scanne.voyage_id or item_scanne.voyage_id != u_voyage_id:
            raise HTTPException(
                status_code=400,
                detail=f"Sécurité Manifeste : Cette pièce n'appartient pas au voyage sélectionné."
            )
    else:
        # Si scan global, on vérifie qu'au moins une pièce est sur ce voyage
        piece_liante = db.query(models.ColisItem).filter(
            models.ColisItem.colis_id == colis.id, 
            models.ColisItem.voyage_id == u_voyage_id
        ).first()
        if not piece_liante:
            raise HTTPException(
                status_code=400,
                detail=f"Sécurité Manifeste : Ce colis global ne fait pas partie de ce voyage."
            )

    # Verrou concordance agent - destination
    if colis.id_agence_destination != agent.id_agence:
        raise HTTPException(
            status_code=400, 
            detail=f"Erreur de routage : Ce colis est destiné à une autre agence."
        )

    # 3. Fonction utilitaire de notification préservée
    def notifier_colis(sujet, message_dest, message_exp, sms_dest=None, sms_exp=None):
        if colis.destinataire_email:
            background_tasks.add_task(send_notification_email, colis.destinataire_email, sujet, message_dest, colis.tracking_code)
        if colis.expediteur_email:
            background_tasks.add_task(send_notification_email, colis.expediteur_email, sujet, message_exp, colis.tracking_code)
        if sms_dest and colis.destinataire_tel:
            ajouter_sms(db, colis.destinataire_tel, sms_dest, colis.tracking_code)
        if sms_exp and colis.expediteur_tel:
            ajouter_sms(db, colis.expediteur_tel, sms_exp, colis.tracking_code)

    # 4. Gestion et normalisation des statuts
    statut_actuel = item_scanne.statut if item_scanne else colis.statut
    statut_clean = statut_actuel.lower().strip() if statut_actuel else ""

    if statut_clean in ["arrivé", "arrive"]:
        raise HTTPException(status_code=400, detail="Ce colis ou cette pièce a déjà été réceptionné.")
        
    if statut_clean in ["livré", "livre"]:
        raise HTTPException(status_code=400, detail="Ce colis a déjà été livré au destinataire.")

    # Acceptation souple des statuts de route d'origine
    if statut_clean not in ["embarqué", "en_transit", "embarque", "en transit"]:
        raise HTTPException(
            status_code=400, 
            detail=f"Impossible de réceptionner. Statut actuel : '{statut_actuel}'."
        )

    message = ""

    # 5. Algorithme d'attribution automatique de Rack (Inchangé)
    try:
        poids_a_ranger = float(item_scanne.poids_kg) if item_scanne else float(colis.poids_kg)
        
        emplacement_propose = db.query(models.MagasinEmplacement).\
            join(models.MagasinRack, models.MagasinEmplacement.id_rack == models.MagasinRack.id).\
            join(models.MagasinZone, models.MagasinRack.id_zone == models.MagasinZone.id).\
            filter(
                models.MagasinZone.id_agence == agent.id_agence,
                models.MagasinEmplacement.statut == "disponible",
                models.MagasinEmplacement.poids_max_kg >= poids_a_ranger
            ).\
            order_by(
                (models.MagasinEmplacement.niveau_index == 'A').desc() if poids_a_ranger > 25.0 else models.MagasinEmplacement.niveau_index.asc(),
                models.MagasinEmplacement.code_emplacement.asc()
            ).first()

        id_emplacement_attribue = None
        code_emplacement_attribue = "Zone de vrac / Sol"

        if emplacement_propose:
            id_emplacement_attribue = emplacement_propose.id
            code_emplacement_attribue = emplacement_propose.code_emplacement
            emplacement_propose.statut = "occupé"

        # --- TRAITEMENT DES MISES A JOUR ---
        if item_scanne is None:
            tous_les_items = db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).all()
            total_pieces = len(tous_les_items)
            
            db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).update({
                "statut": "Arrivé",
                "id_agent_reception": u_agent_id,
                "id_emplacement": id_emplacement_attribue,
                "created_at": datetime.now(RDC_TIMEZONE)
            }, synchronize_session=False)
            
            colis.statut = "Arrivé"
            db.commit()
            
            notifier_colis(
                sujet="📍 Votre colis est disponible - Classic Coach",
                message_dest=f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} est arrivé à l'agence de {nom_de_l_agence}.",
                message_exp=f"Bonjour {colis.expediteur_nom}, votre envoi {colis.tracking_code} est arrivé à destination."
            )
            message = f"Réception totale validée pour {colis.tracking_code} ! Placé en {code_emplacement_attribue}."

        else:
            item_scanne.statut = "Arrivé"
            item_scanne.id_agent_reception = u_agent_id
            item_scanne.id_emplacement = id_emplacement_attribue
            db.commit()
            
            tous_les_items = db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).all()
            total_pieces = len(tous_les_items)
            pieces_arrivees = sum(1 for p in tous_les_items if p.statut.lower() in ["arrivé", "arrive"])

            if pieces_arrivees == total_pieces:
                colis.statut = "Arrivé"
                db.commit()
                notifier_colis(
                    sujet="📍 Votre colis est disponible - Classic Coach",
                    message_dest=f"Bonjour {colis.destinataire_nom}, votre colis {colis.tracking_code} est complet au dépôt.",
                    message_exp=f"Bonjour {colis.expediteur_nom}, votre envoi {colis.tracking_code} est arrivé."
                )
                message = f"Réception totale validée ! Placé en {code_emplacement_attribue}."
            else:
                message = f"Réception partielle ({pieces_arrivees}/{total_pieces}). Pièce {item_scanne.sub_tracking_code} rangée en {code_emplacement_attribue}."

        return {
            "status": "success",
            "message": message,
            "need_placement": False,
            "sub_tracking_code": item_scanne.sub_tracking_code if item_scanne else colis.tracking_code,
            "emplacement": code_emplacement_attribue
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur interne : {str(e)}")
    
@app.get("/admin/bus", response_model=List[schemas.BusResponse])
def get_all_bus(db: Session = Depends(database.get_db)):
    bus_list = db.query(models.Bus).order_by(models.Bus.created_at.desc()).all()
    return bus_list

# 2. Route pour ajouter un nouveau bus
@app.post("/admin/bus", response_model=schemas.BusResponse)
def create_bus(bus_in: schemas.BusCreate, db: Session = Depends(database.get_db)):
    # Vérifier si la plaque existe déjà pour éviter les doublons
    existing_bus = db.query(models.Bus).filter(models.Bus.numero_plaque == bus_in.numero_plaque).first()
    if existing_bus:
        raise HTTPException(status_code=400, detail="Un bus avec cette plaque d'immatriculation existe déjà.")
    
    nouveau_bus = models.Bus(**bus_in.dict())
    
    try:
        db.add(nouveau_bus)
        db.commit()
        db.refresh(nouveau_bus)
        return nouveau_bus
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erreur lors de l'enregistrement du bus.")
    



from fastapi import HTTPException, Depends, status
from sqlalchemy.orm import Session, joinedload
import models, schemas, database

# Route utilitaire pour l'Admin afin de lister les agences dispo
@app.get("/admin/agences-options", response_model=list[schemas.Agence])
async def lister_agences_options(db: Session = Depends(database.get_db)):
    return db.query(models.Agence).order_by(models.Agence.ville).all()

@app.post("/admin/lignes", response_model=schemas.LigneResponse)
async def creer_ligne(ligne_in: schemas.LigneCreate, db: Session = Depends(database.get_db)):
    # 1. Validation stricte anti-boucle (Même agence)
    if ligne_in.id_agence_depart == ligne_in.id_agence_destination:
        raise HTTPException(
            status_code=400, 
            detail="L'agence de départ et de destination ne peuvent pas être identiques."
        )
        
    # 2. Validation anti-doublon (Vérifier si le trajet existe déjà en DB)
    ligne_existante = db.query(models.Ligne).filter(
        models.Ligne.id_agence_depart == ligne_in.id_agence_depart,
        models.Ligne.id_agence_destination == ligne_in.id_agence_destination
    ).first()
    
    if ligne_existante:
        raise HTTPException(
            status_code=400, 
            detail="Cet axe routier (Ligne) existe déjà."
        )
        
    nouvelle_ligne = models.Ligne(**ligne_in.dict())
    try:
        db.add(nouvelle_ligne)
        db.commit()
        db.refresh(nouvelle_ligne)
        
        # Re-charger l'objet avec ses relations agence_depart/destination pour la réponse
        return db.query(models.Ligne).options(
            joinedload(models.Ligne.agence_depart),
            joinedload(models.Ligne.agence_destination)
        ).filter(models.Ligne.id == nouvelle_ligne.id).first()
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erreur interne lors de la création de la ligne.")

@app.get("/admin/lignes", response_model=list[schemas.LigneResponse])
async def lister_lignes(db: Session = Depends(database.get_db)):
    return db.query(models.Ligne).options(
        joinedload(models.Ligne.agence_depart),
        joinedload(models.Ligne.agence_destination)
    ).all()


@app.post("/admin/voyages", response_model=schemas.VoyageResponse)
async def planifier_voyage(voyage_in: schemas.VoyageCreate, db: Session = Depends(database.get_db)):
    # Optionnel : Tu pourrais ajouter ici une validation pour vérifier 
    # si le bus n'est pas déjà assigné à un autre voyage le même jour.
    
    nouveau_voyage = models.Voyage(**voyage_in.dict())
    try:
        db.add(nouveau_voyage)
        db.commit()
        db.refresh(nouveau_voyage)
        
        # On recharge avec les relations pour satisfaire le schéma de réponse
        return db.query(models.Voyage).options(
            joinedload(models.Voyage.bus),
            joinedload(models.Voyage.ligne).joinedload(models.Ligne.agence_depart),
            joinedload(models.Voyage.ligne).joinedload(models.Ligne.agence_destination)
        ).filter(models.Voyage.id == nouveau_voyage.id).first()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Erreur lors de la planification du voyage.")

@app.get("/admin/voyages", response_model=list[schemas.VoyageResponse])
async def lister_voyages(db: Session = Depends(database.get_db)):
    return db.query(models.Voyage).options(
        joinedload(models.Voyage.bus),
        joinedload(models.Voyage.ligne).joinedload(models.Ligne.agence_depart),
        joinedload(models.Voyage.ligne).joinedload(models.Ligne.agence_destination)
    ).order_by(models.Voyage.date_depart.asc()).all()




class ScanColisInput(BaseModel):
    sub_tracking_code: str # Le code scanné sur l'étiquette de la pièce

@app.post("/voyages/{id_voyage}/scanner-item")
def scanner_item_dans_bus(id_voyage: UUID, payload: ScanColisInput, db: Session = Depends(database.get_db)):
    # 1. Trouver le voyage et charger sa ligne et son bus associés
    voyage = db.query(models.Voyage).filter(models.Voyage.id == id_voyage).first()
    if not voyage:
        raise HTTPException(status_code=404, detail="Voyage introuvable.")
        
    # 2. Trouver la pièce de colis scannée (ColisItem)
    item = db.query(models.ColisItem).filter(models.ColisItem.sub_tracking_code == payload.sub_tracking_code).first()
    if not item:
        raise HTTPException(status_code=404, detail="Pièce de colis introuvable.")
        
    if item.statut == "Embarqué":
        raise HTTPException(status_code=400, detail="Ce colis est déjà chargé dans le bus.")

    # 3. Récupérer le colis principal pour connaître sa vraie destination
    colis_parent = db.query(models.Colis).filter(models.Colis.id == item.colis_id).first()
    
    # 🔒 BARRIÈRE DE SÉCURITÉ 1 : Vérification de la Destination
    # On compare la destination finale du colis avec la destination de la ligne du bus
    if colis_parent.id_agence_destination != voyage.ligne.id_agence_destination:
        # On récupère le nom de la bonne destination pour faire un message d'erreur clair
        bonne_destination = db.query(models.Agence).filter(models.Agence.id == colis_parent.id_agence_destination).first()
        nom_dest = bonne_destination.nom_agence if bonne_destination else "une autre agence"
        raise HTTPException(
            status_code=400, 
            detail=f"Erreur de aiguillage ! Ce colis est destiné à [{nom_dest}]. Ce bus va ailleurs."
        )

    # 🔒 BARRIÈRE DE SÉCURITÉ 2 : Vérification de la Capacité Fret du Bus
    # On somme le poids de tous les items qui ont déjà ce voyage_id
    poids_actuel_charge = db.query(func.sum(models.ColisItem.poids_kg)).filter(models.ColisItem.voyage_id == id_voyage).scalar() or 0.0
    
    capacite_max_bus = voyage.bus.capacite_colis_kg
    if poids_actuel_charge + item.poids_kg > capacite_max_bus:
        place_restante = capacite_max_bus - poids_actuel_charge
        raise HTTPException(
            status_code=400, 
            detail=f"Surcharge du bus ! Capacité restante : {place_restante} kg. Ce colis pèse {item.poids_kg} kg."
        )

    # 🛠️ TOUT EST VALIDE : Le colis peut monter
    item.voyage_id = id_voyage
    item.statut = "Embarqué"
    
    # Optionnel : On peut mettre à jour le statut du colis parent pour dire "En cours d'acheminement"
    colis_parent.statut = "En Transit"
    
    db.commit()
    
    return {
        "status": "success",
        "message": f"Pièce scannée et embarquée avec succès !",
        "charge_actuelle": f"{poids_actuel_charge + item.poids_kg} / {capacite_max_bus} kg"
    }

@app.get("/voyages/{voyage_id}/statut-charge")
async def get_statut_charge_voyage(voyage_id: str, db: Session = Depends(database.get_db)):
    try:
        u_voyage_id = UUID(voyage_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format ID Voyage invalide")

    voyage = db.query(models.Voyage).filter(models.Voyage.id == u_voyage_id).first()
    if not voyage:
        raise HTTPException(status_code=404, detail="Voyage introuvable")

    bus = db.query(models.Bus).filter(models.Bus.id == voyage.id_bus).first()
    if not bus:
        raise HTTPException(status_code=404, detail="Bus introuvable")

    # ⚖️ RECALCUL EN TEMPS RÉEL : On somme les pièces actuellement dans ce bus (En transit)
    pieces_embarquees = db.query(models.ColisItem).filter(
        models.ColisItem.voyage_id == u_voyage_id,
        models.ColisItem.statut == "En transit"
    ).all()
    
    poids_actuel = sum(float(p.poids_kg) if p.poids_kg else 0.0 for p in pieces_embarquees)
    capacite_max = float(bus.capacite_colis_kg) if bus.capacite_colis_kg else 1000.0
    poids_restant = max(0.0, capacite_max - poids_actuel)

    return {
        "voyage_id": str(voyage.id),
        "poids_actuel": poids_actuel,
        "capacite_colis_kg": capacite_max,
        "poids_restant": poids_restant,
        "nombre_pieces_embarquees": len(pieces_embarquees)
    }


@app.get("/voyages/{voyage_id}/colis-embarques")
def get_colis_embarques_voyage(voyage_id: UUID, db: Session = Depends(database.get_db)):
    # Récupère tous les ColisItem liés à ce voyage qui sont actuellement scannés ("En transit")
    colis = db.query(models.ColisItem).filter(
        models.ColisItem.voyage_id == voyage_id,
        models.ColisItem.statut == "En transit"
    ).all()
    
    return colis




@app.patch("/admin/voyages/{id_voyage}/statut")
def modifier_statut_voyage(id_voyage: UUID, statut: str, db: Session = Depends(database.get_db)):
    voyage = db.query(models.Voyage).filter(models.Voyage.id == id_voyage).first()
    if not voyage:
        raise HTTPException(status_code=404, detail="Voyage non trouvé")
        
    statuts_autorises = ["en_preparation", "en_cours", "termine", "annule"]
    if statut not in statuts_autorises:
        raise HTTPException(status_code=400, detail="Statut invalide")
        
    # Mettre à jour le statut du voyage
    voyage.statut = statut
    
    # [Optionnel mais puissant] Mettre aussi à jour le statut du bus associé
    bus = db.query(models.Bus).filter(models.Bus.id == voyage.id_bus).first()
    if bus:
        if statut == "en_cours":
            bus.statut = "en_voyage"
        elif statut == "termine" or statut == "annule":
            bus.statut = "disponible"

    db.commit()
    return {"message": f"Le voyage est maintenant {statut}", "statut": statut}




from uuid import UUID
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
# On suppose que models et database sont déjà importés en haut de ton fichier

@app.get("/agents/{agent_id}/historique-scans")
def obtenir_historique_scans_agent(agent_id: UUID, db: Session = Depends(database.get_db)):
    """
    Récupère l'historique complet de tous les colis scannés et chargés par un agent,
    groupés par voyage (bus) et classés du plus récent au plus ancien.
    """
    
    # 1. Recherche des voyages de l'agent en tant que convoyeur via models.Voyage
    voyages_agent = (
        db.query(models.Voyage)
        .filter(models.Voyage.id_chauffeur == agent_id)
        .order_by(models.Voyage.date_depart.desc())
        .all()
    )
    
    if not voyages_agent:
        return []

    resultat_historique = []

    # 2. On parcourt les voyages
    for voyage in voyages_agent:
        
        # Utilisation de la relation magique 'voyage.bus' configurée dans ton modèle
        plaque = voyage.bus.numero_plaque if voyage.bus else "Bus Inconnu"
        
        # Utilisation de la relation magique 'voyage.ligne' pour récupérer la destination
        destination_label = "Destination non spécifiée"
        if voyage.ligne:
            # Grâce aux relations agence_depart et agence_destination de ta classe Ligne
            nom_dep = voyage.ligne.agence_depart.nom_agence if voyage.ligne.agence_depart else "Départ"
            nom_dest = voyage.ligne.agence_destination.nom_agence if voyage.ligne.agence_destination else "Destination"
            destination_label = f"{nom_dep} ➔ {nom_dest}"

        # 3. Récupération des sous-colis via models.ColisItem
        items_scannes = (
            db.query(models.ColisItem)
            .filter(models.ColisItem.voyage_id == voyage.id)
            .order_by(models.ColisItem.created_at.desc())
            .all()
        )

        # Si aucun scan n'a encore été fait pour ce voyage, on passe au suivant
        if not items_scannes:
            continue

        # Formatage des colis pour le JSON attendu par le mobile
        liste_colis_formattee = []
        for item in items_scannes:
            heure_scan = item.created_at.strftime("%H:%M") if item.created_at else "--:--"
            
            liste_colis_formattee.append({
                "tracking_code": item.sub_tracking_code,
                "heure_scan": heure_scan
            })

        # Format de date propre (JJ/MM/AAAA) pour le tri chronologique côté front
        date_voyage_str = voyage.date_depart.strftime("%d/%m/%Y") if voyage.date_depart else "Date inconnue"

        resultat_historique.append({
            "date_voyage": date_voyage_str,
            "bus_plaque": plaque,
            "destination": destination_label,
            "colis": liste_colis_formattee
        })

    return resultat_historique


@app.get("/agents/{agent_id}/historique-receptions")
def obtenir_historique_receptions_agent(agent_id: UUID, db: Session = Depends(database.get_db)):
    """
    Récupère l'historique complet de tous les colis qu'un agent local a déchargés/réceptionnés
    à l'arrivée des bus, groupés par voyage (provenance) et par date de réception.
    """
    
    # 1. On récupère tous les items de colis que CET agent spécifique a réceptionnés
    # On les trie du plus récent scan au plus ancien
    items_receptionnes = (
        db.query(models.ColisItem)
        .filter(models.ColisItem.id_agent_reception == agent_id) # Ton champ de traçabilité réception
        .order_by(models.ColisItem.created_at.desc()) # Ou une colonne updated_at si tu captures l'instant du déchargement
        .all()
    )
    
    if not items_receptionnes:
        return []

    # 2. On va regrouper ces items par Voyage pour garder la même structure visuelle pro
    # Dictionnaire temporaire pour regrouper : { voyage_id: [liste_des_items] }
    groupement_par_voyage = {}
    for item in items_receptionnes:
        if item.voyage_id not in groupement_par_voyage:
            groupement_par_voyage[item.voyage_id] = []
        groupement_par_voyage[item.voyage_id].append(item)

    resultat_reception = []

    # 3. Pour chaque voyage identifié, on extrait les métadonnées (Plaque, Provenance)
    for voyage_id, items in groupement_par_voyage.items():
        if not voyage_id:
            continue
            
        voyage = db.query(models.Voyage).filter(models.Voyage.id == voyage_id).first()
        if not voyage:
            continue

        # Récupération de la plaque du bus via la relation ou requête
        plaque = voyage.bus.numero_plaque if voyage.bus else "Bus Inconnu"
        
        # Détermination de la provenance (L'agence de départ devient l'origine du flux)
        provenance_label = "Provenance inconnue"
        if voyage.ligne:
            nom_dep = voyage.ligne.agence_depart.nom_agence if voyage.ligne.agence_depart else "Origine"
            nom_dest = voyage.ligne.agence_destination.nom_agence if voyage.ligne.agence_destination else "Ma Station"
            provenance_label = f"Provenance : {nom_dep} ➔ {nom_dest}"

        # Formatage des colis déchargés de ce bus précis
        liste_colis_formattee = []
        for item in items:
            # Heure à laquelle le colis a été scanné à la descente
            heure_reception = item.created_at.strftime("%H:%M") if item.created_at else "--:--"
            
            liste_colis_formattee.append({
                "tracking_code": item.sub_tracking_code,
                "heure_scan": heure_reception
            })

        # Date à laquelle le traitement a eu lieu (Format: JJ/MM/AAAA)
        # On prend la date du premier item scanné comme repère de la journée de travail
        date_action_str = items[0].created_at.strftime("%d/%m/%Y") if items[0].created_at else "Date inconnue"

        resultat_reception.append({
            "date_voyage": date_action_str, # Clé identique pour que le Frontend réutilise la même logique de tri
            "bus_plaque": plaque,
            "destination": provenance_label,
            "colis": liste_colis_formattee
        })

    # Trier le résultat final pour s'assurer que les dates les plus récentes apparaissent en premier
    resultat_reception.sort(key=lambda x: datetime.strptime(x["date_voyage"], "%d/%m/%Y"), reverse=True)

    return resultat_reception


@app.get("/api/traçabilite/{sub_tracking_code}")
def obtenir_parcours_colis(sub_tracking_code: str, db: Session = Depends(database.get_db)):
    """
    Récupère le parcours logistique complet et l'historique d'un ColisItem (pièce de fret)
    en remontant les relations SQL vers son colis maître, son voyage et les agences concernées.
    """
    
    # 1. Extraction de la pièce de fret spécifique via le module models
    item = db.query(models.ColisItem).filter(models.ColisItem.sub_tracking_code == sub_tracking_code).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Aucun élément trouvé pour ce sous-code de suivi."
        )

    # 2. Remontée vers le Colis maître (Données globales et clients)
    colis_principal = db.query(models.Colis).filter(models.Colis.id == item.colis_id).first()
    if not colis_principal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Le colis principal associé à cet item est introuvable."
        )

    # 3. Extraction des Agences de départ et destination via le Colis maître
    agence_dep = db.query(models.Agence).filter(models.Agence.id == colis_principal.id_agence_depart).first()
    agence_dest = db.query(models.Agence).filter(models.Agence.id == colis_principal.id_agence_destination).first()

    # 4. Identification du Voyage et du Bus (Si l'item est lié à un voyage actif)
    voyage = db.query(models.Voyage).filter(models.Voyage.id == item.voyage_id).first() if item.voyage_id else None
    bus = db.query(models.Bus).filter(models.Bus.id == voyage.id_bus).first() if voyage else None
    chauffeur = db.query(models.Agent).filter(models.Agent.id == voyage.id_chauffeur).first() if (voyage and voyage.id_chauffeur) else None

    # 5. Extraction de l'Agent responsable du dernier statut (Déchargement / Réception)
    agent_rcpt = db.query(models.Agent).filter(models.Agent.id == item.id_agent_reception).first() if item.id_agent_reception else None

    # Extraction des statuts pour centraliser la comparaison
    statut_item = item.statut  # 'Reçu', 'En transit', 'Arrivé'
    statut_maitre = colis_principal.statut  # Gère le statut 'Livré' global

    # ================= CONSTITUTION DYNAMIQUE DU TIMELINE =================
    etapes_reelles = []

    # Étape 1 : Réception Initiale à l'agence d'expédition
    date_dep_str = item.created_at.strftime("%d/%m/%Y") if item.created_at else "Date inconnue"
    heure_dep_str = item.created_at.strftime("%H:%M") if item.created_at else "--:--"
    
    etapes_reelles.append({
        "id": 1,
        "titre": "Réception & Enregistrement Fret",
        "description": f"La pièce '{item.nature_contenu}' a été pesée à {float(item.poids_kg)} kg et validée dans le système.",
        "ville": agence_dep.ville if agence_dep else "Inconnue",
        "agence": agence_dep.nom_agence if agence_dep else "Non spécifiée",
        "date": date_dep_str,
        "heure": heure_dep_str,
        "agent": "Système Classic Coach",
        "icon": "package",
        "status": "completed"
    })

    # Étape 2 : Transit / Soute du Bus (Si voyage et bus validés)
    if voyage and bus:
        date_voyage_str = voyage.date_depart.strftime("%d/%m/%Y") if voyage.date_depart else date_dep_str
        heure_voyage_str = voyage.date_depart.strftime("%H:%M") if voyage.date_depart else "--:--"
        
        etapes_reelles.append({
            "id": 2,
            "titre": "Embarquement dans le Bus",
            "description": "Colis chargé dans la soute du véhicule affecté au voyage.",
            "ville": agence_dep.ville if agence_dep else "Transit",
            "bus_plaque": bus.numero_plaque if bus.numero_plaque else "Bus Inconnu",
            "bus_modele": bus.modele or "Autocar Standard",
            "date": date_voyage_str,
            "heure": heure_voyage_str,
            "agent": chauffeur.nom_complet if chauffeur else "Chauffeur non assigné",
            "icon": "truck",
            # Complété si l'item est arrivé à destination ou si le colis global est déjà livré
            "status": "completed" if (statut_item == "Arrivé" or statut_maitre == "Livré") else "current"
        })

    # Étape 3 : Arrivée au point de stockage destination
    if (statut_item == "Arrivé" or statut_maitre == "Livré") and agence_dest:
        etapes_reelles.append({
            "id": 3,
            "titre": "Arrivée Destination & Déchargement",
            "description": "Le bus est arrivé au terminus. Fret stocké en magasin en attente de retrait.",
            "ville": agence_dest.ville,
            "agence": agence_dest.nom_agence,
            "date": "Validé", 
            "heure": "Scan Réception",
            "agent": agent_rcpt.nom_complet if agent_rcpt else "Magasinier Destination",
            "icon": "map-pin",
            # Reste l'étape active ('current') tant que le client n'est pas venu chercher le colis complet
            "status": "completed" if statut_maitre == "Livré" else "current"
        })

    # Étape 4 : Retrait Client (Clôture globale du flux basé sur le colis maître)
    if statut_maitre == "Livré":
        etapes_reelles.append({
            "id": 4,
            "titre": "Fret Clôturé & Livré",
            "description": "Le lot complet de colis a été retiré et remis en main propre après vérification de l'identité.",
            "ville": agence_dest.ville if agence_dest else "Destination",
            "agence": agence_dest.nom_agence if agence_dest else "Destination",
            "date": "Remis",
            "heure": "",
            "agent": agent_rcpt.nom_complet if agent_rcpt else "Guichetier",
            "icon": "shield-check",
            "status": "current"
        })

    # Payload propre structure pour ton interface React Premium
    return {
        "sub_tracking_code": item.sub_tracking_code,
        "nature_contenu": item.nature_contenu,
        "poids_kg": float(item.poids_kg),
        "statut_individuel": statut_item,
        "statut_global_colis": statut_maitre,  # Pratique pour ton front si tu veux afficher un badge global
        "colis_principal": {
            "tracking_code": colis_principal.tracking_code,
            "expediteur_nom": colis_principal.expediteur_nom,
            "expediteur_tel": colis_principal.expediteur_tel,
            "destinataire_nom": colis_principal.destinataire_nom,
            "destinataire_tel": colis_principal.destinataire_tel,
            "prix_transport": str(colis_principal.prix_transport)
        },
        "parcours": {
            "ville_depart": agence_dep.ville if agence_dep else "Inconnu",
            "agence_depart_nom": agence_dep.nom_agence if agence_dep else "Inconnu",
            "ville_destination": agence_dest.ville if agence_dest else "Inconnu",
            "agence_destination_nom": agence_dest.nom_agence if agence_dest else "Inconnu"
        },
        "etapes": etapes_reelles
    }





@app.post("/api/magasin/agences/{agence_id}/zones", response_model=schemas.ZoneResponse, status_code=status.HTTP_201_CREATED)
def creer_zone_magasin(agence_id: UUID, zone: schemas.ZoneCreate, db: Session = Depends(database.get_db)):
    """
    Permet à l'administrateur de configurer une grande zone ou allée dans une agence spécifique.
    """
    zone_existante = db.query(models.MagasinZone).filter(
        models.MagasinZone.id_agence == agence_id,
        models.MagasinZone.code_zone == zone.code_zone.upper()
    ).first()
    
    if zone_existante:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La zone '{zone.code_zone}' existe déjà dans cette agence."
        )
    
    nouvelle_zone = models.MagasinZone(
        id_agence=agence_id,
        code_zone=zone.code_zone.upper(),
        description=zone.description
    )
    db.add(nouvelle_zone)
    db.commit()
    db.refresh(nouvelle_zone)
    return nouvelle_zone

@app.get("/api/magasin/agences/{agence_id}/zones", response_model=List[schemas.ZoneResponse])
def lister_zones_agence(agence_id: UUID, db: Session = Depends(database.get_db)):
    """ Récupère toutes les zones configurées pour une agence donnée """
    return db.query(models.MagasinZone).filter(models.MagasinZone.id_agence == agence_id).all()


# =====================================================================
# 2. GESTION DES RACKS (ÉTAGÈRES) & GÉNÉRATION AUTOMATIQUE DES PLACES
# =====================================================================

@app.post("/api/magasin/zones/{zone_id}/racks", response_model=schemas.RackResponse, status_code=status.HTTP_201_CREATED)
def creer_rack_et_generer_emplacements(zone_id: UUID, rack: schemas.RackCreate, db: Session = Depends(database.get_db)):
    """
    Crée une étagère (Rack) et génère automatiquement ses cases de stockage (Emplacements)
    en fonction du nombre de sections et de la hauteur configurée.
    """
    zone = db.query(models.MagasinZone).filter(models.MagasinZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone parente introuvable.")

    rack_existant = db.query(models.MagasinRack).filter(
        models.MagasinRack.id_zone == zone_id,
        models.MagasinRack.code_rack == rack.code_rack.upper()
    ).first()
    
    if rack_existant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"L'étagère '{rack.code_rack}' existe déjà dans cette zone."
        )

    nouveau_rack = models.MagasinRack(
        id_zone=zone_id,
        code_rack=rack.code_rack.upper(),
        nombre_sections=rack.nombre_sections,
        hauteur_max_lettre=rack.hauteur_max_lettre.upper()
    )
    db.add(nouveau_rack)
    db.flush()

    start_char = ord('A')
    end_char = ord(rack.hauteur_max_lettre.upper())
    
    if end_char < start_char or end_char > ord('Z'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="La hauteur doit être une lettre valide entre A et Z."
        )

    for sec in range(1, rack.nombre_sections + 1):
        section_str = f"{sec:02d}"
        
        for char_code in range(start_char, end_char + 1):
            text_niveau = chr(char_code)
            
            # Niveau A (Sol) = 300kg, sinon les niveaux supérieurs sont bridés à 40kg
            poids_limite = 300.0 if text_niveau == 'A' else 40.0
            
            code_final = f"{zone.code_zone}-{nouveau_rack.code_rack}-{section_str}-{text_niveau}"
            
            nouvel_emplacement = models.MagasinEmplacement(
                id_rack=nouveau_rack.id,
                code_emplacement=code_final,
                section_index=section_str,
                niveau_index=text_niveau,
                poids_max_kg=poids_limite
            )
            db.add(nouvel_emplacement)

    db.commit()
    db.refresh(nouveau_rack)
    return nouveau_rack

@app.get("/api/magasin/zones/{zone_id}/racks", response_model=List[schemas.RackResponse])
def lister_racks_zone(zone_id: UUID, db: Session = Depends(database.get_db)):
    """ Liste toutes les étagères d'une zone """
    return db.query(models.MagasinRack).filter(models.MagasinRack.id_zone == zone_id).all()


# =====================================================================
# 3. VISUALISATION DES PLACES DU MAGASIN
# =====================================================================

@app.get("/api/magasin/racks/{rack_id}/emplacements", response_model=List[schemas.EmplacementResponse])
def lister_emplacements_dun_rack(rack_id: UUID, db: Session = Depends(database.get_db)):
    """ 
    Retourne toutes les cases générées pour une étagère (Grille Front-end)
    """
    return (
        db.query(models.MagasinEmplacement)
        .filter(models.MagasinEmplacement.id_rack == rack_id)
        .order_by(models.MagasinEmplacement.section_index, models.MagasinEmplacement.niveau_index)
        .all()
    )

import qrcode
import qrcode.image.svg
from fastapi.responses import Response

@app.get("/api/magasin/emplacements/{emplacement_id}/qrcode")
def obtenir_qrcode_emplacement(emplacement_id: UUID, db: Session = Depends(database.get_db)):
    """
    Génère un QR Code SVG unique pour un emplacement précis du magasin, 
    prêt à être scanné ou imprimé sur une étiquette.
    """
    emplacement = db.query(models.MagasinEmplacement).filter(models.MagasinEmplacement.id == emplacement_id).first()
    if not emplacement:
        raise HTTPException(status_code=404, detail="Emplacement introuvable.")
    
    # Le contenu du QR Code est le code unique de l'emplacement (ex: ZONE1-AA-01-A)
    qr_data = emplacement.code_emplacement
    
    # Génération du QR en format SVG (léger et ultra-propre pour l'impression)
    factory = qrcode.image.svg.SvgImage
    img = qrcode.make(qr_data, image_factory=factory, box_size=10, border=2)
    
    svg_bytes = img.to_string()
    
    return Response(content=svg_bytes, media_type="image/svg+xml")

@app.get("/api/magasin/emplacements/{emplacement_id}/contenu", response_model=List[dict])
def obtenir_contenu_emplacement(emplacement_id: UUID, db: Session = Depends(database.get_db)):
    """
    Récupère la liste de toutes les pièces (ColisItem) actuellement stockées
    dans un emplacement précis, avec les infos de leur colis global.
    """
    # 1. On cherche les items liés à cet emplacement
    items = db.query(models.ColisItem).filter(models.ColisItem.id_emplacement == emplacement_id).all()
    
    resultat = []
    for item in items:
        # On récupère les infos du colis parent pour avoir le tracking général si besoin
        colis_parent = db.query(models.Colis).filter(models.Colis.id == item.colis_id).first()
        
        resultat.append({
            "id": str(item.id),
            "sub_tracking_code": item.sub_tracking_code,
            "poids_kg": item.poids_kg,
            "statut_item": item.statut, # ex: 'Reçu', 'En stock'
            "tracking_code_global": colis_parent.tracking_code if colis_parent else "Inconnu"
        })
        
    return resultat



@app.get("/colis/verifier/{code}")
async def verifier_colis_ou_item(code: str, db: Session = Depends(database.get_db)):
    """
    Endpoint intelligent : Analyse le code (global ou sub-tracking) 
    et retourne son emplacement actuel avant transfert.
    """
    clean_code = code.strip()
    
    # Vérification Cas 1 : C'est une pièce spécifique (ColisItem)
    item = db.query(models.ColisItem).filter(models.ColisItem.sub_tracking_code == clean_code).first()
    if item:
        emplacement_actuel = "Aucun"
        if item.id_emplacement:
            emp = db.query(models.MagasinEmplacement).filter(models.MagasinEmplacement.id == item.id_emplacement).first()
            if emp:
                emplacement_actuel = emp.code_emplacement
        
        return {
            "type": "ITEM_INDIVIDUEL",
            "code": item.sub_tracking_code,
            "emplacement_actuel": emplacement_actuel,
            "details": f"Pièce ({item.poids_kg} kg)"
        }
        
    # Vérification Cas 2 : C'est un colis global
    colis = db.query(models.Colis).filter(models.Colis.tracking_code == clean_code).first()
    if colis:
        tous_les_items = db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).all()
        
        # Récupération des emplacements uniques des pièces du colis
        emplacements = set()
        for it in tous_les_items:
            if it.id_emplacement:
                emp = db.query(models.MagasinEmplacement).filter(models.MagasinEmplacement.id == it.id_emplacement).first()
                if emp:
                    emplacements.add(emp.code_emplacement)
        
        emplacement_actuel = ", ".join(emplacements) if emplacements else "Aucun"
        return {
            "type": "COLIS_GLOBAL",
            "code": colis.tracking_code,
            "emplacement_actuel": emplacement_actuel,
            "details": f"{len(tous_les_items)} pièce(s) incluse(s)"
        }

    raise HTTPException(status_code=404, detail=f"Code [{clean_code}] inconnu (ni colis, ni pièce).")


from pydantic import BaseModel

# 1. Définir le schéma attendu dans le Body
class TransfertRequest(BaseModel):
    item_or_colis_code: str
    target_emplacement_code: str
    agent_id: str

@app.post("/colis/transferer")
async def transferer_colis(
    payload: TransfertRequest,  # Utilisation du schéma ici
    db: Session = Depends(database.get_db)
):
    # 2. Extraire les variables nettoyées depuis le payload
    clean_item_code = str(payload.item_or_colis_code).strip()
    clean_rack_code = str(payload.target_emplacement_code).strip()
    agent_id = str(payload.agent_id).strip()
    
    print(f"\n========== 🔄 LOGIQUE DE TRANSFERT LOGISTIQUE ==========")
    print(f"Cible : {clean_item_code} ---> Nouvel Emplacement : {clean_rack_code}")

    try:
        u_agent_id = UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format ID agent invalide.")

    agent = db.query(models.Agent).filter(models.Agent.id == u_agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent non identifié.")

    # Validation de l'emplacement cible
    nouvel_emplacement = db.query(models.MagasinEmplacement).filter(
        models.MagasinEmplacement.code_emplacement == clean_rack_code
    ).first()

    if not nouvel_emplacement:
        raise HTTPException(status_code=404, detail=f"L'emplacement [{clean_rack_code}] n'existe pas.")

    if nouvel_emplacement.statut == "occupé":
        raise HTTPException(status_code=400, detail=f"L'emplacement [{clean_rack_code}] est déjà occupé.")

    item_scanne = db.query(models.ColisItem).filter(models.ColisItem.sub_tracking_code == clean_item_code).first()
    
    try:
        # --- CAS 1 : C'EST UNE PIÈCE SPÉCIFIQUE (ColisItem) ---
        if item_scanne:
            if item_scanne.poids_kg > nouvel_emplacement.poids_max_kg:
                raise HTTPException(status_code=400, detail="Sécurité Charge maximale dépassée.")

            ancien_emplacement_id = item_scanne.id_emplacement
            item_scanne.id_emplacement = nouvel_emplacement.id
            nouvel_emplacement.statut = "occupé"
            
            if ancien_emplacement_id:
                reste = db.query(models.ColisItem).filter(
                    models.ColisItem.id_emplacement == ancien_emplacement_id,
                    models.ColisItem.id != item_scanne.id
                ).first()
                if not reste:
                    ancien_emp = db.query(models.MagasinEmplacement).filter(models.MagasinEmplacement.id == ancien_emplacement_id).first()
                    if map_emp := ancien_emp:
                        ancien_emp.statut = "disponible"

            db.commit()
            return {"status": "success", "message": f"Pièce déplacée vers {nouvel_emplacement.code_emplacement}."}

        # --- CAS 2 : C'EST UN COLIS GLOBAL ---
        else:
            colis = db.query(models.Colis).filter(models.Colis.tracking_code == clean_item_code).first()
            if not colis:
                raise HTTPException(status_code=404, detail="Colis ou pièce introuvable.")
            
            tous_les_items = db.query(models.ColisItem).filter(models.ColisItem.colis_id == colis.id).all()
            if not tous_les_items:
                raise HTTPException(status_code=400, detail="Ce colis ne contient aucune pièce.")

            anciens_emplacements_ids = list({item.id_emplacement for item in tous_les_items if item.id_emplacement})

            for item in tous_les_items:
                item.id_emplacement = nouvel_emplacement.id
            
            nouvel_emplacement.statut = "occupé"

            for anc_id in anciens_emplacements_ids:
                reste = db.query(models.ColisItem).filter(models.ColisItem.id_emplacement == anc_id, models.ColisItem.colis_id != colis.id).first()
                if not reste:
                    ancien_emp = db.query(models.MagasinEmplacement).filter(models.MagasinEmplacement.id == anc_id).first()
                    if ancien_emp:
                        ancien_emp.statut = "disponible"

            db.commit()
            return {"status": "success", "message": f"Les {len(tous_les_items)} pièces ont été déplacées vers {nouvel_emplacement.code_emplacement}."}

    except HTTPException as he:
        db.rollback()
        raise he
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erreur serveur : {str(e)}")





@app.post("/api/voyages/{voyage_id}/cloturer")
async def cloturer_voyage(
    voyage_id: str, 
    agent_id: str, 
    db: Session = Depends(database.get_db)
):
    print("\n========== CLÔTURE DE VOYAGE & MANIFESTE ==========")
    
    # 1. Validation des UUIDs
    try:
        u_voyage_id = UUID(voyage_id)
        u_agent_id = UUID(agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format UUID invalide pour le voyage ou l'agent.")

    # 2. Récupération du voyage avec ses relations (bus, ligne)
    voyage = db.query(models.Voyage).filter(models.Voyage.id == u_voyage_id).first()
    if not voyage:
        raise HTTPException(status_code=404, detail="Voyage introuvable.")

    # 3. Vérification du cycle de vie (Sécurité)
    if voyage.statut != "en_preparation":
        raise HTTPException(
            status_code=400, 
            detail=f"Impossible de clôturer : ce voyage est déjà {voyage.statut}."
        )

    # 4. Changement de statut de la feuille de route (Verrouillage du Bus)
    voyage.statut = "en_cours"
    
    # 5. Extraction des pièces embarquées pour le Manifeste
    # On cible les items liés à ce voyage et dont le statut est passé "En transit" lors du scan
    pieces_embarquees = db.query(models.ColisItem).filter(
        models.ColisItem.voyage_id == u_voyage_id,
        models.ColisItem.statut == "En transit"
    ).all()

    # 6. Structuration des données du Manifeste pour l'impression frontend
    liste_manifeste = []
    poids_total_manifeste = 0.0

    for item in pieces_embarquees:
        parent = item.colis_principal # Utilisation de ta "relation magique" SQLAlchemy
        poids_item = float(item.poids_kg) if item.poids_kg else 0.0
        poids_total_manifeste += poids_item

        liste_manifeste.append({
            "sub_tracking_code": item.sub_tracking_code,
            "nature_piece": item.nature_contenu,
            "poids_kg": poids_item,
            "recu_parent": {
                "tracking_code": parent.tracking_code,
                "expediteur": parent.expediteur_nom,
                "expediteur_tel": parent.expediteur_tel,
                "destinataire": parent.destinataire_nom,
                "destinataire_tel": parent.destinataire_tel,
                "destination_finale": voyage.ligne.id_agence_destination if voyage.ligne else None
            }
        })

    # On valide les changements en BDD
    db.commit()
    
    print(f"Voyage {voyage_id} verrouillé. {len(liste_manifeste)} pièces enregistrées au manifeste.")
    print("==================================================\n")

    return {
        "status": "success",
        "message": "Le chargement est clos. Le bus est verrouillé et prêt pour le départ.",
        "voyage": {
            "id": str(voyage.id),
            "statut": voyage.statut,
            "bus_plaque": voyage.bus.numero_plaque if voyage.bus else "Inconnu",
            "date_depart": voyage.date_depart.isoformat() if voyage.date_depart else None
        },
        "manifeste": {
            "nombre_total_pieces": len(liste_manifeste),
            "poids_total_fret_kg": poids_total_manifeste,
            "pieces": liste_manifeste
        }
    }




@app.get("/api/voyages/{voyage_id}/manifeste")
async def obtenir_manifeste_voyage(
    voyage_id: str, 
    db: Session = Depends(database.get_db)
):
    print(f"\n=== EXTRACTION MANIFESTE PREMIUM POUR LE VOYAGE : {voyage_id} ===")
    
    try:
        u_voyage_id = UUID(voyage_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format UUID du voyage invalide.")

    # Chargement du voyage
    voyage = db.query(models.Voyage).filter(models.Voyage.id == u_voyage_id).first()
    if not voyage:
        raise HTTPException(status_code=404, detail="Voyage introuvable.")

    # Récupération du chauffeur (table agents)
    nom_chauffeur = "Non assigné"
    if voyage.id_chauffeur:
        chauffeur = db.query(models.Agent).filter(models.Agent.id == voyage.id_chauffeur).first()
        if chauffeur:
            nom_chauffeur = chauffeur.nom_complet

    # Récupération des informations d'agences via la relation ligne
    agence_dep = voyage.ligne.agence_depart if voyage.ligne else None
    agence_dest = voyage.ligne.agence_destination if voyage.ligne else None

    # Extraction des pièces chargées sur ce voyage
    pieces_embarquees = db.query(models.ColisItem).filter(
        models.ColisItem.voyage_id == u_voyage_id
    ).all()

    liste_pieces = []
    poids_total = 0.0

    for item in pieces_embarquees:
        parent = item.colis_principal
        agent_rec = item.agent_reception
        poids_item = float(item.poids_kg) if item.poids_kg else 0.0
        poids_total += poids_item

        liste_pieces.append({
            "sub_tracking_code": item.sub_tracking_code,
            "nature_piece": item.nature_contenu,
            "poids_kg": poids_item,
            "statut": item.statut,
            "date_reception": item.created_at.strftime("%d/%m/%Y %H:%M") if item.created_at else "---",
            "agent_reception_nom": agent_rec.nom_complet if agent_rec else "Système",
            "colis_parent": {
                "tracking_code": parent.tracking_code if parent else "---",
                "expediteur": parent.expediteur_nom if parent else "---",
                "expediteur_tel": parent.expediteur_tel if parent else "---",
                "destinataire": parent.destinataire_nom if parent else "---",
                "destinataire_tel": parent.destinataire_tel if parent else "---"
            }
        })

    # Génération de l'URL du QR Code via le service hautement disponible QRServer
    # Format standardisé injecté dans le QR code : VOYAGE:UUID
    voyage_data_string = f"VOYAGE:{voyage.id}"
    qrcode_url = f"https://api.qrserver.com/v1/create-qr-code/?size=150x150&data={voyage_data_string}"

    return {
        "manifeste_details": {
            "voyage_id": str(voyage.id),
            "qrcode_manifeste_url": qrcode_url,
            "statut_voyage": voyage.statut.upper(),
            "date_depart_prevue": voyage.date_depart.strftime("%d/%m/%Y %H:%M") if voyage.date_depart else "---",
            "date_generation": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "chauffeur": nom_chauffeur,
            "bus": {
                "plaque": voyage.bus.numero_plaque if voyage.bus else "Inconnu",
                "modele": voyage.bus.modele if voyage.bus else "Inconnu",
                "capacite_max_kg": float(voyage.bus.capacite_colis_kg) if voyage.bus and voyage.bus.capacite_colis_kg else 0.0
            },
            "agence_depart": {
                "nom": agence_dep.nom_agence if agence_dep else "Inconnue",
                "ville": agence_dep.ville if agence_dep else "---"
            },
            "agence_destination": {
                "nom": agence_dest.nom_agence if agence_dest else "Inconnue",
                "ville": agence_dest.ville if agence_dest else "---"
            },
            "statistiques": {
                "nombre_total_pieces": len(liste_pieces),
                "poids_total_charge_kg": round(poids_total, 2)
            }
        },
        "pieces": liste_pieces
    }



@app.get("/voyages/historique")
def get_voyages_historique(id_agence: Optional[UUID] = None, flux: Optional[str] = None, db: Session = Depends(database.get_db)):
    # 🌟 CORRECTION : On cherche les voyages "termine" au lieu de "arrive"
    query = db.query(models.Voyage).filter(models.Voyage.statut.in_(["termine", "annule"]))
    
    if id_agence:
        query = query.join(models.Ligne)
        if flux == "depart":
            query = query.filter(models.Ligne.id_agence_depart == id_agence)
        elif flux == "destination":
            query = query.filter(models.Ligne.id_agence_destination == id_agence)
        else:
            query = query.filter(or_(models.Ligne.id_agence_depart == id_agence, models.Ligne.id_agence_destination == id_agence))
            
    voyages = query.order_by(models.Voyage.date_depart.desc()).all()
    resultat = []
    
    for v in voyages:
        bus = db.query(models.Bus).filter(models.Bus.id == v.id_bus).first()
        ag_dep = db.query(models.Agence).filter(models.Agence.id == v.ligne.id_agence_depart).first() if v.ligne else None
        ag_dest = db.query(models.Agence).filter(models.Agence.id == v.ligne.id_agence_destination).first() if v.ligne else None
        agent = db.query(models.Agent).filter(models.Agent.id == v.id_chauffeur).first() if v.id_chauffeur else None
        
        pieces_embarquees = db.query(models.ColisItem).filter(models.ColisItem.voyage_id == v.id).all()
        poids_total_bus = sum(float(p.poids_kg) if p.poids_kg else 0.0 for p in pieces_embarquees)

        resultat.append({
            "id": v.id,
            "bus_plaque": bus.numero_plaque if bus else "Inconnu",
            "bus_modele": bus.modele if bus else "",
            "nombre_pieces": len(pieces_embarquees),
            "poids_total": poids_total_bus,
            "agence_depart": ag_dep.nom_agence if ag_dep else "Inconnu",
            "agence_destination": ag_dest.nom_agence if ag_dest else "Inconnu",
            "date_depart": v.date_depart,
            # 🌟 TRANSFORMATION : On renvoie "arrive" si c'est "termine" pour plaire à ton composant React
            "statut": "arrive" if v.statut == "termine" else v.statut,
            "nom_chauffeur": agent.nom_complet if agent else "Non assigné"
        })
        
    return resultat

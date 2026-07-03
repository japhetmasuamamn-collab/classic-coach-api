from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
import database, models

router = APIRouter(
    prefix="/documents",
    tags=["Documents"]
)

@router.get("/manifeste-{id_voyage}/preview", response_class=HTMLResponse)
def apercu_manifeste_voyage(id_voyage: str, db: Session = Depends(database.get_db)):
    # 1. Récupérer les données du voyage
    voyage = db.query(models.Voyage).filter(models.Voyage.id == id_voyage).first()
    if not voyage:
        raise HTTPException(status_code=404, detail="Voyage introuvable.")

    # 2. Extraction sécurisée via les relations SQLAlchemy existantes
    bus_plaque = voyage.bus.numero_plaque if voyage.bus else "Non assigné"
    bus_modele = voyage.bus.modele if voyage.bus else "Bus Standard"
    
    # Récupération de l'axe routier (Ex: Kolwezi -> Lubumbashi)
    nom_axe = "Axe non défini"
    if voyage.ligne:
        agence_dep = voyage.ligne.agence_depart.ville if voyage.ligne.agence_depart else "Départ"
        agence_dest = voyage.ligne.agence_destination.ville if voyage.ligne.agence_destination else "Destination"
        nom_axe = f"{agence_dep} ➔ {agence_dest}"

    # Récupération du chauffeur assigné
    chauffeur_nom = "Non assigné"
    chauffeur_tel = "-"
    if voyage.id_vrai_chauffeur:
        chauffeur = db.query(models.Chauffeur).filter(models.Chauffeur.id == voyage.id_vrai_chauffeur).first()
        if chauffeur:
            chauffeur_nom = chauffeur.nom_complet
            chauffeur_tel = chauffeur.telephone

    # 3. DONNÉES STATIQUES DE DÉMONSTRATION (Simulées en attendant la gestion des modules)
    passagers_maquette = [
        {"siege": "01", "nom": "KABANGE MUTOMBO Jean", "cni": "ID-102938-A", "tel": "+243 812 345 678"},
        {"siege": "02", "nom": "MWAMBA BANZA Dorcas", "cni": "ID-584930-B", "tel": "+243 997 123 456"},
        {"siege": "03", "nom": "ILUNGA KANSHIMBA Eric", "cni": "ID-992039-C", "tel": "+243 854 987 654"}
    ]

    colis_maquette = [
        {"code": "CL-2606-01", "desc": "Carton de marchandises (Pièces rechange)", "exp": "MUKENDI Marc (+243 821 000 111)", "dest": "KAYEMBE Paul", "paiement": "PAYÉ (Caisse)"},
        {"code": "CL-2606-02", "desc": "Sac de friperie", "exp": "NGOY Alice (+243 810 222 333)", "dest": "ILUNGA Jean", "paiement": "À PERCEVOIR"}
    ]

    # 4. Génération du rendu HTML A4 professionnel
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>APERÇU - MANIFESTE DE ROUTE</title>
        <style>
            body {{ font-family: monospace; color: #000; padding: 20px; font-size: 11px; line-height: 1.4; }}
            .header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #000; padding-bottom: 10px; margin-bottom: 20px; }}
            .watermark {{ position: fixed; top: 40%; left: 10%; transform: rotate(-35deg); font-size: 65px; color: rgba(0,0,0,0.05); font-weight: bold; pointer-events: none; white-space: nowrap; }}
            .meta-section {{ display: flex; justify-content: space-between; margin-bottom: 20px; border: 1px solid #000; padding: 10px; background-color: #fafafa; }}
            .meta-column {{ width: 48%; }}
            table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 10px; }}
            th, td {{ border: 1px solid #000; padding: 6px; text-align: left; }}
            th {{ background-color: #f2f2f2; font-weight: bold; text-transform: uppercase; }}
            h3 {{ text-transform: uppercase; border-bottom: 1px solid #000; padding-bottom: 3px; margin-top: 20px; font-size: 12px; }}
            .footer {{ margin-top: 40px; display: flex; justify-content: space-between; page-break-inside: avoid; }}
            .signature-box {{ border: 1px dashed #000; width: 45%; height: 75px; padding: 6px; }}
            @media print {{
                .no-print {{ display: none; }}
                body {{ padding: 0; }}
            }}
        </style>
    </head>
    <body>
        <div class="watermark">MAQUETTE - NON SCELLÉ</div>

        <div class="no-print" style="background: #fff3cd; padding: 12px; border: 1px solid #ffeeba; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; font-family: sans-serif;">
            <span style="color: #856404; font-size: 12px;"><strong>💡 Mode Maquette :</strong> Les sections passagers et colis utilisent des données simulées. Les métadonnées du haut sont réelles.</span>
            <button onclick="window.print()" style="padding: 6px 12px; background: #000; color: #fff; border: none; cursor: pointer; font-family: monospace; font-weight: bold; font-size: 11px;">IMPRIMER L'APERÇU</button>
        </div>

        <div class="header">
            <div>
                <h1 style="margin: 0; font-size: 18px; font-weight: bold; letter-spacing: 1px;">CLASSIC COACH LOGISTICS</h1>
                <p style="margin: 3px 0 0 0; font-size: 10px; text-transform: uppercase; color: #555;">Suivi des Flux Transports Inter-Agences</p>
            </div>
            <div style="text-align: right;">
                <h2 style="margin: 0; font-size: 14px; font-weight: bold;">MANIFESTE DE ROUTE</h2>
                <p style="margin: 3px 0 0 0; font-size: 10px; font-weight: bold;">ID VOYAGE : #{str(voyage.id)[:8].upper()}</p>
            </div>
        </div>

        <div class="meta-section">
            <div class="meta-column">
                <p style="margin: 2px 0;"><strong>AXE ROUTIER :</strong> {nom_axe}</p>
                <p style="margin: 2px 0;"><strong>DATE DÉPART :</strong> {voyage.date_depart.strftime('%d/%m/%Y à %H:%M')}</p>
                <p style="margin: 2px 0;"><strong>STATUT SYSTEME :</strong> <span style="text-transform: uppercase; background-color: #000; color: #fff; padding: 0 4px;">{voyage.statut}</span></p>
            </div>
            <div class="meta-column" style="text-align: right;">
                <p style="margin: 2px 0;"><strong>VEHICULE :</strong> {bus_modele} ({bus_plaque})</p>
                <p style="margin: 2px 0;"><strong>CHAUFFEUR :</strong> {chauffeur_nom}</p>
                <p style="margin: 2px 0;"><strong>CONTACT CHAUFFEUR :</strong> {chauffeur_tel}</p>
            </div>
        </div>

        <h3>1. Liste des Passagers à Bord ({len(passagers_maquette)} Simulés)</h3>
        <table>
            <thead>
                <tr>
                    <th style="width: 8%;">Siège</th>
                    <th style="width: 40%;">Nom Complet</th>
                    <th style="width: 25%;">N° Pièce d'Identité</th>
                    <th style="width: 27%;">Numéro de Téléphone</th>
                </tr>
            </thead>
            <tbody>
                {"".join([f"<tr><td><strong>{p['siege']}</strong></td><td>{p['nom']}</td><td>{p['cni']}</td><td>{p['tel']}</td></tr>" for p in passagers_maquette])}
            </tbody>
        </table>

        <h3>2. Manifeste du Fret & Colisage ({len(colis_maquette)} Simulés)</h3>
        <table>
            <thead>
                <tr>
                    <th style="width: 15%;">N° Bordereau</th>
                    <th style="width: 30%;">Description Contenu</th>
                    <th style="width: 30%;">Expéditeur</th>
                    <th style="width: 13%;">Destinataire</th>
                    <th style="width: 12%;">Statut Reglement</th>
                </tr>
            </thead>
            <tbody>
                {"".join([f"<tr><td><code>{c['code']}</code></td><td>{c['desc']}</td><td>{c['exp']}</td><td>{c['dest']}</td><td><strong>{c['paiement']}</strong></td></tr>" for c in colis_maquette])}
            </tbody>
        </table>

        <div class="footer">
            <div class="signature-box">
                <span style="font-size: 9px; font-weight: bold; text-decoration: underline;">Visa Agent de Chargement :</span>
                <p style="margin: 5px 0 0 0; font-size: 8px; color: #666;">Certifie l'exactitude du fret embarqué.</p>
            </div>
            <div class="signature-box">
                <span style="font-size: 9px; font-weight: bold; text-decoration: underline;">Visa Prise en Charge Chauffeur :</span>
                <p style="margin: 5px 0 0 0; font-size: 8px; color: #666;">Reconnaît avoir pris la responsabilité des flux listés.</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content
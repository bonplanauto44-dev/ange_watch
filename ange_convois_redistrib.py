"""ange_convois_redistrib.py — redistribution convois d'un Grenier inactif.

Principe : les Greniers ont un delta positif → le plan ignore les dettes classiques
(condition D["delta"] >= 0 dans calcul_plan). On écrit donc directement dans les onglets
publics plutôt que dans le sheet Dettes.

Usage :
    python3 ange_convois_redistrib.py pseudo=djuman          # simulation initiale
    python3 ange_convois_redistrib.py pseudo=djuman ecrire=oui  # jour 1 (mise hors chaîne)
    python3 ange_convois_redistrib.py action=appliquer_jour  # cron nuit 23h50 UTC (00h50 Paris)
    python3 ange_convois_redistrib.py action=tester          # rapport intégrité seul
"""
import json, math, os, sys
from datetime import date

DOSSIER = os.path.dirname(os.path.abspath(__file__))
REDISTRIB_JSON = os.path.join(DOSSIER, "dettes_redistrib.json")
LISSAGE = 7


def _sheets():
    sys.path.insert(0, DOSSIER)
    import ange_convois as C
    import ange_convois_prod as PR
    return C, PR


def charger_sauv_convois(jour_plan=None):
    """Charge la dernière sauvegarde pre_<jour>.json."""
    import glob
    if jour_plan:
        path = os.path.join(DOSSIER, "sauvegardes_convois", f"pre_{jour_plan}.json")
    else:
        files = sorted(glob.glob(os.path.join(DOSSIER, "sauvegardes_convois", "pre_20*.json")))
        path = files[-1] if files else None
    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"Sauvegarde introuvable : {path}")
    return json.load(open(path, encoding="utf-8"))


def redistribuer(pseudo, ecrire=False, jour_plan=None):
    """Redistribue les convois d'un Grenier inactif sur tous les Greniers actifs.

    NB : n'insère PAS dans le sheet Dettes (les Greniers ont delta >= 0, le plan ignorerait
    ces entrées). Écrit dettes_redistrib.json et met à jour les onglets si ecrire=True.
    Retourne (lignes_sortie, nouvelles_dettes_simulées).
    """
    C, PR = _sheets()
    out = []

    # --- 1. Charger données plan ---
    sauv = charger_sauv_convois(jour_plan)
    prive_data = sauv.get("prive", {})
    base   = prive_data.get("Base convois", [])
    tdc_m  = prive_data.get("TDC moyenne", [])
    convois = prive_data.get("Convois", [])

    tdc_dict = {}
    for r in tdc_m[1:]:
        if len(r) >= 2 and r[0] and r[1]:
            try: tdc_dict[str(r[0]).lower()] = float(r[1])
            except: pass

    # --- 2. Vérifier pseudo est un Grenier ---
    cible = next((r for r in base[1:] if str(r[0]).lower() == pseudo.lower()), None)
    if not cible:
        return [f"ERREUR : {pseudo} introuvable dans la Base convois"], []
    if "grenier" not in str(cible[2]).lower():
        return [f"ERREUR : {pseudo} est {cible[2]}, pas un Grenier"], []
    out.append(f"{pseudo} — {cible[2]} — TDC moyen : {int(cible[4]):,}")

    # --- 3. Total réel (onglet public du joueur) ---
    total_reel = None
    try:
        sh_g = C.client().open_by_key(PR.PROD["greniers"])
        ws = sh_g.worksheet(pseudo)
        vals = C.avec_reprise(lambda: ws.get("B2:B2", value_render_option="UNFORMATTED_VALUE"))
        import re
        raw = str(vals[0][0]) if vals and vals[0] else ""
        m = re.search(r"reste\s+([\d\s ]+)", raw)
        if m:
            total_reel = int(m.group(1).replace(" ", "").replace("\xa0", ""))
            out.append(f"Reste affiché dans l'onglet : {total_reel:,} bois")
    except Exception as e:
        out.append(f"Lecture onglet impossible ({e}), fallback bois_a_envoyer × 2")

    if total_reel is None:
        total_reel = 2 * int(cible[5])
        out.append(f"Fallback : {total_reel:,} bois")

    # --- 4. Créanciers depuis les convois ---
    convois_cible = [r for r in convois if len(r) > 1 and str(r[1]).lower() == pseudo.lower()]
    if not convois_cible:
        return [f"ERREUR : aucun convoi pour {pseudo}"], []
    crediteurs_totaux = {}
    for r in convois_cible:
        dest = str(r[2])
        crediteurs_totaux[dest] = crediteurs_totaux.get(dest, 0) + int(r[3])
    plan_total = sum(crediteurs_totaux.values())
    out.append("Créanciers : " + ", ".join(f"{d} ({v:,})" for d, v in crediteurs_totaux.items()))

    # --- 5. Greniers actifs (hors pseudo) ---
    greniers = [r for r in base[1:] if len(r) > 2
                and "grenier" in str(r[2]).lower()
                and str(r[0]).lower() != pseudo.lower()]
    total_tdc = sum(tdc_dict.get(str(r[0]).lower(), float(r[4])) for r in greniers)
    out.append(f"Greniers actifs : {len(greniers)} — TDC total : {total_tdc:,.0f}")

    # --- 6. Calcul des parts proportionnelles ---
    nouvelles_dettes = []
    debiteurs_map = {}
    for r in greniers:
        p_g = str(r[0])
        tdc = tdc_dict.get(p_g.lower(), float(r[4]))
        part = round(total_reel * tdc / total_tdc)
        m85 = {c: round(part * v / plan_total) for c, v in crediteurs_totaux.items()}
        debiteurs_map[p_g] = {
            "poste": str(r[2]),
            "dette_totale_initiale": part,
            **{f"dette_initiale_{c.lower()}": v for c, v in m85.items()}
        }
        for cred, montant in m85.items():
            if montant > 0:
                nouvelles_dettes.append([p_g, cred, montant, montant])

    out.append(f"\nRedistribution {total_reel:,} bois → {len(greniers)} Greniers (lissé {LISSAGE}j) :")
    for p_g, info in sorted(debiteurs_map.items(), key=lambda x: -x[1]["dette_totale_initiale"]):
        t = info["dette_totale_initiale"]
        out.append(f"  {p_g:<20} {t:>10,}  +{math.ceil(t/LISSAGE):>8,}/j")

    if not ecrire:
        out.append("\n[SIMULATION — rien écrit]")
        return out, nouvelles_dettes

    # --- 7. Écrire dettes_redistrib.json ---
    suivi = {
        "actif": True,
        "pseudo_absent": pseudo,
        "poste_absent": str(cible[2]),
        "date_debut": date.today().isoformat(),
        "nb_jours": LISSAGE,
        "total_initial": total_reel,
        "crediteurs": list(crediteurs_totaux.keys()),
        "debiteurs": debiteurs_map,
        "historique_jours": [],
        "jour_actuel": 1,
        "restant_actuel": round(total_reel * (LISSAGE - 1) / LISSAGE),
        "dernier_test": None,
    }
    with open(REDISTRIB_JSON, "w", encoding="utf-8") as f:
        json.dump(suivi, f, indent=2, ensure_ascii=False)
    out.append(f"✅ dettes_redistrib.json écrit")

    # --- 8. Mise à jour onglets Jour 1 ---
    out.append("")
    out.extend(maj_onglets_jour(pseudo, ecrire=True, suivi=suivi))

    # --- 9. Tests post-écriture ---
    ok, total_att, erreurs = tester_redistrib(suivi=suivi)
    suivi["dernier_test"] = {
        "ok": ok, "total": total_att,
        "erreurs": erreurs[:5],
        "date": date.today().isoformat()
    }
    with open(REDISTRIB_JSON, "w", encoding="utf-8") as f:
        json.dump(suivi, f, indent=2, ensure_ascii=False)
    out.append(f"🧪 Tests J1 : {ok}/{total_att} OK{(' — ⚠️ ' + ', '.join(erreurs[:3])) if erreurs else ' ✅'}")
    return out, nouvelles_dettes


def maj_onglets_jour(pseudo=None, ecrire=False, suivi=None):
    """Ajoute les lignes de livraison redistrib dans les onglets des Greniers débiteurs.

    Modifie uniquement A9:I20 (livraisons) de chaque Grenier — PAS les sections
    réception A25:C36 des créanciers (elles déborderaient : 14 Greniers > 12 lignes max).
    """
    if suivi is None:
        if not os.path.exists(REDISTRIB_JSON):
            return ["ERREUR : dettes_redistrib.json introuvable"]
        suivi = json.load(open(REDISTRIB_JSON, encoding="utf-8"))
    if pseudo is None:
        pseudo = suivi.get("pseudo_absent", "")

    debiteurs = suivi.get("debiteurs", {})
    crediteurs = suivi.get("crediteurs", [])
    C, PR = _sheets()
    sh_g = C.client().open_by_key(PR.PROD["greniers"])
    out = []

    skipped_paires = set()   # paires (grenier, créancier) où l'onglet est plein → exclus des réceptions créancier

    for grenier_pseudo, info in debiteurs.items():
        try:
            ws = sh_g.worksheet(grenier_pseudo)
            vals = C.avec_reprise(lambda: ws.get("A9:I20", value_render_option="UNFORMATTED_VALUE"))

            # gspread tronque les lignes vides en fin de range :
            # si vals a N < 12 éléments, les lignes N+1..12 sont vides et libres
            premier_vide = None
            # Inventorier les créanciers dans l'onglet : redistrib (valeur ≈ par_jour) vs plan normal (valeur différente)
            cred_redistrib_presents = set()  # créanciers déjà présents avec la VALEUR redistrib exacte
            cred_plan_normal = set()         # créanciers présents avec une AUTRE valeur (plan normal)
            cred_attendus = {}  # {cred.lower(): par_jour} pour comparer
            for cred in crediteurs:
                cle = f"dette_initiale_{cred.lower()}"
                montant_total = info.get(cle, 0)
                if montant_total > 0:
                    cred_attendus[cred.lower()] = math.ceil(montant_total / LISSAGE)
            for idx, row in enumerate(vals):
                nom = row[0].strip().lower() if row and row[0] else ""
                if nom in cred_attendus:
                    valeur_onglet = row[1] if len(row) > 1 and isinstance(row[1], (int, float)) else 0
                    if valeur_onglet == cred_attendus[nom]:
                        cred_redistrib_presents.add(nom)   # redistrib déjà là
                    else:
                        cred_plan_normal.add(nom)           # plan normal, ne pas dupliquer NI ajouter redistrib
                if not row or not row[0]:
                    premier_vide = 9 + idx
                    break
            if premier_vide is None and len(vals) < 12:
                premier_vide = 9 + len(vals)  # première ligne au-delà des données retournées

            if premier_vide is None:
                out.append(f"SKIP {grenier_pseudo} : onglet plein (12/12)")
                for cred in crediteurs:
                    skipped_paires.add((grenier_pseudo, cred))
                continue

            # Les paires plan-normal → exclure aussi des réceptions créancier (évite désync)
            for cred in crediteurs:
                if cred.lower() in cred_plan_normal:
                    skipped_paires.add((grenier_pseudo, cred))

            nouvelles_lignes = []
            for cred in crediteurs:
                if cred.lower() in cred_redistrib_presents:
                    continue   # redistrib déjà présente — rien à faire
                if cred.lower() in cred_plan_normal:
                    continue   # plan normal → ne pas ajouter redistrib
                cle = f"dette_initiale_{cred.lower()}"
                montant_total = info.get(cle, 0)
                if montant_total <= 0:
                    continue
                if premier_vide + len(nouvelles_lignes) > 20:
                    out.append(f"SKIP {grenier_pseudo}→{cred} : dépassement A20")
                    skipped_paires.add((grenier_pseudo, cred))
                    continue
                par_jour = math.ceil(montant_total / LISSAGE)
                nouvelles_lignes.append((cred, par_jour))

            if not nouvelles_lignes:
                continue

            if ecrire:
                import urllib.parse
                PROFIL_URL = "https://s5.fourmizzz.fr/Membre.php?Pseudo="
                _q = lambda n: urllib.parse.quote(n, safe="-_.!~*'()")
                # Un seul batchUpdate : lien A, valeur B, checkbox C+dataValidation, formule H, retard I
                batch_rows = []
                for ligne_idx, (cred_nom, par_jour) in enumerate(nouvelles_lignes):
                    r_num = premier_vide + ligne_idx   # numéro ligne Sheets (1-indexed)
                    formula_h = (f'=IF($A{r_num}="";"";'
                                 f'IF($C{r_num};0;MAX(0;$B{r_num}-SUM($D{r_num}:$G{r_num}))))')
                    batch_rows.append({"values": [
                        {"userEnteredValue": {"stringValue": cred_nom},
                         "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": PROFIL_URL + _q(cred_nom)}}}]},
                        {"userEnteredValue": {"numberValue": par_jour}},
                        {"userEnteredValue": {"boolValue": False},
                         "dataValidation": {"condition": {"type": "BOOLEAN"}, "strict": True, "showCustomUi": True}},
                        {}, {}, {}, {},
                        {"userEnteredValue": {"formulaValue": formula_h}},
                        {"userEnteredValue": {"numberValue": 0}},
                    ]})
                C.avec_reprise(lambda: ws.spreadsheet.batch_update({"requests": [{
                    "updateCells": {
                        "range": {"sheetId": ws.id,
                                  "startRowIndex": premier_vide - 1,
                                  "endRowIndex": premier_vide - 1 + len(batch_rows),
                                  "startColumnIndex": 0, "endColumnIndex": 9},
                        "rows": batch_rows,
                        "fields": "userEnteredValue,textFormatRuns,dataValidation"
                    }
                }]}))
                detail = " + ".join(
                    f"{n}:{pj:,}" for n, pj in nouvelles_lignes
                )
                out.append(f"✅ {grenier_pseudo:<20} +{len(nouvelles_lignes)} ligne(s) A{premier_vide} [{detail}]")
            else:
                out.append(f"[SIM] {grenier_pseudo} : +{len(nouvelles_lignes)} dès A{premier_vide}")

        except Exception as e:
            out.append(f"⚠️ {grenier_pseudo} : ERREUR {e}")

    # --- Mise à jour des onglets CRÉANCIERS (réceptions extended A37:B50) ---
    # Zone A37+ = jamais touchée par prod.py (clôture nuit écrit A25:B36) ni pages_ecrire
    # Le contrôle lit désormais R_MAX=28 lignes depuis A25 → capture ces lignes
    R_EXT_START = 37
    import urllib.parse
    PROFIL_URL = "https://s5.fourmizzz.fr/Membre.php?Pseudo="
    _q2 = lambda n: urllib.parse.quote(n, safe="-_.!~*'()")
    # Ouvrir les 3 fichiers pour trouver l'onglet du créancier (il peut être Grenier/Passeur/Chasseur)
    _sh_p = C.client().open_by_key(PR.PROD["passeurs"])
    _sh_c = C.client().open_by_key(PR.PROD["chasseurs"])

    def _ws_cred(nom):
        """Cherche l'onglet du créancier dans greniers, puis passeurs, puis chasseurs."""
        for sh in (sh_g, _sh_p, _sh_c):
            try:
                return sh.worksheet(nom)
            except Exception:
                pass
        raise ValueError(f"Onglet introuvable pour le créancier {nom!r} dans les 3 fichiers")

    for cred_nom in crediteurs:
        try:
            ws_c = _ws_cred(cred_nom)
            # Calculer les lignes redistrib attendues pour ce créancier
            # (exclure les paires où l'onglet du débiteur était plein)
            lignes_cred = []
            for gren_pseudo, info in debiteurs.items():
                if (gren_pseudo, cred_nom) in skipped_paires:
                    continue
                cle = f"dette_initiale_{cred_nom.lower()}"
                montant_total = info.get(cle, 0)
                if montant_total <= 0:
                    continue
                par_jour = math.ceil(montant_total / LISSAGE)
                lignes_cred.append((gren_pseudo, par_jour))

            if ecrire:
                # 1. Effacer la zone extended (lignes R_EXT_START à R_EXT_START+16)
                n_clear = max(len(lignes_cred), 1) + 2
                clear_rows = [{"values": [{"userEnteredValue": {"stringValue": ""}},
                                          {"userEnteredValue": {"stringValue": ""}}]}
                              for _ in range(n_clear)]
                C.avec_reprise(lambda: ws_c.spreadsheet.batch_update({"requests": [{
                    "updateCells": {
                        "range": {"sheetId": ws_c.id,
                                  "startRowIndex": R_EXT_START - 1,
                                  "endRowIndex": R_EXT_START - 1 + n_clear,
                                  "startColumnIndex": 0, "endColumnIndex": 2},
                        "rows": clear_rows,
                        "fields": "userEnteredValue,textFormatRuns"
                    }
                }]}))
                # 2. Écrire les lignes redistrib avec lien profil
                if lignes_cred:
                    batch_cred = []
                    for gren_pseudo, par_jour in lignes_cred:
                        batch_cred.append({"values": [
                            {"userEnteredValue": {"stringValue": gren_pseudo},
                             "textFormatRuns": [{"startIndex": 0, "format": {
                                 "link": {"uri": PROFIL_URL + _q2(gren_pseudo)}}}]},
                            {"userEnteredValue": {"numberValue": par_jour}},
                        ]})
                    C.avec_reprise(lambda: ws_c.spreadsheet.batch_update({"requests": [{
                        "updateCells": {
                            "range": {"sheetId": ws_c.id,
                                      "startRowIndex": R_EXT_START - 1,
                                      "endRowIndex": R_EXT_START - 1 + len(batch_cred),
                                      "startColumnIndex": 0, "endColumnIndex": 2},
                            "rows": batch_cred,
                            "fields": "userEnteredValue,textFormatRuns"
                        }
                    }]}))
                    out.append(f"✅ {cred_nom:<20} réceptions A{R_EXT_START}:B{R_EXT_START+len(lignes_cred)-1} ({len(lignes_cred)} livreurs)")
            else:
                out.append(f"[SIM] {cred_nom} : {len(lignes_cred)} livreurs dès A{R_EXT_START}")

        except Exception as e:
            out.append(f"⚠️ créancier {cred_nom} : ERREUR {e}")

    return out


def tester_redistrib(suivi=None):
    """Vérifie que chaque Grenier a les lignes de livraison vers chaque créancier.

    Retourne (ok_count, total_attendu, erreurs[]).
    Rapide : lit seulement la colonne A9:A20 (noms destinataires) de chaque Grenier.
    """
    if suivi is None:
        if not os.path.exists(REDISTRIB_JSON):
            return 0, 0, ["Pas de dettes_redistrib.json"]
        suivi = json.load(open(REDISTRIB_JSON, encoding="utf-8"))
    if not suivi.get("actif"):
        return 0, 0, ["Redistrib inactive"]

    debiteurs = suivi.get("debiteurs", {})
    crediteurs = suivi.get("crediteurs", [])
    C, PR = _sheets()
    sh_g = C.client().open_by_key(PR.PROD["greniers"])

    ok_count = 0
    total = 0
    erreurs = []

    for grenier_pseudo, info in debiteurs.items():
        for cred in crediteurs:
            if info.get(f"dette_initiale_{cred.lower()}", 0) <= 0:
                continue
            total += 1
            try:
                ws = sh_g.worksheet(grenier_pseudo)
                vals = C.avec_reprise(lambda: ws.get("A9:A20", value_render_option="UNFORMATTED_VALUE"))
                noms = [str(r[0]).lower() for r in vals if r and r[0]]
                if cred.lower() in noms:
                    ok_count += 1
                else:
                    erreurs.append(f"❌ {grenier_pseudo}→{cred}")
            except Exception as e:
                erreurs.append(f"⚠️ {grenier_pseudo}: {e}")

    return ok_count, total, erreurs


def appliquer_jour_redistrib():
    """À lancer par cron à 23h50 UTC (00h50 Paris) pendant les 7 jours actifs.

    Ajoute les lignes de livraison APRÈS l'écriture du plan de minuit par prod.py,
    met à jour historique et métriques dans dettes_redistrib.json.
    """
    if not os.path.exists(REDISTRIB_JSON):
        return ["[OK] Pas de redistrib active (fichier absent)"]

    suivi = json.load(open(REDISTRIB_JSON, encoding="utf-8"))
    if not suivi.get("actif"):
        return ["[OK] Redistrib inactive"]

    date_debut = date.fromisoformat(suivi["date_debut"])
    today = date.today()
    jour_num = (today - date_debut).days + 1

    if jour_num > suivi["nb_jours"]:
        suivi["actif"] = False
        suivi["jour_actuel"] = jour_num
        suivi["restant_actuel"] = 0
        with open(REDISTRIB_JSON, "w", encoding="utf-8") as f:
            json.dump(suivi, f, indent=2, ensure_ascii=False)
        return [f"✅ Redistrib terminée — {suivi['nb_jours']} jours traités, actif=False"]

    if jour_num < 2:
        return [f"[INFO] Jour {jour_num} : géré au moment de la mise hors chaîne"]

    out = [f"=== Redistrib {suivi['pseudo_absent']} — Jour {jour_num}/{suivi['nb_jours']} ==="]

    # Écrire les lignes dans les onglets Greniers
    out.extend(maj_onglets_jour(suivi.get("pseudo_absent"), ecrire=True, suivi=suivi))

    # Tests d'intégrité
    ok, total_att, erreurs = tester_redistrib(suivi=suivi)
    out.append(f"🧪 Tests : {ok}/{total_att} OK{(' — ' + '; '.join(erreurs[:3])) if erreurs else ' ✅'}")

    # Décroissance linéaire du restant
    total_init = suivi.get("total_initial", 0)
    restant = round(total_init * max(0, suivi["nb_jours"] - jour_num) / suivi["nb_jours"])

    suivi["jour_actuel"] = jour_num
    suivi["restant_actuel"] = restant
    suivi["dernier_test"] = {
        "ok": ok, "total": total_att,
        "erreurs": erreurs[:5],
        "date": today.isoformat()
    }
    suivi.setdefault("historique_jours", []).append({
        "jour": jour_num,
        "date": today.isoformat(),
        "ok": ok,
        "total": total_att,
        "erreurs_count": len(erreurs)
    })
    with open(REDISTRIB_JSON, "w", encoding="utf-8") as f:
        json.dump(suivi, f, indent=2, ensure_ascii=False)

    out.append(f"Restant estimé : {restant:,} bois ({round(restant / 1_000_000, 1)}M)")
    return out


def AUTOTEST():
    """Tests unitaires rapides — appelés par le démon avant activation."""
    assert LISSAGE == 7, "LISSAGE doit être 7"
    assert callable(redistribuer), "redistribuer manquant"
    assert callable(maj_onglets_jour), "maj_onglets_jour manquant"
    assert callable(tester_redistrib), "tester_redistrib manquant"
    assert callable(appliquer_jour_redistrib), "appliquer_jour_redistrib manquant"
    # Vérifier tester_redistrib sans fichier JSON → retourne (0, 0, [...])
    import tempfile, os as _os
    tmp = REDISTRIB_JSON + ".bak_autotest"
    if _os.path.exists(REDISTRIB_JSON):
        _os.rename(REDISTRIB_JSON, tmp)
    try:
        ok, tot, err = tester_redistrib()
        assert tot == 0, f"tot={tot} attendu 0 sans JSON"
    finally:
        if _os.path.exists(tmp):
            _os.rename(tmp, REDISTRIB_JSON)
    print("AUTOTEST ange_convois_redistrib : OK")


if __name__ == "__main__":
    prm = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    action = prm.get("action", "")
    pseudo = prm.get("pseudo", "")
    ecrire = prm.get("ecrire", "non").lower() == "oui"

    if action == "appliquer_jour":
        for l in appliquer_jour_redistrib():
            print(l)
    elif action == "tester":
        ok, tot, errs = tester_redistrib()
        print(f"Tests : {ok}/{tot} OK")
        for e in errs:
            print(f"  {e}")
    elif pseudo:
        lines, _ = redistribuer(pseudo, ecrire)
        for l in lines:
            print(l)
    else:
        print("Usage :")
        print("  python3 ange_convois_redistrib.py pseudo=PSEUDO [ecrire=oui]")
        print("  python3 ange_convois_redistrib.py action=appliquer_jour")
        print("  python3 ange_convois_redistrib.py action=tester")

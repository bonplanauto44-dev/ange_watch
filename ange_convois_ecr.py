"""ange_convois_ecr.py : écriture du plan du jour dans la feuille privée (port de majConvois : TDC moyenne, Dettes, Base convois,
Historiques, Convois). SÉCURITÉ : n'écrit que dans la copie TEST (ouvrir_test refuse la prod). La comparaison avec la prod est en lecture seule."""
import json, math, os, sqlite3
from datetime import datetime, timedelta

import ange_convois as C
import ange_convois_nuit as N
import ange_convois_plan as P

JOURS_HISTORIQUE = 14
MOYENNE_MIN_RELEVES = 12
ENT_BASE = ["Jour", "Joueur", "Chaîne", "Poste", "TDC cible", "TDC compté", "Récolte du jour (× 48)", "Flux de colonisation", "Delta de convoi", "Base"]
ENT_PLAN = ["Jour", "Expéditeur", "Chaîne expéditeur", "Destinataire", "Chaîne destinataire", "Quantité", "Distance"]


def _paris(dt=None):
    from zoneinfo import ZoneInfo
    return (dt or datetime.now(ZoneInfo("Europe/Paris"))).astimezone(ZoneInfo("Europe/Paris"))


def aujourdhui_paris():
    return _paris().strftime("%Y-%m-%d")


def veille_de(jour):
    return (datetime.strptime(jour, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def lignes_moyenne(conn, jour):
    """Contenu de l'onglet « TDC moyenne » pour le jour Paris `jour` : (lignes triées, nb de relevés du jour)."""
    moy = C.moyenne_jour(conn, jour)
    deb, fin = C.bornes_jour_paris(jour)
    nb = conn.execute("SELECT COUNT(DISTINCT version) FROM tdc WHERE alliance='ANGE' AND version>=? AND version<?", (deb, fin)).fetchone()[0]
    lignes = [[p, C.arrondi_js(m), n] for p, (m, n) in sorted(moy.items())]      # ordre ASCII comme la prod (20cyl, 6gale, Aaaaa…)
    return lignes, nb


def ecrire_moyenne(prive, conn, jour_moyenne, ecrire):
    lignes, nb = lignes_moyenne(conn, jour_moyenne)
    if ecrire:
        ws = prive.worksheet("TDC moyenne")
        C.avec_reprise(lambda: ws.batch_clear(["A2:C1000"]))
        if lignes:
            C.avec_reprise(lambda: ws.update(range_name="A2", values=lignes, value_input_option="RAW"))
        meta = [["Jour moyenné", jour_moyenne], ["Relevés utilisés", nb], ["Calculé le", _paris().strftime("%d/%m/%Y")]]
        C.avec_reprise(lambda: ws.update(range_name="E1", values=meta, value_input_option="RAW"))
    return lignes, nb


def lire_base(prive):
    """Lignes de « Base convois » (mode moyenne), dans l'ordre de « Chaîne unique »."""
    ws = prive.worksheet("Chaîne unique")
    v = C.avec_reprise(lambda: ws.get("A3:AH96", value_render_option="UNFORMATTED_VALUE"))
    try:                       # statut Nouveau : pas dans la base du plan (même filtre que charger_prive)
        exclus = C.exclus_nouveaux(C.lire_plages(prive, ["nv"], ["Nouveaux!A2:D200"])["nv"])
    except Exception as ex:
        if "400" not in str(ex):
            raise
        exclus = set()         # onglet « Nouveaux » absent
    base = []
    for r in v:
        r = list(r) + [""] * (34 - len(r))
        pseudo, poste = str(r[2]).strip(), str(r[1]).strip()
        d = C._num(r[31])
        if not pseudo or d is None or d != d or pseudo in exclus:
            continue
        g = N.groupe(poste)
        c33 = C._num(r[33]) if r[33] != "" else None
        cible = c33 if c33 is not None else (C._num(r[6]) or 0)
        n = lambda x: C._num(x) or 0
        base.append([pseudo, "Chaîne " + g, poste, cible, n(r[28]), C.arrondi_js(n(r[29]) / 48), C.arrondi_js(n(r[29])),
                     C.arrondi_js(n(r[30])), C.arrondi_js(d)])
        # colonnes de la prod : Joueur, Chaîne, Poste, TDC cible, TDC moyen d'hier, TDC compté (récolte/48), Récolte (× 48), Flux de colonisation, Delta
    return base


def part_colo(prive):
    for cel in ("V77", "AE77"):
        try:
            x = C._num(prive.worksheet("Chaîne unique").acell(cel, value_render_option="UNFORMATTED_VALUE").value)
            if x is not None and 0 < x <= 1:
                return x
        except Exception:
            pass
    return 0


def archiver(ws, entetes, lignes, jour):
    """Port d'archiverHistorique_ : remplace les lignes du jour, garde 14 jours."""
    limite = (datetime.strptime(jour, "%Y-%m-%d") - timedelta(days=JOURS_HISTORIQUE)).strftime("%Y-%m-%d")
    last = ws.row_count
    ex = C.avec_reprise(lambda: ws.get("A2:%s" % chr(64 + len(entetes)), value_render_option="UNFORMATTED_VALUE"))
    anciennes = [r + [""] * (len(entetes) - len(r)) for r in ex if r and str(r[0]) and str(r[0]) != jour and str(r[0]) >= limite]
    toutes = anciennes + [[jour] + l for l in lignes]
    C.avec_reprise(lambda: ws.batch_clear(["A2:%s%d" % (chr(64 + len(entetes)), max(last, 2))]))
    C.avec_reprise(lambda: ws.update(range_name="A1", values=[entetes] + toutes, value_input_option="RAW"))


def calculer(prive, jour):
    """Tout le calcul, sans écrire : retourne un dict avec plan, convois (lignes de l'onglet), dettes, base, couverture."""
    joueurs, postes, pos, interdits, dettes, membres, posv = C.charger_prive(prive)

    def dist(a, b):
        return math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]) if a in pos and b in pos else 999

    # MALUS_HOOK — décommenter pour activer les malus convois :
    # from ange_convois_malus import appliquer_malus, tick_malus
    # joueurs = appliquer_malus(joueurs)   # ajuste les deltas avant le plan
    # (tick_malus() est appelé après l'écriture, plus bas — chercher MALUS_TICK)

    lignes, dettes2, couv = P.calcul_plan(joueurs, dist, interdits, dettes)
    lignes = N.trier_plan(lignes, dist)
    gde = {p: N.groupe(po) for p, po in postes.items()}
    sortie = []
    for g in sorted(set(gde.values())):
        for l in lignes:
            if gde.get(l["s"]) == g:
                sortie.append(["Chaîne " + g, l["s"], l["r"], l["q"]])
            if gde.get(l["s"]) != gde.get(l["r"]) and gde.get(l["r"]) == g:
                sortie.append(["Chaîne " + g, l["s"], l["r"], l["q"]])
    hist_plan = [[l["s"], "Chaîne " + str(gde.get(l["s"])), l["r"], "Chaîne " + str(gde.get(l["r"])), l["q"], math.ceil(dist(l["s"], l["r"]))] for l in lignes]
    return {"lignes": lignes, "convois": sortie, "dettes": [[x["d"], x["c"], x["m"], x["ini"]] for x in dettes2 if x["m"] > 0],
            "couverture": couv, "hist_plan": hist_plan, "joueurs": joueurs}


DERNIER = None
COL_F0, COL_F1 = 25, 34          # colonnes Z..AH de « Chaîne unique » : formules de calcul (TDC moyen d'hier, compté, récolte, delta...)


def lignes_sans_formules(v):
    """Pure. v = valeurs de « Chaîne unique » à partir de la ligne 3. Retourne (ligne modèle, [lignes à compléter]) en numéros de ligne.
    Une ligne de joueur (poste et pseudo remplis) dont la colonne AC (TDC moyen d'hier) est vide n'a pas ses formules (ajoutée à la main en bas de la chaîne)."""
    modele, manque = None, []
    for i, r in enumerate(v):
        r = list(r) + [""] * (34 - len(r))
        if not str(r[2]).strip() or not str(r[1]).strip():
            continue
        if isinstance(r[28], (int, float)) and not isinstance(r[28], bool):
            if not manque:
                modele = i + 3            # dernière ligne complète avant le premier trou
        else:
            manque.append(i + 3)
    return modele, manque


def completer_formules(prive):
    """Recopie les formules de calcul (Z..AH) sur les joueurs ajoutés à la main sans leurs formules : sinon leur TDC compte pour 0 et ils n'ont aucun convoi.
    Retourne la liste des pseudos réparés."""
    ws = prive.worksheet("Chaîne unique")
    v = C.avec_reprise(lambda: ws.get("A3:AH202", value_render_option="UNFORMATTED_VALUE"))
    modele, manque = lignes_sans_formules(v)
    if not manque:
        return []
    if modele is None:
        raise RuntimeError("aucune ligne modèle avec formules")
    src = {"sheetId": ws.id, "startRowIndex": modele - 1, "endRowIndex": modele, "startColumnIndex": COL_F0, "endColumnIndex": COL_F1}
    req = [{"copyPaste": {"source": src, "destination": {"sheetId": ws.id, "startRowIndex": n - 1, "endRowIndex": n,
                                                          "startColumnIndex": COL_F0, "endColumnIndex": COL_F1},
                          "pasteType": "PASTE_NORMAL"}} for n in manque]
    C.avec_reprise(lambda: prive.batch_update({"requests": req}))
    return [str(v[n - 3][2]).strip() for n in manque]


REDISTRIB_JSON = os.path.join(os.path.dirname(__file__), "dettes_redistrib.json")

def _maj_dettes_redistrib(dettes_restantes, jour):
    """Met à jour dettes_redistrib.json après chaque clôture 22h : calcule le restant et le jour en cours."""
    try:
        if not os.path.exists(REDISTRIB_JSON):
            return
        suivi = json.load(open(REDISTRIB_JSON, encoding="utf-8"))
        if not suivi.get("actif"):
            return
        debiteurs_set = set(d.lower() for d in suivi.get("debiteurs", {}).keys())
        crediteurs_set = set(c.lower() for c in suivi.get("crediteurs", []))
        # Calculer le restant : somme des dettes impliquant nos débiteurs ET créanciers
        restant = sum(
            int(r[2]) for r in dettes_restantes
            if len(r) >= 3 and str(r[0]).lower() in debiteurs_set and str(r[1]).lower() in crediteurs_set
        )
        # Jour en cours (1 = premier jour, 7 = dernier)
        date_debut = datetime.strptime(suivi["date_debut"], "%Y-%m-%d").date()
        date_plan  = datetime.strptime(jour, "%Y-%m-%d").date()
        jour_num   = (date_plan - date_debut).days + 1
        # Historique
        historique = suivi.get("historique_jours", [])
        historique = [h for h in historique if h.get("jour") != jour]  # évite les doublons
        historique.append({"jour": jour, "jour_num": jour_num, "restant": restant})
        suivi["historique_jours"] = sorted(historique, key=lambda h: h["jour"])
        suivi["restant_actuel"] = restant
        suivi["jour_actuel"] = jour_num
        if restant == 0 or jour_num > suivi.get("nb_jours", 7):
            suivi["actif"] = False
        with open(REDISTRIB_JSON, "w", encoding="utf-8") as fout:
            json.dump(suivi, fout, indent=2, ensure_ascii=False)
    except Exception as ex:
        pass  # non bloquant


def ecrire_tout(jour=None, ecrire=False, prive=None, dettes_ecrites=True):
    """Répétition complète du calcul de minuit sur la copie privée TEST : TDC moyenne -> plan -> Dettes, Base convois, Historiques, Convois."""
    prive = prive or C.ouvrir_test("prive")
    jour = jour or aujourdhui_paris()
    veille = veille_de(jour)
    conn = sqlite3.connect("file:" + os.path.join(C.DOSSIER, "tdc_serveur.db") + "?mode=ro", uri=True)
    try:
        lm, nb = ecrire_moyenne(prive, conn, veille, ecrire)
    finally:
        conn.close()
    out = [("jour du plan", jour), ("jour moyenné", veille), ("joueurs moyenne", len(lm)), ("relevés du jour", nb)]
    if nb < MOYENNE_MIN_RELEVES:
        out.append(("ARRÊT", "moins de %d relevés : la prod retomberait sur le TDC du moment" % MOYENNE_MIN_RELEVES))
        return out
    reparees = []
    if ecrire:
        try:
            reparees = completer_formules(prive)      # joueur ajouté à la main sans formules : on les recopie AVANT le calcul
        except Exception as ex:
            out.append(("formules à compléter", "ERREUR (non bloquant) " + str(ex)[:100]))
        if reparees:
            out.append(("formules complétées", ", ".join(reparees)))
    if ecrire:
        import time
        time.sleep(8 + (12 if reparees else 0))       # laisse les formules de « Chaîne unique » (colonne AF) se recalculer
    global DERNIER
    r = calculer(prive, jour)
    DERNIER = r          # plan calculé AVANT l'écriture des dettes (les fichiers doivent utiliser CE plan)
    base = lire_base(prive)
    out += [("lignes du plan", len(r["lignes"])), ("lignes Convois", len(r["convois"])), ("dettes", len(r["dettes"])),
            ("couverture", round(r["couverture"], 4)), ("joueurs base", len(base)),
            ("total du plan", sum(l["q"] for l in r["lignes"]))]
    if not ecrire:
        out.append(("ÉCRIT", "non (simulation)"))
        return out
    # Dettes (cette nuit, Apps Script reste propriétaire des dettes : sinon elles seraient décomptées deux fois)
    if dettes_ecrites:
        wd = prive.worksheet("Dettes")
        C.avec_reprise(lambda: wd.batch_clear(["A2:D2000"]))
        if r["dettes"]:
            C.avec_reprise(lambda: wd.update(range_name="A2", values=r["dettes"], value_input_option="RAW"))
    # Base convois
    try:
        wb = prive.worksheet("Base convois")
    except Exception:
        wb = prive.add_worksheet("Base convois", 400, 12)
    C.avec_reprise(lambda: wb.clear())
    cov = r["couverture"]
    C.avec_reprise(lambda: wb.update(range_name="A1", values=[
        ["Plan calculé le", _paris().strftime("%d/%m/%Y %H:%M"), "Part de colonisation", part_colo(prive), "", "Couverture du jour", cov,
         "⚠ sous 95 % : vérifier les joueurs en surplus qui ne livrent pas" if cov < 0.95 else "OK", "", "Base de calcul", "moyenne"],
        ["", "", "", "", "", "", "", "", "", "Jour moyenné", veille],
        ["Joueur", "Chaîne", "Poste", "TDC cible", "TDC moyen d'hier", "TDC compté", "Récolte du jour (× 48)", "Flux de colonisation", "Delta de convoi"]] + base,
        value_input_option="RAW"))
    C.avec_reprise(lambda: wb.format("G1", {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}))
    C.avec_reprise(lambda: wb.format("A3:I3", {"textFormat": {"bold": True}}))
    # Historiques
    def feuille(nom):
        try:
            return prive.worksheet(nom)
        except Exception:
            ws = prive.add_worksheet(nom, 2000, 10)
            ws.freeze(rows=1)
            return ws
    wh = feuille("Historique base convois")
    C.avec_reprise(lambda: wh.format("A:A", {"numberFormat": {"type": "TEXT"}}))
    archiver(wh, ENT_BASE, [[b[0], b[1], b[2], b[3], b[4], b[6], b[7], b[8], "moyenne"] for b in base], jour)
    wp = feuille("Historique plan convois")
    C.avec_reprise(lambda: wp.format("A:A", {"numberFormat": {"type": "TEXT"}}))
    archiver(wp, ENT_PLAN, r["hist_plan"], jour)
    # Convois
    wc = prive.worksheet("Convois")
    C.avec_reprise(lambda: wc.batch_clear(["A4:D2000"]))
    if r["convois"]:
        C.avec_reprise(lambda: wc.update(range_name="A4", values=r["convois"], value_input_option="RAW"))
    # Suivi redistribution dettes grenier inactif
    if dettes_ecrites and r.get("dettes") is not None:
        _maj_dettes_redistrib([[d[0],d[1],d[2]] for d in r["dettes"]], jour)
    # MALUS_TICK — décommenter quand MALUS_HOOK est actif :
    # from ange_convois_malus import tick_malus, resume_malus
    # _reste, _expires = tick_malus()
    # if _expires:
    #     import ange_convois_prod as PR2
    #     PR2.alerter("🏁 Malus terminé : " + ", ".join(m["pseudo"] for m in _expires))
    out.append(("ÉCRIT", "oui, sur la copie privée TEST" if dettes_ecrites is not None and prive.id == C.TEST["prive"] else "oui"))
    return out


def comparer(jour_ref=None):
    """Compare la copie TEST avec la PRODUCTION (lecture seule) : TDC moyenne, Convois, Dettes, Base convois."""
    test = C.ouvrir_test("prive")
    prod = C.client().open_by_key(C.PRIVE_PROD)
    rend = "UNFORMATTED_VALUE"
    out = []

    def lire(sh, rng):
        return C.avec_reprise(lambda: sh.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s" % (sh.id, rng),
                                                         params={"valueRenderOption": rend}).json().get("values", []))

    def cle_conv(v):
        d = {}
        for r in v:
            if len(r) >= 4 and str(r[1]).strip():
                k = (str(r[0]), str(r[1]), str(r[2]))
                d[k] = d.get(k, 0) + int(round(C._num(r[3]) or 0))
        return d
    a, b = cle_conv(lire(test, "Convois!A4:D2000")), cle_conv(lire(prod, "Convois!A4:D2000"))
    out += [("Convois test/prod", "%d / %d lignes" % (len(a), len(b))), ("Convois identiques", sum(1 for k in a if b.get(k) == a[k])),
            ("Convois différentes", sum(1 for k in set(a) | set(b) if a.get(k) != b.get(k)))]
    for k in [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)][:6]:
        out.append(("écart " + k[1] + ">" + k[2], "test %s / prod %s" % (a.get(k), b.get(k))))
    ma = {str(r[0]): r[1:3] for r in lire(test, "'TDC moyenne'!A2:C1000") if r}
    mb = {str(r[0]): r[1:3] for r in lire(prod, "'TDC moyenne'!A2:C1000") if r}
    out.append(("TDC moyenne identique", "%d / %d" % (sum(1 for k in ma if mb.get(k) == ma[k]), len(mb))))
    ba = {str(r[0]): r[1:9] for r in lire(test, "'Base convois'!A4:I400") if r}
    bb = {str(r[0]): r[1:9] for r in lire(prod, "'Base convois'!A4:I400") if r}
    sans_cible = lambda v: None if v is None else v[:2] + v[3:]       # « TDC cible » évolue dans la journée (formule vivante) : comparée à part
    dif = [k for k in set(ba) | set(bb) if sans_cible(ba.get(k)) != sans_cible(bb.get(k))]
    out.append(("Base convois identique (hors cible)", "%d / %d" % (len(bb) - len([k for k in dif if k in bb]), len(bb))))
    out.append(("TDC cible identique", "%d / %d" % (sum(1 for k in bb if k in ba and ba[k][2:3] == bb[k][2:3]), len(bb))))
    for k in dif[:5]:
        out.append(("base écart " + k, "test %s / prod %s" % (ba.get(k), bb.get(k))))
    da = sorted(map(tuple, lire(test, "Dettes!A2:D2000")))
    db = sorted(map(tuple, lire(prod, "Dettes!A2:D2000")))
    out.append(("Dettes test/prod", "%d / %d" % (len(da), len(db))))
    return out


def AUTOTEST():
    assert veille_de("2026-11-12") == "2026-11-11" and veille_de("2026-03-01") == "2026-02-28"
    import sqlite3 as s
    c = s.connect(":memory:")
    c.execute("CREATE TABLE tdc(version TEXT, pseudo TEXT, alliance TEXT, tdc REAL)")
    for h in range(23, 24):
        c.execute("INSERT INTO tdc VALUES('202611102300','Bb','ANGE',100.5)")
        c.execute("INSERT INTO tdc VALUES('202611102300','Aa','ANGE',50)")
    c.execute("INSERT INTO tdc VALUES('202611110000','Aa','ANGE',51)")
    lm, nb = lignes_moyenne(c, "2026-11-11")
    assert [x[0] for x in lm] == ["Aa", "Bb"] and lm[0][1] == 51 and lm[1][1] == 101 and nb == 2, (lm, nb)
    # joueurs ajoutés à la main sans formules (colonne AC vide) : repérés, avec la dernière ligne complète comme modèle
    plein = lambda p: ["2.1", "Passeur 1", p] + [""] * 25 + [1000]
    vide = lambda p: ["2.1", "Passeur 1", p]
    v = [plein("A"), plein("B"), vide("C"), vide("D"), ["", "", ""], ["2.1", "", "Z"]]
    assert lignes_sans_formules(v) == (4, [5, 6]), lignes_sans_formules(v)       # lignes 5 et 6 (3 + indices 2 et 3)
    assert lignes_sans_formules([plein("A"), plein("B")]) == (4, [])
    assert lignes_sans_formules([vide("A")]) == (None, [3])
    return True
# v2 formules chaine unique completees automatiquement
# v3 statut Nouveau exclu de la base

"""ange_convois_entetes.py : morceau 2 de la migration de majChaines — en-tête de chaque onglet joueur
(A1, A2, TDC cible, objectif O, récolte, texte de calcul A5, ligne « Données mises à jour » A6).
Port fidèle de remplirOnglet_ / texteCalcul_ / etatColo_ ; seules les VALEURS sont écrites (la mise en forme existe déjà dans les onglets)."""
import json, re, time
from datetime import datetime
from zoneinfo import ZoneInfo
import ange_convois as C
import ange_convois_nuit as N
import ange_convois_pages as PG

FICHIERS = PG.FICHIERS
FAMILLE = {"3.1": "Grenier", "3.2": "Passeur", "3.3": "Chasseur"}
NOMS_FICHIER = {"3.1": "Greniers", "3.2": "Passeurs", "3.3": "Chasseurs"}
LIEN_FORMULAIRE = "https://docs.google.com/forms/d/e/1FAIpQLSf4N11g2OtzEmMIjfX5-XYWBNEhGiWCth8Hcxa8R07wRHigtQ/viewform"
T2 = "corriger mes données (formulaire)"


def fmt(n):
    return re.sub(r"\B(?=(\d{3})+(?!\d))", " ", str(int(round(n + 1e-9 if n >= 0 else n - 1e-9))))


def arrondi(x):
    """Math.round de JavaScript (arrondit .5 vers +∞)."""
    import math
    return int(math.floor(x + 0.5))


def fmt_js(n):
    return re.sub(r"\B(?=(\d{3})+(?!\d))", " ", str(arrondi(n)))


def lire_contexte_entetes(prive):
    plages = ["'Chaîne unique'!A3:AA202", "'Base convois'!A1:K400", "'Mode'!B5:B7"]
    lus = C.avec_reprise(lambda: C.lire_plages(prive, ["cu", "base", "mode"], plages))
    membres = []
    for r in lus["cu"]:
        r = list(r) + [""] * (27 - len(r))
        g0, poste, pseudo = str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()
        if not (isinstance(r[0], (int, float)) or re.match(r"^\d+\.\d+$", g0)) or not pseudo or poste not in N.POSTES:
            continue
        membres.append({"pseudo": pseudo, "poste": poste, "g": N.groupe(poste), "tdc": PG.nb(r[6]), "objO": PG.nb(r[7]),
                        "colonise": PG.nettoyer_colo(r[14]), "coloniseur": PG.nettoyer_colo(r[15]), "hors": r[24] is True,
                        "flux": PG.nb(r[26])})
    b = lus["base"] + [[]] * 3
    quand = b[0][1] if len(b[0]) > 1 else ""
    if isinstance(quand, (int, float)) and not isinstance(quand, bool):   # la cellule a été convertie en date : on reconstitue l'affichage jj/mm/aaaa hh:mm
        from datetime import timedelta
        quand = (datetime(1899, 12, 30) + timedelta(minutes=round(quand * 1440))).strftime("%d/%m/%Y %H:%M")
    base = {"quand": str(quand), "part": PG.nb(b[0][3]) if len(b[0]) > 3 else 0,
            "mode": "moyenne" if (len(b[0]) > 10 and str(b[0][10]) == "moyenne") else "live", "joueurs": {}}
    base["part"] = base["part"] if isinstance(base["part"], (int, float)) else 0
    for r in lus["base"][3:]:
        r = list(r) + [""] * (9 - len(r))
        if not r[0]:
            continue
        n = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
        base["joueurs"][str(r[0]).strip()] = {"cible": n(r[3]), "live": n(r[4]), "compte": n(r[5]), "prod": n(r[6]), "flux": n(r[7])}
    forms = [str((lus["mode"][i] or [""])[0]).strip() if i < len(lus["mode"]) and lus["mode"][i] else "" for i in range(3)]
    return {"membres": membres, "base": base, "forms": forms}


def etat_colo(j, col):
    if not col:
        return ""
    n = lambda s: str(s).strip().lower()
    if j["poste"] == "Chasseur" and j["colonise"]:
        chasseur, grenier = j["pseudo"], j["colonise"]
    elif j["poste"] != "Chasseur" and j["coloniseur"]:
        chasseur, grenier = j["coloniseur"], j["pseudo"]
    else:
        return ""
    if n(grenier) not in col:
        return ""
    return "  ✅ Colonisation en place." if col[n(grenier)] == n(chasseur) else "  ⏳ Colonisation pas encore détectée (vérifiée chaque heure)."


def texte_calcul(j, b, base, etat):
    if j.get("attaqueSeule"):
        return "🧮 Tu es dans la chaîne pour attaquer et pour être attaqué, mais tu n'es pas compté dans les convois : ton TDP est trop bas. Tu n'as aucun convoi à livrer ni à recevoir."
    moyenne = bool(base and base["mode"] == "moyenne")
    if base and base["quand"]:
        titre = "🧮 Comment ton convoi du jour est calculé" + (" (plan calculé le %s, avec la moyenne de ton TDC de la journée d'hier)" % base["quand"] if moyenne
                                                                 else " (plan calculé le %s, avec les TDC de ce moment-là)" % base["quand"])
    else:
        titre = "🧮 Comment ton convoi du jour est calculé"
    if not b or not b["live"] > 0:
        return titre + "\nTon TDC n'est pas encore connu : le calcul sera affiché dès qu'il le sera."
    L = [titre]
    L.append("1. TDC compté : " + fmt_js(b["compte"]) + (" (la moyenne de ton TDC sur la journée d'hier, relevé toutes les heures : une attaque ou une montée de TDC après 22h ne change pas ton convoi du jour)." if moyenne
                                                          else " (ton TDC au moment du calcul)."))
    L.append("2. Ce que tu récoltes aujourd'hui : %s × 48 = %s ressources." % (fmt_js(b["compte"]), fmt_js(b["prod"])))

    def taux_de(x):
        return arrondi(-x["flux"] / x["prod"] * 1000) / 10 if x and x["prod"] > 0 and x["flux"] < 0 else None
    taux = None
    if b["flux"] < 0:
        taux = taux_de(b)
    elif b["flux"] > 0 and j["colonise"] and base and j["colonise"] in base["joueurs"]:
        taux = taux_de(base["joueurs"][j["colonise"]])
    if taux is not None:
        t = ("%d" % taux if taux == int(taux) else repr(taux)).replace(".", ",")
        pc = t + " %"
    else:
        pc = "%d %%" % arrondi(base["part"] * 100) if base and base["part"] > 0 else ""
    if b["flux"] < 0 and j["coloniseur"]:
        L.append("3. Colonisation : %s te prend %s de ta récolte : −%s.%s" % (j["coloniseur"], pc, fmt_js(-b["flux"]), etat))
    elif b["flux"] > 0 and j["colonise"]:
        L.append("3. Colonisation : tu colonises %s, tu reçois %s de sa récolte : +%s.%s" % (j["colonise"], pc, fmt_js(b["flux"]), etat))
    else:
        L.append("3. Colonisation : aucune en place aujourd'hui, rien n'est ajouté ni retiré." + etat)
    return "\n".join(L)


def entete(j, base, col, live, forms, nom_fichier, seul):
    """Valeurs attendues de l'en-tête d'un onglet joueur : dict cellule -> valeur."""
    j = dict(j, attaqueSeule=j["pseudo"].lower() in seul and j["hors"])
    b = base["joueurs"].get(j["pseudo"])
    prod = arrondi(live[j["pseudo"]] * 48) if live.get(j["pseudo"], 0) > 0 else ""
    if b and b["live"] > 0:
        prod = b["prod"]
    if j["attaqueSeule"]:
        prod = ""
    etat = etat_colo(j, col)
    fam = FAMILLE[j["g"]]
    out = {"A1": "👤 " + j["pseudo"], "A2": nom_fichier + " · " + j["poste"],
           "D4": "–" if j["objO"] == "" else j["objO"],
           "E4": "💰 Récolte du jour (TDC compté × 48)",
           "F4": "–" if prod == "" else prod,
           "A5": texte_calcul(j, b, base, etat)}
    if fam == "Grenier":
        out["A4"] = "🎯 TDC cible"
        out["B4"] = "–" if j["tdc"] == "" else j["tdc"]
    else:
        out["A4"] = ""
        out["B4"] = ""
    i = ["Grenier", "Passeur", "Chasseur"].index(fam)
    out["lien"] = forms[i] if forms[i] else LIEN_FORMULAIRE
    return out


def horodatage():
    return datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m à %H:%M")


def requetes_entete(sheet_id, v):
    """updateCells (valeurs seules) pour A1:A2, A4:B4, D4:F4, A5, A6 d'un onglet joueur."""
    s = lambda x: {"userEnteredValue": {"stringValue": str(x)}}
    nombre = lambda x: {"userEnteredValue": {"numberValue": x}} if isinstance(x, (int, float)) else s(x)
    vide = {}
    r = []

    def zone(r0, c0, lignes, champs="userEnteredValue"):
        r.append({"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": r0, "columnIndex": c0}, "rows": [{"values": l} for l in lignes], "fields": champs}})
    zone(0, 0, [[s(v["A1"])], [s(v["A2"])]])
    zone(3, 0, [[s(v["A4"]) if v["A4"] else vide, nombre(v["B4"]) if v["B4"] != "" else vide, vide, nombre(v["D4"]), s(v["E4"]), nombre(v["F4"])]])
    zone(4, 0, [[s(v["A5"])]])
    t1 = "Données mises à jour le %s (chaque heure) · " % horodatage()
    zone(5, 0, [[{"userEnteredValue": {"stringValue": t1 + T2},
                  "textFormatRuns": [{"startIndex": 0, "format": {}}, {"startIndex": len(t1), "format": {"link": {"uri": v["lien"]}}}]}]],
         "userEnteredValue,textFormatRuns")
    return r


def calcul_entetes(prive, col):
    ctx = lire_contexte_entetes(prive)
    pg = PG.lire_contexte(prive)
    seul = {m["pseudo"].lower() for m in pg["membres"] if m["attaqueSeule"]}
    live = pg["live"]
    res = {}
    for m in ctx["membres"]:
        if m["hors"] and m["pseudo"].lower() not in seul:
            continue
        res[m["pseudo"]] = (m["g"], entete(m, ctx["base"], col, live, ctx["forms"], NOMS_FICHIER[m["g"]], seul))
    return res


def lire_existant(ss, titres):
    """Valeurs affichées de l'en-tête (A1:F6) des onglets joueurs : {titre: [[...]]}."""
    out = {}
    for i in range(0, len(titres), 40):
        lot = titres[i:i + 40]
        rng = ["'%s'!A1:F6" % t.replace("'", "''") for t in lot]
        j = C.avec_reprise(lambda: ss.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchGet" % ss.id,
                                                     params={"ranges": rng, "valueRenderOption": "UNFORMATTED_VALUE"}).json())
        for t, vr in zip(lot, j.get("valueRanges", [])):
            out[t] = vr.get("values", [])
    return out


def _cell(vals, r, c):
    try:
        v = vals[r][c]
    except IndexError:
        return ""
    return v


def comparer(prive, fichiers, col, filtrer=True):
    calc = calcul_entetes(prive, col)
    tot, ident, perimes, diffs = 0, 0, 0, []
    horo = {}
    titres = {}
    for cle, ss in fichiers.items():
        titres[cle] = {w.title for w in C.avec_reprise(lambda: ss.worksheets())}
        mine = [p for p, (g, _) in calc.items() if g == FICHIERS[cle] and p in titres[cle]]
        for t, h in PG.lire_horodatages(ss, mine).items():
            horo[(cle, t)] = h
    recent = max(horo.values()) if horo else ""
    for cle, ss in fichiers.items():
        mine = [p for p, (g, _) in calc.items() if g == FICHIERS[cle] and p in titres[cle]]
        exist = lire_existant(ss, mine)
        for p in mine:
            tot += 1
            if filtrer and horo.get((cle, p)) != recent:
                perimes += 1
                continue
            v = calc[p][1]
            e = exist.get(p, [])
            attendu = {"A1": v["A1"], "A2": v["A2"], "A4": v["A4"], "B4": v["B4"], "D4": v["D4"], "E4": v["E4"], "F4": v["F4"], "A5": v["A5"]}
            reel = {"A1": _cell(e, 0, 0), "A2": _cell(e, 1, 0), "A4": _cell(e, 3, 0), "B4": _cell(e, 3, 1), "D4": _cell(e, 3, 3),
                    "E4": _cell(e, 3, 4), "F4": _cell(e, 3, 5), "A5": _cell(e, 4, 0)}
            perim = horo.get((cle, p)) != recent
            bad = [k for k in attendu if not (perim and k == "D4") and str(attendu[k]).strip() != str(reel[k]).strip()
                   and not (isinstance(attendu[k], (int, float)) and isinstance(reel[k], (int, float)) and abs(attendu[k] - reel[k]) < 1e-6)]
            if bad:
                k = bad[0]
                diffs.append("%s/%s %s : VPS %r / AS %r" % (cle, p, k, str(attendu[k])[:90], str(reel[k])[:90]))
            else:
                ident += 1
    return {"onglets": tot, "dernier passage AS": recent, "onglets non rafraîchis par AS": perimes, "identiques": ident,
            "différents": tot - perimes - ident}, diffs


def ecrire(prive, fichiers, col, dossier):
    import os
    calc = calcul_entetes(prive, col)
    bilan, sauv = {}, {}
    for cle, ss in fichiers.items():
        ids = {w.title: w.id for w in C.avec_reprise(lambda: ss.worksheets())}
        mine = [p for p, (g, _) in calc.items() if g == FICHIERS[cle] and p in ids]
        sauv[cle] = lire_existant(ss, mine)
        reqs = []
        for p in mine:
            reqs += requetes_entete(ids[p], calc[p][1])
        os.makedirs(dossier, exist_ok=True)
        json.dump(sauv, open(os.path.join(dossier, "entetes_pre_%s.json" % time.strftime("%Y%m%d_%H%M")), "w"), default=str)
        if reqs:
            C.avec_reprise(lambda: ss.batch_update({"requests": reqs}), essais=3, pause=20)
        bilan[cle] = len(mine)
    return bilan


def AUTOTEST():
    assert arrondi(2.5) == 3 and arrondi(-0.5) == 0 and fmt_js(3880272) == "3 880 272"
    base = {"quand": "12/11/2026 00:07", "part": 0.3, "mode": "moyenne",
            "joueurs": {"A": {"cible": 1, "live": 100, "compte": 80839, "prod": 3880272, "flux": 0}}}
    j = {"pseudo": "A", "poste": "Passeur 5", "g": "3.2", "tdc": "", "objO": 141351, "colonise": "", "coloniseur": "", "hors": False, "flux": ""}
    v = entete(j, base, None, {"A": 100}, ["", "", ""], "Passeurs", set())
    assert v["A2"] == "Passeurs · Passeur 5" and v["A4"] == "" and v["F4"] == 3880272
    assert v["A5"].startswith("🧮 Comment ton convoi du jour est calculé (plan calculé le 12/11/2026 00:07, avec la moyenne")
    assert "3. Colonisation : aucune en place aujourd'hui, rien n'est ajouté ni retiré." in v["A5"]
    j2 = dict(j, poste="Grenier 1", g="3.1", tdc=500000, coloniseur="Z")
    base["joueurs"]["A"]["flux"] = -1000000
    v2 = entete(j2, base, {"a": "z"}, {"A": 100}, ["", "", ""], "Greniers", set())
    assert v2["B4"] == 500000 and "Z te prend 25,8 %" in v2["A5"] and "✅" in v2["A5"], v2["A5"]
    r = requetes_entete(7, v)
    assert len(r) == 4
    return True

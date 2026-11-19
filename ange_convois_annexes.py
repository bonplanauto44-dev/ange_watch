"""ange_convois_annexes.py : morceau 3 de la migration de majChaines — distances des convois, onglet « Suivi », page « Ma chaîne ».
Valeurs / formules / liens / couleurs de fond seulement (la mise en page de base existe déjà dans les fichiers)."""
import json, os, re, time
from datetime import datetime
from zoneinfo import ZoneInfo
import ange_convois as C
import ange_convois_nuit as N
import ange_convois_pages as PG
import ange_convois_entetes as EN

FICHIERS = PG.FICHIERS
C_PREM, C_MAX, R_PREM, R_MAX = 9, 12, 25, 12
TXT_PERSONNE = "Personne ne te livre."
COUL = {"Grenier 1": "#fde68a", "Grenier 2": "#fde9a0", "Grenier 3": "#fef3c7", "Grenier 4": "#fef6d0", "Grenier 5": "#fef9c3",
        "Passeur 1": "#bfdbfe", "Passeur 2": "#c9e0fd", "Passeur 3": "#d3e7fc", "Passeur 4": "#dbeafe", "Passeur 5": "#e0f2fe",
        "Passeur 6": "#e8f4fb", "Passeur 7": "#eef5f9", "Passeur 8": "#f1f5f9", "Chasseur": "#dcfce7"}


def rgb(h):
    h = h.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def lire_tabs(ss, titres, zone):
    """Valeurs brutes d'une zone (ex. 'A8:K20') pour plusieurs onglets : {titre: [[...]]}."""
    out = {}
    for i in range(0, len(titres), 40):
        lot = titres[i:i + 40]
        rng = ["'%s'!%s" % (t.replace("'", "''"), zone) for t in lot]
        j = C.avec_reprise(lambda: ss.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchGet" % ss.id,
                                                     params={"ranges": rng, "valueRenderOption": "UNFORMATTED_VALUE"}).json())
        for t, vr in zip(lot, j.get("valueRanges", [])):
            out[t] = vr.get("values", [])
    return out


def distance(pos, a, b):
    pa, pb = pos.get(a), pos.get(b)
    if not pa or not pb:
        return ""
    return __import__("math").ceil(__import__("math").hypot(pa[0] - pb[0], pa[1] - pb[1]))


def distances_attendues(pos, pseudo, noms_livr, noms_recu):
    """Colonne K (livraisons, 12 lignes) et colonne C (réceptions, 12 lignes) : distance ou ''."""
    f = lambda n: "" if (not str(n).strip() or str(n).strip() == TXT_PERSONNE) else distance(pos, pseudo, str(n).strip())
    return [f(n) for n in noms_livr], [f(n) for n in noms_recu]


def suivi_lignes(tabs_a):
    """Livreurs du Suivi : ceux dont l'onglet a des lignes de livraison, triés (localeCompare fr base)."""
    vus = [p for p, a in tabs_a.items() if any(str(r[0]).strip() != "" for r in a if r)]
    return sorted(vus, key=PG.cle_tri_pseudo if hasattr(PG, "cle_tri_pseudo") else (lambda s: PG.cle_tri(s)))


def reqs_suivi(sheet_suivi, ids, livreurs):
    n = len(livreurs)
    rows = []
    for i, p in enumerate(livreurs):
        r = 4 + i
        q = "'" + p.replace("'", "''") + "'!"
        rows.append({"values": [
            {"userEnteredValue": {"stringValue": p}, "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": "#gid=%s" % ids[p]}}}]},
            {"userEnteredValue": {"formulaValue": "=SUM(%sB%d:B%d)" % (q, C_PREM, C_PREM + C_MAX - 1)}},
            {"userEnteredValue": {"formulaValue": "=SUM(%sH%d:H%d)" % (q, C_PREM, C_PREM + C_MAX - 1)}},
            {"userEnteredValue": {"formulaValue": '=IF(B%d=0;"–";IF(C%d=0;"✅ Fait";"⏳ En cours"))' % (r, r)}}]})
    while len(rows) < 80:    # efface le reste d'une ancienne liste plus longue
        rows.append({"values": [{}, {}, {}, {}]})
    return [{"updateCells": {"start": {"sheetId": sheet_suivi, "rowIndex": 3, "columnIndex": 0}, "rows": rows,
                             "fields": "userEnteredValue,textFormatRuns"}}]


def lignes_ma_chaine(membres, ids, forms_ok=True):
    """Tableau de « Ma chaîne » (B10:F...) : poste, pseudo (lien interne), TDC cible, objectif O, colonisation."""
    l = sorted(membres, key=lambda m: (N.POSTES.index(m["poste"]), PG.cle_tri(m["pseudo"])))
    out = []
    for m in l:
        grenier = m["poste"].startswith("Grenier")
        tdc = ("–" if m["tdc"] == "" else m["tdc"]) if grenier else ""
        colo = ("Colonise " + m["colonise"] if m["colonise"] else "") if m["poste"] == "Chasseur" else ("Colonisé par " + m["coloniseur"] if m["coloniseur"] else "")
        out.append((m["poste"], m["pseudo"], tdc, "–" if m["objO"] == "" else m["objO"], colo))
    return out


def reqs_ma_chaine(sheet_id, ids, lignes, horodatage):
    """updateCells : B2 (date), puis le tableau B10:F(n) avec liens internes et fonds de couleur ; efface le reste (jusqu'à 80 lignes)."""
    s = lambda x: {"userEnteredValue": {"stringValue": str(x)}}
    nb = lambda x: {"userEnteredValue": {"numberValue": x}} if isinstance(x, (int, float)) else (s(x) if x != "" else {})
    rows = []
    for poste, pseudo, tdc, obj, colo in lignes:
        fond = {"backgroundColor": rgb(COUL.get(poste, "#ffffff"))}
        rows.append({"values": [
            dict(s(poste), userEnteredFormat=dict(fond, textFormat={"bold": True})),
            dict(s(pseudo), textFormatRuns=[{"startIndex": 0, "format": {"link": {"uri": "#gid=%s" % ids[pseudo]}}}], userEnteredFormat=fond),
            dict(nb(tdc), userEnteredFormat=dict(fond, horizontalAlignment="RIGHT", numberFormat={"type": "NUMBER", "pattern": "#,##0"})),
            dict(nb(obj), userEnteredFormat=dict(fond, horizontalAlignment="RIGHT", numberFormat={"type": "NUMBER", "pattern": "#,##0"})),
            dict(s(colo) if colo else {}, userEnteredFormat=dict(fond, textFormat={"foregroundColor": rgb("#4b5563")}))]})
    while len(rows) < 80:
        rows.append({"values": [{}, {}, {}, {}, {}]})
    return [
        {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": 1}, "rows": [{"values": [s("Mis à jour le %s · toutes les heures" % horodatage)]}],
                         "fields": "userEnteredValue"}},
        {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": 9, "columnIndex": 1}, "rows": rows,
                         "fields": "userEnteredValue,textFormatRuns,userEnteredFormat.backgroundColor,userEnteredFormat.horizontalAlignment,userEnteredFormat.numberFormat,userEnteredFormat.textFormat"}}]


def reqs_distances(sheet_id, dk, dc):
    cellule = lambda d: {"userEnteredValue": {"numberValue": d}} if d != "" else {}
    return [
        {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": C_PREM - 1, "columnIndex": 10}, "rows": [{"values": [cellule(d)]} for d in dk],
                         "fields": "userEnteredValue"}},
        {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": R_PREM - 1, "columnIndex": 2}, "rows": [{"values": [cellule(d)]} for d in dc],
                         "fields": "userEnteredValue"}}]


def preparer(prive, fichiers, col):
    """Calcule tout ce qu'il faut écrire : {cle: {"ids":…, "reqs":[…], "attendu":{…}}}."""
    ctx = EN.lire_contexte_entetes(prive)
    pg = PG.lire_contexte(prive)
    seul = {m["pseudo"].lower() for m in pg["membres"] if m["attaqueSeule"]}
    pos = pg["pos"]
    res = {}
    for cle, ss in fichiers.items():
        g = FICHIERS[cle]
        feuilles = {w.title: w.id for w in C.avec_reprise(lambda: ss.worksheets())}
        actifs = [m for m in ctx["membres"] if m["g"] == g and (not m["hors"] or m["pseudo"].lower() in seul) and m["pseudo"] in feuilles]
        noms = [m["pseudo"] for m in actifs]
        liv = lire_tabs(ss, noms, "A%d:A%d" % (C_PREM, C_PREM + C_MAX - 1))
        rec = lire_tabs(ss, noms, "A%d:A%d" % (R_PREM, R_PREM + R_MAX - 1))
        reqs, att_dist = [], {}
        for m in actifs:
            p = m["pseudo"]
            nl = [(r[0] if r else "") for r in liv[p]] + [""] * C_MAX
            nr = [(r[0] if r else "") for r in rec[p]] + [""] * R_MAX
            dk, dc = distances_attendues(pos, p, nl[:C_MAX], nr[:R_MAX])
            att_dist[p] = (dk, dc)
            reqs += reqs_distances(feuilles[p], dk, dc)
        livreurs = suivi_lignes(liv)
        ids = {p: feuilles[p] for p in noms}
        if "Suivi" in feuilles:
            reqs += reqs_suivi(feuilles["Suivi"], ids, livreurs)
        lignes = lignes_ma_chaine(actifs, ids)
        if "Ma chaîne" in feuilles:
            reqs += reqs_ma_chaine(feuilles["Ma chaîne"], ids, lignes, EN.horodatage())
        res[cle] = {"ids": ids, "reqs": reqs, "dist": att_dist, "livreurs": livreurs, "lignes": lignes, "feuilles": feuilles}
    return res


def ecrire(prive, fichiers, col, dossier):
    prep = preparer(prive, fichiers, col)
    bilan = {}
    for cle, ss in fichiers.items():
        p = prep[cle]
        os.makedirs(dossier, exist_ok=True)
        if p["reqs"]:
            C.avec_reprise(lambda: ss.batch_update({"requests": p["reqs"]}), essais=3, pause=20)
        bilan[cle] = "%d livreurs, %d lignes Ma chaîne" % (len(p["livreurs"]), len(p["lignes"]))
    return bilan


def comparer(prive, fichiers, col):
    """Compare Suivi (noms), Ma chaîne (tableau) et distances avec l'existant. Retourne (résumé, diffs)."""
    prep = preparer(prive, fichiers, col)
    diffs, ok = [], {"distances": 0, "Suivi": 0, "Ma chaîne": 0}
    tot = {"distances": 0, "Suivi": 0, "Ma chaîne": 0}
    for cle, ss in fichiers.items():
        p = prep[cle]
        noms = list(p["ids"])
        for pseudo, (dk, dc) in p["dist"].items():
            pass
        k = lire_tabs(ss, noms, "K%d:K%d" % (C_PREM, C_PREM + C_MAX - 1))
        c = lire_tabs(ss, noms, "C%d:C%d" % (R_PREM, R_PREM + R_MAX - 1))
        for pseudo in noms:
            tot["distances"] += 1
            ek = [(r[0] if r else "") for r in k[pseudo]] + [""] * C_MAX
            ec = [(r[0] if r else "") for r in c[pseudo]] + [""] * R_MAX
            dk, dc = p["dist"][pseudo]
            if [str(x) for x in dk] == [str(x) for x in ek[:C_MAX]] and [str(x) for x in dc] == [str(x) for x in ec[:R_MAX]]:
                ok["distances"] += 1
            else:
                diffs.append("%s/%s distances : VPS %s %s / AS %s %s" % (cle, pseudo, dk[:4], dc[:4], ek[:4], ec[:4]))
        tot["Suivi"] += 1
        sv = lire_tabs(ss, ["Suivi"], "A4:D83")["Suivi"]
        eff = [str(r[0]) for r in sv if r and str(r[0]).strip()]
        if eff == p["livreurs"]:
            ok["Suivi"] += 1
        else:
            diffs.append("%s/Suivi : VPS %s / AS %s" % (cle, p["livreurs"][:6], eff[:6]))
        tot["Ma chaîne"] += 1
        mc = lire_tabs(ss, ["Ma chaîne"], "B10:F89")["Ma chaîne"]
        eff = [tuple(str(x) for x in (list(r) + [""] * 5)[:5]) for r in mc if r and any(str(x).strip() for x in r)]
        att = [tuple(str(x) for x in (a, b, c2, d, e)) for a, b, c2, d, e in p["lignes"]]
        norm = lambda t: tuple(re.sub(r"\.0$", "", x) for x in t)
        if [norm(t) for t in eff] == [norm(t) for t in att]:
            ok["Ma chaîne"] += 1
        else:
            i = next((i for i, (x, y) in enumerate(zip(eff, att)) if norm(x) != norm(y)), min(len(eff), len(att)))
            diffs.append("%s/Ma chaîne ligne %d : VPS %s / AS %s (nb %d / %d)" % (cle, 10 + i, att[i] if i < len(att) else None, eff[i] if i < len(eff) else None, len(att), len(eff)))
    resume = {k: "%d / %d identiques" % (ok[k], tot[k]) for k in ok}
    return resume, diffs


def AUTOTEST():
    pos = {"A": (1, 2), "B": (4, 6)}
    assert distance(pos, "A", "B") == 5 and distance(pos, "A", "Z") == ""
    dk, dc = distances_attendues(pos, "A", ["B", "", "Z"], [TXT_PERSONNE])
    assert dk == [5, "", ""] and dc == [""]
    r = reqs_suivi(5, {"a": 1, "b": 2}, ["a", "b"])
    assert len(r[0]["updateCells"]["rows"]) == 80 and "SUM('a'!B9:B20)" in r[0]["updateCells"]["rows"][0]["values"][1]["userEnteredValue"]["formulaValue"]
    lig = lignes_ma_chaine([{"poste": "Grenier 1", "pseudo": "X", "tdc": 5, "objO": "", "colonise": "", "coloniseur": "Y"},
                            {"poste": "Chasseur", "pseudo": "Y", "tdc": "", "objO": 3, "colonise": "X", "coloniseur": ""}], {})
    assert lig[0] == ("Grenier 1", "X", 5, "–", "Colonisé par Y") and lig[1] == ("Chasseur", "Y", "", 3, "Colonise X")
    q = reqs_ma_chaine(9, {"X": 1, "Y": 2}, lig, "12/11 à 13:00")
    assert len(q) == 2 and len(q[1]["updateCells"]["rows"]) == 80
    return True

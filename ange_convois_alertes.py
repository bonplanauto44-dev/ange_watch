"""ange_convois_alertes.py : onglets « Alertes » et « Historique » du sheet privé (port de majAlertes_ et suivreHistorique_ d'Apps Script)."""
import json, os, re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import ange_convois as C

PARIS = ZoneInfo("Europe/Paris")
NOMS_FICHIER = {"3.1": "Greniers", "3.2": "Passeurs", "3.3": "Chasseurs"}
ETAT_PROD = os.path.join(C.DOSSIER, "convois_alertes_etat.json")
ETAT_TEST = os.path.join(C.DOSSIER, "convois_alertes_etat_test.json")


def norm(s):
    return str(s).strip().lower()


def _ms(txt, formats):
    for f in formats:
        try:
            return int(datetime.strptime(str(txt).strip(), f).replace(tzinfo=PARIS).timestamp() * 1000)
        except ValueError:
            pass
    return None


def lire_sources(prive):
    """Data (colonne L = dernière MAJ), Réponses au formulaire 2 (A = horodateur, E = pseudo), Historique (A:C), tels qu'affichés."""
    rngs = ["'Data'!A2:L400", "'Réponses au formulaire 2'!A2:E5000", "'Historique'!A2:C5000"]
    out = []
    for r in rngs:
        try:
            j = C.avec_reprise(lambda: prive.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchGet" % prive.id,
                                                            params={"ranges": [r], "valueRenderOption": "FORMATTED_VALUE"}).json())
            out.append(j["valueRanges"][0].get("values", []))
        except Exception:
            out.append([])
    return {"data": out[0], "reps": out[1], "histo": out[2]}


def derniere_maj(src):
    maj = {}
    for r in src["data"]:
        r = list(r) + [""] * (12 - len(r))
        if not str(r[0]).strip():
            continue
        t = _ms(r[11], ["%d/%m/%Y %H:%M"])
        if t:
            maj[norm(r[0])] = t
    for r in src["reps"]:
        r = list(r) + [""] * (5 - len(r))
        p = norm(r[4])
        t = _ms(r[0], ["%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"])
        if p and t and t > maj.get(p, 0):
            maj[p] = t
    return maj


def hors_depuis_histo(src, maintenant):
    """Date d'entrée hors chaîne retrouvée dans l'onglet Historique (dernier « Passé hors chaîne » sans « Retour » après) — amorçage seulement."""
    res = {}
    an = datetime.fromtimestamp(maintenant / 1000, PARIS).year
    for r in src["histo"]:
        r = list(r) + [""] * 3
        p, ev = str(r[1]).strip(), str(r[2]).strip()
        if not p:
            continue
        if ev == "Passé hors chaîne":
            t = _ms("%s %d" % (r[0], an), ["%d/%m %H:%M %Y"])
            if t and t > maintenant + 86400000:
                t = _ms("%s %d" % (r[0], an - 1), ["%d/%m %H:%M %Y"])
            if t:
                res[p] = t
        elif ev == "Retour dans la chaîne":
            res.pop(p, None)
    return res


def calculer(membres, live, col, src, hors_prec, maintenant):
    """Retourne (rouges, oranges, hors_nouveau) — lignes [type, joueur, chaîne, détail]."""
    maj = derniere_maj(src)
    rouges, oranges, connus, nouveau = [], [], set(), {}
    for m in membres:
        nomc = NOMS_FICHIER.get(m["g"], m["g"])
        connus.add(norm(m["pseudo"]))
        if not (live.get(m["pseudo"], 0) > 0):
            rouges.append(["🔴 TDC actuel inconnu", m["pseudo"], nomc, "Pseudo absent de l'API ou mal écrit : il n'a pas de liste d'attaque et n'apparaît dans celle de personne."])
        if m["hors"]:
            nouveau[m["pseudo"]] = hors_prec.get(m["pseudo"]) or maintenant
            jours = (maintenant - nouveau[m["pseudo"]]) // 86400000
            if jours >= 7 and not m["attaqueSeule"]:
                oranges.append(["🟠 Hors chaîne depuis longtemps", m["pseudo"], nomc, "%d jours hors chaîne : à revoir." % jours])
            continue
        d = maj.get(norm(m["pseudo"]))
        if not d:
            oranges.append(["🟠 Aucune donnée", m["pseudo"], nomc, "Aucune réponse au formulaire enregistrée (ouvrières / TDP)."])
        elif maintenant - d > 24 * 3600 * 1000:
            oranges.append(["🟠 Données de plus de 24 h", m["pseudo"], nomc, "Dernière mise à jour il y a %d h." % ((maintenant - d) // 3600000)])
    if col:
        hors_set = {norm(m["pseudo"]) for m in membres if m["hors"]}
        for m in membres:
            nomc = NOMS_FICHIER.get(m["g"], m["g"])
            if not m["colonise"] or m["hors"] or norm(m["colonise"]) in hors_set:
                continue
            cible = norm(m["colonise"])
            if cible not in col:
                rouges.append(["🔴 Grenier introuvable dans l'API", m["colonise"], nomc, "Pseudo à vérifier (colonisé par %s dans le tableau)." % m["pseudo"]])
                continue
            if col[cible] == norm(m["pseudo"]):
                continue
            if col[cible] == "":
                oranges.append(["🟠 Colonisation pas lancée", m["pseudo"], nomc, "%s doit coloniser %s : pas encore fait." % (m["pseudo"], m["colonise"])])
            else:
                rouges.append(["🔴 Colonisé par un autre", m["colonise"], nomc, "Le tableau prévoit %s, mais le jeu indique : %s." % (m["pseudo"], col[cible])])
    vus = set()
    for r in src["reps"][-300:]:
        r = list(r) + [""] * (5 - len(r))
        s = str(r[4]).strip()
        if not s or norm(s) in connus or norm(s) in vus:
            continue
        vus.add(norm(s))
        oranges.append(["🟠 Pseudo du formulaire introuvable", s, "", "Ne correspond à aucun joueur du tableau : faute de frappe ou joueur pas encore placé."])
    return rouges, oranges, nouveau


def _rgb(h):
    return {"red": int(h[1:3], 16) / 255, "green": int(h[3:5], 16) / 255, "blue": int(h[5:7], 16) / 255}


def _s(v, **fmt):
    c = {"userEnteredValue": {"stringValue": v}}
    f = {}
    t = {}
    for k in ("bold", "italic", "fontSize"):
        if k in fmt: t[k] = fmt[k]
    if "fg" in fmt: t["foregroundColor"] = _rgb(fmt["fg"])
    if t: f["textFormat"] = t
    if "bg" in fmt: f["backgroundColor"] = _rgb(fmt["bg"])
    if fmt.get("wrap"): f["wrapStrategy"] = "WRAP"
    if f: c["userEnteredFormat"] = f
    return c


def requetes_alertes(sheet_id, rouges, oranges, maintenant):
    quand = datetime.fromtimestamp(maintenant / 1000, PARIS).strftime("%d/%m à %H:%M")
    q = [{"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1000, "startColumnIndex": 0, "endColumnIndex": 4},
                         "cell": {}, "fields": "userEnteredValue,userEnteredFormat"}}]
    def ligne(r, rows):
        return {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": r, "columnIndex": 0}, "rows": [{"values": v} for v in rows],
                                "fields": "userEnteredValue,userEnteredFormat"}}
    q.append(ligne(0, [[_s("🚨 Alertes", fontSize=20, bold=True)], [_s("Mis à jour le " + quand, fg="#6b7280", italic=True)]]))
    tout = rouges + oranges
    if not tout:
        q.append(ligne(3, [[_s("✅ Rien à signaler.")]]))
        return q
    q.append(ligne(3, [[_s(t, bold=True, fg="#ffffff", bg="#1f2937") for t in ("Type", "Joueur", "Chaîne", "Détail")]]))
    rows = []
    for i, r in enumerate(tout):
        bg = "#fee2e2" if i < len(rouges) else "#ffedd5"
        rows.append([_s(r[0], bg=bg), _s(r[1], bg=bg), _s(r[2], bg=bg) if r[2] != "" else {"userEnteredFormat": {"backgroundColor": _rgb(bg)}}, _s(r[3], bg=bg, wrap=True)])
    q.append(ligne(4, rows))
    return q


# ------------------------------------------------------------------ Historique
def evenements(membres, avant, maintenant):
    """avant/maintenant : {pseudo: {g, poste, hors}} -> lignes [pseudo, événement, avant, après] (ordre d'Apps Script)."""
    nomc = lambda g: NOMS_FICHIER.get(g, g)
    lignes = []
    for pseudo, n in maintenant.items():
        a = avant.get(pseudo)
        if not a:
            lignes.append([pseudo, "Arrivée", "", "%s · %s" % (nomc(n["g"]), n["poste"])])
            continue
        if a["g"] != n["g"] or a["poste"] != n["poste"]:
            lignes.append([pseudo, "Changement de poste", "%s · %s" % (nomc(a["g"]), a["poste"]), "%s · %s" % (nomc(n["g"]), n["poste"])])
        if not a["hors"] and n["hors"]:
            lignes.append([pseudo, "Passé hors chaîne", "", ""])
        if a["hors"] and not n["hors"]:
            lignes.append([pseudo, "Retour dans la chaîne", "", ""])
    for pseudo, a in avant.items():
        if pseudo not in maintenant:
            lignes.append([pseudo, "Retiré de la chaîne", "%s · %s" % (nomc(a["g"]), a["poste"]), ""])
    return lignes


def instantane(membres):
    return {m["pseudo"]: {"g": m["g"], "poste": m["poste"], "hors": 1 if m["hors"] else 0} for m in membres}


def serie_paris(maintenant):
    n = datetime.fromtimestamp(maintenant / 1000, PARIS).replace(tzinfo=None)
    return (n - datetime(1899, 12, 30)).total_seconds() / 86400


def requetes_historique(sheet_id, premiere_ligne, lignes, maintenant):
    rows = []
    for pseudo, ev, av, ap in lignes:
        rows.append({"values": [{"userEnteredValue": {"numberValue": serie_paris(maintenant)}, "userEnteredFormat": {"numberFormat": {"type": "DATE_TIME", "pattern": "dd/MM HH:mm"}}},
                                {"userEnteredValue": {"stringValue": pseudo}}, {"userEnteredValue": {"stringValue": ev}},
                                {"userEnteredValue": {"stringValue": av}} if av != "" else {}, {"userEnteredValue": {"stringValue": ap}} if ap != "" else {}]})
    return [{"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": premiere_ligne - 1, "columnIndex": 0}, "rows": rows,
                             "fields": "userEnteredValue,userEnteredFormat.numberFormat"}}]


# ------------------------------------------------------------------ exécution
def ecrire(prive, ctx, col, dossier, test, maintenant=None):
    import time
    maintenant = maintenant or int(time.time() * 1000)
    fichier = ETAT_TEST if test else ETAT_PROD
    try:
        etat = json.load(open(fichier))
    except Exception:
        etat = {}
    membres = ctx["membres"]
    src = lire_sources(prive)
    hors_prec = etat.get("horsDepuis")
    if hors_prec is None:
        hors_prec = hors_depuis_histo(src, maintenant)
    rouges, oranges, hors_new = calculer(membres, ctx["live"], col, src, hors_prec, maintenant)
    titres = {w.title: w.id for w in C.avec_reprise(lambda: prive.worksheets())}
    reqs, bilan = [], {}
    if "Alertes" not in titres:
        raise RuntimeError("onglet Alertes introuvable")
    reqs += requetes_alertes(titres["Alertes"], rouges, oranges, maintenant)
    bilan["alertes"] = "%d rouge(s), %d orange(s)" % (len(rouges), len(oranges))
    snap = instantane(membres)
    avant = etat.get("snapshot")
    if avant is not None and "Historique" in titres:
        ev = evenements(membres, avant, snap)
        if ev:
            derniere = len(src["histo"]) + 1            # la ligne 1 est l'en-tête, src["histo"] part de la ligne 2
            reqs += requetes_historique(titres["Historique"], derniere + 1, ev, maintenant)
        bilan["historique"] = "%d événement(s)" % len(ev)
    else:
        bilan["historique"] = "instantané mémorisé"
    os.makedirs(dossier, exist_ok=True)
    C.avec_reprise(lambda: prive.batch_update({"requests": reqs}), essais=3, pause=20)
    etat["horsDepuis"], etat["snapshot"] = hors_new, snap
    json.dump(etat, open(fichier, "w"))
    return bilan


def comparer(prive, ctx, col):
    """Compare avec l'onglet Alertes existant (sans tenir compte de l'ordre ni des « il y a N h »)."""
    import time
    maintenant = int(time.time() * 1000)
    src = lire_sources(prive)
    try:
        etat = json.load(open(ETAT_PROD))
    except Exception:
        etat = {}
    hp = etat.get("horsDepuis") or hors_depuis_histo(src, maintenant)
    rouges, oranges, _ = calculer(ctx["membres"], ctx["live"], col, src, hp, maintenant)
    j = C.avec_reprise(lambda: prive.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchGet" % prive.id,
                                                    params={"ranges": ["'Alertes'!A5:D1000"], "valueRenderOption": "FORMATTED_VALUE"}).json())
    ex = [list(r) + [""] * (4 - len(r)) for r in j["valueRanges"][0].get("values", [])]
    gen = lambda l: sorted((r[0], r[1], r[2], re.sub(r"\d+ h\.|\d+ jours", "N", r[3])) for r in l)
    a, e = gen(rouges + oranges), gen(ex)
    sa, se = set(a), set(e)
    return {"VPS": len(a), "AS": len(e), "seulement VPS": [list(x) for x in sorted(sa - se)][:15], "seulement AS": [list(x) for x in sorted(se - sa)][:15]}


def AUTOTEST():
    ms = _ms("12/11/2026 10:00", ["%d/%m/%Y %H:%M"])
    now = ms + 30 * 3600 * 1000
    mem = [{"pseudo": "A", "g": "3.1", "poste": "Grenier 1", "hors": False, "attaqueSeule": False, "colonise": "", "coloniseur": ""},
           {"pseudo": "B", "g": "3.2", "poste": "Passeur 1", "hors": True, "attaqueSeule": False, "colonise": "", "coloniseur": ""},
           {"pseudo": "C", "g": "3.3", "poste": "Chasseur", "hors": False, "attaqueSeule": False, "colonise": "A", "coloniseur": ""}]
    src = {"data": [["A", "", "", "", "", "", "", "", "", "", "", "12/11/2026 10:00"]], "reps": [["12/11/2026 11:00:00", "", "", "", "Zoe"]], "histo": []}
    r, o, h = calculer(mem, {"A": 5, "C": 5}, {"a": "z"}, src, {"B": now - 8 * 86400000}, now)
    assert [x[0] for x in r] == ["🔴 TDC actuel inconnu", "🔴 Colonisé par un autre"], r
    types = [x[0] for x in o]
    assert "🟠 Hors chaîne depuis longtemps" in types and "🟠 Données de plus de 24 h" in types and "🟠 Aucune donnée" in types and "🟠 Pseudo du formulaire introuvable" in types, types
    assert [x for x in o if x[0].startswith("🟠 Données")][0][3] == "Dernière mise à jour il y a 30 h."
    ev = evenements(mem, {"A": {"g": "3.1", "poste": "Grenier 2", "hors": 0}, "B": {"g": "3.2", "poste": "Passeur 1", "hors": 0}, "Z": {"g": "3.1", "poste": "Grenier 1", "hors": 0}}, instantane(mem))
    assert ev == [["A", "Changement de poste", "Greniers · Grenier 2", "Greniers · Grenier 1"], ["B", "Passé hors chaîne", "", ""],
                  ["C", "Arrivée", "", "Chasseurs · Chasseur"], ["Z", "Retiré de la chaîne", "Greniers · Grenier 1", ""]], ev
    assert requetes_alertes(1, [], [], now)[-1]["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]["stringValue"].startswith("✅")
    assert len(requetes_alertes(1, r, o, now)) == 4
    assert hors_depuis_histo({"histo": [["01/11 10:00", "B", "Passé hors chaîne"], ["02/11 10:00", "B", "Retour dans la chaîne"], ["03/11 10:00", "D", "Passé hors chaîne"]]}, now).keys() == {"D"}
    return True

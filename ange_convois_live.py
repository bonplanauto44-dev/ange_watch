"""ange_convois_live.py : changements IMMÉDIATS de membres/postes dans les 3 fichiers (hors clôture de minuit).
Quand Arno met un joueur hors chaîne, le retire, ou change son poste :
  - onglet masqué (hors chaîne) / supprimé (plus dans ce fichier) / créé depuis « Modèle » (arrivée dans ce fichier)
  - les convois liés à un joueur qui n'est plus actif sont retirés des AUTRES onglets (équivalent de purgerConvois_)
Ne touche jamais aux onglets système, aux formules H/J, ni aux listes d'attaque (restent hebdomadaires/horaires côté Apps Script).
SÉCURITÉ : ecrire=False par défaut ; les fichiers de prod sont refusés par ange_convois.ouvrir_test."""
import json, os
import ange_convois as C
import ange_convois_nuit as N

DESC_JOUEUR = "Seules les cases jaunes sont modifiables"
SYSTEME = ["Ma chaîne", "Règles", "Suivi", "Journal", "Modèle"]


def metadonnees(ss):
    j = C.avec_reprise(lambda: ss.client.request(
        "get", "https://sheets.googleapis.com/v4/spreadsheets/%s" % ss.id,
        params={"fields": "sheets(properties(sheetId,title,hidden,index),protectedRanges(protectedRangeId,description))"}).json())
    out = {}
    for s in j.get("sheets", []):
        p = s["properties"]
        out[p["title"]] = {"id": p["sheetId"], "hidden": bool(p.get("hidden")), "index": p.get("index", 0),
                           "prot": [x.get("description", "") for x in s.get("protectedRanges", [])]}
    return out


def plan_actions(membres, g, onglets):
    """Pure : compare les membres attendus du fichier g avec les onglets présents. Retourne un dict d'actions."""
    attendus = {m["pseudo"]: m for m in membres if m["g"] == g}
    actifs = {p for p, m in attendus.items() if (not m["hors"]) or m["seul"]}
    a = {"creer": [], "montrer": [], "masquer": [], "supprimer": [], "proteger": [], "actifs": actifs}
    for p in sorted(actifs):
        o = onglets.get(p)
        if o is None:
            a["creer"].append(p)
        else:
            if o["hidden"]:
                a["montrer"].append(p)
            if DESC_JOUEUR not in o["prot"]:
                a["proteger"].append(p)
    for p, o in onglets.items():
        if p in SYSTEME:
            continue
        if p not in attendus:
            a["supprimer"].append(p)
        elif p not in actifs and not o["hidden"]:
            a["masquer"].append(p)
    return a


def requetes_structure(a, onglets, dernier_index):
    """Requêtes batchUpdate pour créer / montrer / masquer / supprimer / protéger. Les créations passent en premier."""
    reqs = []
    modele = onglets.get("Modèle")
    if a["creer"] and not modele:
        raise RuntimeError("onglet Modèle absent : impossible de créer les onglets joueurs")
    for i, p in enumerate(a["creer"]):
        reqs.append({"duplicateSheet": {"sourceSheetId": modele["id"], "newSheetName": p, "insertSheetIndex": dernier_index + i}})
    for p in a["montrer"]:
        reqs.append({"updateSheetProperties": {"properties": {"sheetId": onglets[p]["id"], "hidden": False}, "fields": "hidden"}})
    for p in a["masquer"]:
        reqs.append({"updateSheetProperties": {"properties": {"sheetId": onglets[p]["id"], "hidden": True}, "fields": "hidden"}})
    for p in a["supprimer"]:
        reqs.append({"deleteSheet": {"sheetId": onglets[p]["id"]}})
    return reqs


def requete_protection(sheet_id):
    """Onglet joueur : protégé sauf C9:G20 (cases jaunes). Sans liste d'éditeurs : seuls le propriétaire et le compte du script écrivent."""
    return {"addProtectedRange": {"protectedRange": {
        "range": {"sheetId": sheet_id}, "description": DESC_JOUEUR,
        "unprotectedRanges": [{"sheetId": sheet_id, "startRowIndex": N.C_PREM - 1, "endRowIndex": N.C_PREM - 1 + N.C_MAX,
                               "startColumnIndex": 2, "endColumnIndex": 7}],
        "warningOnly": False}}}


def purge_onglet(p, ligne_a, ligne_r, titre, actifs):
    """Pure. ligne_a : valeurs A9:K20 ; ligne_r : A25:C36. Retourne e (format requetes_onglet) ou None si rien à retirer.
    Les noms qui ne sont plus actifs disparaissent ; les autres lignes (envois, cases, retards) sont conservées."""
    liv, dist = [], {}
    retire = False
    for r in ligne_a:
        r = list(r) + [""] * (11 - len(r))
        nom = str(r[0]).strip()
        if not nom:
            continue
        if nom not in actifs:
            retire = True
            continue
        dist[nom] = r[10]
        liv.append({"r": nom, "du": N._n0(r[1]), "retard": N._n0(r[8]),
                    "env": {"c": r[2] is True, "e": [r[3], r[4], r[5], r[6]]}})
    rec = []
    for r in ligne_r:
        r = list(r) + [""] * (3 - len(r))
        nom = str(r[0]).strip()
        if not nom or nom == N.TXT_PERSONNE:
            continue
        if nom not in actifs:
            retire = True
            continue
        dist[nom] = r[2]
        rec.append({"s": nom, "q": N._n0(r[1])})
    if not retire:
        return None, None
    return {"lignes": liv, "recus": rec, "trop": False}, dist


PARKING_TEST = os.path.join(C.DOSSIER, "convois_parking.json")
PARKING_PROD = os.path.join(C.DOSSIER, "convois_parking_prod.json")
CIBLE = "test"        # « prod » seulement pendant un appel explicite de la veille de production (ange_convois_tick)


def _parking_chemin():
    return PARKING_PROD if CIBLE == "prod" else PARKING_TEST


def _ouvrir(cle):
    if CIBLE == "prod":
        import ange_convois_prod as PR
        return PR.ouvrir_prod(cle, PR.jour_du_plan())      # refuse si la production n'est pas armée
    return C.ouvrir_test(cle)


def _lire_parking():
    try:
        return json.load(open(_parking_chemin(), encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _paris():
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris"))
    except Exception:
        return datetime.now()


def lignes_vers_e(ligne_a, ligne_r, actifs, parking=None, tab=None, jour=None):
    """Pure. Construit l'état d'un onglet : on garde les lignes dont l'autre joueur est actif, on met au « parking » celles dont il
    ne l'est plus (rien n'est perdu), et on restaure depuis le parking celles dont l'autre joueur est revenu (même jour).
    Retourne (e, dist, parking_nouveau, changé)."""
    parking = [dict(x) for x in (parking or [])]
    liv, rec, dist, change = [], [], {}, False
    for r in ligne_a:
        r = list(r) + [""] * (11 - len(r))
        nom = str(r[0]).strip()
        if not nom:
            continue
        ligne = {"r": nom, "du": N._n0(r[1]), "retard": N._n0(r[8]), "env": {"c": r[2] is True, "e": [r[3], r[4], r[5], r[6]]}}
        if nom in actifs:
            liv.append(ligne)
            dist[nom] = r[10]
        else:
            change = True
            if tab is not None:
                parking.append({"jour": jour, "tab": tab, "type": "liv", "autre": nom, "ligne": ligne})
    for r in ligne_r:
        r = list(r) + [""] * (3 - len(r))
        nom = str(r[0]).strip()
        if not nom or nom == N.TXT_PERSONNE:
            continue
        if nom in actifs:
            rec.append({"s": nom, "q": N._n0(r[1])})
            dist[nom] = r[2]
        else:
            change = True
            if tab is not None:
                parking.append({"jour": jour, "tab": tab, "type": "rec", "autre": nom, "ligne": {"s": nom, "q": N._n0(r[1])}})
    reste = []
    for x in parking:
        if tab is not None and x["tab"] == tab and x["jour"] == jour and x["autre"] in actifs:
            if x["type"] == "liv" and len(liv) < N.C_MAX and x["autre"] not in [l["r"] for l in liv]:
                liv.append(x["ligne"]); change = True; continue
            if x["type"] == "rec" and len(rec) < N.R_MAX and x["autre"] not in [c["s"] for c in rec]:
                rec.append(x["ligne"]); change = True; continue
        reste.append(x)
    return {"lignes": liv, "recus": rec, "trop": False}, dist, reste, change


def synchroniser(cle, ecrire=False, prive=None, phase="tout"):
    """Synchronise le fichier TEST `cle` avec la feuille privée TEST. phase : « creer » (tout sauf suppressions), « supprimer » ou « tout ».
    Un joueur qui change de FICHIER garde ses livraisons : elles sont copiées dans son nouvel onglet avant la suppression de l'ancien."""
    prive = prive or _ouvrir("prive")
    ss = _ouvrir(cle)
    g = {"greniers": "3.1", "passeurs": "3.2", "chasseurs": "3.3"}[cle]
    _, _, _, _, _, membres, posv = C.charger_prive(prive)
    onglets = metadonnees(ss)
    a = plan_actions(membres, g, onglets)
    if CIBLE == "prod" and (len(membres) < 70 or len(a["supprimer"]) > 3 or len(a["creer"]) > 6):
        raise RuntimeError("garde-fou prod : %d membres, %d suppressions, %d créations demandées : rien fait" % (len(membres), len(a["supprimer"]), len(a["creer"])))
    tous_actifs = {m["pseudo"] for m in membres if (not m["hors"]) or m["seul"]}   # chaîne unique : un convoi peut venir d'un autre fichier
    out = [("fichier", ss.title)]
    for k in ("creer", "montrer", "masquer", "supprimer", "proteger"):
        out.append((k, ", ".join(a[k]) or "-"))
    jour = _paris().strftime("%Y-%m-%d")
    parking = _lire_parking()

    def dist_g(p, d):
        def f(a_, b_):
            if a_ in posv and b_ in posv:
                return __import__("math").ceil(__import__("math").hypot(posv[a_][0] - posv[b_][0], posv[a_][1] - posv[b_][1]))
            return d.get(b_, "")
        return f

    reste = [p for p in a["actifs"] if p in onglets and p not in a["supprimer"]]
    reqs_purge, nb_purge = [], 0
    if reste and phase != "supprimer":
        rngs = []
        for p in reste:
            q = "'%s'!" % p.replace("'", "''")
            rngs += [q + "A7", q + "A9:K20", q + "A25:C36"]
        lus = C.avec_reprise(lambda: C.lire_plages(ss, rngs, rngs))
        for p in reste:
            q = "'%s'!" % p.replace("'", "''")
            titre = (lus[q + "A7"][0][0] if lus[q + "A7"] and lus[q + "A7"][0] else N.TITRE_JOUR)
            e, dist, parking, change = lignes_vers_e(lus[q + "A9:K20"], lus[q + "A25:C36"], tous_actifs, parking, p, jour)
            if not change:
                continue
            nb_purge += 1
            reqs_purge += N.requetes_onglet(onglets[p]["id"], p, e, titre, dist_g(p, dist))
    out.append(("onglets à purger", nb_purge))
    # joueurs qui arrivent dans ce fichier depuis un autre : on récupère leurs livraisons dans l'ancien onglet
    reprises = {}
    if a["creer"] and phase != "supprimer":
        for autre in ("greniers", "passeurs", "chasseurs"):
            if autre == cle:
                continue
            ss2 = _ouvrir(autre)
            on2 = metadonnees(ss2)
            vus = [p for p in a["creer"] if p in on2 and p not in reprises]
            if not vus:
                continue
            rg = []
            for p in vus:
                q = "'%s'!" % p.replace("'", "''")
                rg += [q + "A9:K20", q + "A25:C36"]
            lu2 = C.avec_reprise(lambda: C.lire_plages(ss2, rg, rg))
            for p in vus:
                q = "'%s'!" % p.replace("'", "''")
                e, dist, _, _ = lignes_vers_e(lu2[q + "A9:K20"], lu2[q + "A25:C36"], tous_actifs)
                reprises[p] = (e, dist)
    out.append(("livraisons reprises", ", ".join(reprises) or "-"))
    dernier = onglets["Journal"]["index"] if "Journal" in onglets else max([o["index"] for o in onglets.values()] + [0])
    a2 = dict(a)
    if phase == "creer":
        a2["supprimer"] = []
    if phase == "supprimer":
        a2 = {k: ([] if k in ("creer", "montrer", "masquer", "proteger") else v) for k, v in a.items()}
        a2["actifs"] = a["actifs"]
    reqs_struct = requetes_structure(a2, onglets, dernier)
    out.append(("requêtes structure", len(reqs_struct)))
    out.append(("requêtes purge", len(reqs_purge)))
    if not ecrire:
        out.append(("ÉCRIT", "non (simulation)"))
        return out
    for i in range(0, len(reqs_purge), 150):
        C.avec_reprise(lambda i=i: ss.batch_update({"requests": reqs_purge[i:i + 150]}))
    if reqs_struct:
        C.avec_reprise(lambda: ss.batch_update({"requests": reqs_struct}))
    nouveaux = a2["creer"] + a2["proteger"]
    if nouveaux:
        meta = metadonnees(ss)
        reqs = []
        for p in a2["creer"]:
            if p in meta:
                reqs.append({"updateSheetProperties": {"properties": {"sheetId": meta[p]["id"], "hidden": False}, "fields": "hidden"}})
        for p in nouveaux:
            if p in meta and DESC_JOUEUR not in meta[p]["prot"]:
                reqs.append(requete_protection(meta[p]["id"]))
        for p in a2["creer"]:
            if p in meta and p in reprises:
                e, dist = reprises[p]
                reqs += N.requetes_onglet(meta[p]["id"], p, e, N.TITRE_JOUR, dist_g(p, dist))
        if reqs:
            C.avec_reprise(lambda: ss.batch_update({"requests": reqs}))
    try:
        json.dump(parking, open(_parking_chemin(), "w", encoding="utf-8"))
    except OSError:
        pass
    out.append(("ÉCRIT", "oui, en PRODUCTION" if CIBLE == "prod" else "oui, sur la copie TEST"))
    return out


def synchroniser_tout(prive=None, ecrire=True):
    """Orchestration : d'abord créer/montrer/masquer/purger dans les 3 fichiers (en reprenant les livraisons des joueurs qui changent de
    fichier), puis seulement supprimer les anciens onglets."""
    prive = prive or _ouvrir("prive")
    res = {}
    for ph in ("creer", "supprimer"):
        for cle in ("greniers", "passeurs", "chasseurs"):
            res[(ph, cle)] = synchroniser(cle, ecrire, prive, ph)
    return res


def AUTOTEST():
    mem = [{"pseudo": "A", "poste": "Grenier 1", "g": "3.1", "hors": False, "seul": False},
           {"pseudo": "B", "poste": "Grenier 2", "g": "3.1", "hors": True, "seul": False},
           {"pseudo": "C", "poste": "Passeur 1", "g": "3.2", "hors": False, "seul": False},
           {"pseudo": "D", "poste": "Grenier 3", "g": "3.1", "hors": False, "seul": False}]
    ong = {"A": {"id": 1, "hidden": False, "index": 3, "prot": [DESC_JOUEUR]},
           "B": {"id": 2, "hidden": False, "index": 4, "prot": [DESC_JOUEUR]},
           "C": {"id": 3, "hidden": False, "index": 5, "prot": [DESC_JOUEUR]},
           "Modèle": {"id": 9, "hidden": True, "index": 8, "prot": []}}
    a = plan_actions(mem, "3.1", ong)
    assert a["creer"] == ["D"] and a["masquer"] == ["B"] and a["supprimer"] == ["C"] and not a["montrer"], a
    r = requetes_structure(a, ong, 8)
    assert r[0]["duplicateSheet"]["newSheetName"] == "D" and any("deleteSheet" in x for x in r)
    e, d = purge_onglet("A", [["X", 10, True, 1, "", "", "", "", 5, "", 7], ["Y", 20, False, "", "", "", "", "", 0, "", 9]],
                        [["Z", 4, 3], ["Personne ne te livre."]], N.TITRE_JOUR, {"Y"})
    assert [l["r"] for l in e["lignes"]] == ["Y"] and e["recus"] == [] and d == {"Y": 9}
    assert purge_onglet("A", [["Y", 1]], [], N.TITRE_JOUR, {"Y"}) == (None, None)
    # parking : un joueur sort puis revient le même jour -> ses lignes reviennent
    ra = [["X", 10, True, 1, "", "", "", "", 5, "", 7], ["Y", 20, False, "", "", "", "", "", 0, "", 9]]
    e, d, pk, ch = lignes_vers_e(ra, [["Z", 4, 3]], {"Y"}, [], "A", "2026-11-12")
    assert ch and [l["r"] for l in e["lignes"]] == ["Y"] and e["recus"] == [] and len(pk) == 2
    ra2 = [["Y", 20, False, "", "", "", "", "", 0, "", 9]]
    e2, d2, pk2, ch2 = lignes_vers_e(ra2, [], {"Y", "X", "Z"}, pk, "A", "2026-11-12")
    assert ch2 and sorted(l["r"] for l in e2["lignes"]) == ["X", "Y"] and e2["recus"] == [{"s": "Z", "q": 4}] and pk2 == []
    x = [l for l in e2["lignes"] if l["r"] == "X"][0]
    assert x["env"]["c"] is True and x["env"]["e"][0] == 1 and x["retard"] == 5
    e3, _, pk3, ch3 = lignes_vers_e(ra2, [], {"Y", "X", "Z"}, pk, "A", "2026-11-13")
    assert [l["r"] for l in e3["lignes"]] == ["Y"] and len(pk3) == 2
    e4, _, _, ch4 = lignes_vers_e(ra2, [], {"Y"}, [], "A", "2026-11-12")
    assert not ch4
    return True

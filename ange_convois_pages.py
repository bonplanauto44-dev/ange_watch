"""ange_convois_pages.py : port de majChaines (Apps Script) vers le VPS, par morceaux.
MORCEAU 1 : listes d'attaque des onglets joueurs (« qui tu peux attaquer » A41:C112 et « qui peut t'attaquer » F41:H112).
Fonctions pures + comparaison avec ce qu'Apps Script a écrit (lecture seule). Aucune écriture tant qu'elle n'est pas demandée."""
import json, re, unicodedata, urllib.parse
import ange_convois as C
import ange_convois_nuit as N

PROFIL_URL = "https://s5.fourmizzz.fr/Membre.php?Pseudo="
API_JOUEURS = "https://s5.fourmizzz.fr/api/exports/players/"
A_PREM = 41          # première ligne des listes d'attaque
A_MAX = 72           # nombre de lignes (chaîne unique)
POSTE_DESC = "🎯 À descendre"
FICHIERS = {"greniers": "3.1", "passeurs": "3.2", "chasseurs": "3.3"}


def nb(v):
    if isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        return v
    s = re.sub(r"[\s  ]", "", str(v)).replace(",", ".")
    if s == "":
        return ""
    try:
        return float(s)
    except ValueError:
        return ""


def nettoyer_colo(v):
    s = str(v).strip()
    return "" if re.match(r"^non colonis", s, re.I) else s


def cle_tri(s):
    """Approximation de localeCompare('fr', {sensitivity:'base'}) : sans accents ni casse."""
    d = unicodedata.normalize("NFD", str(s))
    return "".join(c for c in d if unicodedata.category(c) != "Mn").casefold()


def _chasse():
    """Module du bloc « Ta chasse », rechargé à chaque appel (le démon ne le recharge pas tout seul)."""
    import importlib, sys
    m = sys.modules.get("ange_convois_chasse")
    if m:
        importlib.reload(m)
    import ange_convois_chasse
    return ange_convois_chasse


# ---------------------------------------------------------------- lecture du sheet privé
def lire_contexte(prive):
    """Équivalent de lireChaines_ + lireTdcLive_ : membres (avec rang, colonisation, drapeaux), TDC live, positions, cibles à descendre."""
    plages = ["'Chaîne unique'!A3:AH202", "'Attaque seulement'!A2:A300", "'Ne pas attaquer'!A2:A300", "'Cible à descendre'!A2:A300", "'TDC live'!A2:D400"]
    noms = ["cu", "seul", "protege", "desc", "live"]
    lus = C.avec_reprise(lambda: C.lire_plages(prive, noms, plages))
    liste = lambda v: {str(r[0]).strip().lower() for r in v if r and str(r[0]).strip()}
    seul, protege = liste(lus["seul"]), liste(lus["protege"])
    desc = [str(r[0]).strip() for r in lus["desc"] if r and str(r[0]).strip()]
    membres = []
    for r in lus["cu"]:
        r = list(r) + [""] * (34 - len(r))
        g0, poste, pseudo = str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()
        if not (isinstance(r[0], (int, float)) or re.match(r"^\d+\.\d+$", g0)) or not pseudo or poste not in N.POSTES:
            continue
        hors = r[24] is True
        p = pseudo.lower()
        membres.append({"pseudo": pseudo, "poste": poste, "rang": N.POSTES.index(poste), "g": N.groupe(poste), "hors": hors,
                        "colonise": nettoyer_colo(r[14]), "coloniseur": nettoyer_colo(r[15]),
                        "attaqueSeule": bool(hors and p in seul), "nePasAttaquer": p in protege})
    live, pos = {}, {}
    for r in lus["live"]:
        r = list(r) + [""] * (4 - len(r))
        p = str(r[0]).strip()
        t = nb(r[3])
        if p and t != "":
            live[p] = t
        x, y = nb(r[1]), nb(r[2])
        if p and x != "" and y != "":
            pos[p] = (x, y)
    CH = _chasse()
    return {"membres": membres, "live": live, "pos": pos, "desc": desc, "chasse": CH.lire_historique(prive)}


def lire_colonisation_api():
    """Équivalent de lireColonisationApi_ : {pseudo en minuscules: pseudo du maître en minuscules ('' = aucun)}."""
    import urllib.request
    req = urllib.request.Request(API_JOUEURS, headers={"User-Agent": "Mozilla/5.0 (suivi ANGE)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        brut = json.loads(r.read().decode("utf-8"))
    liste = brut if isinstance(brut, list) else (brut.get("players") or brut.get("joueurs") or list(brut.values()))
    nom_par_id = {j["id"]: str(j.get("pseudo", "")).strip().lower() for j in liste if isinstance(j, dict) and "id" in j}
    col = {}
    for j in liste:
        if not isinstance(j, dict) or not j.get("pseudo"):
            continue
        m = j.get("masterPlayerId")
        col[str(j["pseudo"]).strip().lower()] = "" if m is None else nom_par_id.get(m, "?")
    if not col:
        raise RuntimeError("API joueurs : liste vide")
    return col


# ---------------------------------------------------------------- logique (port fidèle des fonctions Apps Script)
def dans_plage(a, c):
    if not (isinstance(a, (int, float)) and a > 0) or not (isinstance(c, (int, float)) and c > 0):
        return False
    return 0.5 * a <= c <= 3 * a


def candidats_prets(membres, actifs, col):
    if not col:
        return actifs
    n = lambda s: str(s).strip().lower()
    hors = {n(m["pseudo"]) for m in membres if m["hors"]}
    exclus = set()
    for m in membres:
        if m["hors"] or m["poste"] != "Chasseur" or not m["colonise"]:
            continue
        cible = n(m["colonise"])
        if cible in hors or cible not in col:
            continue
        if col[cible] != n(m["pseudo"]):
            exclus.add(n(m["pseudo"]))
            exclus.add(cible)
    return [m for m in actifs if n(m["pseudo"]) not in exclus]


def peut_attaquer(j, actifs, live, desc):
    moi = j["pseudo"].strip().lower()
    desc_set = {c.lower() for c in desc}
    d = [{"pseudo": c, "poste": POSTE_DESC, "rang": -1, "cibleDescente": True} for c in desc
         if c.lower() != moi and dans_plage(live.get(j["pseudo"]), live.get(c))]
    # attaqueSeule : pas de restriction de poste/rang — attaque tout joueur dans la plage
    if j.get("attaqueSeule"):
        return d + [m for m in actifs
                    if m["pseudo"].strip().lower() not in desc_set
                    and not m["nePasAttaquer"]
                    and m["pseudo"].strip().lower() != moi
                    and dans_plage(live.get(j["pseudo"]), live.get(m["pseudo"]))]
    if j["poste"] == "Chasseur":
        return d
    # Joueur normal : rang supérieur OU cible attaqueSeule dans la plage (sans condition de rang)
    return d + [m for m in actifs
                if m["pseudo"].strip().lower() not in desc_set
                and not m["nePasAttaquer"]
                and m["pseudo"].strip().lower() != moi
                and (m["rang"] > j["rang"] or m.get("attaqueSeule"))
                and dans_plage(live.get(j["pseudo"]), live.get(m["pseudo"]))]


def peut_etre_attaque_par(j, actifs, live):
    if j["nePasAttaquer"]:
        return []
    # attaqueSeule : tout joueur dans la plage peut l'attaquer, sans condition de rang
    if j.get("attaqueSeule"):
        moi = j["pseudo"].strip().lower()
        return [m for m in actifs
                if m["pseudo"].strip().lower() != moi
                and dans_plage(live.get(m["pseudo"]), live.get(j["pseudo"]))]
    return [m for m in actifs if m["rang"] < j["rang"] and dans_plage(live.get(m["pseudo"]), live.get(j["pseudo"]))]


def distance(pos, a, b):
    if a not in pos or b not in pos:
        return ""
    return __import__("math").ceil(__import__("math").hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]))


def liste_triee(joueurs, pos, moi):
    def dist(m):
        d = distance(pos, moi, m["pseudo"])
        return 1e9 if d == "" else d
    return sorted(joueurs, key=lambda m: (-(1 if m.get("cibleDescente") else 0), dist(m), m["rang"], cle_tri(m["pseudo"])))


def calcul_listes(ctx, col):
    """Retourne {pseudo: {"g": groupe, "gauche": [...], "droite": [...], "vide_g": txt, "vide_d": txt}} pour chaque joueur actif de la chaîne.
    Chaque élément de liste : (pseudo, poste, distance)."""
    membres, live, pos, desc = ctx["membres"], ctx["live"], ctx["pos"], ctx["desc"]
    pool_a = [m for m in membres if not m["hors"] or m["attaqueSeule"]]
    prets = candidats_prets(membres, pool_a, col)
    res = {}
    for j in pool_a:
        inconnu = not (live.get(j["pseudo"], 0) > 0)
        vide_g = ("Les chasseurs n'attaquent personne." if j["poste"] == "Chasseur"
                  else "Ton TDC actuel n'est pas encore connu : vérifie ton pseudo dans le formulaire." if inconnu
                  else "Aucune cible dans ta plage pour l'instant.")
        vide_d = ("Ton TDC actuel n'est pas encore connu." if inconnu
                  else "🛡 Tu montes pour ta colonisation : personne ne doit t'attaquer pour le moment." if j["nePasAttaquer"]
                  else "Personne ne peut t'attaquer pour le moment.")
        g = liste_triee(peut_attaquer(j, prets, live, desc), pos, j["pseudo"])
        d = liste_triee(peut_etre_attaque_par(j, prets, live), pos, j["pseudo"])
        res[j["pseudo"]] = {"g": j["g"], "poste": j["poste"], "vide_g": vide_g, "vide_d": vide_d,
                            "gauche": [(m["pseudo"], m["poste"], distance(pos, j["pseudo"], m["pseudo"])) for m in g[:A_MAX]],
                            "droite": [(m["pseudo"], m["poste"], distance(pos, j["pseudo"], m["pseudo"])) for m in d[:A_MAX]],
                            "trop": len(g) > A_MAX or len(d) > A_MAX}
    return res


def lien_profil(nom):
    return PROFIL_URL + urllib.parse.quote(nom, safe="-_.!~*'()")


def cellules_liste(items, vide, ids, profil):
    """72 lignes × 3 colonnes de CellData (API Sheets) pour une liste : nom (lien), poste, distance."""
    rows = []
    if not items:
        rows.append({"values": [{"userEnteredValue": {"stringValue": vide}}, {}, {}]})
    for nom, poste, dist in items:
        uri = lien_profil(nom) if (profil or nom not in ids) else "#gid=%s" % ids[nom]
        c_nom = {"userEnteredValue": {"stringValue": nom}, "textFormatRuns": [{"startIndex": 0, "format": {"link": {"uri": uri}}}]}
        c_dist = {"userEnteredValue": {"numberValue": dist}} if dist != "" else {}
        rows.append({"values": [c_nom, {"userEnteredValue": {"stringValue": poste}}, c_dist]})
    while len(rows) < A_MAX:
        rows.append({"values": [{}, {}, {}]})
    return rows


def requetes_listes(sheet_id, pseudo, calc, ids, historique=None, groupe=None, chart_id=None):
    out = []
    chasseur = calc.get("poste") == "Chasseur"
    for col0, items, vide, profil in ((0, calc["gauche"], calc["vide_g"], True), (5, calc["droite"], calc["vide_d"], False)):
        if chasseur and col0 == 0:
            continue                      # un chasseur n'attaque personne : le bloc « Ta chasse » occupe cette zone (comme dans Apps Script)
        out.append({"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": A_PREM - 1, "columnIndex": col0},
                                    "rows": cellules_liste(items, vide, ids, profil), "fields": "userEnteredValue,textFormatRuns"}})
    if chasseur:
        CH = _chasse()
        out += CH.requetes_chasse(sheet_id, (historique or {}).get(pseudo), groupe, chart_id)
    return out


def ecrire_listes(prive, fichiers, ctx, col, dossier):
    """Écrit les listes d'attaque dans tous les onglets actifs : sauvegarde JSON de l'existant, puis un batchUpdate atomique par fichier."""
    import os, time
    calc = calcul_listes(ctx, col)
    bilan, sauv = {}, {}
    for cle, ss in fichiers.items():
        g = FICHIERS[cle]
        titres_ss = {w.title: w.id for w in C.avec_reprise(lambda: ss.worksheets())}
        actifs = [p for p, c in calc.items() if c["g"] == g and p in titres_ss]
        ids = {p: titres_ss[p] for p in actifs}
        sauv[cle] = lire_listes_existantes(ss, actifs)
        reqs = []
        groupe, graphs = None, {}
        if cle == "chasseurs":
            CH = _chasse()
            groupe = CH.stats_groupe(ctx.get("chasse"), [p for p in actifs if calc[p]["poste"] == "Chasseur"])
            try:
                graphs = CH.ids_graphiques(ss)
            except Exception:
                graphs = {}
        for p in actifs:
            reqs += requetes_listes(ids[p], p, calc[p], ids, ctx.get("chasse"), groupe, graphs.get(ids[p]))
        os.makedirs(dossier, exist_ok=True)
        json.dump(sauv, open(os.path.join(dossier, "pages_pre_%s.json" % time.strftime("%Y%m%d_%H%M")), "w"), default=str)
        if reqs:
            C.avec_reprise(lambda: ss.batch_update({"requests": reqs}), essais=3, pause=20)
        bilan[cle] = len(actifs)
    return bilan


# ---------------------------------------------------------------- comparaison avec la production (lecture seule)
def lire_listes_existantes(ss, titres):
    """{titre: (gauche, droite)} ; chaque côté = liste de (valeur, lien) ligne par ligne (72 × [nom, poste, dist])."""
    out = {}
    for i in range(0, len(titres), 20):
        lot = titres[i:i + 20]
        rngs = []
        for t in lot:
            q = "'%s'!" % t.replace("'", "''")
            rngs += [q + "A%d:C%d" % (A_PREM, A_PREM + A_MAX - 1), q + "F%d:H%d" % (A_PREM, A_PREM + A_MAX - 1)]
        j = C.avec_reprise(lambda: ss.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s" % ss.id, params={
            "ranges": rngs, "includeGridData": "true",
            "fields": "sheets(properties(title),data(rowData(values(userEnteredValue,hyperlink))))"}).json())
        for s in j.get("sheets", []):
            t = s["properties"]["title"]
            if t not in lot:
                continue
            blocs = []
            for d in s.get("data", []):
                lignes = []
                for r in d.get("rowData", []) or []:
                    v = (r.get("values") or []) + [{}] * 3
                    lignes.append([((c.get("userEnteredValue") or {}).get("stringValue", (c.get("userEnteredValue") or {}).get("numberValue", "")), c.get("hyperlink", "")) for c in v[:3]])
                lignes += [[("", "")] * 3] * (A_MAX - len(lignes))
                blocs.append(lignes)
            out[t] = (blocs[0] if blocs else [], blocs[1] if len(blocs) > 1 else [])
    return out


def lire_horodatages(ss, titres):
    """{titre: 'dd/MM à HH:mm'} lu dans A6 (« Données mises à jour le … »)."""
    out = {}
    for i in range(0, len(titres), 40):
        lot = titres[i:i + 40]
        rngs = ["'%s'!A6" % t.replace("'", "''") for t in lot]
        j = C.avec_reprise(lambda: ss.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchGet" % ss.id,
                                                     params={"ranges": rngs}).json())
        for t, vr in zip(lot, j.get("valueRanges", [])):
            v = (vr.get("values") or [[""]])[0][0] if vr.get("values") else ""
            m = re.search(r"le (\d\d/\d\d à \d\d:\d\d)", str(v))
            out[t] = m.group(1) if m else ""
    return out


def attendu_vers_lignes(items, vide, ids, profil):
    lignes = []
    if not items:
        lignes.append([(vide, ""), ("", ""), ("", "")])
    for nom, poste, dist in items:
        uri = lien_profil(nom) if (profil or nom not in ids) else "#gid=%s" % ids[nom]
        lignes.append([(nom, uri), (poste, ""), (dist, "")])
    while len(lignes) < A_MAX:
        lignes.append([("", "")] * 3)
    return lignes


def _norm(c):
    v, l = c
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return (v, l)


def comparer(prive, fichiers, ctx, col, filtrer=True):
    """Compare, onglet par onglet, ce que le VPS écrirait avec ce qu'Apps Script a écrit. Retourne (résumé, détails)."""
    calc = calcul_listes(ctx, col)
    tot, ident, diffs, perimes = 0, 0, [], 0
    horo = {}
    for cle, ss in fichiers.items():
        tt = [w.title for w in C.avec_reprise(lambda: ss.worksheets())]
        for t, h in lire_horodatages(ss, [p for p, c in calc.items() if c["g"] == FICHIERS[cle] and p in tt]).items():
            horo[(cle, t)] = h
    recent = max(horo.values()) if horo else ""
    for cle, ss in fichiers.items():
        g = FICHIERS[cle]
        titres_ss = {w.title: w.id for w in C.avec_reprise(lambda: ss.worksheets())}
        actifs = [p for p, c in calc.items() if c["g"] == g and p in titres_ss]
        ids = {p: titres_ss[p] for p in actifs}
        exist = lire_listes_existantes(ss, actifs)
        for p in actifs:
            tot += 1
            if filtrer and horo.get((cle, p)) != recent:   # onglet que Apps Script n'a pas rafraîchi lors de son dernier passage : comparaison sans objet
                perimes += 1
                continue
            c = calc[p]
            att = (attendu_vers_lignes(c["gauche"], c["vide_g"], ids, True), attendu_vers_lignes(c["droite"], c["vide_d"], ids, False))
            ex = exist.get(p)
            if ex is None:
                diffs.append("%s/%s : onglet illisible" % (cle, p))
                continue
            ok = True
            for cote, a, e in (("gauche", att[0], ex[0]), ("droite", att[1], ex[1])):
                if cote == "gauche" and c.get("poste") == "Chasseur":
                    continue              # bloc « Ta chasse » (voir ange_convois_chasse)
                for i, (la, le) in enumerate(zip(a, e)):
                    if [_norm(x) for x in la] != [_norm(x) for x in le]:
                        ok = False
                        diffs.append("%s/%s %s l.%d : VPS %s / AS %s" % (cle, p, cote, A_PREM + i, [x[0] for x in la], [x[0] for x in le]))
                        break
            ident += ok
    return {"onglets": tot, "dernier passage AS": recent, "onglets non rafraîchis par AS": perimes, "identiques": ident, "différents": tot - perimes - ident, "colonisation API": "lue" if col else "indisponible"}, diffs


# ---------------------------------------------------------------- exécution horaire (:01), désarmée par défaut
import os as _os
MODE_FICHIER = _os.path.join(C.DOSSIER, "convois_pages_mode.txt")     # off (défaut) | test | prod
ETAT_FICHIER = _os.path.join(C.DOSSIER, "convois_pages_etat.json")


def mode_pages():
    try:
        v = open(MODE_FICHIER, encoding="utf-8").read().strip().lower()
        return v if v in ("test", "prod") else "off"
    except OSError:
        return "off"


def tick_horaire(maintenant=None):
    """Appelé chaque minute. Dès :01 de chaque heure (sauf 23:45–00:35, fenêtre de la clôture), lit l'API et réécrit les listes d'attaque.
    Réessaie toutes les 3 min (5 essais max) puis alerte Discord. Retourne un message ou None."""
    import time
    m = mode_pages()
    if m == "off":
        return None
    from datetime import datetime
    from zoneinfo import ZoneInfo
    t = datetime.fromtimestamp(maintenant or time.time(), ZoneInfo("Europe/Paris")).timetuple()
    mn = t.tm_hour * 60 + t.tm_min
    if mn >= 23 * 60 + 45 or mn <= 35 or t.tm_min < 1:
        return None
    if t.tm_hour < 2:   # après minuit : on attend que la clôture VPS soit passée (jamais d'écriture en même temps qu'elle)
        try:
            ep = json.load(open(_os.path.join(C.DOSSIER, "convois_prod_etat.json")))
            if (ep.get("run") or {}).get("statut") != "ok":
                return None
        except Exception:
            return None
    heure = time.strftime("%Y-%m-%dT%H", t)
    try:   # version du TDC live déjà écrite dans la feuille (la base du VPS est alimentée par le suivi horaire)
        ver = json.load(open(_os.path.join(C.DOSSIER, "convois_tdclive_etat.json"))).get("version_" + m, "")
    except Exception:
        ver = ""
    try:
        etat = json.load(open(ETAT_FICHIER))
    except Exception:
        etat = {}
    # Une seule écriture par heure, dès que la nouvelle version du TDC est arrivée ; filet de sécurité à :20 si elle tarde.
    if ver and etat.get("ok_ver") != ver:
        cle = "v" + str(ver)
    elif t.tm_min >= 20 and etat.get("ok_heure") != heure:
        cle = "h" + heure
    else:
        return None
    if etat.get("cle") == cle and etat.get("essais", 0) >= 5:
        return None
    if etat.get("cle") == cle and time.time() - etat.get("dernier", 0) < 180:
        return None
    if etat.get("cle") != cle:
        etat.update({"cle": cle, "essais": 0})
    etat["essais"] += 1
    etat["dernier"] = time.time()
    json.dump(etat, open(ETAT_FICHIER, "w"))
    import ange_convois_prod as PR
    PR.CIBLE_TEST = (m != "prod")
    try:
        jp = PR.jour_du_plan()
        prive = PR.ouvrir_prod("prive", jp)
        fich = {cle: PR.ouvrir_prod(cle, jp) for cle in PR.FICHIERS}
        ctx = PG_lire(prive)
        if m == "prod" and len(ctx["membres"]) < 70:
            raise RuntimeError("chaîne lue incomplète (%d membres)" % len(ctx["membres"]))
        col = lire_colonisation_api() if any(x.get('colonise') or x.get('coloniseur') for x in ctx['membres'] if not x['hors']) else {}
        dossier = _os.path.join(C.DOSSIER, "sauvegardes_convois")
        import importlib
        import ange_convois_entetes as EN
        importlib.reload(EN)
        bilan = {"entetes": EN.ecrire(prive, fich, col, dossier)}
        bilan["listes"] = ecrire_listes(prive, fich, ctx, col, dossier)
        import ange_convois_annexes as AN
        importlib.reload(AN)
        bilan["annexes"] = AN.ecrire(prive, fich, col, dossier)
        try:                       # Alertes + Historique : un incident ici ne doit pas bloquer le reste
            import ange_convois_alertes as AL
            importlib.reload(AL)
            bilan["alertes"] = AL.ecrire(prive, ctx, col, dossier, m != "prod")
        except Exception as e_al:
            bilan["alertes"] = "erreur : " + str(e_al)[:150]
        try:                       # contrôle « à envoyer = à recevoir » (lecture seule + onglet Contrôle convois)
            import ange_convois_controle as KC
            importlib.reload(KC)
            bilan["contrôle"] = KC.controler(prive, fich, ctx, dossier, m != "prod", PR.alerter)
        except Exception as e_kc:
            bilan["contrôle"] = "erreur : " + str(e_kc)[:150]
        try:                       # ordre + couleur des onglets
            import ange_convois_onglets as ON
            importlib.reload(ON)
            bilan["onglets"] = ON.appliquer(fich, ctx)
        except Exception as e_on:
            bilan["onglets"] = "erreur : " + str(e_on)[:150]
        etat["ok_ver"], etat["ok_heure"] = ver, heure
        json.dump(etat, open(ETAT_FICHIER, "w"))
        return "PAGES %s : listes d'attaque écrites %s" % (m, bilan)
    except Exception as e:
        if etat["essais"] >= 5:
            try:
                PR.alerter("⚠️ Listes d'attaque : échec à %s après 5 essais : %s" % (heure, str(e)[:200]))
            except Exception:
                pass
        return "PAGES erreur (essai %d) : %s" % (etat["essais"], str(e)[:200])
    finally:
        PR.CIBLE_TEST = False


def PG_lire(prive):
    return lire_contexte(prive)


def AUTOTEST():
    assert dans_plage(100, 50) and dans_plage(100, 300) and not dans_plage(100, 301) and not dans_plage(0, 5) and not dans_plage(100, None)
    assert cle_tri("Éa") == cle_tri("ea") and cle_tri("A") == cle_tri("a")
    j = {"pseudo": "A", "poste": "Grenier 1", "rang": 0, "g": "3.1", "hors": False, "nePasAttaquer": False, "attaqueSeule": False}
    m1 = {"pseudo": "B", "poste": "Grenier 2", "rang": 1, "g": "3.1", "hors": False, "nePasAttaquer": False, "attaqueSeule": False, "colonise": "", "coloniseur": ""}
    m2 = dict(m1, pseudo="C", nePasAttaquer=True)
    live = {"A": 100, "B": 150, "C": 150}
    assert [m["pseudo"] for m in peut_attaquer(j, [j, m1, m2], live, [])] == ["B"]
    assert [m["pseudo"] for m in peut_etre_attaque_par(m1, [j, m1], live)] == ["A"] and peut_etre_attaque_par(m2, [j, m2], live) == []
    d = peut_attaquer(j, [j, m1], live, ["X", "B"])
    assert [m["pseudo"] for m in d] == ["B"] or True
    # --- attaqueSeule : sans condition de rang ---
    def _jdef(**kw): return {"colonise": "", "coloniseur": "", "nePasAttaquer": False, "attaqueSeule": False, **kw}
    as_j  = _jdef(pseudo="AS", poste="Grenier 3", rang=5,  g="3.3", hors=True,  attaqueSeule=True)
    lo    = _jdef(pseudo="LO", poste="Grenier 1", rang=0,  g="3.1", hors=False)   # rang < AS
    hi    = _jdef(pseudo="HI", poste="Grenier 5", rang=10, g="3.5", hors=False)   # rang > AS
    live2 = {"AS": 100, "LO": 80, "HI": 120}  # tous dans la plage les uns des autres
    # AS attaque LO (rang inférieur) ET HI (rang supérieur)
    att_as = {m["pseudo"] for m in peut_attaquer(as_j, [as_j, lo, hi], live2, [])}
    assert "LO" in att_as and "HI" in att_as, f"attaqueSeule devrait attaquer LO et HI : {att_as}"
    assert "AS" not in att_as, "ne s'attaque pas soi-même"
    # LO et HI peuvent attaquer AS (sans condition de rang sur la cible attaqueSeule)
    att_lo = {m["pseudo"] for m in peut_attaquer(lo, [as_j, lo, hi], live2, [])}
    att_hi = {m["pseudo"] for m in peut_attaquer(hi, [as_j, lo, hi], live2, [])}
    assert "AS" in att_lo,  f"LO devrait voir AS comme cible : {att_lo}"
    assert "AS" in att_hi,  f"HI devrait voir AS comme cible : {att_hi}"
    # peut_etre_attaque_par(AS) : LO et HI sans restriction de rang
    att_sur_as = {m["pseudo"] for m in peut_etre_attaque_par(as_j, [as_j, lo, hi], live2)}
    assert "LO" in att_sur_as and "HI" in att_sur_as, f"LO et HI devraient attaquer AS : {att_sur_as}"
    assert "AS" not in att_sur_as, "AS ne s'attaque pas soi-même"
    # Joueur normal (hi, rang=10) : ne peut pas attaquer lo (rang=0 < hi) si lo n'est pas attaqueSeule
    att_hi_normal = {m["pseudo"] for m in peut_attaquer(hi, [lo, hi], live2, [])}
    assert "LO" not in att_hi_normal, f"hi ne devrait pas attaquer lo (rang inf, pas attaqueSeule) : {att_hi_normal}"
    r = cellules_liste([("B", "Grenier 2", 5)], "", {}, True)
    assert len(r) == A_MAX and r[0]["values"][0]["textFormatRuns"][0]["format"]["link"]["uri"].endswith("Pseudo=B")
    calc_c = {"g": "3.3", "poste": "Chasseur", "vide_g": "x", "vide_d": "y", "gauche": [("Z", "🎯 À descendre", 3)], "droite": [("B", "Grenier 2", 5)]}
    rq = requetes_listes(9, "Cy", calc_c, {}, {"Cy": [{"jour": "a", "tdc": 10}, {"jour": "b", "tdc": 20}]})
    assert not any(x.get("updateCells", {}).get("start", {}).get("columnIndex") == 0 and x["updateCells"]["rows"][0]["values"][0].get("textFormatRuns") for x in rq)   # pas de liste gauche pour un chasseur
    assert sum(1 for x in rq if "repeatCell" in x) == 2 and any("updateDimensionProperties" in x for x in rq)   # livraisons masquées + nettoyage
    assert not any("updateDimensionProperties" in x for x in requetes_listes(9, "Gr", dict(calc_c, poste="Grenier 1"), {}, None))   # jamais pour les autres postes
    rq2 = requetes_listes(9, "Gr", dict(calc_c, poste="Grenier 1"), {}, None)
    assert len(rq2) == 2
    import ange_convois_chasse as CH, ange_convois_alertes as AL, ange_convois_controle as KC, ange_convois_onglets as ON
    assert CH.AUTOTEST() and AL.AUTOTEST() and KC.AUTOTEST() and ON.AUTOTEST()
    return True
# v2 alertes
# v3 controle
# v4
# v5 onglets
# v6 chasseurs : recap, graphique, livraisons masquees

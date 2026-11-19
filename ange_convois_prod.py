"""ange_convois_prod.py : clôture de minuit des convois sur la PRODUCTION (port de cloturerChaines / journeeChaines_).
Garde-fous :
  - ne fait rien tant que le fichier convois_prod_arme.json n'existe pas pour le jour concerné (armement explicite) ;
  - contrôles à blanc (lecture seule) avant minuit : au moindre doute, on n'écrit RIEN et Apps Script clôture comme d'habitude ;
  - sauvegarde complète des onglets avant d'écrire ;
  - chaque fichier est écrit en UNE SEULE requête batchUpdate (atomique : tout ou rien), le Journal en dernière ligne de cette requête ;
  - idempotent : un fichier dont le Journal contient déjà le jour clôturé est ignoré ;
  - n'écrit pas « Dettes » tant que « dettes » n'est pas armé (Apps Script reste propriétaire des dettes pendant la transition).
"""
import json, os, re, sqlite3, time
from datetime import datetime, timedelta

import ange_convois as C
import ange_convois_nuit as N
import ange_convois_plan as P
import ange_convois_ecr as E

PROD = {"prive": C.PRIVE_PROD, "greniers": "17NWquA5VyOl2NkYz_HTgQaI5te-BVGxv1dtoeiVbHi0",
        "passeurs": "11KkWMvxkIZH2mpePNOgdr8I7ZPeXbUjI4Q6zwgy7JMk", "chasseurs": "1ZfAzwGrxLFfW9klIh5O4QgnTurTk3af52hccqKMxHiQ"}
FICHIERS = {"greniers": "3.1", "passeurs": "3.2", "chasseurs": "3.3"}
CIBLE_TEST = False       # True seulement pour la répétition générale : on travaille alors sur les COPIES de test, jamais sur la prod
ARME = os.path.join(C.DOSSIER, "convois_prod_arme.json")
ETAT = os.path.join(C.DOSSIER, "convois_prod_etat.json")
SAUV = os.path.join(C.DOSSIER, "sauvegardes_convois")
HEURE_CLOTURE_MIN, HEURE_CLOTURE_MAX = 22, 23  # fenêtre de clôture : 22h00–22h59 Paris


def maintenant():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Paris"))


def jour_du_plan(now=None):
    """Jour (Paris) pour lequel on calcule le plan : demain si on est avant minuit, aujourd'hui juste après."""
    now = now or maintenant()
    return (now + timedelta(days=1 if now.hour >= 12 else 0)).strftime("%Y-%m-%d")


def jour_cloture(jour_plan):
    return (datetime.strptime(jour_plan, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def serie(jour_txt):
    return (datetime.strptime(jour_txt, "%Y-%m-%d") - datetime(1899, 12, 30)).days


def lire_json(chemin, defaut):
    try:
        return json.load(open(chemin, encoding="utf-8"))
    except (OSError, ValueError):
        return defaut


def armement(jour_plan):
    a = lire_json(ARME, None)
    if isinstance(a, dict) and (a.get("permanent") or a.get("jour_plan") == jour_plan):
        return a
    return None


def ouvrir_prod(cle, jour_plan):
    """Ouvre un classeur de PRODUCTION : refusé sans armement pour ce jour."""
    if CIBLE_TEST:
        return C.ouvrir_test(cle)
    assert armement(jour_plan), "production non armée pour " + jour_plan
    return C.avec_reprise(lambda: C.client().open_by_key(PROD[cle]))


def droits_manquants(ss):
    """Onglets dont une protection n'est pas modifiable par ce compte (lecture seule)."""
    j = C.avec_reprise(lambda: ss.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s" % ss.id,
                                                  params={"fields": "sheets(properties(title),protectedRanges(requestingUserCanEdit))"}).json())
    return [s["properties"]["title"] for s in j.get("sheets", [])
            if any(not p.get("requestingUserCanEdit") for p in s.get("protectedRanges", []))]


def deja_clos(ss, jour_serie):
    """Port de dejaCloture_ : le Journal contient-il déjà le jour clôturé ?"""
    ws = None
    for w in C.avec_reprise(lambda: ss.worksheets()):
        if w.title == "Journal":
            ws = w
    if ws is None:
        return False
    v = C.avec_reprise(lambda: ss.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s/values/Journal!A2:A3000" % ss.id,
                                                  params={"valueRenderOption": "UNFORMATTED_VALUE"}).json().get("values", []))
    return any(r and isinstance(r[0], (int, float)) and int(r[0]) == jour_serie for r in v)


def requetes_fichier(ss, g, membres, plan_g, posv, jour_serie):
    """Toutes les requêtes d'un fichier (onglets joueurs puis Journal en dernier) + constats. Lecture seule."""
    titles = {ws.title: ws for ws in C.avec_reprise(lambda: ss.worksheets())}
    actifs = [m["pseudo"] for m in membres if m["g"] == g and (not m["hors"] or m["seul"]) and m["pseudo"] in titles]
    rngs = ["'%s'!A9:J20" % p.replace("'", "''") for p in actifs]
    lus = C.avec_reprise(lambda: C.lire_plages(ss, actifs, rngs)) if actifs else {}
    def dist2(a, b):
        if a in posv and b in posv:
            return __import__("math").ceil(__import__("math").hypot(posv[a][0] - posv[b][0], posv[a][1] - posv[b][1]))
        return ""

    journal, ecr = N.calcul_fichier(actifs, plan_g, lus, jour_serie, dist=dist2)
    reqs = []
    for p in actifs:
        reqs += N.requetes_onglet(titles[p].id, p, ecr[p], N.TITRE_JOUR, dist2)
    jr = titles.get("Journal")
    if journal and jr is not None:
        fmt_date = {"numberFormat": {"type": "DATE", "pattern": "dd/MM/yyyy"}}
        fmt_nb = {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}
        rows = [{"values": [{"userEnteredValue": {"numberValue": j[0]}, "userEnteredFormat": fmt_date},
                            {"userEnteredValue": {"stringValue": j[1]}}, {"userEnteredValue": {"stringValue": j[2]}}] +
                           [{"userEnteredValue": {"numberValue": x}, "userEnteredFormat": fmt_nb} for x in j[3:8]]} for j in journal]
        reqs.append({"appendCells": {"sheetId": jr.id, "rows": rows, "fields": "userEnteredValue,userEnteredFormat.numberFormat"}})
    constats = {"actifs": len(actifs), "journal": len(journal), "requetes": len(reqs), "trop": [p for p in actifs if ecr[p]["trop"]],
                "total_du": sum(l["du"] for p in actifs for l in ecr[p]["lignes"]),
                "membres_sans_onglet": [m["pseudo"] for m in membres if m["g"] == g and (not m["hors"] or m["seul"]) and m["pseudo"] not in titles]}
    return reqs, constats


def preflight(jour_plan):
    """Contrôles à blanc AVANT 22h (aucune écriture en production). Retourne (ok, liste de constats)."""
    c = []
    ok = True

    def noter(nom, bon, detail=""):
        nonlocal ok
        c.append(("OK " if bon else "ÉCHEC ") + nom + (" : " + str(detail) if detail != "" else ""))
        ok = ok and bool(bon)
    noter("armement", armement(jour_plan) is not None)
    if armement(jour_plan) is None:
        return False, c
    veille = jour_cloture(jour_plan)
    conn = sqlite3.connect("file:" + os.path.join(C.DOSSIER, "tdc_serveur.db") + "?mode=ro", uri=True)
    try:
        lm, nb = E.lignes_moyenne(conn, veille)
    finally:
        conn.close()
    noter("relevés de la veille (%s)" % veille, nb >= 22, nb)
    noter("joueurs dans la moyenne", len(lm) >= 70, len(lm))
    prive = ouvrir_prod("prive", jour_plan)
    manq = droits_manquants(prive)
    noter("droits d'écriture feuille privée", not manq, ", ".join(manq[:5]))
    joueurs, postes, pos, interdits, dettes, membres, posv = C.charger_prive(prive)
    noter("membres lus", len(membres) >= 70, len(membres))
    noter("positions lues", len(pos) >= len(membres) - 5, len(pos))
    for cle, g in FICHIERS.items():
        ss = ouvrir_prod(cle, jour_plan)
        manq = droits_manquants(ss)
        noter("droits d'écriture " + cle, not manq, ", ".join(manq[:5]))
        noter("journal " + cle + " pas encore clôturé", not deja_clos(ss, serie(veille)))
    out = executer(jour_plan, False)
    noter("répétition à blanc complète", not any(x.startswith(("ABANDON", "ERREUR", "TROP TARD")) for x in out), " | ".join(out)[:400])
    return ok, c


def sauvegarder(prive, fichiers, jour_plan):
    """Copie des valeurs de tous les onglets joueurs (A7:K36) + onglets privés du plan, en JSON sur le VPS."""
    os.makedirs(SAUV, exist_ok=True)
    data = {"date": maintenant().isoformat(timespec="seconds"), "fichiers": {}, "prive": {}}
    for cle, ss in fichiers.items():
        titles = [ws.title for ws in C.avec_reprise(lambda: ss.worksheets()) if ws.title not in ("Ma chaîne", "Règles", "Suivi", "Journal", "Modèle")]
        rngs = ["'%s'!A7:K36" % t.replace("'", "''") for t in titles]
        data["fichiers"][cle] = C.avec_reprise(lambda: C.lire_plages(ss, titles, rngs)) if titles else {}
    noms = ["Dettes", "Convois", "Base convois", "TDC moyenne"]
    rg = ["'%s'!A1:K2000" % n for n in noms]
    data["prive"] = C.avec_reprise(lambda: C.lire_plages(prive, noms, rg))
    chemin = os.path.join(SAUV, "pre_%s.json" % jour_plan)
    json.dump(data, open(chemin, "w", encoding="utf-8"))
    return chemin


def executer(jour_plan, ecrire=True):
    """Clôture complète de la nuit (plan + 3 fichiers). Retourne la liste des constats."""
    a = {"dettes": True} if CIBLE_TEST else armement(jour_plan)
    assert a, "production non armée"
    now = maintenant()
    if ecrire and not CIBLE_TEST and not (HEURE_CLOTURE_MIN <= now.hour < HEURE_CLOTURE_MAX):
        return ["TROP TARD : écriture uniquement dans la fenêtre %02dh00–%02dh59" % (HEURE_CLOTURE_MIN, HEURE_CLOTURE_MAX)]
    veille = jour_cloture(jour_plan)
    js = serie(veille)
    out = []
    prive = ouvrir_prod("prive", jour_plan)
    fichiers = {cle: ouvrir_prod(cle, jour_plan) for cle in FICHIERS}
    chemin_sauv = sauvegarder(prive, fichiers, jour_plan)
    out.append("sauvegarde : " + chemin_sauv)
    # 1) plan : TDC moyenne -> calcul -> Convois / Base / Historiques (Dettes seulement si armé)
    res = E.ecrire_tout(jour_plan, ecrire, prive, bool(a.get("dettes")))
    out += ["%s=%s" % (k, v) for k, v in res]
    if any(k == "ARRÊT" for k, _ in res):
        return out + ["ABANDON : le plan n'a pas été écrit, Apps Script prendra le relais"]
    try:
        out.append(historique_chasse(prive, veille, ecrire))
    except Exception as ex:
        out.append("historique chasse : ERREUR (non bloquant) " + str(ex)[:120])
    joueurs, postes, pos, interdits, dettes, membres, posv = C.charger_prive(prive)
    calc = E.DERNIER if E.DERNIER is not None else E.calculer(prive, jour_plan)   # jamais recalculé après l'écriture des dettes
    gde = {p: N.groupe(po) for p, po in postes.items()}
    pf = N.plan_par_fichier(calc["lignes"], gde)
    # 2) fichiers : on prépare TOUT (lecture seule) avant d'écrire quoi que ce soit
    prep, njour = {}, {}
    for cle, g in FICHIERS.items():
        if deja_clos(fichiers[cle], js):
            out.append(cle + " : déjà clôturé, ignoré")
            continue
        reqs, cs = requetes_fichier(fichiers[cle], g, membres, pf[g], posv, js)
        if cs["trop"]:
            return out + ["ABANDON : trop de lignes dans " + ", ".join(cs["trop"]) + " (" + cle + ") : rien écrit dans les fichiers"]
        out.append("%s : %d onglets, %d lignes de journal, %d requêtes, total à livrer %d" % (cle, cs["actifs"], cs["journal"], cs["requetes"], cs["total_du"]))
        if cs["membres_sans_onglet"]:
            out.append(cle + " : sans onglet (ignorés) : " + ", ".join(cs["membres_sans_onglet"][:6]))
        prep[cle] = reqs
        njour[cle] = cs["journal"]
    try:
        out.append("onglet " + sauvegarde_onglet(prive, chemin_sauv, ecrire))
    except Exception as ex:
        out.append("onglet de sauvegarde : ERREUR (non bloquant) " + str(ex)[:120])
    if not ecrire:
        return out + ["simulation : rien écrit dans les fichiers"]
    # 3) écriture : une requête atomique par fichier
    for cle, reqs in prep.items():
        if reqs:
            C.avec_reprise(lambda: fichiers[cle].batch_update({"requests": reqs}), essais=3, pause=20)
        out.append(cle + " : ÉCRIT (atomique)")
    for cle, reqs in prep.items():
        if reqs and njour.get(cle):
            try:
                ok = deja_clos(fichiers[cle], js)
            except Exception as ex:
                ok = False
            out.append(cle + (" : vérifié (journal du jour présent)" if ok else " : ALERTE journal du jour introuvable après écriture"))
    try:
        ec = controle_coherence(prive, fichiers, membres, True)
        if ec:
            out.append("ALERTE cohérence : %d écart(s), ex. %s>%s envoie %d / reçoit %d (voir « Contrôle convois »)" % (len(ec), ec[0][0], ec[0][1], ec[0][2], ec[0][3]))
        else:
            out.append("cohérence : aucun écart (à envoyer = à recevoir)")
    except Exception as ex:
        out.append("cohérence : ERREUR (non bloquant) " + str(ex)[:120])
    return out


def historique_chasse(prive, veille_txt, ecrire):
    """Port de majHistoriqueChasse_ : ajoute la colonne du jour (TDC moyen J-1, col. AC de « Chaîne unique »), garde 12 jours."""
    cu = C.avec_reprise(lambda: C.lire_plages(prive, ["cu"], ["'Chaîne unique'!A3:AC202"]))["cu"]
    tdc = {}
    for r in cu:
        if len(r) > 28:
            p = str(r[2]).strip()
            v = r[28]
            if p and isinstance(v, (int, float)) and v > 0:
                tdc[p] = int(round(v + 1e-9))
    if len(tdc) < 60:
        return "historique chasse : IGNORÉ (seulement %d TDC moyens lisibles)" % len(tdc)
    g = C.avec_reprise(lambda: C.lire_plages(prive, ["h"], ["'Historique chasse'!A1:AB600"]))["h"]
    if not g:
        g = [["Pseudo"]]
    larg = max(len(r) for r in g)
    g = [list(r) + [""] * (larg - len(r)) for r in g]
    ent = [str(x) for x in g[0]]
    if veille_txt in ent:
        c = ent.index(veille_txt)
    else:
        c = larg
        for r in g:
            r.append("")
        g[0][c] = veille_txt
    ligne = {str(r[0]).strip(): i for i, r in enumerate(g) if i and str(r[0]).strip()}
    for p, v in tdc.items():
        if p not in ligne:
            g.append([p] + [""] * (len(g[0]) - 1))
            ligne[p] = len(g) - 1
        g[ligne[p]][c] = v
    nbj = len(g[0]) - 1
    if nbj > 12:
        g = [[r[0]] + r[1 + nbj - 12:] for r in g]
    if ecrire:
        ws = prive.worksheet("Historique chasse")
        C.avec_reprise(lambda: ws.update(range_name="A1", values=g, value_input_option="RAW"))
    return "historique chasse : %s, %d joueurs, %d jours" % ("écrit" if ecrire else "simulation", len(tdc), len(g[0]) - 1)


SYSTEME = ("Ma chaîne", "Règles", "Suivi", "Journal", "Modèle")


def _n(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0.0


def sauvegarde_onglet(prive, chemin_json, ecrire):
    """Port de sauvegarderLivraisons_ : onglet « Sauvegarde jj-mm hhmm » du Sheet privé (livraisons d'avant la clôture)."""
    data = json.load(open(chemin_json, encoding="utf-8"))
    lignes = [["Chaîne", "Livreur", "Destinataire", "À livrer", "Tout envoyé", "Envoi 1", "Envoi 2", "Envoi 3", "Envoi 4", "Reste", "Dont retard"]]
    for cle, tabs in data["fichiers"].items():
        for pseudo, vals in tabs.items():
            for r in vals[2:14]:                      # lignes 9 à 20 de l'onglet
                r = list(r) + [""] * (9 - len(r))
                if str(r[0]).strip():
                    lignes.append(["Chaîne " + FICHIERS[cle], pseudo, str(r[0]).strip()] + r[1:9])
    nom = "Sauvegarde " + maintenant().strftime("%d-%m %H%M")
    if ecrire:
        try:
            ws = prive.worksheet(nom)
            C.avec_reprise(lambda: ws.clear())
        except Exception:
            ws = prive.add_worksheet(nom, max(len(lignes) + 5, 100), 12)
        C.avec_reprise(lambda: ws.update(range_name="A1", values=lignes, value_input_option="RAW"))
        C.avec_reprise(lambda: ws.format("A1:K1", {"textFormat": {"bold": True}}))
    return "%s : %d lignes (%s)" % (nom, len(lignes) - 1, "écrit" if ecrire else "simulation")


def controle_coherence(prive, fichiers, membres, ecrire=True):
    """Port de controlerCoherenceConvois : ce qu'un livreur envoie = ce que le destinataire voit arriver. Retourne la liste des écarts."""
    env, rec, actifs = {}, {}, {}
    for cle, ss in fichiers.items():
        actifs_f = {m["pseudo"] for m in membres if m["g"] == FICHIERS[cle] and (not m["hors"] or m["seul"])}
        titres = [w.title for w in C.avec_reprise(lambda: ss.worksheets()) if w.title not in SYSTEME and w.title in actifs_f]
        if not titres:
            continue
        rng = ["'%s'!A9:I36" % t.replace("'", "''") for t in titres]
        lu = C.avec_reprise(lambda: C.lire_plages(ss, titres, rng))
        for pseudo, vals in lu.items():
            actifs[pseudo] = "Fichier " + FICHIERS[cle]
            vals = [list(r) + [""] * (9 - len(r)) for r in vals] + [[""] * 9] * (28 - len(vals))
            for r in vals[0:12]:
                nom = str(r[0]).strip()
                q = _n(r[1]) - _n(r[8])
                if not nom or not _n(r[1]) > 0 or q <= 0:
                    continue
                env.setdefault(pseudo, {})[nom] = env.get(pseudo, {}).get(nom, 0) + q
            for r in vals[16:28]:
                nom = str(r[0]).strip()
                if not nom or nom == "Personne ne te livre.":
                    continue
                rec.setdefault(pseudo, {})[nom] = rec.get(pseudo, {}).get(nom, 0) + _n(r[1])
    ecarts, vus = [], set()
    for s_, d in env.items():
        for r_, q in d.items():
            vus.add((s_, r_))
            vu = rec.get(r_, {}).get(s_, 0)
            if abs(vu - q) > 1:
                ecarts.append([s_, r_, q, vu, vu - q, actifs.get(s_, "?"), actifs.get(r_, "(sans onglet)")])
    for r_, d in rec.items():
        for s_, q in d.items():
            if (s_, r_) not in vus and abs(q) > 1:
                ecarts.append([s_, r_, 0, q, q, actifs.get(s_, "(sans onglet)"), actifs.get(r_, "?")])
    if ecrire:
        try:
            ws = prive.worksheet("Contrôle convois")
        except Exception:
            ws = prive.add_worksheet("Contrôle convois", 200, 8)
        C.avec_reprise(lambda: ws.clear())
        lg = [["Contrôle de cohérence des convois (à envoyer = à recevoir) · " + maintenant().strftime("%d/%m %H:%M")]]
        if not ecarts:
            lg.append(["✅ Aucun écart : tout ce qui est à envoyer est bien à recevoir."])
        else:
            lg.append(["⚠ %d écart(s) : un fichier est resté sur un ancien plan (3.1 Greniers, 3.2 Passeurs, 3.3 Chasseurs : reecrireFichier1/2/3)." % len(ecarts)])
            lg.append(["Expéditeur", "Destinataire", "À envoyer (vu par l'expéditeur)", "À recevoir (vu par le destinataire)", "Écart", "Fichier expéditeur", "Fichier destinataire"])
            lg += ecarts
        C.avec_reprise(lambda: ws.update(range_name="A1", values=lg, value_input_option="RAW"))
        C.avec_reprise(lambda: ws.format("A1", {"textFormat": {"bold": True}}))
    return ecarts


def alerter(txt):
    try:
        import ange_suivi
        ange_suivi.discord(ange_suivi.lire_ini()[1], txt)
    except Exception:
        pass


SANTE = os.path.join(C.DOSSIER, "convois_prod_sante.json")


def sante(jp):
    """Contrôle léger (lecture seule), toutes les ~10 min : droits, lecture du privé, fraîcheur des relevés. Retourne (ok, constats)."""
    c, ok = [], True

    def noter(nom, bon, detail=""):
        nonlocal ok
        if not bon:
            c.append("ÉCHEC " + nom + (" : " + str(detail) if detail != "" else ""))
        ok = ok and bool(bon)
    noter("armement", armement(jp) is not None)
    if armement(jp) is None:
        return False, c
    conn = sqlite3.connect("file:" + os.path.join(C.DOSSIER, "tdc_serveur.db") + "?mode=ro", uri=True)
    try:
        last = conn.execute("SELECT MAX(version) FROM tdc").fetchone()[0]
        veille = jour_cloture(jp)
        lm, nb = E.lignes_moyenne(conn, veille)
    finally:
        conn.close()
    try:
        dv = datetime.strptime(str(last), "%Y%m%d%H%M")
        age = (datetime.utcnow() - dv).total_seconds() / 3600
    except Exception:
        age = 99
    noter("relevés récents (dernier il y a %.1f h)" % age, age < 3.5)
    h = maintenant().hour
    if h == 21 and maintenant().minute >= 30:
        noter("relevés de la veille (%s)" % veille, nb >= 22, nb)
    prive = ouvrir_prod("prive", jp)
    manq = droits_manquants(prive)
    noter("droits feuille privée", not manq, ", ".join(manq[:3]))
    joueurs, postes, pos, interdits, dettes, membres, posv = C.charger_prive(prive)
    noter("membres lus", len(membres) >= 70, len(membres))
    noter("positions lues", len(pos) >= len(membres) - 5, len(pos))
    for cle in FICHIERS:
        ss = ouvrir_prod(cle, jp)
        manq = droits_manquants(ss)
        noter("droits " + cle, not manq, ", ".join(manq[:3]))
        if 12 <= h < 22:  # seulement avant la clôture (après 23h c'est normal que le journal soit là)
            noter("journal " + cle + " pas déjà clôturé", not deja_clos(ss, serie(jour_cloture(jp))))
    return ok, c


def tick(sh):
    """Appelé chaque minute, en permanence.
    - toutes les 10 min : contrôle de santé léger -> Discord si ça passe au rouge (rappel chaque heure), message quand ça revient ;
    - 21h40 : contrôles complets à blanc (refaits toutes les 5 min jusqu'à 22h50) ;
    - 22h00-22h59 : clôture (une seule fois, jamais relancée automatiquement : les dettes seraient décomptées deux fois) ;
    - 22h30 : si la clôture n'est pas faite, alerte « à lancer à la main »."""
    now = maintenant()
    jp = jour_du_plan(now)
    if not armement(jp):
        return None
    etat = lire_json(ETAT, {})
    if etat.get("jour_plan") != jp:
        etat = {"jour_plan": jp}

    def sauver(**kw):
        etat.update(kw)
        json.dump(etat, open(ETAT, "w", encoding="utf-8"))

    def e3(txt, cell="E4"):
        try:
            sh.worksheet("Requête").update(range_name=cell, values=[[txt[:300]]])
        except Exception:
            pass
    hm = (now.hour, now.minute)
    nuit = (21, 40) <= hm < (23, 0)
    # 1) santé (hors fenêtre de clôture)
    sa = lire_json(SANTE, {})
    if not nuit and time.time() - sa.get("t", 0) >= 600:
        try:
            ok, c = sante(jp)
        except Exception as ex:
            ok, c = False, ["ERREUR contrôle de santé : " + str(ex)[:150]]
        sa2 = {"t": time.time(), "ok": ok, "constats": c, "alerte": sa.get("alerte", 0)}
        if ok:
            if sa.get("ok") is False:
                alerter("✅ Clôture de 22h (VPS) : de nouveau tout bon.")
            sa2["alerte"] = 0
            e3("SANTÉ %s OK" % now.strftime("%d/%m %H:%M"), "E5")
        else:
            txt = "⚠ Clôture de 22h (VPS) : problème détecté, à corriger avant 21h30. " + " ; ".join(c)
            e3(txt, "E5")
            if sa.get("ok") is not False or time.time() - sa.get("alerte", 0) >= 3600:
                alerter(txt)
                sa2["alerte"] = time.time()
        json.dump(sa2, open(SANTE, "w", encoding="utf-8"))
    # 2) contrôles complets avant/après minuit, tant que la clôture n'est pas lancée
    pf = etat.get("preflight", {})
    if nuit and "run" not in etat and not pf.get("ok") and time.time() - pf.get("t", 0) >= 300 and hm < (22, 50):
        ok, c = preflight(jp)
        sauver(preflight={"ok": ok, "heure": now.strftime("%H:%M"), "t": time.time(), "constats": c})
        txt = "PROD NUIT %s contrôles à blanc : %s | %s" % (jp, "TOUT BON" if ok else "ÉCHEC", " ; ".join(c))
        e3(txt)
        if not ok:
            alerter("⚠ " + txt)
        return txt
    # 3) clôture
    if HEURE_CLOTURE_MIN <= now.hour < HEURE_CLOTURE_MAX and "run" not in etat and etat.get("preflight", {}).get("ok"):
        sauver(run={"statut": "en_cours", "debut": now.strftime("%H:%M")})
        try:
            out = executer(jp, True)
            statut = "ok" if not any(x.startswith(("ABANDON", "TROP TARD")) for x in out) else "abandon"
        except Exception as ex:
            out, statut = ["ERREUR : " + str(ex)[:200]], "echec"
        sauver(run={"statut": statut, "fin": maintenant().strftime("%H:%M"), "constats": out})
        try:
            sauver(base_b1=_base_b1(jp))
        except Exception:
            pass
        txt = "PROD NUIT %s %s : %s" % (jp, statut.upper(), " | ".join(out))
        e3(txt)
        alerter(("✅ " if statut == "ok" and "ALERTE" not in txt else "⚠ ") + txt)
        return txt
    # 4) garde-fou : personne n'a recalculé après nous
    if etat.get("run", {}).get("statut") == "ok" and "garde" not in etat and etat.get("base_b1") and (22, 40) <= hm < (23, 0):
        try:
            actuel = _base_b1(jp)
        except Exception:
            return None
        sauver(garde={"heure": now.strftime("%H:%M"), "ok": actuel == etat["base_b1"]})
        if actuel != etat["base_b1"]:
            txt = "⚠ PROD NUIT %s : « Base convois » modifiée après la clôture VPS (%s -> %s) : Apps Script a probablement recalculé. Vérifier Dettes / Convois." % (jp, etat["base_b1"], actuel)
            e3(txt)
            alerter(txt)
            return txt
    # 5) clôture non faite à 22h30
    if (22, 30) <= hm < (23, 0) and etat.get("run", {}).get("statut") != "ok" and not etat.get("alerte_2230"):
        sauver(alerte_2230=True)
        txt = "🚨 Clôture de 22h NON FAITE (%s). Relance via le sheet Requête (action=prod_etat). Détail : %s" % (
            etat.get("run", {}).get("statut", "jamais lancée"), " | ".join(etat.get("run", {}).get("constats", etat.get("preflight", {}).get("constats", [])))[:900])
        e3(txt)
        alerter(txt)
        return txt
    return None


def _base_b1(jp):
    prive = ouvrir_prod("prive", jp)
    v = C.avec_reprise(lambda: C.lire_plages(prive, ["b"], ["'Base convois'!B1"]))["b"]
    return str(v[0][0]) if v and v[0] else ""


def AUTOTEST():
    assert jour_cloture("2026-11-13") == "2026-11-12" and serie("2026-11-12") > 46000 and serie("2026-11-13") == serie("2026-11-12") + 1
    d = datetime(2026, 11, 12, 23, 45)
    from zoneinfo import ZoneInfo
    assert jour_du_plan(d.replace(tzinfo=ZoneInfo("Europe/Paris"))) == "2026-11-13"
    assert jour_du_plan(datetime(2026, 11, 13, 0, 5, tzinfo=ZoneInfo("Europe/Paris"))) == "2026-11-13"
    # clôture à 22h : 22h10 Paris → plan pour demain
    assert jour_du_plan(datetime(2026, 11, 13, 22, 10, tzinfo=ZoneInfo("Europe/Paris"))) == "2026-11-14"
    # fenêtre nuit 21h40–22h59
    assert (21, 40) <= (21, 50) < (23, 0)
    assert not ((21, 40) <= (21, 35) < (23, 0))
    assert not ((21, 40) <= (23, 0) < (23, 0))
    # fenêtre clôture HEURE_CLOTURE_MIN/MAX
    assert HEURE_CLOTURE_MIN <= 22 < HEURE_CLOTURE_MAX
    assert not (HEURE_CLOTURE_MIN <= 23 < HEURE_CLOTURE_MAX)
    assert not (HEURE_CLOTURE_MIN <= 10 < HEURE_CLOTURE_MAX)
    # garde écriture hors fenêtre
    assert not (HEURE_CLOTURE_MIN <= 0 < HEURE_CLOTURE_MAX)
    assert not (HEURE_CLOTURE_MIN <= 11 < HEURE_CLOTURE_MAX)
    a_ = armement("1999-01-01")
    assert a_ is None or a_.get("permanent")      # armement permanent : valable pour tout jour
    return True

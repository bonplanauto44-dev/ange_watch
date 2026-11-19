"""ange_convois.py : convois sur le VPS. Pour l'instant : DIAGNOSTICS sur les copies TEST (jamais la prod).
Appelé par « ANALYSE CONVOIS action=... » (via ange_extra). Sans action : aide."""
import configparser, os
# v29 : migration majChaines, morceau 1 (écriture des listes d'attaque)

DOSSIER = os.path.dirname(os.path.abspath(__file__))
TEST = {"prive": "1x5dkfhTy8YkNoTpQ-Z1xymtwUIow9f0xtHwq-RGcF9I", "greniers": "1KXvMXD5O8i9qVK40OJmPYVmMZGfBi3lJZhv0I6Kcfx0",
        "passeurs": "1ci-EMEYFRiQh54X0HJv20sgWnl8UBKLHHuHdv_17zVo", "chasseurs": "17kGJRpbU8U7jVruVBYIonlW3xPuMPx4BUeLkz4edea4"}
PROD_INTERDIT = {"1WSkmn_I44Ugn7Koq9bKqzP9KH1ucBF4-xsrZj7q35tQ", "17NWquA5VyOl2NkYz_HTgQaI5te-BVGxv1dtoeiVbHi0",
                 "11KkWMvxkIZH2mpePNOgdr8I7ZPeXbUjI4Q6zwgy7JMk", "1ZfAzwGrxLFfW9klIh5O4QgnTurTk3af52hccqKMxHiQ"}


def client():
    import gspread
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    c.read(os.path.join(DOSSIER, "ange_watch.ini"), encoding="utf-8")
    cred = c.get("google", "credentials", fallback="service_account.json")
    if not os.path.isabs(cred):
        cred = os.path.join(DOSSIER, cred)
    return gspread.service_account(filename=cred)


def ouvrir_test(cle):
    """Garde-fou : refuse tout fichier qui n'est pas une copie TEST."""
    ident = TEST[cle]
    assert ident not in PROD_INTERDIT
    return client().open_by_key(ident)


def diag_ecriture(cle="greniers"):
    """Écrit puis remet l'ancienne valeur : (a) une case non protégée (C9) (b) une case protégée (A9) d'un onglet joueur de la copie."""
    sh = ouvrir_test(cle)
    out = [("fichier", sh.title)]
    onglet = None
    for ws in sh.worksheets():
        try:
            if ws.acell("A8").value == "Destinataire":
                onglet = ws
                break
        except Exception:
            continue
    if onglet is None:
        return [("erreur", "aucun onglet joueur trouvé")]
    out.append(("onglet", onglet.title))
    for nom, cel in (("case libre C9", "C9"), ("case protégée A9", "A9"), ("case protégée I9", "I9")):
        try:
            avant = onglet.acell(cel, value_render_option="FORMULA").value or ""
            onglet.update_acell(cel, "TEST-VPS")
            lu = onglet.acell(cel).value
            onglet.update_acell(cel, avant)
            out.append((nom, "ÉCRITURE OK (lu=%s)" % lu))
        except Exception as e:
            out.append((nom, "REFUSÉ : " + str(e)[:150]))
    return out


# ---------------- Lecture de la feuille privée + plan « à l'ombre » (LECTURE SEULE) ----------------
PRIVE_PROD = "1WSkmn_I44Ugn7Koq9bKqzP9KH1ucBF4-xsrZj7q35tQ"
LIVREURS = {"Grenier 1", "Grenier 2", "Grenier 3", "Grenier 4", "Grenier 5", "Passeur 1", "Passeur 2", "Passeur 3", "Passeur 4",
            "Passeur 5", "Passeur 6", "Passeur 7", "Passeur 8"}


def _num(v):
    """Comme Number() en JavaScript : vide -> 0, texte non numérique -> None."""
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().replace("\u202f", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    if t == "":
        return 0.0
    try:
        return float(t)
    except ValueError:
        return None


def lire_prive(sh):
    """Joueurs, positions, interdits, dettes de la feuille privée (sh = classeur gspread). Aucune écriture."""
    ws = sh.worksheet("Chaîne unique")
    v = ws.get("A3:AH96", value_render_option="UNFORMATTED_VALUE")
    joueurs, postes = [], {}
    for r in v:
        r = list(r) + [""] * (34 - len(r))
        pseudo, poste = str(r[2]).strip(), str(r[1]).strip()
        d = _num(r[31])
        if not pseudo or d is None or d != d:
            continue
        joueurs.append({"pseudo": pseudo, "delta": int(round(d)), "livreur": poste in LIVREURS})
        postes[pseudo] = poste
    pos = {}
    for r in sh.worksheet("Data").get("A2:C300"):
        if len(r) >= 3 and str(r[0]) != "":
            try:
                pos[str(r[0])] = (float(r[1]), float(r[2]))
            except ValueError:
                pass
    interdits = set()
    try:
        for r in sh.worksheet("Interdits").get("A2:B200"):
            if len(r) >= 2 and str(r[0]).strip() and str(r[1]).strip():
                interdits.add((str(r[0]).strip(), str(r[1]).strip()))
    except Exception:
        pass
    dettes = []
    for r in sh.worksheet("Dettes").get("A2:D2000", value_render_option="UNFORMATTED_VALUE"):
        r = list(r) + [""] * (4 - len(r))
        m = _num(r[2])
        if r[0] and r[1] and m and m > 0:
            ini = _num(r[3]) or 0
            dettes.append({"d": str(r[0]), "c": str(r[1]), "m": int(round(m)), "ini": int(round(ini)) if ini >= m else int(round(m))})
    return joueurs, postes, pos, interdits, dettes


def plan_ombre():
    """Recalcule le plan depuis la feuille privée de PRODUCTION (lecture seule) et le compare à l'onglet « Convois » publié."""
    import ange_convois_plan as P
    sh = client().open_by_key(PRIVE_PROD)
    joueurs, postes, pos, interdits, dettes = lire_prive(sh)

    def dist(a, b):
        return __import__("math").hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]) if a in pos and b in pos else 999

    lignes, dettes2, couv = P.calcul_plan(joueurs, dist, interdits, dettes)
    mine = {(l["s"], l["r"]): l["q"] for l in lignes}
    ref = {}
    for r in sh.worksheet("Convois").get("A4:D400", value_render_option="UNFORMATTED_VALUE"):
        if len(r) >= 4 and str(r[1]).strip() and str(r[2]).strip() and _num(r[3]):
            k = (str(r[1]).strip(), str(r[2]).strip())
            ref[k] = ref.get(k, 0) + int(round(_num(r[3])))   # une ligne inter-chaînes est écrite deux fois : on dédoublonne ci-dessous
    # une paire écrite dans deux chaînes apparaît deux fois dans Convois : on la compte une fois
    ref1 = {}
    for r in sh.worksheet("Convois").get("A4:D400", value_render_option="UNFORMATTED_VALUE"):
        if len(r) >= 4 and str(r[1]).strip() and str(r[2]).strip() and _num(r[3]):
            ref1[(str(r[1]).strip(), str(r[2]).strip())] = int(round(_num(r[3])))
    ident = sum(1 for k in ref1 if mine.get(k) == ref1[k])
    out = [("joueurs lus", len(joueurs)), ("lignes calculées", len(mine)), ("lignes Convois (uniques)", len(ref1)),
           ("identiques", ident), ("absentes chez moi", sum(1 for k in ref1 if k not in mine)),
           ("en trop chez moi", sum(1 for k in mine if k not in ref1)),
           ("total calculé", sum(mine.values())), ("total Convois", sum(ref1.values())),
           ("couverture", round(couv, 4)), ("dettes après", len(dettes2))]
    for k in [k for k in ref1 if mine.get(k) != ref1[k]][:8]:
        out.append(("écart " + k[0] + ">" + k[1], "réf %s / moi %s" % (ref1[k], mine.get(k))))
    return out


# ---------------- TDC moyen de la veille (calculé depuis la base du VPS) ----------------
def bornes_jour_paris(jour):
    """(début, fin) en versions UTC AAAAMMJJHHmm pour le jour Paris 'AAAA-MM-JJ' (22h J-1 → 22h J)."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    d = datetime.strptime(jour, "%Y-%m-%d")
    prev = d - timedelta(days=1)
    deb = datetime(prev.year, prev.month, prev.day, 22, 0, tzinfo=ZoneInfo("Europe/Paris")).astimezone(timezone.utc)
    fin = datetime(d.year, d.month, d.day, 22, 0, tzinfo=ZoneInfo("Europe/Paris")).astimezone(timezone.utc)
    return deb.strftime("%Y%m%d%H00"), fin.strftime("%Y%m%d%H00")


def arrondi_js(x):
    """Math.round de JavaScript : .5 arrondi vers le haut (Python arrondit au pair)."""
    import math
    return int(math.floor(x + 0.5))


def moyenne_jour(conn, jour, alliance="ANGE"):
    """{pseudo: (moyenne arrondie, nb relevés)} sur les relevés du jour Paris où le joueur est dans l'alliance."""
    deb, fin = bornes_jour_paris(jour)
    rows = conn.execute("SELECT pseudo, AVG(tdc), COUNT(*) FROM tdc WHERE alliance=? AND version>=? AND version<? GROUP BY pseudo",
                        (alliance, deb, fin)).fetchall()
    return {p: (m, n) for p, m, n in rows}


def moyenne_ombre(jour=None):
    """Compare la moyenne calculée ici à l'onglet « TDC moyenne » de production (LECTURE SEULE)."""
    import sqlite3
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    sh = client().open_by_key(PRIVE_PROD)
    lu = sh.worksheet("TDC moyenne").get("A1:F4", value_render_option="UNFORMATTED_VALUE")
    jour = jour or str(lu[0][5]) if len(lu[0]) > 5 else jour
    conn = sqlite3.connect("file:" + os.path.join(DOSSIER, "tdc_serveur.db") + "?mode=ro", uri=True)
    try:
        calc = moyenne_jour(conn, jour)
    finally:
        conn.close()
    ref = {}
    for r in sh.worksheet("TDC moyenne").get("A2:C400", value_render_option="UNFORMATTED_VALUE"):
        if len(r) >= 3 and str(r[0]).strip():
            ref[str(r[0]).strip()] = (_num(r[1]), _num(r[2]))
    egal_arr = egal_tronq = 0
    ecarts = []
    for p, (m, n) in ref.items():
        if p not in calc:
            ecarts.append((p, "absent du calcul", ref[p][0]))
            continue
        cm, cn = calc[p]
        if arrondi_js(cm) == arrondi_js(m):
            egal_arr += 1
        elif int(cm) == int(m):
            egal_tronq += 1
        else:
            ecarts.append((p, "réf %s / moi %.1f" % (m, cm), "n %s/%s" % (n, cn)))
    out = [("jour", jour), ("joueurs dans l'onglet", len(ref)), ("joueurs calculés ici", len(calc)),
           ("identiques (arrondi)", egal_arr), ("identiques (tronqué seulement)", egal_tronq), ("écarts", len(ecarts)),
           ("calculés absents de l'onglet", len([p for p in calc if p not in ref]))]
    out += [("écart " + a, b + " " + str(c)) for a, b, c in ecarts[:8]]
    out += [("en plus ici: " + p, "") for p in [p for p in calc if p not in ref][:6]]
    return out


# ---------------- Clôture de nuit : répétition sur les COPIES TEST uniquement ----------------
def membres_chaine(sh):
    """Membres de la chaîne depuis « Chaîne unique » : [{pseudo, poste, g, hors, seul}]. Équivalent de lireChaines_."""
    import re
    import ange_convois_nuit as N
    v = sh.worksheet("Chaîne unique").get("A3:Y96", value_render_option="UNFORMATTED_VALUE")
    try:
        seul = {str(r[0]).strip().lower() for r in sh.worksheet("Attaque seulement").get("A2:A300") if r and str(r[0]).strip()}
    except Exception:
        seul = set()
    out = []
    for r in v:
        r = list(r) + [""] * (25 - len(r))
        g0, poste, pseudo = str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()
        if not (isinstance(r[0], (int, float)) or re.match(r"^\d+\.\d+$", g0)) or not pseudo or poste not in N.POSTES:
            continue
        out.append({"pseudo": pseudo, "poste": poste, "g": N.groupe(poste), "hors": r[24] is True,
                    "seul": pseudo.lower() in seul})
    return out


def lire_positions(sh):
    pos = {}
    for r in sh.worksheet("TDC live").get("A2:D400", value_render_option="UNFORMATTED_VALUE"):
        if len(r) >= 3 and str(r[0]).strip() and not isinstance(r[1], str) and not isinstance(r[2], str):
            pos[str(r[0]).strip()] = (r[1], r[2])
    return pos


DEBUG = {}


def avec_reprise(f, essais=4, pause=25):
    """Relance f() si Google répond « quota dépassé » (429) ; le quota de lectures est partagé avec le suivi ANGE."""
    import time
    for i in range(essais):
        try:
            return f()
        except Exception as e:
            if "429" in str(e) and i < essais - 1:
                time.sleep(pause)
                continue
            raise


def jour_texte(v):
    """Date d'une cellule (texte AAAA-MM-JJ, JJ/MM/AAAA ou numéro de série Sheets) -> « AAAA-MM-JJ » (« » si illisible)."""
    import re
    from datetime import datetime, timedelta
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (datetime(1899, 12, 30) + timedelta(days=int(v))).strftime("%Y-%m-%d")
    s = str(v).strip().lstrip("'")
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1))) if m else ""


def jour_plan_paris(now=None):
    """Jour pour lequel on calcule le plan (comme ange_convois_prod.jour_du_plan) : demain à partir de midi, aujourd'hui avant."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo("Europe/Paris"))
    return (now + timedelta(days=1 if now.hour >= 12 else 0)).strftime("%Y-%m-%d")


def exclus_nouveaux(lignes, jour_plan=None):
    """Pseudos au statut Nouveau pour le plan du jour : ligne de l'onglet « Nouveaux » [pseudo, poste, entré, dernier plan sans convoi] avec jour_plan <= dernier plan."""
    jp = jour_plan or jour_plan_paris()
    out = set()
    for r in lignes:
        r = list(r) + [""] * (4 - len(r))
        fin = jour_texte(r[3])
        if str(r[0]).strip() and fin and jp <= fin:
            out.add(str(r[0]).strip())
    return out


def charger_prive(sh):
    """TOUTES les lectures de la feuille privée en UNE requête. Retourne (joueurs, postes, pos, interdits, dettes, membres, posv)."""
    import re
    import ange_convois_nuit as N
    plages = ["'Chaîne unique'!A3:AH96", "Data!A2:C300", "Interdits!A2:B200", "Dettes!A2:D2000",
              "'Attaque seulement'!A2:A300", "'TDC live'!A2:D400", "Nouveaux!A2:D200"]

    def appel(pl):
        return sh.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchGet" % sh.id,
                                 params={"ranges": pl, "valueRenderOption": "UNFORMATTED_VALUE"}).json()["valueRanges"]
    try:
        vr = avec_reprise(lambda: appel(plages))
    except Exception as e:
        if "400" not in str(e):
            raise
        plages[6] = "'Chaîne unique'!A1:A1"           # onglet « Nouveaux » absent (copies TEST) : on le neutralise
        try:
            vr = avec_reprise(lambda: appel(plages))
        except Exception as e2:
            if "400" not in str(e2):
                raise
            plages[4] = "'Chaîne unique'!A1:A1"       # onglet « Attaque seulement » absent : on le neutralise aussi
            vr = avec_reprise(lambda: appel(plages))
    cu, data, inter, det, seul, live, nouv = [v.get("values", []) for v in vr]
    exclus = exclus_nouveaux(nouv) if plages[6].startswith("Nouveaux") else set()      # statut Nouveau : pas dans le calcul des convois
    DEBUG["plages"] = [(p, len(v.get("values", []))) for p, v in zip(plages, vr)]
    DEBUG["cu0"] = repr(cu[:1])[:260]
    joueurs, postes, membres = [], {}, []
    seuls = {str(r[0]).strip().lower() for r in seul if r and str(r[0]).strip()} if plages[4].startswith("'Attaque") else set()
    for r in cu:
        r = list(r) + [""] * (34 - len(r))
        g0, poste, pseudo = str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()
        d = _num(r[31])
        if pseudo and d is not None and d == d:
            joueurs.append({"pseudo": pseudo, "delta": int(round(d)), "livreur": poste in LIVREURS})
            postes[pseudo] = poste
        if (isinstance(r[0], (int, float)) or re.match(r"^\d+\.\d+$", g0)) and pseudo and poste in N.POSTES:   # « 2.1 » est stocké comme une date
            membres.append({"pseudo": pseudo, "poste": poste, "g": N.groupe(poste), "hors": r[24] is True, "seul": pseudo.lower() in seuls})
    pos = {}
    for r in data:
        if len(r) >= 3 and str(r[0]) != "":
            x, y = _num(r[1]), _num(r[2])
            if x is not None and y is not None:
                pos[str(r[0])] = (x, y)
    interdits = {(str(r[0]).strip(), str(r[1]).strip()) for r in inter if len(r) >= 2 and str(r[0]).strip() and str(r[1]).strip()}
    dettes = []
    for r in det:
        r = list(r) + [""] * (4 - len(r))
        m = _num(r[2])
        if r[0] and r[1] and m and m > 0:
            ini = _num(r[3]) or 0
            dettes.append({"d": str(r[0]), "c": str(r[1]), "m": int(round(m)), "ini": int(round(ini)) if ini >= m else int(round(m))})
    posv = {}
    for r in live:
        if len(r) >= 3 and str(r[0]).strip() and not isinstance(r[1], str) and not isinstance(r[2], str):
            posv[str(r[0]).strip()] = (r[1], r[2])
    joueurs = [j for j in joueurs if j["pseudo"] not in exclus]
    return joueurs, postes, pos, interdits, dettes, membres, posv


def lire_plages(ss, noms, rngs):
    """Lecture groupée de plusieurs plages (une seule requête) -> {nom: valeurs brutes}."""
    rep = ss.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchGet" % ss.id,
                            params={"ranges": rngs, "valueRenderOption": "UNFORMATTED_VALUE"})
    vr = rep.json()["valueRanges"]
    assert len(vr) == len(noms), "lecture groupée incomplète"
    return {p: v.get("values", []) for p, v in zip(noms, vr)}


def cloture_test(cle, jour_txt, ecrire):
    """Répète la clôture sur la copie TEST `cle` (greniers|passeurs|chasseurs). Plan recalculé depuis la copie privée TEST."""
    import ange_convois_plan as P
    import ange_convois_nuit as N
    from datetime import datetime
    prive = ouvrir_test("prive")
    ss = ouvrir_test(cle)
    g = {"greniers": "3.1", "passeurs": "3.2", "chasseurs": "3.3"}[cle]
    joueurs, postes, pos, interdits, dettes, membres, posv = charger_prive(prive)

    def dist(a, b):
        return __import__("math").hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]) if a in pos and b in pos else 999

    lignes, dettes2, couv = P.calcul_plan(joueurs, dist, interdits, dettes)
    lignes = N.trier_plan(lignes, dist)
    gde = {p: N.groupe(po) for p, po in postes.items()}
    plan_g = N.plan_par_fichier(lignes, gde)[g]
    titles = {ws.title: ws for ws in avec_reprise(lambda: ss.worksheets())}
    actifs = [m["pseudo"] for m in membres if m["g"] == g and (not m["hors"] or m["seul"]) and m["pseudo"] in titles]
    if not actifs:
        return [("ERREUR", "aucun onglet joueur trouvé"), ("membres lus", len(membres)), ("onglets du fichier", len(titles)),
                ("plages", repr(DEBUG.get("plages"))[:300]), ("1re ligne", DEBUG.get("cu0")), ("joueurs", len(joueurs)), ("exemple onglets", ", ".join(list(titles)[:6]))]
    rngs = ["'%s'!A9:J20" % p.replace("'", "''") for p in actifs]
    lus = avec_reprise(lambda: lire_plages(ss, actifs, rngs))
    jour_serie = (datetime.strptime(jour_txt, "%Y-%m-%d") - datetime(1899, 12, 30)).days
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
        rows = []
        for j in journal:
            rows.append({"values": [{"userEnteredValue": {"numberValue": j[0]}, "userEnteredFormat": fmt_date},
                                    {"userEnteredValue": {"stringValue": j[1]}}, {"userEnteredValue": {"stringValue": j[2]}}] +
                                   [{"userEnteredValue": {"numberValue": x}, "userEnteredFormat": fmt_nb} for x in j[3:8]]})
        reqs.append({"appendCells": {"sheetId": jr.id, "rows": rows, "fields": "userEnteredValue,userEnteredFormat.numberFormat"}})
    out = [("fichier", ss.title), ("onglets joueurs", len(actifs)), ("lignes de plan du fichier", len(plan_g)),
           ("lignes de journal", len(journal)), ("requêtes", len(reqs)),
           ("onglets avec trop de lignes", sum(1 for p in actifs if ecr[p]["trop"])),
           ("total à livrer écrit", sum(l["du"] for p in actifs for l in ecr[p]["lignes"])),
           ("total report (retards)", sum(l["retard"] for p in actifs for l in ecr[p]["lignes"] if l["retard"] > 0))]
    if ecrire:
        for i in range(0, len(reqs), 150):
            avec_reprise(lambda i=i: ss.batch_update({"requests": reqs[i:i + 150]}))
        out.append(("ÉCRIT", "oui, sur la copie TEST"))
    else:
        out.append(("ÉCRIT", "non (simulation)"))
    return out


def commande(prm):
    action = (prm.get("action") or "").lower()
    if action == "diag_ecriture":
        lignes = diag_ecriture(prm.get("fichier", "greniers"))
        return ["test", "résultat"], [list(x) for x in lignes]
    if action == "cloture_test":
        return ["contrôle", "valeur"], [list(x) for x in cloture_test(prm.get("fichier", "greniers"), prm.get("jour", "2026-11-12"),
                                                                     (prm.get("ecrire", "non").lower() == "oui"))]
    if action == "sync_test":
        import importlib, ange_convois_live
        importlib.reload(ange_convois_live)
        return ["contrôle", "valeur"], [list(x) for x in ange_convois_live.synchroniser(prm.get("fichier", "greniers"), (prm.get("ecrire", "non").lower() == "oui"))]
    if action == "sync_tout_test":
        import importlib, ange_convois_live
        importlib.reload(ange_convois_live)
        r = ange_convois_live.synchroniser_tout(None, (prm.get("ecrire", "non").lower() == "oui"))
        return ["contrôle", "valeur"], [[ph + "/" + cle + " " + k, str(v)] for (ph, cle), lg in r.items() for k, v in lg if k != "fichier"]
    if action == "ecrire_plan_test":
        import importlib, ange_convois_ecr
        importlib.reload(ange_convois_ecr)
        return ["contrôle", "valeur"], [list(x) for x in ange_convois_ecr.ecrire_tout(prm.get("jour"), (prm.get("ecrire", "non").lower() == "oui"))]
    if action == "comparer_plan":
        import importlib, ange_convois_ecr
        importlib.reload(ange_convois_ecr)
        return ["contrôle", "valeur"], [list(x) for x in ange_convois_ecr.comparer()]
    if action == "nuit_sim":       # simulation de l'enchaînement de nuit avec une FAUSSE horloge, sur les COPIES de test (stage=1|2|3)
        import importlib, json, ange_convois_prod as PR, ange_convois_ecr as E2
        from datetime import datetime
        from zoneinfo import ZoneInfo
        importlib.reload(E2)
        importlib.reload(PR)
        dos = DOSSIER
        PR.ETAT, PR.SANTE, PR.ARME = [os.path.join(dos, "sim_nuit_%s.json" % n) for n in ("etat", "sante", "arme")]
        stage = prm.get("stage", "1")
        if stage == "1":
            for f in (PR.ETAT, PR.SANTE):
                if os.path.exists(f):
                    os.remove(f)
            json.dump({"jour_plan": "2026-11-16", "permanent": True, "dettes": True}, open(PR.ARME, "w"))
        msgs = []
        PR.alerter = lambda t: msgs.append("DISCORD: " + t[:300])

        class Stub:
            def worksheet(self, n):
                class W:
                    def update(self_, range_name=None, values=None):
                        msgs.append("%s: %s" % (range_name, str(values[0][0])[:300]))
                return W()
        orig_lm, orig_vd = E2.lignes_moyenne, E2.veille_de
        E2.veille_de = lambda j: "2026-11-11"
        E2.lignes_moyenne = lambda conn, j: orig_lm(conn, "2026-11-11")
        PR.CIBLE_TEST = True
        try:
            horloges = {"1": [(2026, 11, 15, 23, 30), (2026, 11, 15, 23, 41)], "2": [(2026, 11, 16, 0, 0)], "3": [(2026, 11, 16, 0, 45), (2026, 11, 16, 1, 35)]}[stage]
            for h in horloges:
                PR.maintenant = lambda h=h: datetime(*h, tzinfo=ZoneInfo("Europe/Paris"))
                r = PR.tick(Stub())
                msgs.append("tick %02d:%02d -> %s" % (h[3], h[4], (str(r)[:700] if r else "rien")))
        finally:
            PR.CIBLE_TEST = False
            E2.lignes_moyenne, E2.veille_de = orig_lm, orig_vd
        msgs.append("état: " + json.dumps(PR.lire_json(PR.ETAT, {}), ensure_ascii=False)[:600])
        return ["constat"], [[m[:480]] for m in msgs]
    if action == "pages_comparer":  # morceau 1 de la migration de majChaines : listes d'attaque VPS vs Apps Script (lecture seule, cible=prod|test)
        import importlib, ange_convois_pages as PG, ange_convois_prod as PR
        importlib.reload(PG)
        importlib.reload(PR)
        PR.CIBLE_TEST = prm.get("cible", "prod") != "prod"
        try:
            jp = PR.jour_du_plan()
            prive = PR.ouvrir_prod("prive", jp)
            fich = {cle: PR.ouvrir_prod(cle, jp) for cle in PR.FICHIERS}
            ctx = PG.lire_contexte(prive)
            try:
                col = PG.lire_colonisation_api()
            except Exception as ex:
                col = None
                DEBUG["col"] = str(ex)[:150]
            resume, diffs = PG.comparer(prive, fich, ctx, col, prm.get("cible", "prod") == "prod")
        finally:
            PR.CIBLE_TEST = False
        lignes = [[k, str(v)] for k, v in resume.items()] + [["diff", d[:450]] for d in diffs[:25]]
        if DEBUG.get("col"):
            lignes.append(["erreur API", DEBUG["col"]])
        return ["contrôle", "valeur"], lignes
    if action == "pages_ecrire":   # écrit les listes d'attaque (cible=test par défaut ; cible=prod seulement si demandé)
        import importlib, ange_convois_pages as PG, ange_convois_prod as PR
        importlib.reload(PG)
        importlib.reload(PR)
        PR.CIBLE_TEST = prm.get("cible", "test") != "prod"
        try:
            jp = PR.jour_du_plan()
            prive = PR.ouvrir_prod("prive", jp)
            fich = {cle: PR.ouvrir_prod(cle, jp) for cle in PR.FICHIERS}
            ctx = PG.lire_contexte(prive)
            try:
                col = PG.lire_colonisation_api()
            except Exception:
                col = None
            if not col and prm.get("cible", "test") == "prod":
                raise RuntimeError("API colonisation indisponible : pas d'écriture en prod")
            bilan = PG.ecrire_listes(prive, fich, ctx, col, os.path.join(DOSSIER, "sauvegardes_convois"))
        finally:
            PR.CIBLE_TEST = False
        return ["fichier", "onglets écrits"], [[k, str(v)] for k, v in bilan.items()]
    if action in ("entetes_comparer", "entetes_ecrire"):   # morceau 2 : en-tête des onglets joueurs (cible=prod|test)
        import importlib, ange_convois_pages as PG, ange_convois_entetes as EN, ange_convois_prod as PR
        for mod in (PG, EN, PR):
            importlib.reload(mod)
        PR.CIBLE_TEST = prm.get("cible", "test" if action == "entetes_ecrire" else "prod") != "prod"
        try:
            jp = PR.jour_du_plan()
            prive = PR.ouvrir_prod("prive", jp)
            fich = {cle: PR.ouvrir_prod(cle, jp) for cle in PR.FICHIERS}
            try:
                col = PG.lire_colonisation_api()
            except Exception:
                col = None
            if action == "entetes_ecrire":
                bilan = EN.ecrire(prive, fich, col, os.path.join(DOSSIER, "sauvegardes_convois"))
                return ["fichier", "onglets écrits"], [[k, str(v)] for k, v in bilan.items()]
            resume, diffs = EN.comparer(prive, fich, col, prm.get("cible", "prod") == "prod" and prm.get("tout") != "oui")
            return ["contrôle", "valeur"], [[k, str(v)] for k, v in resume.items()] + [["diff", d[:450]] for d in diffs[:25]]
        finally:
            PR.CIBLE_TEST = False
    if action in ("annexes_comparer", "annexes_ecrire"):   # morceau 3 : distances des convois, Suivi, Ma chaîne
        import importlib, ange_convois_pages as PG, ange_convois_entetes as EN, ange_convois_annexes as AN, ange_convois_prod as PR
        for mod in (PG, EN, AN, PR):
            importlib.reload(mod)
        PR.CIBLE_TEST = prm.get("cible", "test" if action == "annexes_ecrire" else "prod") != "prod"
        try:
            jp = PR.jour_du_plan()
            prive = PR.ouvrir_prod("prive", jp)
            fich = {cle: PR.ouvrir_prod(cle, jp) for cle in PR.FICHIERS}
            try:
                col = PG.lire_colonisation_api()
            except Exception:
                col = None
            if action == "annexes_ecrire":
                bilan = AN.ecrire(prive, fich, col, os.path.join(DOSSIER, "sauvegardes_convois"))
                return ["fichier", "écrit"], [[k, str(v)] for k, v in bilan.items()]
            resume, diffs = AN.comparer(prive, fich, col)
            return ["contrôle", "valeur"], [[k, str(v)] for k, v in resume.items()] + [["diff", d[:450]] for d in diffs[:25]]
        finally:
            PR.CIBLE_TEST = False
    if action in ("onglets_comparer", "onglets_ecrire"):   # ordre + couleur des onglets (comparer = lecture seule sur la prod ; ecrire = copies TEST)
        import importlib, ange_convois_pages as PG, ange_convois_onglets as ON, ange_convois_prod as PR
        for mod in (PG, ON, PR):
            importlib.reload(mod)
        ecrire = action == "onglets_ecrire"
        PR.CIBLE_TEST = ecrire
        try:
            jp = PR.jour_du_plan()
            prive = PR.ouvrir_prod("prive", jp)
            fich = {cle: PR.ouvrir_prod(cle, jp) for cle in PR.FICHIERS}
            ctx = PG.lire_contexte(prive)
            bilan = ON.appliquer(fich, ctx, ecrire)
            return ["fichier", "résultat" if ecrire else "écart avec l'ordre voulu"], [[k, str(v)] for k, v in bilan.items()]
        finally:
            PR.CIBLE_TEST = False
    if action in ("alertes_comparer", "alertes_ecrire"):   # onglets Alertes + Historique du sheet privé (cible=test par défaut pour l'écriture)
        import importlib, ange_convois_pages as PG, ange_convois_alertes as AL, ange_convois_prod as PR
        for mod in (PG, AL, PR):
            importlib.reload(mod)
        PR.CIBLE_TEST = prm.get("cible", "test" if action == "alertes_ecrire" else "prod") != "prod"
        try:
            prive = PR.ouvrir_prod("prive", PR.jour_du_plan())
            ctx = PG.lire_contexte(prive)
            try:
                col = PG.lire_colonisation_api()
            except Exception:
                col = None
            if action == "alertes_ecrire":
                bilan = AL.ecrire(prive, ctx, col, os.path.join(DOSSIER, "sauvegardes_convois"), PR.CIBLE_TEST)
                return ["bloc", "résultat"], [[k, str(v)] for k, v in bilan.items()]
            res = AL.comparer(prive, ctx, col)
            return ["contrôle", "valeur"], [[k, str(v)[:450]] for k, v in res.items()]
        finally:
            PR.CIBLE_TEST = False
    if action in ("controle_comparer", "controle_ecrire"):   # contrôle « à envoyer = à recevoir » (cible=prod en lecture ; l'écriture n'écrit que l'onglet Contrôle convois)
        import importlib, ange_convois_pages as PG, ange_convois_controle as KC, ange_convois_prod as PR
        for mod in (PG, KC, PR):
            importlib.reload(mod)
        PR.CIBLE_TEST = prm.get("cible", "test" if action == "controle_ecrire" else "prod") != "prod"
        try:
            jp = PR.jour_du_plan()
            prive = PR.ouvrir_prod("prive", jp)
            fich = {cle: PR.ouvrir_prod(cle, jp) for cle in PR.FICHIERS}
            ctx = PG.lire_contexte(prive)
            if action == "controle_ecrire":
                bilan = KC.controler(prive, fich, ctx, os.path.join(DOSSIER, "sauvegardes_convois"), True, None)
                return ["bloc", "résultat"], [[k, str(v)] for k, v in bilan.items()]
            actifs = {}
            for m in ctx["membres"]:
                if not m["hors"] or m["attaqueSeule"]:
                    actifs.setdefault(m["g"], []).append(m["pseudo"])
            liste = KC.ecarts(KC.lire_onglets(fich, actifs))
            return ["contrôle", "valeur"], [["écarts", str(len(liste))]] + [["écart", str(l)[:450]] for l in liste[:25]]
        finally:
            PR.CIBLE_TEST = False
    if action in ("tdclive_mode", "tdclive_comparer", "tdclive_ecrire"):   # port de majTdcLive : mode off|test|prod ; comparaison lecture seule avec la prod ; écriture sur la copie test
        import importlib, ange_convois_tdclive as TL, ange_convois_prod as PR
        importlib.reload(TL)
        if action == "tdclive_mode":
            v = prm.get("valeur", "").lower()
            if v in ("off", "test", "prod"):
                open(TL.MODE_FICHIER, "w", encoding="utf-8").write(v)
            return ["tdclive_mode"], [[TL.mode_tdclive()]]
        v = TL.derniere_version()
        lg = TL.lignes(TL.joueurs_alliance(v))
        PR.CIBLE_TEST = (action == "tdclive_ecrire") or prm.get("cible", "prod") != "prod"
        try:
            prive = PR.ouvrir_prod("prive", PR.jour_du_plan())
            if action == "tdclive_ecrire":
                sid = {w.title: w.id for w in avec_reprise(lambda: prive.worksheets())}["TDC live"]
                avec_reprise(lambda: prive.batch_update({"requests": TL.requetes(sid, lg, v)}), essais=3, pause=20)
                return ["version", "joueurs"], [[v, str(len(lg))]]
            ex = lire_plages(prive, ["a", "k"], ["'TDC live'!A2:E400", "'TDC live'!K1:K3"])
        finally:
            PR.CIBLE_TEST = False
        att = {str(l[0]): l[1:4] for l in lg}
        reel = {str(r[0]): [r[1] if len(r) > 1 else "", r[2] if len(r) > 2 else "", r[3] if len(r) > 3 else ""] for r in ex["a"] if r}
        nd = [p for p in set(att) | set(reel) if [str(x) for x in att.get(p, [])] != [str(x) for x in reel.get(p, [])]]
        sorted_ok = [str(r[0]) for r in ex["a"] if r] == [l[0] for l in lg]
        return ["contrôle", "valeur"], [["version API", v], ["joueurs API", str(len(lg))], ["joueurs feuille", str(len(reel))],
                                          ["lignes différentes", str(len(nd))], ["même ordre", str(sorted_ok)], ["version feuille K2", str((ex["k"] + [[], [], []])[1][:1])],
                                          ["exemples", ", ".join(nd[:8])]]
    if action in ("forms_mode", "forms_verifier", "forms_maj"):   # liste « Pseudo » des 3 formulaires : off|prod ; vérification (lecture seule) ; mise à jour immédiate
        import importlib, ange_convois_forms as FM, ange_convois_prod as PR
        importlib.reload(FM)
        if action == "forms_mode":
            v = prm.get("valeur", "").lower()
            if v in ("off", "prod"):
                open(FM.MODE_FICHIER, "w", encoding="utf-8").write(v)
            return ["forms_mode"], [[FM.mode_forms()]]
        prive = PR.ouvrir_prod("prive", PR.jour_du_plan())
        if action == "forms_verifier":
            return ["formulaire", "état"], FM.verifier(prive)
        return ["formulaire", "mis à jour"], [[m] for m in FM.mettre_a_jour(prive, prm.get("forcer") == "oui")] or [["rien à changer"]]
    if action in ("reponses_mode", "reponses_comparer"):   # port de traiterReponsePoste : off | observe | prod ; comparaison de ce qui a été prévu avec le Journal formulaire d'Apps Script
        import importlib, json as _j, ange_convois_reponses as RP, ange_convois_prod as PR
        importlib.reload(RP)
        if action == "reponses_mode":
            v = prm.get("valeur", "").lower()
            if v in ("off", "observe", "prod"):
                open(RP.MODE_FICHIER, "w", encoding="utf-8").write(v)
                if v == "off":
                    pass
            return ["reponses_mode"], [[RP.mode_reponses()]]
        if prm.get("test") == "api":     # vérifie l'accès Forms (réponses des dernières 24 h, lecture seule)
            from datetime import datetime as _dt, timedelta as _td
            prive = PR.ouvrir_prod("prive", PR.jour_du_plan())
            ses = RP.session()
            dep = (_dt.utcnow() - _td(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            lg = []
            for fam, fid in RP.ids_formulaires(prive).items():
                try:
                    lg.append([fam, str(len(RP.reponses_depuis(ses, fid, dep))) + " réponse(s) en 24 h"])
                except Exception as e:
                    lg.append([fam, "ERREUR " + str(e)[:300]])
            return ["formulaire", "accès"], lg
        if prm.get("test") == "rejouer":     # rejoue les réponses des dernières 24 h (lecture seule) et compare avec ce qu'Apps Script a noté dans le Journal formulaire
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            prive = PR.ouvrir_prod("prive", PR.jour_du_plan())
            ses = RP.session()
            dep = (_dt.utcnow() - _td(hours=int(prm.get("heures", "24")))).strftime("%Y-%m-%dT%H:%M:%SZ")
            tabs, dp, entetes, dem = RP.lire_contexte(prive)
            jr = lire_plages(prive, ["j"], ["'Journal formulaire'!A2:D5000"])["j"]
            jr = [(((_dt(1899, 12, 30) + _td(days=float(r[0]))).replace(tzinfo=RP.PARIS)) if isinstance(r[0], (int, float)) else None, r) for r in jr if len(r) > 3]
            lignes = []
            for fam, fid in RP.ids_formulaires(prive).items():
                for x in RP.reponses_depuis(ses, fid, dep):
                    rep = x["rep"]
                    if not rep.get(RP.T_PSEUDO):
                        continue
                    t = _dt.strptime(x["t"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_tz.utc).astimezone(RP.PARIS)
                    res = RP.decider(fam, rep, tabs, dp, entetes, dem)
                    vu = [r for d, r in jr if d and abs((d - t).total_seconds()) < 240 and str(r[1]).strip().lower() == rep[RP.T_PSEUDO].strip().lower()]
                    as_ = vu[-1][3] if vu else "(pas de ligne AS)"
                    ok = (as_ == res["action"]) or (res["action"].startswith("Data mis à jour") and str(as_).startswith("Data mis à jour") and as_.split("(")[1:] == res["action"].split("(")[1:])
                    lignes.append([t.strftime("%d/%m %H:%M"), rep[RP.T_PSEUDO], fam, "OK" if ok else "DIFFÉRENT", res["action"], str(as_)])
            return ["quand", "pseudo", "famille", "verdict", "VPS prévoit", "AS a fait"], lignes or [["aucune réponse", "", "", "", "", ""]]
        try:
            obs = _j.load(open(RP.OBS_FICHIER))
        except Exception:
            obs = []
        prive = PR.ouvrir_prod("prive", PR.jour_du_plan())
        jr = lire_plages(prive, ["j"], ["'Journal formulaire'!A2:D3000"])["j"]
        lignes = []
        for o in obs[-30:]:
            vu = [r for r in jr if len(r) > 3 and str(r[1]).strip().lower() == o["pseudo"].strip().lower()]
            dernier = vu[-1][3] if vu else "(absent du journal AS)"
            lignes.append([o["quand"], o["pseudo"], o["fam"], "OK" if dernier == o["action"] else "DIFFÉRENT", o["action"], str(dernier)])
        return ["quand", "pseudo", "famille", "verdict", "VPS prévoit", "AS a fait"], lignes or [["aucune réponse observée", "", "", "", "", ""]]
    if action == "pages_mode":     # convois_pages_mode.txt : off | test | prod (exécution horaire des listes d'attaque)
        v = prm.get("valeur", "").lower()
        if v in ("off", "test", "prod"):
            open(os.path.join(DOSSIER, "convois_pages_mode.txt"), "w", encoding="utf-8").write(v)
        try:
            cur = open(os.path.join(DOSSIER, "convois_pages_mode.txt"), encoding="utf-8").read().strip()
        except OSError:
            cur = "off"
        return ["pages_mode"], [[cur]]
    if action == "mode":           # convois_mode.txt : test | prod | off  (prod = changements de chaîne immédiats en production)
        v = prm.get("valeur", "").lower()
        if v in ("test", "prod", "off"):
            open(os.path.join(DOSSIER, "convois_mode.txt"), "w", encoding="utf-8").write(v)
        try:
            actuel = open(os.path.join(DOSSIER, "convois_mode.txt"), encoding="utf-8").read().strip()
        except OSError:
            actuel = "(absent = test)"
        return ["contrôle", "valeur"], [["mode", actuel]]
    if action == "veille_blanc":   # ce que la veille de PRODUCTION ferait maintenant (simulation, rien d'écrit)
        import importlib, ange_convois_live as L2, ange_convois_prod as PR
        importlib.reload(L2)
        importlib.reload(PR)
        L2.CIBLE = "prod"
        try:
            res = L2.synchroniser_tout(PR.ouvrir_prod("prive", PR.jour_du_plan()), False)
        finally:
            L2.CIBLE = "test"
        lignes = []
        for (ph, cle), r in res.items():
            lignes.append(["%s/%s" % (ph, cle), " ; ".join("%s=%s" % (k, v) for k, v in r if k != "fichier")[:450]])
        return ["phase/fichier", "actions prévues"], lignes
    if action == "discord_test":    # envoie un message de test sur le Discord configuré (sans afficher l'adresse)
        import ange_suivi
        hook = ange_suivi.lire_ini()[1]
        if not hook:
            return ["résultat"], [["PAS DE WEBHOOK : renseigner [discord] webhook dans ange_watch.ini"]]
        ange_suivi.discord(hook, "✅ Test : les alertes de la clôture de minuit arrivent bien ici.")
        return ["résultat"], [["message envoyé (webhook renseigné)"]]
    if action == "coherence":      # contrôle à envoyer = à recevoir, LECTURE SEULE (cible=prod|test)
        import importlib, ange_convois_prod as PR
        importlib.reload(PR)
        PR.CIBLE_TEST = prm.get("cible", "test") != "prod"
        try:
            jp = prm.get("jour_plan", PR.jour_du_plan())
            prive = PR.ouvrir_prod("prive", jp)
            fich = {cle: PR.ouvrir_prod(cle, jp) for cle in PR.FICHIERS}
            membres = charger_prive(prive)[5]
            ec = PR.controle_coherence(prive, fich, membres, False)
        finally:
            PR.CIBLE_TEST = False
        return ["expéditeur", "destinataire", "envoie", "reçoit", "écart", "fichier exp.", "fichier dest."], [[str(x) for x in e] for e in ec[:40]] or [["aucun écart"]]
    if action == "prod_repet":     # répétition générale de la clôture de minuit sur les COPIES de test (écrit pour de vrai sur les copies)
        import importlib, ange_convois_prod as PR, ange_convois_ecr as E2
        importlib.reload(E2)
        importlib.reload(PR)
        PR.CIBLE_TEST = True
        ancien = E2.veille_de
        try:
            if prm.get("moyenne"):
                E2.veille_de = lambda j: prm["moyenne"]
            sortie = PR.executer(prm["jour_plan"], prm.get("ecrire", "oui") == "oui")
        finally:
            E2.veille_de = ancien
            PR.CIBLE_TEST = False
        return ["constat"], [[x[:480]] for x in sortie]
    if action in ("prod_armer", "prod_blanc", "prod_etat", "prod_desarmer"):
        import importlib, json, ange_convois_prod as PR
        importlib.reload(PR)
        if action == "prod_armer":
            json.dump({"jour_plan": prm["jour_plan"], "permanent": prm.get("permanent", "non").lower() == "oui", "dettes": prm.get("dettes", "non").lower() == "oui", "le": PR.maintenant().isoformat(timespec="seconds")},
                      open(PR.ARME, "w", encoding="utf-8"))
            return ["contrôle", "valeur"], [["armé pour", prm["jour_plan"]], ["permanent", prm.get("permanent", "non")], ["dettes écrites par le VPS", prm.get("dettes", "non")]]
        if action == "prod_desarmer":
            if os.path.exists(PR.ARME):
                os.remove(PR.ARME)
            return ["contrôle", "valeur"], [["armement", "retiré"]]
        if action == "prod_etat":
            return ["contrôle", "valeur"], [["armement", json.dumps(PR.lire_json(PR.ARME, None))], ["état", json.dumps(PR.lire_json(PR.ETAT, {}), ensure_ascii=False)[:2500]]]
        ok, c = PR.preflight(prm["jour_plan"])
        return ["contrôle", "valeur"], [["TOUT BON" if ok else "ÉCHEC", ""]] + [[x, ""] for x in c]
    if action == "plan_prod_sim":     # plan de PRODUCTION calculé sans rien écrire ; pseudo=… : lignes de ce joueur dans le plan (test du statut Nouveau)
        import importlib, ange_convois_ecr, ange_convois_prod as PR
        importlib.reload(ange_convois_ecr)
        prive = PR.ouvrir_prod("prive", PR.jour_du_plan())
        jp = PR.jour_du_plan()
        res = ange_convois_ecr.ecrire_tout(jp, False, prive, False)
        j, _p, _po, _i, _d, membres, _pv = charger_prive(prive)
        exclus = exclus_nouveaux(avec_reprise(lambda: lire_plages(prive, ["nv"], ["Nouveaux!A2:D200"]))["nv"], jp)
        out = [["jour du plan", jp], ["statut Nouveau (exclus du calcul)", ", ".join(sorted(exclus)) or "-"], ["joueurs dans le calcul", len(j)]]
        out += [list(x) for x in res]
        p = (prm.get("pseudo") or "").strip()
        if p:
            ls = [l for l in (ange_convois_ecr.DERNIER or {}).get("lignes", []) if l["s"] == p or l["r"] == p]
            out.append(["lignes du plan pour " + p, len(ls)])
            out.append(["%s dans le calcul" % p, "oui" if any(x["pseudo"] == p for x in j) else "non"])
            out.append(["%s dans la chaîne" % p, "oui" if any(m["pseudo"] == p for m in membres) else "non"])
        return ["contrôle", "valeur"], out
    if action == "versions":
        import gspread
        return ["module", "version"], [["gspread", gspread.__version__]]
    if action == "moyenne_ombre":
        return ["contrôle", "valeur"], [list(x) for x in moyenne_ombre(prm.get("jour"))]
    if action == "plan_ombre":
        return ["contrôle", "valeur"], [list(x) for x in plan_ombre()]
    return ["aide"], [["ANALYSE CONVOIS action=diag_ecriture fichier=greniers|passeurs|chasseurs  (copies TEST uniquement)"]]


def AUTOTEST():
    assert jour_texte(46340) == "2026-11-14" and jour_texte("'2026-11-14") == "2026-11-14" and jour_texte("14/11/2026") == "2026-11-14" and jour_texte("") == ""
    nv = [["Morb", "Passeur 5", "2026-11-13", "2026-11-14"], ["Old", "Passeur 1", "2026-11-01", "2026-11-02"], ["", "", "", ""]]
    assert exclus_nouveaux(nv, "2026-11-14") == {"Morb"} and exclus_nouveaux(nv, "2026-11-13") == {"Morb"} and exclus_nouveaux(nv, "2026-11-15") == set()
    assert jour_plan_paris(__import__("datetime").datetime(2026, 11, 13, 13, 0)) == "2026-11-14" and jour_plan_paris(__import__("datetime").datetime(2026, 11, 14, 0, 30)) == "2026-11-14"
    try:
        import ange_convois_nuit
    except ImportError:      # module pas encore installé par la synchro : on ne bloque pas
        ange_convois_nuit = None
    if ange_convois_nuit:
        ange_convois_nuit.AUTOTEST()
    try:
        import ange_convois_live
        ange_convois_live.AUTOTEST()
    except ImportError:
        pass
    try:
        import ange_convois_tick
        ange_convois_tick.AUTOTEST()
    except ImportError:
        pass
    try:
        import ange_convois_ecr
        ange_convois_ecr.AUTOTEST()
    except ImportError:
        pass
    try:
        import ange_convois_prod
        ange_convois_prod.AUTOTEST()
    except ImportError:
        pass
    try:
        import ange_convois_pages
        ange_convois_pages.AUTOTEST()
    except ImportError:
        pass
    try:
        import ange_convois_entetes
        ange_convois_entetes.AUTOTEST()
        import ange_convois_annexes
        ange_convois_annexes.AUTOTEST()
        import ange_convois_tdclive
        ange_convois_tdclive.AUTOTEST()
        import ange_convois_forms
        ange_convois_forms.AUTOTEST()
    except ImportError:
        pass
    assert PROD_INTERDIT.isdisjoint(TEST.values())
    c, l = commande({})
    assert c and l

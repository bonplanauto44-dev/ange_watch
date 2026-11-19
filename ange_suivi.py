"""ange_suivi.py : suivi automatique d'ANGE à partir de la base tdc_serveur.db (versions horaires de l'API, en UTC).

Appelé par le service ange_requete (tick) à chaque nouvelle version horaire, et chaque matin à 08h (Paris) pour le récap.
Écrit dans le Google Sheet « Suivi ANGE » les onglets :
  Chasse            chasse par chasseur et par jour (hausses de TDC, corrigées des attaques subies dans la même heure) + total ANGE net
  Chasse horaire    à quelles heures (Paris) chaque chasseur chasse
  Fuites            les pertes d'un membre ANGE dont un joueur HORS ANGE gagne exactement le même montant la même heure (plage 50-150 %)
  Attaquants        classement des joueurs / alliances qui nous prennent du TDC
  Activité          par membre (hors chasseurs) : attaques/jour, heures de jeu, jours sous la moyenne du poste, retards de convois, score
  Classement        maillons forts et faibles
  Récap             résumé du jour (envoyé aussi sur Discord)
Règles d'Arno : le TDC d'un membre ne peut aller qu'à un autre membre ; la chasse crée du TDC (hausse de chasseur) ; un chasseur n'attaque
personne ; le 31/10/2026 (heure de Paris, passage à la chaîne unique) ne compte pas dans les relevés.
"""
import collections, configparser, json, os, sqlite3, statistics, urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover
    PARIS = None

DOSSIER = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DOSSIER, "tdc_serveur.db")
ETAT = os.path.join(DOSSIER, "suivi_etat.json")
PRIVE_ID = "1WSkmn_I44Ugn7Koq9bKqzP9KH1ucBF4-xsrZj7q35tQ"       # sheet privé (onglets « Chaîne unique » et « Alertes »), lecture seule
CHASSEURS_DEFAUT = "Cylo, Fannou, Fhezz, FourmiDiable, Judalos, Kalipzio, LaVague, Scooby-Doo, Shaka, Tatsuo, Zorka, mousse85, willo"
PERIODES_EXCLUES = (("202610302300", "202610312300"),)          # 31/10 Paris, en UTC
FENETRE_JOURS = 14
JOURS_NOTE = 7
HEURE_RECAP = 8


def exclu(v):
    return any(a <= v < b for a, b in PERIODES_EXCLUES)


def paris(v):
    dt = datetime.strptime(v, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    return dt.astimezone(PARIS) if PARIS else dt + timedelta(hours=1)


def lire_ini():
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    c.read(os.path.join(DOSSIER, "ange_watch.ini"), encoding="utf-8")
    ch = c.get("general", "chasseurs", fallback=CHASSEURS_DEFAUT)
    return {x.strip().lower() for x in ch.split(",") if x.strip()}, c.get("discord", "webhook", fallback="").strip()


def discord(webhook, texte):
    if not webhook:
        return
    try:
        req = urllib.request.Request(webhook, data=json.dumps({"content": texte[:1900]}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "ange-suivi"})
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print("discord:", e, flush=True)


# ---------------------------------------------------------------- calcul
def charger(conn):
    last = conn.execute("SELECT MAX(version) FROM tdc").fetchone()[0]
    if not last:
        return [], {}
    debut = (datetime.strptime(last, "%Y%m%d%H%M") - timedelta(days=FENETRE_JOURS)).strftime("%Y%m%d%H%M")
    versions, data = [], {}
    for v, p, a, t in conn.execute("SELECT version, pseudo, alliance, tdc FROM tdc WHERE version >= ? ORDER BY version", (debut,)):
        if v not in data:
            data[v] = {}
            versions.append(v)
        data[v][p] = (a or "", t)
    return versions, data


SEUIL_CHASSE = 0.8   # une hausse de chasseur sous 80 % de sa chasse habituelle = chasse amputée d'une perte dans la même heure
FENETRE_CHASSE_H = 36
AGE_MAX_ALERTE_H = 4     # une alerte Discord n'est envoyée que pour un événement de moins de 4 h


def estimer_chasse(hausses):
    """hausses : [(version AAAAMMJJHHmm, hausse>0)] d'UN chasseur, triées.
    Une chasse habituelle est lisse d'un retour à l'autre (testé sur FourmiDiable : écart de 1 à 4 %). La référence locale R est la médiane des
    hausses voisines (±36 h). Si une hausse est nettement sous R, le chasseur a été attaqué dans la même heure : chasse = R, perte = R - hausse.
    Retourne {version: (chasse estimée, perte estimée)}."""
    t = lambda v: datetime.strptime(v, "%Y%m%d%H%M")
    ok = list(hausses)
    res = {}
    for _ in range(4):
        res, garde = {}, []
        for v, d in hausses:
            vois = sorted(x for w, x in ok if w != v and abs((t(w) - t(v)).total_seconds()) <= FENETRE_CHASSE_H * 3600)
            if len(vois) < 2:
                res[v] = (d, 0)
                garde.append((v, d))
                continue
            n = len(vois)
            r = vois[n // 2] if n % 2 else (vois[n // 2 - 1] + vois[n // 2]) / 2
            if d < SEUIL_CHASSE * r:
                res[v] = (round(r), round(r - d))
            else:
                res[v] = (d, 0)
                garde.append((v, d))
        ok = garde
    return res


def calculer(versions, data, chasseurs):
    R = types_vides()
    ch_hausses = collections.defaultdict(list)    # chasseur -> [(version, jour, heure, hausse)]
    for v0, v1 in zip(versions, versions[1:]):
        if exclu(v0) or exclu(v1):
            continue
        A, B = data[v0], data[v1]
        pp = paris(v1)
        j, h = pp.strftime("%Y-%m-%d"), pp.hour
        pertes, gains_out, net = [], collections.defaultdict(list), 0
        for p, (al1, t1) in B.items():
            a0 = A.get(p)
            if a0 is None:
                continue
            al0, t0 = a0
            d = t1 - t0
            if al0 == "ANGE" and al1 == "ANGE":
                net += d
                R["tdc_jour"][p][j].append(t1)
                if p.lower() in chasseurs:
                    if d > 0:
                        ch_hausses[p].append((v1, j, h, d))
                else:
                    if d > 0:
                        R["att"][p][j] += 1
                        R["att_h"][p][h] += 1
                if d <= -1000:
                    pertes.append((p, -d, t0))
            elif d >= 1000 and al0 != "ANGE" and al1 != "ANGE":
                gains_out[d].append((p, al1, t0))
        R["net_jour"][j] += net
        R["net_h"].append((v1, net))
        for p, m, t0 in pertes:
            cand = [c for c in gains_out.get(m, []) if 0.5 * t0 <= c[2] <= 1.5 * t0]
            if len(cand) != 1:
                continue
            a, al, ta = cand[0]
            niveau = "SÛRE" if m % 100 else "FAIBLE (montant rond)"
            R["preuves"].append((v1, p, m, t0, a, al, ta, niveau))
    # chasse estimée : hausse observée + perte subie dans la même heure (voir estimer_chasse)
    for p, lst in ch_hausses.items():
        est = estimer_chasse([(v, d) for v, _, _, d in lst])
        for v, j, h, d in lst:
            c, perte = est[v]
            R["chasse"][p][j] += c
            R["chasse_h"][p][h] += c
            R["chasse_brute"][p][j] += d
            R["chasse_pertes"][(v, p.lower())] = (c, perte)
    return R


def types_vides():
    return {"chasse": collections.defaultdict(lambda: collections.defaultdict(int)),
            "chasse_h": collections.defaultdict(lambda: [0] * 24),
            "att": collections.defaultdict(lambda: collections.defaultdict(int)),
            "att_h": collections.defaultdict(lambda: [0] * 24),
            "tdc_jour": collections.defaultdict(lambda: collections.defaultdict(list)),
            "net_jour": collections.defaultdict(int), "net_h": [], "preuves": [],
            "chasse_brute": collections.defaultdict(lambda: collections.defaultdict(int)), "chasse_pertes": {},
            "chasse_rep": collections.defaultdict(lambda: collections.defaultdict(int))}


def jours_complets(R, aujourdhui):
    tous = sorted({j for d in R["att"].values() for j in d} | set(R["net_jour"]))
    return [j for j in tous if j < aujourdhui][-JOURS_NOTE:]


def evaluer(R, postes, retards, chasseurs, aujourdhui):
    """Score par membre (hors chasseurs). Retourne la liste de dicts."""
    jours = jours_complets(R, aujourdhui)
    membres = [p for p in R["tdc_jour"] if p.lower() not in chasseurs
               and (not postes or postes.get(p.lower(), "?") not in ("?", "Hors chaîne"))]   # seuls les joueurs de la chaîne sont notés
    RODAGE[:] = sorted((p, postes.get(p.lower(), "?"), RECENTS[p.lower()]) for p in membres if p.lower() in RECENTS)
    membres = [p for p in membres if p.lower() not in RECENTS]   # nouveaux / changement de rang : 7 jours de rodage, pas notés
    moy_att = {p: (sum(R["att"][p].get(j, 0) for j in jours) / max(len(jours), 1)) for p in membres}
    moy_tdc = {p: {j: statistics.mean(R["tdc_jour"][p][j]) for j in jours if R["tdc_jour"][p].get(j)} for p in membres}
    par_poste = collections.defaultdict(list)
    for p in membres:
        par_poste[postes.get(p.lower(), "?")].append(p)
    med_att = {k: statistics.median(moy_att[p] for p in v) for k, v in par_poste.items()}
    med_tdc = {}
    for k, v in par_poste.items():
        for j in jours:
            xs = [moy_tdc[p][j] for p in v if j in moy_tdc[p]]
            if xs:
                med_tdc[(k, j)] = statistics.median(xs)
    out = []
    for p in membres:
        poste = postes.get(p.lower(), "?")
        grenier = poste.lower().startswith("grenier")
        bas = 0
        for j in jours:
            m = med_tdc.get((poste, j))
            if m and len(par_poste[poste]) >= 3 and j in moy_tdc[p] and moy_tdc[p][j] < 0.8 * m:
                bas += 1
        ratio = (moy_att[p] / med_att[poste]) if med_att.get(poste) else 1.0
        ratio = max(0.0, min(2.0, ratio))
        r = retards.get(p.lower(), 0)
        rp = [moy_tdc[p][j] / med_tdc[(poste, j)] for j in jours if j in moy_tdc[p] and med_tdc.get((poste, j))]
        pos = statistics.mean(rp) if rp else 1.0           # TDC moyen par rapport à la médiane du même poste
        facteur = 0.3 if pos >= 1.2 else (0.6 if pos >= 1.0 else 1.0)   # bien placé dans son rang : peu d'attaques pèsent moins
        pen_att = 0 if grenier else 30 * max(0.0, 1 - ratio) * facteur
        score = 70 - pen_att - 7 * bas - 12 * r        # petits malus seulement, aucun bonus
        score = max(0, min(100, round(score)))
        pics = sorted(range(24), key=lambda h: -R["att_h"][p][h])[:2] if sum(R["att_h"][p]) else []
        pics = [h for h in pics if R["att_h"][p][h] > 0]
        statut = "Fort" if score >= 70 else ("Faible" if score <= 50 else "Moyen")
        notes = []
        if not grenier and med_att.get(poste) and moy_att[p] < 0.5 * med_att[poste]:
            notes.append("peu d'attaques")
        if bas >= 3:
            notes.append(f"{bas} jours bien sous la moyenne du poste")
        if r:
            notes.append(f"{r} retard(s) de convoi")
        out.append({"pseudo": p, "poste": poste, "att": round(moy_att[p], 1), "med": round(med_att.get(poste, 0), 1), "ratio": round(ratio, 2),
                    "bas": bas, "retards": r, "pos": round(pos, 2), "pics": ", ".join(f"{h}h" for h in sorted(pics)), "score": score, "statut": statut,
                    "notes": " ; ".join(notes)})
    return sorted(out, key=lambda x: -x["score"])


# ---------------------------------------------------------------- lecture du sheet privé
RECENTS = {}   # {pseudo minuscule: date d'arrivée ou de changement de rang} pour les 7 derniers jours : pas notés
RODAGE = []    # liste (pseudo, poste, date) des joueurs en rodage, remplie par evaluer
INFOS = {}   # {pseudo minuscule: {x, y, va, tdp}} lu dans l'onglet « Data » du sheet privé (rempli par lire_prive)


def _entier(s):
    import re
    m = re.findall(r"\d+", str(s).replace(" ", "").replace("\xa0", "").replace(" ", ""))
    return int(m[-1]) if m else None


def lire_infos(pv):
    infos = {}
    for r in pv.worksheet("Data").get("A2:K400"):
        r = list(r) + [""] * 11
        p = r[0].strip().lower()
        if not p:
            continue
        i = {}
        x, y, tdp, va = _entier(r[1]), _entier(r[2]), _entier(r[6]), _entier(r[8])
        if r[1].strip() != "" and r[2].strip() != "" and x is not None and y is not None:
            i["x"], i["y"] = x, y
        i["tdp"] = tdp or 0
        i["va"] = va or 0
        infos[p] = i
    return infos


def lire_prive(sh):
    global INFOS
    postes, retards = {}, {}
    try:
        import gspread
        c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
        c.read(os.path.join(DOSSIER, "ange_watch.ini"), encoding="utf-8")
        cred = c.get("google", "credentials", fallback="service_account.json")
        if not os.path.isabs(cred):
            cred = os.path.join(DOSSIER, cred)
        pv = gspread.service_account(filename=cred).open_by_key(PRIVE_ID)
        try:
            INFOS = lire_infos(pv)
        except Exception as e:
            print("lecture Data (positions, VA) :", e, flush=True)
        for r in pv.worksheet("Chaîne unique").get("B3:C150"):
            if len(r) >= 2 and r[1].strip():
                postes[r[1].strip().lower()] = r[0].strip()
        try:   # une ligne BARRÉE dans « Chaîne unique » = joueur sorti de la chaîne (règle d'Arno) : même traitement que « Hors chaîne »
            meta = pv.fetch_sheet_metadata({"includeGridData": "true", "ranges": "'Chaîne unique'!B3:C150",
                                            "fields": "sheets.data.rowData.values(formattedValue,effectiveFormat.textFormat.strikethrough)"})
            for ligne in meta["sheets"][0]["data"][0].get("rowData", []):
                v = ligne.get("values", [])
                if len(v) >= 2 and v[1].get("formattedValue") and any(x.get("effectiveFormat", {}).get("textFormat", {}).get("strikethrough") for x in v):
                    postes[v[1]["formattedValue"].strip().lower()] = "Hors chaîne"
        except Exception as e:
            print("lecture des lignes barrées :", e, flush=True)
        # les alertes de convois sont écrites par Apps Script dans le Suivi ANGE (A=niveau, B=type, C=joueur)
        for r in sh.worksheet("Alertes").get("A5:C300"):
            if len(r) >= 3 and r[1].strip().lower() in ("convoi", "retard cumulé"):
                retards[r[2].strip().lower()] = retards.get(r[2].strip().lower(), 0) + 1
    except Exception as e:
        print("lecture sheet privé :", e, flush=True)
    return postes, retards


# ---------------------------------------------------------------- mise en forme
def fm(n):
    return f"{int(n):,}".replace(",", " ")


def hp(v):
    return paris(v).strftime("%d/%m %Hh%M")


def onglet_chasse(R, chasseurs, aujourdhui):
    jours = sorted({j for d in R["chasse"].values() for j in d})[-FENETRE_JOURS:]
    ent = ["Chasseur"] + [datetime.strptime(j, "%Y-%m-%d").strftime("%d/%m") + (" (en cours)" if j == aujourdhui else "") for j in jours] + ["Moyenne 7 j complets"]
    lignes = [["Chasse estimée de chaque chasseur = hausses horaires de son TDC. Si une hausse est nettement sous sa chasse habituelle (médiane des retours voisins), il a été attaqué dans la même heure : on compte la chasse habituelle. Les chasses cachées derrière une baisse sont retrouvées grâce au net horaire d'ANGE ; le petit reste que rien n'explique est réparti entre les chasseurs au prorata de leur chasse du jour."], ent]
    compl = [j for j in jours if j < aujourdhui][-JOURS_NOTE:]
    tot = collections.defaultdict(int)
    for p in sorted(R["chasse"], key=lambda p: -sum(R["chasse"][p].get(j, 0) for j in compl)):
        row = [p]
        for j in jours:
            x = R["chasse"][p].get(j, 0)
            row.append(x)
            tot[j] += x
        row.append(round(sum(R["chasse"][p].get(j, 0) for j in compl) / max(len(compl), 1)))
        lignes.append(row)
    lignes.append(["TOTAL chassé (hausses)"] + [tot[j] for j in jours] + [round(sum(tot[j] for j in compl) / max(len(compl), 1))])
    lignes.append(["Variation nette TOTALE d'ANGE (tous membres)"] + [R["net_jour"].get(j, 0) for j in jours] + [round(sum(R["net_jour"].get(j, 0) for j in compl) / max(len(compl), 1))])
    lignes.append(["Écart net - chasse (>0 : ANGE gagne plus que la chasse minimale)"] + [R["net_jour"].get(j, 0) - tot[j] for j in jours] + [""])
    return lignes


def onglet_chasse_h(R):
    lignes = [["Part de la chasse de chaque chasseur par heure de la journée (heure de Paris), sur les 14 derniers jours."],
              ["Chasseur"] + [f"{h}h" for h in range(24)] + ["Heure de pic"]]
    for p in sorted(R["chasse_h"], key=lambda p: -sum(R["chasse_h"][p])):
        s = sum(R["chasse_h"][p]) or 1
        pic = max(range(24), key=lambda h: R["chasse_h"][p][h])
        lignes.append([p] + [f"{round(100 * R['chasse_h'][p][h] / s)} %" for h in range(24)] + [f"{pic}h"])
    return lignes


def onglet_fuites(R):
    ev = sorted(R["preuves"], key=lambda x: x[0], reverse=True)
    sures = [e for e in ev if e[7] == "SÛRE"]
    lignes = [["Pertes d'un membre ANGE dont UN SEUL joueur hors ANGE gagne exactement le même montant, la même heure, avec un TDC de 50 à 150 % de la victime."],
              [f"SÛRE = montant non rond (preuve forte). FAIBLE = montant rond (peut être un hasard). SÛRES : {len(sures)} · total {fm(sum(e[2] for e in sures))} TDC · fenêtre {FENETRE_JOURS} jours (31/10 exclu)."],
              ["Heure (Paris)", "Niveau", "Victime ANGE", "TDC perdu", "TDC victime", "Attaquant hors ANGE", "Alliance", "TDC attaquant", "Ratio", "Message pour la victime"]]
    for v, vic, m, t0, a, al, ta, niv in ev[:300]:
        d = paris(v)
        msg = (f"Info : le {d:%d/%m} vers {d:%H}h, {a} ({al or 'sans alliance'}) t'a pris {fm(m)} TDC. Regarde tes rapports d'attaque et préviens-nous si "
               f"tu étais en train de livrer ou si tu as été flood.") if niv == "SÛRE" else ""
        lignes.append([hp(v), niv, vic, m, t0, a, al or "(aucune)", ta, f"{round(100 * ta / t0)} %", msg])
    return lignes


def onglet_attaquants(R):
    par, par_al = {}, {}
    for v, vic, m, t0, a, al, ta, niv in R["preuves"]:
        e = par.setdefault(a, {"al": al, "s": 0, "ms": 0, "f": 0, "vic": set(), "der": v})
        if niv == "SÛRE":
            e["s"] += 1
            e["ms"] += m
        else:
            e["f"] += 1
        e["vic"].add(vic)
        e["der"] = max(e["der"], v)
        if niv == "SÛRE":
            x = par_al.setdefault(al or "(aucune)", {"n": 0, "m": 0, "j": set()})
            x["n"] += 1
            x["m"] += m
            x["j"].add(a)
    lignes = [["Qui nous prend du TDC (preuves SÛRES d'abord)."], ["Joueur", "Alliance", "Prises SÛRES", "TDC pris (SÛRES)", "Prises faibles (rondes)", "Victimes différentes", "Dernière"]]
    for a, e in sorted(par.items(), key=lambda x: (-x[1]["s"], -x[1]["f"])):
        lignes.append([a, e["al"] or "(aucune)", e["s"], e["ms"], e["f"], len(e["vic"]), hp(e["der"])])
    lignes += [[], ["Par alliance (SÛRES)"], ["Alliance", "Prises", "TDC pris", "Joueurs"]]
    for al, x in sorted(par_al.items(), key=lambda x: -x[1]["m"]):
        lignes.append([al, x["n"], x["m"], len(x["j"])])
    return lignes


def onglet_activite(notes):
    lignes = [["Activité des membres (hors chasseurs), 7 derniers jours complets. Attaques = attaques réussies identifiées (qui a pris à qui), datées à l'heure d'ENVOI "
               "(heure d'impact moins temps de trajet) ; détail dans l'onglet « Attaques détail ». Greniers : pas jugés sur les attaques (ils n'ont parfois pas de cible)."],
              ["Joueur", "Poste", "Attaques/jour", "Médiane du poste", "Rapport", "Heures de pic (Paris)", "Jours < 80 % de la moyenne du poste", "Retards convois", "Score", "Statut", "Remarques"]]
    for x in notes:
        lignes.append([x["pseudo"], x["poste"], x["att"], x["med"], x["ratio"], x["pics"], x["bas"], x["retards"], x["score"], x["statut"], x["notes"]])
    return lignes


def meilleurs_pires(notes):
    ordre = sorted(notes, key=lambda x: (-x["score"], -x["att"] * (x["poste"].lower().startswith("grenier") is False), -x["pos"]))
    return ordre[:10], sorted(notes, key=lambda x: (x["score"], x["pos"]))[:10]


def notes_chasseurs(R, chasseurs, aujourdhui):
    jours = jours_complets(R, aujourdhui)
    moy = {p: sum(R["chasse"][p].get(j, 0) for j in jours) / max(len(jours), 1) for p in R["chasse"] if p.lower() in chasseurs and p.lower() not in RECENTS}
    if len(moy) < 3:
        return []
    med = statistics.median(moy.values())
    out = [(p, round(v), round(med), round(v / med, 2) if med else 0) for p, v in moy.items()]
    return sorted(out, key=lambda x: x[3])


def onglet_classement(notes, chass=None):
    forts, faibles = meilleurs_pires(notes)
    lignes = [["Meilleurs et pires (score sur 70 : on ne retire que des points, jamais de bonus). Retirés : attaques/jour bien sous le poste (réduit si le joueur est au-dessus de la médiane de son poste en TDC), jours bien sous la moyenne du poste, retards de convoi. Les nouveaux et les changements de rang ne sont pas notés pendant 7 jours."],
              ["MEILLEURS"], ["Joueur", "Poste", "Score", "Attaques/jour", "TDC / médiane du poste"]]
    lignes += [[x["pseudo"], x["poste"], x["score"], x["att"], x["pos"]] for x in forts]
    lignes += [[], ["PIRES"], ["Joueur", "Poste", "Score", "Attaques/jour", "TDC / médiane du poste", "Remarques"]]
    lignes += [[x["pseudo"], x["poste"], x["score"], x["att"], x["pos"], x["notes"]] for x in faibles]
    if chass:
        lignes += [[], ["CHASSEURS (TDC chassé par jour sur 7 jours, comparé à la médiane des chasseurs)"], ["Chasseur", "Chassé/jour", "Médiane", "Rapport", "Remarque"]]
        lignes += [[p, v, m, r, "chasse très basse : à vérifier" if r < 0.5 else ""] for p, v, m, r in chass[:6]]
    if RODAGE:
        lignes += [[], ["EN RODAGE (non notés pendant 7 jours)"]] + [[p, po, "depuis le " + d] for p, po, d in RODAGE]
    return lignes


def recap(R, notes, chasseurs, aujourdhui, maintenant):
    hier = (datetime.strptime(aujourdhui, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    ch_h = {p: R["chasse"][p].get(hier, 0) for p in R["chasse"]}
    tot = sum(ch_h.values())
    top = sorted(ch_h.items(), key=lambda x: -x[1])[:3]
    net = R["net_jour"].get(hier, 0)
    ev24 = [e for e in R["preuves"] if e[0] >= (maintenant - timedelta(hours=24)).strftime("%Y%m%d%H%M") and e[7] == "SÛRE"]
    neg = [x for x in R["net_h"] if x[0] >= (maintenant - timedelta(hours=24)).strftime("%Y%m%d%H%M") and x[1] < 0]
    lignes = [f"📊 Récap ANGE du {datetime.strptime(hier, '%Y-%m-%d'):%d/%m}",
              f"Chasse (estimée) : {fm(tot)} TDC" + (" · top : " + ", ".join(f"{p} {fm(v)}" for p, v in top) if top else ""),
              f"Variation nette d'ANGE : {fm(net)} TDC (écart avec la chasse : {fm(net - tot)})",
              f"Fuites sûres sur 24 h : {len(ev24)} ({fm(sum(e[2] for e in ev24))} TDC)" + ("".join(f"\n  - {hp(e[0])} : {e[1]} → {e[4]} ({e[5] or 'sans alliance'}) {fm(e[2])}" for e in ev24[:5])),
              f"Heures où le total d'ANGE a baissé : {len(neg)}" + (" (" + ", ".join(f"{hp(v)} {fm(n)}" for v, n in neg[:4]) + ")" if neg else "")]
    forts, faibles = meilleurs_pires(notes)
    forts, faibles = forts[:5], faibles[:5]
    lignes.append("💪 Forts : " + (", ".join(f"{x['pseudo']} ({x['score']})" for x in forts) or "-"))
    lignes.append("⚠️ Faibles : " + (", ".join(f"{x['pseudo']} ({x['score']})" for x in faibles) or "-"))
    return "\n".join(lignes)


def recap_feuille(R, notes, chasseurs, aujourdhui, maintenant):
    """Récap lisible pour l'onglet : retourne (lignes, styles) ; styles = liste de (n° de ligne 1-based, type)."""
    hier = (datetime.strptime(aujourdhui, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    ch_h = {p: R["chasse"][p].get(hier, 0) for p in R["chasse"]}
    tot = sum(ch_h.values())
    net = R["net_jour"].get(hier, 0)
    lim = (maintenant - timedelta(hours=24)).strftime("%Y%m%d%H%M")
    ev24 = [e for e in R["preuves"] if e[0] >= lim and e[7] == "SÛRE"]
    neg = [x for x in R["net_h"] if x[0] >= lim and x[1] < 0]
    L, S = [], []

    def add(row, typ=None):
        L.append(list(row) + [""] * (5 - len(row)))
        if typ:
            S.append((len(L), typ))

    add([f"📊 Récap ANGE du {datetime.strptime(hier, '%Y-%m-%d'):%d/%m}"], "titre")
    add([f"Mis à jour le {(datetime.now(PARIS) if PARIS else datetime.now() + timedelta(hours=1)):%d/%m à %Hh%M} · dernier relevé de TDC : {(maintenant.astimezone(PARIS) if PARIS else maintenant + timedelta(hours=1)):%d/%m %Hh%M}"], "sous")
    add([""])
    add(["CHIFFRES CLÉS", "", "", "", ""], "section")
    add(["Chasse du jour (estimée)", fm(tot) + " TDC", "TDC ramassés par les chasseurs (hausses horaires corrigées des attaques subies)"])
    add(["Variation nette d'ANGE", fm(net) + " TDC", "TDC total de l'alliance, fin de journée moins début"])
    ecart = net - tot
    add(["Écart avec la chasse", fm(ecart) + " TDC", ("ANGE a gagné moins que la chasse : du TDC est parti" if ecart < -1000 else "RAS : ANGE a gagné au moins la chasse")], "alerte" if ecart < -1000 else None)
    add(["Fuites sûres (24 h)", f"{len(ev24)}  ({fm(sum(e[2] for e in ev24))} TDC)", "perte d'un membre = gain exact d'un joueur hors ANGE"], "alerte" if ev24 else None)
    add(["Heures où le total a baissé (24 h)", str(len(neg)), "toute baisse du total ANGE = TDC perdu à l'extérieur"], "alerte" if neg else None)
    add([""])
    add(["CHASSEURS DU JOUR", "", "", "", ""], "section")
    add(["Rang", "Chasseur", "TDC chassé hier", "Moyenne 7 jours", ""], "entete")
    jours7 = jours_complets(R, aujourdhui)[-7:]
    classement = sorted(ch_h.items(), key=lambda x: -x[1])
    for i, (p, v) in enumerate(classement[:10], 1):
        moy = sum(R["chasse"][p].get(j, 0) for j in jours7) / max(len(jours7), 1)
        add([str(i), p, fm(v), fm(moy)])
    add([""])
    add([f"FUITES SÛRES (24 h) : {len(ev24)}", "", "", "", ""], "section")
    if ev24:
        add(["Heure", "Victime", "Perte", "Gagnant hors ANGE", "Alliance"], "entete")
        for e in ev24[:12]:
            add([hp(e[0]), e[1], fm(e[2]) + " TDC", e[4], e[5] or "sans alliance"])
    else:
        add(["✅ Aucune fuite sûre sur les dernières 24 h."])
    add([""])
    forts = [x for x in notes if x["statut"] == "Fort"][:6]
    faibles = sorted([x for x in notes if x["statut"] == "Faible"], key=lambda x: x["score"])[:8]
    add(["MAILLONS FORTS", "", "", "", ""], "section")
    add(["Joueur", "Poste", "Score /100", "Attaques par jour", "Heures de pic"], "entete")
    for x in forts:
        add([x["pseudo"], x["poste"], str(x["score"]), str(x["att"]).replace(".", ","), x["pics"]])
    if not forts:
        add(["-"])
    add([""])
    add(["MAILLONS FAIBLES", "", "", "", ""], "section")
    add(["Joueur", "Poste", "Score /100", "Attaques par jour", "Pourquoi"], "entete")
    for x in faibles:
        add([x["pseudo"], x["poste"], str(x["score"]), str(x["att"]).replace(".", ","), x["notes"]], "faible")
    if not faibles:
        add(["-"])
    return L, S


def ecrire_recap(sh, lignes, styles):
    """Écrit l'onglet Récap et le met en forme (colonnes larges, sections colorées)."""
    import gspread
    try:
        ws = sh.worksheet("Récap")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("Récap", rows=60, cols=5)
    sid = ws.id
    if LIEN_GID:
        lignes = [[""] * 5] + lignes
        styles = [(n + 1, t) for n, t in styles]
    ws.clear()
    ws.resize(rows=max(len(lignes) + 2, 10), cols=5)
    ws.update(range_name="A1", values=lignes, value_input_option="RAW")
    if LIEN_GID:
        ws.update(range_name="A1", values=[[lien_accueil()]], value_input_option="USER_ENTERED")
    reqs = [{"repeatCell": {"range": {"sheetId": sid}, "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat"}}]
    for i, w in enumerate([260, 190, 170, 190, 330]):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                   "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": len(lignes), "startColumnIndex": 0, "endColumnIndex": 5},
                                "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
                                "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy"}})

    def fmt(n, bg=None, bold=False, size=None, color=None, cols=5):
        f, fl = {}, []
        if bg:
            f["backgroundColor"] = bg
            fl.append("userEnteredFormat.backgroundColor")
        tf = {}
        if bold:
            tf["bold"] = True
        if size:
            tf["fontSize"] = size
        if color:
            tf["foregroundColor"] = color
        if tf:
            f["textFormat"] = tf
            fl.append("userEnteredFormat.textFormat")
        reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": n - 1, "endRowIndex": n, "startColumnIndex": 0, "endColumnIndex": cols},
                                    "cell": {"userEnteredFormat": f}, "fields": ",".join(fl)}})

    def c(h):
        return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}

    for n, typ in styles:
        if typ == "titre":
            fmt(n, bold=True, size=20)
        elif typ == "sous":
            fmt(n, color=c("6b7280"))
        elif typ == "section":
            fmt(n, bg=c("1f2937"), bold=True, color=c("ffffff"))
        elif typ == "entete":
            fmt(n, bg=c("e5e7eb"), bold=True)
        elif typ == "alerte":
            fmt(n, bg=c("fee2e2"))
        elif typ == "faible":
            fmt(n, bg=c("ffedd5"))
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"hideGridlines": True}}, "fields": "gridProperties.hideGridlines"}})
    sh.batch_update({"requests": reqs})


# ---------------------------------------------------------------- écriture dans le sheet
LIEN_GID = None   # identifiant de l'onglet Accueil (lien « 🏠 Accueil » en haut de chaque onglet)


LIEN_SEP = ","


def lien_accueil():
    return '=HYPERLINK("#gid=%d"%s"🏠 Accueil")' % (LIEN_GID, LIEN_SEP)


def ecrire_onglet(sh, nom, lignes):
    import gspread
    try:
        ws = sh.worksheet(nom)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(nom, rows=max(len(lignes), 10) + 5, cols=max(max((len(r) for r in lignes), default=1), 5))
    lignes = [list(r) for r in lignes]
    if LIEN_GID:
        lignes = [[""]] + lignes
    ws.clear()
    ws.resize(rows=max(len(lignes), 2), cols=max(max((len(r) for r in lignes), default=1), 1))
    ws.update(range_name="A1", values=lignes, value_input_option="RAW")
    if LIEN_GID:
        ws.update(range_name="A1", values=[[lien_accueil()]], value_input_option="USER_ENTERED")


def lire_etat():
    try:
        return json.load(open(ETAT))
    except Exception:
        return {}


def ecrire_etat(st):
    json.dump(st, open(ETAT, "w"))


def construire(conn, chasseurs, postes, retards, maintenant=None):
    versions, data = charger(conn)
    if len(versions) < 3:
        return None
    R = calculer(versions, data, chasseurs)
    R["_v"], R["_d"] = versions, data      # réutilisés par ange_alliances
    R["_att"] = None
    if postes:   # attribution exacte des attaques (qui a attaqué qui, temps de trajet) : seulement si la chaîne est connue
        try:
            import importlib, sys
            m = sys.modules.get("ange_attaques")
            m = importlib.reload(m) if m else importlib.import_module("ange_attaques")
            res = m.analyser(versions, data, chasseurs, postes, INFOS, paris, exclu, R["chasse_pertes"])
            R["_att"] = (m, res)
            for p in list(R["att"]):
                if p.lower() in postes and not p.lower() in chasseurs:
                    R["att"][p] = collections.defaultdict(int)
                    R["att_h"][p] = [0] * 24
            for p0 in R["tdc_jour"]:   # chasses cachées derrière des pertes, retrouvées grâce au net horaire d'ANGE
                for v, c in res["chasse_cachee"].get(p0.lower(), {}).items():
                    pp = paris(v)
                    R["chasse"][p0][pp.strftime("%Y-%m-%d")] += c
                    R["chasse_h"][p0][pp.hour] += c
            rep = collections.defaultdict(lambda: collections.defaultdict(int))   # chasse non retrouvée, répartie au prorata de la chasse du jour
            poids_j = collections.defaultdict(dict)
            for c in res.get("compta", []):
                if c["reste"] <= 0:
                    continue
                pp = paris(c["v1"])
                j = pp.strftime("%Y-%m-%d")
                if j not in poids_j:
                    poids_j[j] = {p0: R["chasse"][p0].get(j, 0) for p0 in R["chasse"] if R["chasse"][p0].get(j, 0) > 0}
                tot_p = sum(poids_j[j].values())
                if tot_p <= 0:
                    continue
                for p0, w in poids_j[j].items():
                    part = int(round(c["reste"] * w / tot_p))
                    rep[p0][j] += part
                    R["chasse_h"][p0][pp.hour] += part
            for p0, jours in rep.items():
                for j, part in jours.items():
                    R["chasse"][p0][j] += part
                    R["chasse_rep"][p0][j] += part
            for p, jours in res["att"].items():
                for p0 in R["tdc_jour"]:
                    if p0.lower() == p:
                        for j, n in jours.items():
                            R["att"][p0][j] += n
                        for h, n in enumerate(res["att_h"][p]):
                            R["att_h"][p0][h] += n
        except Exception as e:
            print("attaques :", e, flush=True)
            R["_att"] = None
    maintenant = maintenant or datetime.strptime(versions[-1], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    aujourdhui = (maintenant.astimezone(PARIS) if PARIS else maintenant + timedelta(hours=1)).strftime("%Y-%m-%d")
    notes = evaluer(R, postes, retards, chasseurs, aujourdhui)
    return R, notes, aujourdhui, maintenant, versions[-1]


FORMAT = 29 # à augmenter quand la mise en page change : force une réécriture immédiate des onglets


def maj_arrivees(st, postes, now_p):
    """Un joueur qui entre dans la chaîne ou change de poste n'est pas noté pendant 7 jours (RECENTS)."""
    auj = now_p.strftime("%Y-%m-%d")
    vus = st.setdefault("postes_vus", {})
    premiere = not vus
    for p, poste in postes.items():
        if poste.lower().startswith("hors"):
            continue
        if p not in vus:
            vus[p] = [poste, "2000-01-01" if premiere else auj]
        elif vus[p][0] != poste:
            vus[p] = [poste, auj]
    if not st.get("seed_rodage"):   # joueurs signalés à la main le 11/11
        for p, d in (("willo", "2026-11-11"), ("thething", "2026-11-11"), ("venom02", "2026-11-11"), ("vetroix", "2026-11-11"),
                     ("rogany", "2026-11-08"), ("akuretari", "2026-11-09")):
            if p in vus:
                vus[p][1] = d
        st["seed_rodage"] = True
    if not st.get("seed_rodage2"):
        if "notice" in vus:
            vus["notice"][1] = "2026-11-09"
        st["seed_rodage2"] = True
    limite = (now_p - timedelta(days=7)).strftime("%Y-%m-%d")
    RECENTS.clear()
    RECENTS.update({p: v[1] for p, v in vus.items() if v[1] > limite})


def tick(sh, force=False):
    """Appelé par le service. Ne fait rien tant qu'il n'y a pas de nouvelle version (ou de récap à envoyer)."""
    try:   # veille des membres/postes (convois) : ne doit JAMAIS gêner le suivi (toute erreur est avalée)
        import importlib, sys
        m = sys.modules.get("ange_convois_tick")
        m = importlib.reload(m) if m else importlib.import_module("ange_convois_tick")
        msg_v = m.tick(sh)
        if msg_v:
            print(msg_v, flush=True)
    except ModuleNotFoundError:
        pass
    except Exception as e_v:
        print("veille convois :", str(e_v)[:200], flush=True)
    try:   # notes d'activite quotidiennes (a HEURE_NOTES Paris, une fois/jour)
        import importlib, sys
        mn = sys.modules.get("ange_note_activite")
        mn = importlib.reload(mn) if mn else importlib.import_module("ange_note_activite")
        msg_n = mn.tick_notes(sh)
        if msg_n:
            print(msg_n, flush=True)
    except ModuleNotFoundError:
        pass
    except Exception as e_n:
        print("notes activite :", str(e_n)[:200], flush=True)
    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA query_only=ON")
    try:
        last = conn.execute("SELECT MAX(version) FROM tdc").fetchone()[0]
        st = lire_etat()
        now_p = datetime.now(PARIS) if PARIS else datetime.now()
        recap_du = now_p.hour >= HEURE_RECAP and st.get("recap") != now_p.strftime("%Y-%m-%d")
        if not force and st.get("version") == last and not recap_du and st.get("format") == FORMAT:
            return None
        global LIEN_GID, LIEN_SEP
        mods = {}
        try:  # page d'accueil : doit exister avant l'écriture des autres onglets
            import importlib, sys
            for nom in ("ange_accueil", "ange_alliances"):
                m = sys.modules.get(nom)
                mods[nom] = importlib.reload(m) if m else importlib.import_module(nom)
            LIEN_GID = mods["ange_accueil"].preparer(sh)
            LIEN_SEP = mods["ange_accueil"].separateur(sh)
        except Exception as e:
            LIEN_GID = None
            print("accueil :", e, flush=True)
        chasseurs, webhook = lire_ini()
        postes, retards = lire_prive(sh)
        maj_arrivees(st, postes, now_p)
        if postes:  # la feuille privée fait foi : plus rien à tenir à la main dans l'ini
            chasseurs = {p.lower() for p, poste in postes.items() if poste.lower().startswith("chasseur")}
        res = construire(conn, chasseurs, postes, retards)
        if res is None:
            return None
        R, notes, aujourdhui, maintenant, derniere = res
        try:   # chasse estimée par jour de chaque chasseur, lue par les onglets chasseurs (ange_convois_chasse)
            tmp = os.path.join(DOSSIER, "chasse_jour.json.tmp")
            json.dump({"maj": maintenant.isoformat(), "aujourdhui": aujourdhui,
                       "jours": {p.lower(): {j: int(v) for j, v in d.items()} for p, d in R["chasse"].items() if p.lower() in chasseurs}},
                      open(tmp, "w", encoding="utf-8"))
            os.replace(tmp, os.path.join(DOSSIER, "chasse_jour.json"))
        except Exception as e_cj:
            print("chasse_jour.json :", e_cj, flush=True)
        ecrire_onglet(sh, "Chasse", onglet_chasse(R, chasseurs, aujourdhui))
        ecrire_onglet(sh, "Chasse horaire", onglet_chasse_h(R))
        ecrire_onglet(sh, "Fuites", onglet_fuites(R))
        ecrire_onglet(sh, "Attaquants", onglet_attaquants(R))
        ecrire_onglet(sh, "Activité attaques", onglet_activite(notes))
        if R.get("_att"):
            try:
                m_att, res_att = R["_att"]
                ecrire_onglet(sh, "Attaques détail", m_att.feuille(res_att, postes, INFOS, chasseurs, paris, fm, hp))
                ecrire_onglet(sh, "Heure par heure", m_att.feuille_heures(res_att, hp))
                ecrire_onglet(sh, "Pertes à élucider", m_att.feuille_anomalies(res_att, postes, chasseurs, hp, fm))
                ecrire_onglet(sh, "Chasse heure par heure", m_att.feuille_chasse(res_att, hp, fm))
            except Exception as e:
                print("onglet Attaques détail :", e, flush=True)
        ecrire_onglet(sh, "Classement", onglet_classement(notes, notes_chasseurs(R, chasseurs, aujourdhui)))
        # alertes Discord : calcul exact d'ange_attaques (la toute première exécution marque l'historique sans l'envoyer)
        vues = set(st.get("alertes", []))
        for e in R["preuves"]:
            vues.add(f"{e[0]}|{e[1]}|{e[4]}|{e[2]}")      # ancien système : on garde seulement l'historique
        try:
            if R.get("_att"):
                nb_al, prem = alertes_discord(R["_att"][1], postes, chasseurs, st, webhook)
                print(f"alertes : {nb_al} ({'historique marqué, rien envoyé' if prem else 'envoyées'})", flush=True)
        except Exception as e:
            print("alertes Discord :", e, flush=True)
        st["alertes"] = sorted(vues)[-3000:]
        st["version"] = last
        st["format"] = FORMAT
        texte = recap(R, notes, chasseurs, aujourdhui, maintenant)
        try:  # l'onglet Récap est remis à jour à chaque relevé ; Discord une seule fois par jour
            lg, st_ = recap_feuille(R, notes, chasseurs, aujourdhui, maintenant)
            ecrire_recap(sh, lg, st_)
        except Exception as e:
            print("onglet Récap :", e, flush=True)
            ecrire_onglet(sh, "Récap", [[l] for l in texte.split("\n")])
        try:  # autres alliances suivies (onglet « Alliances » + un onglet par TAG)
            mods["ange_alliances"].tick_alliances(sh, conn, R["_v"], R["_d"], chasseurs, R["preuves"], webhook, st, maintenant, LIEN_GID, LIEN_SEP)
        except Exception as e:
            print("alliances :", e, flush=True)
        try:  # accueil à jour (liste réelle des onglets) puis rangement des onglets
            if LIEN_GID:
                mods["ange_accueil"].ecrire(sh)
                mods["ange_accueil"].ranger(sh)
        except Exception as e:
            print("accueil :", e, flush=True)
        if recap_du:
            discord(webhook, texte)
            try:
                for msg in (recap_contacts(R, R["_att"][1], postes, chasseurs, now_p.strftime("%Y-%m-%d")) if R.get("_att") else []):
                    discord(webhook, msg)
            except Exception as e:
                print("recap contacts :", e, flush=True)
            st["recap"] = now_p.strftime("%Y-%m-%d")
        ecrire_etat(st)
        return f"SUIVI {now_p:%H:%M} : version {last}, {len(R['preuves'])} preuves, {len(notes)} membres"
    finally:
        conn.close()


def recap_contacts(R, res, postes, chasseurs, aujourdhui):
    """2e message du matin : erreurs de la veille (heure par heure) et joueurs à contacter."""
    hier = (datetime.strptime(aujourdhui, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y%m%d")
    # les versions sont en UTC, Paris = UTC+1 : on filtre sur la date de Paris
    def jour_paris(v1):
        return (datetime.strptime(v1, "%Y%m%d%H%M") + timedelta(hours=1)).strftime("%Y%m%d")
    pos = neg = 0
    for c in res.get("compta", []):
        if jour_paris(c["v1"]) == hier:
            r = c.get("reste", 0)
            if r > 0:
                pos += r
            else:
                neg += -r
    pertes = [(v1, vic, m, cause) for v1, vic, m, cause, _ in res.get("anomalies", []) if jour_paris(v1) == hier and m >= 3000]
    poss = [(v1, vic, m, "perte vers " + (noms[0] if noms else "?")) for v1, vic, m, noms, als, cert in res.get("prises_ext", []) if jour_paris(v1) == hier and cert != "sûre" and m >= 3000]
    tout = sorted(pertes + poss, key=lambda x: -x[2])
    lignes = [f"🔎 Erreurs de la veille : chasse non attribuée {fm(pos)} TDC, sorties non expliquées {fm(neg)} TDC, {len(tout)} pertes à élucider"]
    if tout:
        lignes.append("À contacter (demander qui les a attaqués à cette heure-là) :")
        for v1, vic, m, cause in tout[:25]:
            lignes.append(f"- {vic} ({postes.get(vic.lower(), 'hors chaîne')}) : -{fm(m)} à {hp(v1)} ({cause})")
    out, cur = [], ""
    for l in lignes:
        if len(cur) + len(l) + 1 > 1800:
            out.append(cur)
            cur = ""
        cur += l + "\n"
    if cur:
        out.append(cur)
    return out


SEUILS_DEFAUT = {"fuite_sure": 10000, "fuite_possible": 30000, "perte_inconnue": 40000, "chasse_passeur": 10000}


def lire_seuils():
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    c.read(os.path.join(DOSSIER, "ange_watch.ini"), encoding="utf-8")
    out = dict(SEUILS_DEFAUT)
    for k in out:
        try:
            out[k] = int(c.get("alertes", "seuil_" + k, fallback=out[k]))
        except ValueError:
            pass
    return out


def alertes_discord(res, postes, chasseurs, st, webhook):
    """Alertes de l'heure à partir du calcul exact (ange_attaques). Dédoublonnées ; la toute première exécution marque l'historique sans l'envoyer.
    1) fuite certaine : perte d'un membre = gain exact d'un seul joueur plausible hors chaîne, non récupéré par un membre d'ANGE ;
    2) fuite possible : même chose mais coupable incertain (plusieurs candidats ou récupéré aussi dans ANGE) ;
    3) grosse perte qu'aucun joueur ne peut expliquer."""
    seuils = lire_seuils()
    vues = set(st.get("alertes2", []))
    premiere = "alertes2" not in st
    lignes, nouvelles_cles = [], []

    def poste(p):
        return postes.get(p.lower(), "hors chaîne")

    limite = (datetime.utcnow() - timedelta(hours=AGE_MAX_ALERTE_H)).strftime("%Y%m%d%H%M")

    def ajouter(cle, texte):
        if cle in vues:
            return
        nouvelles_cles.append(cle)          # marquée comme vue dans tous les cas
        if cle.split("|")[1] < limite:      # événement ancien (recalculé après un changement de chaîne) : jamais envoyé
            return
        lignes.append(texte)

    for v1, vic, m, noms, als, cert in res.get("prises_ext", []):
        if cert == "sûre" and m >= seuils["fuite_sure"]:
            ajouter(f"F|{v1}|{vic}|{m}", f"🚨 FUITE CERTAINE {hp(v1)} : {vic} ({poste(vic)}) perd {fm(m)} → {noms[0]} ({als[0] or 'sans alliance'}) gagne exactement {fm(m)}")
        elif cert != "sûre" and "récupéré" not in cert and m >= seuils["fuite_possible"]:   # récupéré par un membre d'ANGE = attaque interne : pas d'alerte
            ajouter(f"P|{v1}|{vic}|{m}", f"⚠️ Fuite possible {hp(v1)} : {vic} ({poste(vic)}) perd {fm(m)} ; gagnants exacts hors ANGE : " + ", ".join(f"{n} ({a or 'sans alliance'})" for n, a in zip(noms[:3], als[:3])))
    for v1, vic, m, cause, possibles in res.get("anomalies", []):
        if m >= seuils["perte_inconnue"] and (vic in postes or vic in chasseurs):
            ajouter(f"I|{v1}|{vic}|{m}", f"❓ Perte sans coupable {hp(v1)} : {vic} ({poste(vic)}) perd {fm(m)} ({cause})")
    vues.update(nouvelles_cles)
    st["alertes2"] = sorted(vues)[-4000:]
    if lignes and not premiere:
        for i in range(0, len(lignes), 8):
            discord(webhook, "\n".join(lignes[i:i + 8]))
    return len(lignes), premiere


def AUTOTEST():
    """Test sur une base synthétique : doit tourner sans erreur."""
    e = estimer_chasse([("202611050000", 21000), ("202611050800", 22000), ("202611051600", 10000), ("202611060000", 23000), ("202611060800", 22500)])
    assert e["202611050000"] == (21000, 0) and e["202611051600"][1] > 10000 and 20000 < e["202611051600"][0] < 24000, e
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tdc (version TEXT, pseudo TEXT, alliance TEXT, tdc INTEGER)")
    base = datetime(2026, 11, 1, 0, 2)
    for k in range(60):
        v = (base + timedelta(hours=k)).strftime("%Y%m%d%H%M")
        rows = [(v, "Chass", "ANGE", 20000 + 500 * k), (v, "Vic", "ANGE", 100000 - (61032 if k >= 30 else 0)),
                (v, "Att", "ANGE", 100000 + 50 * k), (v, "Ext", "-AP-", 100000 + (61032 if k >= 30 else 0)),
                (v, "Autre", "ANGE", 90000 + 10 * k), (v, "Ext2", "XX", 50000)]
        conn.executemany("INSERT INTO tdc VALUES (?,?,?,?)", rows)
    res = construire(conn, {"chass"}, {"vic": "Passeur 1", "att": "Passeur 1", "autre": "Passeur 1"}, {})
    assert res is not None
    R, notes, aujourdhui, maintenant, derniere = res
    assert any(e[4] == "Ext" and e[7] == "SÛRE" for e in R["preuves"]), "fuite synthétique non trouvée"
    for f in (lambda: onglet_chasse(R, {"chass"}, aujourdhui), lambda: onglet_chasse_h(R), lambda: onglet_fuites(R), lambda: onglet_attaquants(R),
              lambda: onglet_activite(notes), lambda: onglet_classement(notes), lambda: recap(R, notes, {"chass"}, aujourdhui, maintenant), lambda: recap_feuille(R, notes, {"chass"}, aujourdhui, maintenant)):
        f()


if __name__ == "__main__":
    AUTOTEST()
    print("ok")

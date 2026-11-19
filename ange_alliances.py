"""ange_alliances.py : suivi des AUTRES alliances (TAXE, AAA, TYR...) à partir de la base tdc_serveur.db.

Appelé par ange_suivi.tick() à chaque nouveau relevé horaire.
Onglet « Alliances » : réglage + synthèse. Arno écrit un TAG par ligne dans la colonne A (à partir de la ligne 5).
Pour chaque TAG, un onglet « Alliance <TAG> » est créé et mis à jour tout seul :
  chiffres clés, TDC pris à ANGE (preuves), TDC pris par ANGE chez eux, arrivées / départs, évolution par jour, joueurs.
Alerte Discord : quand un joueur d'une alliance suivie prend du TDC à un membre ANGE avec une preuve SÛRE
(montant non rond, un seul gagnant possible). La toute première exécution marque l'historique sans l'envoyer.
Règles : le 31/10/2026 (heure de Paris) ne compte pas ; une attaque prend 50 à 150 % du TDC de l'attaquant (plage de la cible).
"""
import collections, json, re, sqlite3, urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover
    PARIS = None

PERIODES_EXCLUES = (("202610302300", "202610312300"),)
TAGS_DEFAUT = ["TAXE", "AAA", "TYR"]
JOURS_EVOL = 35
SEUIL = 1000


def exclu(v):
    return any(a <= v < b for a, b in PERIODES_EXCLUES)


def paris(v):
    dt = datetime.strptime(v, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    return dt.astimezone(PARIS) if PARIS else dt + timedelta(hours=1)


def hp(v):
    return paris(v).strftime("%d/%m %Hh%M")


def fm(n):
    return f"{int(n):,}".replace(",", " ")


def fs(n):
    return ("+" if n > 0 else "") + fm(n)


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
def analyser(versions, data, chasseurs, tags, preuves):
    """Pour chaque TAG : arrivées, départs, TDC pris à ANGE (preuves), TDC pris par ANGE chez eux."""
    T = set(tags)
    res = {t: {"arr": [], "dep": [], "prises": [], "ange_chez": []} for t in tags}
    for i in range(len(versions) - 1):
        v0, v1 = versions[i], versions[i + 1]
        if exclu(v0) or exclu(v1):
            continue
        A, B = data[v0], data[v1]
        avant = data[versions[i - 1]] if i >= 1 else {}
        apres = data[versions[i + 2]] if i + 2 < len(versions) else {}
        gains = collections.defaultdict(list)
        pertes = []
        for p, (al1, t1) in B.items():
            a0 = A.get(p)
            u1 = al1.upper()
            if a0 is None:
                if u1 in T and p not in avant:            # un joueur absent un seul relevé n'est pas un nouveau joueur
                    res[u1]["arr"].append((v1, p, "nouveau joueur", t1))
                continue
            al0, t0 = a0
            if al0 != al1:
                if u1 in T:
                    res[u1]["arr"].append((v1, p, "arrivée de " + (al0 or "sans alliance"), t1))
                if al0.upper() in T:
                    res[al0.upper()]["dep"].append((v1, p, "départ vers " + (al1 or "sans alliance"), t0))
                continue
            d = t1 - t0
            if d >= SEUIL:
                gains[d].append((p, al1, t0))
            elif d <= -SEUIL and u1 in T:
                pertes.append((p, -d, t0, u1))
        for p, (al0, t0) in A.items():
            if p not in B and al0.upper() in T and p not in apres:
                res[al0.upper()]["dep"].append((v1, p, "disparu de l'API", t0))
        for p, m, t0, t in pertes:
            cand = [c for c in gains.get(m, []) if 0.5 * t0 <= c[2] <= 1.5 * t0]
            if len(cand) != 1:
                continue
            a, al, ta = cand[0]
            if al == "ANGE" and a.lower() not in chasseurs:
                res[t]["ange_chez"].append((v1, p, m, a, ta, "SÛRE" if m % 100 else "FAIBLE (montant rond)"))
    for e in preuves:
        t = e[5].upper()
        if t in T:
            res[t]["prises"].append(e)
    return res


def evolution(conn, tags, derniere):
    """{TAG: [(jour, joueurs, tdc_total)]} : dernier relevé de chaque jour (Paris), sur JOURS_EVOL jours."""
    debut = (datetime.strptime(derniere, "%Y%m%d%H%M") - timedelta(days=JOURS_EVOL)).strftime("%Y%m%d%H%M")
    ph = ",".join("?" * len(tags))
    par = {t: collections.OrderedDict() for t in tags}
    for v, a, n, s in conn.execute(
            f"SELECT version, UPPER(alliance), COUNT(*), SUM(tdc) FROM tdc WHERE version >= ? AND UPPER(alliance) IN ({ph}) GROUP BY version, UPPER(alliance) ORDER BY version",
            [debut] + list(tags)):
        if exclu(v):
            continue
        par[a][paris(v).strftime("%Y-%m-%d")] = (n, s)
    return {t: [(j, n, s) for j, (n, s) in d.items()] for t, d in par.items()}


def membres(data, version, tag):
    return {p: t for p, (a, t) in data.get(version, {}).items() if a.upper() == tag}


def version_avant(versions, derniere, heures):
    cible = (datetime.strptime(derniere, "%Y%m%d%H%M") - timedelta(hours=heures)).strftime("%Y%m%d%H%M")
    ok = [v for v in versions if v <= cible and not exclu(v)]
    return ok[-1] if ok else None


# ---------------------------------------------------------------- mise en page
def feuille_alliance(tag, versions, data, res, evol, maintenant):
    last = versions[-1]
    now_m = membres(data, last, tag)
    v24, v7 = version_avant(versions, last, 24), version_avant(versions, last, 24 * 7)
    m24 = membres(data, v24, tag) if v24 else {}
    m7 = membres(data, v7, tag) if v7 else {}
    tot = sum(now_m.values())
    d24 = (tot - sum(m24.values())) if m24 else None
    d7 = (tot - sum(m7.values())) if m7 else None
    L, S = [], []

    def add(row, typ=None):
        L.append(list(row) + [""] * (6 - len(row)))
        if typ:
            S.append((len(L), typ))

    cut = (datetime.strptime(last, "%Y%m%d%H%M") - timedelta(days=14)).strftime("%Y%m%d%H%M")
    maj = (datetime.now(PARIS) if PARIS else datetime.now() + timedelta(hours=1))
    add([f"🛡️ Alliance {tag}"], "titre")
    add([f"Mis à jour le {maj:%d/%m à %Hh%M} · dernier relevé de TDC : {hp(last)} · chasse non séparée (on ne connaît pas leurs postes)"], "sous")
    add([""])
    add(["CHIFFRES CLÉS", "", "", "", "", ""], "section")
    if not now_m:
        add(["Aucun joueur avec ce TAG dans le dernier relevé. Vérifie l'orthographe du TAG."], "alerte")
        return L, S
    top = max(now_m.items(), key=lambda x: x[1])
    add(["Joueurs", str(len(now_m)), "", "", "", ""])
    add(["TDC total", fm(tot) + " TDC"])
    add(["Variation 24 h", (fs(d24) + " TDC") if d24 is not None else "-"])
    add(["Variation 7 jours", (fs(d7) + " TDC") if d7 is not None else "-"])
    add(["TDC moyen par joueur", fm(tot / len(now_m)) + " TDC"])
    add(["Plus gros joueur", f"{top[0]} ({fm(top[1])})"])
    pr = [e for e in res["prises"] if e[0] >= cut]
    sure = [e for e in pr if e[7] == "SÛRE"]
    add(["TDC pris à ANGE (14 j, preuves sûres)", f"{len(sure)} prise(s) · {fm(sum(e[2] for e in sure))} TDC"], "alerte" if sure else None)
    ac = [e for e in res["ange_chez"] if e[0] >= cut]
    add(["TDC pris par ANGE chez eux (14 j)", f"{len([e for e in ac if e[5] == 'SÛRE'])} prise(s) sûre(s) · {fm(sum(e[2] for e in ac if e[5] == 'SÛRE'))} TDC"])
    add([""])
    add(["ILS PRENNENT DU TDC À ANGE (14 j)", "", "", "", "", ""], "section")
    if pr:
        add(["Heure", "Victime ANGE", "TDC pris", "Attaquant", "TDC attaquant", "Preuve"], "entete")
        for e in sorted(pr, key=lambda x: x[0], reverse=True)[:30]:
            add([hp(e[0]), e[1], fm(e[2]), e[4], fm(e[6]), e[7]], "alerte" if e[7] == "SÛRE" else None)
    else:
        add(["✅ Aucune prise détectée sur les 14 derniers jours."])
    add([""])
    add(["ANGE PREND DU TDC CHEZ EUX (14 j)", "", "", "", "", ""], "section")
    if ac:
        add(["Heure", "Victime " + tag, "TDC pris", "Joueur ANGE", "TDC du joueur", "Preuve"], "entete")
        for e in sorted(ac, key=lambda x: x[0], reverse=True)[:30]:
            add([hp(e[0]), e[1], fm(e[2]), e[3], fm(e[4]), e[5]])
    else:
        add(["Aucune prise d'ANGE détectée sur les 14 derniers jours."])
    add([""])
    mv = sorted([(e, "A") for e in res["arr"] if e[0] >= cut] + [(e, "D") for e in res["dep"] if e[0] >= cut], key=lambda x: x[0][0], reverse=True)
    add([f"ARRIVÉES ET DÉPARTS (14 j) : {len([1 for _, k in mv if k == 'A'])} arrivée(s), {len([1 for _, k in mv if k == 'D'])} départ(s)", "", "", "", "", ""], "section")
    if mv:
        add(["Heure", "Joueur", "Mouvement", "TDC", "", ""], "entete")
        for e, k in mv[:40]:
            add([hp(e[0]), e[1], e[2], fm(e[3])])
    else:
        add(["Aucun mouvement sur les 14 derniers jours."])
    add([""])
    add(["ÉVOLUTION PAR JOUR (fin de journée)", "", "", "", "", ""], "section")
    ev = evol.get(tag, [])
    if ev:
        add(["Jour", "Joueurs", "TDC total", "Variation", "", ""], "entete")
        prev = None
        lignes = []
        for j, n, s in ev:
            lignes.append([datetime.strptime(j, "%Y-%m-%d").strftime("%d/%m"), str(n), fm(s), fs(s - prev) if prev is not None else "-"])
            prev = s
        for r in reversed(lignes):
            add(r)
    add([""])
    add(["JOUEURS", "", "", "", "", ""], "section")
    add(["Rang", "Joueur", "TDC", "Variation 24 h", "Variation 7 j", ""], "entete")
    for i, (p, t) in enumerate(sorted(now_m.items(), key=lambda x: -x[1]), 1):
        a, b = m24.get(p), m7.get(p)
        add([str(i), p, fm(t), fs(t - a) if a is not None else "-", fs(t - b) if b is not None else "-"])
    return L, S


def ecrire_feuille(sh, nom, lignes, styles, largeurs, ncols=6, gid=None, sep=","):
    import gspread
    try:
        ws = sh.worksheet(nom)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(nom, rows=max(len(lignes) + 2, 20), cols=ncols)
    sid = ws.id
    if gid:
        lignes = [[""] * ncols] + lignes
        styles = [(n + 1, t) for n, t in styles]
    ws.clear()
    ws.resize(rows=max(len(lignes) + 2, 10), cols=ncols)
    ws.update(range_name="A1", values=lignes, value_input_option="RAW")
    if gid:
        ws.update(range_name="A1", values=[['=HYPERLINK("#gid=%d"%s"🏠 Accueil")' % (gid, sep)]], value_input_option="USER_ENTERED")
    reqs = [{"repeatCell": {"range": {"sheetId": sid}, "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat"}}]
    for i, w in enumerate(largeurs):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                   "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": len(lignes), "startColumnIndex": 0, "endColumnIndex": ncols},
                                "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
                                "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.wrapStrategy"}})

    def c(h):
        return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}

    def fmt(n, bg=None, bold=False, size=None, color=None):
        f, fl, tf = {}, [], {}
        if bg:
            f["backgroundColor"] = bg
            fl.append("userEnteredFormat.backgroundColor")
        if bold:
            tf["bold"] = True
        if size:
            tf["fontSize"] = size
        if color:
            tf["foregroundColor"] = color
        if tf:
            f["textFormat"] = tf
            fl.append("userEnteredFormat.textFormat")
        reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": n - 1, "endRowIndex": n, "startColumnIndex": 0, "endColumnIndex": ncols},
                                    "cell": {"userEnteredFormat": f}, "fields": ",".join(fl)}})

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
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"hideGridlines": True}}, "fields": "gridProperties.hideGridlines"}})
    sh.batch_update({"requests": reqs})
    return ws


def nom_onglet(tag):
    return ("Alliance " + re.sub(r"[\[\]*?:/\\]", "", tag))[:99]


# ---------------------------------------------------------------- réglage : onglet « Alliances »
ENTETE_ALL = ["TAG (écris-le ici)", "Joueurs", "TDC total", "Variation 24 h", "Variation 7 j", "Pris à ANGE (14 j)", "ANGE chez eux (14 j)", "Arrivées (14 j)", "Départs (14 j)"]


def lire_tags(sh):
    """Lit les TAG dans l'onglet « Alliances » (colonne A, à partir de la ligne 5). Le crée avec les TAG par défaut si absent."""
    import gspread
    try:
        ws = sh.worksheet("Alliances")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("Alliances", rows=40, cols=len(ENTETE_ALL))
        ws.update(range_name="A1", values=[["🛡️ Alliances suivies"],
                                           ["Écris un TAG par ligne dans la colonne A (à partir de la ligne 5), exactement comme dans le jeu. Un onglet « Alliance <TAG> » est créé et mis à jour tout seul à chaque relevé."],
                                           [""], ENTETE_ALL] + [[t] for t in TAGS_DEFAUT], value_input_option="RAW")
    tags, lignes = [], {}
    for i, r in enumerate(ws.get("A5:A60")):
        t = (r[0] if r else "").strip().upper()
        if t and t not in tags:
            tags.append(t)
            lignes[t] = 5 + i
    return ws, tags, lignes


def maj_synthese(ws, lignes_syn):
    """lignes_syn : {n° de ligne: [valeurs colonnes B..I]}"""
    if lignes_syn:
        ws.batch_update([{"range": f"B{n}", "values": [v]} for n, v in lignes_syn.items()], value_input_option="RAW")
    reqs = []
    sid = ws.id
    for i, w in enumerate([190, 90, 130, 130, 130, 150, 160, 130, 130]):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                   "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1},
                                "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 20}}}, "fields": "userEnteredFormat.textFormat"}})
    reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 3, "endRowIndex": 4, "startColumnIndex": 0, "endColumnIndex": len(ENTETE_ALL)},
                                "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.12, "green": 0.16, "blue": 0.22},
                                                               "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}}},
                                "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat"}})
    ws.spreadsheet.batch_update({"requests": reqs})


# ---------------------------------------------------------------- appel principal
def tick_alliances(sh, conn, versions, data, chasseurs, preuves, webhook, st, maintenant, gid=None, sep=","):
    """Met à jour « Alliances » et les onglets par TAG. Retourne un court texte de statut."""
    ws, tags, lignes = lire_tags(sh)
    if not tags or not versions:
        return "aucun TAG"
    res = analyser(versions, data, chasseurs, tags, preuves)
    evol = evolution(conn, tags, versions[-1])
    last = versions[-1]
    v24, v7 = version_avant(versions, last, 24), version_avant(versions, last, 24 * 7)
    cut = (datetime.strptime(last, "%Y%m%d%H%M") - timedelta(days=14)).strftime("%Y%m%d%H%M")
    syn = {}
    for t in tags:
        now_m = membres(data, last, t)
        if not now_m:
            syn[lignes[t]] = ["TAG introuvable", "", "", "", "", "", "", ""]
            continue
        tot = sum(now_m.values())
        m24, m7 = (membres(data, v24, t) if v24 else {}), (membres(data, v7, t) if v7 else {})
        r = res[t]
        sure = [e for e in r["prises"] if e[0] >= cut and e[7] == "SÛRE"]
        syn[lignes[t]] = ([str(len(now_m)), fm(tot), fs(tot - sum(m24.values())) if m24 else "-", fs(tot - sum(m7.values())) if m7 else "-",
                    f"{len(sure)} ({fm(sum(e[2] for e in sure))})",
                    str(len([e for e in r["ange_chez"] if e[0] >= cut and e[5] == "SÛRE"])),
                    str(len([e for e in r["arr"] if e[0] >= cut])), str(len([e for e in r["dep"] if e[0] >= cut]))])
        lg, sty = feuille_alliance(t, versions, data, r, evol, maintenant)
        ecrire_feuille(sh, nom_onglet(t), lg, sty, [250, 200, 170, 190, 170, 190], gid=gid, sep=sep)
    maj_synthese(ws, syn)
    if gid:
        ws.update(range_name="F1", values=[['=HYPERLINK("#gid=%d"%s"🏠 Accueil")' % (gid, sep)]], value_input_option="USER_ENTERED")
    # alerte Discord : nouvelles prises SÛRES à un membre ANGE par une alliance suivie
    vues = set(st.get("alertes_all", []))
    premiere = "alertes_all" not in st
    nouvelles = []
    for t in tags:
        for e in res[t]["prises"]:
            k = f"{e[0]}|{e[1]}|{e[4]}|{e[2]}"
            if e[7] == "SÛRE" and k not in vues:
                vues.add(k)
                nouvelles.append((t, e))
    if nouvelles and not premiere:
        discord(webhook, "⚠️ TDC pris à un membre ANGE par une alliance suivie (preuve forte) :\n" + "\n".join(
            f"{hp(e[0])} : {e[1]} perd {fm(e[2])} TDC, {e[4]} [{t}] ({fm(e[6])} TDC) gagne exactement {fm(e[2])}" for t, e in nouvelles[:8]))
    st["alertes_all"] = sorted(vues)[-3000:]
    return f"{len(tags)} alliance(s) : " + ", ".join(tags)


def AUTOTEST():
    """Base synthétique : une prise TAXE sur un membre ANGE, un départ, une arrivée."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tdc (version TEXT, pseudo TEXT, alliance TEXT, tdc INTEGER)")
    base = datetime(2026, 11, 1, 0, 2)
    for k in range(60):
        v = (base + timedelta(hours=k)).strftime("%Y%m%d%H%M")
        rows = [(v, "Vic", "ANGE", 100000 - (61033 if k >= 30 else 0)), (v, "Tx1", "TAXE", 100000 + (61033 if k >= 30 else 0)),
                (v, "Tx2", "TAXE", 80000 + 10 * k), (v, "Ext", "XX", 50000)]
        if k < 20:
            rows.append((v, "Mov", "TAXE", 70000))
        else:
            rows.append((v, "Mov", "XX", 70000))
        conn.executemany("INSERT INTO tdc VALUES (?,?,?,?)", rows)
    versions, data = [], {}
    for v, p, a, t in conn.execute("SELECT version, pseudo, alliance, tdc FROM tdc ORDER BY version"):
        if v not in data:
            data[v] = {}
            versions.append(v)
        data[v][p] = (a, t)
    pre = [(versions[30], "Vic", 61033, 100000, "Tx1", "TAXE", 100000, "SÛRE")]
    res = analyser(versions, data, set(), ["TAXE", "AAA"], pre)
    assert len(res["TAXE"]["prises"]) == 1 and res["TAXE"]["dep"], "analyse alliance"
    lg, sty = feuille_alliance("TAXE", versions, data, res["TAXE"], evolution(conn, ["TAXE", "AAA"], versions[-1]), None)
    assert any("TAXE" in r[0] for r in lg)
    lg2, _ = feuille_alliance("AAA", versions, data, res["AAA"], {}, None)
    assert "Aucun joueur" in lg2[-1][0]


if __name__ == "__main__":
    AUTOTEST()
    print("ok")

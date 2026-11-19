"""ange_accueil.py : page d'accueil du Suivi ANGE (liens vers tous les onglets) et rangement automatique des onglets.

L'onglet « Accueil » est toujours le premier. Il est recréé à chaque relevé à partir de la liste réelle des onglets :
une nouvelle alliance ou une nouvelle journée apparaît toute seule. Chaque onglet porte un lien « 🏠 Accueil » en haut à gauche.
"""
import re

ORDRE = [
    ("SYNTHÈSE ET SUIVI AUTOMATIQUE (VPS)", [
        ("Récap", "résumé du jour : chiffres clés, chasseurs, fuites, forts et faibles"),
        ("Classement", "maillons forts et faibles, score de chaque membre"),
        ("Chasse", "TDC chassé par chaque chasseur et par jour"),
        ("Chasse horaire", "à quelles heures chaque chasseur chasse"),
        ("Fuites", "TDC perdu par ANGE : preuves, victimes, gagnants"),
        ("Attaquants", "qui nous prend du TDC, classé par joueur et par alliance"),
        ("Activité attaques", "attaques par jour et heures d'envoi de chaque membre"),
        ("Attaques détail", "qui a attaqué qui, temps de trajet, heure d'envoi estimée, VA et positions manquantes"),
        ("Heure par heure", "chaque heure : TDC avant/après de chaque joueur et ce qui reste inexpliqué"),
        ("Pertes à élucider", "pertes de TDC qu'aucun membre ANGE ne peut expliquer, par joueur"),
        ("Chasse heure par heure", "net d'ANGE = chasse de l'heure : chasse vue, chasse retrouvée, reste inexpliqué")]),
    ("SUIVI DES JOUEURS (à la main)", [
        ("Suivi joueurs", "joueurs à surveiller, statut, excuses"),
        ("Historique soucis", "historique des problèmes constatés"),
        ("Journal contacts", "qui a contacté qui, et la réponse")]),
    ("AUTRES ALLIANCES", [("Alliances", "réglage : écris un TAG par ligne pour suivre une alliance")]),
    ("CHAÎNE (Apps Script)", [
        ("Alertes", "alertes de convois en cours"),
        ("Convois", "convois par livreur"),
        ("Échelle", "postes de la chaîne et joueurs classés par TDC"),
        ("Places", "joueurs par rapport à leur place"),
        ("Activité", "activité de chaque joueur sur 8 jours"),
        ("Activité hebdo", "note d'activité par semaine"),
        ("Hebdo données", "données de l'activité hebdo")]),
    ("DONNÉES ET RÉGLAGES", [
        ("Historique TDC", "relevés de TDC du suivi Apps Script"),
        ("Flux TDC", "flux de TDC"),
        ("Bilan horaire", "bilan par heure"),
        ("Réglages", "réglages du suivi Apps Script"),
        ("Requête", "boîte aux lettres du VPS : requêtes SQL")]),
]


def ordre_voulu(noms):
    """Liste complète des onglets dans l'ordre voulu."""
    res = ["Accueil"]
    deja = {"Accueil"}
    for titre, items in ORDRE:
        for nom, _ in items:
            if nom in noms and nom not in deja:
                res.append(nom)
                deja.add(nom)
        if titre.startswith("AUTRES ALLIANCES"):
            for n in sorted(x for x in noms if x.startswith("Alliance ") and x not in deja):
                res.append(n)
                deja.add(n)
    jours = sorted((n for n in noms if re.match(r"^Jour \d\d-\d\d$", n) and n not in deja), key=lambda n: (n[8:10], n[5:7]), reverse=True)
    res += jours
    deja.update(jours)
    res += [n for n in noms if n not in deja and not re.match(r"^R\d+$", n)]
    res += [n for n in noms if re.match(r"^R\d+$", n) and n not in deja]
    return res


def separateur(sh):
    """Séparateur d'arguments des formules selon la langue du fichier (« ; » en français, « , » en anglais)."""
    try:
        loc = sh.fetch_sheet_metadata().get("properties", {}).get("locale", "en_US")
    except Exception:
        loc = "fr_FR"
    return "," if str(loc).lower().startswith(("en", "ja", "zh", "ko", "th", "he")) else ";"


def lien(gid, texte, sep=","):
    return '=HYPERLINK("#gid=%d"%s"%s")' % (gid, sep, texte.replace('"', "'"))


def preparer(sh):
    """Crée l'onglet Accueil (en tête) s'il manque ; retourne son identifiant (gid)."""
    import gspread
    try:
        return sh.worksheet("Accueil").id
    except gspread.WorksheetNotFound:
        return sh.add_worksheet("Accueil", rows=80, cols=6, index=0).id


def ecrire(sh):
    ws = sh.worksheet("Accueil")
    sep = separateur(sh)
    feuilles = {w.title: w.id for w in sh.worksheets()}
    L, S = [], []

    def add(row, typ=None):
        L.append(list(row) + [""] * (6 - len(row)))
        if typ:
            S.append((len(L), typ))

    add(["🏠 Suivi ANGE : accueil"], "titre")
    add(["Clique sur le nom d'un onglet pour l'ouvrir. Chaque onglet a un lien « 🏠 Accueil » en haut à gauche pour revenir ici."], "sous")
    add([""])
    deja = {"Accueil"}
    for titre, items in ORDRE:
        add([titre, "", "", "", "", ""], "section")
        for nom, desc in items:
            if nom in feuilles:
                add([lien(feuilles[nom], nom, sep), desc])
                deja.add(nom)
        if titre.startswith("AUTRES ALLIANCES"):
            for nom in sorted(x for x in feuilles if x.startswith("Alliance ") and x not in deja):
                add([lien(feuilles[nom], nom, sep), "suivi automatique de l'alliance"])
                deja.add(nom)
        add([""])
    jours = [n for n in ordre_voulu(list(feuilles)) if re.match(r"^Jour \d\d-\d\d$", n)]
    if jours:
        add(["JOURNÉES (alertes et convois, de la plus récente à la plus ancienne)", "", "", "", "", ""], "section")
        for i in range(0, len(jours), 5):
            add([lien(feuilles[n], n, sep) for n in jours[i:i + 5]])
        deja.update(jours)
        add([""])
    autres = [n for n in feuilles if n not in deja and not re.match(r"^R\d+$", n)]
    if autres:
        add(["AUTRES ONGLETS", "", "", "", "", ""], "section")
        for n in autres:
            add([lien(feuilles[n], n, sep)])
    ws.clear()
    ws.resize(rows=max(len(L) + 2, 10), cols=6)
    ws.update(range_name="A1", values=L, value_input_option="USER_ENTERED")
    sid = ws.id
    reqs = [{"repeatCell": {"range": {"sheetId": sid}, "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat"}}]
    for i, w in enumerate([230, 300, 200, 200, 200, 100]):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                   "properties": {"pixelSize": w}, "fields": "pixelSize"}})

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
        reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": n - 1, "endRowIndex": n, "startColumnIndex": 0, "endColumnIndex": 6},
                                    "cell": {"userEnteredFormat": f}, "fields": ",".join(fl)}})

    for n, typ in S:
        if typ == "titre":
            fmt(n, bold=True, size=20)
        elif typ == "sous":
            fmt(n, color={"red": .42, "green": .45, "blue": .5})
        elif typ == "section":
            fmt(n, bg={"red": .12, "green": .16, "blue": .22}, bold=True, color={"red": 1, "green": 1, "blue": 1})
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"hideGridlines": True}}, "fields": "gridProperties.hideGridlines"}})
    sh.batch_update({"requests": reqs})


def ranger(sh):
    """Remet les onglets dans l'ordre voulu (seulement si l'ordre actuel est différent)."""
    ws = sh.worksheets()
    actuel = [w.title for w in ws]
    voulu = ordre_voulu(actuel)
    if voulu == actuel:
        return False
    ids = {w.title: w.id for w in ws}
    sh.batch_update({"requests": [{"updateSheetProperties": {"properties": {"sheetId": ids[n], "index": i}, "fields": "index"}} for i, n in enumerate(voulu)]})
    return True


def AUTOTEST():
    noms = ["Récap", "Requête", "Jour 10-11", "Jour 11-11", "Jour 30-10", "Alliance TAXE", "Alliances", "Accueil", "Alertes", "R18", "Truc", "Suivi joueurs"]
    o = ordre_voulu(noms)
    assert o[0] == "Accueil" and o[1] == "Récap" and sorted(o) == sorted(noms), o
    assert o.index("Jour 11-11") < o.index("Jour 10-11") < o.index("Jour 30-10"), o
    assert o.index("Alliances") < o.index("Alliance TAXE") < o.index("Alertes"), o
    assert o[-1] == "R18" and o.index("Truc") < o.index("R18"), o
    assert lien(5, "Test").startswith('=HYPERLINK("#gid=5"') and lien(5, "T", ";") == '=HYPERLINK("#gid=5";"T")'


if __name__ == "__main__":
    AUTOTEST()
    print("ok")

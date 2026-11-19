"""ange_convois_chasse.py : bloc « Ta chasse » des onglets chasseurs (port de ecrireChasse_ d'Apps Script).
Remplace, sur les onglets chasseurs, la liste « Tu peux attaquer » (les chasseurs n'attaquent personne : le bloc occupe A39:D113)."""
import math, re
import ange_convois as C
import ange_convois_pages as P

A_PREM = P.A_PREM
A_MAX = P.A_MAX
NB_JOURS = 8


def js_round(x):
    return int(math.floor(x + 0.5))


def fmt(n):
    s = str(js_round(n))
    neg = s.startswith("-")
    s = s.lstrip("-")
    out = ""
    while len(s) > 3:
        out = " " + s[-3:] + out
        s = s[:-3]
    return ("-" if neg else "") + s + out


def jour_texte(x):
    """En-tête de colonne tel qu'Apps Script l'affiche (getDisplayValues) : les dates arrivent en numéro de série, affichées « AAAA-MM-JJ »."""
    import datetime
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(x))).isoformat()
    t = str(x).strip()
    if re.fullmatch(r"\d{5}", t):                 # numéro de série reçu sous forme de texte
        return jour_texte(int(t))
    return t


class _Hist(dict):
    """Historique indexé en minuscules ; .get() ignore la casse du pseudo."""
    def get(self, k, d=None):
        return super().get(str(k).lower(), d)


def lire_historique(prive=None):
    """{pseudo: [{'jour': 'AAAA-MM-JJ', 'tdc': TDC chassé ce jour-là}, ...]} sur les NB_JOURS derniers jours COMPLETS.
    Source : chasse_jour.json, écrit à chaque relevé par ange_suivi (chasse estimée = hausses + chasses cachées derrière une attaque)."""
    import json, os
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chasse_jour.json")
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        return _Hist()
    auj = d.get("aujourdhui", "")
    jours = sorted({j for v in d.get("jours", {}).values() for j in v if j < auj})[-NB_JOURS:]
    res = _Hist()
    for p, v in d.get("jours", {}).items():
        l = [{"jour": j, "tdc": v.get(j, 0)} for j in jours]
        if any(x["tdc"] > 0 for x in l):
            res[p.lower()] = l
    return res


def _rgb(h):
    return {"red": int(h[1:3], 16) / 255, "green": int(h[3:5], 16) / 255, "blue": int(h[5:7], 16) / 255}


def _cell(v=None, bold=None, size=None, fg=None, bg=None, nf=None, centre=False):
    c = {}
    if v is not None:
        c["userEnteredValue"] = {"numberValue": v} if isinstance(v, (int, float)) else {"stringValue": v}
    f = {}
    t = {}
    if bold is not None: t["bold"] = bold
    if size: t["fontSize"] = size
    if fg: t["foregroundColor"] = _rgb(fg)
    if t: f["textFormat"] = t
    if bg: f["backgroundColor"] = _rgb(bg)
    if nf: f["numberFormat"] = {"type": "NUMBER", "pattern": nf}
    if centre: f["horizontalAlignment"] = "CENTER"
    if f: c["userEnteredFormat"] = f
    return c


def lignes_chasse(historique_joueur):
    """Contenu logique du bloc : retourne dict(titre, resume, rows) — rows = [[jour, chassé, écart vs veille]]"""
    l = (historique_joueur or [])[-NB_JOURS:]
    titre = "🏹 Ta chasse (TDC chassé par jour)"
    if not l:
        return {"titre": titre, "resume": "Historique en cours de constitution (premier relevé cette nuit).", "rows": []}
    tot = sum(x["tdc"] for x in l)
    resume = "Total sur %d jour%s : %s TDC chassés · moyenne %s TDC/jour" % (len(l), "s" if len(l) > 1 else "", fmt(tot), fmt(tot / len(l)))
    rows = [[x["jour"], js_round(x["tdc"]), "" if i == 0 else js_round(x["tdc"] - l[i - 1]["tdc"])] for i, x in enumerate(l)]
    return {"titre": titre, "resume": resume, "rows": rows}


def _mediane(l):
    l = sorted(l)
    n = len(l)
    if not n:
        return 0
    return l[n // 2] if n % 2 else (l[n // 2 - 1] + l[n // 2]) / 2


def _pente(l):
    """Écart entre la moyenne des jours récents et celle des jours plus anciens (TDC chassés par jour)."""
    n = len(l) // 2
    if n < 1:
        return 0
    return sum(x["tdc"] for x in l[-n:]) / n - sum(x["tdc"] for x in l[:n]) / n


def stats_groupe(historique, chasseurs):
    """Repères ANONYMES du groupe des chasseurs actuels (jamais de noms, jamais de classement) :
    chasse moyenne par jour de chacun sur 8 jours -> min / médiane / max ; tendance médiane ; médiane du groupe pour chaque jour."""
    moys, tends, par_jour = [], [], {}
    for p in chasseurs:
        h = (historique or {}).get(p) or []
        if not h:
            continue
        w = h[-NB_JOURS:]
        moys.append(sum(x["tdc"] for x in w) / len(w))
        tends.append(_pente(w))
        for x in h:
            par_jour.setdefault(x["jour"], []).append(x["tdc"])
    if not moys:
        return {"n": 0}
    return {"n": len(moys), "min": min(moys), "med": _mediane(moys), "max": max(moys), "tend_med": _mediane(tends),
            "par_jour": {j: _mediane(v) for j, v in par_jour.items()}}


def panneau(historique_joueur, groupe):
    """Récap de chasse affiché à droite de « Tu reçois de » : lignes [libellé, toi, groupe] + phrases. None sans historique."""
    l = (historique_joueur or [])[-NB_JOURS:]
    if not l:
        return None
    moy = sum(x["tdc"] for x in l) / len(l)
    tend = _pente(l)
    hier = l[-1]["tdc"]
    meilleur = max(x["tdc"] for x in l)
    g = groupe if groupe and groupe.get("n", 0) >= 5 else None
    pj = g["par_jour"].get(l[-1]["jour"]) if g else None
    lignes = [["Chasse moyenne / jour", js_round(moy), js_round(g["med"]) if g else ""],
              ["Dernier jour complet", js_round(hier), js_round(pj) if pj else ""],
              ["Meilleur jour (%d j)" % len(l), js_round(meilleur), ""],
              ["Tendance (jours récents - anciens)", js_round(tend), js_round(g["tend_med"]) if g else ""],
              ["Prévision sur 7 jours", js_round(moy * 7), js_round(g["med"] * 7) if g else ""]]
    if g:
        fourchette = "Fourchette des chasseurs : de %s à %s TDC chassés par jour en moyenne (8 j)" % (fmt(g["min"]), fmt(g["max"]))
        r = moy / g["med"] if g["med"] else 1
        if r >= 1.15:
            situ = "📈 Tu chasses plus que la médiane du groupe."
        elif r >= 0.85:
            situ = "👍 Tu es dans la moyenne du groupe."
        else:
            situ = "🌱 Tu chasses un peu moins que la médiane du groupe."
        if tend > g["tend_med"] + 1:
            situ += " Tu progresses plus vite que le groupe."
        elif tend < g["tend_med"] - 1:
            situ += " Ta progression est plus lente que celle du groupe."
        else:
            situ += " Ta progression suit celle du groupe."
    else:
        fourchette, situ = "", "Pas encore assez de chasseurs avec un historique pour afficher un repère de groupe."
    return {"lignes": lignes, "fourchette": fourchette, "situation": situ,
            "note": "Pas de classement : ces repères servent à se situer et à progresser, chacun à son rythme."}


def spec_graphique(sheet_id):
    """Courbe : ton TDC chassé par jour (B) et la médiane des chasseurs (D), lignes 40 (en-tête) à 48 de l'onglet."""
    def src(c0):
        return {"sourceRange": {"sources": [{"sheetId": sheet_id, "startRowIndex": A_PREM - 2, "endRowIndex": A_PREM - 2 + NB_JOURS + 1,
                                              "startColumnIndex": c0, "endColumnIndex": c0 + 1}]}}
    return {"title": "TDC chassé par jour : toi et la médiane des chasseurs",
            "basicChart": {"chartType": "LINE", "legendPosition": "BOTTOM_LEGEND", "headerCount": 1,
                           "axis": [{"position": "BOTTOM_AXIS"}, {"position": "LEFT_AXIS"}],
                           "domains": [{"domain": src(0)}],
                           "series": [{"series": src(1), "targetAxis": "LEFT_AXIS", "color": _rgb("#0284c7"), "pointStyle": {"size": 5}},
                                      {"series": src(3), "targetAxis": "LEFT_AXIS", "color": _rgb("#9ca3af"), "lineStyle": {"type": "MEDIUM_DASHED"}}]},
            "hiddenDimensionStrategy": "SKIP_HIDDEN_ROWS"}


def ids_graphiques(ss):
    """{sheetId: chartId} du premier graphique de chaque onglet (lecture seule)."""
    j = C.avec_reprise(lambda: ss.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s" % ss.id,
                                                  params={"fields": "sheets(properties(sheetId),charts(chartId))"}).json())
    return {s["properties"]["sheetId"]: s["charts"][0]["chartId"] for s in j.get("sheets", []) if s.get("charts")}


def requetes_chasse(sheet_id, historique_joueur, groupe=None, chart_id=None):
    b = A_PREM - 4          # index (base 0) de la ligne A_PREM-3 (titre)
    g = lambda r0, c0, r1, c1: {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}
    d = lignes_chasse(historique_joueur)
    pan = panneau(historique_joueur, groupe)
    ok_groupe = bool(groupe and groupe.get("n", 0) >= 5)
    
    F_VBG = "userEnteredValue,userEnteredFormat.backgroundColor"
    q = [
        # un chasseur ne livre rien : on masque « Tes livraisons du jour » (lignes 7 à 22) ; « Tu reçois de » remonte juste sous l'en-tête
        {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 6, "endIndex": 22},
                                       "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}},
        {"repeatCell": {"range": g(b + 1, 0, b + 1 + A_MAX + 3, 4), "cell": {}, "fields": F_VBG}},
        {"repeatCell": {"range": g(22, 5, 37, 11), "cell": {}, "fields": "userEnteredValue,userEnteredFormat"}},    # F23:K37 : récap de chasse
    ]

    def uc(r, c, cells, fields):
        return {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": r, "columnIndex": c}, "rows": [{"values": cells}], "fields": fields}}
    F_TXT = "userEnteredValue,userEnteredFormat.textFormat,userEnteredFormat.backgroundColor,userEnteredFormat.horizontalAlignment,userEnteredFormat.numberFormat"
    q.append(uc(b, 0, [_cell(d["titre"], size=18, bg="#e0f2fe", fg="#075985")],
                "userEnteredValue,userEnteredFormat.textFormat.fontSize,userEnteredFormat.textFormat.foregroundColor,userEnteredFormat.backgroundColor"))
    if pan:
        q.append(uc(22, 5, [_cell("🏹 Ta chasse en un coup d'œil", bold=True, size=12)], "userEnteredValue,userEnteredFormat.textFormat"))
        q.append(uc(23, 5, [_cell(t, bold=True, fg="#ffffff", bg="#1f2937", centre=(i > 0)) for i, t in enumerate(("Repère", "Toi", "Groupe*"))], F_TXT))
        for k, (lib, toi, grp) in enumerate(pan["lignes"]):
            q.append(uc(24 + k, 5, [_cell(lib), _cell(toi, nf="#,##0", centre=True),
                                     _cell(grp, nf="#,##0", centre=True) if grp != "" else _cell(centre=True)], F_TXT))
        if pan["fourchette"]:
            q.append(uc(29, 5, [_cell(pan["fourchette"], bold=True)], "userEnteredValue,userEnteredFormat.textFormat"))
        q.append(uc(31, 5, [_cell(pan["situation"], bold=True, size=11, fg="#075985")], "userEnteredValue,userEnteredFormat.textFormat"))
        q.append(uc(32, 5, [_cell(pan["note"], fg="#6b7280")], "userEnteredValue,userEnteredFormat.textFormat"))
        q.append(uc(33, 5, [_cell("* Groupe = médiane des chasseurs de la chaîne (aucun nom, aucun classement).", fg="#6b7280")], "userEnteredValue,userEnteredFormat.textFormat"))
    if not d["rows"]:
        q.append(uc(b + 1, 0, [_cell(d["resume"])], "userEnteredValue"))
        return q
    q.append(uc(b + 1, 0, [_cell(d["resume"], bold=True)], "userEnteredValue,userEnteredFormat.textFormat.bold"))
    q.append(uc(b + 2, 0, [_cell(t, bold=True, fg="#ffffff", bg="#1f2937") for t in ("Jour", "TDC chassé", "Écart vs veille", "Médiane des chasseurs")],
                "userEnteredValue,userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.foregroundColor,userEnteredFormat.backgroundColor"))
    rows = []
    for jour, tdc, ecart in d["rows"]:
        med = js_round(groupe["par_jour"][jour]) if ok_groupe and groupe["par_jour"].get(jour) else ""
        rows.append({"values": [_cell(jour), _cell(tdc, nf="#,##0", centre=True),
                                _cell(ecart, nf="#,##0", centre=True) if ecart != "" else _cell(nf="#,##0", centre=True),
                                _cell(med, nf="#,##0", centre=True) if med != "" else _cell(nf="#,##0", centre=True)]})
    q.append({"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": b + 3, "columnIndex": 0}, "rows": rows,
                              "fields": "userEnteredValue,userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment"}})
    q.append(uc(b + 1, 5, [_cell("🛡 Ils peuvent te flood en ce moment", bold=True)], "userEnteredValue,userEnteredFormat.textFormat.bold"))
    spec = spec_graphique(sheet_id)
    if chart_id:
        q.append({"updateChartSpec": {"chartId": chart_id, "spec": spec}})
    else:
        q.append({"addChart": {"chart": {"spec": spec, "position": {"overlayPosition": {
            "anchorCell": {"sheetId": sheet_id, "rowIndex": 50, "columnIndex": 0}, "widthPixels": 520, "heightPixels": 260}}}}})
    return q


def AUTOTEST():
    assert fmt(42265.4) == "42 265" and fmt(-1234) == "-1 234" and fmt(8225) == "8 225" and js_round(-0.5) == 0 and js_round(2.5) == 3
    d = lignes_chasse([{"jour": "a", "tdc": 100}, {"jour": "b", "tdc": 140}, {"jour": "c", "tdc": 130}])
    assert d["resume"] == "Total sur 3 jours : 370 TDC chassés · moyenne 123 TDC/jour", d["resume"]
    assert d["rows"] == [["a", 100, ""], ["b", 140, 40], ["c", 130, -10]]
    assert jour_texte(46337) == "2026-11-11" and jour_texte("46337") == "2026-11-11" and jour_texte("2026-11-12") == "2026-11-12"
    assert lignes_chasse([])["rows"] == []
    assert lignes_chasse([{"jour": "a", "tdc": 5}])["resume"].startswith("Total sur 1 jour :")
    h = [{"jour": "a", "tdc": 100}, {"jour": "b", "tdc": 140}]
    r = requetes_chasse(7, h)
    assert r[0]["updateDimensionProperties"]["range"]["startIndex"] == 6 and r[0]["updateDimensionProperties"]["range"]["endIndex"] == 22   # livraisons masquées
    assert r[1]["repeatCell"]["range"]["startRowIndex"] == A_PREM - 3
    assert any("addChart" in x for x in r) and not any("updateChartSpec" in x for x in r)
    assert any("updateChartSpec" in x for x in requetes_chasse(7, h, None, 55))
    assert not any("addChart" in x for x in requetes_chasse(7, []))                   # sans historique : pas de graphique
    # groupe : 6 chasseurs, repères anonymes
    hist = {c: [{"jour": "j%d" % i, "tdc": base + 100 * i} for i in range(8)] for c, base in zip("abcdef", (1000, 2000, 3000, 4000, 5000, 6000))}
    g = stats_groupe(hist, list("abcdef"))
    assert g["n"] == 6 and g["min"] == 1350 and g["max"] == 6350 and g["med"] == 3850 and g["tend_med"] == 400, g
    assert g["par_jour"]["j0"] == 3500
    pn = panneau(hist["a"], g)
    assert pn["lignes"][0][:2] == ["Chasse moyenne / jour", 1350] and pn["lignes"][0][2] == 3850 and "moins que la médiane" in pn["situation"], pn
    assert "plus que la médiane" in panneau(hist["f"], g)["situation"] and "dans la moyenne" in panneau(hist["c"], g)["situation"]
    hh = _Hist({"abc": [1]})
    assert hh.get("ABC") == [1] and hh.get("x") is None and hh.get("x", 5) == 5
    assert panneau([], g) is None and "Pas encore assez" in panneau(hist["a"], {"n": 3})["situation"]
    s7 = spec_graphique(7)["basicChart"]
    assert s7["headerCount"] == 1 and len(s7["series"]) == 2
    return True

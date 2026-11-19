"""ange_convois_nuit.py : clôture de nuit des convois (port de journeeChaines_ d'Apps Script).
Partie 1 = logique pure (testée par AUTOTEST). Partie 2 = lectures / écritures Google Sheets.
SÉCURITÉ : toute écriture est refusée hors copies TEST, sauf si la config l'autorise explicitement (voir ange_convois.ecriture_prod_autorisee)."""
import math

C_PREM, C_MAX = 9, 12      # livraisons : lignes 9 à 20
R_PREM, R_MAX = 25, 12     # réceptions : lignes 25 à 36
DESC_JOUEUR = "Seules les cases jaunes sont modifiables"
TXT_PERSONNE = "Personne ne te livre."
TITRE_JOUR = "📦 Tes livraisons du jour"
TITRE_INACTIF = "📦 Pas de convoi pour le moment : ils démarreront dès que chacun sera à sa place."
PROFIL_URL = "https://s5.fourmizzz.fr/Membre.php?Pseudo="
POSTES = ["Grenier 1", "Grenier 2", "Grenier 3", "Grenier 4", "Grenier 5", "Passeur 1", "Passeur 2", "Passeur 3", "Passeur 4",
          "Passeur 5", "Passeur 6", "Passeur 7", "Passeur 8", "Chasseur"]
FICHIERS = {"3.1": "Greniers", "3.2": "Passeurs", "3.3": "Chasseurs"}


def groupe(poste):
    p = str(poste).strip()
    return "3.1" if p.startswith("Grenier") else "3.2" if p.startswith("Passeur") else "3.3"


def nb(v):
    """Comme nb_ d'Apps Script : nombre, ou '' si vide / non numérique."""
    if isinstance(v, bool):
        return ""
    if isinstance(v, (int, float)):
        return v
    t = "".join(str(v).split()).replace(" ", "").replace("\xa0", "").replace(",", ".")
    if t == "":
        return ""
    try:
        return float(t)
    except ValueError:
        return ""


def _n0(v):
    x = nb(v)
    return 0 if x == "" else x


def _i(x):
    return int(round(x)) if x == x else 0


# ---------------- Partie 1 : logique pure ----------------
def lire_lignes_onglet(rows):
    """rows : 12 lignes [A..J] de l'onglet joueur (valeurs brutes). Équivalent de lireLivraisons_."""
    out = []
    for r in rows:
        r = list(r) + [""] * (10 - len(r))
        nom = str(r[0]).strip()
        if not nom:
            continue
        du0 = _n0(r[1])
        retard = _n0(r[8])
        credit = (du0 == 0 and retard < 0)
        total = retard if credit else du0
        envoye = _n0(r[3]) + _n0(r[4]) + _n0(r[5]) + _n0(r[6])
        livre = du0 if (r[2] is True and not credit) else envoye
        out.append({"r": nom, "du": total, "retard": retard, "quota": 0 if credit else du0 - retard, "livre": livre, "reste": total - livre})
    return out


def trier_plan(lignes, dist):
    """Même tri que majConvois : total envoyé par expéditeur (décroissant), puis nom, puis distance."""
    total = {}
    for l in lignes:
        total[l["s"]] = total.get(l["s"], 0) + l["q"]
    return sorted(lignes, key=lambda l: (-total[l["s"]], l["s"].casefold(), dist(l["s"], l["r"])))


def plan_par_fichier(lignes, groupe_de):
    """{g: [lignes]} : une ligne apparaît dans le fichier de l'expéditeur ET dans celui du destinataire."""
    res = {g: [] for g in FICHIERS}
    for l in lignes:
        gs, gr = groupe_de.get(l["s"]), groupe_de.get(l["r"])
        if gs in res:
            res[gs].append(l)
        if gr in res and gr != gs:
            res[gr].append(l)
    return res


def calcul_fichier(actifs, plan_g, lus, jour, avec_cloture=True, envois_gardes=None, dist=None):
    """actifs : pseudos du fichier. plan_g : lignes {s,r,q}. lus : {pseudo: 12 lignes lues avant écriture}.
    Retourne (journal, ecritures) ; ecritures[pseudo] = {"lignes": [...], "recus": [...]}."""
    journal, report = [], {}
    if avec_cloture:
        for p in actifs:
            for l in lire_lignes_onglet(lus.get(p, [])):
                journal.append([jour, p, l["r"], l["quota"], l["retard"], l["du"], l["livre"], l["reste"]])
                if l["reste"] != 0:
                    report.setdefault(p, {})[l["r"]] = l["reste"]
    quotas, recus = {}, {}
    for c in plan_g:
        quotas.setdefault(c["s"], {})
        quotas[c["s"]][c["r"]] = quotas[c["s"]].get(c["r"], 0) + c["q"]
        lst = recus.setdefault(c["r"], [])
        deja = next((x for x in lst if x["s"] == c["s"]), None)
        if deja:
            deja["q"] += c["q"]
        else:
            lst.append({"s": c["s"], "q": c["q"]})
    ecr = {}
    for p in actifs:
        q, rep = quotas.get(p, {}), report.get(p, {})
        ex = (envois_gardes or {}).get(p, {})
        dests = list(q) + [r for r in rep if r not in q]
        dests += [r for r in ex if r not in dests]
        lignes = []
        for r in dests:
            rp = rep.get(r, 0)
            total = q.get(r, 0) + rp
            l = {"r": r, "du": total, "retard": rp} if total >= 0 else {"r": r, "du": 0, "retard": total}
            if r in ex:
                l["env"] = ex[r]
            if l["du"] > 0 or l["retard"] < 0 or l.get("env"):
                lignes.append(l)
        def _d(autre):
            v = dist(p, autre) if dist else ""
            return 1e9 if v in ("", None) else v
        if dist:                                       # rangé par distance croissante (puis plus gros dû)
            lignes.sort(key=lambda l: (_d(l["r"]), -l["du"]))
            rec = sorted(recus.get(p, []), key=lambda c: (_d(c["s"]), -c["q"]))
        else:
            lignes.sort(key=lambda l: -l["du"])           # tri stable, comme Array.sort
            rec = sorted(recus.get(p, []), key=lambda c: -c["q"])
        ecr[p] = {"lignes": lignes[:C_MAX], "recus": rec[:R_MAX], "trop": len(lignes) > C_MAX}
    return journal, ecr


def requetes_onglet(sheet_id, p, e, titre, dist, jour_serie=None):
    """Requêtes batchUpdate (API Sheets) qui réécrivent l'onglet d'un joueur. N'écrit jamais la colonne H ni J (formules)."""
    def cel_texte(txt, lien=None):
        c = {"userEnteredValue": {"stringValue": txt}}
        if lien:
            c["textFormatRuns"] = [{"startIndex": 0, "format": {"link": {"uri": lien}}}]
        return c

    def rng(r0, r1, c0, c1):
        return {"sheetId": sheet_id, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}

    lignes, recus = e["lignes"], e["recus"]
    rows, ret, dis = [], [], []
    for i in range(C_MAX):
        if i < len(lignes):
            l = lignes[i]
            env = l.get("env")
            a = cel_texte(l["r"], PROFIL_URL + _quote(l["r"]))
            b = {"userEnteredValue": {"numberValue": l["du"]}}
            c = {"userEnteredValue": {"boolValue": bool(env and env.get("c"))}, "dataValidation": {"condition": {"type": "BOOLEAN"}}}
            ev = (env or {}).get("e") or ["", "", "", ""]
            d4 = [({"userEnteredValue": {"numberValue": x}} if nb(x) != "" else {}) for x in ev]
            rows.append({"values": [a, b, c] + d4})
            ret.append({"values": [{"userEnteredValue": {"numberValue": l["retard"]}}]})
            d = dist(p, l["r"])
            dis.append({"values": [{"userEnteredValue": {"numberValue": d}} if d != "" else {}]})
        else:
            rows.append({"values": [{}] * 7})
            ret.append({"values": [{}]})
            dis.append({"values": [{}]})
    req = [
        {"updateCells": {"range": rng(C_PREM - 1, C_PREM - 1 + C_MAX, 0, 7), "rows": rows, "fields": "userEnteredValue,textFormatRuns,dataValidation"}},
        {"updateCells": {"range": rng(C_PREM - 1, C_PREM - 1 + C_MAX, 8, 9), "rows": ret, "fields": "userEnteredValue"}},
        {"updateCells": {"range": rng(C_PREM - 1, C_PREM - 1 + C_MAX, 10, 11), "rows": dis, "fields": "userEnteredValue"}},
        {"updateCells": {"range": rng(6, 7, 0, 1), "rows": [{"values": [{"userEnteredValue": {"stringValue": titre}}]}], "fields": "userEnteredValue"}},
    ]
    rrows, rdis = [], []
    for i in range(R_MAX):
        if i < len(recus):
            c = recus[i]
            rrows.append({"values": [cel_texte(c["s"], PROFIL_URL + _quote(c["s"])), {"userEnteredValue": {"numberValue": c["q"]}}]})
            d = dist(p, c["s"])
            rdis.append({"values": [{"userEnteredValue": {"numberValue": d}} if d != "" else {}]})
        elif i == 0:
            rrows.append({"values": [cel_texte(TXT_PERSONNE), {}]})
            rdis.append({"values": [{}]})
        else:
            rrows.append({"values": [{}, {}]})
            rdis.append({"values": [{}]})
    req.append({"updateCells": {"range": rng(R_PREM - 1, R_PREM - 1 + R_MAX, 0, 2), "rows": rrows, "fields": "userEnteredValue,textFormatRuns"}})
    req.append({"updateCells": {"range": rng(R_PREM - 1, R_PREM - 1 + R_MAX, 2, 3), "rows": rdis, "fields": "userEnteredValue"}})
    return req


def _quote(s):
    from urllib.parse import quote
    return quote(str(s), safe="")


def AUTOTEST():
    # lecture d'un onglet : coché = exactement le dû ; envois notés ; crédit
    rows = [["A", 1000, True, "", "", "", "", 0, 0],
            ["B", 500, False, 200, 100, "", "", 0, 0],
            ["C", 0, False, "", "", "", "", 0, -300],
            ["D", 800, False, "", "", "", "", 0, 200]]
    L = {l["r"]: l for l in lire_lignes_onglet(rows)}
    assert L["A"]["livre"] == 1000 and L["A"]["reste"] == 0
    assert L["B"]["livre"] == 300 and L["B"]["reste"] == 200
    assert L["C"]["du"] == -300 and L["C"]["quota"] == 0 and L["C"]["reste"] == -300
    assert L["D"]["reste"] == 800 and L["D"]["quota"] == 600
    # nuit : le reste (retard) et le crédit sont reportés, le nouveau plan s'ajoute
    plan = [{"s": "X", "r": "B", "q": 400}, {"s": "X", "r": "C", "q": 100}, {"s": "Y", "r": "X", "q": 50}]
    lus = {"X": rows}
    j, e = calcul_fichier(["X", "Y"], plan, lus, "2026-11-12")
    d = {l["r"]: l for l in e["X"]["lignes"]}
    assert d["B"]["du"] == 600 and d["B"]["retard"] == 200
    assert d["C"]["du"] == 0 and d["C"]["retard"] == -200      # 100 du jour - 300 de crédit = -200
    assert d["D"]["du"] == 800 and d["D"]["retard"] == 800      # pas dans le plan : seul le report
    assert "A" not in d                                          # tout livré, rien de nouveau
    assert [r["s"] for r in e["X"]["recus"]] == ["Y"] and e["Y"]["recus"] == []
    assert len(j) == 4
    # réécriture sans clôture : les envois gardés restent
    jd, ed = calcul_fichier(["X"], plan, {"X": rows}, "2026-11-12", dist=lambda a, b: {"B": 9, "C": 3}.get(b, 5))
    assert [l["r"] for l in ed["X"]["lignes"]] == ["C", "B", "D"] or [l["r"] for l in ed["X"]["lignes"]][0] == "C"
    j2, e2 = calcul_fichier(["X"], plan, lus, "2026-11-12", avec_cloture=False, envois_gardes={"X": {"B": {"c": False, "e": [150, "", "", ""], "du": 400}}})
    assert j2 == [] and {l["r"]: l for l in e2["X"]["lignes"]}["B"]["env"]["e"][0] == 150
    # requêtes : jamais la colonne H (7) ni J (9)
    for r in requetes_onglet(1, "X", e["X"], TITRE_JOUR, lambda a, b: 5):
        g = r["updateCells"]["range"]
        assert not (g["startColumnIndex"] <= 7 < g["endColumnIndex"] and g["startRowIndex"] >= 8 and g["endRowIndex"] <= 20)
        assert not (g["startColumnIndex"] <= 9 < g["endColumnIndex"] and g["startRowIndex"] >= 8 and g["endRowIndex"] <= 20)
    pf = plan_par_fichier(plan, {"X": "3.1", "Y": "3.2", "B": "3.2", "C": "3.3"})
    assert len(pf["3.1"]) == 3 and len(pf["3.2"]) == 2 and len(pf["3.3"]) == 1
    return True

# v4 tri distance

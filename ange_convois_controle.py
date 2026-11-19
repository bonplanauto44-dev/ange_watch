"""ange_convois_controle.py : contrôle « à envoyer = à recevoir » (port de controlerCoherenceConvois / controleHoraire d'Apps Script).
Lecture seule des 3 fichiers + écriture de l'onglet « Contrôle convois » du sheet privé. Alerte Discord si un écart dure plus d'un contrôle (pas de réécriture automatique)."""
import json, os
from datetime import datetime
from zoneinfo import ZoneInfo
import ange_convois as C
import ange_convois_pages as P

C_PREM, C_MAX, R_PREM, R_MAX = 9, 12, 25, 12  # 12 réceptions normales (A25:B36)
TXT_PERSONNE = "Personne ne te livre."
FICHIERS = {"greniers": "3.1", "passeurs": "3.2", "chasseurs": "3.3"}
ETAT_PROD = os.path.join(C.DOSSIER, "convois_controle_etat.json")
ETAT_TEST = os.path.join(C.DOSSIER, "convois_controle_etat_test.json")


def lire_onglets(fichiers, actifs_par_g):
    """{pseudo: (fichier, lignes livraisons A9:I20, lignes réceptions A25:B36)}"""
    out = {}
    for cle, ss in fichiers.items():
        g = FICHIERS[cle]
        titres = {w.title for w in C.avec_reprise(lambda: ss.worksheets())}
        noms = [p for p in actifs_par_g.get(g, []) if p in titres]
        for i in range(0, len(noms), 20):
            lot = noms[i:i + 20]
            rngs = []
            for p in lot:
                q = "'%s'!" % p.replace("'", "''")
                rngs += [q + "A%d:I%d" % (C_PREM, C_PREM + C_MAX - 1), q + "A%d:B%d" % (R_PREM, R_PREM + R_MAX - 1)]
            j = C.avec_reprise(lambda: ss.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s/values:batchGet" % ss.id,
                                                         params={"ranges": rngs, "valueRenderOption": "UNFORMATTED_VALUE"}).json())
            vr = j["valueRanges"]
            for k, p in enumerate(lot):
                out[p] = (g, vr[2 * k].get("values", []), vr[2 * k + 1].get("values", []))
    return out


def _n(v):
    x = P.nb(v)
    return x if x != "" else 0


def ecarts(onglets, redistrib_paires=None):
    """Retourne la liste [expéditeur, destinataire, à envoyer, à recevoir, écart, fichier exp., fichier dest.]
    redistrib_paires : set de (expediteur.lower(), destinataire.lower()) — paires redistrib ignorées par le contrôle."""
    if redistrib_paires is None:
        redistrib_paires = set()
    env, rec, actifs = {}, {}, {}
    for p, (g, liv, receptions) in onglets.items():
        actifs[p] = "Fichier " + g
        for r in liv:
            r = list(r) + [""] * (9 - len(r))
            nom = str(r[0]).strip()
            q = _n(r[1]) - _n(r[8])                 # sans le retard d'hier : le destinataire ne voit que le plan du jour
            if not nom or not (isinstance(r[1], (int, float)) and r[1] > 0) or q <= 0:
                continue
            if (p.lower(), nom.lower()) in redistrib_paires:
                continue                              # paire redistrib : ignorée par le contrôle
            env.setdefault(p, {})[nom] = env.get(p, {}).get(nom, 0) + q
        for r in receptions:
            r = list(r) + [""] * (2 - len(r))
            nom = str(r[0]).strip()
            if not nom or nom == TXT_PERSONNE:
                continue
            if (nom.lower(), p.lower()) in redistrib_paires:
                continue                              # paire redistrib : ignorée par le contrôle
            rec.setdefault(p, {})[nom] = rec.get(p, {}).get(nom, 0) + _n(r[1])
    res, vus = [], set()
    for s in env:
        for r in env[s]:
            vus.add((s, r))
            vu = rec.get(r, {}).get(s, 0)
            if abs(vu - env[s][r]) > 1:
                res.append([s, r, env[s][r], vu, vu - env[s][r], actifs.get(s, "?"), actifs.get(r, "(sans onglet)")])
    for r in rec:
        for s in rec[r]:
            if (s, r) in vus:
                continue
            if abs(rec[r][s]) > 1:
                res.append([s, r, 0, rec[r][s], rec[r][s], actifs.get(s, "(sans onglet)"), actifs.get(r, "?")])
    return res


def _fmt(x):
    return "{:,.0f}".format(x).replace(",", " ")


def livraisons_anormales(onglets):
    """[expéditeur, destinataire, dû, livré] quand ce qui est noté livré dépasse nettement ce qui est dû
    (plus de 1,5 × le dû ET plus de 1 M d'écart ; ou plus de 1 M livré alors que rien n'est dû).
    « Tout envoyé » coché = livré égal au dû (jamais anormal). Sinon livré = somme des envois 1 à 4."""
    out = []
    for p, (g, liv, _rec) in onglets.items():
        for r in liv:
            r = list(r) + [""] * (9 - len(r))
            nom = str(r[0]).strip()
            if not nom:
                continue
            du = r[1] if isinstance(r[1], (int, float)) and not isinstance(r[1], bool) else 0
            if r[2] is True:
                continue
            livre = sum(x for x in r[3:7] if isinstance(x, (int, float)) and not isinstance(x, bool))
            if livre - du > 1000000 and (livre > 1.5 * du):
                out.append([p, nom, du, livre])
    return out


def requetes_onglet(sheet_id, liste, maintenant):
    quand = maintenant.strftime("%d/%m %H:%M")
    def rows(vals, **f):
        out = []
        for v in vals:
            if isinstance(v, (int, float)):
                out.append({"userEnteredValue": {"numberValue": v}})
            elif v == "":
                out.append({})
            else:
                out.append({"userEnteredValue": {"stringValue": str(v)}})
        return {"values": out}
    q = [{"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1000, "startColumnIndex": 0, "endColumnIndex": 8},
                         "cell": {}, "fields": "userEnteredValue,userEnteredFormat"}}]
    bold = {"userEnteredFormat": {"textFormat": {"bold": True}}}
    def cell(v, gras=False):
        c = {"userEnteredValue": {"stringValue": v}}
        if gras:
            c.update(bold)
        return c
    q.append({"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
                              "rows": [{"values": [cell("Contrôle de cohérence des convois (à envoyer = à recevoir) · " + quand, True)]},
                                       {"values": [cell("✅ Aucun écart : tout ce qui est à envoyer est bien à recevoir." if not liste else
                                                        "⚠ %d écart(s) : un fichier est resté sur un ancien plan. Selon le fichier concerné (3.1 Greniers, 3.2 Passeurs, 3.3 Chasseurs), demande une réécriture." % len(liste))]}],
                              "fields": "userEnteredValue,userEnteredFormat.textFormat.bold"}})
    if liste:
        ent = ["Expéditeur", "Destinataire", "À envoyer (vu par l'expéditeur)", "À recevoir (vu par le destinataire)", "Écart", "Fichier expéditeur", "Fichier destinataire"]
        lignes = [{"values": [cell(t, True) for t in ent]}] and [{"values": [cell(t, True) for t in ent]}]
        lignes += [rows(l) for l in liste]
        q.append({"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": 2, "columnIndex": 0}, "rows": lignes,
                                  "fields": "userEnteredValue,userEnteredFormat.textFormat.bold"}})
        q.append({"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 3, "endRowIndex": 3 + len(liste), "startColumnIndex": 2, "endColumnIndex": 5},
                                 "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}}, "fields": "userEnteredFormat.numberFormat"}})
    return q


def _charger_redistrib_paires():
    """Charge dettes_redistrib.json et retourne set((expediteur.lower(), destinataire.lower())) actives.
    Retourne un set vide si redistrib inactive ou fichier absent."""
    try:
        import ange_convois_redistrib as R
        if not os.path.exists(R.REDISTRIB_JSON):
            return set()
        suivi = json.load(open(R.REDISTRIB_JSON, encoding="utf-8"))
        if not suivi.get("actif"):
            return set()
        paires = set()
        crediteurs = [c.lower() for c in suivi.get("crediteurs", [])]
        for grenier in suivi.get("debiteurs", {}):
            for cred in crediteurs:
                paires.add((grenier.lower(), cred))
        return paires
    except Exception as ex:
        print("_charger_redistrib_paires:", ex, flush=True)
        return set()


def controler(prive, fichiers, ctx, dossier, test, alerter=None):
    actifs = {}
    for m in ctx["membres"]:
        if not m["hors"] or m["attaqueSeule"]:
            actifs.setdefault(m["g"], []).append(m["pseudo"])
    onglets = lire_onglets(fichiers, actifs)
    redistrib_paires = _charger_redistrib_paires()
    liste = ecarts(onglets, redistrib_paires)
    try:
        bizarres = livraisons_anormales(onglets)
    except Exception as ex:
        print("livraisons_anormales :", ex, flush=True)
        bizarres = []
    titres = {w.title: w.id for w in C.avec_reprise(lambda: prive.worksheets())}
    if "Contrôle convois" not in titres:
        raise RuntimeError("onglet Contrôle convois introuvable")
    C.avec_reprise(lambda: prive.batch_update({"requests": requetes_onglet(titres["Contrôle convois"], liste, datetime.now(ZoneInfo("Europe/Paris")))}), essais=3, pause=20)
    fichier = ETAT_TEST if test else ETAT_PROD
    try:
        etat = json.load(open(fichier))
    except Exception:
        etat = {}
    sig = "|".join(sorted("%s>%s:%s" % (e[0], e[1], e[4]) for e in liste))
    if liste and sig == etat.get("sig"):
        etat["n"] = etat.get("n", 1) + 1
    else:
        etat["n"] = 1 if liste else 0
    etat["sig"] = sig
    if liste and etat["n"] == 2 and alerter and not test:           # même écart sur deux contrôles de suite (≈ 1 h)
        e = liste[0]
        alerter("⚠️ Chaînes ANGE : %d convoi(s) désynchronisé(s) depuis plus d'une heure. Exemple : %s → %s, envoie %s, reçoit %s. Détail : onglet « Contrôle convois »." % (len(liste), e[0], e[1], e[2], e[3]))
    # livraisons anormales (faute de frappe probable) : alerte dès la première détection, une seule fois par valeur
    deja = set(etat.get("bizarres", []))
    cles = ["%s>%s:%d" % (b[0], b[1], b[3]) for b in bizarres]
    nouveaux = [b for b, k in zip(bizarres, cles) if k not in deja]
    if nouveaux and alerter and not test:
        txt = "\n".join("• %s → %s : livré %s pour %s dû (×%.1f)" % (b[0], b[1], _fmt(b[3]), _fmt(b[2]), b[3] / b[2] if b[2] else 0) for b in nouveaux[:8])
        alerter("⚠️ Chaînes ANGE : livraison(s) anormale(s), faute de frappe probable (zéro en trop ?). À corriger dans l'onglet de l'expéditeur :\n" + txt)
    etat["bizarres"] = cles                       # on oublie celles qui sont corrigées
    json.dump(etat, open(fichier, "w"))
    return {"écarts": len(liste), "livraisons anormales": len(bizarres)}


def AUTOTEST():
    liv_a = [["B", 100, "", "", "", "", "", "", 20], ["C", 50, "", "", "", "", "", "", 0], ["", "", ""]]
    rec_b = [["A", 80], [TXT_PERSONNE, ""]]
    rec_c = [["A", 40]]
    r = ecarts({"A": ("3.1", liv_a, []), "B": ("3.2", [], rec_b), "C": ("3.2", [], rec_c)})
    assert r == [["A", "C", 50, 40, -10, "Fichier 3.1", "Fichier 3.2"]], r
    r2 = ecarts({"B": ("3.2", [], [["Z", 30]])})
    assert r2 == [["Z", "B", 0, 30, 30, "(sans onglet)", "Fichier 3.2"]], r2
    assert ecarts({"A": ("3.1", [["B", 10.0, "", "", "", "", "", "", 0]], []), "B": ("3.2", [], [["A", 10]])}) == []
    ang = livraisons_anormales({
        "breto": ("3.1", [["Cylo", 9913601, False, 73936010, "", "", "", "", 0],           # ×7,5 : anormal
                          ["Bomric", 6460157, False, 7000000, "", "", "", "", 0],            # léger dépassement : normal
                          ["Shaka", 100, True, 99999999, "", "", "", "", 0],                 # « tout envoyé » coché : ignoré
                          ["Il-vecchio", 0, False, 3000000, "", "", "", "", 0],               # rien dû mais 3 M livrés : anormal
                          ["Zorka", 5000000, False, 2000000, 2000000, 1000000, "", "", 0]], [])})
    assert ang == [["breto", "Cylo", 9913601, 73936010], ["breto", "Il-vecchio", 0, 3000000]], ang
    assert _fmt(73936010) == "73 936 010"
    q = requetes_onglet(5, r, datetime(2026, 11, 12, 15, 0))
    assert len(q) == 4 and len(requetes_onglet(5, [], datetime(2026, 11, 12, 15, 0))) == 2
    # Paires redistrib : G → mousse85 ignorée (ni désync côté livraison, ni côté réception)
    rp = {("g", "mousse85")}
    r3 = ecarts({"G": ("3.1", [["mousse85", 100000, "", "", "", "", "", "", 0]], []),
                 "mousse85": ("3.3", [], [])}, redistrib_paires=rp)
    assert r3 == [], r3  # paire redistrib → pas de désync
    r4 = ecarts({"G": ("3.1", [], []),
                 "mousse85": ("3.3", [], [["G", 100000]])}, redistrib_paires=rp)
    assert r4 == [], r4  # réception côté créancier aussi ignorée
    return True
# v3 redistrib_paires filtre désync redistrib

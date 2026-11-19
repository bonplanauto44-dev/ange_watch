"""ange_convois_reponses.py : port de traiterReponsePoste (réponses aux 3 formulaires Greniers / Passeurs / Chasseurs).
Surveille les réponses par l'API Google Forms (toutes les 60 s). Modes (convois_reponses_mode.txt) :
  off     : rien ;  observe : calcule ce qu'il ferait et le note (convois_reponses_obs.json), N'ÉCRIT RIEN ;  prod : applique (Data, hors chaîne, demandes de retour, Journal formulaire).
"""
import json, os, re, time
from datetime import datetime
from zoneinfo import ZoneInfo
import ange_convois as C

PARIS = ZoneInfo("Europe/Paris")
MODE_FICHIER = os.path.join(C.DOSSIER, "convois_reponses_mode.txt")
ETAT_FICHIER = os.path.join(C.DOSSIER, "convois_reponses_etat.json")
OBS_FICHIER = os.path.join(C.DOSSIER, "convois_reponses_obs.json")
API = "https://forms.googleapis.com/v1/forms/"
FAMILLES = ["Grenier", "Passeur", "Chasseur"]
CHAMPS = {
    "Grenier": [("Nombre d'ouvrières (O)", "O actuelles"), ("TDP actuel", "TDP"), ("VA (vitesse d'attaque)", "VA")],
    "Passeur": [("CdF (capacité de flood : nombre de fourmis pour savoir ce qu'une attaque peut prendre de TDC)", "CdF"), ("TDP actuel", "TDP"), ("VA (vitesse d'attaque)", "VA")],
    "Chasseur": [("FdF (force de frappe : attaque)", "FdF"), ("TDP actuel", "TDP"), ("Étable à pucerons", "Étable à pucerons")],
}
T_PSEUDO, T_REJOINDRE, T_DONNEES = "Pseudo", "Veux-tu rejoindre la chaîne dès maintenant ?", "Mettre à jour mes données"


def mode_reponses():
    try:
        v = open(MODE_FICHIER, encoding="utf-8").read().strip().lower()
        return v if v in ("observe", "prod") else "off"
    except OSError:
        return "off"


def nombre(v):
    s = re.sub(r"[\s  ]", "", str(v)).replace(",", ".")
    if str(v).strip() == "":
        return ""
    try:
        x = float(s)
    except ValueError:
        return ""
    if x != x or x in (float("inf"), float("-inf")):
        return ""
    return int(x) if x == int(x) else x


def decider(fam, rep, tabs, data_pseudos, data_entetes, demandes):
    """Décision pure. tabs = {'Chaîne unique': [(ligne, pseudo, hors)], 'Ébauche - Chaîne': [...]} (ligne = numéro réel de ligne).
    Retourne {'action': str, 'hors': [(onglet, ligne)], 'demande': bool, 'data': (ligne, [(colonne, valeur)])|None, 'modifs': [...]}"""
    pseudo = rep.get(T_PSEUDO, "")
    res = {"action": "", "hors": [], "demande": False, "data": None, "modifs": []}
    choix = rep.get(T_REJOINDRE, "")
    p = pseudo.lower()
    if choix:
        hors = choix.lower().startswith("non")
        trouve = False
        for nom in ("Chaîne unique", "Ébauche - Chaîne"):
            for ligne, n, est_hors in tabs.get(nom, []):
                if n.strip().lower() != p:
                    continue
                trouve = True
                if hors:
                    res["hors"].append((nom, ligne))
                    res["action"] = "Mis hors chaîne"
                elif est_hors is True and nom == "Chaîne unique":
                    res["action"] = "Demande de retour : à valider par un admin"
                    if p not in {d.strip().lower() for d in demandes}:
                        res["demande"] = True
                elif not res["action"]:
                    res["action"] = "Déjà dans la chaîne (rien à faire)"
                break
        if not trouve:
            res["action"] = "Pseudo introuvable dans la chaîne"
    elif fam:
        ligne = next((2 + k for k, d in enumerate(data_pseudos) if str(d).strip().lower() == p), -1)
        if ligne < 0:
            res["action"] = "Pseudo introuvable dans Data"
        else:
            modifs, cellules = [], []
            for titre, col in CHAMPS[fam]:
                x = nombre(rep.get(titre, ""))
                if x == "":
                    continue
                if col == "TDP" and (x < 15 or x > 100):
                    modifs.append("TDP refusé (%s hors 15-100)" % x)
                    continue
                cellules.append((col, x))
                modifs.append("%s=%s" % (col, x))
            res["data"] = (ligne, cellules)
            res["action"] = "Data mis à jour (%s)" % ", ".join(modifs)
    return res


# ------------------------------------------------------------------ Forms API
def session():
    import configparser
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import AuthorizedSession
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    c.read(os.path.join(C.DOSSIER, "ange_watch.ini"), encoding="utf-8")
    cred = c.get("google", "credentials", fallback="service_account.json")
    if not os.path.isabs(cred):
        cred = os.path.join(C.DOSSIER, cred)
    return AuthorizedSession(Credentials.from_service_account_file(cred, scopes=[
        "https://www.googleapis.com/auth/forms.body.readonly", "https://www.googleapis.com/auth/forms.responses.readonly"]))


def titres_questions(ses, form_id):
    j = ses.get(API + form_id, timeout=30)
    j.raise_for_status()
    out = {}
    for it in j.json().get("items", []):
        q = (it.get("questionItem") or {}).get("question") or {}
        if q.get("questionId"):
            out[q["questionId"]] = str(it.get("title", "")).strip()
    return out


def reponses_depuis(ses, form_id, depuis):
    """Réponses créées après `depuis` (texte ISO UTC), triées de la plus ancienne à la plus récente, avec {titre: texte}."""
    tit = titres_questions(ses, form_id)
    out, token = [], None
    while True:
        params = {"filter": "timestamp > " + depuis, "pageSize": 100}
        if token:
            params["pageToken"] = token
        r = ses.get(API + form_id + "/responses", params=params, timeout=30)
        r.raise_for_status()
        j = r.json()
        for x in j.get("responses", []):
            rep = {}
            for qid, a in (x.get("answers") or {}).items():
                v = ((a.get("textAnswers") or {}).get("answers") or [{}])[0].get("value", "")
                rep[tit.get(qid, qid)] = str(v).strip()
            out.append({"t": x.get("lastSubmittedTime") or x.get("createTime"), "rep": rep})
        token = j.get("nextPageToken")
        if not token:
            break
    return sorted(out, key=lambda x: x["t"])


# ------------------------------------------------------------------ lecture / écriture du sheet privé
def lire_contexte(prive):
    plages = ["'Chaîne unique'!C3:Y202", "'Ébauche - Chaîne'!C3:Y202", "'Data'!A1:AZ1", "'Data'!A2:A600", "'Demandes de retour'!B2:B600"]
    noms = ["cu", "eb", "dh", "dp", "dem"]
    rngs = []
    for p in plages:
        rngs.append(p)
    try:
        lus = C.avec_reprise(lambda: C.lire_plages(prive, noms, plages))
    except Exception:
        lus = {}
        for n, p in zip(noms, plages):          # un onglet absent (Ébauche, Demandes) ne doit pas tout bloquer
            try:
                lus[n] = C.avec_reprise(lambda: C.lire_plages(prive, [n], [p]))[n]
            except Exception:
                lus[n] = []
    tabs = {}
    for nom, cle in (("Chaîne unique", "cu"), ("Ébauche - Chaîne", "eb")):
        tabs[nom] = [(3 + k, str(r[0]) if r else "", (r[22] if len(r) > 22 else None)) for k, r in enumerate(lus[cle]) if r and str(r[0]).strip()]
    entetes = [str(x).strip().lower() for x in (lus["dh"][0] if lus["dh"] else [])]
    dp = [r[0] if r else "" for r in lus["dp"]]
    dem = [r[0] for r in lus["dem"] if r]
    return tabs, dp, entetes, dem


def col_lettre(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def appliquer(prive, res, fam, pseudo, rep, choix_txt, quand, test=False):
    """Écrit la décision dans le sheet privé. quand = datetime Paris."""
    ses = prive.client
    base = "https://sheets.googleapis.com/v4/spreadsheets/%s/values" % prive.id
    put = lambda rng, vals: C.avec_reprise(lambda: ses.request("put", "%s/%s?valueInputOption=USER_ENTERED" % (base, rng), json={"values": vals}).json())
    for nom, ligne in res["hors"]:
        put("'%s'!Y%d" % (nom, ligne), [[True]])
    if res["demande"]:
        j = C.avec_reprise(lambda: ses.request("post", "%s/'Demandes de retour'!A1:C1:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS" % base,
                                               json={"values": [[quand.strftime("%d/%m/%Y %H:%M:%S"), pseudo, ""]]}).json())
        m = re.search(r"!A(\d+):C(\d+)$", j.get("updates", {}).get("updatedRange", ""))
        if m:
            r = m.group(1)
            put("'Demandes de retour'!C%s" % r, [["=IFERROR(IF(INDEX('Chaîne unique'!$Y$3:$Y$98;MATCH(B%s;'Chaîne unique'!$C$3:$C$98;0))=TRUE;\"⏳ À valider (décoche sa case Y)\";\"✅ Réintégré\");\"Introuvable\")" % r]])
    if res["data"]:
        ligne, cellules = res["data"]
        entetes = lire_contexte(prive)[2]
        n = len(entetes)
        for col, val in cellules + [("Dernière MAJ", quand.strftime("%d/%m/%Y %H:%M"))]:
            if col.lower() in entetes:
                c = entetes.index(col.lower()) + 1
            else:
                n += 1
                c = n
                entetes.append(col.lower())
                put("'Data'!%s1" % col_lettre(c), [[col]])
            put("'Data'!%s%d" % (col_lettre(c), ligne), [[val]])
    C.avec_reprise(lambda: ses.request("post", "%s/'Journal formulaire'!A1:D1:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS" % base,
                                       json={"values": [[quand.strftime("%d/%m/%Y %H:%M:%S"), pseudo, "%s · %s" % (fam or "?", choix_txt), res["action"]]]}).json())


# ------------------------------------------------------------------ boucle
def ids_formulaires(prive):
    j = C.avec_reprise(lambda: C.lire_plages(prive, ["m"], ["'Mode'!B8:B10"]))["m"]
    ids = [str(r[0]).strip() if r else "" for r in j]
    return dict(zip(FAMILLES, ids + [""] * (3 - len(ids))))


def tick(sh=None):
    m = mode_reponses()
    if m == "off":
        return None
    try:
        etat = json.load(open(ETAT_FICHIER))
    except Exception:
        etat = {}
    if time.time() - etat.get("t", 0) < 60:
        return None
    etat["t"] = time.time()
    try:
        import ange_convois_prod as PR
        PR.CIBLE_TEST = False
        prive = PR.ouvrir_prod("prive", PR.jour_du_plan())
        ids = ids_formulaires(prive)
        ses = session()
        dernier = etat.setdefault("dernier", {})
        msgs = []
        for fam, fid in ids.items():
            if not fid:
                continue
            if fam not in dernier:                       # première activation : on part de maintenant, on ne rejoue pas l'historique
                dernier[fam] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                continue
            for x in reponses_depuis(ses, fid, dernier[fam]):
                rep = x["rep"]
                if not rep.get(T_PSEUDO):
                    dernier[fam] = max(dernier[fam], x["t"])
                    continue
                tabs, dp, entetes, dem = lire_contexte(prive)
                res = decider(fam, rep, tabs, dp, entetes, dem)
                choix_txt = rep.get(T_REJOINDRE) or T_DONNEES
                quand = datetime.now(PARIS)
                if m == "prod":
                    appliquer(prive, res, fam, rep[T_PSEUDO], rep, choix_txt, quand)
                else:
                    try:
                        obs = json.load(open(OBS_FICHIER))
                    except Exception:
                        obs = []
                    obs.append({"quand": quand.strftime("%d/%m/%Y %H:%M:%S"), "pseudo": rep[T_PSEUDO], "fam": fam, "action": res["action"], "t": x["t"]})
                    json.dump(obs[-200:], open(OBS_FICHIER, "w"), ensure_ascii=False)
                dernier[fam] = x["t"]
                msgs.append("%s %s : %s" % (fam, rep[T_PSEUDO], res["action"]))
        json.dump(etat, open(ETAT_FICHIER, "w"))
        return ("RÉPONSES %s : " % m + " | ".join(msgs)) if msgs else None
    except Exception as e:
        json.dump(etat, open(ETAT_FICHIER, "w"))
        return "RÉPONSES erreur : " + str(e)[:200]


def AUTOTEST():
    tabs = {"Chaîne unique": [(3, "Alice", False), (4, "Bob", True)], "Ébauche - Chaîne": [(3, "Alice", False)]}
    r = decider("Grenier", {T_PSEUDO: "alice", "TDP actuel": "42", "Nombre d'ouvrières (O)": "12 500", "VA (vitesse d'attaque)": ""}, tabs, ["Zoe", "Alice"], ["pseudo", "tdp"], [])
    assert r["data"] == (3, [("O actuelles", 12500), ("TDP", 42)]) and r["action"] == "Data mis à jour (O actuelles=12500, TDP=42)", r
    r = decider("Grenier", {T_PSEUDO: "Alice", "TDP actuel": "400"}, tabs, ["Alice"], [], [])
    assert r["action"] == "Data mis à jour (TDP refusé (400 hors 15-100))" and r["data"][1] == [], r
    r = decider("Passeur", {T_PSEUDO: "Alice", T_REJOINDRE: "Non, je la rejoindrai plus tard"}, tabs, [], [], [])
    assert r["hors"] == [("Chaîne unique", 3), ("Ébauche - Chaîne", 3)] and r["action"] == "Mis hors chaîne"
    r = decider("Passeur", {T_PSEUDO: "Bob", T_REJOINDRE: "Oui, je rejoins la chaîne"}, tabs, [], [], [])
    assert r["action"] == "Demande de retour : à valider par un admin" and r["demande"]
    assert not decider("Passeur", {T_PSEUDO: "Bob", T_REJOINDRE: "Oui, je rejoins la chaîne"}, tabs, [], [], ["bob"])["demande"]
    assert decider("Passeur", {T_PSEUDO: "Alice", T_REJOINDRE: "Oui, je rejoins la chaîne"}, tabs, [], [], [])["action"] == "Déjà dans la chaîne (rien à faire)"
    assert decider("Passeur", {T_PSEUDO: "Nul", T_REJOINDRE: "Oui, je rejoins la chaîne"}, tabs, [], [], [])["action"] == "Pseudo introuvable dans la chaîne"
    assert decider("Passeur", {T_PSEUDO: "Nul", "TDP actuel": "30"}, tabs, ["Alice"], [], [])["action"] == "Pseudo introuvable dans Data"
    assert nombre("1 234,5") == 1234.5 and nombre("") == "" and nombre("abc") == "" and col_lettre(27) == "AA"
    return True

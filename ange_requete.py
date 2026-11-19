#!/usr/bin/env python3
"""
ange_requete.py : service qui exécute, sur la base tdc_serveur.db (LECTURE SEULE), les demandes écrites dans l'onglet
« Requête » du Google Sheet Suivi ANGE.

Onglet « Requête » : lignes 2 à 21 = une demande par ligne.
  A = commande : une requête SQL (SELECT / WITH seulement) ou ANALYSE <RAPPORT|RESIDUS|ATTAQUANTS|LAG|DEPARTS|OSCILLATIONS> ...
  B = OUI pour la lancer  ->  EN COURS  ->  FAIT hh:mm · N lignes -> R<ligne>   (ou ERREUR ...)
  Résultat de la ligne N dans l'onglet « R<N> ».
Plusieurs lignes à OUI = traitées ensemble (les analyses partagent une seule lecture de la base).

Mise à jour automatique : le dossier Drive privé « ange_code » contient les fichiers ange_*.py ; chaque minute le service
télécharge les nouveaux, les teste (compilation + AUTOTEST) et ne les installe que si le test passe (sinon ancienne version
conservée). ange_requete.py lui-même n'est jamais remplacé ainsi. Statut dans « Requête »!E1. Suivi automatique horaire (ange_suivi.py) : statut dans E2.

Lancement :  python ange_requete.py --daemon   (service systemd : surveille le Sheet, 20 s au repos, 3 s juste après une demande)
             python ange_requete.py            (une seule passe, pour test)
"""
import os, re, sqlite3, sys, time
import configparser
from datetime import datetime

DOSSIER = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DOSSIER, "tdc_serveur.db")
MAX_LIGNES = 3000
PREMIERE, DERNIERE = 2, 21
REPOS, ACTIF, DUREE_ACTIF = 20, 3, 300
ENTETE = ["Commande (SQL SELECT ou ANALYSE ...)", "Lancer ? (écrire OUI)", "Statut : résultat dans l'onglet R<ligne>"]


def heure():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris")).strftime("%H:%M")
    except Exception:
        return datetime.now().strftime("%H:%M")


def lire_config():
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    c.read(os.path.join(DOSSIER, "ange_watch.ini"), encoding="utf-8")
    cred = c.get("google", "credentials", fallback="service_account.json")
    if not os.path.isabs(cred):
        cred = os.path.join(DOSSIER, cred)
    return c.get("google", "sheet_id", fallback=""), cred


def ouvrir_base():
    c = sqlite3.connect(DB, timeout=60)
    c.execute("PRAGMA query_only=ON")
    return c


def verifier(sql):
    s = sql.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", s, re.I):
        raise ValueError("Seulement SELECT (ou WITH ... SELECT), ou ANALYSE ...")
    if ";" in s:
        raise ValueError("Une seule requête à la fois (pas de « ; » au milieu).")
    return s


def executer_lot(commandes):
    """Toute la logique (SQL, analyses) est dans ange_analyse.py, mis à jour automatiquement depuis Drive."""
    import importlib, ange_analyse
    importlib.reload(ange_analyse)
    conn = ouvrir_base()
    try:
        return ange_analyse.executer_tout(conn, commandes)
    finally:
        conn.close()


# ---------- synchronisation du code depuis le dossier Drive privé ----------
DOSSIER_CODE_ID = "1SLK0pfNK2pDN_RRRTsQZiPvWjY4-XWOb"
NOM_OK = re.compile(r"^ange_[a-z0-9_]+\.py$")
ETAT_CODE = os.path.join(DOSSIER, "code_etat.json")
TEST_CODE = (
    "import sys, importlib.util\n"
    "chemin, nom = sys.argv[1], sys.argv[2]\n"
    "import py_compile; py_compile.compile(chemin, doraise=True)\n"
    "sp = importlib.util.spec_from_file_location('_essai_' + nom[:-3], chemin)\n"
    "m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)\n"
    "t = getattr(m, 'AUTOTEST', None)\n"
    "if t: t()\n"
)


def verifier_code(chemin, nom):
    import subprocess
    rep = subprocess.run([sys.executable, "-c", TEST_CODE, chemin, nom], cwd=DOSSIER, capture_output=True, text=True, timeout=120)
    return rep.returncode == 0, (rep.stderr.strip().splitlines() or [""])[-1][:200]


def sync_code():
    """Télécharge les nouveaux ange_*.py du dossier Drive, les teste, puis les installe. Retourne la liste des messages."""
    import hashlib, json
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import AuthorizedSession
    _, cred = lire_config()
    s = AuthorizedSession(Credentials.from_service_account_file(cred, scopes=["https://www.googleapis.com/auth/drive.readonly"]))
    rep = s.get("https://www.googleapis.com/drive/v3/files", timeout=30, params={
        "q": f"'{DOSSIER_CODE_ID}' in parents and trashed=false", "fields": "files(id,name,md5Checksum,modifiedTime)",
        "orderBy": "modifiedTime desc", "pageSize": 100})
    rep.raise_for_status()
    recents = {}
    for f in rep.json().get("files", []):
        recents.setdefault(f["name"], f)      # le plus récent de chaque nom
    etat = json.load(open(ETAT_CODE)) if os.path.exists(ETAT_CODE) else {}
    msgs = []
    for nom, f in recents.items():
        md5 = f.get("md5Checksum")
        if nom == "ange_requete.py" or not NOM_OK.match(nom) or not md5:
            continue
        e = etat.get(nom, {})
        if e.get("md5") == md5 or e.get("echec") == md5:
            continue
        d = s.get(f"https://www.googleapis.com/drive/v3/files/{f['id']}", params={"alt": "media"}, timeout=60)
        d.raise_for_status()
        if hashlib.md5(d.content).hexdigest() != md5:
            msgs.append(f"{nom} : téléchargement corrompu, nouvelle tentative")
            continue
        tmp = os.path.join(DOSSIER, "_nouveau_" + nom)   # doit finir par .py pour être importable
        open(tmp, "wb").write(d.content)
        try:
            ok, err = verifier_code(tmp, nom)
        except Exception as ex:
            ok, err = False, str(ex)[:200]
        if ok:
            cible = os.path.join(DOSSIER, nom)
            if os.path.exists(cible):
                os.replace(cible, cible + ".bak")
            os.replace(tmp, cible)
            etat[nom] = {"md5": md5, "date": datetime.now().isoformat(timespec="seconds")}
            msgs.append(f"{nom} mis à jour ({md5[:6]})")
        else:
            os.remove(tmp)
            etat[nom] = dict(etat.get(nom, {}), echec=md5)
            msgs.append(f"{nom} REFUSÉ (test échoué : {err}) : ancienne version conservée")
    json.dump(etat, open(ETAT_CODE, "w"))
    return msgs


def preparer_onglet(sh):
    import gspread
    try:
        ws = sh.worksheet("Requête")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("Requête", rows=30, cols=5)
    if ws.get("A1:C1") != [ENTETE]:
        ws.update(range_name="A1:C1", values=[ENTETE])
    return ws


def ecrire_resultat(sh, ligne, cols, lignes):
    import gspread
    nom = f"R{ligne}"
    try:
        res = sh.worksheet(nom)
    except gspread.WorksheetNotFound:
        res = sh.add_worksheet(nom, rows=100, cols=max(len(cols), 5))
    donnees = [[str(c) for c in cols]] + [["" if x is None else x for x in r] for r in lignes]
    res.clear()
    res.resize(rows=max(len(donnees), 2), cols=max(max((len(r) for r in donnees), default=1), 1))
    res.update(range_name="A1", values=donnees, value_input_option="RAW")
    return nom


def passe(sh, ws):
    """Une lecture de l'onglet. Retourne True si des demandes ont été traitées."""
    lu = ws.get(f"A{PREMIERE}:B{DERNIERE}")
    demandes = []
    for k in range(DERNIERE - PREMIERE + 1):
        ligne = lu[k] if k < len(lu) else []
        a, b = (list(ligne) + ["", ""])[:2]
        if str(b).strip().upper() == "OUI" and str(a).strip():
            demandes.append((PREMIERE + k, str(a)))
    if not demandes:
        return False
    for n, _ in demandes:
        ws.update(range_name=f"B{n}", values=[["EN COURS"]])
    t0 = time.time()
    resultats = executer_lot([a for _, a in demandes])
    for (n, _), (cols, lignes, tronque, err) in zip(demandes, resultats):
        h = heure()
        if err:
            ws.update(range_name=f"B{n}", values=[[f"ERREUR {h} : {err}"[:300]]])
            continue
        try:
            nom = ecrire_resultat(sh, n, cols, lignes)
            ws.update(range_name=f"B{n}", values=[[f"FAIT {h} · {len(lignes)} lignes" + (" (tronqué)" if tronque else "") + f" → {nom}"]])
        except Exception as e:
            ws.update(range_name=f"B{n}", values=[[f"ERREUR écriture {h} : {e}"[:300]]])
    print(f"{heure()} : {len(demandes)} demande(s) traitée(s) en {time.time() - t0:.1f} s", flush=True)
    return True


def connecter():
    import gspread
    sheet_id, cred = lire_config()
    gc = gspread.service_account(filename=cred)
    sh = gc.open_by_key(sheet_id)
    return sh, preparer_onglet(sh)


def main():
    daemon = "--daemon" in sys.argv
    sh = ws = None
    echecs, actif_jusqua, dernier_sync, dernier_suivi, dernier_msg = 0, 0, 0, 0, ""
    while True:
        if time.time() - dernier_sync >= 60:
            dernier_sync = time.time()
            try:
                msgs = sync_code()
                if msgs and ws is not None:
                    texte = f"CODE {heure()} : " + " ; ".join(msgs)
                    print(texte, flush=True)
                    ws.update(range_name="E1", values=[[texte[:300]]])
            except Exception as e:
                print(f"{heure()} : synchro du code impossible : {e}", flush=True)
        if ws is not None and sh is not None and time.time() - dernier_suivi >= 60:
            dernier_suivi = time.time()
            try:
                import importlib, ange_suivi          # suivi automatique (module synchronisé depuis Drive ; absent = ignoré)
                importlib.reload(ange_suivi)
                msg = ange_suivi.tick(sh)
                if msg:
                    print(msg, flush=True)
                    ws.update(range_name="E2", values=[[msg[:300]]])
            except ModuleNotFoundError:
                pass
            except Exception as e:
                msg = f"SUIVI ERREUR {heure()} : {e}"[:300]
                print(msg, flush=True)
                if msg[:40] != dernier_msg[:40]:
                    dernier_msg = msg
                    try:
                        ws.update(range_name="E2", values=[[msg]])
                    except Exception:
                        pass
        try:
            if ws is None:
                sh, ws = connecter()
            if passe(sh, ws):
                actif_jusqua = time.time() + DUREE_ACTIF
            echecs = 0
        except Exception as e:
            echecs += 1
            print(f"{heure()} : erreur ({echecs}) : {e}", flush=True)
            if echecs >= 3:
                sh = ws = None   # nouvelle connexion
        if not daemon:
            return
        time.sleep(min(60, ACTIF * (2 ** echecs)) if echecs else (ACTIF if time.time() < actif_jusqua else REPOS))


if __name__ == "__main__":
    main()

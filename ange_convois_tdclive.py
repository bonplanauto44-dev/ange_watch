"""ange_convois_tdclive.py : port de majTdcLive (Apps Script) : onglet « TDC live » du sheet privé = dernier relevé de l'API pour l'alliance ANGE.
Désarmé par défaut (convois_tdclive_mode.txt : off | test | prod). Même contenu qu'Apps Script : A2:E (pseudo, x, y, TDC, grade), K1:K3 (date, version, nombre)."""
import json, os, time, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo
import ange_convois as C

API = "https://s5.fourmizzz.fr"
TAG = "ANGE"
MODE_FICHIER = os.path.join(C.DOSSIER, "convois_tdclive_mode.txt")
ETAT_FICHIER = os.path.join(C.DOSSIER, "convois_tdclive_etat.json")


def mode_tdclive():
    try:
        v = open(MODE_FICHIER, encoding="utf-8").read().strip().lower()
        return v if v in ("test", "prod") else "off"
    except OSError:
        return "off"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (suivi ANGE)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def derniere_version():
    v = (_get(API + "/api/exports/").get("players") or [])
    return v[0] if v else None


def joueurs_alliance(version):
    return [j for j in _get(API + "/api/exports/players/?version=%s" % version) if j.get("alliance") == TAG]


def lignes(joueurs):
    """Lignes A:E triées par TDC décroissant (tri stable comme JavaScript)."""
    tri = sorted(joueurs, key=lambda j: -(j.get("field") or 0))
    return [[j["pseudo"], j.get("x"), j.get("y"), j.get("field"), j.get("grade") or ""] for j in tri]


def serie_paris(maintenant=None):
    n = (maintenant or datetime.now(ZoneInfo("Europe/Paris"))).replace(tzinfo=None)
    return (n - datetime(1899, 12, 30)).total_seconds() / 86400


def requetes(sheet_id, lg, version, maintenant=None):
    cel = lambda x: {"userEnteredValue": {"numberValue": x}} if isinstance(x, (int, float)) and not isinstance(x, bool) else (
        {"userEnteredValue": {"stringValue": str(x)}} if x not in ("", None) else {})
    rows = [{"values": [cel(c) for c in l]} for l in lg]
    while len(rows) < 399:                     # efface l'ancien contenu (A2:E400)
        rows.append({"values": [{}] * 5})
    return [
        {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": 1, "columnIndex": 0}, "rows": rows, "fields": "userEnteredValue"}},
        {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 10},
                         "rows": [{"values": [cel(serie_paris(maintenant))]}, {"values": [cel(int(version) if str(version).isdigit() else str(version))]},
                                  {"values": [cel(len(lg))]}], "fields": "userEnteredValue"}}]


DB = os.path.join(C.DOSSIER, "tdc_serveur.db")


def version_db():
    """Dernière version ANGE déjà enregistrée par le suivi horaire dans la base du VPS (aucun appel à l'API)."""
    import sqlite3
    c = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=30)
    try:
        r = c.execute("SELECT MAX(version) FROM tdc WHERE alliance=?", (TAG,)).fetchone()
        return r[0] if r else None
    finally:
        c.close()


def joueurs_db(version):
    import sqlite3
    c = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=30)
    try:
        return {p: t for p, t in c.execute("SELECT pseudo, tdc FROM tdc WHERE version=? AND alliance=?", (version, TAG))}
    finally:
        c.close()


def fusion_base(tdc_db, anciennes):
    """TDC pris dans la base, positions et grades repris de la feuille actuelle. Retourne None si un joueur est inconnu de la feuille (appel API nécessaire)."""
    pos = {str(r[0]): r for r in anciennes if r}
    out = []
    for p, t in tdc_db.items():
        r = pos.get(p)
        if r is None or len(r) < 3 or r[1] in ("", None) or r[2] in ("", None):
            return None
        out.append({"pseudo": p, "x": r[1], "y": r[2], "field": t, "grade": (r[4] if len(r) > 4 else "")})
    return out


def tick(sh=None, maintenant=None):
    """Chaque minute : toutes les 2 min on regarde la liste des versions ; si une nouvelle est sortie on réécrit « TDC live »."""
    m = mode_tdclive()
    if m == "off":
        return None
    try:
        etat = json.load(open(ETAT_FICHIER))
    except Exception:
        etat = {}
    mn = (maintenant or datetime.now(ZoneInfo("Europe/Paris"))).minute
    if time.time() - etat.get("t", 0) < (120 if mn <= 20 else 600):   # la nouvelle version sort vers :00-:12 : on guette toutes les 2 min, puis plus rarement
        return None
    etat["t"] = time.time()
    json.dump(etat, open(ETAT_FICHIER, "w"))
    try:
        v_api = derniere_version()                       # une seule petite requête (liste des versions) pour repérer la sortie sans attendre la base
        v_db = version_db()
        v = v_api if (v_api and (not v_db or v_api > v_db)) else v_db
        if not v or v == etat.get("version_" + m):
            return None
        import ange_convois_prod as PR
        PR.CIBLE_TEST = (m != "prod")
        try:
            prive = PR.ouvrir_prod("prive", PR.jour_du_plan())
            sid = {w.title: w.id for w in C.avec_reprise(lambda: prive.worksheets())}["TDC live"]
            anc = C.lire_plages(prive, ["a"], ["'TDC live'!A2:E400"])["a"]
            jl = fusion_base(joueurs_db(v), anc) if v == v_db else None
            source = "base VPS"
            if jl is None or time.time() - etat.get("api_" + m, 0) > 6 * 3600:   # joueur nouveau ou positions à rafraîchir : un appel API
                jl = joueurs_alliance(v)
                etat["api_" + m] = time.time()
                source = "API"
            if len(jl) < 40:
                raise RuntimeError("liste ANGE trop courte (%d)" % len(jl))
            C.avec_reprise(lambda: prive.batch_update({"requests": requetes(sid, lignes(jl), v)}), essais=3, pause=20)
        finally:
            PR.CIBLE_TEST = False
        etat["version_" + m] = v
        json.dump(etat, open(ETAT_FICHIER, "w"))
        return "TDC LIVE %s : version %s, %d joueurs (%s)" % (m, v, len(jl), source)
    except Exception as e:
        etat["erreurs"] = etat.get("erreurs", 0) + 1
        json.dump(etat, open(ETAT_FICHIER, "w"))
        if etat["erreurs"] % 15 == 5:
            try:
                import ange_convois_prod as PR2
                PR2.alerter("⚠ TDC live (VPS) : échec répété : %s" % str(e)[:200])
            except Exception:
                pass
        return "TDC LIVE erreur : %s" % str(e)[:200]


def AUTOTEST():
    l = lignes([{"pseudo": "a", "x": 1, "y": 2, "field": 5, "grade": ""}, {"pseudo": "b", "x": 3, "y": 4, "field": 9, "grade": "G"}])
    assert l[0][0] == "b" and l[1][4] == ""
    r = requetes(7, l, "202611121300")
    assert len(r[0]["updateCells"]["rows"]) == 399 and r[1]["updateCells"]["rows"][1]["values"][0]["userEnteredValue"]["numberValue"] == 202611121300
    assert 46000 < serie_paris() < 47000
    f = fusion_base({"a": 7, "b": 3}, [["a", 1, 2, 9, "G"], ["b", 4, 5, 8, ""]])
    assert f[0]["x"] == 1 and f[0]["field"] == 7 and fusion_base({"z": 1}, [["a", 1, 2, 9, ""]]) is None
    return True

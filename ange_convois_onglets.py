"""ange_convois_onglets.py : ordre et couleur des onglets des 3 fichiers (port de ordonnerOnglets_ d'Apps Script).
Ordre : Ma chaîne, Règles, Suivi, joueurs actifs (rang puis pseudo), Journal, Modèle. Couleur de l'onglet = couleur du poste.
N'écrit que ce qui diffère (rien si tout est déjà en ordre)."""
import ange_convois as C
import ange_convois_pages as P

COUL = {"Grenier 1": "#fde68a", "Grenier 2": "#fde9a0", "Grenier 3": "#fef3c7", "Grenier 4": "#fef6d0", "Grenier 5": "#fef9c3",
        "Passeur 1": "#bfdbfe", "Passeur 2": "#c9e0fd", "Passeur 3": "#d3e7fc", "Passeur 4": "#dbeafe", "Passeur 5": "#e0f2fe",
        "Passeur 6": "#e8f4fb", "Passeur 7": "#eef5f9", "Passeur 8": "#f1f5f9", "Chasseur": "#dcfce7"}
FICHIERS = {"greniers": "3.1", "passeurs": "3.2", "chasseurs": "3.3"}


def _hex(h):
    return {"red": int(h[1:3], 16) / 255, "green": int(h[3:5], 16) / 255, "blue": int(h[5:7], 16) / 255}


def _meme_couleur(rgb, h):
    if not rgb:
        return False
    c = _hex(h)
    return all(abs(rgb.get(k, 0) - c[k]) < 0.004 for k in ("red", "green", "blue"))


def ordre_voulu(actifs, titres):
    """actifs = [(pseudo, poste, rang)] ; retourne la liste ordonnée des titres existants."""
    tries = [m[0] for m in sorted(actifs, key=lambda m: (m[2], P.cle_tri(m[0])))]
    ordre = ["Ma chaîne", "Règles", "Suivi"] + tries + ["Journal", "Modèle"]
    vus = [t for t in ordre if t in titres]
    reste = [t for t in titres if t not in set(vus)]          # onglet inattendu : on le laisse après les joueurs, avant Journal
    if reste:
        i = vus.index("Journal") if "Journal" in vus else len(vus)
        vus = vus[:i] + reste + vus[i:]
    return vus


def requetes(feuilles, actifs):
    """feuilles = [{'title','sheetId','index','rgb'}] (ordre actuel) ; retourne (requêtes, nb_déplacés, nb_recolorés)."""
    postes = {m[0]: m[1] for m in actifs}
    titres = [f["title"] for f in sorted(feuilles, key=lambda f: f["index"])]
    voulu = ordre_voulu(actifs, titres)
    par_titre = {f["title"]: f for f in feuilles}
    reqs, dep, col = [], 0, 0
    courant = list(titres)
    for i, t in enumerate(voulu):
        if courant[i] != t:
            reqs.append({"updateSheetProperties": {"properties": {"sheetId": par_titre[t]["sheetId"], "index": i}, "fields": "index"}})
            courant.remove(t)
            courant.insert(i, t)
            dep += 1
        p = postes.get(t)
        if p and p in COUL and not _meme_couleur(par_titre[t].get("rgb"), COUL[p]):
            reqs.append({"updateSheetProperties": {"properties": {"sheetId": par_titre[t]["sheetId"], "tabColor": _hex(COUL[p])}, "fields": "tabColor"}})
            col += 1
    return reqs, dep, col


def lire_feuilles(ss):
    j = C.avec_reprise(lambda: ss.client.request("get", "https://sheets.googleapis.com/v4/spreadsheets/%s" % ss.id,
                                                  params={"fields": "sheets(properties(sheetId,title,index,hidden,tabColor))"}).json())
    return [{"title": s["properties"]["title"], "sheetId": s["properties"]["sheetId"], "index": s["properties"]["index"],
             "rgb": s["properties"].get("tabColor")} for s in j["sheets"]]


def appliquer(fichiers, ctx, ecrire=True):
    bilan = {}
    for cle, ss in fichiers.items():
        g = FICHIERS[cle]
        actifs = [(m["pseudo"], m["poste"], m["rang"]) for m in ctx["membres"] if m["g"] == g and (not m["hors"] or m["attaqueSeule"])]
        reqs, dep, col = requetes(lire_feuilles(ss), actifs)
        if reqs and ecrire:
            C.avec_reprise(lambda: ss.batch_update({"requests": reqs}), essais=3, pause=20)
        bilan[cle] = "%d déplacé(s), %d recoloré(s)" % (dep, col)
    return bilan


def AUTOTEST():
    f = [{"title": t, "sheetId": i, "index": i, "rgb": None} for i, t in enumerate(["Suivi", "Ma chaîne", "Règles", "Zed", "Abel", "Journal", "Modèle"])]
    reqs, dep, col = requetes(f, [("Abel", "Grenier 1", 0), ("Zed", "Grenier 1", 0)])
    assert dep >= 1 and col == 2
    assert ordre_voulu([("Zed", "Chasseur", 13), ("abel", "Chasseur", 13)], ["Journal", "Zed", "abel", "Suivi", "Règles", "Ma chaîne", "Modèle"]) == ["Ma chaîne", "Règles", "Suivi", "abel", "Zed", "Journal", "Modèle"]
    ok = [{"title": t, "sheetId": i, "index": i, "rgb": _hex(COUL["Chasseur"]) if t in ("abel", "Zed") else None} for i, t in enumerate(["Ma chaîne", "Règles", "Suivi", "abel", "Zed", "Journal", "Modèle"])]
    assert requetes(ok, [("Zed", "Chasseur", 13), ("abel", "Chasseur", 13)]) == ([], 0, 0)
    return True

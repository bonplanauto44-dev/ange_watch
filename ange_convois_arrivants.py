"""ange_convois_arrivants.py : suivi complet des mouvements de la chaîne (entrées, changements de poste, sorties, hors chaîne / retour).
- Onglet « Mouvements » du sheet privé : journal (date, pseudo, événement, détail).
- Onglet « Nouveaux » : chaque ENTRÉE (pseudo qui apparaît dans « Chaîne unique ») reçoit le statut Nouveau : il garde son onglet et sa place dans les sheets,
  mais n'est PAS pris en compte dans le calcul des convois jusqu'au « dernier plan sans convoi » (jour d'entrée + 1). Le plan suivant l'inclut.
  Le filtre est appliqué à la source (ange_convois.charger_prive et ange_convois_ecr.lire_base), pas par bricolage sur « hors chaîne ».
  Supprimer la ligne d'un joueur dans « Nouveaux » le fait entrer immédiatement dans le prochain calcul.
La toute première exécution mémorise la chaîne actuelle (aucun mouvement)."""
import json, os, re, time
from datetime import datetime
from zoneinfo import ZoneInfo
import ange_convois as C
import ange_convois_nuit as N

ETAT_FICHIER = os.path.join(C.DOSSIER, "convois_suivi_chaine_etat.json")
T_FICHIER = os.path.join(C.DOSSIER, "convois_suivi_chaine_t.txt")


def lire_membres(cu):
    """[(ligne, pseudo, poste, hors)] depuis 'Chaîne unique'!A3:Y202."""
    out = []
    for i, r in enumerate(cu):
        r = list(r) + [""] * (25 - len(r))
        g0, poste, pseudo = str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()
        if not (isinstance(r[0], (int, float)) or re.match(r"^\d+\.\d+$", g0)) or not pseudo or poste not in N.POSTES:
            continue
        out.append((i + 3, pseudo, poste, r[24] is True))
    return out


def jour_suivant(jour):
    from datetime import timedelta
    return (datetime.strptime(jour, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def decider(membres, etat, jour, quand, nouveaux):
    """Pure. membres = lire_membres ; etat = {"postes": {pseudo: poste}, "hors": {pseudo: bool}} ou None (1re exécution) ;
    nouveaux = lignes actuelles de « Nouveaux » [[pseudo, poste, entré, fin]] ; jour = AAAA-MM-JJ (Paris) ; quand = « AAAA-MM-JJ HH:MM ».
    Retourne (journal [[quand, pseudo, événement, détail]], nouveaux2, etat2, alertes)."""
    actuel = {p: (po, h) for _l, p, po, h in membres}
    if etat is None:
        return [], [list(x) for x in nouveaux], {"postes": {p: v[0] for p, v in actuel.items()}, "hors": {p: v[1] for p, v in actuel.items()}}, []
    anc_postes, anc_hors = etat.get("postes", {}), etat.get("hors", {})
    journal, alertes = [], []
    nouveaux2 = [list(x) for x in nouveaux]
    for p, (po, h) in actuel.items():
        if p not in anc_postes:
            fin = jour_suivant(jour)
            journal.append([quand, p, "Entrée", "%s · statut Nouveau : pas de convoi jusqu'au plan du %s inclus%s" % (po, fin, " (hors chaîne)" if h else "")])
            nouveaux2 = [x for x in nouveaux2 if x[0] != p] + [[p, po, jour, fin]]
            alertes.append("🆕 %s entre en chaîne (%s) : visible dans les sheets, sans convoi jusqu'au plan du %s inclus." % (p, po, fin))
            continue
        if anc_postes[p] != po:
            journal.append([quand, p, "Changement de poste", "%s → %s" % (anc_postes[p], po)])
            nouveaux2 = [[x[0], po] + x[2:] if x[0] == p else x for x in nouveaux2]
        if bool(anc_hors.get(p)) != h:
            journal.append([quand, p, "Mis hors chaîne" if h else "Remis en chaîne", po])
    for p, po in anc_postes.items():
        if p not in actuel:
            journal.append([quand, p, "Sortie de la chaîne", po])
            nouveaux2 = [x for x in nouveaux2 if x[0] != p]
    nouveaux2 = [x for x in nouveaux2 if len(x) >= 4 and str(x[3])[:10] >= jour]      # statuts terminés : on nettoie
    return journal, nouveaux2, {"postes": {p: v[0] for p, v in actuel.items()}, "hors": {p: v[1] for p, v in actuel.items()}}, alertes


def ecrire_nouveaux(prive, lignes):
    ws = prive.worksheet("Nouveaux")
    C.avec_reprise(lambda: ws.batch_clear(["A2:D200"]))
    if lignes:
        C.avec_reprise(lambda: ws.update(range_name="A2", values=[[str(c) for c in l[:4]] for l in lignes], value_input_option="RAW"))


def ajouter_journal(prive, journal):
    if not journal:
        return
    ws = prive.worksheet("Mouvements")
    v = C.avec_reprise(lambda: ws.get("A1:A2000"))
    suite = len([r for r in v if r and str(r[0]).strip()]) + 1
    C.avec_reprise(lambda: ws.update(range_name="A%d" % (suite + 1), values=[[str(c) for c in l] for l in journal], value_input_option="RAW"))


def tick(sh=None, maintenant=None):
    try:
        etat = json.load(open(ETAT_FICHIER))
        if "postes" not in etat:
            etat = None
    except Exception:
        etat = None
    try:
        t = float(open(T_FICHIER).read())
    except Exception:
        t = 0
    if time.time() - t < 300:
        return None
    open(T_FICHIER, "w").write(str(time.time()))
    try:
        now = maintenant or datetime.now(ZoneInfo("Europe/Paris"))
        import ange_convois_prod as PR
        prive = C.avec_reprise(lambda: PR.ouvrir_prod("prive", PR.jour_du_plan()))
        lus = C.avec_reprise(lambda: C.lire_plages(prive, ["cu", "nv"], ["'Chaîne unique'!A3:Y202", "Nouveaux!A2:D200"]))
        membres = lire_membres(lus["cu"])
        if len(membres) < 60:
            raise RuntimeError("chaîne lue incomplète (%d)" % len(membres))
        nouveaux = [[C.jour_texte(c) if i >= 2 else str(c) for i, c in enumerate(r[:4])] for r in lus["nv"] if r and str(r[0]).strip()]
        journal, nv2, neuf, alertes = decider(membres, etat, now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M"), nouveaux)
        if nv2 != nouveaux:
            ecrire_nouveaux(prive, nv2)
        ajouter_journal(prive, journal)
        json.dump(neuf, open(ETAT_FICHIER, "w"))
        for a in alertes:
            try:
                PR.alerter(a)
            except Exception:
                pass
        return "SUIVI CHAÎNE : " + " | ".join("%s %s" % (j[1], j[2]) for j in journal) if journal else None
    except Exception as e:
        return "SUIVI CHAÎNE erreur : %s" % str(e)[:200]


def AUTOTEST():
    cu = [["2.1", "Passeur 5", "A", ""] + [""] * 20 + [False], ["2.1", "Passeur 5", "B", ""] + [""] * 20 + [True], ["x", "?", "Z"]]
    assert lire_membres(cu) == [(3, "A", "Passeur 5", False), (4, "B", "Passeur 5", True)]
    m = [(3, "A", "Passeur 5", False), (4, "B", "Grenier 1", True)]
    j, nv, e, al = decider(m, None, "2026-11-13", "2026-11-13 14:00", [])
    assert j == [] and e["postes"] == {"A": "Passeur 5", "B": "Grenier 1"} and nv == []
    # entrée de C : journal + statut Nouveau (fin = lendemain) + alerte
    m2 = m + [(5, "C", "Passeur 8", False)]
    j, nv, e2, al = decider(m2, e, "2026-11-13", "2026-11-13 15:00", [])
    assert j[0][1:3] == ["C", "Entrée"] and nv == [["C", "Passeur 8", "2026-11-13", "2026-11-14"]] and len(al) == 1 and e2["postes"]["C"] == "Passeur 8"
    # changement de poste de C, retour de B en chaîne, sortie de A
    m3 = [(4, "B", "Grenier 1", False), (5, "C", "Passeur 7", False)]
    j, nv, e3, al = decider(m3, e2, "2026-11-13", "2026-11-13 16:00", nv)
    ev = [x[1:3] for x in j]
    assert ["C", "Changement de poste"] in ev and ["B", "Remis en chaîne"] in ev and ["A", "Sortie de la chaîne"] in ev
    assert nv == [["C", "Passeur 7", "2026-11-13", "2026-11-14"]] and al == []
    # le statut est conservé pendant toute la nuit du 14 puis nettoyé après
    j, nv, e4, al = decider(m3, e3, "2026-11-14", "2026-11-14 01:00", nv)
    assert nv == [["C", "Passeur 7", "2026-11-13", "2026-11-14"]]
    j, nv, e5, al = decider(m3, e4, "2026-11-15", "2026-11-15 08:00", nv)
    assert nv == [] and j == []
    # sortie puis ré-entrée : nouveau statut
    j, nv, e6, al = decider([(4, "B", "Grenier 1", False)], e5, "2026-11-15", "x", [])
    assert j[0][2] == "Sortie de la chaîne"
    j, nv, e7, al = decider(m3, e6, "2026-11-15", "y", [])
    assert j[0][2] == "Entrée" and nv[0][3] == "2026-11-16"
    return True
# v2 suivi complet

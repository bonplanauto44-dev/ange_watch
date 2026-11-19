"""ange_convois_malus.py — Système de malus convois.

Un malus réduit temporairement la part de bois allouée à un joueur
pendant X jours (pénalité pour non-envoi ou mauvais comportement).

Mécanique : le malus ajuste le delta du joueur dans calcul_plan.
  - Joueur réception (delta < 0, chasseur/passeur) : delta += montant_par_jour
    → il reçoit moins de bois (plan lui envoie moins)
  - Joueur livraison (delta > 0, grenier) : delta -= montant_par_jour
    → il livre moins (garde davantage pour lui, les autres comblent)
  Le sens est choisi automatiquement selon le delta du joueur.

Activation dans ecrire_tout() :
    Chercher "MALUS_HOOK" dans ange_convois_ecr.py pour activer.

Fichier de config : malus.json (modifiable directement entre deux plans)

Usage CLI :
    python3 ange_convois_malus.py action=lister
    python3 ange_convois_malus.py action=ajouter pseudo=X montant=100000 jours=7 [raison=texte]
    python3 ange_convois_malus.py action=supprimer pseudo=X
    python3 ange_convois_malus.py action=supprimer id=1
    python3 ange_convois_malus.py action=tick     # décrémente les jours (appelé nuit)
    python3 ange_convois_malus.py action=test     # test rapide sans écriture
"""

import json, os, sys
from datetime import date, timedelta

DOSSIER     = os.path.dirname(os.path.abspath(__file__))
MALUS_JSON  = os.path.join(DOSSIER, "malus.json")


# ---------------------------------------------------------------------------
# Lecture / écriture du fichier
# ---------------------------------------------------------------------------

def _charger():
    if not os.path.exists(MALUS_JSON):
        return {"malus_actifs": [], "historique": [], "_next_id": 1}
    return json.load(open(MALUS_JSON, encoding="utf-8"))


def _sauver(data):
    with open(MALUS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def ajouter_malus(pseudo, montant_par_jour=0, nb_jours=7, raison="", pourcentage=0):
    """Ajoute un malus pour `pseudo` pendant `nb_jours` jours.

    Choisir UNE des deux formes :
      - montant_par_jour=200000  → bois fixes retirés chaque jour
      - pourcentage=20           → 20 % du delta quotidien du joueur (s'adapte au TDC)

    Exemples :
      ajouter_malus("Dupont", montant_par_jour=200_000, nb_jours=5)
      ajouter_malus("Dupont", pourcentage=20, nb_jours=7, raison="convois non envoyés 3j")
    """
    if montant_par_jour <= 0 and pourcentage <= 0:
        raise ValueError("Préciser montant_par_jour OU pourcentage")
    data = _charger()
    today = date.today()
    fin   = today + timedelta(days=nb_jours - 1)
    malus = {
        "id":               data.get("_next_id", 1),
        "pseudo":           pseudo,
        "montant_par_jour": int(montant_par_jour),   # 0 si mode pourcentage
        "pourcentage":      float(pourcentage),       # 0.0 si mode montant fixe
        "nb_jours_total":   int(nb_jours),
        "nb_jours_restants": int(nb_jours),
        "date_debut":       today.isoformat(),
        "date_fin":         fin.isoformat(),
        "raison":           raison,
        "actif":            True,
    }
    data["malus_actifs"].append(malus)
    data["_next_id"] = malus["id"] + 1
    _sauver(data)
    return malus


def supprimer_malus(pseudo=None, malus_id=None):
    """Désactive et archive un malus par pseudo ou id.

    Retourne True si trouvé, False sinon.
    """
    data = _charger()
    reste, retires = [], []
    for m in data["malus_actifs"]:
        if (pseudo and m["pseudo"].lower() == pseudo.lower()) or \
           (malus_id is not None and m["id"] == int(malus_id)):
            m["actif"] = False
            retires.append(m)
        else:
            reste.append(m)
    if not retires:
        return False
    data["malus_actifs"] = reste
    data.setdefault("historique", []).extend(retires)
    _sauver(data)
    return True


def lister_malus(actifs_seulement=True):
    """Retourne la liste des malus actifs (ou tous si actifs_seulement=False)."""
    data = _charger()
    malus = data.get("malus_actifs", [])
    if not actifs_seulement:
        malus = malus + data.get("historique", [])
    return [m for m in malus if m.get("actif", True)]


# ---------------------------------------------------------------------------
# Application dans le plan
# ---------------------------------------------------------------------------

def appliquer_malus(joueurs):
    """Ajuste les deltas des joueurs concernés par un malus actif.

    À appeler AVANT calcul_plan(), dans ecrire_tout() — voir MALUS_HOOK.

    `joueurs` : liste de dicts {pseudo, delta, ...} telle que retournée par charger_prive.
    Retourne la liste modifiée (in-place + retour pour clarté).
    """
    malus_actifs = lister_malus()
    if not malus_actifs:
        return joueurs

    index = {j["pseudo"].lower(): j for j in joueurs}
    for m in malus_actifs:
        j = index.get(m["pseudo"].lower())
        if not j:
            continue  # joueur absent du plan ce soir (hors chaîne, etc.)

        # Calcul du montant : fixe OU pourcentage du delta absolu
        if m.get("pourcentage", 0) > 0:
            montant = round(abs(j["delta"]) * m["pourcentage"] / 100)
        else:
            montant = m["montant_par_jour"]

        if j["delta"] <= 0:
            # Joueur réception : delta négatif → moins négatif = reçoit moins
            # Les bois économisés sont réabsorbés par le flot min-coût (pas de cible explicite)
            j["delta"] = min(0, j["delta"] + montant)
        else:
            # Joueur livraison : delta positif → moins positif = livre moins
            # Le manque est comblé naturellement par les autres Greniers dans le flot
            j["delta"] = max(0, j["delta"] - montant)
    return joueurs


# ---------------------------------------------------------------------------
# Tick nuit (décrémente les compteurs, archive les terminés)
# ---------------------------------------------------------------------------

def tick_malus():
    """Décrémente nb_jours_restants de chaque malus actif.

    Archive les malus dont nb_jours_restants atteint 0.
    À appeler UNE FOIS par nuit dans ecrire_tout() — voir MALUS_HOOK.
    Retourne (liste_encore_actifs, liste_expires).
    """
    data  = _charger()
    reste, expires = [], []
    for m in data.get("malus_actifs", []):
        m["nb_jours_restants"] = max(0, m["nb_jours_restants"] - 1)
        if m["nb_jours_restants"] == 0:
            m["actif"] = False
            expires.append(m)
        else:
            reste.append(m)
    data["malus_actifs"] = reste
    data.setdefault("historique", []).extend(expires)
    _sauver(data)
    return reste, expires


# ---------------------------------------------------------------------------
# Résumé lisible (pour Telegram / logs)
# ---------------------------------------------------------------------------

def resume_malus():
    """Retourne une chaîne résumé des malus actifs (pour le Telegram 22h)."""
    malus = lister_malus()
    if not malus:
        return ""
    lignes = ["⚠️ Malus actifs :"]
    for m in malus:
        if m.get("pourcentage", 0) > 0:
            quant = f"{m['pourcentage']}% du delta/j"
        else:
            quant = f"{m['montant_par_jour']:,} bois/j"
        lignes.append(
            f"  • {m['pseudo']} — {quant}"
            f" — J{m['nb_jours_total'] - m['nb_jours_restants'] + 1}/{m['nb_jours_total']}"
            f" — fin {m['date_fin']}"
            + (f" ({m['raison']})" if m.get('raison') else "")
        )
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# AUTOTEST
# ---------------------------------------------------------------------------

def AUTOTEST():
    """Tests unitaires rapides — appelés par le démon avant activation."""
    import tempfile, os as _os

    # Travailler sur une copie temporaire
    bak = MALUS_JSON + ".autotest_bak"
    if _os.path.exists(MALUS_JSON):
        import shutil; shutil.copy(MALUS_JSON, bak)
    try:
        # Repartir d'un fichier vide
        _sauver({"malus_actifs": [], "historique": [], "_next_id": 1})

        # ajouter
        m = ajouter_malus("TestPlayer", 100_000, 3, "autotest")
        assert m["nb_jours_restants"] == 3
        assert m["montant_par_jour"] == 100_000
        assert lister_malus() != []

        # appliquer_malus — mode montant fixe, joueur réception
        joueurs = [{"pseudo": "TestPlayer", "delta": -500_000}]
        appliquer_malus(joueurs)
        assert joueurs[0]["delta"] == -400_000, f"delta={joueurs[0]['delta']}"

        # appliquer_malus — mode montant fixe, joueur livraison
        joueurs2 = [{"pseudo": "TestPlayer", "delta": 500_000}]
        appliquer_malus(joueurs2)
        assert joueurs2[0]["delta"] == 400_000, f"delta={joueurs2[0]['delta']}"

        # ajouter_malus mode pourcentage
        supprimer_malus(pseudo="TestPlayer")
        m2 = ajouter_malus("TestPlayer", pourcentage=20, nb_jours=3)
        assert m2["pourcentage"] == 20
        joueurs3 = [{"pseudo": "TestPlayer", "delta": -1_000_000}]
        appliquer_malus(joueurs3)
        assert joueurs3[0]["delta"] == -800_000, f"delta pct={joueurs3[0]['delta']}"

        # tick
        reste, exp = tick_malus()
        assert reste[0]["nb_jours_restants"] == 2

        # supprimer
        ok = supprimer_malus(pseudo="TestPlayer")
        assert ok
        assert lister_malus() == []

    finally:
        if _os.path.exists(bak):
            import shutil; shutil.move(bak, MALUS_JSON)
        elif _os.path.exists(MALUS_JSON):
            _os.remove(MALUS_JSON)

    print("AUTOTEST ange_convois_malus : OK")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    prm = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    action = prm.get("action", "lister")

    if action == "lister":
        malus = lister_malus()
        if not malus:
            print("Aucun malus actif.")
        else:
            print(f"{len(malus)} malus actif(s) :")
            for m in malus:
                quant = f"{m.get('pourcentage',0)}%/j" if m.get("pourcentage", 0) > 0 else f"{m['montant_par_jour']:>10,} bois/j"
            print(f"  [{m['id']}] {m['pseudo']:<20}"
                      f"  {quant}"
                      f"  J{m['nb_jours_total'] - m['nb_jours_restants'] + 1}/{m['nb_jours_total']}"
                      f"  fin {m['date_fin']}"
                      + (f"  ({m['raison']})" if m.get('raison') else ""))

    elif action == "ajouter":
        pseudo  = prm.get("pseudo", "")
        montant = int(prm.get("montant", 0))
        pct     = float(prm.get("pct", prm.get("pourcentage", 0)))
        jours   = int(prm.get("jours", 7))
        raison  = prm.get("raison", "")
        if not pseudo or (montant <= 0 and pct <= 0):
            print("Usage : action=ajouter pseudo=X (montant=N | pct=N) jours=N [raison=texte]")
            sys.exit(1)
        m = ajouter_malus(pseudo, montant_par_jour=montant, nb_jours=jours, raison=raison, pourcentage=pct)
        quant = f"{pct}%/j" if pct > 0 else f"{montant:,} bois/j"
        print(f"✅ Malus [{m['id']}] ajouté : {pseudo} — {quant} × {jours}j — fin {m['date_fin']}")

    elif action == "supprimer":
        pseudo   = prm.get("pseudo")
        malus_id = prm.get("id")
        ok = supprimer_malus(pseudo=pseudo, malus_id=malus_id)
        print("✅ Malus supprimé." if ok else "❌ Malus introuvable.")

    elif action == "tick":
        reste, exp = tick_malus()
        print(f"Tick : {len(reste)} actif(s), {len(exp)} expiré(s)")
        for m in exp:
            print(f"  → {m['pseudo']} malus terminé")

    elif action == "test":
        AUTOTEST()

    elif action == "resume":
        print(resume_malus() or "Aucun malus actif.")

    else:
        print(f"Action inconnue : {action!r}")

#!/usr/bin/env python3
"""appliquer_redistrib_nuit.py — Lancé par cron à 23h00 UTC (00h00 Paris).

Ajoute les lignes de livraison redistrib dans les onglets des Greniers APRÈS l'écriture
du plan de minuit par ange_convois_prod.py. Exécution silencieuse (log fichier).

Cron : 0 23 * * * /home/ubuntu/ange_watch/venv/bin/python3 /home/ubuntu/ange_watch/appliquer_redistrib_nuit.py
"""
import os, sys

DOSSIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DOSSIER)

LOG = os.path.join(DOSSIER, "logs", "redistrib_nuit.log")
os.makedirs(os.path.join(DOSSIER, "logs"), exist_ok=True)

from datetime import datetime

def run():
    import ange_convois_redistrib as R

    if not os.path.exists(R.REDISTRIB_JSON):
        return ["[OK] Pas de dettes_redistrib.json — rien à faire"]

    import json
    suivi = json.load(open(R.REDISTRIB_JSON, encoding="utf-8"))
    if not suivi.get("actif"):
        return ["[OK] Redistrib inactive"]

    return R.appliquer_jour_redistrib()


if __name__ == "__main__":
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        lines = run()
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"\n--- {ts} ---\n")
            for l in lines:
                f.write(l + "\n")
        for l in lines:
            print(l)
    except Exception as e:
        msg = f"ERREUR appliquer_redistrib_nuit : {e}"
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"\n--- {ts} ---\n{msg}\n")
        print(msg, file=sys.stderr)
        sys.exit(1)

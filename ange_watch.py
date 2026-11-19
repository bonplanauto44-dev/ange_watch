#!/usr/bin/env python3
"""
ange_watch.py : surveille chaque heure où part le TDC de l'alliance ANGE (API Fourmizzz s5).

À chaque version horaire de l'API, le script compare avec la version précédente :
  - SORTANT : un joueur ANGE perd des cases, et un joueur HORS ANGE (TAXE ou autre) gagne EXACTEMENT ce montant
              (ou la somme de 2 joueurs) -> échange identifié.
  - ENTRANT : un joueur ANGE gagne des cases pris à un joueur hors ANGE (même méthode).
  - INTERNE : échange entre deux joueurs ANGE (ignoré dans les alertes, compté dans le bilan).
  - NON EXPLIQUÉ : perte d'un joueur ANGE sans contrepartie exacte (plusieurs attaques mélangées, colonisation, etc.).
Résultats : onglets « Flux TDC » et « Bilan horaire » du Google Sheet Suivi ANGE (si configuré),
            fichier CSV local (toujours), alerte Discord (si configurée).

Installation sur le VPS (Ubuntu) :
  pip3 install gspread          # seulement si tu veux l'écriture dans Google Sheets
  python3 ange_watch.py --rattrapage 48      # 1re fois : traite les 48 dernières heures
  crontab -e  puis ajouter :
  10 * * * * cd /home/ubuntu/ange_watch && /usr/bin/python3 ange_watch.py >> log.txt 2>&1

Réglages : fichier ange_watch.ini à côté du script (voir CONFIG_EXEMPLE ci-dessous) ou variables d'environnement.
"""
import argparse, configparser, csv, json, os, re, sqlite3, sys, time, urllib.parse, urllib.request
from datetime import datetime

CONFIG_EXEMPLE = """[general]
alliance = ANGE
seuil_perte = 1000          ; une perte d'un joueur ANGE plus petite que ça est ignorée
surveiller = TAXE           ; alliance mise en avant (alerte pour tout échange avec elle) ; les échanges avec TOUTES les alliances sont analysés
chasseurs = Cylo, Fannou, Fhezz, FourmiDiable, Judalos, Kalipzio, LaVague, Scooby-Doo, Shaka, Tatsuo, Zorka, mousse85, willo ; leurs gains de TDC ne sont pas comptés
seuil_alerte_sortant = 30000 ; alerte Discord si le TDC sortant CERTAIN dépasse ça sur une heure
seuil_alerte_taxe = 5000     ; alerte pour tout échange identifié avec la TAXE à partir de ce montant
seuil_alerte_inconnu = 150000 ; alerte si la perte non expliquée dépasse ça
[google]
sheet_id = 1Px_nI00WarXo_MtyzG7rsHMnsSLRyge1GNrXhhWY5yY
sheet_chaine_id =            ; optionnel : id du Google Sheet privé des chaînes (partagé avec le compte de service) pour lire les chasseurs automatiquement
credentials = service_account.json
[discord]
webhook =
"""

API = "https://s5.fourmizzz.fr/api/exports/"
DOSSIER = os.path.dirname(os.path.abspath(__file__))
ETAT = os.path.join(DOSSIER, "ange_watch_etat.json")
DB = os.path.join(DOSSIER, "tdc_serveur.db")
CSV_FLUX = os.path.join(DOSSIER, "flux_tdc.csv")
CSV_BILAN = os.path.join(DOSSIER, "bilan_horaire.csv")
ENTETE_FLUX = ["Heure (version)", "Type", "Joueur ANGE", "Variation ANGE", "Contrepartie", "Alliance contrepartie", "Montant contrepartie", "Confiance"]
ENTETE_BILAN = ["Heure (version)", "TDC ANGE avant", "TDC ANGE après", "Variation nette", "Sortant certain", "Sortant ambigu", "Entrant certain", "Interne", "Perte non expliquée", "Variation TAXE", "Alerte"]


# ---------- config ----------
def lire_config():
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    c.read(os.path.join(DOSSIER, "ange_watch.ini"), encoding="utf-8")
    g = lambda s, k, d="": os.environ.get(f"ANGE_{s}_{k}".upper(), c.get(s, k, fallback=d))
    return {
        "alliance": g("general", "alliance", "ANGE"),
        "seuil_perte": int(g("general", "seuil_perte", "1000")),
        "surveiller": g("general", "surveiller", "TAXE"),
        "chasseurs": [x.strip() for x in g("general", "chasseurs", "Cylo, Fannou, Fhezz, FourmiDiable, Judalos, Kalipzio, LaVague, Scooby-Doo, Shaka, Tatsuo, Zorka, mousse85, willo").split(",") if x.strip()],
        "sheet_chaine_id": g("google", "sheet_chaine_id", ""),
        "seuil_alerte_sortant": int(g("general", "seuil_alerte_sortant", "30000")),
        "seuil_alerte_taxe": int(g("general", "seuil_alerte_taxe", "5000")),
        "seuil_alerte_inconnu": int(g("general", "seuil_alerte_inconnu", "150000")),
        "sheet_id": g("google", "sheet_id", ""),
        "credentials": g("google", "credentials", "service_account.json"),
        "webhook": g("discord", "webhook", ""),
    }


# ---------- API ----------
def lire_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (suivi ANGE)"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8")


def lister_versions():
    brut = lire_url(API)
    try:
        data = json.loads(brut)
    except ValueError:
        data = None
    cible = data
    if isinstance(data, dict):
        for k, v in data.items():
            if "player" in k.lower() or "joueur" in k.lower():
                cible = v
                break
    texte = json.dumps(cible) if cible is not None else brut
    return sorted(set(re.findall(r"(?<!\d)(20\d{10})(?!\d)", texte)))


def charger_version(v):
    brut = json.loads(lire_url(API + "players/?version=" + urllib.parse.quote(v)))
    liste = brut if isinstance(brut, list) else next((brut[k] for k in ("players", "joueurs", "data") if isinstance(brut.get(k), list)), list(brut.values()))
    res = {}
    for j in liste:
        if not j or not j.get("pseudo"):
            continue
        try:
            tdc = int(float(j.get("field")))
        except (TypeError, ValueError):
            continue
        a = j.get("alliance")
        if isinstance(a, dict):
            a = a.get("tag") or a.get("name") or a.get("id")
        res[str(j["pseudo"]).strip()] = ((str(a).strip() if a else ""), tdc)
    return res


# ---------- base de données (TDC de tout le serveur, une photo par version horaire) ----------
def db_ouvrir():
    c = sqlite3.connect(DB, timeout=60)
    c.execute("PRAGMA journal_mode=WAL")   # lecture et écriture en même temps (service de requêtes)
    c.execute("CREATE TABLE IF NOT EXISTS tdc (version TEXT, pseudo TEXT, alliance TEXT, tdc INTEGER, PRIMARY KEY (version, pseudo))")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pseudo ON tdc (pseudo, version)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_alliance ON tdc (alliance, version)")
    return c


def db_a_version(c, v):
    return c.execute("SELECT 1 FROM tdc WHERE version=? LIMIT 1", (v,)).fetchone() is not None


def db_enregistrer(c, v, data):
    if db_a_version(c, v):
        return
    c.executemany("INSERT OR IGNORE INTO tdc VALUES (?,?,?,?)", [(v, p, al, t) for p, (al, t) in data.items()])
    c.commit()


# ---------- analyse ----------
def apparier(pertes, candidats, plausible=None):
    """Égalité STRICTE perte = gain, un seul joueur (pas de somme de 2 attaques pour l'instant).
    pertes : [(pseudo, montant>0)] ; candidats : [(pseudo, alliance, montant>0)].
    Une contrepartie ne sert qu'une fois. Si plusieurs pertes de même montant se disputent les mêmes candidats, c'est AMBIGUË.
    Retourne [((pseudo, montant), contrepartie ou None, confiance)]"""
    res, utilises, attribue = [], set(), {}
    nb = {}
    for p, m in pertes:
        nb[m] = nb.get(m, 0) + 1
    for p, m in sorted(pertes, key=lambda x: -x[1]):
        tous = [c for c in candidats if c[2] == m and (plausible is None or plausible(p, c[0]))]
        libres = [c for c in tous if c[0] not in utilises]
        if libres and attribue.get(m, 0) < len(tous):
            attribue[m] = attribue.get(m, 0) + 1
            if len(libres) == 1 and nb[m] == 1 and m % 100 == 0:
                utilises.add(libres[0][0])
                res.append(((p, m), libres[0], "FAIBLE (montant rond : peut être un hasard)"))
            elif len(libres) == 1 and nb[m] == 1:
                utilises.add(libres[0][0])
                res.append(((p, m), libres[0], "HAUTE (1 perte = 1 gain exact, unique)"))
            else:
                noms = ", ".join(c[0] for c in libres[:3])
                res.append(((p, m), libres[0], f"AMBIGUË ({len(libres)} candidat(s) pour {nb[m]} perte(s) identiques : {noms})"))
                if len(libres) >= nb[m]:
                    utilises.add(libres[0][0])
        else:
            res.append(((p, m), None, ""))
    return res


def analyser(v_avant, v_apres, avant, apres, cfg, chasseurs=frozenset()):
    ange = cfg["alliance"].lower()
    surv = cfg["surveiller"].lower()
    est_ange = lambda al: al.lower() == ange
    est_ch = lambda p: p.lower() in chasseurs
    membres = [p for p, (al, _) in apres.items() if est_ange(al) and p in avant]
    hors_ch = [p for p in membres if not est_ch(p)]
    deltas = {p: apres[p][1] - avant[p][1] for p in apres if p in avant}
    # les chasseurs ne comptent pas dans le TDC d'ANGE (ils prennent du TDC par la chasse)
    tdc_avant = sum(avant[p][1] for p in hors_ch)
    tdc_apres = sum(apres[p][1] for p in hors_ch)
    var_surv = sum(deltas[p] for p in deltas if apres[p][0].lower() == surv)
    dehors_gain = [(p, apres[p][0], d) for p, d in deltas.items() if d > 0 and not est_ange(apres[p][0])]
    dehors_perte = [(p, apres[p][0], -d) for p, d in deltas.items() if d < 0 and not est_ange(apres[p][0])]
    ange_gain = [(p, apres[p][0], d) for p, d in deltas.items() if d > 0 and est_ange(apres[p][0])]
    seuil = cfg["seuil_perte"]
    pertes = [(p, -deltas[p]) for p in membres if deltas[p] <= -seuil]
    gains = [(p, deltas[p]) for p in hors_ch if deltas[p] >= seuil]   # gains des chasseurs exclus
    flux = []
    s_cert = s_amb = e_cert = interne = inconnu = 0
    # 1) perte ANGE = gain d'un joueur hors ANGE (SORTANT)
    non_sortantes = []
    def plausible(victime, attaquant):
        # règle du jeu : le TDC de la victime vaut entre 0,5 et 3 fois celui de l'attaquant (marge 15 %)
        tv, ta = avant[victime][1], avant[attaquant][1]
        return ta <= 0 or 0.5 * ta * 0.85 <= tv <= 3 * ta * 1.15
    for (p, m), c, conf in apparier(pertes, dehors_gain, plausible):
        if c is None:
            non_sortantes.append((p, m))
            continue
        certain = conf.startswith("HAUTE")
        flux.append([v_apres, "SORTANT" if certain else "SORTANT (ambigu)", p, -m, c[0], c[1] or "(aucune)", c[2], conf])
        if certain:
            s_cert += m
        else:
            s_amb += m
    # 2) le reste : perte = gain d'un autre joueur ANGE (INTERNE). Si c'est un chasseur : non compté.
    for (p, m), c, conf in apparier(non_sortantes, ange_gain):
        if c is None:
            inconnu += m
            flux.append([v_apres, "NON EXPLIQUÉ", p, -m, "", "", "", "aucune contrepartie exacte"])
        elif est_ch(c[0]):
            flux.append([v_apres, "VERS CHASSEUR (non compté)", p, -m, c[0], "ANGE", c[2], conf])
        else:
            interne += m
            flux.append([v_apres, "INTERNE", p, -m, c[0], "ANGE", c[2], conf])
    # 3) gain d'un joueur ANGE (hors chasseurs) = perte d'un joueur hors ANGE (ENTRANT)
    for (p, m), c, conf in apparier(gains, dehors_perte):
        if c is None:
            continue
        certain = conf.startswith("HAUTE")
        flux.append([v_apres, "ENTRANT" if certain else "ENTRANT (ambigu)", p, m, c[0], c[1] or "(aucune)", -c[2], conf])
        if certain:
            e_cert += m
    alertes = []
    if s_cert >= cfg["seuil_alerte_sortant"]:
        alertes.append(f"{s_cert:,} cases sortent d'ANGE (certain)".replace(",", " "))
    gros_surv = [f for f in flux if f[1] in ("SORTANT", "ENTRANT") and f[5].lower() == surv and abs(f[3]) >= cfg["seuil_alerte_taxe"]]
    if gros_surv:
        alertes.append(f"{len(gros_surv)} échange(s) certain(s) avec {cfg['surveiller']}")
    # Alerte uniquement sur du 100 % certain : la perte non expliquée n'alerte plus
    bilan = [v_apres, tdc_avant, tdc_apres, tdc_apres - tdc_avant, s_cert, s_amb, e_cert, interne, inconnu, var_surv, " ; ".join(alertes)]
    return flux, bilan


def lire_chasseurs(cfg):
    """Chasseurs : liste de l'ini, + lecture automatique dans le Google Sheet des chaînes si configuré."""
    res = {x.lower() for x in cfg["chasseurs"]}
    if cfg.get("sheet_chaine_id") and os.path.exists(cfg["credentials"]):
        try:
            import gspread
            gc = gspread.service_account(filename=cfg["credentials"])
            lignes = gc.open_by_key(cfg["sheet_chaine_id"]).worksheet("Chaîne unique").get("B3:C120")
            auto = {r[1].strip().lower() for r in lignes if len(r) >= 2 and r[0].strip().lower().startswith("chasseur")}
            if auto:
                res = auto
        except Exception as e:
            print("Lecture des chasseurs dans le Sheet impossible, liste de l'ini utilisée :", e)
    return frozenset(res)


# ---------- sorties ----------
def ecrire_csv(chemin, entete, lignes):
    nouveau = not os.path.exists(chemin)
    with open(chemin, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if nouveau:
            w.writerow(entete)
        w.writerows(lignes)


def ecrire_sheet(cfg, flux, bilans):
    if not cfg["sheet_id"] or not os.path.exists(cfg["credentials"]):
        return "Google Sheets non configuré (CSV seulement)."
    try:
        import gspread
    except ImportError:
        return "gspread non installé : pip3 install gspread (CSV seulement)."
    gc = gspread.service_account(filename=cfg["credentials"])
    sh = gc.open_by_key(cfg["sheet_id"])
    for nom, entete, lignes in (("Flux TDC", ENTETE_FLUX, flux), ("Bilan horaire", ENTETE_BILAN, bilans)):
        try:
            ws = sh.worksheet(nom)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(nom, rows=2000, cols=len(entete))
            ws.append_row(entete)
        if ws.row_values(1) != entete:
            ws.clear()
            ws.resize(rows=2000, cols=len(entete))
            ws.append_row(entete)
        if lignes:
            ws.append_rows(lignes, value_input_option="USER_ENTERED")
    return "Google Sheets mis à jour."


def discord(cfg, texte):
    return   # les alertes Discord sont désormais envoyées par ange_suivi.py (calcul exact : rang, ratio, hors-chaîne, chasse) ; ce script garde seulement ses CSV
    if not cfg["webhook"]:
        return
    try:
        req = urllib.request.Request(cfg["webhook"], data=json.dumps({"content": texte[:1900]}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "ange_watch"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        print("Discord : erreur", e)


def formater_version(v):
    return datetime.strptime(v, "%Y%m%d%H%M").strftime("%d/%m/%Y %H:%M")


# ---------- main ----------
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rattrapage", type=int, default=0, help="traiter les N dernières versions (1re utilisation)")
    p.add_argument("--historique", type=int, default=0, help="remplit seulement la base tdc_serveur.db avec les N dernières versions (sans analyse ni alertes)")
    p.add_argument("--exemple-config", action="store_true", help="affiche un ange_watch.ini d'exemple")
    a = p.parse_args()
    if a.exemple_config:
        print(CONFIG_EXEMPLE)
        return
    cfg = lire_config()
    versions = lister_versions()
    if len(versions) < 2:
        sys.exit("Moins de 2 versions disponibles.")
    base = db_ouvrir()
    if a.historique:
        cibles = [v for v in versions[-a.historique:] if not db_a_version(base, v)]
        for i, v in enumerate(cibles, 1):
            try:
                db_enregistrer(base, v, charger_version(v))
            except Exception as e:
                print("Version", v, "ignorée :", e)
            if i % 20 == 0 or i == len(cibles):
                print(f"{i}/{len(cibles)} versions enregistrées")
        return
    etat = json.load(open(ETAT)) if os.path.exists(ETAT) else {}
    derniere = etat.get("derniere_version")
    if a.rattrapage:
        a_traiter = versions[-(a.rattrapage + 1):]
    elif derniere and derniere in versions:
        a_traiter = versions[versions.index(derniere):]
    else:
        a_traiter = versions[-2:]
    if len(a_traiter) < 2:
        print("Rien de nouveau.")
        return
    chasseurs = lire_chasseurs(cfg)
    print(f"{len(chasseurs)} chasseur(s) ignoré(s) pour les gains.")
    tous_flux, tous_bilans = [], []
    cache = {}
    def charger(v):
        if v not in cache:
            cache[v] = charger_version(v)
            try:
                db_enregistrer(base, v, cache[v])
            except Exception as e:
                print("Base de données :", e)
        return cache[v]
    for v0, v1 in zip(a_traiter, a_traiter[1:]):
        flux, bilan = analyser(formater_version(v1), formater_version(v1), charger(v0), charger(v1), cfg, chasseurs)
        cache.pop(v0, None)
        tous_flux += flux
        tous_bilans.append(bilan)
        print(f"{bilan[0]} : ANGE {bilan[3]:+,} | sortant certain {bilan[4]:,} | ambigu {bilan[5]:,} | non expliqué {bilan[8]:,} | TAXE {bilan[9]:+,}".replace(",", " "))
        if bilan[10]:
            lignes = [f"{x[2]} {x[3]:+,} <-> {x[4]} [{x[5]}] {x[7].split(' ')[0]}" for x in flux
                      if x[1] in ("SORTANT", "ENTRANT")][:12]
            discord(cfg, f"⚠️ ANGE {bilan[0]} : {bilan[10]}\n" + "\n".join(lignes))
    ecrire_csv(CSV_FLUX, ENTETE_FLUX, tous_flux)
    ecrire_csv(CSV_BILAN, ENTETE_BILAN, tous_bilans)
    print(ecrire_sheet(cfg, tous_flux, tous_bilans))
    etat["derniere_version"] = a_traiter[-1]
    json.dump(etat, open(ETAT, "w"))


if __name__ == "__main__":
    main()
